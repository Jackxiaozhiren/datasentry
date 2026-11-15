"""R-07: 把既有的 repair 证据收拢成 `render_html(repair_records=...)` 的纯数据入参。

这一层只读取, 不写任何东西, 也不重算任何领域值:
`rows_changed` 取库里的单一持久字段, diff 走 `client.repair_diff()`, 工件名走
`client` 既有的副本路径口径。core 的渲染层负责最后一道人类可读输出前的 PII 掩码。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from datasentry.client import DataSentry

_DIFF_ROW_CAP = 10
_RECORD_CAP = 20


def _cell_map(columns: list[str], row: list[object]) -> dict[str, object]:
    """一行 diff → JSON 对象; 行里没有的位置直接省略, 而不是写成 null。

    "键不存在"和"键的值是 null"是两件事: 给快照里没有的列补一个 `null`, 等于替文件
    编造一个它从未携带的键。`RepairEngine.table_diff` 今天会把两侧的行补齐到列宽,
    所以这里的 `position < len(row)` 只防更短的行; 一旦引擎改用 `ABSENT` 哨兵,
    这条省略规则应与它保持同一个口径。
    """
    return {name: row[position] for position, name in enumerate(columns) if position < len(row)}


def _issue_for(
    report: dict[str, Any], source_scan_run_id: str | None, columns: set[str]
) -> tuple[str | None, str | None]:
    """把一次修复对回它处理的 issue: 同一 scan + 同一目标列, 不新造关联字段。"""
    if not columns:
        return None, None
    issues = report.get("issues")
    if not isinstance(issues, list):
        return None, None
    for item in issues:
        if not isinstance(item, dict):
            continue
        if source_scan_run_id is not None and item.get("scan_run_id") != source_scan_run_id:
            continue
        issue_columns = {str(c) for c in (item.get("columns") or [])}
        if issue_columns & columns:
            return str(item.get("id") or ""), str(item.get("issue_type") or "")
    return None, None


def build_verify_records(client: DataSentry, report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """R-08: 按 repair run 收集 verify 结论, 没跑过 verify 的 run 不在结果里。

    只读, **绝不重扫**: `repair_verify()` 会 `scan_file()` 产生新 ScanRun, 报告
    导出不能带这种副作用。既有数据里能证明"verify 已跑"的唯一痕迹, 是一次以
    修复副本为 `source_path` 的扫描 —— 这也正是 `repair_verify()` 自己留下的。
    所以判据是"存在以该副本为输入的重扫", 而不是"命令被调用过"(库里没记这个)。

    分数两侧都取自 `export_report()` 的 scan 快照: before 是本次报告自己的
    `quality_score`, after 是重扫那次的。版本标注取 `reproducibility`, 不新造
    代码指纹 —— 版本字符串区分不了未提交的本地改动, 这一点由文案明写。
    """
    scan_run_id = str(report.get("scan_run_id") or "")
    repairs = [r for r in client.list_repair_runs() if r.source_scan_run_id == scan_run_id]
    scan_runs = client.list_scan_runs()
    source = report.get("scan")
    if not isinstance(source, dict):
        return {}
    source_score = source.get("quality_score")
    if not isinstance(source_score, dict) or source_score.get("overall") is None:
        return {}
    source_types = _issue_types(report.get("issues"))

    out: dict[str, dict[str, Any]] = {}
    for run in repairs[:_RECORD_CAP]:
        try:
            copy_path = str(client._repaired_copy_path(run))
        except (ValueError, OSError):
            continue
        candidates = [s for s in scan_runs if s.source_path == copy_path]
        if not candidates:
            continue
        vscan = max(candidates, key=lambda s: (s.started_at, s.id))
        if vscan.quality_score is None:
            continue
        try:
            vreport = cast("dict[str, Any]", client.export_report(vscan.id))
        except (KeyError, ValueError):
            continue
        verify_scan = vreport.get("scan")
        after_score = verify_scan.get("quality_score") if isinstance(verify_scan, dict) else None
        if not isinstance(after_score, dict) or after_score.get("overall") is None:
            continue
        source_repro = source.get("reproducibility")
        verify_repro = verify_scan.get("reproducibility") if isinstance(verify_scan, dict) else None
        out[run.id] = {
            "verify_scan_run_id": vscan.id,
            "before_score": float(source_score["overall"]),
            "after_score": float(after_score["overall"]),
            "new_issue_types": sorted(_issue_types(vreport.get("issues")) - source_types),
            "source_code": _code_label(source_repro),
            "verify_code": _code_label(verify_repro),
            "same_code": _same_code(source_repro, verify_repro),
        }
    return out


def _issue_types(issues: Any) -> set[str]:
    if not isinstance(issues, list):
        return set()
    return {str(i.get("issue_type") or "") for i in issues if isinstance(i, dict)} - {""}


def _code_label(repro: Any) -> dict[str, Any]:
    """只做展示摘要, 不伪造代码指纹: 版本 + 检测器数量。"""
    if not isinstance(repro, dict):
        return {"version": "unknown", "detector_count": 0}
    detectors = repro.get("detector_versions") or {}
    return {
        "version": str(repro.get("datasentry_version") or "unknown"),
        "detector_count": len(detectors) if isinstance(detectors, dict) else 0,
    }


def _same_code(a: Any, b: Any) -> bool:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    return a.get("datasentry_version") == b.get("datasentry_version") and a.get(
        "detector_versions"
    ) == b.get("detector_versions")


def build_repair_records(client: DataSentry, report: dict[str, Any]) -> list[dict[str, Any]]:
    """本报告对应那次扫描做过的修复; 一次都没有时返回空列表(章节整节不出现)。

    `repair_diff` 读不到工件时, 该条记录的 `diff` 置 None 而不是丢掉整条:
    报告仍要给出 run id / 工件名 / 回滚命令, 并按 ui.py 的口径明写读不到。
    """
    scan_run_id = str(report.get("scan_run_id") or "")
    runs = [r for r in client.list_repair_runs() if r.source_scan_run_id == scan_run_id]
    records: list[dict[str, Any]] = []
    for run in runs[:_RECORD_CAP]:
        operations: dict[tuple[str, str], int] = {}
        for op in run.operations:
            key = (str(getattr(op.operation, "value", op.operation)), op.column)
            operations[key] = operations.get(key, 0) + 1
        issue_id, issue_type = _issue_for(
            report, run.source_scan_run_id, {column for _, column in operations}
        )

        artifact = Path(run.rollback_artifact).name if run.rollback_artifact else None
        try:
            copy_name: str | None = client._repaired_copy_path(run).name
        except (ValueError, OSError):
            copy_name = artifact.replace(".before", "") if artifact else None

        diff: dict[str, Any] | None
        try:
            _, columns, before_rows, after_rows, changed = client.repair_diff(run.id)
        except (KeyError, ValueError, OSError):
            diff = None
        else:
            shown = changed[:_DIFF_ROW_CAP]
            rows = []
            for index in shown:
                before = before_rows[index] if index < len(before_rows) else []
                after = after_rows[index] if index < len(after_rows) else []
                rows.append(
                    {
                        "line": index + 2,
                        "before": _cell_map(columns, before),
                        "after": _cell_map(columns, after),
                    }
                )
            diff = {
                "columns": [str(c) for c in columns],
                "rows": rows,
                "hidden": max(0, len(changed) - len(shown)),
            }

        records.append(
            {
                "run_id": run.id,
                "status": str(getattr(run.status, "value", run.status)),
                "issue_id": issue_id,
                "issue_type": issue_type,
                "operations": [
                    {"operation": op, "column": column, "count": count}
                    for (op, column), count in operations.items()
                ],
                "rows_changed": run.rows_changed,
                "artifact_before": artifact,
                "artifact_copy": copy_name,
                "diff": diff,
                "rollback_command": f"datasentry repair rollback {run.id}",
            }
        )
    verifies = build_verify_records(client, report)
    for record in records:
        conclusion = verifies.get(str(record["run_id"]))
        if conclusion is not None:
            record["verify"] = conclusion
    return records
