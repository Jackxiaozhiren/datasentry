"""插件测试夹具（Step 84，ADR-084）：`plugin test` 的断言执行器。

- 插件 manifest 可声明 `fixtures:`（data 相对插件根 + expect：
  detector 过滤 / issues 数量阈值 / dimension 维度），复用扫描管线
  （CSV 连接器 + ScanRunner）跑真实检测流程。
- 隔离：fixture 扫描用独立注册表（内置 + 该插件），不污染
  DataSentry 主注册表、不落库。
- fail-fast：逐 fixture 断言，任一失败即返回（结果含明细）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datasentry_core.connectors import ConnectorError, default_registry
from datasentry_core.connectors.spec import EXT_TO_SOURCE_TYPE, DataSourceSpec
from datasentry_core.detectors import DetectionContext, DetectorRegistry
from datasentry_core.detectors.initial import register_default_detectors
from datasentry_core.detectors.runner import ScanRunner
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import ScanConfig
from datasentry_core.plugins import FixtureSpec, load_plugin_detectors_excluding


@dataclass(frozen=True)
class FixtureResult:
    """单个 fixture 的断言结果。"""

    data: str
    passed: bool
    issue_count: int = 0
    detector_id: str | None = None
    dimension: str | None = None
    detail: str = ""


@dataclass
class PluginTestReport:
    """插件测试报告：全通过 / 明细 / 跳过原因。"""

    name: str
    results: list[FixtureResult] = field(default_factory=list)
    skipped: str | None = None

    def passed(self) -> bool:
        if self.skipped is not None:
            return True
        return all(r.passed for r in self.results)


def _evaluate_expectation(
    fixture: FixtureSpec, issues: list[Issue], data_rel: str
) -> FixtureResult:
    expect = fixture.expect
    filtered = [i for i in issues if expect.detector is None or expect.detector in i.detector_ids]
    count = len(filtered)
    dimension_hit = None
    for issue in filtered:
        dims = [d.value for d in issue.quality_dimensions]
        if expect.dimension is None or expect.dimension in dims:
            dimension_hit = issue.quality_dimensions[0].value if issue.quality_dimensions else None
            break
    problems: list[str] = []
    if count < expect.issues:
        problems.append(f"expected >= {expect.issues} issues, got {count}")
    if expect.dimension is not None and dimension_hit is None:
        found = sorted({d for i in filtered for d in (x.value for x in i.quality_dimensions)})
        problems.append(f"expected dimension {expect.dimension}, got none of {found}")
    passed = not problems
    detector = expect.detector
    if detector is None and filtered:
        detector = filtered[0].detector_ids[0] if filtered[0].detector_ids else None
    return FixtureResult(
        data=data_rel,
        passed=passed,
        issue_count=count,
        detector_id=detector,
        dimension=dimension_hit,
        detail="; ".join(problems),
    )


def _open_handle(data_path: Path, options: dict[str, Any] | None = None) -> Any:
    source_type = EXT_TO_SOURCE_TYPE.get(data_path.suffix.lower())
    if source_type is None:
        raise ValueError(f"unsupported fixture data format: {data_path.suffix}")
    spec = DataSourceSpec(
        source_type=source_type,
        path=data_path,
        options=options or {"dataset_id": data_path.stem},
    )
    return default_registry().open(spec)


def run_plugin_fixtures(
    plugin_name: str,
    plugins_root: Path,
    fixtures: list[FixtureSpec],
) -> PluginTestReport:
    """对插件运行声明夹具（隔离注册表：内置 + 该插件单元）。

    返回全量结果（不抛异常）；fixture 文件缺失/打开失败记为该
    fixture 失败（含明细），不影响其他 fixture。
    """
    if not fixtures:
        return PluginTestReport(name=plugin_name, skipped="no fixtures declared in plugin.yaml")
    registry = DetectorRegistry()
    register_default_detectors(registry)
    other_names = {name for name, _ in plugin_units_for(plugins_root)} - {plugin_name}
    load_plugin_detectors_excluding(registry, [plugins_root], exclude=other_names)
    scan_run_id = f"plugin-test-{plugin_name}-{uuid.uuid4().hex[:8]}"
    results: list[FixtureResult] = []
    plugin_root = next(
        (root for name, root in plugin_units_for(plugins_root) if name == plugin_name),
        plugins_root,
    )
    for fixture in fixtures:
        data_path = plugin_root / fixture.data
        try:
            handle = _open_handle(data_path)
        except (OSError, ValueError, ConnectorError) as exc:
            results.append(
                FixtureResult(data=fixture.data, passed=False, detail=f"cannot open: {exc}")
            )
            continue
        try:
            context = DetectionContext(
                dataset_id=plugin_name,
                table_name=None,
                columns=handle.schema().column_names,
                handle=handle,
                config=ScanConfig(),
            )
            _, issues = ScanRunner(registry).run(context, ScanConfig(), scan_run_id)
        finally:
            handle.close()
        results.append(_evaluate_expectation(fixture, issues, fixture.data))
    return PluginTestReport(name=plugin_name, results=results)


def plugin_units_for(plugins_root: Path) -> list[tuple[str, Path]]:
    """插件单元（复用 plugins.plugin_units，隔离导入）。"""
    from datasentry_core.plugins import plugin_units

    return plugin_units(plugins_root)


__all__ = [
    "FixtureResult",
    "PluginTestReport",
    "plugin_units_for",
    "run_plugin_fixtures",
]
