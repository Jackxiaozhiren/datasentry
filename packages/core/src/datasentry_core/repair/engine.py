"""修复引擎（12.5/15 章 MVP 子集，Step 19，ADR-020）。

闭环：propose（Issue → 提案）→ preview（统计面板 + 检测器重跑）→
apply（修复副本 + before artifact）→ rollback（artifact 全量重建）。

设计约束（ADR-020）：
- 只支持确定性、值级操作（trim/case/token→null/set_null/clip）；
  impute 等推断类归 V1（伪造数据风险）
- 原文件永不变；产物在 <workspace>/.datasentry/repairs/
- 回滚 = before artifact 全量重建（operation log 仅样本）
- rule_failures 前后对比 = 重跑同检测器（副本上）
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
import uuid
from itertools import zip_longest
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq

from datasentry_core.connectors import (
    DataSourceSpec,
    DataSourceType,
    default_registry,
)
from datasentry_core.detectors import DetectionContext, DetectorRegistry
from datasentry_core.detectors.common import quote_ident, quote_literal, quote_re
from datasentry_core.models.enums import (
    RepairOperation,
    RepairProposalStatus,
    RepairRunStatus,
    RiskLevel,
)
from datasentry_core.models.issue import Issue
from datasentry_core.models.repair import (
    RepairOperationRecord,
    RepairPreview,
    RepairProposal,
    RepairRun,
    RowBeforeAfter,
)
from datasentry_core.storage.paths import project_repairs_dir

# 缺失标记（与 suspicious_missing_token 检测器一致）
_MISSING_TOKENS = (
    "na",
    "n/a",
    "null",
    "none",
    "-",
    "?",
    "unknown",
    "missing",
    "todo",
    "tbd",
    "n.a.",
)
_ISO_DT_RE = r"^\d{4}-\d{2}-\d{2}$"
_OPERATION_LOG_CAP = 500
_EXAMPLE_CAP = 10

# Issue 类型 → 修复操作（MVP 确定性值级子集；其余检测器不自动提案）
_PROPOSAL_MAP: dict[str, RepairOperation] = {
    "leading_or_trailing_whitespace": RepairOperation.TRIM_WHITESPACE,
    "inconsistent_case": RepairOperation.NORMALIZE_CASE,
    "suspicious_missing_token": RepairOperation.REPLACE_MISSING_TOKEN,
    "invalid_date": RepairOperation.SET_NULL,
    "impossible_date": RepairOperation.SET_NULL,
}
# CLIP_VALUE：仅当 evidence 提供 lower/upper 边界（数值离群类）
_CLIP_ISSUE_TYPES = frozenset({"iqr_outlier", "percentile_outlier", "modified_zscore"})

_RATIONALE: dict[RepairOperation, str] = {
    RepairOperation.TRIM_WHITESPACE: "strip leading/trailing whitespace",
    RepairOperation.NORMALIZE_CASE: "lowercase values to a single canonical form",
    RepairOperation.REPLACE_MISSING_TOKEN: "replace missing stand-ins with NULL",
    RepairOperation.SET_NULL: "set invalid values to NULL (missing semantics)",
    RepairOperation.CLIP_VALUE: "clip values to the detected outlier bounds",
}


def _after_expr(operation: RepairOperation, column: str, params: dict[str, Any]) -> str:
    """修复操作的 SQL 表达式（只读视图上计算 after 值）。"""
    q = quote_ident(column)
    if operation == RepairOperation.TRIM_WHITESPACE:
        return f"trim({q})"
    if operation == RepairOperation.NORMALIZE_CASE:
        return f"lower({q})"
    if operation == RepairOperation.REPLACE_MISSING_TOKEN:
        tokens = ", ".join(quote_literal(t) for t in _MISSING_TOKENS)
        return f"CASE WHEN lower(trim({q})) IN ({tokens}) THEN NULL ELSE {q} END"
    if operation == RepairOperation.SET_NULL:
        return (
            f"CASE WHEN {q} IS NOT NULL "
            f"AND NOT regexp_matches(trim({q}), {quote_re(_ISO_DT_RE)}) "
            f"OR ({q} IS NOT NULL AND regexp_matches(trim({q}), {quote_re(_ISO_DT_RE)}) "
            f"AND try_strptime(trim({q}), '%Y-%m-%d') IS NULL) "
            f"THEN NULL ELSE {q} END"
        )
    if operation == RepairOperation.CLIP_VALUE:
        lower = float(params["lower"])
        upper = float(params["upper"])
        return f"CASE WHEN {q} < {lower} THEN {lower} WHEN {q} > {upper} THEN {upper} ELSE {q} END"
    raise ValueError(f"unsupported repair operation: {operation}")


def cells_equal(left: object, right: object) -> bool:
    """Same cell content on both sides -- which is not the same question as ``==``.

    Value-level by design: ``True``/``1``, ``Decimal("1.0")``/``1.0`` and an int64 cell whose copy
    became a double all answer "not changed", because the cell's content is the same number. The
    audit question is whether a repair moved a value, not whether two readers picked the same
    physical type; a repair never retypes a column, and ``table_diff`` reads both artefacts with
    the same reader so a type drift here would be the reader's, not the file's.

    ``float("nan") != float("nan")`` is true, so a plain comparison reported a change between two
    byte-identical Parquet files. The artefact audit asks "did this cell change", and NaN staying
    NaN is not a change. The UI's diff highlight uses this same predicate so the two faces cannot
    disagree about which cells moved (D2-10).

    Nested values recurse through this same predicate: pandas and pyarrow write ``NaN`` into
    ``list<double>`` and struct columns as routine output, and a top-level-only rule kept calling
    two byte-identical files changed there too (F3).
    """
    if isinstance(left, float) and isinstance(right, float):
        return bool((math.isnan(left) and math.isnan(right)) or left == right)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            cells_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            cells_equal(left[key], right[key]) for key in left
        )
    return bool(left == right)


def changed_cells(before: list[object], after: list[object]) -> set[int]:
    """Which column slots of one row differ -- the only predicate the faces use for that.

    The UI highlights cells, the CLI lists them; the CLI compared with its own ``!=`` while the UI
    used `cells_equal`, so the two faces could disagree about a cell the diff had already put in
    `changed` -- exactly what D2-10 set out to make impossible (F9).
    """
    width = max(len(before), len(after))
    return {
        index
        for index in range(width)
        if not cells_equal(
            before[index] if index < len(before) else None,
            after[index] if index < len(after) else None,
        )
    }


def rows_equal(left: list[object] | None, right: list[object] | None) -> bool:
    """Row-wise `cells_equal`, for two rows that may differ in length."""
    if left is None or right is None:
        return left is right
    if len(left) != len(right):
        return False
    return all(cells_equal(a, b) for a, b in zip(left, right, strict=True))


def _dedupe_names(names: list[str]) -> list[str]:
    """Give one side's columns unique display names, appending `_1`, `_2` on collision.

    Display only: `table_diff` aligns CSV/Parquet/XLSX by position, so this spelling never decides
    which value lands in which column. It cannot be relied on to reproduce DuckDB's rewrite --
    measured: on `a_1,a,a` the connector yields `a_1`/`a`/`a_1_1` while this yields
    `a_1`/`a`/`a_2` -- which is exactly why alignment does not go through names for a format whose
    column order *is* its schema (F4).
    """
    assigned: set[str] = set()
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        count = seen.get(name, 0)
        candidate = name if count == 0 else f"{name}_{count}"
        while candidate in assigned:
            count += 1
            candidate = f"{name}_{count}"
        seen[name] = count + 1
        assigned.add(candidate)
        out.append(candidate)
    return out


def _arrow_rows(table: pa.Table) -> list[list[object]]:
    """按位置取全部单元格（列顺序就是文件里的列顺序）。"""
    columns = [column.to_pylist() for column in table.columns]
    return [[columns[i][row] for i in range(len(columns))] for row in range(table.num_rows)]


#: Source types whose copy is rebuilt cell-by-cell from the source's own cells (G-1).
#: Measured collateral rewrites live here; JSONL and Parquet round-trip values and types
#: faithfully, so they keep the projection path.
_CELL_CARRY_SOURCE_TYPES = frozenset({DataSourceType.CSV, DataSourceType.XLSX})

#: Source types `_write_table` can round-trip. `_suffix` indexes the same idea by extension.
_REPAIRABLE_SOURCE_TYPES = frozenset(
    {DataSourceType.CSV, DataSourceType.PARQUET, DataSourceType.JSONL, DataSourceType.XLSX}
)


class RepairEngine:
    """确定性修复引擎（15 章 MVP 子集）。"""

    def propose(self, issue: Issue, context: DetectionContext) -> RepairProposal | None:
        """Issue → 修复提案；不支持的 issue 返回 None。

        融合后 Issue.issue_type 是家族名（string_format 等），原始类型在
        detector_ids（与检测器 issue_type 同名），按可修复优先级挑选。
        """
        bounds = self._clip_bounds(issue)
        clip_ok = bounds is not None and len(issue.columns) == 1
        source: str | None = None
        operation: RepairOperation | None = None
        for detector_id in issue.detector_ids:
            if detector_id in _PROPOSAL_MAP:
                source = detector_id
                operation = _PROPOSAL_MAP[detector_id]
                break
            if clip_ok and detector_id in _CLIP_ISSUE_TYPES:
                source = detector_id
                operation = RepairOperation.CLIP_VALUE
                break
        if source is None or operation is None:
            return None
        columns = list(issue.columns)
        if not columns:
            return None
        params: dict[str, Any] = {}
        if operation == RepairOperation.CLIP_VALUE:
            assert bounds is not None
            params = {"lower": bounds[0], "upper": bounds[1]}
        affected = self._affected_rows(context, operation, columns, params)
        if affected <= 0:
            return None
        return RepairProposal(
            proposal_id=f"prop_{uuid.uuid4().hex[:12]}",
            issue_id=issue.id,
            issue_type=source,
            operation=operation,
            target_columns=columns,
            parameters=params,
            rationale=_RATIONALE[operation],
            evidence_ids=[e.evidence_id for e in issue.evidence],
            risk_level=self._risk_level(operation),
            reversibility="fully_reversible"
            if operation != RepairOperation.SET_NULL
            else "partially_reversible",
            estimated_rows_changed=affected,
            status=RepairProposalStatus.PROPOSED,
        )

    @staticmethod
    def _clip_bounds(issue: Issue) -> tuple[float, float] | None:
        for evidence in issue.evidence:
            lower = evidence.data.get("lower")
            upper = evidence.data.get("upper")
            if lower is not None and upper is not None:
                return float(lower), float(upper)
        return None

    @staticmethod
    def _risk_level(operation: RepairOperation) -> RiskLevel:
        if operation in (
            RepairOperation.SET_NULL,
            RepairOperation.CLIP_VALUE,
            RepairOperation.REPLACE_MISSING_TOKEN,
        ):
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _affected_rows(
        context: DetectionContext,
        operation: RepairOperation,
        columns: list[str],
        params: dict[str, object],
    ) -> int:
        """估算受影响行数（任一目标列变化即计）。"""
        total = 0
        for column in columns:
            q = quote_ident(column)
            expr = _after_expr(operation, column, params)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data WHERE {q} IS DISTINCT FROM ({expr})"
            ).table
            total += int(table.column("n").to_pylist()[0])
        return total

    def preview(
        self,
        proposal: RepairProposal,
        context: DetectionContext,
        registry: DetectorRegistry,
    ) -> RepairPreview:
        """预览：统计面板 + 检测器重跑前后对比（临时副本，无副作用）。"""
        # `preview` writes a temporary copy to re-run the rules against it, so it needs the same
        # copy writer `apply` does. Guarding only `apply` left preview raising a bare KeyError for
        # sqlite/postgres while apply's own message promised "propose and preview work" (F6).
        self._require_copy_writer(context.handle.source_type)
        before_counts = self._rule_failures(proposal, context, registry)
        stats = self._stats(context, proposal)
        with tempfile.TemporaryDirectory() as tmp:
            handle_source = context.handle.source_path
            source_for_dialect = handle_source if isinstance(handle_source, Path) else None
            tmp_path = Path(tmp) / ("preview" + self._suffix(context, source_for_dialect))
            after_table = self._after_table(context, proposal)
            cells = self._copy_cells(context, after_table, proposal, source_for_dialect)
            self._write_table(tmp_path, after_table, context, source_for_dialect, cells)
            after_handle = default_registry().open(
                DataSourceSpec(
                    source_type=context.handle.source_type,
                    path=tmp_path,
                    options={"dataset_id": context.dataset_id},
                )
            )
            try:
                after_ctx = DetectionContext(
                    dataset_id=context.dataset_id,
                    table_name=None,
                    columns=after_handle.schema().column_names,
                    handle=after_handle,
                )
                after_counts = self._rule_failures(proposal, after_ctx, registry)
            finally:
                after_handle.close()
        row_count = max(context.handle.count_rows(), 1)
        return RepairPreview(
            proposal_id=proposal.proposal_id,
            rows_changed=proposal.estimated_rows_changed,
            rows_changed_ratio=round(min(1.0, proposal.estimated_rows_changed / row_count), 6),
            null_delta=stats["null_delta"],
            unique_delta=stats["unique_delta"],
            rule_failures_before=before_counts,
            rule_failures_after=after_counts,
            changed_examples=self._examples(context, proposal),
        )

    def apply(
        self,
        proposal: RepairProposal,
        context: DetectionContext,
        workspace: Path,
        source_scan_run_id: str | None = None,
    ) -> RepairRun:
        """应用：写修复副本（<run_id><ext>）+ before artifact（.before<ext>）。"""
        run_id = f"rep_{uuid.uuid4().hex[:12]}"
        source_path = context.handle.source_path
        if source_path is None or isinstance(source_path, str) or not source_path.exists():
            raise FileNotFoundError("repair requires an on-disk source file")
        # Refuse before touching the disk -- including the directory. An unsupported type used to
        # leave two artefacts behind and *then* fail, with no `RepairRun`: nothing to diff, nothing
        # to roll back (B-5), and the refusal still created `.datasentry/repairs/` (F8).
        self._require_copy_writer(context.handle.source_type)
        # Compute the projection and the cell carry-over first: a source whose rows cannot be read
        # verbatim refuses here, before the snapshot exists -- the same rule B-5/F8 set, applied to
        # the new refusal path (G-1).
        after_table = self._after_table(context, proposal)
        cells = self._copy_cells(context, after_table, proposal, source_path)
        repairs_dir = project_repairs_dir(workspace)
        repairs_dir.mkdir(parents=True, exist_ok=True)
        suffix = self._suffix(context, source_path)
        artifact_path = repairs_dir / f"{run_id}.before{suffix}"
        output_path = repairs_dir / f"{run_id}{suffix}"
        shutil.copy2(source_path, artifact_path)
        self._write_table(output_path, after_table, context, source_path, cells)
        fingerprint_before = context.handle.fingerprint()
        after_handle = default_registry().open(
            DataSourceSpec(
                source_type=context.handle.source_type,
                path=output_path,
                options={"dataset_id": context.dataset_id},
            )
        )
        try:
            fingerprint_after = after_handle.fingerprint()
        finally:
            after_handle.close()
        operations = self._operation_records(context, proposal)
        return RepairRun(
            id=run_id,
            dataset_id=context.dataset_id,
            proposal_id=proposal.proposal_id,
            source_scan_run_id=source_scan_run_id,
            fingerprint_before=fingerprint_before.file_sha256 or "",
            fingerprint_after=fingerprint_after.file_sha256 or "",
            operations=operations,
            rollback_artifact=str(artifact_path),
            status=RepairRunStatus.APPLIED,
        )

    def rollback(self, run: RepairRun, workspace: Path) -> RepairRun:
        """回滚：before artifact 全量重建修复副本（<id>.rolled_back<ext>）。"""
        if run.rollback_artifact is None:
            raise ValueError("run has no rollback artifact")
        artifact = Path(run.rollback_artifact)
        if not artifact.exists():
            raise FileNotFoundError(f"rollback artifact not found: {artifact}")
        repairs_dir = project_repairs_dir(workspace)
        # artifact 命名 <run_id>.before<suffix>，解析副本后缀
        prefix = f"{run.id}.before"
        if artifact.name.startswith(prefix):
            suffix = artifact.name[len(prefix) :]
        else:
            suffix = artifact.suffix
        output_path = repairs_dir / f"{run.id}.rolled_back{suffix}"
        shutil.copy2(artifact, output_path)
        return run.model_copy(update={"status": RepairRunStatus.ROLLED_BACK})

    # ---- 内部 -----------------------------------------------------------

    def _after_table(self, context: DetectionContext, proposal: RepairProposal) -> pa.Table:
        """修复后的全量表（SQL 表达式，其余列原样）。"""
        select = []
        for column in context.columns:
            if column in proposal.target_columns:
                q = quote_ident(column)
                select.append(
                    f"{_after_expr(proposal.operation, column, proposal.parameters)} AS {q}"
                )
            else:
                select.append(quote_ident(column))
        return context.handle.sql_aggregate(f"SELECT {', '.join(select)} FROM data").table

    def _stats(
        self, context: DetectionContext, proposal: RepairProposal
    ) -> dict[str, dict[str, int]]:
        """null_delta / unique_delta（修复后 − 修复前）。"""
        null_delta: dict[str, int] = {}
        unique_delta: dict[str, int] = {}
        for column in proposal.target_columns:
            q = quote_ident(column)
            before = context.handle.sql_aggregate(
                f"SELECT sum({q} IS NULL) AS n, count(DISTINCT {q}) AS u FROM data"
            ).table
            expr = _after_expr(proposal.operation, column, proposal.parameters)
            after = context.handle.sql_aggregate(
                f"SELECT sum(({expr}) IS NULL) AS n, count(DISTINCT ({expr})) AS u FROM data"
            ).table
            null_delta[column] = int(after.column("n").to_pylist()[0]) - int(
                before.column("n").to_pylist()[0]
            )
            unique_delta[column] = int(after.column("u").to_pylist()[0]) - int(
                before.column("u").to_pylist()[0]
            )
        return {"null_delta": null_delta, "unique_delta": unique_delta}

    def _examples(
        self, context: DetectionContext, proposal: RepairProposal
    ) -> list[RowBeforeAfter]:
        """前 _EXAMPLE_CAP 个变化行（列级 before/after 示例）。"""
        examples: list[RowBeforeAfter] = []
        for column in proposal.target_columns:
            q = quote_ident(column)
            expr = _after_expr(proposal.operation, column, proposal.parameters)
            table = context.handle.sql_aggregate(
                f"SELECT {q} AS before_value, ({expr}) AS after_value FROM data "
                f"WHERE {q} IS DISTINCT FROM ({expr}) LIMIT {_EXAMPLE_CAP}"
            ).table
            before_values = table.column("before_value").to_pylist()
            after_values = table.column("after_value").to_pylist()
            for i, (before, after) in enumerate(zip(before_values, after_values, strict=True)):
                examples.append(
                    RowBeforeAfter(
                        row_id=f"{column}:{i}",
                        column=column,
                        before=before,
                        after=after,
                        reason=_RATIONALE[proposal.operation],
                    )
                )
        return examples

    def _rule_failures(
        self,
        proposal: RepairProposal,
        context: DetectionContext,
        registry: DetectorRegistry,
    ) -> dict[str, int]:
        """目标检测器在（原始/修复副本）上的候选数。"""
        detector_id = _DETECTOR_FOR_ISSUE.get(proposal.issue_type, "")
        if not detector_id:
            return {}
        try:
            detector = registry.get(detector_id)
        except KeyError:
            return {}
        if not detector.supports(context):
            return {}
        return {proposal.issue_type: len(detector.detect(context))}

    def _operation_records(
        self, context: DetectionContext, proposal: RepairProposal
    ) -> list[RepairOperationRecord]:
        """行级 before/after 记录样本（前 _OPERATION_LOG_CAP 条，回滚不依赖）。"""
        records: list[RepairOperationRecord] = []
        for column in proposal.target_columns:
            q = quote_ident(column)
            expr = _after_expr(proposal.operation, column, proposal.parameters)
            table = context.handle.sql_aggregate(
                f"SELECT {q} AS before_value, ({expr}) AS after_value FROM data "
                f"WHERE {q} IS DISTINCT FROM ({expr}) LIMIT {_OPERATION_LOG_CAP}"
            ).table
            before_values = table.column("before_value").to_pylist()
            after_values = table.column("after_value").to_pylist()
            for i, (before, after) in enumerate(zip(before_values, after_values, strict=True)):
                records.append(
                    RepairOperationRecord(
                        row_id=f"{column}:{i}",
                        column=column,
                        operation=proposal.operation,
                        before=before,
                        after=after,
                    )
                )
        return records

    @staticmethod
    def _require_copy_writer(source_type: DataSourceType) -> None:
        """Refuse a source type there is no copy writer for, in the same words, before any I/O.

        Both faces that need one (`apply` and `preview`) ask here, so neither can promise a step
        the other cannot do (F6).
        """
        if source_type not in _REPAIRABLE_SOURCE_TYPES:
            raise ValueError(
                f"repair does not support {source_type} sources yet: propose works, but "
                "preview and apply need a copy writer this format does not have"
            )

    @staticmethod
    def _suffix(context: DetectionContext, source_path: Path | None) -> str:
        """The artefact extension follows the source, so a repaired copy is still the same kind
        of file. Falling back to `.csv` renamed a `.tsv`'s byte-for-byte snapshot as a CSV and
        gave SQLite pages a `.csv` suffix (B-1, B-5)."""
        if context.handle.source_type == DataSourceType.CSV:
            return source_path.suffix if source_path is not None else ".csv"
        mapping = {
            DataSourceType.PARQUET: ".parquet",
            DataSourceType.JSONL: ".jsonl",
            DataSourceType.XLSX: ".xlsx",
        }
        return mapping[context.handle.source_type]

    @staticmethod
    def _read_artefact(
        path: Path, source_type: DataSourceType, *, drop_trailing_blank_rows: bool = True
    ) -> tuple[list[str], list[list[object]]]:
        """修复工件读取（只服务 `table_diff`，与 `_write_table` 格式对称），返回 (列名, 行)。

        CSV 逐格取**原文**（`default_column_type=string` 关掉类型推断）。默认推断会把
        ` 01234 ` 读成 int64 `1234`，before 与 after 两侧同时失真，于是一次改光全表的 TRIM
        在 diff 里变成"什么都没改"（`D2-06`）。工件级审计要比较的正是文件里的字节。

        同一理由适用于列名与键集合：读取不能只看第一条记录（`pa.Table.from_pylist` 只按第一条
        记录定型，第二条才出现的键会整列消失），也不能靠 `dict(zip(header,row))` 折叠重名表头
        （后半张表的数据没了），数字表头还会直接把审计崩掉（`F3`、`F4`）。列名**原样返回**：
        去重是显示层的事（`table_diff` 负责）；逐格搬运必须拿文件自己的拼写与投影对账，
        否则 `a,a,a_1` 被悄悄改写成 `a,a_1,a_1_1` 之后守卫就瞎了。
        """
        if source_type == DataSourceType.PARQUET:
            table = pq.read_table(path)
            return list(table.column_names), _arrow_rows(table)
        if source_type == DataSourceType.JSONL:
            # `\n` is the only JSONL record separator. `str.splitlines()` also breaks on U+2028,
            # U+2029 and U+0085 -- all legal inside a JSON string, and routine in scraped text --
            # which split one record in half and made the audit view crash (B-4).
            text = path.read_text(encoding="utf-8")
            records = [json.loads(line) for line in text.split("\n") if line.strip()]
            names: list[str] = []
            for record in records:
                names.extend(key for key in record if key not in names)
            rows = [[record.get(name) for name in names] for record in records]
            return names, rows
        if source_type == DataSourceType.XLSX:
            from openpyxl import load_workbook

            wb = load_workbook(path, read_only=True)
            # The connector scans `worksheets[0]`; `wb.active` is whichever tab was last saved as
            # current, so a two-sheet workbook compared the source's data against its notes (B-3).
            ws = wb.worksheets[0]
            header = [cell.value for cell in next(ws.iter_rows(), ())]
            rows = [[cell.value for cell in row] for row in ws.iter_rows(min_row=2)]
            # Only *trailing* blank rows are dropped: openpyxl's read-only mode reports the sheet's
            # stored extent, which usually ends in empties. Filtering interior ones too renumbered
            # the rows, so a repair that nulled a middle cell paired every later row with the wrong
            # neighbour and printed a rewrite that never happened (D2-10).
            # The cell carry-over for a copy asks for `drop_trailing_blank_rows=False`: the
            # connector's view keeps those rows, and a reader that trimmed them would either
            # refuse a repairable sheet or shift every later row up by one (G-1).
            if drop_trailing_blank_rows:
                while rows and not any(value is not None for value in rows[-1]):
                    rows.pop()
            # A header cell may hold a number, and a sheet may carry cells past its header row --
            # both are data the file really has, and neither is allowed to end the audit. A *blank*
            # header cell takes the connector's own spelling (`col_<index>`, 0-based, xlsx.py:70) so
            # the snapshot side is not labelled with an empty string; the diff's column names come
            # from the copy regardless, because XLSX columns align by position (F1, F4).
            width = max([len(header), *map(len, rows)], default=0)
            labelled = [
                f"col_{index}"
                if index >= len(header) or header[index] is None
                else str(header[index])
                for index in range(width)
            ]
            aligned = [[*row[:width], *([None] * (width - len(row)))] for row in rows]
            return labelled, aligned
        from datasentry_core.connectors.csv import sniff_profile

        encoding, delimiter = sniff_profile(path)
        table = pa_csv.read_csv(
            path,
            read_options=pa_csv.ReadOptions(encoding=encoding),
            parse_options=pa_csv.ParseOptions(delimiter=delimiter),
            convert_options=pa_csv.ConvertOptions(default_column_type=pa.string()),
        )
        return list(table.column_names), _arrow_rows(table)

    @staticmethod
    def table_diff(
        before_path: Path, after_path: Path, source_type: DataSourceType
    ) -> tuple[list[str], list[list[object]], list[list[object]], list[int]]:
        """V43：修复工件 diff——(列名, before 行, after 行, 变更行索引)。

        行数以较长一侧为准逐位比较：一侧多出的行就是变更，不能像 `zip(strict=False)` 那样
        被截断掉而静默消失。
        """
        before_names, before_rows = RepairEngine._read_artefact(before_path, source_type)
        after_names, after_rows = RepairEngine._read_artefact(after_path, source_type)
        # Taking the width from the after side alone let a column that existed only in the snapshot
        # vanish from the evidence while the diff answered "nothing changed"; both sides' columns
        # are kept, so a dropped or gained column shows as `value -> None` / `None -> value`.
        #
        # How they are matched depends on what the format says a column *is*. In CSV, Parquet and
        # XLSX the column order is the schema and a repair never reorders it, so position is the
        # truth -- naming is not: the snapshot spells a duplicate header `a,a,a_1` while the copy
        # carries DuckDB's rewrite of it, and aligning those by name reads every column one slot
        # over (F4), or splits one column into two when a blank header cell is named differently on
        # the two sides (F1). A JSONL record is a set of keys, order is not data, so name is the
        # truth there and position would report a re-serialised record as rewritten (F2).
        if source_type == DataSourceType.JSONL:
            columns = list(dict.fromkeys([*before_names, *after_names]))

            def align(names: list[str], rows: list[list[object]]) -> list[list[object]]:
                index = {name: position for position, name in enumerate(names)}
                slots = [index.get(column) for column in columns]
                return [
                    [row[slot] if slot is not None and slot < len(row) else None for slot in slots]
                    for row in rows
                ]

            aligned_before = align(before_names, before_rows)
            aligned_after = align(after_names, after_rows)
        else:
            width = max(len(before_names), len(after_names))
            columns = _dedupe_names(
                [
                    after_names[position] if position < len(after_names) else before_names[position]
                    for position in range(width)
                ]
            )

            def pad(names: list[str], rows: list[list[object]]) -> list[list[object]]:
                return [[*row[:width], *([None] * (width - min(len(row), width)))] for row in rows]

            aligned_before = pad(before_names, before_rows)
            aligned_after = pad(after_names, after_rows)
        changed = [
            i
            for i, (b, a) in enumerate(zip_longest(aligned_before, aligned_after))
            if not rows_equal(b, a)
        ]
        return columns, aligned_before, aligned_after, changed

    @staticmethod
    def _write_table(
        path: Path,
        table: pa.Table,
        context: DetectionContext,
        source_path: Path | None,
        cells: tuple[list[str], list[list[object]]] | None = None,
    ) -> None:
        """修复副本写入（格式与源一致）。

        `cells` 是 `_copy_cells` 交回的逐格内容：非目标列取自源文件自己的格子。给了它就用它写，
        因为把值先塞进 `pa.Table` 再落盘会重新引入本次要消灭的格式化（CSV 侧尤甚）。
        """
        source_type = context.handle.source_type
        if cells is not None and source_type == DataSourceType.CSV:
            columns, rows = cells
            carried = pa.table(
                {
                    name: pa.array([row[i] for row in rows], type=pa.string())
                    for i, name in enumerate(columns)
                }
            )
            from datasentry_core.connectors.csv import sniff_dialect

            delimiter = sniff_dialect(source_path) if source_path is not None else ","
            pa_csv.write_csv(carried, path, write_options=pa_csv.WriteOptions(delimiter=delimiter))
            return
        if cells is not None and source_type == DataSourceType.XLSX:
            from openpyxl import Workbook

            columns, rows = cells
            wb = Workbook()
            ws = wb.active
            ws.append(table.column_names)
            for row in rows:
                ws.append(row)
            wb.save(path)
            return
        if source_type == DataSourceType.PARQUET:
            pq.write_table(table, path)
        elif source_type == DataSourceType.JSONL:
            with path.open("w", encoding="utf-8") as fh:
                for row in table.to_pylist():
                    fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        elif source_type == DataSourceType.XLSX:
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.append(table.column_names)
            for row in table.to_pylist():
                ws.append([row.get(name) for name in table.column_names])
            wb.save(path)
        else:
            # The one fall-through branch. An earlier revision of this function lost the `else` and
            # then wrote CSV bytes over the Parquet/XLSX/JSONL copy it had just produced.
            from datasentry_core.connectors.csv import sniff_dialect

            delimiter = sniff_dialect(source_path) if source_path is not None else ","
            pa_csv.write_csv(table, path, write_options=pa_csv.WriteOptions(delimiter=delimiter))

    @staticmethod
    def _copy_cells(
        context: DetectionContext,
        table: pa.Table,
        proposal: RepairProposal,
        source_path: Path | None,
    ) -> tuple[list[str], list[list[object]]] | None:
        """逐格重建副本：非目标列取自源文件自己写下的格，目标列才取修复结果。

        投影是整表过 DuckDB 的，于是**没被授权的格子也被重新格式化**了（`G-1`，本机实测）：
        CSV/TSV 里 `1e5` 写成 `100000`、`2.50e3` 写成 `2500`、`1.50` 写成 `1.5`、`TRUE` 写成
        `true`；混合列的 XLSX 干脆把整数与小数写成文本（`1 -> '1'`）。副本是用户拿去替换生产
        数据的产物，这类改动既不在提案里、也不在 `RepairRun.operations` 里，违反不变量 2/3。
        JSONL 与 Parquet 实测没有附带改写（值与类型都原样），因此不在这里逐格搬运。

        返回 None 表示"按原路径写"。要求逐格但读不出对齐的行（比如参差 CSV）时直接拒绝：
        错位搬一次的比重写一个数字严重得多。
        """
        source_type = context.handle.source_type
        if source_type == DataSourceType.JSONL:
            # Measured, not assumed: a JSONL projection rewrites cells no proposal authorises --
            # `2024-01-05T08:00:00` comes back as `2024-01-05 08:00:00` and a record that never had
            # a key gains `"key": null` -- while `operations` records only the target column. The
            # claim that JSONL is exempt held only for records sharing one key set and no ISO-T
            # strings. Refusing beats writing an unauthorised copy; cell-by-cell carry for JSONL
            # is filed as the follow-up candidate.
            raise ValueError(
                "repair of JSONL sources is refused: the projection rewrites cells the proposal "
                "does not authorise (timestamp formatting, null-filled missing keys), and carrying "
                "JSONL cell-by-cell is not implemented yet"
            )
        if source_type not in _CELL_CARRY_SOURCE_TYPES or source_path is None:
            return None
        try:
            source_names, source_rows = RepairEngine._read_artefact(
                source_path, source_type, drop_trailing_blank_rows=False
            )
        except Exception as exc:
            raise ValueError(
                f"repair of {source_path.name} needs to carry its cells through cell-by-cell, "
                f"but the source cannot be read that way: {type(exc).__name__}"
            ) from exc
        # The projection names columns the connector's way (DuckDB rewrites `a,a,a_1` to
        # `a`/`a_1`/`a_1_1`, and a collapsed duplicate header loses cells), while the carry maps
        # positionally onto the file's own header. When the two spellings disagree, a positional
        # carry writes repaired values into the wrong column -- measured: the copy lost a header
        # cell and `operations` named a different cell than the one that changed. Refuse.
        projection_names = list(table.column_names)
        if len(projection_names) != len(source_names) or any(
            a != b for a, b in zip(projection_names, source_names, strict=True)
        ):
            raise ValueError(
                f"repair of {source_path.name} refuses to carry cells: the connector reads "
                f"{len(projection_names)} columns named {projection_names} while the file spells "
                f"{source_names}; a positional carry would write repaired values into the wrong "
                "column (duplicate or blank headers are the usual cause)"
            )
        if len(source_rows) != table.num_rows:
            raise ValueError(
                f"repair refuses to carry cells: the source has {len(source_rows)} rows "
                f"but the repaired projection has {table.num_rows}; a positional carry-over "
                "would misalign rows"
            )
        targets = {name for name in proposal.target_columns if name in context.columns}
        width = max(len(source_names), len(table.column_names))
        repaired: list[list[object]] = []
        for index, source_row in enumerate(source_rows):
            row: list[object] = []
            for position in range(width):
                column_name = (
                    table.column_names[position]
                    if position < len(table.column_names)
                    else (source_names[position] if position < len(source_names) else "")
                )
                if column_name in targets:
                    value = table[column_name][index].as_py()
                    if value is None:
                        row.append("" if source_type == DataSourceType.CSV else None)
                    elif source_type == DataSourceType.CSV:
                        # A repair result can be typed (a clipped Decimal, a clipped float);
                        # the artefact is delimited text, so the authorised cell is written as text.
                        row.append(str(value))
                    else:
                        row.append(value)
                else:
                    row.append(source_row[position] if position < len(source_row) else None)
            repaired.append(row)
        carried_names = [
            table.column_names[i] if i < len(table.column_names) else source_names[i]
            for i in range(width)
        ]
        return carried_names, repaired


# Issue type → 检测器 id（rule_failures 重跑目标）
_DETECTOR_FOR_ISSUE: dict[str, str] = {
    "leading_or_trailing_whitespace": "leading_or_trailing_whitespace",
    "inconsistent_case": "inconsistent_case",
    "suspicious_missing_token": "suspicious_missing_token",
    "invalid_date": "invalid_date",
    "impossible_date": "impossible_date",
    "iqr_outlier": "iqr_outlier",
    "percentile_outlier": "percentile_outlier",
    "modified_zscore": "modified_zscore",
}
