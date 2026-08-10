"""Step 31 + Step 50 测试：检测器插件 API v1（ADR-031）+ entry points 发现（ADR-050）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry_core.detectors.base import DetectorRegistry
from datasentry_core.plugins import (
    PluginError,
    PluginLoadError,
    discover_entrypoint_detectors,
    load_plugin_detectors,
)

_EXAMPLE_PLUGIN = """\
from typing import ClassVar
from datasentry_core.detectors.base import DetectionContext
from datasentry_core.models.detector import DetectorMeta, IssueCandidate
from datasentry_core.models.enums import QualityDimension

class TinyPluginDetector:
    detector_id: ClassVar[str] = "plugin_tiny"
    detector_version: ClassVar[str] = "1.0.0"
    quality_dimension: ClassVar[QualityDimension] = QualityDimension.VALIDITY

    def supports(self, context: DetectionContext) -> bool:
        return True

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        return []

    def metadata(self) -> DetectorMeta:
        return DetectorMeta(
            detector_id=self.detector_id,
            display_name="Tiny Plugin",
            description="minimal plugin for tests",
            quality_dimension=self.quality_dimension,
        )
"""


@pytest.fixture()
def plugin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plugins"
    d.mkdir()
    (d / "tiny_plugin.py").write_text(_EXAMPLE_PLUGIN, encoding="utf-8")
    return d


def test_load_plugin_detectors_registers(plugin_dir: Path) -> None:
    registry = DetectorRegistry()
    loaded = load_plugin_detectors(registry, [plugin_dir])
    assert loaded == ["plugin_tiny"]
    detector = registry.get("plugin_tiny")
    assert detector.detector_version == "1.0.0"


def test_non_detector_modules_skipped(plugin_dir: Path) -> None:
    (plugin_dir / "helper.py").write_text(
        "from datasentry_core.models.enums import QualityDimension\n"
        "VALUE = 42\n"
        "def helper(): ...\n",
        encoding="utf-8",
    )
    registry = DetectorRegistry()
    loaded = load_plugin_detectors(registry, [plugin_dir])
    assert loaded == ["plugin_tiny"]  # helper.py 无 Detector 类，跳过


def test_duplicate_detector_id_raises(tmp_path: Path, plugin_dir: Path) -> None:
    (plugin_dir / "clone.py").write_text(
        _EXAMPLE_PLUGIN.replace("class TinyPluginDetector", "class TinyPluginClone"),
        encoding="utf-8",
    )
    # 同目录两个模块撞 ID：加载失败且不静默
    with pytest.raises(PluginLoadError, match="already registered: plugin_tiny"):
        load_plugin_detectors(DetectorRegistry(), [plugin_dir])
    # 已注册的 ID 再次加载同样报错（先手动注册占位，再加载同 ID 插件）
    registry = DetectorRegistry()
    registry.register(
        type(
            "Existing",
            (),
            {
                "detector_id": "plugin_tiny",
                "detector_version": "9.9.9",
                "quality_dimension": "validity",
                "supports": lambda context: False,
                "detect": lambda context: [],
                "metadata": lambda: None,
            },
        )()
    )
    with pytest.raises(PluginLoadError, match="already registered"):
        load_plugin_detectors(registry, [plugin_dir])


def test_broken_import_raises_with_location(plugin_dir: Path) -> None:
    (plugin_dir / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    registry = DetectorRegistry()
    with pytest.raises(PluginLoadError, match=r"broken\.py"):
        load_plugin_detectors(registry, [plugin_dir])


def test_missing_directory_returns_empty(tmp_path: Path) -> None:
    registry = DetectorRegistry()
    assert load_plugin_detectors(registry, [tmp_path / "nope"]) == []
    assert load_plugin_detectors(registry, []) == []


def test_plugin_loaded_from_workspace_affects_scan(tmp_path: Path) -> None:
    """workspace/plugins 自动加载：scan_file 产出插件 issue（端到端）。"""
    import shutil

    example = Path(__file__).resolve().parents[1] / "examples" / "plugins" / "example_detector.py"
    ws = tmp_path / "ws"
    ws.mkdir()
    plugins = ws / "plugins"
    plugins.mkdir()
    shutil.copy2(example, plugins / "example_detector.py")
    data = ws / "d.csv"
    data.write_text("id,price\n1,100\n2,-5\n3,300\n", encoding="utf-8")

    client = DataSentry(ws)
    try:
        scan, _, issues = client.scan_file(str(data), dataset_id="t1")
        assert scan is not None
        plugin_issues = [i for i in issues if "plugin_negative_value" in i.detector_ids]
        assert len(plugin_issues) == 1
        assert plugin_issues[0].affected_count == 1  # price=-5
        ids = [d["detector_id"] for d in client.list_detectors()]
        assert "plugin_negative_value" in ids
    finally:
        client.close()


def test_detectors_list_shows_plugins(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    plugins = ws / "plugins"
    plugins.mkdir()
    (plugins / "tiny_plugin.py").write_text(_EXAMPLE_PLUGIN, encoding="utf-8")
    client = DataSentry(ws)
    try:
        detectors = client.list_detectors()
        ids = {d["detector_id"] for d in detectors}
        assert "plugin_tiny" in ids
        entry = next(d for d in detectors if d["detector_id"] == "plugin_tiny")
        assert entry["enabled"] is True
        assert entry["quality_dimension"] == "validity"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Step 50：entry points 发现（V2-C / ADR-050）
# ---------------------------------------------------------------------------


class _FakeEntryPoint:
    """最小 EntryPoint 替身：name + load()（可抛异常或返回值）。"""

    def __init__(self, name: str, value: object) -> None:
        self.name = name
        self._value = value

    def load(self) -> object:
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


def _fake_entry_points(monkeypatch: pytest.MonkeyPatch, points: list[_FakeEntryPoint]) -> None:
    import datasentry_core.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "_entry_points_for", lambda group: points)


class TestEntryPointDiscovery:
    def test_registers_detector_class(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_entry_points(monkeypatch, [_FakeEntryPoint("tiny", _TinyEntryDetector)])
        registry = DetectorRegistry()
        report = discover_entrypoint_detectors(registry)
        assert report.loaded == ["ep_tiny"]
        assert report.errors == []
        assert registry.get("ep_tiny").detector_version == "1.0.0"

    def test_accepts_instance_and_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_entry_points(
            monkeypatch,
            [
                _FakeEntryPoint("inst", _entry_detector("ep_inst")()),
                _FakeEntryPoint("fac", lambda: _entry_detector("ep_fac")()),
            ],
        )
        registry = DetectorRegistry()
        report = discover_entrypoint_detectors(registry)
        assert sorted(report.loaded) == ["ep_fac", "ep_inst"]
        assert report.errors == []

    def test_load_failure_recorded_gracefully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """缺依赖/import 失败 → 只记录错误，其他插件照常加载。"""
        _fake_entry_points(
            monkeypatch,
            [
                _FakeEntryPoint("broken", ImportError("no module named 'missing_dep'")),
                _FakeEntryPoint("tiny", _TinyEntryDetector),
            ],
        )
        registry = DetectorRegistry()
        report = discover_entrypoint_detectors(registry)
        assert report.loaded == ["ep_tiny"]
        assert len(report.errors) == 1
        error = report.errors[0]
        assert isinstance(error, PluginError)
        assert error.name == "broken"
        assert "missing_dep" in error.message

    def test_conflicting_id_recorded_not_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_entry_points(
            monkeypatch,
            [
                _FakeEntryPoint("a", _TinyEntryDetector),
                _FakeEntryPoint("b", _TinyEntryDetector),  # 同 detector_id
            ],
        )
        registry = DetectorRegistry()
        report = discover_entrypoint_detectors(registry)
        assert report.loaded == ["ep_tiny"]
        assert len(report.errors) == 1
        assert "already registered" in report.errors[0].message

    def test_invalid_value_recorded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_entry_points(
            monkeypatch,
            [
                _FakeEntryPoint("bad", 42),
                _FakeEntryPoint("factory_bad", lambda: 42),
                _FakeEntryPoint("tiny", _TinyEntryDetector),
            ],
        )
        registry = DetectorRegistry()
        report = discover_entrypoint_detectors(registry)
        assert report.loaded == ["ep_tiny"]
        assert len(report.errors) == 2

    def test_no_entry_points_empty_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_entry_points(monkeypatch, [])
        report = discover_entrypoint_detectors(DetectorRegistry())
        assert report.loaded == [] and report.errors == []


class TestEntryPointClientIntegration:
    def test_list_plugins_sources_dir_and_entrypoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_entry_points(monkeypatch, [_FakeEntryPoint("tiny", _TinyEntryDetector)])
        ws = tmp_path / "ws"
        ws.mkdir()
        plugins = ws / "plugins"
        plugins.mkdir()
        (plugins / "tiny_plugin.py").write_text(_EXAMPLE_PLUGIN, encoding="utf-8")
        client = DataSentry(ws)
        try:
            plugins_info = client.list_plugins()
            ids = {p["detector_id"] for p in plugins_info["plugins"]}
            assert ids == {"plugin_tiny", "ep_tiny"}
            by_id = {p["detector_id"]: p["source"] for p in plugins_info["plugins"]}
            assert by_id["plugin_tiny"] == "dir"
            assert by_id["ep_tiny"] == "entrypoint"
            assert plugins_info["errors"] == []
        finally:
            client.close()

    def test_list_detectors_has_source_field(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        client = DataSentry(ws)
        try:
            detectors = client.list_detectors()
            by_id = {d["detector_id"]: d for d in detectors}
            assert by_id["uniqueness_violation"]["source"] == "builtin"
        finally:
            client.close()

    def test_entrypoint_failure_visible_in_plugin_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_entry_points(monkeypatch, [_FakeEntryPoint("broken", ImportError("nope"))])
        ws = tmp_path / "ws"
        ws.mkdir()
        client = DataSentry(ws)
        try:
            result = client.list_plugins()
            assert len(result["errors"]) == 1
            assert result["errors"][0]["name"] == "broken"
            scan = client.scan_file  # 扫描能力不受影响
            assert scan is not None
        finally:
            client.close()


class _TinyEntryDetector:
    """最小 entry point 检测器（类形态，注册表无参实例化）。"""

    detector_id = "ep_tiny"
    detector_version = "1.0.0"
    quality_dimension = "validity"

    def supports(self, context: object) -> bool:
        return True

    def detect(self, context: object) -> list[object]:
        return []

    def metadata(self) -> object:
        from datasentry_core.models.detector import DetectorMeta

        return DetectorMeta(
            detector_id=self.detector_id,
            display_name="EP Tiny",
            description="entry point test detector",
            quality_dimension=self.quality_dimension,
        )


def _entry_detector(detector_id: str) -> type:
    """带指定 detector_id 的 entry point 检测器类（避免 fake 间冲突）。"""

    class _ParamEntryDetector(_TinyEntryDetector):
        pass

    _ParamEntryDetector.detector_id = detector_id
    return _ParamEntryDetector


class TestPluginListCli:
    def test_plugin_list_shows_dir_plugin(self, tmp_path: Path, capsys) -> None:
        from datasentry.cli import main

        ws = tmp_path / "ws"
        ws.mkdir()
        plugins = ws / "plugins"
        plugins.mkdir()
        (plugins / "tiny_plugin.py").write_text(_EXAMPLE_PLUGIN, encoding="utf-8")
        code = main(["--project", str(ws), "--format", "json", "plugin", "list"])
        assert code == 0
        data = json.loads(capsys.readouterr().out)["data"]
        by_id = {p["detector_id"]: p for p in data["plugins"]}
        assert "plugin_tiny" in by_id
        assert by_id["plugin_tiny"]["source"] == "dir"
        assert data["errors"] == []

    def test_plugin_list_shows_entrypoint_and_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        from datasentry.cli import main

        _fake_entry_points(
            monkeypatch,
            [
                _FakeEntryPoint("tiny", _TinyEntryDetector),
                _FakeEntryPoint("broken", ImportError("missing_dep")),
            ],
        )
        ws = tmp_path / "ws"
        ws.mkdir()
        code = main(["--project", str(ws), "--format", "json", "plugin", "list"])
        assert code == 0
        data = json.loads(capsys.readouterr().out)["data"]
        by_id = {p["detector_id"]: p for p in data["plugins"]}
        assert by_id["ep_tiny"]["source"] == "entrypoint"
        assert any(e["name"] == "broken" and "missing_dep" in e["error"] for e in data["errors"])
