"""Step 84（ADR-084）测试：插件测试夹具（manifest fixtures + 隔离注册表断言）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry_core.plugin_fixtures import run_plugin_fixtures
from datasentry_core.plugins import (
    FixtureExpectation,
    FixtureSpec,
    PluginManifest,
    PluginManifestError,
)

_PLUGIN_SRC = """\
from typing import ClassVar
from datasentry_core.detectors.base import DetectionContext
from datasentry_core.models.detector import DetectorMeta, IssueCandidate
from datasentry_core.models.enums import QualityDimension

class FixturePluginDetector:
    detector_id: ClassVar[str] = "plugin_fixture"
    detector_version: ClassVar[str] = "1.0.0"
    quality_dimension: ClassVar[QualityDimension] = QualityDimension.VALIDITY

    def supports(self, context: DetectionContext) -> bool:
        return "name" in context.columns

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        batch = context.handle.sql_aggregate(
            "SELECT count(*) AS n FROM data WHERE name IS NULL OR name = ''", {}
        )
        n = int(batch.table.column("n").to_pylist()[0])
        if n == 0:
            return []
        return [IssueCandidate(
            detector_id=self.detector_id,
            detector_version=self.detector_version,
            dataset_id=context.dataset_id,
            issue_type="empty_value",
            title="empty name",
            description="name is empty",
            quality_dimensions=[QualityDimension.COMPLETENESS],
            columns=["name"],
            affected_count=n,
            raw_score=1.0,
            confidence=1.0,
            estimated_false_positive_risk=0.0,
            suggested_severity="medium",
        )]

    def metadata(self) -> DetectorMeta:
        return DetectorMeta(
            detector_id=self.detector_id,
            display_name="Fixture Plugin",
            description="plugin with fixtures for tests",
            quality_dimension=self.quality_dimension,
        )
