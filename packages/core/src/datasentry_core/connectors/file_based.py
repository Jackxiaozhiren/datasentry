"""文件型数据句柄基类（Step 18）：Parquet/JSONL/XLSX 连接器的共享实现。

与 CSV 连接器保持独立（CSV 有编码探测/分隔符嗅探等专属逻辑，不重构，ADR-019）。
共享部分：schema（DESCRIBE 视图）/read_sample/sql_aggregate/count_rows/
fingerprint/warnings（公式注入扫描）/close。
子类需实现：_ensure_view()（注册只读 data 视图）与 read_batches()（流式）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from datasentry_core.connectors.base import (
    ColumnInfo,
    FingerprintMode,
    FrameBatch,
    LoadWarning,
    SamplingMethod,
    SchemaInfo,
)
from datasentry_core.connectors.errors import DataSourceNotFoundError
from datasentry_core.connectors.spec import DataSourceSpec
from datasentry_core.engine import DuckDBExecutor
from datasentry_core.models.enums import Severity
from datasentry_core.models.fingerprint import DatasetFingerprint

FORMULA_INJECTION_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
_WARNING_CAP = 100


def _schema_hash(column_signature: list[tuple[str, str]]) -> str:
    """schema_hash = 列签名规范 JSON 的 SHA-256（19.1）。"""
    canonical = json.dumps(column_signature, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    """流式文件 SHA-256（≤1GB 全量，大文件走 sampled 档）。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FileDataHandle:
    """文件型数据句柄基类（DataHandle 协议共享实现）。"""

    def __init__(self, spec: DataSourceSpec) -> None:
        if spec.path is None:
            raise DataSourceNotFoundError(f"{type(self).__name__} requires a path")
        self._spec = spec
        self._path = spec.path
        self._executor = DuckDBExecutor()
        self._view_ready = False
        self._schema_info: SchemaInfo | None = None
        self._warnings: list[LoadWarning] | None = None
        self._closed = False

    # ---- 子类契约 ------------------------------------------------------

    def _ensure_view(self) -> None:
        raise NotImplementedError

    def read_batches(self, batch_size: int = 65536) -> Iterator[FrameBatch]:
        raise NotImplementedError

    # ---- 共享实现 ------------------------------------------------------

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
        """公式注入标记（11.7）：遍历批处理扫描字符串列，前 _WARNING_CAP 条。"""
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
