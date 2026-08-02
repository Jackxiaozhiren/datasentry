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

from datasentry_core.connectors import (
    DataSourceSpec,
    DataSourceType,
    default_registry,
)
from datasentry_core.detectors import DetectionContext, DetectorRegistry
from datasentry_core.detectors.initial import register_default_detectors
from datasentry_core.detectors.runner import ScanRunner
from datasentry_core.models.enums import Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.repair import RepairPreview, RepairProposal, RepairRun
from datasentry_core.models.scan import DetectorRun, ScanConfig, ScanRun
from datasentry_core.repair import RepairEngine
from datasentry_core.reporting import build_report
from datasentry_core.storage import MetadataStore

_SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]

# 文件扩展名 → 数据源类型（Step 18：scan_file 自动推断，覆盖 CSV/Parquet/JSONL/XLSX）
_EXT_TO_SOURCE_TYPE: dict[str, DataSourceType] = {
    ".csv": DataSourceType.CSV,
    ".tsv": DataSourceType.CSV,
    ".parquet": DataSourceType.PARQUET,
    ".pq": DataSourceType.PARQUET,
    ".jsonl": DataSourceType.JSONL,
    ".ndjson": DataSourceType.JSONL,
    ".xlsx": DataSourceType.XLSX,
}


def _source_type_for_path(path: Path) -> DataSourceType | None:
    """按扩展名推断数据源类型，未知返回 None。"""
    return _EXT_TO_SOURCE_TYPE.get(path.suffix.lower())


class DataSentry:
    """项目工作区门面：持有元数据库与扫描入口（23.1 MVP 子集）。"""

    def __init__(self, project: str | Path | None = None) -> None:
        self._workspace = Path(project).expanduser() if project else Path.cwd()
        self._store = MetadataStore.for_workspace(self._workspace)
        self._runner = ScanRunner(self._registry())
        self._ensure_gitignore()

    @staticmethod
    def _registry() -> DetectorRegistry:
        registry = DetectorRegistry()
        register_default_detectors(registry)
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

    # ---- 扫描 ----------------------------------------------------------

    def scan_file(
        self,
        path: str | Path,
        *,
        dataset_id: str | None = None,
        config: ScanConfig | None = None,
    ) -> tuple[ScanRun, list[DetectorRun], list[Issue]]:
        """导入 + 扫描 + 评分 + 落库（数据源不可用抛 FileNotFoundError 类异常）。"""
        source_path = Path(path).expanduser()
        if not source_path.is_file():
            raise FileNotFoundError(f"data source not found: {source_path}")
        source_type = _source_type_for_path(source_path)
        if source_type is None:
            raise FileNotFoundError(
                f"unsupported data source format: {source_path.suffix or '(no extension)'}"
            )
        dataset_id = dataset_id or source_path.stem
        spec = DataSourceSpec(
            source_type=source_type,
            path=source_path,
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
            )
            scan_run, runs, issues = self._runner.run_scan(context, config)
            self._store.save_scan(scan_run, runs, issues)
            return scan_run, runs, issues
        finally:
            handle.close()

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
            preview = engine.preview(proposal, context, self._registry())
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
