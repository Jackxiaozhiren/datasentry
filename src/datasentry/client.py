"""DataSentry SDK 客户端（23.1）：MVP 闭环 = 导入 → 扫描 → 落库 → 查询 → 报告。

使用方式：
    from datasentry import DataSentry

    client = DataSentry(project="/path/to/workspace")  # 默认当前目录
    scan, runs, issues = client.scan_file("data.csv")
    print(scan.id, len(issues))
    issues = client.list_issues(severity_at_least="high")
    report = client.export_report(scan.id)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from datasentry.repair_ai import AIRepairService
from datasentry_core.connectors import (
    DataSourceSpec,
    DataSourceType,
    default_registry,
)
from datasentry_core.connectors.spec import EXT_TO_SOURCE_TYPE as _EXT_TO_SOURCE_TYPE
from datasentry_core.detectors import DetectionContext, DetectorRegistry
from datasentry_core.detectors.initial import register_default_detectors
from datasentry_core.detectors.runner import ScanRunner
from datasentry_core.llm.provider import LLMError
from datasentry_core.models.contract import QualityGate, TableReference
from datasentry_core.models.drift import DriftReport
from datasentry_core.models.enums import Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.llm import LLMInvocation
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.repair import RepairPreview, RepairProposal, RepairRun
from datasentry_core.models.rules import Rule
from datasentry_core.models.scan import DetectorRun, ScanConfig, ScanRun
from datasentry_core.repair import RepairEngine
from datasentry_core.reporting import build_report
from datasentry_core.scoring.gate import GateResult, QualityGateEvaluator
from datasentry_core.storage import MetadataStore

_SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]


def _source_type_for_path(path: Path) -> DataSourceType | None:
    """按扩展名推断数据源类型，未知返回 None。"""
    return _EXT_TO_SOURCE_TYPE.get(path.suffix.lower())


class DataSentry:
    """项目工作区门面：持有元数据库与扫描入口（23.1 MVP 子集）。"""

    def __init__(self, project: str | Path | None = None) -> None:
        self._workspace = Path(project).expanduser() if project else Path.cwd()
        self._store = MetadataStore.for_workspace(self._workspace)
        self._registry = self._registry_with_plugins()
        self._runner = ScanRunner(self._registry)
        self._last_table_name: dict[str, str | None] = {}
        self._ensure_gitignore()

    def _registry_with_plugins(self) -> DetectorRegistry:
        """内置 + 目录插件（Step 31）+ entry points 插件（Step 50，V2-C）。

        目录插件保持 fail-fast（ADR-031 语义）；entry points 插件优雅降级，
        失败项经 `list_plugins()` 可观测（ADR-050）。
        """
        from datasentry_core.plugins import (
            DETECTOR_ENTRY_POINT_GROUP,
            discover_entrypoint_detectors,
            load_plugin_detectors,
        )

        registry = DetectorRegistry()
        register_default_detectors(registry)
        self._source_map = {d.detector_id: "builtin" for d in registry.list()}
        self._plugin_errors: list[dict[str, str]] = []
        for detector_id in load_plugin_detectors(registry, [self._workspace / "plugins"]):
            self._source_map[detector_id] = "dir"
        report = discover_entrypoint_detectors(registry, group=DETECTOR_ENTRY_POINT_GROUP)
        for detector_id in report.loaded:
            self._source_map[detector_id] = "entrypoint"
        self._plugin_errors = [{"name": err.name, "error": err.message} for err in report.errors]
        return registry

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def db_path(self) -> Path:
        return self._store.db_path

    @property
    def reports_dir(self) -> Path:
        """报告导出默认目录：<workspace>/.datasentry/reports（ADR-010）。"""
        from datasentry_core.storage.paths import project_reports_dir

        return project_reports_dir(self._workspace)

    def _ensure_gitignore(self) -> None:
        """ADR-010：工作区打开即确保 .gitignore 含 .datasentry/ 条目（防元数据入库）。"""
        gitignore = self._workspace / ".gitignore"
        entry = ".datasentry/\n"
        if gitignore.exists() and entry in gitignore.read_text(encoding="utf-8"):
            return
        with gitignore.open("a", encoding="utf-8") as f:
            f.write(entry)

    def close(self) -> None:
        """关闭元数据库连接。"""
        self._store.close()

    def list_detectors(self) -> list[dict[str, Any]]:
        """注册表快照（内置 + 插件）：检测器元数据，供 CLI/UI 展示。"""
        result: list[dict[str, Any]] = []
        for detector in self._registry.list():
            meta = detector.metadata()
            result.append(
                {
                    "detector_id": meta.detector_id,
                    "display_name": meta.display_name,
                    "description": meta.description,
                    "quality_dimension": meta.quality_dimension.value,
                    "version": detector.detector_version,
                    "enabled": self._registry.is_enabled(detector.detector_id),
                    "source": self._source_map.get(detector.detector_id, "builtin"),
                }
            )
        return result

    def list_plugins(self) -> dict[str, Any]:
        """插件清单（Step 50，V2-C）：已加载插件 + 失败项（entry points 优雅降级）。"""
        plugins = [d for d in self.list_detectors() if d["source"] in ("dir", "entrypoint")]
        return {
            "plugins": plugins,
            "errors": self._plugin_errors,
            "workspace_plugins_dir": str(self._workspace / "plugins"),
        }

    # ---- 扫描 ----------------------------------------------------------

    def scan_file(
        self,
        path: str | Path,
        *,
        dataset_id: str | None = None,
        table_name: str | None = None,
        config: ScanConfig | None = None,
        references: list[TableReference] | None = None,
    ) -> tuple[ScanRun, list[DetectorRun], list[Issue]]:
        """导入 + 扫描 + 评分 + 落库（数据源不可用抛 FileNotFoundError 类异常）。

        table_name：DuckDB/SQLite/PostgreSQL/MySQL 必填（Step 38/54/55/56）；其他格式忽略。
        path 传 postgresql://、postgres:// 或 mysql:// URL 时按对应远程数据库
        数据源处理（Step 55/56，V4/V5）：凭据经 spec.options["dsn"] 内存态
        流转，不落库/日志/报告。
        references：契约跨表引用（Step 40），触发外键完整性检测。
        """
        if isinstance(path, str) and (
            path.startswith("postgresql://") or path.startswith("postgres://")
        ):
            spec, dataset_id = self._postgres_spec(path, dataset_id, table_name)
        elif isinstance(path, str) and path.startswith("mysql://"):
            spec, dataset_id = self._mysql_spec(path, dataset_id, table_name)
        elif isinstance(path, str) and (
            path.startswith("s3://") or path.startswith("gs://") or path.startswith("az://")
        ):
            spec, dataset_id = self._remote_spec(path, dataset_id, table_name)
        else:
            source_path = Path(path).expanduser()
            if not source_path.is_file():
                raise FileNotFoundError(f"data source not found: {source_path}")
            source_type = _source_type_for_path(source_path)
            if source_type is None:
                raise FileNotFoundError(
                    f"unsupported data source format: {source_path.suffix or '(no extension)'}"
                )
            dataset_id = dataset_id or source_path.stem
            self._last_table_name[str(source_path)] = table_name
            spec = DataSourceSpec(
                source_type=source_type,
                path=source_path,
                table_name=table_name,
                options={"dataset_id": dataset_id},
            )
        handle = default_registry().open(spec)
        try:
            context = DetectionContext(
                dataset_id=dataset_id,
                table_name=None,
                columns=handle.schema().column_names,
                handle=handle,
                config=config or ScanConfig(),
                references=references,
            )
            scan_run, runs, issues = self._runner.run_scan(context, config)
            self._store.save_scan(scan_run, runs, issues)
            return scan_run, runs, issues
        finally:
            handle.close()

    @staticmethod
    def _postgres_spec(
        dsn: str,
        dataset_id: str | None,
        table_name: str | None,
    ) -> tuple[DataSourceSpec, str]:
        """PostgreSQL 数据源 spec（Step 55）：DSN 走 options 内存态，缺表名由连接器报错。"""
        resolved = dataset_id or table_name or "postgres"
        return (
            DataSourceSpec(
                source_type=DataSourceType.POSTGRESQL,
                table_name=table_name,
                options={"dsn": dsn, "dataset_id": resolved},
            ),
            resolved,
        )

    @staticmethod
    def _mysql_spec(
        dsn: str,
        dataset_id: str | None,
        table_name: str | None,
    ) -> tuple[DataSourceSpec, str]:
        """MySQL 数据源 spec（Step 56）：DSN 走 options 内存态，缺表名由连接器报错。"""
        resolved = dataset_id or table_name or "mysql"
        return (
            DataSourceSpec(
                source_type=DataSourceType.MYSQL,
                table_name=table_name,
                options={"dsn": dsn, "dataset_id": resolved},
            ),
            resolved,
        )

    @staticmethod
    def _remote_spec(
        uri: str,
        dataset_id: str | None,
        table_name: str | None,
    ) -> tuple[DataSourceSpec, str]:
        """云存储文件数据源 spec（Step 57，V5）：s3:// gs:// az:// URI。

        格式按 URI 路径后缀推断（csv/parquet/jsonl）；凭据只走进程环境
        （httpfs 原生读取），非机密会话配置（endpoint/region 等）可经
        options["s3_*"] 传入；缺后缀（.gz 等）由连接器报可操作错误。
        """
        from urllib.parse import urlparse

        suffix = urlparse(uri).path.rsplit(".", 1)[-1].lower()
        source_type = _source_type_for_path(Path(f"x.{suffix}"))
        if source_type is None or source_type not in (
            DataSourceType.CSV,
            DataSourceType.PARQUET,
            DataSourceType.JSONL,
        ):
            raise FileNotFoundError(
                f"unsupported cloud data source format: s3:// gs:// az:// files "
                f"must use .csv/.parquet/.jsonl suffixes, got: {uri}"
            )
        resolved = dataset_id or uri.split("/", 3)[2]
        return (
            DataSourceSpec(
                source_type=source_type,
                path=uri,
                table_name=table_name,
                options={"dataset_id": resolved},
            ),
            resolved,
        )

    # ---- 查询 ----------------------------------------------------------

    def list_issues(
        self,
        *,
        severity_at_least: str | None = None,
        scan_run_id: str | None = None,
    ) -> list[Issue]:
        """Issue 列表；severity_at_least（info/low/medium/high/critical）按权重下限过滤。"""
        if scan_run_id:
            all_issues = self._store.get_issues(scan_run_id)
        else:
            all_issues = [
                issue
                for scan in self._store.list_scan_runs()
                for issue in self._store.get_issues(scan.id)
            ]
        if severity_at_least is None:
            return all_issues
        floor = Severity(severity_at_least)
        return [
            issue
            for issue in all_issues
            if _SEVERITY_ORDER.index(issue.severity) <= _SEVERITY_ORDER.index(floor)
        ]

    def get_scan(self, scan_run_id: str) -> ScanRun | None:
        return self._store.get_scan_run(scan_run_id)

    def list_scan_runs(self) -> list[ScanRun]:
        """ScanRun 列表（按创建时间降序）。"""
        return self._store.list_scan_runs()

    def drift_compare(
        self,
        reference_run_id: str,
        current_run_id: str,
        *,
        row_ratio_threshold: float = 0.20,
        score_threshold: float = 5.0,
    ) -> DriftReport:
        """Step 39：两历史扫描版本漂移比较（18.2，V1）。"""
        from datasentry_core.drift import compare_scans

        reference = self._store.get_scan_run(reference_run_id)
        current = self._store.get_scan_run(current_run_id)
        if reference is None or current is None:
            raise KeyError("scan run not found")
        return compare_scans(
            reference,
            current,
            self._store.get_issues(reference_run_id),
            self._store.get_issues(current_run_id),
            row_ratio_threshold=row_ratio_threshold,
            score_threshold=score_threshold,
        )

    def drift_latest(
        self,
        dataset_id: str,
        *,
        row_ratio_threshold: float = 0.20,
        score_threshold: float = 5.0,
    ) -> DriftReport:
        """最近两次该数据集的扫描比较；不足两次抛 ValueError。"""
        runs = [r for r in self._store.list_scan_runs(dataset_id) if r.status == "completed"]
        if len(runs) < 2:
            raise ValueError(f"dataset {dataset_id!r} has fewer than 2 completed scans")
        return self.drift_compare(
            runs[-1].id,
            runs[0].id,
            row_ratio_threshold=row_ratio_threshold,
            score_threshold=score_threshold,
        )

    def get_detector_runs(self, scan_run_id: str) -> list[DetectorRun]:
        return self._store.get_detector_runs(scan_run_id)

    def quality_score(self, scan_run_id: str) -> QualityScore | None:
        """27 章：扫描时计算的质量总分（随 ScanRun 落库，历史保留原权重与 score_version）。"""
        scan = self.get_scan(scan_run_id)
        if scan is None:
            raise KeyError(f"scan run not found: {scan_run_id}")
        return scan.quality_score

    def export_report(self, scan_run_id: str) -> dict[str, object]:
        """26.2 规范 JSON 报告：报告头 + scan + detector_runs + issues + quality。"""
        scan = self.get_scan(scan_run_id)
        if scan is None:
            raise KeyError(f"scan run not found: {scan_run_id}")
        runs = self.get_detector_runs(scan_run_id)
        issues = self._store.get_issues(scan_run_id)
        return build_report(
            scan,
            runs,
            issues,
            scan.quality_score,
            generated_at=scan.finished_at or None,
        )

    # ---- 修复（Step 21，15 章 / ADR-020） --------------------------------

    def evaluate_gate(
        self,
        issues: list[Issue],
        gate: QualityGate,
        *,
        dataset_id: str | None = None,
    ) -> GateResult:
        """质量门禁求值（22 章场景 C）：require_repair_validation 时注入修复证据。

        修复证据 = 工作区内存在已应用（applied）状态的修复记录（Step 35）。
        """
        repair_validated = (
            self._store.has_applied_repairs() if gate.require_repair_validation else False
        )
        return QualityGateEvaluator().evaluate(issues, gate, repair_validated=repair_validated)

    def repair_open(self, source_path: str | Path) -> DetectionContext:
        """打开数据源句柄供修复引擎使用（源路径由 CLI 显式传入）。"""
        path = Path(source_path).expanduser()
        source_type = _source_type_for_path(path)
        if source_type is None:
            raise FileNotFoundError(
                f"unsupported data source format: {path.suffix or '(no extension)'}"
            )
        spec = DataSourceSpec(
            source_type=source_type,
            path=path,
            table_name=self._last_table_name.get(str(path)),
            options={"dataset_id": path.stem},
        )
        handle = default_registry().open(spec)
        return DetectionContext(
            dataset_id=path.stem,
            table_name=None,
            columns=handle.schema().column_names,
            handle=handle,
        )

    def repair_propose(
        self,
        issue_id: str,
        source_path: str | Path,
    ) -> RepairProposal | None:
        """Issue → 修复提案；不支持的 issue 返回 None（并落库提案）。"""
        issue = self._store.get_issue_by_id(issue_id)
        if issue is None:
            raise KeyError(f"issue not found: {issue_id}")
        context = self.repair_open(source_path)
        try:
            proposal = RepairEngine().propose(issue, context)
        finally:
            context.handle.close()
        if proposal is not None:
            self._store.save_repair_proposal(proposal)
        return proposal

    def repair_propose_ai(
        self,
        issue_id: str,
        source_path: str | Path,
    ) -> RepairProposal | None:
        """Issue → AI 修复候选（Step 44）；未配置 LLM 抛 LLMNotConfiguredError。

        规则引擎兜底：LLM 只在检测器对应的操作集内选择（clip 边界 /
        rationale 可生成），候选经审计并落库（status=proposed）。
        """
        service = AIRepairService(self._store)
        result = service.propose(issue_id, str(source_path))
        if result.llm_error is not None:
            raise LLMError(result.llm_error)
        return result.proposal

    def repair_preview(
        self,
        issue_id: str,
        source_path: str | Path,
    ) -> tuple[RepairProposal, RepairPreview] | None:
        """提案 + 预览（统计面板 + 规则重跑前后）。"""
        issue = self._store.get_issue_by_id(issue_id)
        if issue is None:
            raise KeyError(f"issue not found: {issue_id}")
        context = self.repair_open(source_path)
        try:
            engine = RepairEngine()
            proposal = engine.propose(issue, context)
            if proposal is None:
                return None
            preview = engine.preview(proposal, context, self._registry)
        finally:
            context.handle.close()
        return proposal, preview

    def repair_apply(
        self,
        issue_id: str,
        source_path: str | Path,
        *,
        workspace: Path | None = None,
    ) -> RepairRun:
        """应用修复：写修复副本 + before artifact + 落库 run。"""
        issue = self._store.get_issue_by_id(issue_id)
        if issue is None:
            raise KeyError(f"issue not found: {issue_id}")
        context = self.repair_open(source_path)
        try:
            engine = RepairEngine()
            proposal = engine.propose(issue, context)
            if proposal is None:
                raise ValueError(f"no repair proposal for issue: {issue_id} ({issue.issue_type})")
            run = engine.apply(proposal, context, workspace or self.workspace)
        finally:
            context.handle.close()
        self._store.save_repair_run(run)
        return run

    def repair_rollback(self, run_id: str) -> RepairRun:
        """回滚：读库中的 run，重建 rolled_back 副本，更新 run 状态。"""
        run = self._store.get_repair_run(run_id)
        if run is None:
            raise KeyError(f"repair run not found: {run_id}")
        rolled = RepairEngine().rollback(run, self.workspace)
        self._store.save_repair_run(rolled)
        return rolled

    def list_repair_runs(self) -> list[RepairRun]:
        return self._store.list_repair_runs()

    def list_llm_invocations(self, limit: int = 20) -> list[LLMInvocation]:
        """最近 LLM 调用审计（13.11；不含 prompt 原文）。"""
        return self._store.list_llm_invocations(limit=limit)

    def list_rules(self) -> list[Rule]:
        """已落库规则列表（14.1/14.4）。"""
        return self._store.list_rules()

    def get_issue(self, issue_id: str) -> Issue | None:
        """按 ID 取 Issue（修复工作台等 UI 用途）。"""
        return self._store.get_issue_by_id(issue_id)
