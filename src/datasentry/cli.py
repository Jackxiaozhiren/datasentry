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
from datasentry_core.connectors.errors import ConnectorError, DataSourceNotFoundError
from datasentry_core.llm.provider import LLMError
from datasentry_core.models.contract import Contract, QualityGate
from datasentry_core.models.enums import Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import ScanConfig
from datasentry_core.scoring.gate import GateResult

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
    """22.1 scan：导入 → 扫描 → 评分 → 落库；数据源缺失退出码 4；门禁失败退出码 1。

    --contract 绑定契约（Step 35）：quality_gate 求值 + 契约 rules 进
    ScanConfig.custom_rules；显式 --fail-on/--max-failure-ratio 覆盖契约 gate。
    契约 references（Step 40）触发跨表外键完整性检测。
    """
    client = DataSentry(args.project)
    config = ScanConfig(detectors=args.detector or None, seed=args.seed)
    contract = None
    if args.contract:
        contract = _load_contract(args.contract, args.format)
        if contract is None:
            return EXIT_CONFIG
        if contract.rules:
            config.custom_rules = contract.rules
    try:
        scan_run, runs, issues = client.scan_file(
            args.path,
            table_name=args.table,
            config=config,
            references=contract.references if contract is not None else None,
        )
    except FileNotFoundError as exc:
        _emit(_envelope("scan", {"error": str(exc)}), args.format)
        return EXIT_SOURCE_UNAVAILABLE
    except DataSourceNotFoundError as exc:
        _emit(_envelope("scan", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    except ConnectorError as exc:
        # Step 55：PG 连接失败等运行期源错误（凭据已净化）→ 源不可用
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
    if args.contract is not None or args.fail_on is not None:
        gate_result = _evaluate_gate(issues, args, contract, client, scan_run.dataset_id)
        gate_data = gate_result.model_dump()
        gate_data["failed_count"] = gate_result.failed_count
        summary["gate"] = gate_data
        _emit(_envelope("scan", summary), args.format)
        return EXIT_GATE_FAILED if not gate_result.passed else EXIT_OK
    _emit(_envelope("scan", summary), args.format)
    return EXIT_OK


def _load_contract(path: str, fmt: str) -> Contract | None:
    """读取并校验契约 YAML；失败返回 None（配置错误）。"""
    import yaml
    from pydantic import ValidationError

    from datasentry_core.models.contract import Contract

    contract_path = Path(path).expanduser()
    try:
        raw = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _emit(_envelope("contract validate", {"error": f"file not found: {path}"}), fmt)
        return None
    except yaml.YAMLError as exc:
        _emit(_envelope("contract validate", {"valid": False, "error": str(exc)}), fmt)
        return None
    try:
        return Contract.model_validate(raw)
    except ValidationError as exc:
        _emit(_envelope("contract validate", {"valid": False, "error": str(exc)}), fmt)
        return None


def _evaluate_gate(
    issues: list[Issue],
    args: argparse.Namespace,
    contract: Contract | None,
    client: DataSentry,
    dataset_id: str,
) -> GateResult:
    """22 章场景 C：契约 gate（--contract）或显式 --fail-on 求值。

    优先级：显式 --fail-on/--max-failure-ratio 覆盖契约 gate 对应项；
    require_repair_validation 的修复证据由 client 查询注入（Step 35）。
    """
    gate = QualityGate()
    if contract is not None and contract.quality_gate is not None:
        gate = contract.quality_gate
    if args.fail_on is not None:
        gate.fail_on = [Severity(args.fail_on)]
    if args.max_failure_ratio != 0.01 or gate.maximum_failed_rows_ratio == 0.01:
        gate.maximum_failed_rows_ratio = args.max_failure_ratio
    return client.evaluate_gate(issues, gate)


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
    elif args.as_format == "junit":
        from datasentry_core.reporting.junit import render_junit

        content = render_junit(report)
    elif args.as_format == "sarif":
        from datasentry_core.reporting.sarif import render_sarif

        content = json.dumps(render_sarif(report), ensure_ascii=False, indent=2)
    else:
        from datasentry.trends import build_trends
        from datasentry_core.reporting.html import render_html

        trends = [t.to_report_dict() for t in build_trends(client.list_scan_runs())]
        content = render_html(report, trends=trends or None)
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


def _cmd_contract_export(args: argparse.Namespace) -> int:
    """Step 37：契约 → Pandera 代码 / GE ExpectationSuite（V1 交付物）。"""
    from datasentry_core.contracts import to_great_expectations, to_pandera
    from datasentry_core.models.contract import Contract

    path = Path(args.path)
    if not path.is_file():
        _emit(_envelope("contract export", {"error": f"file not found: {path}"}), args.format)
        return EXIT_SOURCE_UNAVAILABLE
    try:
        import yaml

        contract = Contract.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except Exception as exc:  # YAML 语法或 schema 不符均视为导出失败
        _emit(_envelope("contract export", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    if args.as_format == "pandera":
        content = to_pandera(contract)
    else:
        content = json.dumps(to_great_expectations(contract), ensure_ascii=False, indent=2)
    out = (
        Path(args.output).expanduser()
        if args.output
        else path.with_name(f"{path.stem}.{'py' if args.as_format == 'pandera' else 'ge.json'}")
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    _emit(_envelope("contract export", {"path": str(out), "format": args.as_format}), args.format)
    return EXIT_OK


def _cmd_repair_propose(args: argparse.Namespace) -> int:
    """15 章：issue → 修复提案（--ai 走 AI 修复候选，Step 44）。"""
    client = DataSentry(args.project)
    try:
        if args.ai:
            proposal = client.repair_propose_ai(args.issue_id, args.file)
        else:
            proposal = client.repair_propose(args.issue_id, args.file)
    except (KeyError, ValueError) as exc:
        _emit(_envelope("repair propose", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    except LLMError as exc:
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
                "ai": bool(getattr(args, "ai", False)),
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


def _cmd_drift_compare(args: argparse.Namespace) -> int:
    """Step 39：两历史扫描版本漂移比较（18.2，V1）。"""
    client = DataSentry(args.project)
    try:
        report = client.drift_compare(
            args.reference_run_id,
            args.current_run_id,
            row_ratio_threshold=args.row_ratio_threshold,
            score_threshold=args.score_threshold,
        )
    except KeyError as exc:
        _emit(_envelope("drift compare", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    data = {
        "drift_report_id": report.id,
        "reference_dataset_id": report.reference_dataset_id,
        "current_dataset_id": report.current_dataset_id,
        "schema_changes": [
            {
                "change_type": c.change_type,
                "column": c.column,
                "before": c.before,
                "after": c.after,
            }
            for c in report.schema_changes
        ],
        "column_drifts": [
            {
                "column": d.column,
                "drift_type": d.drift_type,
                "metric": d.metric,
                "value": d.value,
                "threshold": d.threshold,
                "direction": d.direction,
                "severity": d.severity.value,
            }
            for d in report.column_drifts
        ],
    }
    _emit(_envelope("drift compare", data), args.format)
    return EXIT_OK


def _cmd_drift_latest(args: argparse.Namespace) -> int:
    """最近两次扫描比较；不足两次退出码 2。"""
    client = DataSentry(args.project)
    try:
        report = client.drift_latest(
            args.dataset_id,
            row_ratio_threshold=args.row_ratio_threshold,
            score_threshold=args.score_threshold,
        )
    except ValueError as exc:
        _emit(_envelope("drift latest", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    data = {
        "drift_report_id": report.id,
        "schema_changes": len(report.schema_changes),
        "column_drifts": len(report.column_drifts),
    }
    _emit(_envelope("drift latest", data), args.format)
    return EXIT_OK


def _cmd_llm_status(args: argparse.Namespace) -> int:
    """LLM 提供方状态与配置来源（13.11 审计查询入口）+ PII 加密保险库状态。"""
    from datasentry.llm_providers import load_llm_config
    from datasentry.pii_vault import PIIVault

    config = load_llm_config()
    client = DataSentry(args.project)
    try:
        invocations = client.list_llm_invocations(limit=20)
        vault = PIIVault(client._store)
        mappings = client._store.count_pii_mappings()
    finally:
        client.close()
    summary = {
        "provider": config.provider,
        "model": config.model or "n/a",
        "base_url": config.base_url or "n/a",
        "configured": config.provider != "null",
        "recent_calls": len(invocations),
        "last_status": invocations[0].status if invocations else None,
        "pii_vault": {"key_source": vault.key_source, "mappings": mappings},
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


def _cmd_llm_restore(args: argparse.Namespace) -> int:
    """PII 加密会话管理（Step 48）：列表 / 摘要 / 还原文本 / 删除。

    显式授权语义：CLI 是本地用户命令，`restore <session>` 即授权
    查看明文；报告与 UI 默认打码不受影响。
    """
    from datasentry.pii_vault import PIIVault, VaultKeyMissingError, format_mapping_summary

    client = DataSentry(args.project)
    try:
        vault = PIIVault(client._store)
        warnings: list[str] = []
        if vault.key_source == "dev":
            warnings.append(
                "using built-in development key: set DATASENTRY_ENCRYPTION_KEY "
                "or run 'datasentry llm rotate-key'"
            )
        if args.session_id is None:
            sessions = client._store.list_pii_mappings(limit=args.limit)
            data = {
                "sessions": [
                    {
                        "session_id": s["session_id"],
                        "key_version": s["key_version"],
                        "created_at": s["created_at"].isoformat(),
                    }
                    for s in sessions
                ]
            }
            _emit(_envelope("llm restore", data, warnings), args.format)
            return EXIT_OK
        session_id = args.session_id
        if args.delete:
            deleted = client._store.delete_pii_mapping(session_id)
            _emit(
                _envelope("llm restore", {"deleted": deleted, "session_id": session_id}, warnings),
                args.format,
            )
            return EXIT_OK
        if args.text is not None:
            restored = vault.restore_text(args.text, session_id)
            _emit(
                _envelope(
                    "llm restore", {"session_id": session_id, "restored": restored}, warnings
                ),
                args.format,
            )
            return EXIT_OK
        mapping = vault.load_mapping(session_id)
        _emit(
            _envelope(
                "llm restore",
                {"session_id": session_id, "mapping": format_mapping_summary(mapping)},
                warnings,
            ),
            args.format,
        )
        return EXIT_OK
    except VaultKeyMissingError as exc:
        _emit(_envelope("llm restore", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    except KeyError as exc:
        _emit(_envelope("llm restore", {"error": str(exc)}), args.format)
        return EXIT_ERROR
    finally:
        client.close()


def _cmd_llm_rotate_key(args: argparse.Namespace) -> int:
    """轮换 PII 加密密钥：新密钥重加密全部映射 + 写入本地 key 文件。"""
    from datasentry.pii_vault import PIIVault, VaultKeyMissingError

    client = DataSentry(args.project)
    try:
        vault = PIIVault(client._store)
        result = vault.rotate_key(new_key=args.new_key)
        data = {
            "new_key": result["new_key"],
            "rotated": result["rotated"],
            "key_file": result["key_file"],
        }
        if result["rotated"] == 0:
            data["note"] = "no encrypted mappings existed; key file created"
        _emit(_envelope("llm rotate-key", data), args.format)
        return EXIT_OK
    except VaultKeyMissingError as exc:
        _emit(_envelope("llm rotate-key", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    finally:
        client.close()


def _cmd_detectors_list(args: argparse.Namespace) -> int:
    """列出注册表检测器（内置 + workspace/plugins 插件，ADR-031）。"""
    client = DataSentry(args.project)
    try:
        detectors = client.list_detectors()
    finally:
        client.close()
    _emit(_envelope("detectors list", {"detectors": detectors}), args.format)
    return EXIT_OK


def _cmd_plugin_list(args: argparse.Namespace) -> int:
    """列出已发现插件（Step 50，V2-C，ADR-050）：目录 + entry points，含失败项。"""
    client = DataSentry(args.project)
    try:
        result = client.list_plugins()
    finally:
        client.close()
    _emit(_envelope("plugin list", result), args.format)
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

    p_scan = sub.add_parser(
        "scan", help="scan a data file or a PostgreSQL table (postgresql://DSN, Step 55)"
    )
    p_scan.add_argument("path", type=str, help="data file path or postgresql:// DSN")
    p_scan.add_argument(
        "--table",
        type=str,
        default=None,
        help="table name for DuckDB/SQLite files or PostgreSQL "
        "(required for .duckdb/.db/.sqlite and postgresql:// DSN, Step 38/54/55)",
    )
    p_scan.add_argument(
        "--contract",
        type=str,
        default=None,
        help="contract YAML: binds quality_gate + rules to the scan (Step 35)",
    )
    p_scan.add_argument(
        "--detector", action="append", default=None, help="detector whitelist (repeatable)"
    )
    p_scan.add_argument(
        "--fail-on",
        type=str,
        default=None,
        choices=["info", "low", "medium", "high", "critical"],
        help="quality gate: fail (exit 1) on issues at this severity; "
        "overrides --contract (22 章场景 C)",
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
        choices=["json", "markdown", "html", "junit", "sarif"],
        help="report format (json|markdown|html|junit|sarif, default: json)",
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
    p_export = contract_sub.add_parser("export", help="export contract to external ecosystems (V1)")
    p_export.add_argument("path", type=str)
    p_export.add_argument(
        "--as",
        dest="as_format",
        type=str,
        default="pandera",
        choices=["pandera", "ge"],
        help="export target (pandera|ge, default: pandera)",
    )
    p_export.add_argument("--output", type=str, default=None, help="output file path")
    p_export.set_defaults(func=_cmd_contract_export)

    p_drift = sub.add_parser("drift", help="drift engine (18.2, V1): historical scan comparison")
    drift_sub = p_drift.add_subparsers(dest="drift_cmd", required=True)
    p_compare = drift_sub.add_parser("compare", help="compare two scan runs")
    p_compare.add_argument("reference_run_id", type=str)
    p_compare.add_argument("current_run_id", type=str)
    p_compare.add_argument("--row-ratio-threshold", type=float, default=0.20)
    p_compare.add_argument("--score-threshold", type=float, default=5.0)
    p_compare.set_defaults(func=_cmd_drift_compare)
    p_latest = drift_sub.add_parser("latest", help="compare the two most recent scans of a dataset")
    p_latest.add_argument("dataset_id", type=str)
    p_latest.add_argument("--row-ratio-threshold", type=float, default=0.20)
    p_latest.add_argument("--score-threshold", type=float, default=5.0)
    p_latest.set_defaults(func=_cmd_drift_latest)

    p_repair = sub.add_parser(
        "repair", help="repair engine (15 章, ADR-020; propose→preview→apply→rollback)"
    )
    repair_sub = p_repair.add_subparsers(dest="repair_cmd", required=True)
    p_propose = repair_sub.add_parser("propose", help="issue → repair proposal")
    p_propose.add_argument("issue_id", type=str)
    p_propose.add_argument("--file", type=str, required=True, help="source data file")
    p_propose.add_argument(
        "--ai", action="store_true", help="AI repair candidate (Step 44, needs LLM)"
    )
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
    p_restore = llm_sub.add_parser(
        "restore", help="PII encrypted-mapping sessions: list / preview / restore (Step 48)"
    )
    p_restore.add_argument("session_id", type=str, nargs="?", help="encrypted mapping session")
    p_restore.add_argument("--text", type=str, default=None, help="restore placeholders in TEXT")
    p_restore.add_argument("--delete", action="store_true", help="delete the session mapping")
    p_restore.add_argument("--limit", type=int, default=100, help="max sessions (default 100)")
    p_restore.set_defaults(func=_cmd_llm_restore)
    p_rotate = llm_sub.add_parser(
        "rotate-key", help="re-encrypt all mappings with a new key (writes local key file)"
    )
    p_rotate.add_argument("--new-key", type=str, default=None, help="new key material")
    p_rotate.set_defaults(func=_cmd_llm_rotate_key)

    p_detectors = sub.add_parser("detectors", help="list registered detectors (built-in + plugins)")
    p_detectors.set_defaults(func=_cmd_detectors_list)

    p_plugin = sub.add_parser("plugin", help="list discovered plugins (dir + entry points, V2-C)")
    plugin_sub = p_plugin.add_subparsers(dest="plugin_cmd", required=True)
    p_plugin_list = plugin_sub.add_parser("list", help="list plugins & load failures")
    p_plugin_list.set_defaults(func=_cmd_plugin_list)

    from datasentry.mcp_server import build_mcp_parser, run_mcp

    p_mcp = build_mcp_parser(sub)
    p_mcp.set_defaults(func=run_mcp)

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
