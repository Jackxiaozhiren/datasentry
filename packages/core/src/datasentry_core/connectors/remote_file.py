"""云存储文件源连接器（Step 57，V5：云存储文件源，ADR-057）。

经 DuckDB httpfs 扩展只读直读对象存储：s3://、gs://、az:// 前缀的
CSV/Parquet/JSONL 文件，注册只读 data 视图后复用 FileDataHandle
共享实现（schema/read_sample/sql_aggregate/count_rows/warnings）。
零新依赖（httpfs 随 DuckDB 分发，离线需预装）。

凭据红线（沿用 Step 55/56 全套语义）：
- 凭据只走进程环境变量（AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY 等
  httpfs 原生读取）或 spec.options 内存态覆盖；不落库、不进日志/
  evidence/报告
- 非机密会话配置（endpoint/region/url_style/use_ssl）可经 options
  传入（如 MinIO：options["s3_endpoint"]），有自定义 endpoint 时
  自动 path-style + 非 SSL（MinIO 必需）；无 endpoint 走 AWS 默认
  （vhost-style + SSL）
- httpfs 错误文本可能回显完整 URL（az:// 可含 SAS token 查询参数）：
  _RemoteRedactingExecutor 将 URI 整体净化后转 ConnectorError 传播

变更感知（快速失效层）：
- httpfs 不暴露对象 ETag（glob 仅 file 列）→ `content_fingerprint()`
  用 size + last_modified 元数据组合哈希（read_blob 元数据列，
  HEAD 级开销 ~0.004s，免下载）作为调度跳过判定基准
- 语义：同内容 → size/mtime 不变 → 指纹相同（跳过）；变更（重新
  上传）→ Last-Modified 必更新 → 指纹变（重扫）。已知局限（ADR-057）：
  同秒内同 size 覆盖的极限窗口会漏判；元数据抖动会多扫一次（无害）
- 源不可达（对象删除/断网）→ DataSourceNotFoundError，调度回退
  正常失败路径，不误跳过
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from typing import ClassVar

import duckdb
import pyarrow as pa

from datasentry_core.connectors.base import FingerprintMode, FrameBatch
from datasentry_core.connectors.errors import ConnectorError, DataSourceNotFoundError
from datasentry_core.connectors.file_based import FileDataHandle, _schema_hash
from datasentry_core.connectors.spec import DataSourceSpec, DataSourceType
from datasentry_core.engine import DuckDBExecutor
from datasentry_core.engine.base import SqlParams
from datasentry_core.models.fingerprint import DatasetFingerprint

#: 云存储 URI 前缀（httpfs 原生支持的三种对象存储）
_CLOUD_PREFIXES: tuple[str, ...] = ("s3://", "gs://", "az://")

#: 扩展加载失败提示（缺网络/离线/发行版未带扩展）
_HTTPFS_EXTENSION_HINT = (
    "failed to load duckdb httpfs extension: network access is required on first use "
    "(or the extension must be pre-provisioned for offline environments); "
    "original error: {}"
)

#: read_* 函数按格式分派
_READ_FUNCS = {
    DataSourceType.CSV: "read_csv_auto",
    DataSourceType.PARQUET: "read_parquet",
    DataSourceType.JSONL: "read_json_auto",
}


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def redact_uri(text: str, uri: str) -> str:
    """净化错误文本：完整 URI（含 az:// SAS token 等查询参数）不对外。"""
    return text.replace(uri, "<remote-uri>")


def _is_cloud_uri(path: str) -> bool:
    return path.startswith(_CLOUD_PREFIXES)


def _s3_session_options(spec: DataSourceSpec) -> list[str]:
    """S3 会话配置（仅 s3://）：endpoint 经 options 或标准 env；凭据只走 env。

    有自定义 endpoint（MinIO 等）→ 自动 path-style + 非 SSL（MinIO 必需）；
    无 endpoint → AWS 默认（vhost-style + SSL），零配置。
    """
    options = spec.options
    endpoint = (
        options.get("s3_endpoint")
        or os.environ.get("AWS_ENDPOINT_URL_S3")
        or os.environ.get("AWS_ENDPOINT_URL")
    )
    if not endpoint:
        return []
    statements = [
        f"SET s3_endpoint = {_sql_string_literal(str(endpoint))}",
        "SET s3_region = "
        f"{_sql_string_literal(str(options.get('s3_region') or os.environ.get('AWS_REGION') or 'us-east-1'))}",  # noqa: E501
        f"SET s3_url_style = {_sql_string_literal(str(options.get('s3_url_style') or 'path'))}",
        f"SET s3_use_ssl = {_sql_string_literal(str(options.get('s3_use_ssl', 'false')).lower())}",
    ]
    return statements


