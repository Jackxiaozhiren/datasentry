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

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from datasentry.pii_vault import PIIVault
    from datasentry_core.scoring.replay import ReplayReport

from datasentry.repair_ai import AIRepairService
from datasentry_core.connectors import (
    DataHandle,
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
from datasentry_core.models.fingerprint import DatasetFingerprint
from datasentry_core.models.issue import Issue
from datasentry_core.models.llm import LLMInvocation
from datasentry_core.models.profile import ColumnProfile
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.repair import (
    RepairPreview,
    RepairProposal,
    RepairRun,
    RepairVerifyReport,
)
from datasentry_core.models.rules import Rule
from datasentry_core.models.scan import DetectorRun, ScanConfig, ScanRun
from datasentry_core.repair import RepairEngine
from datasentry_core.reporting import build_report
from datasentry_core.rescan import RescanReport, RescanStatus, SourceKind, diff_issue_sets
from datasentry_core.scoring.gate import GateResult, QualityGateEvaluator
from datasentry_core.storage import MetadataStore
from datasentry_core.storage.fixtures import (
    DEFAULT_MAX_BYTES,
    CaptureReport,
    CaptureRow,
    FixtureEntry,
    FixtureStore,
)

_SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]


def _source_type_for_path(path: Path) -> DataSourceType | None:
    """按扩展名推断数据源类型，未知返回 None。"""
    return _EXT_TO_SOURCE_TYPE.get(path.suffix.lower())


#: 重扫会打到活系统或远端凭据的源前缀。AC-11b 只重扫本地 fixture，这些必须显式拒绝，
#: 否则"重扫"会变成对生产库的又一次查询。
_REMOTE_SOURCE_PREFIXES = ("postgresql://", "postgres://", "mysql://", "s3://", "gs://", "az://")

#: 按表扫描、但 `ScanRun` 不持久化 `table_name` 的源类型：重扫无法定位到具体表。
_TABLE_SCOPED_SOURCES = frozenset({DataSourceType.SQLITE, DataSourceType.DUCKDB})


def _fingerprint_change(run: ScanRun, fresh: DatasetFingerprint) -> str:
    """存档指纹 vs 重算指纹：相同返回空串，不同返回可读原因。

    只有指纹相同，issue 集差异才能归因到检测器代码；否则差异同时含"数据变了"这一项，
    本工具不得给出结论。
    """
    stored = run.fingerprint
    if stored.fingerprint_type != fresh.fingerprint_type:
        return f"指纹档位不同 ({stored.fingerprint_type} vs {fresh.fingerprint_type})"
    if stored.schema_hash != fresh.schema_hash:
        return "列结构已变, issue 集差异无法归因到检测器"
    if stored.row_count != fresh.row_count:
        return f"行数已变 ({stored.row_count} -> {fresh.row_count})"
    if stored.file_sha256 and fresh.file_sha256 and stored.file_sha256 != fresh.file_sha256:
        return "文件 SHA-256 已变, issue 集差异无法归因到检测器"
    if (
        stored.content_sample_hash
        and fresh.content_sample_hash
        and stored.content_sample_hash != fresh.content_sample_hash
    ):
        return "抽样内容指纹已变, issue 集差异无法归因到检测器"
    return ""


