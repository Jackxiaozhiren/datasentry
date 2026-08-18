"""DataSentry CLI（22 章 MVP 子集）。

命令：init / scan（--fail-on 门禁）/ issues list|show / report export（--as）/
score / contract validate
全局选项：--project / --format text|json / --seed / --lang en|zh / --version
JSON 统一 envelope（22.1）：{"ok", "command", "data", "warnings", "llm_usage"}
退出码：0 成功；1 质量门禁失败；2 配置错误；3 执行错误；4 数据源不可用
"""

from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import re
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from datasentry import __version__
from datasentry.client import DataSentry
from datasentry_core.connectors.errors import ConnectorError, DataSourceNotFoundError
from datasentry_core.llm.provider import LLMError
from datasentry_core.models.contract import Contract, QualityGate
from datasentry_core.models.enums import RepairRunStatus, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import SamplingConfig, ScanConfig
from datasentry_core.reporting.i18n import t
from datasentry_core.scoring.gate import GateResult

if TYPE_CHECKING:
    from datasentry.scheduler.store import SchedulerStore

EXIT_OK = 0
EXIT_GATE_FAILED = 1  # scan --fail-on 质量门禁（22 章场景 C）
EXIT_CONFIG = 2
EXIT_ERROR = 3
EXIT_SOURCE_UNAVAILABLE = 4

