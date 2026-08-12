"""PostgreSQL 数据源连接器（Step 55，V4：多数据源第二落点，ADR-055）。

经 DuckDB postgres 扩展（LOAD postgres）只读 ATTACH 远程库
（TYPE postgres, READ_ONLY），注册只读 data 视图后复用 FileDataHandle
共享实现（schema/read_sample/sql_aggregate/count_rows/warnings）。
表名必填；schema 名经 options["schema"] 传入，默认 public。

凭据红线（V4 硬性约束）：
- DSN 只存在于内存态（spec.options["dsn"]）或经 connection_ref
  指向的环境变量（如 DATASENTRY_PG_DSN），不落库、不进日志/evidence/报告
- DuckDB 错误文本会原样包含 DSN 与密码：_RedactingExecutor 将
  DuckDB 异常净化（DSN → postgresql://***、密码 → ***）后转
  ConnectorError 传播，REST 400 / CLI 错误面只见净化文本
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import ClassVar, cast
from urllib.parse import urlparse

import duckdb
import pyarrow as pa

from datasentry_core.connectors.base import FingerprintMode, FrameBatch
from datasentry_core.connectors.errors import ConnectorError, DataSourceNotFoundError
from datasentry_core.connectors.file_based import FileDataHandle, _schema_hash
from datasentry_core.connectors.spec import DataSourceSpec, DataSourceType
from datasentry_core.engine import DuckDBExecutor
from datasentry_core.engine.base import SqlParams
from datasentry_core.models.fingerprint import DatasetFingerprint
from datasentry_core.secrets import lookup_secret

#: ATTACH 目标别名（连接器内部名，避开用户数据命名冲突概率极低）
_DB_ALIAS = "pg"

#: 加载 postgres 扩展失败的提示（缺网络/离线/发行版未带扩展）
_PG_EXTENSION_HINT = (
    "failed to load duckdb postgres extension: network access is required on first use "
    "(or the extension must be pre-provisioned for offline environments); "
    "original error: {}"
)

#: 连接失败提示（凭据已净化）
_PG_CONNECT_HINT = "postgres connection failed (check host/port/database/credentials): {}"


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ident(value: str) -> str:
    """双引号标识符转义（防表名/schema 名注入）。"""
    return '"' + value.replace('"', '""') + '"'


def redact_credentials(text: str, dsn: str) -> str:
    """净化错误文本：DSN 整体与密码不对外（凭据红线，可单测）。"""
    redacted = text.replace(dsn, "postgresql://***")
    try:
        parsed = urlparse(dsn)
    except ValueError:
        return redacted
    if parsed.password:
        redacted = redacted.replace(parsed.password, "***")
    return redacted


def _resolve_dsn(spec: DataSourceSpec) -> str:
    """DSN 解析（Step 59，ADR-059）：options["dsn"] 内存态 > connection_ref
    统一解析链（进程环境变量 > secrets.env，`lookup_secret`）。"""
    dsn = spec.options.get("dsn")
    if isinstance(dsn, str) and dsn:
        return dsn
    if spec.connection_ref:
        ref = spec.connection_ref
        dsn = lookup_secret(ref)
        if dsn:
            return dsn
        raise DataSourceNotFoundError(
            f"postgres connection_ref not set (env or secrets.env): {ref}"
        )
    raise DataSourceNotFoundError(
        "postgres connector requires a dsn (options['dsn'] or connection_ref env/secrets)"
    )


class _RedactingExecutor:
    """PG 句柄专用执行器包装：DuckDB 异常净化后转 ConnectorError 传播。

    只读守卫（sql_guard）在 DuckDBExecutor 内先于执行触发，其异常
    （UnsafeSqlError 等连接器族）原样透传；查询期远程错误（断连等）
    同样净化，避免 DSN 出现在 DetectorRun.error / 调度 summary 等
    落库面。
    """

    def __init__(self, inner: DuckDBExecutor, dsn: str) -> None:
        self._inner = inner
        self._dsn = dsn

    def execute_setup(self, sql: str) -> None:
        try:
            self._inner.execute_setup(sql)
        except duckdb.Error as exc:
            raise ConnectorError(redact_credentials(str(exc), self._dsn)) from exc

    def register(self, name: str, obj: object) -> None:
        self._inner.register(name, obj)

    def execute(self, sql: str, params: SqlParams = None) -> pa.Table:
        try:
            return self._inner.execute(sql, params)
        except duckdb.Error as exc:
            raise ConnectorError(redact_credentials(str(exc), self._dsn)) from exc

    def execute_stream(self, sql: str, batch_size: int = 65536) -> Iterator[pa.RecordBatch]:
        try:
            yield from self._inner.execute_stream(sql, batch_size)
        except duckdb.Error as exc:
            raise ConnectorError(redact_credentials(str(exc), self._dsn)) from exc

    def close(self) -> None:
        self._inner.close()


class PostgresDataHandle(FileDataHandle):
    """PostgreSQL 远程表数据句柄（只读，表名必填，无文件字节）。"""

    requires_path: ClassVar[bool] = False

    def __init__(self, spec: DataSourceSpec) -> None:
        super().__init__(spec)
        if spec.table_name is None:
            raise DataSourceNotFoundError("postgres connector requires table_name")
        self._dsn = _resolve_dsn(spec)
        self._table_name = spec.table_name
        self._schema_name = spec.options.get("schema")
        assert isinstance(self._executor, DuckDBExecutor)
        self._executor = _RedactingExecutor(self._executor, self._dsn)

    @property
    def table_name(self) -> str | None:
        return self._table_name

    def _ensure_view(self) -> None:
        if self._view_ready:
            return
        try:
            self._executor.execute_setup("LOAD postgres")
        except ConnectorError as exc:
            raise ConnectorError(_PG_EXTENSION_HINT.format(exc)) from exc
        try:
            self._executor.execute_setup(
                f"ATTACH {_sql_string_literal(self._dsn)} AS {_DB_ALIAS} (TYPE postgres, READ_ONLY)"
            )
        except ConnectorError as exc:
            raise ConnectorError(_PG_CONNECT_HINT.format(exc)) from exc
        schema_ident = _ident(cast(str, self._schema_name) or "public")
        table_ident = f"{_DB_ALIAS}.{schema_ident}.{_ident(self._table_name)}"
        try:
            self._executor.execute(f"SELECT 1 FROM {table_ident} LIMIT 0")
        except ConnectorError as exc:
            raise DataSourceNotFoundError(
                f"postgres table not found: {self._schema_name or 'public'}.{self._table_name}"
            ) from exc
        self._executor.execute_setup(f"CREATE OR REPLACE VIEW data AS SELECT * FROM {table_ident}")
        self._view_ready = True

    def read_batches(self, batch_size: int = 65536) -> Iterator[FrameBatch]:
        self._check_open()
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self._ensure_view()
        row_offset = 0
        for idx, batch in enumerate(
            self._executor.execute_stream("SELECT * FROM data", batch_size)
        ):
            table = pa.Table.from_batches([batch])
            yield FrameBatch(
                source=self._spec,
                table=table,
                batch_index=idx,
                row_offset=row_offset,
            )
            row_offset += table.num_rows

    def content_fingerprint(self) -> str:
        """内容指纹（Step 55）：单查询全表哈希（行序无关、NULL 不折叠）。

        逐行哈希 = md5(concat_ws(chr(31), coalesce(col::VARCHAR, chr(0))...))，
        排序聚合后并入 schema_hash 与行数 —— 类型/数量/取值任一变化
        指纹即变；相同内容指纹恒定（供调度变更感知作跳过基准）。
        """
        self._check_open()
        self._ensure_view()
        signature = [(c.name, c.physical_type) for c in self.schema().columns]
        schema_hash = _schema_hash(signature)
        exprs = ", ".join(
            f"coalesce(cast({_ident(name)} AS VARCHAR), chr(0))" for name, _typ in signature
        )
        table = self._executor.execute(
            "SELECT count(*) AS n, md5(string_agg(rh, '' ORDER BY rh)) AS h FROM "
            f"(SELECT md5(concat_ws(chr(31), {exprs})) AS rh FROM data) t"
        )
        row_count = table.column(0).to_pylist()[0]
        rows_hash = table.column(1).to_pylist()[0]
        assert isinstance(row_count, int)
        digest = hashlib.sha256(f"{schema_hash}|{row_count}|{rows_hash or ''}".encode()).hexdigest()
        return digest

    def fingerprint(self, mode: FingerprintMode = "full") -> DatasetFingerprint:
        """PG 无文件字节：file_sha256=None，内容指纹放 content_sample_hash。

        sampled 档 MVP 与 full 同（远程源暂不做增量采样优化，ADR-055）。
        """
        self._check_open()
        signature = [(c.name, c.physical_type) for c in self.schema().columns]
        row_count = self.count_rows()
        content_hash = self.content_fingerprint() if mode != "metadata_only" else None
        return DatasetFingerprint(
            dataset_id=self._spec.options.get("dataset_id", ""),
            fingerprint_type=mode,
            file_sha256=None,
            schema_hash=_schema_hash(signature),
            row_count=row_count,
            column_count=len(signature),
            column_signature=signature,
            content_sample_hash=content_hash,
        )


class PostgresConnector:
    """PostgreSQL 数据源连接器（Step 55，V4，ADR-055）。"""

    connector_id = "postgres"
    display_name = "PostgreSQL"

    def supports(self, source: DataSourceSpec) -> bool:
        if source.source_type != DataSourceType.POSTGRESQL:
            return False
        dsn = source.options.get("dsn")
        return (isinstance(dsn, str) and bool(dsn)) or source.connection_ref is not None

    def open(self, source: DataSourceSpec) -> PostgresDataHandle:
        if source.table_name is None:
            raise DataSourceNotFoundError(
                "postgres connector requires table_name (e.g. scan --table <table>)"
            )
        return PostgresDataHandle(source)

    def close(self) -> None:
        pass


__all__ = ["PostgresConnector", "PostgresDataHandle", "redact_credentials"]
