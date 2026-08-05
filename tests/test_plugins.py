"""Step 31 测试：检测器插件 API v1（ADR-031）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry_core.detectors.base import DetectorRegistry
from datasentry_core.plugins import PluginLoadError, load_plugin_detectors

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
