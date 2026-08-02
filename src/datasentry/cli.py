"""DataSentry CLI（22 章 MVP 子集）。

命令：init / scan / issues list / issues show / report export / contract validate
全局选项：--project / --format text|json / --seed / --version
JSON 统一 envelope（22.1）：{"ok", "command", "data", "warnings", "llm_usage"}
退出码：0 成功；2 配置错误；3 执行错误；4 数据源不可用
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from datasentry import __version__
from datasentry.client import DataSentry
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import ScanConfig

EXIT_OK = 0
EXIT_GATE_FAILED = 1  # validate --fail-on 质量门禁（V1 契约引擎）
EXIT_CONFIG = 2
EXIT_ERROR = 3
EXIT_SOURCE_UNAVAILABLE = 4

_GLOBAL_EPILOG = """Global options:
  --project PATH  project workspace (default: current dir)
  --format FMT    text|json (default: text)
  --seed N        reproducibility seed
  --version       show version
"""


def _envelope(command: str, data: dict, warnings: list[str] | None = None) -> dict:
    return {
        "ok": True,
        "command": command,
        "data": data,
        "warnings": warnings or [],
        "llm_usage": {"calls": 0, "tokens": 0},
    }


def _emit(envelope: dict, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(envelope["data"], ensure_ascii=False, indent=2))


def _issue_lines(issue: Issue) -> list[str]:
    return [
        f"  {issue.id} [{issue.severity.value}] {issue.title} "
        f"(priority={issue.priority_score:.1f}, conf={issue.confidence:.2f}, "
        f"affected={issue.affected_count})",
        f"    columns={issue.columns} detectors={issue.detector_ids}",
    ]


def _cmd_init(args: argparse.Namespace) -> int:
    """22.1 init：创建 .datasentry/ 目录与 .gitignore 条目（ADR-010 布局）。"""
    client = DataSentry(args.project)
    data = {"project": str(client.workspace), "db": str(client.db_path)}
    _emit(_envelope("init", data), args.format)
    return EXIT_OK


def _cmd_scan(args: argparse.Namespace) -> int:
    """22.1 scan：导入 → 扫描 → 评分 → 落库；数据源缺失退出码 4。"""
    client = DataSentry(args.project)
    config = ScanConfig(detectors=args.detector or None, seed=args.seed)
    try:
        scan_run, runs, issues = client.scan_file(args.path, config=config)
    except FileNotFoundError as exc:
        _emit(_envelope("scan", {"error": str(exc)}), args.format)
        return EXIT_SOURCE_UNAVAILABLE
    summary = {
        "scan_run_id": scan_run.id,
        "dataset_id": scan_run.dataset_id,
        "status": scan_run.status,
        "row_count": scan_run.fingerprint.row_count,
        "issues_count": {k.value: v for k, v in scan_run.issues_count.items()},
        "total_issues": len(issues),
        "detector_runs": len(runs),
    }
    _emit(_envelope("scan", summary), args.format)
    return EXIT_OK


def _cmd_issues_list(args: argparse.Namespace) -> int:
    """22.1 issues list：Issue 列表（--severity 过滤）。"""
    client = DataSentry(args.project)
    issues = client.list_issues(severity_at_least=args.severity, scan_run_id=args.scan_run)
    data = {"issues": [i.model_dump(mode="json") for i in issues], "count": len(issues)}
    if args.format == "json":
        _emit(_envelope("issues list", data), args.format)
    else:
        for issue in issues:
            print("\n".join(_issue_lines(issue)))
        print(f"\n{len(issues)} issues")
    return EXIT_OK


def _cmd_issues_show(args: argparse.Namespace) -> int:
    """22.1 issues show：完整 Issue 详情（含证据）。"""
    client = DataSentry(args.project)
    issue = None
    for scan in client._store.list_scan_runs():
        for i in client._store.get_issues(scan.id):
            if i.id == args.issue_id:
                issue = i
                break
        if issue:
            break
    if issue is None:
        _emit(_envelope("issues show", {"error": f"issue not found: {args.issue_id}"}), args.format)
        return EXIT_CONFIG
    data = issue.model_dump(mode="json")
    _emit(_envelope("issues show", data), args.format)
    return EXIT_OK


def _cmd_report_export(args: argparse.Namespace) -> int:
    """22.1 report export：JSON 报告（HTML 归 V1）。"""
    client = DataSentry(args.project)
    try:
        report = client.export_report(args.run_id)
    except KeyError as exc:
        _emit(_envelope("report export", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    _emit(_envelope("report export", report), args.format)
    return EXIT_OK


def _cmd_contract_validate(args: argparse.Namespace) -> int:
    """ADR-004：MVP 提供 contract validate（YAML 格式校验）。"""
    import yaml

    from datasentry_core.models.contract import Contract

    path = Path(args.path)
    if not path.is_file():
        _emit(_envelope("contract validate", {"error": f"file not found: {path}"}), args.format)
        return EXIT_SOURCE_UNAVAILABLE
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        contract = Contract.model_validate(raw)
    except Exception as exc:  # YAML 语法或 schema 不符均视为校验失败
        _emit(
            _envelope("contract validate", {"valid": False, "error": str(exc)}),
            args.format,
        )
        return EXIT_CONFIG
    data = {"valid": True, "version": contract.version, "dataset": contract.dataset.name}
    _emit(_envelope("contract validate", data), args.format)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datasentry",
        description="DataSentry AI — evidence-driven data quality copilot",
        epilog=_GLOBAL_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"datasentry {__version__}")
    parser.add_argument(
        "--project", type=str, default=None, help="project workspace path (default: current dir)"
    )
    parser.add_argument(
        "--format", type=str, default="text", choices=["text", "json"], help="output format"
    )
    parser.add_argument("--seed", type=int, default=42, help="reproducibility seed")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="initialize workspace (.datasentry/ + .gitignore)")
    p_init.set_defaults(func=_cmd_init)

    p_scan = sub.add_parser("scan", help="scan a data file (CSV)")
    p_scan.add_argument("path", type=str, help="data file path")
    p_scan.add_argument(
        "--detector", action="append", default=None, help="detector whitelist (repeatable)"
    )
    p_scan.set_defaults(func=_cmd_scan)

    p_issues = sub.add_parser("issues", help="issue queries")
    issues_sub = p_issues.add_subparsers(dest="issues_cmd", required=True)
    p_list = issues_sub.add_parser("list", help="list issues")
    p_list.add_argument("--severity", type=str, default=None, help="minimum severity filter")
    p_list.add_argument("--scan-run", type=str, default=None, help="restrict to scan run")
    p_list.set_defaults(func=_cmd_issues_list)
    p_show = issues_sub.add_parser("show", help="issue detail")
    p_show.add_argument("issue_id", type=str)
    p_show.set_defaults(func=_cmd_issues_show)

    p_report = sub.add_parser("report", help="report export")
    report_sub = p_report.add_subparsers(dest="report_cmd", required=True)
    p_export = report_sub.add_parser("export", help="export scan report (JSON)")
    p_export.add_argument("run_id", type=str)
    p_export.set_defaults(func=_cmd_report_export)

    p_contract = sub.add_parser("contract", help="data contracts (V1 engine; MVP validates only)")
    contract_sub = p_contract.add_subparsers(dest="contract_cmd", required=True)
    p_validate = contract_sub.add_parser("validate", help="validate contract YAML format")
    p_validate.add_argument("path", type=str)
    p_validate.set_defaults(func=_cmd_contract_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --version(0) / 用法错误(2) 由 argparse 直接退出
        return int(exc.code or EXIT_OK)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return EXIT_ERROR
    except Exception as exc:  # 执行期错误统一退出码 3
        _emit(_envelope(args.command, {"error": str(exc)}), args.format)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
