"""跨表检测器（Step 40，integrity 维度第一个真实检测器）。

外键完整性：主表列值必须存在于引用表对应列。对每个契约声明
的 TableReference 计算孤儿行（主表非 NULL 但引用表无匹配的行）。

实现说明：DetectionContext 只持有主表句柄，引用源由契约显式声明
（调用方信任域），因此本检测器自建 DuckDBExecutor 并把主表与所有
引用表建成只读视图后执行 LEFT JOIN。支持主表/引用文件为
CSV/Parquet/JSONL/DuckDB（DuckDB 需 table 名；XLSX 引用文件 MVP
不支持，supports 会排除）。路径与标识符一律转义，防注入。
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from datasentry_core.connectors.spec import DataSourceType
from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.common import DetectorBase, make_candidate, make_evidence
from datasentry_core.engine import DuckDBExecutor
from datasentry_core.models.contract import TableReference
from datasentry_core.models.detector import DetectorCapabilities, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _source_type_for_path(path: str) -> DataSourceType | None:
    suffix = Path(path).suffix.lower()
    mapping: dict[str, DataSourceType] = {
        ".csv": DataSourceType.CSV,
        ".tsv": DataSourceType.CSV,
        ".parquet": DataSourceType.PARQUET,
        ".jsonl": DataSourceType.JSONL,
        ".ndjson": DataSourceType.JSONL,
        ".duckdb": DataSourceType.DUCKDB,
    }
    return mapping.get(suffix)


class ForeignKeyViolationDetector(DetectorBase):
    """外键完整性（11 章 integrity 维度）：契约 references 逐条比较。

    无 references 时 supports=False，不影响单表扫描。孤儿行按行统计，
    issue 细化为列级（每个主表列一条）。
    """

    detector_id = "foreign_key_violation"
    display_name = "Foreign-Key Violation"
    description = (
        "Compares contract-declared cross-table references: rows whose key "
        "has no matching value in the referenced table are orphaned."
    )
    quality_dimension = QualityDimension.INTEGRITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(
        supports_sql_pushdown=True,
        requires_row_materialization=False,
    )
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"max_orphan_ratio": 0.0}

    def supports(self, context: DetectionContext) -> bool:
        if not context.references:
            return False
        main_type = context.handle.source_type
        if main_type not in (DataSourceType.CSV, DataSourceType.PARQUET, DataSourceType.DUCKDB):
            return False
        return any(
            _source_type_for_path(ref.path) is not None
            and not (_source_type_for_path(ref.path) == DataSourceType.DUCKDB and not ref.table)
            for ref in context.references
        )

    def _main_view(self, context: DetectionContext, alias: str) -> str | None:
        """主表 → CREATE VIEW alias AS ...；不支持的类型返回 None。"""
        source_type = context.handle.source_type
        path = context.handle.source_path
        if path is None:
            return None
        if source_type == DataSourceType.CSV:
            return (
                f"CREATE OR REPLACE VIEW {alias} AS "
                f"SELECT * FROM read_csv_auto({_sql_string_literal(str(path))})"
            )
        if source_type == DataSourceType.PARQUET:
            return (
                f"CREATE OR REPLACE VIEW {alias} AS "
                f"SELECT * FROM read_parquet({_sql_string_literal(str(path))})"
            )
        if source_type == DataSourceType.DUCKDB:
            table_name = context.handle.table_name
            if table_name is None:
                return None
            attach = f"ATTACH {_sql_string_literal(str(path))} AS _main_db (READ_ONLY)"
            return (
                attach + ";" + f"CREATE OR REPLACE VIEW {alias} AS "
                f"SELECT * FROM _main_db.{_ident(table_name)}"
            )
        return None

    def _ref_view(self, executor: DuckDBExecutor, ref: TableReference, alias: str) -> bool:
        """引用表 → CREATE VIEW alias AS ...；成功返回 True。"""
        ref_type = _source_type_for_path(ref.path)
        if ref_type is None:
            return False
        if ref_type == DataSourceType.DUCKDB:
            if ref.table is None:
                return False
            qualified = (
                f"{_ident(str(ref.schema_name))}.{_ident(ref.table)}"
                if ref.schema_name
                else _ident(ref.table)
            )
            executor.execute_setup(f"ATTACH {_sql_string_literal(ref.path)} AS {alias} (READ_ONLY)")
            executor.execute_setup(
                f"CREATE OR REPLACE VIEW {alias}_tbl AS SELECT * FROM {alias}.{qualified}"
            )
            return True
        if ref_type == DataSourceType.CSV:
            sql = f"SELECT * FROM read_csv_auto({_sql_string_literal(ref.path)})"
        elif ref_type == DataSourceType.PARQUET:
            sql = f"SELECT * FROM read_parquet({_sql_string_literal(ref.path)})"
        else:
            sql = (
                "SELECT * FROM read_json_auto("
                f"{_sql_string_literal(ref.path)}, format='newline_delimited')"
            )
        executor.execute_setup(f"CREATE OR REPLACE VIEW {alias}_tbl AS {sql}")
        return True

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        main_sql = self._main_view(context, "_main")
        if main_sql is None:
            return candidates
        executor = DuckDBExecutor()
        try:
            for stmt in main_sql.split(";"):
                if stmt.strip():
                    executor.execute_setup(stmt)
            for ref in context.references:
                alias = f"_ref_{len(candidates)}"
                if not self._ref_view(executor, ref, alias):
                    continue
                ref_table = f"{alias}_tbl"
                for main_col, ref_col in ref.columns.items():
                    if main_col not in context.columns:
                        continue
                    try:
                        row = executor.execute(
                            "SELECT "
                            "(SELECT count(*) FROM _main WHERE _main."
                            + _ident(main_col)
                            + " IS NOT NULL) AS total, count(*) AS orphan_count "
                            "FROM _main LEFT JOIN "
                            + ref_table
                            + " ON _main."
                            + _ident(main_col)
                            + " = "
                            + ref_table
                            + "."
                            + _ident(ref_col)
                            + " AND "
                            + ref_table
                            + "."
                            + _ident(ref_col)
                            + " IS NOT NULL WHERE _main."
                            + _ident(main_col)
                            + " IS NOT NULL AND "
                            + ref_table
                            + "."
                            + _ident(ref_col)
                            + " IS NULL"
                        ).to_pylist()[0]
                    except Exception:
                        continue
                    total = int(row["total"]) if row["total"] is not None else 0
                    orphan_count = (
                        int(row["orphan_count"]) if row["orphan_count"] is not None else 0
                    )
                    orphan_ratio = orphan_count / total if total > 0 else 0.0
                    if orphan_count == 0:
                        continue
                    evidence = make_evidence(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        evidence_type=EvidenceType.CONSTRAINT_VIOLATION,
                        description=(f"orphan rows for {main_col} -> {ref.name}.{ref_col}"),
                        data={
                            "reference": ref.name,
                            "reference_column": ref_col,
                            "orphan_count": orphan_count,
                            "orphan_ratio": orphan_ratio,
                            "main_total_non_null": total,
                        },
                    )
                    candidates.append(
                        make_candidate(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            context=context,
                            issue_type="foreign_key_violation",
                            columns=[main_col],
                            affected_count=orphan_count,
                            evidence=[evidence],
                            raw_score=min(orphan_ratio * 100.0, 100.0),
                            confidence=0.95,
                            severity=Severity.HIGH,
                            fpr=0.05,
                        )
                    )
        finally:
            executor.close()
        return candidates