"""

_GOOD_CSV = "name,age\nalice,30\nbob,25\n"
_BAD_CSV = "name,age\n,30\nbob,\n"


@pytest.fixture()
def plugin_root(tmp_path: Path) -> Path:
    root = tmp_path / "fixture_plugin"
    root.mkdir()
    (root / "detector.py").write_text(_PLUGIN_SRC, encoding="utf-8")
    (root / "good.csv").write_text(_GOOD_CSV, encoding="utf-8")
    (root / "bad.csv").write_text(_BAD_CSV, encoding="utf-8")
    return root


def _install(project: Path, root: Path) -> DataSentry:
    client = DataSentry(project)
    client.install_plugin(root)
    return client


# ---- manifest fixtures 解析 -------------------------------------------


def test_manifest_parses_fixtures(tmp_path: Path) -> None:
    p = tmp_path / "plugin.yaml"
    p.write_text(
        "name: demo\nversion: 1.0.0\nfixtures:\n"
        "  - data: bad.csv\n    expect:\n"
        "      detector: plugin_fixture\n      issues: 2\n      dimension: completeness\n",
        encoding="utf-8",
    )
    m = PluginManifest.from_file(p)
    assert m.fixtures == [
        FixtureSpec(
            data="bad.csv",
            expect=FixtureExpectation(
                detector="plugin_fixture", issues=2, dimension="completeness"
            ),
        )
    ]


def test_manifest_fixtures_defaults(tmp_path: Path) -> None:
    p = tmp_path / "plugin.yaml"
    p.write_text("name: demo\nversion: 1.0.0\nfixtures:\n  - data: bad.csv\n", encoding="utf-8")
    m = PluginManifest.from_file(p)
    assert m.fixtures == [FixtureSpec(data="bad.csv", expect=FixtureExpectation())]


def test_manifest_fixtures_missing_data_rejected(tmp_path: Path) -> None:
    p = tmp_path / "plugin.yaml"
    p.write_text(
        "name: demo\nversion: 1.0.0\nfixtures:\n  - expect:\n      issues: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(PluginManifestError, match="data"):
        PluginManifest.from_file(p)


def test_manifest_fixtures_negative_issues_rejected(tmp_path: Path) -> None:
    p = tmp_path / "plugin.yaml"
    p.write_text(
        "name: demo\nversion: 1.0.0\nfixtures:\n  - data: x.csv\n    expect:\n      issues: -1\n",
        encoding="utf-8",
    )
    with pytest.raises(PluginManifestError, match="issues"):
        PluginManifest.from_file(p)


def test_manifest_no_fixtures_empty() -> None:
    m = PluginManifest(name="a", version="1.0")
    assert m.fixtures == []


# ---- fixture 执行 -----------------------------------------------------


def test_run_fixtures_pass(plugin_root: Path, tmp_path: Path) -> None:
    client = _install(tmp_path / "proj", plugin_root)
    fixtures = [
        FixtureSpec(data="bad.csv", expect=FixtureExpectation(detector="plugin_fixture", issues=1)),
        FixtureSpec(
            data="good.csv",
            expect=FixtureExpectation(detector="plugin_fixture", issues=0),
        ),
    ]
    report = run_plugin_fixtures("fixture_plugin", tmp_path / "proj" / "plugins", fixtures)
    assert report.passed()
    assert all(r.passed for r in report.results)
    assert report.results[0].issue_count == 1
    assert report.results[0].detector_id == "plugin_fixture"
    client.close()


def test_run_fixtures_fail_on_low_count(plugin_root: Path, tmp_path: Path) -> None:
    client = _install(tmp_path / "proj", plugin_root)
    fixtures = [
        FixtureSpec(data="bad.csv", expect=FixtureExpectation(detector="plugin_fixture", issues=2))
    ]
    report = run_plugin_fixtures("fixture_plugin", tmp_path / "proj" / "plugins", fixtures)
    assert not report.passed()
    assert not report.results[0].passed
    assert "expected >= 2" in report.results[0].detail
    client.close()


def test_run_fixtures_fail_on_dimension(plugin_root: Path, tmp_path: Path) -> None:
    client = _install(tmp_path / "proj", plugin_root)
    fixtures = [
        FixtureSpec(
            data="bad.csv",
            expect=FixtureExpectation(detector="plugin_fixture", issues=1, dimension="integrity"),
        )
    ]
    report = run_plugin_fixtures("fixture_plugin", tmp_path / "proj" / "plugins", fixtures)
    assert not report.passed()
    assert "expected dimension integrity" in report.results[0].detail
    client.close()


def test_run_fixtures_missing_data_file(plugin_root: Path, tmp_path: Path) -> None:
    client = _install(tmp_path / "proj", plugin_root)
    fixtures = [FixtureSpec(data="nope.csv", expect=FixtureExpectation(issues=1))]
    report = run_plugin_fixtures("fixture_plugin", tmp_path / "proj" / "plugins", fixtures)
    assert not report.passed()
    assert "cannot open" in report.results[0].detail
    client.close()


def test_run_fixtures_skip_without_fixtures(plugin_root: Path, tmp_path: Path) -> None:
    client = _install(tmp_path / "proj", plugin_root)
    report = run_plugin_fixtures("fixture_plugin", tmp_path / "proj" / "plugins", [])
    assert report.skipped is not None
    assert report.passed()
    client.close()


# ---- client.test_plugin ------------------------------------------------


def _manifest_with_fixtures(root: Path) -> None:
    (root / "plugin.yaml").write_text(
        "name: fixture_plugin\nversion: 1.0.0\nfixtures:\n"
        "  - data: bad.csv\n    expect:\n      detector: plugin_fixture\n      issues: 1\n",
        encoding="utf-8",
    )


def test_client_test_plugin_passes(plugin_root: Path, tmp_path: Path) -> None:
    _manifest_with_fixtures(plugin_root)
    client = _install(tmp_path / "proj", plugin_root)
    result = client.test_plugin("fixture_plugin")
    assert result["passed"] is True
    assert result["results"][0]["passed"] is True
    assert result["results"][0]["issue_count"] == 1
    assert result["skipped"] is None
    client.close()


def test_client_test_plugin_fails(plugin_root: Path, tmp_path: Path) -> None:
    (plugin_root / "plugin.yaml").write_text(
        "name: fixture_plugin\nversion: 1.0.0\nfixtures:\n"
        "  - data: good.csv\n    expect:\n      detector: plugin_fixture\n      issues: 1\n",
        encoding="utf-8",
    )
    client = _install(tmp_path / "proj", plugin_root)
    result = client.test_plugin("fixture_plugin")
    assert result["passed"] is False
    assert result["results"][0]["passed"] is False
    client.close()


def test_client_test_plugin_skipped(plugin_root: Path, tmp_path: Path) -> None:
    client = _install(tmp_path / "proj", plugin_root)
    result = client.test_plugin("fixture_plugin")
    assert result["passed"] is True
    assert "no fixtures" in result["skipped"]
    client.close()


def test_client_test_plugin_not_installed(tmp_path: Path) -> None:
    client = DataSentry(tmp_path / "proj")
    with pytest.raises(FileNotFoundError, match="not installed"):
        client.test_plugin("ghost")
    client.close()


def test_test_plugin_does_not_pollute_main_registry(plugin_root: Path, tmp_path: Path) -> None:
    _manifest_with_fixtures(plugin_root)
    client = _install(tmp_path / "proj", plugin_root)
    before = {d["detector_id"] for d in client.list_detectors()}
    client.test_plugin("fixture_plugin")
    after = {d["detector_id"] for d in client.list_detectors()}
    assert before == after
    client.close()