_GLOBAL_EPILOG = """Global options:
  --project PATH  project workspace (default: current dir)
  --format FMT    text|json (default: text)
  --seed N        reproducibility seed
  --lang LANG     en|zh CLI text output language (default: en)
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


def _cli_scan_progress(done: int, total: int, name: str) -> None:
    """扫描检测器进度（stderr，不污染 stdout 的 JSON 输出；\r 原地刷新）。"""
    sys.stderr.write(f"\rscan: detector {done + 1}/{total} — {name}  ")
    sys.stderr.flush()


def _cli_scan_done() -> None:
    sys.stderr.write("\r" + " " * 80 + "\r")
    sys.stderr.flush()


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


_SECRET_KEY_HELP = "secret key (must match env-var shape [A-Z][A-Z0-9_]*, e.g. DATASENTRY_PG_DSN)"


def _secret_key_or_error(name: str, fmt: str) -> str | None:
    import re

    from datasentry_core.secrets import _KEY_PATTERN

    if not re.fullmatch(_KEY_PATTERN.pattern, name):
        _emit(_envelope("secrets", {"error": f"invalid key {name!r}; {_SECRET_KEY_HELP}"}), fmt)
        return None
    return name


def _cmd_secrets_set(args: argparse.Namespace) -> int:
    """secrets set：交互式无回显写入/更新凭据（chmod 600 强制，不进 shell history）。"""
    fmt = args.format
    name = _secret_key_or_error(args.key, fmt)
    if name is None:
        return EXIT_CONFIG
    import getpass

    from datasentry_core.secrets import SecretsFileError, set_secret

    value = getpass.getpass(f"secret value for {name} (no echo): ")
    confirm = getpass.getpass(f"confirm {name}: ")
    if value != confirm:
        _emit(_envelope("secrets", {"error": "confirmation mismatch, secret not saved"}), fmt)
        return EXIT_CONFIG
    try:
        path = set_secret(name, value)
    except SecretsFileError as exc:
        _emit(_envelope("secrets", {"error": str(exc)}), fmt)
        return EXIT_CONFIG
    _emit(_envelope("secrets set", {"key": name, "path": str(path)}), fmt)
    return EXIT_OK


def _cmd_secrets_get(args: argparse.Namespace) -> int:
    """secrets get：读出单条凭据（stdout 原值，供脚本/环境注入）。"""
    fmt = args.format
    name = _secret_key_or_error(args.key, fmt)
    if name is None:
        return EXIT_CONFIG
    from datasentry_core.secrets import lookup_secret

    value = lookup_secret(name)
    if value is None:
        _emit(
            _envelope("secrets", {"error": f"secret not set (env or secrets.env): {name}"}),
            fmt,
        )
        return EXIT_CONFIG
    _emit(_envelope("secrets get", {"key": name, "value": value}), fmt)
    return EXIT_OK


def _cmd_secrets_list(args: argparse.Namespace) -> int:
    """secrets list：仅显示键名与文件路径，绝不显示值（审计语义）。"""
    from datasentry_core.secrets import load_secrets, secrets_path

    path = secrets_path()
    secrets = load_secrets(path)
    _emit(
        _envelope("secrets list", {"path": str(path), "keys": sorted(secrets)}),
        args.format,
    )
    return EXIT_OK


def _cmd_secrets_rm(args: argparse.Namespace) -> int:
    """secrets rm：删除单条凭据。"""
    fmt = args.format
    name = _secret_key_or_error(args.key, fmt)
    if name is None:
        return EXIT_CONFIG
    from datasentry_core.secrets import SecretsFileError, remove_secret

    try:
        removed = remove_secret(name)
    except SecretsFileError as exc:
        _emit(_envelope("secrets", {"error": str(exc)}), fmt)
        return EXIT_CONFIG
    if not removed:
        _emit(_envelope("secrets", {"error": f"secret not set: {name}"}), fmt)
        return EXIT_CONFIG
    _emit(_envelope("secrets rm", {"key": name}), fmt)
    return EXIT_OK


def _cmd_ui(args: argparse.Namespace) -> int:
    """118：进入交互式终端界面（Textual，ADR-118）。"""
    from datasentry.tui import run_tui

    return run_tui(args.project)


def _cmd_scan(args: argparse.Namespace) -> int:
    """22.1 scan：导入 → 扫描 → 评分 → 落库；数据源缺失退出码 4；门禁失败退出码 1。

    --contract 绑定契约（Step 35）：quality_gate 求值 + 契约 rules 进
    ScanConfig.custom_rules；显式 --fail-on/--max-failure-ratio 覆盖契约 gate。
    契约 references（Step 40）触发跨表外键完整性检测。
    """
    client = DataSentry(args.project)
    config = ScanConfig(detectors=args.detector or None, seed=args.seed)
    if args.sampling_size is not None or args.sampling_ratio is not None:
        config.sampling = SamplingConfig(
            method=args.sampling_method,
            sample_size=args.sampling_size,
            ratio=args.sampling_ratio,
            seed=args.sampling_seed,
        )
    contract = None
    if args.contract:
        contract = _load_contract(args.contract, args.format)
        if contract is None:
            return EXIT_CONFIG
        if contract.rules:
            config.custom_rules = contract.rules
    paths = _expand_cli_paths(args.path)
    if len(paths) > 1:
        return _scan_many(client, args, config, contract, paths)
    target = paths[0] if paths else args.path
    try:
        scan_run, runs, issues = client.scan_file(
            target,
            table_name=args.table,
            config=config,
            references=contract.references if contract is not None else None,
            on_progress=_cli_scan_progress,
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
    _cli_scan_done()
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


def _expand_cli_paths(raw: str) -> list[str]:
    """CLI 批量路径解析（V26）：逗号/分号/换行分隔 + glob 展开 + 去重保序。

    不存在的路径保留原样（交给 scan_file 报错），保持单文件语义。
    """
    seen: set[str] = set()
    paths: list[str] = []
    for part in re.split(r"[,\n;]+", raw):
        part = part.strip().strip("\"'")
        if not part or part in seen:
            continue
        seen.add(part)
        expanded = _glob.glob(str(Path(part).expanduser()))
        for c in expanded or [part]:
            if c not in paths:
                paths.append(c)
    return paths


def _scan_many(
    client: DataSentry,
    args: argparse.Namespace,
    config: ScanConfig,
    contract: Contract | None,
    paths: list[str],
) -> int:
    """V26：批量扫描多文件 → 逐文件汇总；失败文件记入 errors，任一失败退出码 4。

    gate（--fail-on/契约）对每个文件求值，任一不通过退出码 1。
    """
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    gate_failed = False
    for path in paths:
        p = Path(path)

        def on_progress(done: int, total: int, name: str, _path: Path = p) -> None:
            sys.stderr.write(f"\rscan {_path.name}: detector {done + 1}/{total} — {name}  ")
            sys.stderr.flush()

        try:
            scan_run, runs, issues = client.scan_file(
                path,
                table_name=args.table,
                config=config,
                references=contract.references if contract is not None else None,
                on_progress=on_progress,
            )
        except (FileNotFoundError, DataSourceNotFoundError, ConnectorError) as exc:
            errors.append({"path": path, "error": str(exc)})
            continue
        sys.stderr.write("\r" + " " * 80 + "\r")
        sys.stderr.flush()
        item: dict[str, Any] = {
            "scan_run_id": scan_run.id,
            "dataset_id": scan_run.dataset_id,
            "status": scan_run.status,
            "row_count": scan_run.fingerprint.row_count,
            "total_issues": len(issues),
            "detector_runs": len(runs),
            "quality_score": scan_run.quality_score.overall if scan_run.quality_score else None,
        }
        if args.contract is not None or args.fail_on is not None:
            gate_result = _evaluate_gate(issues, args, contract, client, scan_run.dataset_id)
            item["gate"] = {"passed": gate_result.passed, "failed_count": gate_result.failed_count}
            if not gate_result.passed:
                gate_failed = True
        results.append(item)
    if not results:
        detail = "; ".join(f"{e['path']}: {e['error']}" for e in errors)
        _emit(_envelope("scan", {"batch": [], "errors": errors, "error": detail}), args.format)
        return EXIT_SOURCE_UNAVAILABLE
    summary: dict[str, Any] = {
        "batch": results,
        "errors": errors,
        "files_scanned": len(results),
        "files_failed": len(errors),
        "total_issues": sum(int(r["total_issues"]) for r in results),
    }
    if args.format == "text" and results:
        for r in results:
            print(
                f"{r['dataset_id']}: run={r['scan_run_id']} issues={r['total_issues']} "
                f"score={r['quality_score'] if r['quality_score'] is not None else '—'}"
            )
        if errors:
            print(f"{len(errors)} file(s) failed:", file=sys.stderr)
            for e in errors:
                print(f"  {e['path']}: {e['error']}", file=sys.stderr)
    else:
        _emit(_envelope("scan", summary), args.format)
    return EXIT_GATE_FAILED if gate_failed else (EXIT_SOURCE_UNAVAILABLE if errors else EXIT_OK)


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
    """22.1 issues list：Issue 列表（--severity 过滤，--limit 截断）。"""
    client = DataSentry(args.project)
    issues = client.list_issues(severity_at_least=args.severity, scan_run_id=args.scan_run)
    if args.limit is not None:
        issues = issues[: args.limit]
    data = {"issues": [i.model_dump(mode="json") for i in issues], "count": len(issues)}
    if args.format == "json":
        _emit(_envelope("issues list", data), args.format)
    else:
        for issue in issues:
            print("\n".join(_issue_lines(issue)))
        print("\n" + t(args.lang, "cli.issues_count").format(n=len(issues)))
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

        content = render_markdown(report, lang=args.lang)
    elif args.as_format == "junit":
        from datasentry_core.reporting.junit import render_junit

        content = render_junit(report)
    elif args.as_format == "sarif":
        from datasentry_core.reporting.sarif import render_sarif

        content = json.dumps(render_sarif(report), ensure_ascii=False, indent=2)
    else:
        from datasentry.trends import build_comparison, build_trends
        from datasentry_core.reporting.html import render_html

        trends = [t.to_report_dict() for t in build_trends(client.list_scan_runs())]
        profiles = client.load_profile(args.run_id)
        dataset_id = str(cast("dict[str, Any]", report)["scan"]["dataset_id"])
        comparison = build_comparison(client.list_scan_runs(), dataset_id, args.run_id)
        content = render_html(
            report,
            trends=trends or None,
            profiles=profiles,
            comparison=comparison,
            lang=args.lang,
        )
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
    run_id = args.run_id
    if run_id == "latest":
        runs = client._store.list_scan_runs()
        if not runs:
            _emit(_envelope("score", {"error": "no scan runs yet"}), args.format)
            return EXIT_CONFIG
        run_id = runs[0].id
    try:
        quality = client.quality_score(run_id)
    except KeyError as exc:
        _emit(_envelope("score", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    if quality is None:
        _emit(_envelope("score", {"scored": False}), args.format)
        return EXIT_OK
    data = {"scored": True, "scan_run_id": run_id, "score": quality.model_dump(mode="json")}
    if args.format == "text":
        print(
            t(args.lang, "cli.score_overall").format(
                score=quality.overall, version=quality.score_version
            )
        )
        for dim, value in quality.dimensions.items():
            label = f"{dim:15s} {value!s:>6}"
            weight = quality.weights.get(dim)
            print(
                t(args.lang, "cli.score_weight").format(
                    label=label, weight=weight if weight is not None else "-"
                )
            )
        print(t(args.lang, "cli.score_notes").format(notes=quality.calculation_notes))
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


def _cmd_repair_propose_batch(args: argparse.Namespace) -> int:
    """V36：批量提案——run 下多个 issue（--issues 逗号分隔或 --all）。"""
    client = DataSentry(args.project)
    issue_ids = _resolve_issue_ids(client, args)
    results: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for issue_id in issue_ids:
        try:
            proposal = client.repair_propose(issue_id, args.file)
        except Exception as exc:
            errors[issue_id] = str(exc)
            continue
        if proposal is None:
            results.append({"issue_id": issue_id, "proposed": False})
            continue
        results.append(
            {
                "issue_id": issue_id,
                "proposed": True,
                "operation": proposal.operation.value,
                "target_columns": proposal.target_columns,
                "estimated_rows_changed": proposal.estimated_rows_changed,
                "rationale": proposal.rationale,
            }
        )
    _emit(
        _envelope(
            "repair propose-batch",
            {"issues": results, "errors": errors, "failed": len(errors)},
        ),
        args.format,
    )
    return EXIT_SOURCE_UNAVAILABLE if errors else EXIT_OK


def _cmd_repair_apply_batch(args: argparse.Namespace) -> int:
    """V36：批量应用——逐条写修复副本 + before 快照（源文件不覆盖）。"""
    client = DataSentry(args.project)
    issue_ids = _resolve_issue_ids(client, args)
    results: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for issue_id in issue_ids:
        try:
            if client.repair_propose(issue_id, args.file) is None:
                results.append({"issue_id": issue_id, "applied": False, "reason": "no_proposal"})
                continue
            run = client.repair_apply(issue_id, args.file)
        except Exception as exc:
            errors[issue_id] = str(exc)
            continue
        results.append(
            {
                "issue_id": issue_id,
                "applied": True,
                "run_id": run.id,
                "fingerprint_before": run.fingerprint_before,
                "fingerprint_after": run.fingerprint_after,
                "changed": run.fingerprint_before != run.fingerprint_after,
            }
        )
    _emit(
        _envelope(
            "repair apply-batch",
            {"applied": results, "errors": errors, "failed": len(errors)},
        ),
        args.format,
    )
    return EXIT_SOURCE_UNAVAILABLE if errors else EXIT_OK


def _cmd_repair_rollback_batch(args: argparse.Namespace) -> int:
    """V36：批量回滚——逐条恢复 before 快照。"""
    client = DataSentry(args.project)
    results: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for run_id in [r.strip() for r in args.run_ids.split(",") if r.strip()]:
        try:
            run = client.repair_rollback(run_id)
        except Exception as exc:
            errors[run_id] = str(exc)
            continue
        results.append({"run_id": run.id, "status": run.status.value})
    _emit(
        _envelope(
            "repair rollback-batch",
            {"rolled_back": results, "errors": errors, "failed": len(errors)},
        ),
        args.format,
    )
    return EXIT_SOURCE_UNAVAILABLE if errors else EXIT_OK


def _resolve_issue_ids(client: DataSentry, args: argparse.Namespace) -> list[str]:
    """batch 命令共用：--issues 逗号分隔（缺省 = run 下全部 issues）。"""
    if getattr(args, "issues", None):
        return [i.strip() for i in args.issues.split(",") if i.strip()]
    if getattr(args, "all", False):
        return [i.id for i in client.list_issues(scan_run_id=args.run)]
    raise ValueError("specify --issues or --all")


def _cmd_repair_list(args: argparse.Namespace) -> int:
    """列出修复执行记录（--run 过滤到单个扫描 run 的修复）。"""
    client = DataSentry(args.project)
    runs = client.list_repair_runs()
    if getattr(args, "run", None):
        target = client.get_scan(args.run)
        if target is None:
            raise ValueError(f"scan run not found: {args.run}")
        runs = [r for r in runs if r.dataset_id == target.dataset_id]
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
        ],
        "summary": {
            "total": len(runs),
            "applied": sum(1 for r in runs if r.status == RepairRunStatus.APPLIED),
            "rolled_back": sum(1 for r in runs if r.status == RepairRunStatus.ROLLED_BACK),
            "failed": sum(1 for r in runs if r.status == RepairRunStatus.FAILED),
        },
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


def _cmd_trend_list(args: argparse.Namespace) -> int:
    """V7（ADR-065）trend list：跨扫描趋势数据面（同 build_trends）。"""
    from datasentry.trends import build_trends

    client = DataSentry(args.project)
    trends = build_trends(client.list_scan_runs())
    if args.dataset_id is not None:
        trends = [t for t in trends if t.dataset_id == args.dataset_id]
    data = {
        "trends": [
            {
                **t.to_report_dict(),
                "delta": t.delta,
                "direction": t.direction,
                "latest_score": t.latest_score,
                "latest_issues": t.latest_issues,
            }
            for t in trends
        ],
        "count": len(trends),
    }
    _emit(_envelope("trend list", data), args.format)
    return EXIT_OK


def _job_store(project: str | Path) -> SchedulerStore:
    """项目调度存储（与 MCP/API 同源：<workspace>/.datasentry/metadata.db）。"""
    from datasentry.scheduler.store import SchedulerStore
    from datasentry_core.storage.paths import project_db_path

    return SchedulerStore(project_db_path(Path(project)))


def _cmd_job_list(args: argparse.Namespace) -> int:
    """列出调度任务（V13，ADR-086）。"""
    jobs = _job_store(args.project).list_jobs()
    views = [j.view() for j in jobs]
    if args.status is not None:
        views = [v for v in views if v["status"] == args.status]
    _emit(_envelope("job list", {"jobs": views, "count": len(views)}), args.format)
    return EXIT_OK


def _cmd_job_create(args: argparse.Namespace) -> int:
    """注册调度任务（cron + 门禁 + webhook；与 MCP job_create 同语义）。"""
    from datasentry.scheduler.core import InvalidCronError, next_run, validate_cron
    from datasentry.scheduler.models import JobCommand, ScheduledJob, utcnow

    try:
        validate_cron(args.cron)
    except InvalidCronError as exc:
        _emit(_envelope("job create", {"error": str(exc)}), args.format)
        return EXIT_CONFIG
    client = DataSentry(args.project)
    try:
        project = str(client.workspace)
        path = str(Path(args.path).expanduser())
        now = utcnow()
        job = ScheduledJob(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            name=args.name,
            project=project,
            command=JobCommand(
                project=project,
                path=path,
                dataset_id=args.dataset_id,
                table_name=args.table_name,
                export_report=args.export_report,
            ),
            cron=args.cron,
            retry_attempts=args.retry_attempts,
            webhook_url=args.webhook_url,
            gate_quality_min=args.gate_quality_min,
            next_run_at=next_run(args.cron, now),
            created_at=now,
            updated_at=now,
        )
        _job_store(project).create_job(job)
    finally:
        client.close()
    _emit(_envelope("job create", job.view()), args.format)
    return EXIT_OK


def _cmd_job_trigger(args: argparse.Namespace) -> int:
    """立即触发一次任务（同步执行；已在运行则拒绝）。

    `--remote-url`（V21，ADR-111）时委托远端 worker 执行：
    `RemoteScanExecutor`（token 必填、传输层重试、可选 preflight），
    报告自动回传本工作区 `.datasentry/reports`（尽力而为）。
    """
    from datasentry.scheduler.core import LocalScanExecutor, ScanExecutor, Scheduler
    from datasentry.scheduler.remote import RemoteScanExecutor
    from datasentry_core.storage.paths import project_reports_dir

    store = _job_store(args.project)
    job = store.get_job(args.job_id)
    if job is None:
        _emit(_envelope("job trigger", {"error": f"job not found: {args.job_id}"}), args.format)
        return EXIT_CONFIG
    if args.remote_url:
        if not args.remote_token:
            _emit(
                _envelope(
                    "job trigger",
                    {"error": "remote execution requires --remote-token (worker data plane)"},
                ),
                args.format,
            )
            return EXIT_CONFIG
        executor: ScanExecutor = RemoteScanExecutor(
            args.remote_url,
            args.remote_token,
            retries=args.remote_retries,
            report_dir=project_reports_dir(Path(args.project)),
            preflight=args.remote_preflight,
        )
    else:
        executor = LocalScanExecutor()
    run_id = Scheduler(store=store, executor=executor).trigger(args.job_id)
    if run_id is None:
        _emit(
            _envelope("job trigger", {"error": f"job {args.job_id} is already running"}),
            args.format,
        )
        return EXIT_CONFIG
    _emit(_envelope("job trigger", {"job_id": args.job_id, "run_id": run_id}), args.format)
    return EXIT_OK


def _cmd_job_cancel(args: argparse.Namespace) -> int:
    """取消正在运行的任务（run → cancelled，job → idle）。

    `--remote-url`（V22，ADR-115）时尽力而为通知远端 worker（网络
    失败仅警告，调度端取消语义不受影响）。
    """
    from datasentry.scheduler.core import LocalScanExecutor, Scheduler

    store = _job_store(args.project)
    run_id = Scheduler(store=store, executor=LocalScanExecutor()).cancel(args.job_id)
    if run_id is None:
        _emit(
            _envelope("job cancel", {"job_id": args.job_id, "error": "job is not running"}),
            args.format,
        )
        return EXIT_CONFIG
    data: dict[str, object] = {"job_id": args.job_id, "run_id": run_id, "status": "cancelled"}
    if args.remote_url:
        if not args.remote_token:
            _emit(
                _envelope(
                    "job cancel",
                    {"job_id": args.job_id, "error": "remote notify requires --remote-token"},
                ),
                args.format,
            )
            return EXIT_CONFIG
        try:
            from datasentry.scheduler.remote import RemoteScanExecutor

            RemoteScanExecutor(args.remote_url, args.remote_token).cancel(run_id)
            data["remote_notified"] = True
        except Exception as exc:  # 尽力而为：失败仅警告
            data["remote_notified"] = False
            data["remote_warning"] = str(exc)
    _emit(_envelope("job cancel", data), args.format)
    return EXIT_OK


def _cmd_job_status(args: argparse.Namespace) -> int:
    """任务视图 + 最近运行历史。"""
    store = _job_store(args.project)
    job = store.get_job(args.job_id)
    if job is None:
        _emit(_envelope("job status", {"error": f"job not found: {args.job_id}"}), args.format)
        return EXIT_CONFIG
    data = job.view()
    data["recent_runs"] = [r.view() for r in store.list_runs(args.job_id, limit=5)]
    _emit(_envelope("job status", data), args.format)
    return EXIT_OK


def _cmd_job_remove(args: argparse.Namespace) -> int:
    """删除调度任务。"""
    if not _job_store(args.project).delete_job(args.job_id):
        _emit(_envelope("job remove", {"error": f"job not found: {args.job_id}"}), args.format)
        return EXIT_CONFIG
    _emit(_envelope("job remove", {"job_id": args.job_id, "removed": True}), args.format)
    return EXIT_OK


def _cmd_worker(args: argparse.Namespace) -> int:
    """启动远端执行节点（V14，ADR-091）：复用 api 服务，/rpc/execute
    需 token 启用（参数或 DATASENTRY_WORKER_TOKEN 环境变量）。"""
    import uvicorn

    from datasentry.api import create_app

    token = args.token or os.environ.get("DATASENTRY_WORKER_TOKEN")
    if not token:
        _emit(
            _envelope(
                "worker",
                {
                    "warning": "no worker token set: /rpc/execute disabled; "
                    "pass --token or set DATASENTRY_WORKER_TOKEN"
                },
            ),
            args.format,
        )
    uvicorn.run(create_app(args.project, worker_token=token), host=args.host, port=args.port)
    return EXIT_OK


def _cmd_ping(args: argparse.Namespace) -> int:
    """探测远端 worker 健康（`GET /rpc/health` 公开信息面，无需 token）。"""
    from datasentry.scheduler.remote import RemoteScanExecutor

    executor = RemoteScanExecutor(
        args.url,
        args.token or "",
        timeout=args.timeout,
    )
    try:
        info = executor.health()
    except Exception as exc:  # 网络/HTTP/契约失败统一 EXIT_ERROR
        _emit(_envelope("ping", {"url": args.url, "error": str(exc)}), args.format)
        return EXIT_ERROR
    _emit(
        _envelope(
            "ping",
            {
                "url": args.url,
                "service": info.get("service"),
                "version": info.get("version"),
                "worker": info.get("worker"),
                "ok": True,
            },
        ),
        args.format,
    )
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
        "pii_vault": {
            "key_source": vault.key_source,
            "key_fingerprint": vault.key_fingerprint,
            "key_file": vault.key_file_info,
            "mappings": mappings,
        },
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
        if args.purge:
            if args.session_id is not None:
                _emit(
                    _envelope(
                        "llm restore",
                        {"error": "--purge cannot be combined with a session_id"},
                    ),
                    args.format,
                )
                return EXIT_ERROR
            if args.older_than < 1:
                _emit(
                    _envelope("llm restore", {"error": "--older-than must be >= 1"}),
                    args.format,
                )
                return EXIT_ERROR
            purged = vault.purge_sessions(args.older_than)
            _emit(_envelope("llm restore", {"purged": purged}, warnings), args.format)
            return EXIT_OK
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
    """列出已发现插件（Step 50/82）：目录 + entry points，含失败项与清单。"""
    client = DataSentry(args.project)
    try:
        result = client.list_plugins()
    finally:
        client.close()
    _emit(_envelope("plugin list", result), args.format)
    return EXIT_OK


def _cmd_plugin_install(args: argparse.Namespace) -> int:
    """安装插件（Step 82，ADR-082）：文件或目录 → workspace/plugins/<name>/。"""
    client = DataSentry(args.project)
    try:
        result = client.install_plugin(args.source)
    except (FileNotFoundError, ValueError) as exc:
        _emit(_envelope("plugin install", {"error": str(exc)}), args.format)
        return EXIT_SOURCE_UNAVAILABLE
    finally:
        client.close()
    _emit(_envelope("plugin install", result), args.format)
    return EXIT_OK


def _cmd_plugin_uninstall(args: argparse.Namespace) -> int:
    """卸载插件（Step 82，ADR-082）：删除 workspace/plugins/<name>/。"""
    client = DataSentry(args.project)
    try:
        result = client.uninstall_plugin(args.name)
    except FileNotFoundError as exc:
        _emit(_envelope("plugin uninstall", {"error": str(exc)}), args.format)
        return EXIT_SOURCE_UNAVAILABLE
    finally:
        client.close()
    _emit(_envelope("plugin uninstall", result), args.format)
    return EXIT_OK


def _cmd_plugin_reaccept(args: argparse.Namespace) -> int:
    """重新锁定插件当前内容（Step 83，ADR-083）：完整性校验失败后放行。"""
    client = DataSentry(args.project)
    try:
        result = client.reaccept_plugin(args.name)
    except FileNotFoundError as exc:
        _emit(_envelope("plugin reaccept", {"error": str(exc)}), args.format)
        return EXIT_SOURCE_UNAVAILABLE
    finally:
        client.close()
    _emit(_envelope("plugin reaccept", result), args.format)
    return EXIT_OK


def _cmd_plugin_test(args: argparse.Namespace) -> int:
    """插件测试夹具（Step 84，ADR-084）：manifest fixtures 断言。"""
    client = DataSentry(args.project)
    try:
        result = client.test_plugin(args.name)
    except FileNotFoundError as exc:
        _emit(_envelope("plugin test", {"error": str(exc)}), args.format)
        return EXIT_SOURCE_UNAVAILABLE
    finally:
        client.close()
    _emit(_envelope("plugin test", result), args.format)
    if not result.get("passed", False):
        return EXIT_GATE_FAILED
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
        print(t(args.lang, "cli.llm_cache"))
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
        print(t(args.lang, "cli.llm_proposed").format(n=len(data["rules"])))
        for entry in data["rules"]:
            if "rejected" in entry:
                print(t(args.lang, "cli.llm_rejected").format(reason=entry["rejected"]))
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
    parser.add_argument(
        "--lang",
        type=str,
        default="en",
        choices=["en", "zh"],
        help="CLI text output language (en|zh, Step 74/ADR-074; default: en)",
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="initialize workspace (.datasentry/ + .gitignore)")
    p_init.set_defaults(func=_cmd_init)

    p_ui = sub.add_parser(
        "ui",
        help=(
            "interactive terminal UI (Textual; also the default when run with no command, Step 118)"
        ),
    )
    p_ui.set_defaults(func=_cmd_ui)

    p_scan = sub.add_parser(
        "scan",
        help="scan a data file, PostgreSQL table (postgresql://DSN, Step 55), "
        "MySQL table (mysql://DSN, Step 56) or cloud file (s3:// gs:// az:// "
        "CSV/Parquet/JSONL, Step 57)",
    )
    p_scan.add_argument(
        "path",
        type=str,
        help="data file path, postgresql:// or mysql:// DSN, or s3:// gs:// az:// URI",
    )
    p_scan.add_argument(
        "--table",
        type=str,
        default=None,
        help="table name for DuckDB/SQLite files or PostgreSQL/MySQL "
        "(required for .duckdb/.db/.sqlite and postgresql:// / mysql:// DSN, "
        "Step 38/54/55/56; ignored for cloud file URIs)",
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
        "--sampling-size",
        type=int,
        default=None,
        help="sample N rows for sampling-capable detectors (reservoir, "
        "Step 71/ADR-071; requires --sampling-size or --sampling-ratio to enable)",
    )
    p_scan.add_argument(
        "--sampling-ratio",
        type=float,
        default=None,
        help="sample ratio in (0, 1] of total rows (mutually exclusive with "
        "--sampling-size; Step 71/ADR-071)",
    )
    p_scan.add_argument(
        "--sampling-method",
        type=str,
        default="reservoir",
        choices=["random", "reservoir", "none"],
        help="sampling method (default reservoir; Step 71/ADR-071)",
    )
    p_scan.add_argument(
        "--sampling-seed",
        type=int,
        default=42,
        help="sampling reproducibility seed (default 42; Step 71/ADR-071)",
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
    p_list.add_argument("--limit", type=int, default=None, help="max issues to show")
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
    p_score.add_argument(
        "run_id",
        nargs="?",
        type=str,
        default="latest",
        help="scan run id, or `latest` for the most recent scan (default: latest)",
    )
    p_score.set_defaults(func=_cmd_score)

    p_trend = sub.add_parser("trend", help="cross-scan quality trends (V7, ADR-065)")
    trend_sub = p_trend.add_subparsers(dest="trend_cmd", required=True)
    p_trend_list = trend_sub.add_parser("list", help="list per-dataset trends")
    p_trend_list.add_argument("--dataset-id", type=str, default=None, help="restrict to dataset")
    p_trend_list.set_defaults(func=_cmd_trend_list)

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
    p_propose_batch = repair_sub.add_parser("propose-batch", help="batch proposals for a scan run")
    p_propose_batch.add_argument("run", type=str, help="scan run id")
    p_propose_batch.add_argument("--file", type=str, required=True, help="source data file")
    p_propose_batch.add_argument("--issues", type=str, default="", help="comma-separated issue ids")
    p_propose_batch.add_argument(
        "--all", action="store_true", help="all issues of the run (default when no --issues)"
    )
    p_propose_batch.set_defaults(func=_cmd_repair_propose_batch)
    p_apply_batch = repair_sub.add_parser("apply-batch", help="batch apply repairs for a run")
    p_apply_batch.add_argument("run", type=str, help="scan run id")
    p_apply_batch.add_argument("--file", type=str, required=True, help="source data file")
    p_apply_batch.add_argument("--issues", type=str, default="", help="comma-separated issue ids")
    p_apply_batch.add_argument(
        "--all", action="store_true", help="all issues of the run (default when no --issues)"
    )
    p_apply_batch.set_defaults(func=_cmd_repair_apply_batch)
    p_rollback_batch = repair_sub.add_parser("rollback-batch", help="batch rollback repair runs")
    p_rollback_batch.add_argument("run_ids", type=str, help="comma-separated repair run ids")
    p_rollback_batch.set_defaults(func=_cmd_repair_rollback_batch)
    p_runs = repair_sub.add_parser("list", help="list repair runs")
    p_runs.add_argument("--run", type=str, default="", help="filter to a scan run's dataset")
    p_runs.set_defaults(func=_cmd_repair_list)

    p_job = sub.add_parser(
        "job", help="scheduled scan jobs (V13: list/create/trigger/status/remove, ADR-086)"
    )
    job_sub = p_job.add_subparsers(dest="job_cmd", required=True)
    p_job_list = job_sub.add_parser("list", help="list scheduled jobs")
    p_job_list.add_argument(
        "--status", type=str, default=None, help="filter by status (idle/queued/running/dead)"
    )
    p_job_list.set_defaults(func=_cmd_job_list)
    p_job_create = job_sub.add_parser("create", help="register a scheduled scan job")
    p_job_create.add_argument("name", type=str, help="job name")
    p_job_create.add_argument("path", type=str, help="data file/DSN/cloud URI to scan")
    p_job_create.add_argument(
        "--cron", type=str, required=True, help="5-field cron expression, e.g. '0 9 * * *'"
    )
    p_job_create.add_argument("--dataset-id", type=str, default=None, help="dataset id")
    p_job_create.add_argument("--table-name", type=str, default=None, help="table name (remote DB)")
    p_job_create.add_argument("--retry-attempts", type=int, default=0, help="retry attempts (0-10)")
    p_job_create.add_argument(
        "--webhook-url", type=str, default=None, help="notify URL (HTTP POST)"
    )
    p_job_create.add_argument(
        "--gate-quality-min", type=float, default=None, help="quality gate threshold (0-100)"
    )
    p_job_create.add_argument(
        "--export-report", action="store_true", help="export HTML report after scan"
    )
    p_job_create.set_defaults(func=_cmd_job_create)
    p_job_trigger = job_sub.add_parser("trigger", help="run a job immediately")
    p_job_trigger.add_argument("job_id", type=str)
    p_job_trigger.add_argument(
        "--remote-url",
        type=str,
        default=None,
        help="execute on remote worker base URL (e.g. http://127.0.0.1:8000); "
        "omitted = local execution",
    )
    p_job_trigger.add_argument(
        "--remote-token",
        type=str,
        default=None,
        help="worker token for remote execution (required with --remote-url)",
    )
    p_job_trigger.add_argument(
        "--remote-retries",
        type=int,
        default=0,
        help="transport-level retries for remote execution (default 0)",
    )
    p_job_trigger.add_argument(
        "--remote-preflight",
        action="store_true",
        help="health-probe the worker before remote execution (fast fail)",
    )
    p_job_trigger.set_defaults(func=_cmd_job_trigger)
    p_job_cancel = job_sub.add_parser("cancel", help="cancel a running job (V22, ADR-114)")
    p_job_cancel.add_argument("job_id", type=str)
    p_job_cancel.add_argument(
        "--remote-url",
        type=str,
        default=None,
        help="worker base URL to notify (best-effort, V22/ADR-115)",
    )
    p_job_cancel.add_argument(
        "--remote-token",
        type=str,
        default=None,
        help="worker token (required with --remote-url)",
    )
    p_job_cancel.set_defaults(func=_cmd_job_cancel)
    p_job_status = job_sub.add_parser("status", help="job view + recent run history")
    p_job_status.add_argument("job_id", type=str)
    p_job_status.set_defaults(func=_cmd_job_status)
    p_job_remove = job_sub.add_parser("remove", help="delete a scheduled job")
    p_job_remove.add_argument("job_id", type=str)
    p_job_remove.set_defaults(func=_cmd_job_remove)

    p_worker = sub.add_parser(
        "worker", help="run as remote scan worker (V14: /rpc/execute endpoint, ADR-091)"
    )
    p_worker.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    p_worker.add_argument("--port", type=int, default=8000, help="bind port (default 8000)")
    p_worker.add_argument(
        "--token",
        default=None,
        help="worker token (default DATASENTRY_WORKER_TOKEN env; "
        "unset = /rpc/execute disabled, plain API service)",
    )
    p_worker.set_defaults(func=_cmd_worker)

    p_ping = sub.add_parser(
        "ping",
        help="probe a remote worker health (V21: GET /rpc/health, ADR-112)",
    )
    p_ping.add_argument("url", type=str, help="worker base URL, e.g. http://127.0.0.1:8000")
    p_ping.add_argument(
        "--token",
        type=str,
        default=None,
        help="worker token (optional: /rpc/health is public info plane)",
    )
    p_ping.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="probe timeout in seconds (default 10)",
    )
    p_ping.set_defaults(func=_cmd_ping)

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
    p_restore.add_argument(
        "--purge",
        action="store_true",
        help="delete sessions older than --older-than days (Step 103)",
    )
    p_restore.add_argument(
        "--older-than",
        type=int,
        default=30,
        help="purge sessions created before N days (default 30, requires --purge)",
    )
    p_restore.set_defaults(func=_cmd_llm_restore)
    p_rotate = llm_sub.add_parser(
        "rotate-key", help="re-encrypt all mappings with a new key (writes local key file)"
    )
    p_rotate.add_argument("--new-key", type=str, default=None, help="new key material")
    p_rotate.set_defaults(func=_cmd_llm_rotate_key)

    p_detectors = sub.add_parser("detectors", help="list registered detectors (built-in + plugins)")
    p_detectors.set_defaults(func=_cmd_detectors_list)

    p_plugin = sub.add_parser("plugin", help="manage plugins (list/install/uninstall, V12)")
    plugin_sub = p_plugin.add_subparsers(dest="plugin_cmd", required=True)
    p_plugin_list = plugin_sub.add_parser("list", help="list plugins, manifests & load failures")
    p_plugin_list.set_defaults(func=_cmd_plugin_list)
    p_plugin_install = plugin_sub.add_parser(
        "install", help="install plugin from file or directory (Step 82, ADR-082)"
    )
    p_plugin_install.add_argument("source", help="plugin .py file or directory (with plugin.yaml)")
    p_plugin_install.set_defaults(func=_cmd_plugin_install)
    p_plugin_uninstall = plugin_sub.add_parser(
        "uninstall", help="remove installed plugin by name (Step 82, ADR-082)"
    )
    p_plugin_uninstall.add_argument("name", help="plugin name (manifest name / directory name)")
    p_plugin_uninstall.set_defaults(func=_cmd_plugin_uninstall)
    p_plugin_reaccept = plugin_sub.add_parser(
        "reaccept",
        help="re-lock plugin current content after integrity failure (Step 83, ADR-083)",
    )
    p_plugin_reaccept.add_argument("name", help="plugin name to re-lock")
    p_plugin_reaccept.set_defaults(func=_cmd_plugin_reaccept)
    p_plugin_test = plugin_sub.add_parser(
        "test", help="run plugin fixtures declared in plugin.yaml (Step 84, ADR-084)"
    )
    p_plugin_test.add_argument("name", help="plugin name to test")
    p_plugin_test.set_defaults(func=_cmd_plugin_test)

    p_secrets = sub.add_parser(
        "secrets",
        help="credential store (~/.config/datasentry/secrets.env, chmod 600; "
        "Step 59, ADR-059): connection_ref 统一解析链 env > secrets.env",
    )
    secrets_sub = p_secrets.add_subparsers(dest="secrets_cmd", required=True)
    p_set = secrets_sub.add_parser("set", help="set/update a secret (interactive, no echo)")
    p_set.add_argument("key", type=str, help=_SECRET_KEY_HELP)
    p_set.set_defaults(func=_cmd_secrets_set)
    p_get = secrets_sub.add_parser("get", help="read a secret value")
    p_get.add_argument("key", type=str, help=_SECRET_KEY_HELP)
    p_get.set_defaults(func=_cmd_secrets_get)
    p_list = secrets_sub.add_parser("list", help="list secret key names only (audit)")
    p_list.set_defaults(func=_cmd_secrets_list)
    p_rm = secrets_sub.add_parser("rm", help="remove a secret")
    p_rm.add_argument("key", type=str, help=_SECRET_KEY_HELP)
    p_rm.set_defaults(func=_cmd_secrets_rm)

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
    if not hasattr(args, "func"):
        # 无子命令 → 交互式终端界面（Step 118，ADR-118）
        from datasentry.tui import run_tui

        return run_tui(getattr(args, "project", None))
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