class _RemoteRedactingExecutor:
    """云文件句柄专用执行器包装：DuckDB 异常净化（URI 打码）后转 ConnectorError。

    只读守卫（sql_guard）在 DuckDBExecutor 内先于执行触发，其异常
    （UnsafeSqlError 等连接器族）原样透传；httpfs 错误文本可能回显
    完整 URL（az:// SAS token），净化后传播避免凭据出现在落库面。
    """

    def __init__(self, inner: DuckDBExecutor, uri: str) -> None:
        self._inner = inner
        self._uri = uri

    def execute_setup(self, sql: str) -> None:
        try:
            self._inner.execute_setup(sql)
        except duckdb.Error as exc:
            raise ConnectorError(redact_uri(str(exc), self._uri)) from exc

    def register(self, name: str, obj: object) -> None:
        self._inner.register(name, obj)

    def execute(self, sql: str, params: SqlParams = None) -> pa.Table:
        try:
            return self._inner.execute(sql, params)
        except duckdb.Error as exc:
            raise ConnectorError(redact_uri(str(exc), self._uri)) from exc

    def execute_stream(self, sql: str, batch_size: int = 65536) -> Iterator[pa.RecordBatch]:
        try:
            yield from self._inner.execute_stream(sql, batch_size)
        except duckdb.Error as exc:
            raise ConnectorError(redact_uri(str(exc), self._uri)) from exc

    def close(self) -> None:
        self._inner.close()


class RemoteFileDataHandle(FileDataHandle):
    """云存储文件数据句柄（只读，s3:// gs:// az:// 前缀）。"""

    requires_path: ClassVar[bool] = True

    def __init__(self, spec: DataSourceSpec) -> None:
        super().__init__(spec)
        if not isinstance(self._path, str) or not _is_cloud_uri(self._path):
            raise DataSourceNotFoundError(
                f"remote file connector requires a cloud uri (s3:// gs:// az://), got: {self._path}"
            )
        self._uri = self._path
        assert isinstance(self._executor, DuckDBExecutor)
        self._executor = _RemoteRedactingExecutor(self._executor, self._uri)

    @property
    def source_path(self) -> str | None:
        return self._uri

    def _ensure_view(self) -> None:
        if self._view_ready:
            return
        try:
            self._executor.execute_setup("LOAD httpfs")
        except ConnectorError as exc:
            raise ConnectorError(_HTTPFS_EXTENSION_HINT.format(exc)) from exc
        for statement in _s3_session_options(self._spec):
            self._executor.execute_setup(statement)
        read_func = _READ_FUNCS[self._spec.source_type]
        try:
            self._executor.execute(
                f"SELECT 1 FROM {read_func}({_sql_string_literal(self._uri)}) LIMIT 0"
            )
        except ConnectorError as exc:
            raise DataSourceNotFoundError(
                f"remote file not found or unreadable: {read_func}: {exc}"
            ) from exc
        self._executor.execute_setup(
            "CREATE OR REPLACE VIEW data AS "
            f"SELECT * FROM {read_func}({_sql_string_literal(self._uri)})"
        )
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
        """内容指纹（Step 57）：size + last_modified 元数据组合（快速失效层）。

        read_blob 元数据列（HEAD 级开销 ~0.004s）免下载；对象重新上传
        Last-Modified 必更新 → 指纹变。极限窗口（同秒同 size 覆盖）与
        元数据抖动行为记录在 ADR-057 已知局限。
        """
        self._check_open()
        self._ensure_view()
        try:
            meta = self._executor.execute(
                f"SELECT size, last_modified FROM read_blob({_sql_string_literal(self._uri)})"
            )
        except ConnectorError as exc:
            raise DataSourceNotFoundError(f"remote file metadata unavailable: {exc}") from exc
        rows = meta.to_pylist()
        if not rows:
            raise DataSourceNotFoundError(f"remote file not found: {self._uri}")
        size = rows[0]["size"]
        last_modified = str(rows[0]["last_modified"])
        assert isinstance(size, int)
        return hashlib.sha256(f"{self._uri}|{size}|{last_modified}".encode()).hexdigest()

    def fingerprint(self, mode: FingerprintMode = "full") -> DatasetFingerprint:
        """云文件无本地字节：file_sha256=None，内容指纹（快速失效层）放
        content_sample_hash（与 PG/MySQL 语义一致）。"""
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


class RemoteFileConnector:
    """云存储文件源连接器（Step 57，V5，ADR-057）：CSV/Parquet/JSONL。"""

    connector_id = "remote"
    display_name = "Cloud Storage"

    def supports(self, source: DataSourceSpec) -> bool:
        return (
            source.source_type in _READ_FUNCS
            and isinstance(source.path, str)
            and _is_cloud_uri(source.path)
        )

    def open(self, source: DataSourceSpec) -> RemoteFileDataHandle:
        return RemoteFileDataHandle(source)

    def close(self) -> None:
        pass


__all__ = ["RemoteFileConnector", "RemoteFileDataHandle", "_is_cloud_uri", "redact_uri"]
