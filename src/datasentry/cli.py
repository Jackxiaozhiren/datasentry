"""DataSentry CLI（22 章 MVP 子集）。

命令：init / scan（--fail-on 门禁）/ issues list|show / report export（--as）/
score / contract validate
全局选项：--project / --format text|json / --seed / --version
JSON 统一 envelope（22.1）：{"ok", "command", "data", "warnings", "llm_usage"}
退出码：0 成功；1 质量门禁失败；2 配置错误；3 执行错误；4 数据源不可用
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from datasentry import __version__
from datasentry.client import DataSentry
from datasentry_core.models.enums import Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import ScanConfig
from datasentry_core.scoring.gate import GateResult, QualityGateEvaluator

EXIT_OK = 0
EXIT_GATE_FAILED = 1  # scan --fail-on 质量门禁（22 章场景 C）
EXIT_CONFIG = 2
EXIT_ERROR = 3
EXIT_SOURCE_UNAVAILABLE = 4

_GLOBAL_EPILOG = """Global options:
  --project PATH  project workspace (default: current dir)
  --format FMT    text|json (default: text)
  --seed N        reproducibility seed
  --version       show version
"""


def _envelope(
    command: str, data: dict[Any, Any], warnings: list[str] | None = None
) -> dict[str, object]:
    return {
        "ok": True,
        "command": command,
        "data": data,
        "warnings": warnings or [],
        "llm_usage": {"calls": 0, "tokens": 0},
    }


def _emit(envelope: dict[str, object], fmt: str) -> None:
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
    """22.1 scan：导入 → 扫描 → 评分 → 落库；数据源缺失退出码 4；门禁失败退出码 1。"""
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
        "quality_score": scan_run.quality_score.overall if scan_run.quality_score else None,
    }
    if args.fail_on is not None:
        gate_result = _evaluate_gate(issues, args)
        gate_data = gate_result.model_dump()
        gate_data["failed_count"] = gate_result.failed_count
        summary["gate"] = gate_data
        _emit(_envelope("scan", summary), args.format)
        return EXIT_GATE_FAILED if not gate_result.passed else EXIT_OK
    _emit(_envelope("scan", summary), args.format)
    return EXIT_OK


def _evaluate_gate(issues: list[Issue], args: argparse.Namespace) -> GateResult:
    """22 章场景 C：scan --fail-on SEV [--max-failure-ratio R] → 质量门禁。"""
    from datasentry_core.models.contract import QualityGate

    gate = QualityGate(
        fail_on=[Severity(args.fail_on)],
        maximum_failed_rows_ratio=args.max_failure_ratio,
    )
    return QualityGateEvaluator().evaluate(issues, gate)


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
    """22.1 report export：JSON/Markdown/HTML 报告（26 章），输出文件路径。"""
    client = DataSentry(args.project)
    try:
        report = client.export_report(args.run_id)
    except KeyError as exc:
        _emit(_envelope("report export", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    if args.as_format == "json":
        content = json.dumps(report, ensure_ascii=False, indent=2)
    elif args.as_format == "markdown":
        from datasentry_core.reporting.markdown import render_markdown

        content = render_markdown(report)
    else:
        from datasentry_core.reporting.html import render_html

        content = render_html(report)
    path = _report_output_path(client, args, args.as_format)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _emit(_envelope("report export", {"path": str(path), "format": args.as_format}), args.format)
    return EXIT_OK


def _report_output_path(client: DataSentry, args: argparse.Namespace, fmt: str) -> Path:
    """默认落点 <project>/.datasentry/reports/<run_id>.<ext>（ADR-010），--output 可覆盖。"""
    if args.output:
        return Path(args.output).expanduser()
    return client.reports_dir / f"{args.run_id}.{fmt}"


def _cmd_score(args: argparse.Namespace) -> int:
    """27 章：质量总分展示（维度构成 + 权重 + 计算说明，27.3 可解释性）。"""
    client = DataSentry(args.project)
    try:
        quality = client.quality_score(args.run_id)
    except KeyError as exc:
        _emit(_envelope("score", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    if quality is None:
        _emit(_envelope("score", {"scored": False}), args.format)
        return EXIT_OK
    data = {"scored": True, "score": quality.model_dump(mode="json")}
    if args.format == "text":
        print(f"Overall quality score: {quality.overall}  (score_version={quality.score_version})")
        for dim, value in quality.dimensions.items():
            label = f"{dim:15s} {value!s:>6}"
            weight = quality.weights.get(dim)
            print(f"  {label}  weight={weight if weight is not None else '-'}")
        print(f"  notes: {quality.calculation_notes}")
        return EXIT_OK
    _emit(_envelope("score", data), args.format)
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


def _cmd_repair_propose(args: argparse.Namespace) -> int:
    """15 章：issue → 修复提案。"""
    client = DataSentry(args.project)
    try:
        proposal = client.repair_propose(args.issue_id, args.file)
    except (KeyError, ValueError) as exc:
        _emit(_envelope("repair propose", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    if proposal is None:
        _emit(_envelope("repair propose", {"proposed": False}), args.format)
        return EXIT_OK
    _emit(
        _envelope(
            "repair propose",
            {
                "proposed": True,
                "proposal_id": proposal.proposal_id,
                "issue_id": proposal.issue_id,
                "issue_type": proposal.issue_type,
                "operation": proposal.operation.value,
                "target_columns": proposal.target_columns,
                "estimated_rows_changed": proposal.estimated_rows_changed,
                "rationale": proposal.rationale,
            },
        ),
        args.format,
    )
    return EXIT_OK


def _cmd_repair_preview(args: argparse.Namespace) -> int:
    """15.6：提案统计面板 + 规则重跑前后对比。"""
    client = DataSentry(args.project)
    try:
        result = client.repair_preview(args.issue_id, args.file)
    except (KeyError, ValueError) as exc:
        _emit(_envelope("repair preview", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    if result is None:
        _emit(_envelope("repair preview", {"previewed": False}), args.format)
        return EXIT_OK
    proposal, preview = result
    data = {
        "previewed": True,
        "proposal_id": proposal.proposal_id,
        "issue_type": proposal.issue_type,
        "operation": proposal.operation.value,
        "rows_changed": preview.rows_changed,
        "rows_changed_ratio": preview.rows_changed_ratio,
        "rule_failures_before": preview.rule_failures_before,
        "rule_failures_after": preview.rule_failures_after,
        "null_delta": preview.null_delta,
        "unique_delta": preview.unique_delta,
        "changed_examples": [
            {
                "column": ex.column,
                "before": ex.before,
                "after": ex.after,
            }
            for ex in preview.changed_examples
        ],
    }
    _emit(_envelope("repair preview", data), args.format)
    return EXIT_OK


def _cmd_repair_apply(args: argparse.Namespace) -> int:
    """15.7：应用修复（副本写入 + before artifact + 落库）。"""
    client = DataSentry(args.project)
    try:
        run = client.repair_apply(args.issue_id, args.file)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        _emit(_envelope("repair apply", {"error": str(exc)}), args.format)
        return EXIT_ERROR
    _emit(
        _envelope(
            "repair apply",
            {
                "applied": True,
                "run_id": run.id,
                "proposal_id": run.proposal_id,
                "fingerprint_before": run.fingerprint_before,
                "fingerprint_after": run.fingerprint_after,
                "changed": run.fingerprint_before != run.fingerprint_after,
                "rollback_artifact": run.rollback_artifact,
            },
        ),
        args.format,
    )
    return EXIT_OK


def _cmd_repair_rollback(args: argparse.Namespace) -> int:
    """15.7：回滚（artifact 全量重建 + 状态更新）。"""
    client = DataSentry(args.project)
    try:
        run = client.repair_rollback(args.run_id)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        _emit(_envelope("repair rollback", {"error": str(exc)}), args.format)
        return EXIT_ERROR
    _emit(
        _envelope(
            "repair rollback",
            {"rolled_back": True, "run_id": run.id, "status": run.status.value},
        ),
        args.format,
    )
    return EXIT_OK


def _cmd_repair_list(args: argparse.Namespace) -> int:
    """列出修复执行记录。"""
    client = DataSentry(args.project)
    runs = client.list_repair_runs()
    data = {
        "runs": [
            {
                "id": r.id,
                "proposal_id": r.proposal_id,
                "dataset_id": r.dataset_id,
                "status": r.status.value,
                "fingerprint_before": r.fingerprint_before,
                "fingerprint_after": r.fingerprint_after,
                "created_at": r.created_at.isoformat(),
            }
            for r in runs
        ]
    }
    _emit(_envelope("repair list", data), args.format)
    return EXIT_OK


def _cmd_llm_status(args: argparse.Namespace) -> int:
    """LLM 提供方状态与配置来源（13.11 审计查询入口）。"""
    from datasentry.llm_providers import load_llm_config

    config = load_llm_config()
    client = DataSentry(args.project)
    try:
        invocations = client.list_llm_invocations(limit=20)
    finally:
        client.close()
    summary = {
        "provider": config.provider,
        "model": config.model or "n/a",
        "base_url": config.base_url or "n/a",
        "configured": config.provider != "null",
        "recent_calls": len(invocations),
        "last_status": invocations[0].status if invocations else None,
    }
    _emit(_envelope("llm status", summary), args.format)
    return EXIT_OK


def _cmd_llm_invocations(args: argparse.Namespace) -> int:
    """列出最近 LLM 调用审计（字段名 + 统计量，不含 prompt 原文）。"""
    client = DataSentry(args.project)
    try:
        invocations = client.list_llm_invocations(limit=args.limit)
    finally:
        client.close()
    data = {
        "invocations": [
            {
                "invocation_id": i.invocation_id,
                "task_type": i.task_type,
                "provider_id": i.provider_id,
                "model": i.model,
                "input_tokens": i.input_tokens,
                "output_tokens": i.output_tokens,
                "cache_hit": i.cache_hit,
                "latency_ms": i.latency_ms,
                "status": i.status,
                "masked_sample_count": i.masked_sample_count,
                "injection_flagged": i.injection_flagged,
                "created_at": i.created_at.isoformat(),
            }
            for i in invocations
        ]
    }
    _emit(_envelope("llm invocations", data), args.format)
    return EXIT_OK


def _cmd_detectors_list(args: argparse.Namespace) -> int:
    """列出注册表检测器（内置 + workspace/plugins 插件，ADR-031）。"""
    client = DataSentry(args.project)
    try:
        detectors = client.list_detectors()
    finally:
        client.close()
    _emit(_envelope("detectors list", {"detectors": detectors}), args.format)
    return EXIT_OK


def _cmd_rules_propose(args: argparse.Namespace) -> int:
    """自然语言 → 规则候选（14.4）：脱敏 → LLM → 严格校验 → 预运行，不落库。"""
    from datasentry.rules_ai import RuleProposalService

    client = DataSentry(args.project)
    try:
        service = RuleProposalService(store=client._store, project=args.project)
        result = service.propose(args.description, args.file, budget_tokens=args.budget)
    finally:
        client.close()
    if result.llm_error:
        _emit(_envelope("rules propose", {"error": result.llm_error}), args.format)
        return EXIT_ERROR
    if result.cache_hit:
        print("  [cache] prompt matched llm_cache; no LLM call made")
    data: dict[str, Any] = {"masked_sample_count": result.masked_sample_count, "rules": []}
    for item in result.rules:
        if item.candidate is None:
            data["rules"].append({"rejected": item.rejected_reason})
            continue
        rule = item.candidate.rule
        entry = {
            "rule_id": rule.id,
            "type": rule.type.value,
            "severity": rule.severity.value,
            "description": rule.description,
            "when": rule.when.model_dump() if rule.when else None,
            "columns": rule.columns,
            "confidence": item.candidate.confidence,
            "paraphrase": item.candidate.paraphrase,
        }
        if item.preflight is not None:
            entry["preflight"] = {
                "valid": item.preflight.valid,
                "schema_valid": item.preflight.schema_valid,
                "dangerous": item.preflight.dangerous,
                "rows_tested": item.preflight.sample_run.rows_tested
                if item.preflight.sample_run
                else None,
                "failures": item.preflight.sample_run.failures
                if item.preflight.sample_run
                else None,
                "failure_ratio": item.preflight.sample_run.failure_ratio
                if item.preflight.sample_run
                else None,
            }
        data["rules"].append(entry)
    if args.format == "text":
        print(f"proposed {len(data['rules'])} rule candidate(s) (preflight run, NOT saved)")
        for entry in data["rules"]:
            if "rejected" in entry:
                print(f"  ✗ rejected: {entry['rejected']}")
                continue
            pf = entry.get("preflight") or {}
            print(
                f"  {entry['rule_id']} [{entry['severity']}] {entry['type']}: "
                f"{entry['description']} (conf={entry['confidence']:.2f}, "
                f"rows={pf.get('rows_tested')}, failures={pf.get('failures')}, "
                f"dangerous={pf.get('dangerous')})"
            )
            print(
                f"    approve with: datasentry rules approve {entry['rule_id']} --file {args.file}"
            )
    else:
        _emit(_envelope("rules propose", data), args.format)
    return EXIT_OK


def _cmd_rules_approve(args: argparse.Namespace) -> int:
    """批准候选规则落库（14.4 用户批准；source=llm_candidate）。

    提供 --file 时对目标数据重跑预运行复核；dangerous（违规行占比
    > 0.5）的规则必须带 --force 才批准（14.4 安全阀门）。
    """
    from datasentry.rules_ai import RuleApprovalBlockedError, RuleProposalService

    client = DataSentry(args.project)
    try:
        service = RuleProposalService(store=client._store, project=args.project)
        rule = service.approve(args.rule_id, data_path=args.file, force=args.force)
    except RuleApprovalBlockedError as exc:
        _emit(
            _envelope(
                "rules approve",
                {"error": str(exc), "rule_id": exc.rule_id, "reason": exc.reason},
            ),
            args.format,
        )
        return EXIT_ERROR
    finally:
        client.close()
    if rule is None:
        _emit(_envelope("rules approve", {"error": f"rule not found: {args.rule_id}"}), args.format)
        return EXIT_SOURCE_UNAVAILABLE
    data: dict[str, Any] = {"rule_id": rule.id, "source": rule.source, "enabled": rule.enabled}
    _emit(_envelope("rules approve", data), args.format)
    return EXIT_OK


def _cmd_rules_list(args: argparse.Namespace) -> int:
    """列出已批准规则（14.1 落库视图）。"""
    client = DataSentry(args.project)
    try:
        rules = client.list_rules()
    finally:
        client.close()
    data = {
        "rules": [
            {
                "id": r.id,
                "type": r.type.value,
                "severity": r.severity.value,
                "description": r.description,
                "source": r.source,
                "enabled": r.enabled,
                "version": r.version,
            }
            for r in rules
        ]
    }
    _emit(_envelope("rules list", data), args.format)
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
    p_scan.add_argument(
        "--fail-on",
        type=str,
        default=None,
        choices=["info", "low", "medium", "high", "critical"],
        help="quality gate: fail (exit 1) on issues at this severity (22 章场景 C)",
    )
    p_scan.add_argument(
        "--max-failure-ratio",
        type=float,
        default=0.01,
        help="quality gate: max affected row ratio (default: 0.01)",
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
    p_export = report_sub.add_parser("export", help="export scan report (26 章)")
    p_export.add_argument("run_id", type=str)
    p_export.add_argument(
        "--as",
        dest="as_format",
        type=str,
        default="json",
        choices=["json", "markdown", "html"],
        help="report format (json|markdown|html, default: json)",
    )
    p_export.add_argument(
        "--output", type=str, default=None, help="output path (default: .datasentry/reports/)"
    )
    p_export.set_defaults(func=_cmd_report_export)

    p_score = sub.add_parser("score", help="quality score (27 章)")
    p_score.add_argument("run_id", type=str)
    p_score.set_defaults(func=_cmd_score)

    p_contract = sub.add_parser("contract", help="data contracts (V1 engine; MVP validates only)")
    contract_sub = p_contract.add_subparsers(dest="contract_cmd", required=True)
    p_validate = contract_sub.add_parser("validate", help="validate contract YAML format")
    p_validate.add_argument("path", type=str)
    p_validate.set_defaults(func=_cmd_contract_validate)

    p_repair = sub.add_parser(
        "repair", help="repair engine (15 章, ADR-020; propose→preview→apply→rollback)"
    )
    repair_sub = p_repair.add_subparsers(dest="repair_cmd", required=True)
    p_propose = repair_sub.add_parser("propose", help="issue → repair proposal")
    p_propose.add_argument("issue_id", type=str)
    p_propose.add_argument("--file", type=str, required=True, help="source data file")
    p_propose.set_defaults(func=_cmd_repair_propose)
    p_preview = repair_sub.add_parser("preview", help="proposal + preview panel (rule re-run)")
    p_preview.add_argument("issue_id", type=str)
    p_preview.add_argument("--file", type=str, required=True, help="source data file")
    p_preview.set_defaults(func=_cmd_repair_preview)
    p_apply = repair_sub.add_parser("apply", help="apply repair (copy + .before artifact)")
    p_apply.add_argument("issue_id", type=str)
    p_apply.add_argument("--file", type=str, required=True, help="source data file")
    p_apply.set_defaults(func=_cmd_repair_apply)
    p_rollback = repair_sub.add_parser("rollback", help="rollback a repair run")
    p_rollback.add_argument("run_id", type=str)
    p_rollback.set_defaults(func=_cmd_repair_rollback)
    p_runs = repair_sub.add_parser("list", help="list repair runs")
    p_runs.set_defaults(func=_cmd_repair_list)

    p_llm = sub.add_parser("llm", help="LLM provider status & audit (Step 27, 13.11)")
    llm_sub = p_llm.add_subparsers(dest="llm_cmd", required=True)
    p_status = llm_sub.add_parser("status", help="show provider config & recent calls")
    p_status.set_defaults(func=_cmd_llm_status)
    p_invocations = llm_sub.add_parser("invocations", help="list recent LLM call audit")
    p_invocations.add_argument("--limit", type=int, default=20, help="max rows (default 20)")
    p_invocations.set_defaults(func=_cmd_llm_invocations)

    p_detectors = sub.add_parser("detectors", help="list registered detectors (built-in + plugins)")
    p_detectors.set_defaults(func=_cmd_detectors_list)

    p_rules = sub.add_parser("rules", help="data quality rules (14.1/14.4)")
    rules_sub = p_rules.add_subparsers(dest="rules_cmd", required=True)
    p_propose = rules_sub.add_parser(
        "propose", help="natural language → rule candidates (LLM, preflight, NOT saved)"
    )
    p_propose.add_argument("description", type=str, help="rule requirement in natural language")
    p_propose.add_argument(
        "--file", type=str, required=True, help="data file to profile + preflight"
    )
    p_propose.add_argument("--budget", type=int, default=20000, help="max output tokens (13.9)")
    p_propose.set_defaults(func=_cmd_rules_propose)
    p_approve = rules_sub.add_parser("approve", help="approve a proposed rule into store")
    p_approve.add_argument("rule_id", type=str)
    p_approve.add_argument(
        "--file",
        type=str,
        default=None,
        help="data file to re-run preflight against before approving",
    )
    p_approve.add_argument(
        "--force",
        action="store_true",
        help="approve even if preflight marks the rule dangerous (failures > 50% of rows)",
    )
    p_approve.set_defaults(func=_cmd_rules_approve)
    p_rule_list = rules_sub.add_parser("list", help="list approved rules")
    p_rule_list.set_defaults(func=_cmd_rules_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --version(0) / 用法错误(2) 由 argparse 直接退出
        return int(exc.code or EXIT_OK)
    try:
        func = cast("Callable[[argparse.Namespace], int]", args.func)
        return func(args)
    except KeyboardInterrupt:
        return EXIT_ERROR
    except Exception as exc:  # 执行期错误统一退出码 3
        _emit(_envelope(args.command, {"error": str(exc)}), args.format)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
