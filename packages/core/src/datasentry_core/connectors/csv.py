"""CSV 连接器（49.3/四十五 Issue #6）。

能力：编码探测（charset-normalizer）、分隔符嗅探（csv.Sniffer）、BOM 处理、
公式注入标记（11.7 suspicious_formula_injection 的加载期预警）、
只读聚合（ADR-005，SQL 经 engine.sql_guard 守卫）、三档指纹（19.1）。
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pa_csv
from charset_normalizer import from_path as charset_from_path

from datasentry_core.connectors.base import (
    ColumnInfo,
    DataHandle,
    FingerprintMode,
    FrameBatch,
    LoadWarning,
    SamplingMethod,
    SchemaInfo,
)
from datasentry_core.connectors.errors import ConnectorError, DataSourceNotFoundError
from datasentry_core.connectors.spec import DataSourceSpec, DataSourceType
from datasentry_core.engine import DuckDBExecutor
from datasentry_core.models.enums import Severity
from datasentry_core.models.fingerprint import DatasetFingerprint

FORMULA_INJECTION_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
_WARNING_CAP = 100


def _detect_encoding(path: Path, override: str | None = None) -> str:
    """编码探测：用户指定 > BOM 探测 > 检测器 > utf-8 回退。
    检测器对短文件可能误判，因此 options["encoding"] 是权威通道（测试锁定该行为）。"""
    if override:
        return override
    try:
        raw = path.read_bytes()[:65536]
    except OSError as exc:
        raise DataSourceNotFoundError(f"cannot read {path}: {exc}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        result = charset_from_path(path).best()
    except OSError as exc:
        raise DataSourceNotFoundError(f"cannot read {path}: {exc}") from exc
    if result is None:
        return "utf-8"
    return result.encoding or "utf-8"


def _sniff_delimiter(path: Path, encoding: str) -> str:
    """基于文件头部样本嗅探分隔符，失败回退逗号。"""
    try:
        with path.open("r", encoding=encoding, errors="replace") as fh:
            head = fh.read(8192)
    except OSError as exc:
        raise DataSourceNotFoundError(f"cannot read {path}: {exc}") from exc
    try:
        dialect = csv.Sniffer().sniff(head, delimiters=",;\t|")
        return dialect.delimiter
    except Exception:
        return ","


def _schema_hash(column_signature: list[tuple[str, str]]) -> str:
    """schema_hash = 列签名规范 JSON 的 SHA-256（19.1）。"""
    canonical = json.dumps(column_signature, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sql_string_literal(value: str) -> str:
    """SQL 单引号字面量（视图创建时内联路径/分隔符用）。"""
    return "'" + value.replace("'", "''") + "'"


class CsvDataHandle:
    """CSV 数据句柄：pyarrow 流式读取 + duckdb 只读聚合。"""

    def __init__(self, spec: DataSourceSpec) -> None:
        if spec.path is None or isinstance(spec.path, str):
            raise DataSourceNotFoundError("csv source requires a local file path")
        self._spec = spec
        self._path = spec.path
        self._encoding = _detect_encoding(spec.path, override=spec.options.get("encoding"))
        self._delimiter = _sniff_delimiter(spec.path, self._encoding)
        self._executor = DuckDBExecutor()
        self._view_ready = False
        self._schema_info: SchemaInfo | None = None
        self._warnings: list[LoadWarning] | None = None
        self._closed = False

    @property
    def source_type(self) -> DataSourceType:
        return self._spec.source_type

    @property
    def source_path(self) -> Path | str | None:
        return self._path

    @property
    def table_name(self) -> str | None:
        """CSV 无表名语义（Step 40 协议成员）。"""
        return None

    def _ensure_view(self) -> None:
        """注册只读 view。注意：duckdb 默认将空字符串视为 NULL，
        pyarrow 读取路径保留空字符串——两路径语义差异在检测器层统一（Step 6 文档约定）。"""
        if self._view_ready:
            return
        if self._encoding in ("utf-8", "utf-8-sig", "ascii"):
            self._executor.execute_setup(
                "CREATE OR REPLACE VIEW data AS "
                "SELECT * FROM read_csv_auto("
                f"{_sql_string_literal(str(self._path))}, "
                "header=true, "
                "strict_mode=false, "
                "quote='\"', "
                f"delim={_sql_string_literal(self._delimiter)})"
            )
        else:
            table = pa_csv.read_csv(
                self._path,
                read_options=pa_csv.ReadOptions(encoding=self._encoding),
                parse_options=pa_csv.ParseOptions(delimiter=self._delimiter),
            )
            self._executor.register("data", table)
        self._view_ready = True

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError("handle is closed")

    def schema(self) -> SchemaInfo:
        self._check_open()
        if self._schema_info is None:
            self._ensure_view()
            table = self._executor.execute("DESCRIBE SELECT * FROM data")
            names = table.column(0).to_pylist()
            types = table.column(1).to_pylist()
            self._schema_info = SchemaInfo(
                columns=[
                    ColumnInfo(name=str(name), physical_type=str(typ))
                    for name, typ in zip(names, types, strict=True)
                ]
            )
        return self._schema_info

    def read_batches(self, batch_size: int = 65536) -> Iterator[FrameBatch]:
        """流式读取（pyarrow open_csv，block_size 按 batch_size 缩放）。"""
        self._check_open()
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        read_opts = pa_csv.ReadOptions(
            encoding=self._encoding,
            block_size=max(batch_size * 16, 65536),
        )
        parse_opts = pa_csv.ParseOptions(delimiter=self._delimiter)
        row_offset = 0
        with pa_csv.open_csv(
            self._path, read_options=read_opts, parse_options=parse_opts
        ) as reader:
            for idx, rb in enumerate(reader):
                table = pa.Table.from_batches([rb])
                yield FrameBatch(
                    source=self._spec,
                    table=table,
                    batch_index=idx,
                    row_offset=row_offset,
                )
                row_offset += table.num_rows

    def read_sample(self, n: int, method: SamplingMethod = "random") -> FrameBatch:
        """抽样读取：reservoir(REPEATABLE seed) 保证可复现（20.3）。"""
        self._check_open()
        if n < 1:
            raise ValueError("n must be >= 1")
        self._ensure_view()
        seed = int(self._spec.options.get("seed", 42))
        if method == "none":
            sql = "SELECT * FROM data LIMIT ?"
            params: list[object] = [n]
        elif method == "time_based":
            column = self._spec.options.get("time_column")
            if not isinstance(column, str) or not column:
                method = "reservoir"
                sql = f"SELECT * FROM data USING SAMPLE reservoir({n} ROWS) REPEATABLE ({seed})"
                params = []
            else:
                sql = f'SELECT * FROM data ORDER BY "{column}" LIMIT ?'
                params = [n]
        else:
            # SAMPLE 子句不支持预编译参数，n/seed 均为代码内整数（已校验），安全内联
            sql = f"SELECT * FROM data USING SAMPLE reservoir({n} ROWS) REPEATABLE ({seed})"
            params = []
        table = self._executor.execute(sql, params)
        return FrameBatch(source=self._spec, table=table)

    def sql_aggregate(self, sql: str, params: dict[str, object] | None = None) -> FrameBatch:
        """只读聚合（DuckDB pushdown）。SQL 必须通过 engine.sql_guard 只读白名单。"""
        self._check_open()
        self._ensure_view()
        table = self._executor.execute(sql, params)
        return FrameBatch(source=self._spec, table=table)

    def count_rows(self) -> int:
        self._check_open()
        self._ensure_view()
        value = self._executor.execute("SELECT count(*) FROM data").column(0).to_pylist()[0]
        assert isinstance(value, int)
        return value

    def fingerprint(self, mode: FingerprintMode = "full") -> DatasetFingerprint:
        self._check_open()
        signature = [(col.name, col.physical_type) for col in self.schema().columns]
        row_count = self.count_rows()
        file_sha256: str | None = None
        content_sample_hash: str | None = None
        if mode == "full":
            file_sha256 = _file_sha256(self._path)
        elif mode == "sampled":
            head = self._executor.execute("SELECT * FROM data LIMIT 1000")
            rand = self._executor.execute(
                "SELECT * FROM data USING SAMPLE reservoir(100000) REPEATABLE (42)"
            )
            digest = hashlib.sha256()
            digest.update(str(head.to_pylist()).encode("utf-8"))
            digest.update(str(rand.to_pylist()).encode("utf-8"))
            content_sample_hash = digest.hexdigest()
        return DatasetFingerprint(
            dataset_id=self._spec.options.get("dataset_id", ""),
            fingerprint_type=mode,
            file_sha256=file_sha256,
            schema_hash=_schema_hash(signature),
            row_count=row_count,
            column_count=len(signature),
            column_signature=signature,
            content_sample_hash=content_sample_hash,
        )

    def warnings(self) -> list[LoadWarning]:
        """公式注入标记（11.7）：向量化扫描字符串列，前 _WARNING_CAP 条。"""
        self._check_open()
        if self._warnings is not None:
            return list(self._warnings)
        warnings: list[LoadWarning] = []
        for batch in self.read_batches():
            for col_name in batch.table.column_names:
                col = batch.table[col_name]
                if not pa.types.is_string(col.type) or pa.types.is_null(col.type):
                    continue
                for prefix in FORMULA_INJECTION_PREFIXES:
                    mask = pc.starts_with(col, pattern=prefix)
                    indices = pc.indices_nonzero(mask)
                    for row in indices.to_pylist():
                        value = col[row].as_py() or ""
                        warnings.append(
                            LoadWarning(
                                row=batch.row_offset + int(row),
                                column=col_name,
                                message=f"value starts with formula-injection prefix {prefix!r}",
                                severity=Severity.LOW,
                                value_preview=value[:20],
                            )
                        )
                        if len(warnings) >= _WARNING_CAP:
                            self._warnings = warnings
                            return warnings
        self._warnings = warnings
        return list(warnings)

    def close(self) -> None:
        self._executor.close()
        self._closed = True

    def content_fingerprint(self) -> str:
        """内容指纹（Step 55）：文件源 = 文件 SHA-256（Step 53 调度哈希语义）。"""
        if not isinstance(self._path, Path):
            raise ConnectorError("csv fingerprint requires a local file path")
        return _file_sha256(self._path)


def _file_sha256(path: Path) -> str:
    """流式文件 SHA-256（≤1GB 全量，大文件走 sampled 档）。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CsvConnector:
    """CSV 连接器（49.3）。"""

    connector_id = "csv"
    display_name = "CSV"

    def supports(self, source: DataSourceSpec) -> bool:
        # Step 57：本地文件连接器排除云 URI（s3:// gs:// az:// 归 RemoteFileConnector）
        return (
            source.source_type == DataSourceType.CSV
            and source.path is not None
            and not isinstance(source.path, str)
        )

    def open(self, source: DataSourceSpec) -> DataHandle:
        return CsvDataHandle(source)

    def close(self) -> None:
        pass
