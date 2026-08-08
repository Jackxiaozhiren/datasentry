"""JUnit XML 报告导出（CI 集成：每个 issue = 一个 failure testcase）。

语义：一次 scan 的 issues 映射为单个 <testsuite>；issue 存在即
质量问题 → testcase failure（type=severity），errors=0 保留给
平台级错误。与门禁判定解耦（门禁请用 validate 命令）。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from datasentry_core.reporting import Report

_SEVERITY_TYPE = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "info",
}


def _suite_name(report: Report) -> str:
    dataset_id = report["scan"]["dataset_id"]
    return f"datasentry:{dataset_id}"


def render_junit(report: Report) -> str:
    """26 章报告 → JUnit XML 字符串（28 章 CI 场景）。"""
    issues = report["issues"]
    scan = report["scan"]
    suite = ET.Element(
        "testsuite",
        {
            "name": _suite_name(report),
            "tests": str(len(issues)),
            "failures": str(len(issues)),
            "errors": "0",
            "timestamp": report["generated_at"],
        },
    )
    quality = report["quality"]
    overall = f"{quality['overall']:.2f}" if quality else "n/a"
    overview = (
        f"dataset={scan['dataset_id']} rows={scan['fingerprint']['row_count']} "
        f"columns={scan['fingerprint']['column_count']} overall={overall} "
        f"scan_run_id={report['scan_run_id']}"
    )
    ET.SubElement(suite, "properties").append(
        ET.Element("property", {"name": "overview", "value": overview})
    )
    for issue in issues:
        columns = ",".join(issue["columns"]) or "dataset"
        testcase = ET.SubElement(
            suite,
            "testcase",
            {
                "name": issue["issue_type"],
                "classname": columns,
                "file": scan["dataset_id"],
            },
        )
        failure = ET.SubElement(
            testcase,
            "failure",
            {
                "type": _SEVERITY_TYPE.get(issue["severity"], "unknown"),
                "message": issue["title"],
            },
        )
        detail = [
            f"severity: {issue['severity']}",
            f"confidence: {issue['confidence']:.2f}",
            f"affected: {issue['affected_count']} rows ({issue['affected_ratio']:.2%})",
            f"detectors: {','.join(issue['detector_ids'])}",
        ]
        if issue.get("description"):
            detail.append(f"description: {issue['description']}")
        failure.text = "\n".join(detail)
    return ET.tostring(suite, encoding="unicode", xml_declaration=True)
