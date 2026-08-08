"""SARIF 2.1.0 报告导出（CI 集成：GitHub Code Scanning / IDE）。

语义：一次 scan 映射为一个 run；每个 issue → result（level 按
严重度映射），issue_type → rule 去重注册；无行号证据时 location
落在文件级（dataset_id 作 artifact uri），列名进 message 与
properties。
"""

from __future__ import annotations

from typing import Any

from datasentry_core.reporting import Report

_SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

_LEVEL_BY_SEVERITY = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}


def render_sarif(report: Report) -> dict[str, Any]:
    """26 章报告 → SARIF 2.1.0 JSON 字典（CLI 序列化）。"""
    issues = report["issues"]
    scan = report["scan"]
    rules: dict[str, dict[str, Any]] = {}
    for issue in issues:
        rule = rules.setdefault(
            issue["issue_type"],
            {
                "id": issue["issue_type"],
                "name": issue["issue_type"],
                "shortDescription": {"text": issue["title"]},
                "defaultConfiguration": {
                    "level": _LEVEL_BY_SEVERITY.get(issue["severity"], "note")
                },
            },
        )
        if not rule["shortDescription"]["text"]:
            rule["shortDescription"]["text"] = issue["title"]

    results: list[dict[str, Any]] = []
    for issue in issues:
        columns = ",".join(issue["columns"]) or "dataset"
        message = (
            f"[{issue['severity']}] {issue['title']} "
            f"(columns: {columns}, affected: {issue['affected_count']} rows)"
        )
        results.append(
            {
                "ruleId": issue["issue_type"],
                "level": _LEVEL_BY_SEVERITY.get(issue["severity"], "note"),
                "message": {"text": message},
                "locations": [
                    {"physicalLocation": {"artifactLocation": {"uri": scan["dataset_id"]}}}
                ],
                "properties": {
                    "columns": issue["columns"],
                    "affected_count": issue["affected_count"],
                    "affected_ratio": issue["affected_ratio"],
                    "confidence": issue["confidence"],
                    "detector_ids": issue["detector_ids"],
                    "scan_run_id": report["scan_run_id"],
                },
            }
        )

    driver: dict[str, Any] = {
        "name": "DataSentry",
        "version": report["datasentry_version"],
        "rules": [rules[k] for k in sorted(rules)],
    }
    return {
        "$schema": _SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": driver},
                "automationDetails": {"id": report["scan_run_id"]},
                "results": results,
                "properties": {
                    "dataset_id": scan["dataset_id"],
                    "row_count": scan["fingerprint"]["row_count"],
                    "column_count": scan["fingerprint"]["column_count"],
                },
            }
        ],
    }
