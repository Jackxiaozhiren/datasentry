"""Step 82（ADR-082）测试：插件清单（plugin.yaml）+ 清单目录加载 + 安装/卸载/列表。"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry_core.detectors.base import DetectorRegistry
from datasentry_core.plugins import (
    PLUGIN_MANIFEST_FILE,
    PluginManifest,
    PluginManifestError,
    load_plugin_detectors,
    read_plugin_manifests,
)

_EXAMPLE_PLUGIN = """\
from typing import ClassVar
from datasentry_core.detectors.base import DetectionContext
from datasentry_core.models.detector import DetectorMeta, IssueCandidate
from datasentry_core.models.enums import QualityDimension

class ManifestedPluginDetector:
    detector_id: ClassVar[str] = "plugin_manifested"
    detector_version: ClassVar[str] = "1.0.0"
    quality_dimension: ClassVar[QualityDimension] = QualityDimension.VALIDITY

    def supports(self, context: DetectionContext) -> bool:
        return True

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        return []

    def metadata(self) -> DetectorMeta:
        return DetectorMeta(
            detector_id=self.detector_id,
            display_name="Manifested Plugin",
            description="plugin with manifest for tests",
            quality_dimension=self.quality_dimension,
        )
"""


def _make_manifested_plugin(root: Path, name: str = "demo") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    d = root / name
    d.mkdir()
    (d / PLUGIN_MANIFEST_FILE).write_text(
        f"name: {name}\nversion: 0.2.0\nauthor: alice\nlicense: MIT\ndescription: demo plugin\n",
        encoding="utf-8",
    )
    (d / "detector.py").write_text(_EXAMPLE_PLUGIN, encoding="utf-8")
    return d


# ---- manifest 解析 ---------------------------------------------------


def test_manifest_required_fields() -> None:
    m = PluginManifest(name="a", version="1.0.0")
    assert m.author == "unknown"
    assert m.license == "unknown"
    assert m.description == ""


def test_manifest_from_file(tmp_path: Path) -> None:
    p = tmp_path / "plugin.yaml"
    p.write_text("name: foo\nversion: 1.2.3\nauthor: bob\nlicense: Apache-2.0\n", encoding="utf-8")
    m = PluginManifest.from_file(p)
    assert m == PluginManifest(name="foo", version="1.2.3", author="bob", license="Apache-2.0")


def test_manifest_from_file_missing_name(tmp_path: Path) -> None:
    p = tmp_path / "plugin.yaml"
    p.write_text("version: 1.0.0\n", encoding="utf-8")
    with pytest.raises(PluginManifestError, match="name"):
        PluginManifest.from_file(p)


def test_manifest_from_file_invalid_name_chars(tmp_path: Path) -> None:
    p = tmp_path / "plugin.yaml"
    p.write_text("name: bad name!\nversion: 1.0.0\n", encoding="utf-8")
    with pytest.raises(PluginManifestError, match="name"):
        PluginManifest.from_file(p)


def test_manifest_from_file_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "plugin.yaml"
    p.write_text("name: [unclosed\n", encoding="utf-8")
    with pytest.raises(PluginManifestError, match="YAML"):
        PluginManifest.from_file(p)


# ---- 扫描与加载 ------------------------------------------------------


def test_read_plugin_manifests_collects(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    _make_manifested_plugin(root)
    manifests = read_plugin_manifests([root])
    assert set(manifests) == {"demo"}
    assert manifests["demo"].version == "0.2.0"
    assert manifests["demo"].author == "alice"


def test_read_plugin_manifests_duplicate_name(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    _make_manifested_plugin(root, name="demo")
    _make_manifested_plugin(root, name="other")
    (root / "other" / PLUGIN_MANIFEST_FILE).write_text(
        "name: demo\nversion: 0.3.0\n", encoding="utf-8"
    )
    with pytest.raises(PluginManifestError, match="duplicate"):
        read_plugin_manifests([root])


def test_manifested_plugin_dir_loads(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    _make_manifested_plugin(root)
    registry = DetectorRegistry()
    loaded = load_plugin_detectors(registry, [root])
    assert loaded == ["plugin_manifested"]
    assert registry.get("plugin_manifested").detector_version == "1.0.0"


def test_flat_and_manifested_mix_loads(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    (root / "flat.py").write_text(
        _EXAMPLE_PLUGIN.replace("plugin_manifested", "plugin_flat"), encoding="utf-8"
    )
    _make_manifested_plugin(root)
    registry = DetectorRegistry()
    loaded = load_plugin_detectors(registry, [root])
    assert set(loaded) == {"plugin_manifested", "plugin_flat"}


def test_subdir_without_manifest_ignored(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "deep.py").write_text(
        _EXAMPLE_PLUGIN.replace("plugin_manifested", "plugin_nested"), encoding="utf-8"
    )
    registry = DetectorRegistry()
    assert load_plugin_detectors(registry, [root]) == []


# ---- 安装 / 卸载 / 列表 ------------------------------------------------


def test_install_plugin_from_dir(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    src = _make_manifested_plugin(tmp_path / "sources")
    client = DataSentry(project)
    result = client.install_plugin(src)
    assert result["name"] == "demo"
    assert result["version"] == "0.2.0"
    assert (project / "plugins" / "demo" / PLUGIN_MANIFEST_FILE).is_file()
    assert (project / "plugins" / "demo" / "detector.py").is_file()
    loaded = client.list_plugins()
    assert loaded["manifests"] == [
        {
            "name": "demo",
            "version": "0.2.0",
            "author": "alice",
            "license": "MIT",
            "description": "demo plugin",
            "integrity": "ok",
        }
    ]
    client.close()


def test_install_plugin_from_single_file(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "solo.py"
    src.write_text(_EXAMPLE_PLUGIN, encoding="utf-8")
    client = DataSentry(project)
    result = client.install_plugin(src)
    assert result["name"] == "solo"
    assert (project / "plugins" / "solo" / "solo.py").is_file()
    assert (project / "plugins" / "solo" / PLUGIN_MANIFEST_FILE).is_file()
    client.close()


def test_install_plugin_dir_without_manifest_placeholder(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "raw"
    src.mkdir()
    (src / "det.py").write_text(_EXAMPLE_PLUGIN, encoding="utf-8")
    client = DataSentry(project)
    result = client.install_plugin(src)
    assert result["name"] == "raw"
    assert (project / "plugins" / "raw" / PLUGIN_MANIFEST_FILE).is_file()
    client.close()


def test_install_plugin_duplicate_rejected(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    src = _make_manifested_plugin(tmp_path / "sources")
    client = DataSentry(project)
    client.install_plugin(src)
    with pytest.raises(PluginManifestError, match="already installed"):
        client.install_plugin(src)
    client.close()


def test_install_plugin_missing_source(tmp_path: Path) -> None:
    client = DataSentry(tmp_path / "proj")
    with pytest.raises(FileNotFoundError):
        client.install_plugin(tmp_path / "nope.py")
    client.close()


def test_uninstall_plugin(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    client = DataSentry(project)
    client.install_plugin(_make_manifested_plugin(tmp_path / "sources"))
    result = client.uninstall_plugin("demo")
    assert not (project / "plugins" / "demo").exists()
    assert result["name"] == "demo"
    client.close()


def test_uninstall_plugin_not_installed(tmp_path: Path) -> None:
    client = DataSentry(tmp_path / "proj")
    with pytest.raises(FileNotFoundError, match="not installed"):
        client.uninstall_plugin("ghost")
    client.close()


def test_installed_plugin_detected_by_new_client(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    client = DataSentry(project)
    client.install_plugin(_make_manifested_plugin(tmp_path / "sources"))
    client.close()
    fresh = DataSentry(project)
    detectors = fresh.list_detectors()
    plugin_detector = [d for d in detectors if d["source"] == "dir"]
    assert [d["detector_id"] for d in plugin_detector] == ["plugin_manifested"]
    fresh.close()


def test_install_rejects_non_py_file(tmp_path: Path) -> None:
    client = DataSentry(tmp_path / "proj")
    src = tmp_path / "plugin.txt"
    src.write_text("not a plugin", encoding="utf-8")
    with pytest.raises(PluginManifestError, match=r"\.py"):
        client.install_plugin(src)
    client.close()