class DataSentry:
    """项目工作区门面：持有元数据库与扫描入口（23.1 MVP 子集）。"""

    def __init__(
        self,
        project: str | Path | None = None,
        *,
        enforce_scan_containment: bool = False,
    ) -> None:
        self._workspace = Path(project).expanduser() if project else Path.cwd()
        # D5-13: a face that takes paths from the network confines them to this workspace.
        # The CLI and the local scheduler stay on the operator's own trust boundary, so the
        # flag is opt-in per construction site instead of a rule inside scan_file.
        self._enforce_scan_containment = enforce_scan_containment
        self._store = MetadataStore.for_workspace(self._workspace)
        self._registry = self._registry_with_plugins()
        self._runner = ScanRunner(self._registry)
        self._last_table_name: dict[str, str | None] = {}
        self._ensure_gitignore()

    def _registry_with_plugins(self) -> DetectorRegistry:
        """内置 + 目录插件（Step 31）+ entry points 插件（Step 50，V2-C）。

        目录插件保持 fail-fast（ADR-031 语义）；entry points 插件优雅降级，
        失败项经 `list_plugins()` 可观测（ADR-050）。Step 83（ADR-083）：
        目录插件 import 前做完整性校验——无锁条目自动建锁（锁定当前
        内容，覆盖本功能之前的旧插件）；被篡改插件跳过加载并记入
        `_plugin_errors`（校验失败仅限该插件，不影响内置与其他插件）。
        """
        from datasentry_core.plugin_locks import (
            PluginLocks,
            build_lock,
            integrity_report,
        )
        from datasentry_core.plugins import (
            DETECTOR_ENTRY_POINT_GROUP,
            discover_entrypoint_detectors,
            load_plugin_detectors_excluding,
        )

        registry = DetectorRegistry()
        register_default_detectors(registry)
        self._source_map = {d.detector_id: "builtin" for d in registry.list()}
        self._plugin_errors: list[dict[str, str]] = []
        plugins_root = self._workspace / "plugins"
        self._locks = PluginLocks.from_file(self._locks_path())
        report = integrity_report(plugins_root, self._locks)
        for entry in report.entries:
            if entry.status == "no_lock":
                self._locks.set_plugin(entry.name, build_lock(plugins_root / entry.name))
            elif entry.status == "tampered":
                self._plugin_errors.append(
                    {
                        "name": entry.name,
                        "error": (
                            f"integrity check failed: {entry.detail}; re-run install "
                            f"or use `plugin reaccept {entry.name}` after confirming "
                            f"the current content"
                        ),
                    }
                )
        if report.entries:
            self._locks.to_file(self._locks_path())
        skip = {e.name for e in report.tampered()}
        for detector_id in load_plugin_detectors_excluding(registry, [plugins_root], skip):
            self._source_map[detector_id] = "dir"
        ep_report = discover_entrypoint_detectors(registry, group=DETECTOR_ENTRY_POINT_GROUP)
        for detector_id in ep_report.loaded:
            self._source_map[detector_id] = "entrypoint"
        self._plugin_errors.extend(
            {"name": err.name, "error": err.message} for err in ep_report.errors
        )
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

    @property
    def profiles_dir(self) -> Path:
        """画像 sidecar 目录：<workspace>/.datasentry/profiles（Step 61，ADR-061）。"""
        from datasentry_core.storage.paths import project_profiles_dir

        return project_profiles_dir(self._workspace)

    @property
    def replay_dir(self) -> Path:
        """评分重放差异表目录：<workspace>/.datasentry/replay（AC-11a）。"""
        from datasentry_core.storage.paths import project_replay_dir

        return project_replay_dir(self._workspace)

    @property
    def rescan_dir(self) -> Path:
        """重扫差异表目录：<workspace>/.datasentry/rescan（AC-11b）。"""
        from datasentry_core.storage.paths import project_rescan_dir

        return project_rescan_dir(self._workspace)

    @property
    def fixtures_dir(self) -> Path:
        """重扫留存目录：<workspace>/.datasentry/fixtures（AC-11b 的源数据副本）。"""
        from datasentry_core.storage.paths import project_fixtures_dir

        return project_fixtures_dir(self._workspace)

    def load_profile(self, scan_run_id: str) -> dict[str, Any] | None:
        """按 run_id 读扫描期画像 sidecar（ADR-061）；缺失/损坏返回 None。

        画像为显示侧增值数据：缺 sidecar 时 HTML 报告不渲染 Column Profiles
        节，JSON 报告契约不受影响。
        """
        path = self.profiles_dir / f"{scan_run_id}.json"
        try:
            return cast(dict[str, Any] | None, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def _save_profile(self, scan_run: ScanRun, handle: DataHandle) -> None:
        """扫描期画像 sidecar：单条 SQL 聚合下推（Profiler），全源可用。

        不落元数据库（26 章契约与审计面不动），仅供 HTML Column Profiles 节
        消费；画像为增值信息，无列/聚合异常时静默跳过，扫描结果不受影响。
        Step 80（ADR-080）：schema 列集合（名+物理类型）与上次 completed
        画像一致 → 数据可能变更，全量画像（画像必变，不优化）；有增/删/改
        列 → 未变列复用上次 sidecar，仅重算新增/变更列（绝不引入漏检：
        数据行变更仍全量；列改名视为删+增）。
        """
        from datasentry_core.engine.profiler import Profiler

        try:
            reuse = self._column_reuse_candidates(
                scan_run.dataset_id, handle, scan_run_id=scan_run.id
            )
            profile = Profiler(handle, dataset_id=scan_run.dataset_id).profile(
                row_count=scan_run.fingerprint.row_count,
                reuse=reuse,
            )
        except (ValueError, AssertionError):
            return
        path = self.profiles_dir / f"{scan_run.id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _column_reuse_candidates(
        self, dataset_id: str, handle: DataHandle, scan_run_id: str | None = None
    ) -> dict[str, ColumnProfile] | None:
        """Step 80（ADR-080）：上次 completed 画像 sidecar 的列级复用候选。

        返回 None = 全量画像（无上次 sidecar / sidecar 损坏 / 列集合一致
        ——数据可能变，画像必变）；否则返回同名同物理类型的交集列
        （增/删/改列不在其中，将重算）。scan_run_id 排除当前 run
        （已落库 completed 但 sidecar 尚未写入）。
        """
        for scan in self._store.list_scan_runs():
            if (
                scan.id == scan_run_id
                or scan.dataset_id != dataset_id
                or scan.status != "completed"
            ):
                continue
            prev = self.load_profile(scan.id)
            if prev is None:
                return None
            prev_cols = prev.get("column_profiles") or {}
            if not prev_cols:
                return None
            current = {c.name: c.physical_type for c in handle.schema().columns}
            prev_sig = {name: cp.get("physical_type") for name, cp in prev_cols.items()}
            if current == prev_sig:
                return None
            reuse: dict[str, ColumnProfile] = {}
            for name, ptype in current.items():
                cp = prev_cols.get(name)
                if cp is not None and cp.get("physical_type") == ptype:
                    reuse[name] = ColumnProfile.model_validate(cp)
            return reuse or None
        return None

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
        """插件清单（Step 50/82）：已加载插件 + 清单字段 + 失败项。

        Step 82（ADR-082）：`manifests` 为目录插件的清单级视图
        （name/version/author/license/description）；检测器级列表保持
        Step 50 结构（source: dir/entrypoint）。清单非法时记录错误，
        不中断列表。
        """
        from datasentry_core.plugins import read_plugin_manifests

        manifests = {}
        try:
            manifests = read_plugin_manifests([self._workspace / "plugins"])
        except ValueError as exc:
            self._plugin_errors.append({"name": "manifest", "error": str(exc)})
        integrity = self._plugin_integrity_status()
        plugins = [d for d in self.list_detectors() if d["source"] in ("dir", "entrypoint")]
        return {
            "plugins": plugins,
            "manifests": [
                {
                    "name": m.name,
                    "version": m.version,
                    "author": m.author,
                    "license": m.license,
                    "description": m.description,
                    "integrity": integrity.get(m.name, "unknown"),
                }
                for m in manifests.values()
            ],
            "errors": self._plugin_errors,
            "workspace_plugins_dir": str(self._workspace / "plugins"),
        }

    def _locks_path(self) -> Path:
        return self._workspace / ".datasentry" / "plugin_locks.json"

    def _plugin_integrity_status(self) -> dict[str, str]:
        """插件完整性状态（Step 83，ADR-083）：name → ok/tampered/no_lock。"""
        from datasentry_core.plugin_locks import PluginLocks, integrity_report

        locks = PluginLocks.from_file(self._locks_path())
        report = integrity_report(self._workspace / "plugins", locks)
        return {e.name: e.status for e in report.entries}

    def plugins_dir(self) -> Path:
        """工作区插件目录（ADR-031）：<workspace>/plugins（不存在时创建）。"""
        path = self._workspace / "plugins"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def install_plugin(self, source: str | Path) -> dict[str, Any]:
        """安装插件（Step 82，ADR-082）：文件或目录 → workspace/plugins/<name>/。

        - 目录：复制整个目录（含 plugin.yaml 清单，无清单生成占位）
        - 单 .py 文件：安装为 <name>/<file>.py + 生成占位清单
        - 同名已存在 → PluginManifestError（需先 uninstall）
        返回 {"name", "version", "installed_to", "files"}。
        """
        import shutil

        from datasentry_core.plugin_locks import build_lock
        from datasentry_core.plugins import (
            PLUGIN_MANIFEST_FILE,
            PluginManifest,
            PluginManifestError,
        )

        src = Path(source).expanduser()
        if not src.exists():
            raise FileNotFoundError(f"plugin source not found: {src}")
        if src.is_dir():
            manifest_path = src / PLUGIN_MANIFEST_FILE
            if manifest_path.is_file():
                manifest = PluginManifest.from_file(manifest_path)
            else:
                manifest = PluginManifest(
                    name=src.name,
                    version="0.1.0",
                    description=f"installed from {src}",
                )
            target = self.plugins_dir() / manifest.name
            if target.exists():
                raise PluginManifestError(
                    f"plugin {manifest.name!r} already installed at {target} (uninstall first)"
                )
            files = [p for p in src.rglob("*") if p.is_file()]
            shutil.copytree(src, target)
            if not (target / PLUGIN_MANIFEST_FILE).is_file():
                _write_placeholder_manifest(target, manifest)
        else:
            if src.suffix != ".py":
                raise PluginManifestError(f"plugin file must be .py: {src}")
            manifest = PluginManifest(
                name=src.stem,
                version="0.1.0",
                description=f"installed from {src}",
            )
            target = self.plugins_dir() / manifest.name
            if target.exists():
                raise PluginManifestError(
                    f"plugin {manifest.name!r} already installed at {target} (uninstall first)"
                )
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target / src.name)
            files = [target / src.name]
            _write_placeholder_manifest(target, manifest)
        self._locks.set_plugin(manifest.name, build_lock(target, version=manifest.version))
        self._locks.to_file(self._locks_path())
        return {
            "name": manifest.name,
            "version": manifest.version,
            "installed_to": str(target),
            "files": [str(f) for f in files],
        }

    def uninstall_plugin(self, name: str) -> dict[str, Any]:
        """卸载插件（Step 82/83）：删除 <workspace>/plugins/<name>/ + 锁条目。

        未知插件抛 FileNotFoundError（未安装）。
        """
        import shutil

        from datasentry_core.plugin_locks import PluginLocks

        target = self._workspace / "plugins" / name
        if not target.is_dir():
            raise FileNotFoundError(f"plugin {name!r} not installed at {target}")
        shutil.rmtree(target)
        locks = PluginLocks.from_file(self._locks_path())
        locks.remove_plugin(name)
        locks.to_file(self._locks_path())
        return {"name": name, "removed": str(target)}

    def reaccept_plugin(self, name: str) -> dict[str, Any]:
        """重新锁定插件当前内容（Step 83，ADR-083）：用户确认后放行。

        完整性校验失败（tampered）的插件在 `plugin reaccept <name>`
        后按当前内容重新建锁，下次加载通过。
        """
        from datasentry_core.plugin_locks import build_lock

        target = self._workspace / "plugins" / name
        if not target.is_dir():
            raise FileNotFoundError(f"plugin {name!r} not installed at {target}")
        lock = build_lock(target)
        self._locks.set_plugin(name, lock)
        self._locks.to_file(self._locks_path())
        return {"name": name, "version": lock.version, "relocked": True}

    def test_plugin(self, name: str) -> dict[str, Any]:
        """插件测试夹具（Step 84，ADR-084）：隔离注册表跑 manifest fixtures。

        无 fixtures 声明 → {"skipped": true}；任一 fixture 失败 →
        结果含明细（调用方按 passed 决定退出码）。插件未安装抛
        FileNotFoundError。
        """
        from datasentry_core.plugin_fixtures import run_plugin_fixtures
        from datasentry_core.plugins import PluginManifest

        plugins_root = self._workspace / "plugins"
        unit = next((u for u in _plugin_units(plugins_root) if u[0] == name), None)
        if unit is None:
            raise FileNotFoundError(f"plugin {name!r} not installed at {plugins_root}")
        _, plugin_root = unit
        manifest_path = plugin_root / "plugin.yaml"
        fixtures = []
        if manifest_path.is_file():
            fixtures = PluginManifest.from_file(manifest_path).fixtures
        report = run_plugin_fixtures(name, plugins_root, fixtures)
        return {
            "name": report.name,
            "passed": report.passed(),
            "results": [
                {
                    "data": r.data,
                    "passed": r.passed,
                    "issue_count": r.issue_count,
                    "detector": r.detector_id,
                    "dimension": r.dimension,
                    "detail": r.detail,
                }
                for r in report.results
            ],
            "skipped": report.skipped,
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
        incremental: bool = False,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[ScanRun, list[DetectorRun], list[Issue]]:
        """导入 + 扫描 + 评分 + 落库（数据源不可用抛 FileNotFoundError 类异常）。

        table_name：DuckDB/SQLite/PostgreSQL/MySQL 必填（Step 38/54/55/56）；其他格式忽略。
        path 传 postgresql://、postgres:// 或 mysql:// URL 时按对应远程数据库
        数据源处理（Step 55/56，V4/V5）：凭据经 spec.options["dsn"] 内存态
        流转，不落库/日志/报告。
        references：契约跨表引用（Step 40），触发外键完整性检测。
        incremental（Step 77，ADR-077）：本地文件源指纹（文件 SHA-256，
        与调度器 Step 53 同源）比对最近一次完成扫描——未变更直接复用
        上次 scan_run/检测器运行/Issue（画像随 scan_run_id 自然复用，
        不建新 run）；无基准（远程源、抽样扫描 sampled 档指纹、首次
        扫描）或指纹计算失败 → 降级全量扫描（绝不误跳过）。默认
        False 行为与旧版完全一致。
        """
        if self._enforce_scan_containment:
            from datasentry.scan_paths import resolve_allowed_scan_path

            # Use the validated string. Discarding it meant the gate checked `workspace/x.csv`
            # while this method went on to open `x.csv` against the process CWD -- the same
            # wrong-base defect r5/A-1 closed on the REST face (A-10 review #2). Remote URIs come
            # back unchanged, so the DSN and cloud branches below still match.
            path = resolve_allowed_scan_path(str(path), workspace=self._workspace)
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
        if incremental:
            cached = self._incremental_cached(path, dataset_id, table_name)
            if cached is not None:
                return cached
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
            scan_run, runs, issues = self._runner.run_scan(context, config, on_progress)
            self._store.save_scan(scan_run, runs, issues)
            self._save_profile(scan_run, handle)
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

    def _incremental_cached(
        self,
        path: str | Path,
        dataset_id: str | None,
        table_name: str | None,
    ) -> tuple[ScanRun, list[DetectorRun], list[Issue]] | None:
        """增量画像（Step 77，ADR-077）：本地文件源指纹比对最近完成扫描。

        基准缺失（远程 DSN/URI、首次扫描、上次为抽样 sampled 档指纹或
        异常扫描）或指纹计算失败 → 返回 None 降级全量扫描（绝不误跳过）；
        未变更 → 返回上次 scan_run + 检测器运行 + Issue（不建新 run，
        画像按 scan_run_id 自然复用）。
        """
        if isinstance(path, str) and (
            path.startswith(("postgresql://", "postgres://", "mysql://", "s3://", "gs://", "az://"))
        ):
            return None
        source_path = Path(path).expanduser()
        if not source_path.is_file():
            return None
        previous = None
        for scan in self._store.list_scan_runs():
            if (
                scan.dataset_id == dataset_id
                and scan.status == "completed"
                and scan.fingerprint.file_sha256
            ):
                previous = scan
                break
        if previous is None:
            return None
        from datasentry.scheduler.core import _source_fingerprint
        from datasentry.scheduler.models import JobCommand

        try:
            current = _source_fingerprint(
                JobCommand(
                    project=str(self.workspace),
                    path=str(source_path),
                    dataset_id=dataset_id,
                    table_name=table_name,
                ),
                None,
            )
        except OSError:
            return None
        if current is None or current != previous.fingerprint.file_sha256:
            return None
        return (
            previous,
            self.get_detector_runs(previous.id),
            self._store.get_issues(previous.id),
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

    # ---- PII vault 门面（D1-01：三端经 client 取 vault/映射，不碰 _store） ----

    def pii_vault(self) -> PIIVault:
        """绑定本工作区元数据库的 PII vault（与 CLI/API/MCP 同源）。"""
        from datasentry.pii_vault import PIIVault

        return PIIVault(self._store)

    def list_pii_mappings(self, limit: int = 100) -> list[dict[str, Any]]:
        """PII 会话映射列表（只读视图行，不过密文）。"""
        return self._store.list_pii_mappings(limit=limit)

    def count_pii_mappings(self) -> int:
        """PII 会话映射计数。"""
        return self._store.count_pii_mappings()

    def delete_pii_mapping(self, session_id: str) -> bool:
        """删除 PII 会话映射（只删密文行不解密）；不存在返回 False。"""
        return self._store.delete_pii_mapping(session_id)

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

    def replay_scores(
        self, *, run_ids: list[str] | None = None, limit: int | None = None
    ) -> ReplayReport:
        """AC-11a：用当前评分代码重放历史 run 的 issue 快照, 返回差异表。

        只重放评分, 不重放检测（store 存的是 issue 快照, 不是源数据行）。未评分 run
        进 ``skipped`` 并带原因; 历史分数只读, 不会被改写。
        """
        from datasentry_core.scoring.replay import replay_runs

        return replay_runs(self._store, run_ids=run_ids, limit=limit)

    def rescan_runs(
        self, *, run_ids: list[str] | None = None, limit: int | None = None
    ) -> RescanReport:
        """AC-11b：重扫历史 run 的本地 fixture, 返回 issue 集差异表。

        与 ``replay_scores`` 互补：那个只能重算分数, 这个能复核检测器变更。重扫在内存中完成
        （``run_scan`` 的返回值不落库）, 历史 run 只读。源优先取 ``fixtures capture`` 留存的
        副本, 其次 run 记录的原始路径；两者都不可用才记 ``uncovered``, 不会变成"无差异"。
        """
        runs = self._select_runs(run_ids=run_ids, limit=limit)
        rows = []
        for run in runs:
            status, note, rescanned, source_kind = self._rescan_one(run)
            stored = self._store.get_issues(run.id) if status == "compared" else []
            rows.append(
                diff_issue_sets(
                    run.id, stored, rescanned, status=status, note=note, source_kind=source_kind
                )
            )
        return RescanReport(rows=tuple(rows))

    def capture_fixtures(
        self,
        *,
        run_ids: list[str] | None = None,
        limit: int | None = None,
        max_bytes: int | None = DEFAULT_MAX_BYTES,
        overwrite: bool = False,
    ) -> CaptureReport:
        """AC-11b 的前提：把历史 run 的源数据副本留存进工作区。

        复制前先按 run 的指纹档位重算并比对——源已被改写时拒绝留存，否则存下的副本会让后续
        "检测器漂移"的结论混入"数据变了"。签名与 ``rescan_runs`` 一致，便于同一批 run 先
        capture 再 rescan。
        """
        runs = self._select_runs(run_ids=run_ids, limit=limit)
        store = self._fixture_store()
        rows: list[CaptureRow] = []
        for run in runs:
            existing = store.get(run.id)
            if existing is not None and not overwrite:
                rows.append(
                    CaptureRow(
                        run_id=run.id,
                        status="already",
                        reason=f"已留存副本 {existing.sha256[:12]} ({existing.captured_at})",
                        size_bytes=existing.size_bytes,
                        sha256=existing.sha256,
                    )
                )
                continue
            tampered = existing is not None and not store.verify(existing)
            status, note, path, _kind = self._resolve_source(run, prefer_fixture=False)
            if path is None:
                rows.append(CaptureRow(run_id=run.id, status="skipped", reason=note or status))
                continue
            verified, verify_note, handle = self._open_verified(run, path)
            if handle is None:
                reason = f"{verified}: {verify_note}" if verify_note else verified
                rows.append(CaptureRow(run_id=run.id, status="skipped", reason=reason))
                continue
            handle.close()
            try:
                entry, created = store.capture(
                    run_id=run.id,
                    source=path,
                    source_path=run.source_path or str(path),
                    fingerprint_type=run.fingerprint.fingerprint_type,
                    max_bytes=max_bytes,
                    overwrite=overwrite,
                )
            except (OSError, ValueError) as exc:
                rows.append(
                    CaptureRow(
                        run_id=run.id, status="failed", reason=f"{type(exc).__name__}: {exc}"
                    )
                )
                continue
            # "captured" 指这次真的产生了新副本：新建目录、换掉不同字节，或重建被改坏的副本。
            replaced = existing is not None and existing.sha256 != entry.sha256
            if created or replaced or tampered:
                rows.append(
                    CaptureRow(
                        run_id=run.id,
                        status="captured",
                        reason="副本已损坏, 已按原始文件重建" if tampered else "",
                        size_bytes=entry.size_bytes,
                        sha256=entry.sha256,
                    )
                )
                continue
            rows.append(
                CaptureRow(
                    run_id=run.id,
                    status="already",
                    reason="副本内容与既有留存一致",
                    size_bytes=entry.size_bytes,
                    sha256=entry.sha256,
                )
            )
        return CaptureReport(rows=tuple(rows))

    def fixture_entries(self) -> list[FixtureEntry]:
        """已留存的副本清单，按留存时间升序（``fixtures list`` 用）。"""
        return self._fixture_store().entries()

    def fixture_orphans(self) -> list[str]:
        """认不出的 fixture 目录：有目录无清单（崩溃残留），或名字不合白名单。"""
        return self._fixture_store().orphan_dirs()

    def prune_fixtures(
        self, *, keep: int | None = None, max_bytes: int | None = None, dry_run: bool = False
    ) -> list[FixtureEntry]:
        """删除超出保留策略的副本，返回被删清单项（``dry_run`` 只算不删）。"""
        return self._fixture_store().prune(keep=keep, max_bytes=max_bytes, dry_run=dry_run)

    def _fixture_store(self) -> FixtureStore:
        from datasentry_core.storage.paths import project_fixtures_dir

        return FixtureStore(project_fixtures_dir(self._workspace))

    def _select_runs(self, *, run_ids: list[str] | None, limit: int | None) -> list[ScanRun]:
        """选中要复核的 run：显式 id 优先，否则取最近的 ``limit`` 条。"""
        if limit is not None and limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        if run_ids is not None:
            runs: list[ScanRun] = []
            for run_id in run_ids:
                run = self._store.get_scan_run(run_id)
                if run is None:
                    raise KeyError(f"scan run not found: {run_id}")
                runs.append(run)
            return runs
        runs = self._store.list_scan_runs()
        return runs[:limit] if limit is not None else runs

    def _resolve_source(
        self, run: ScanRun, *, prefer_fixture: bool = True
    ) -> tuple[RescanStatus, str, Path | None, SourceKind]:
        """定位可重扫的源：默认留存副本优先，其次原始路径。返回 (状态, 原因, 路径, 来源)。

        副本优先的理由是稳定性：原始路径可能已被移动或改写，而副本的内容由清单的 sha256
        固定。两者都没有时返回 ``None`` 路径，由调用方计入 uncovered。``prefer_fixture=False``
        只给 ``capture`` 用——修复一份被改坏的副本必须从原始路径取样。
        """
        if run.status != "completed":
            return (
                "run_incomplete",
                f"历史 run 状态为 {run.status}, 其 issue 集不是完整基线",
                None,
                "none",
            )
        source = run.source_path
        if not source:
            return ("source_unsupported", "run 未记录源路径, 无法定位 fixture", None, "none")
        if source.startswith(_REMOTE_SOURCE_PREFIXES):
            return (
                "source_unsupported",
                "远程源重扫会打到活库或对象存储, 不在本工具范围",
                None,
                "none",
            )
        if prefer_fixture:
            store = self._fixture_store()
            try:
                entry = store.get(run.id)
                retained = store.path_for(entry) if entry is not None else None
            except ValueError as exc:
                # 清单损坏不该让整批崩掉，但也不能当成"没有副本"：记 scan_failed 并入 uncovered。
                return ("scan_failed", f"留存清单不可用: {exc}", None, "none")
            if retained is not None and retained.is_file():
                return ("compared", "", retained, "fixture")
        path = Path(source).expanduser()
        if not path.is_file():
            return ("source_missing", f"原始文件与留存副本都不可用: {path}", None, "none")
        return ("compared", "", path, "origin")

    def _open_verified(
        self, run: ScanRun, path: Path
    ) -> tuple[RescanStatus, str, DataHandle | None]:
        """打开 ``path`` 并按 run 的指纹档位重算；句柄非空表示指纹与存档一致。

        桩函数风格的返回便于两个调用方共用：``rescan`` 需要句柄继续跑检测器，``capture``
        只需要这个判断然后立刻关掉句柄。
        """
        source_type = _source_type_for_path(path)
        if source_type is None:
            return "source_unsupported", f"未知数据源格式: {path.suffix or '(none)'}", None
        if source_type in _TABLE_SCOPED_SOURCES:
            return (
                "source_unsupported",
                f"{source_type.value} 源按表扫描, store 未持久化 table_name, 无法定位",
                None,
            )
        spec = DataSourceSpec(
            source_type=source_type,
            path=path,
            table_name=None,
            options={"dataset_id": run.dataset_id},
        )
        try:
            handle = default_registry().open(spec)
        except (OSError, ValueError, KeyError) as exc:
            return "source_unsupported", f"无法打开数据源: {type(exc).__name__}: {exc}", None
        try:
            fresh = handle.fingerprint(mode=run.fingerprint.fingerprint_type)
            changed = _fingerprint_change(run, fresh)
        except (OSError, ValueError, KeyError) as exc:
            handle.close()
            return "scan_failed", f"读取指纹失败: {type(exc).__name__}: {exc}", None
        if changed:
            handle.close()
            return "source_changed", changed, None
        return "compared", "", handle

    def _rescan_one(self, run: ScanRun) -> tuple[RescanStatus, str, list[Issue], SourceKind]:
        """重扫单条 run 并返回 (状态, 原因, 新 issue 集, 源类型)。

        只有"源可用 + 指纹未变 + 重扫成功"三者同时成立才返回 ``compared``; 其余一律给出具体
        原因, 由调用方计入 uncovered。
        """
        status, note, path, source_kind = self._resolve_source(run)
        if path is None:
            return status, note, [], source_kind
        verified, verify_note, handle = self._open_verified(run, path)
        if handle is None:
            return verified, verify_note, [], source_kind
        try:
            try:
                context = DetectionContext(
                    dataset_id=run.dataset_id,
                    table_name=None,
                    columns=handle.schema().column_names,
                    handle=handle,
                    config=run.config,
                )
                _scan, _runs, issues = self._runner.run_scan(context, run.config)
            except (OSError, ValueError, KeyError) as exc:
                return "scan_failed", f"重扫失败: {type(exc).__name__}: {exc}", [], source_kind
            return "compared", "", issues, source_kind
        finally:
            handle.close()

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
            run = engine.apply(
                proposal,
                context,
                workspace or self.workspace,
                source_scan_run_id=issue.scan_run_id,
            )
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

    def get_repair_run(self, run_id: str) -> RepairRun | None:
        return self._store.get_repair_run(run_id)

    @staticmethod
    def _repaired_copy_path(run: RepairRun) -> Path:
        if run.rollback_artifact is None:
            raise ValueError("no rollback artifact")
        artifact = Path(run.rollback_artifact)
        return artifact.with_name(artifact.name.replace(".before", ""))

    def repair_diff(
        self, run_id: str
    ) -> tuple[RepairRun, list[str], list[list[object]], list[list[object]], list[int]]:
        """V43：修复工件 diff——before 快照 vs 修复副本（列、行、变更索引）。"""
        run = self._store.get_repair_run(run_id)
        if run is None:
            raise KeyError(f"repair run not found: {run_id}")
        artifact = Path(run.rollback_artifact) if run.rollback_artifact else None
        if artifact is None:
            raise ValueError("no rollback artifact")
        copy_path = self._repaired_copy_path(run)
        if not copy_path.exists():
            raise FileNotFoundError(f"repaired copy missing: {copy_path}")
        source_type = _source_type_for_path(copy_path)
        if source_type is None:
            raise ValueError(f"unsupported repaired copy format: {copy_path.suffix}")
        columns, before_rows, after_rows, changed = RepairEngine.table_diff(
            artifact, copy_path, source_type
        )
        return run, columns, before_rows, after_rows, changed

    def repair_verify(self, run_id: str) -> tuple[ScanRun, RepairVerifyReport]:
        """V41：验证闭环——重扫修复副本，返回 (新 scan run, 对比报告)。

        报告键：source_scan_run_id / verify_scan_run_id / source_issue_count
        / verify_issue_count / fixed_types / persistent_types / new_types。
        """
        run = self._store.get_repair_run(run_id)
        if run is None:
            raise KeyError(f"repair run not found: {run_id}")
        if run.source_scan_run_id is None:
            raise ValueError("repair has no source scan context (created before v0.46)")
        copy_path = self._repaired_copy_path(run)
        if not copy_path.exists():
            raise FileNotFoundError(f"repaired copy missing: {copy_path}")
        scan, _, _ = self.scan_file(copy_path)
        source_issues = self._store.get_issues(run.source_scan_run_id)
        verify_issues = self._store.get_issues(scan.id)
        source_types = {i.issue_type for i in source_issues}
        verify_types = {i.issue_type for i in verify_issues}
        return scan, {
            "source_scan_run_id": run.source_scan_run_id,
            "verify_scan_run_id": scan.id,
            "source_issue_count": len(source_issues),
            "verify_issue_count": len(verify_issues),
            "fixed_types": sorted(source_types - verify_types),
            "persistent_types": sorted(source_types & verify_types),
            "new_types": sorted(verify_types - source_types),
        }

    def list_llm_invocations(self, limit: int = 20) -> list[LLMInvocation]:
        """最近 LLM 调用审计（13.11；不含 prompt 原文）。"""
        return self._store.list_llm_invocations(limit=limit)

    def list_rules(self) -> list[Rule]:
        """已落库规则列表（14.1/14.4）。"""
        return self._store.list_rules()

    def get_issue(self, issue_id: str) -> Issue | None:
        """按 ID 取 Issue（修复工作台等 UI 用途）。"""
        return self._store.get_issue_by_id(issue_id)


def _plugin_units(plugins_root: Path) -> list[tuple[str, Path]]:
    """插件单元（name, 路径）：复用 core 的 plugin_units。"""
    from datasentry_core.plugins import plugin_units

    return plugin_units(plugins_root)


def _write_placeholder_manifest(target: Path, manifest: Any) -> None:
    """写入占位 plugin.yaml（Step 82，ADR-082）：安装无清单来源时生成。"""
    from datasentry_core.plugins import PLUGIN_MANIFEST_FILE

    content = (
        f"name: {manifest.name}\n"
        f"version: {manifest.version}\n"
        f"author: {manifest.author}\n"
        f"license: {manifest.license}\n"
        f"description: {manifest.description}\n"
    )
    (target / PLUGIN_MANIFEST_FILE).write_text(content, encoding="utf-8")
