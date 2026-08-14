"""Step 83（ADR-083）测试：插件完整性锁（SHA-256 锁定 + 加载前校验）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry_core.plugin_locks import (
    PluginLock,
    PluginLocks,
    build_lock,
    compute_sha256,
    integrity_report,
)
from datasentry_core.plugins import load_plugin_detectors_excluding

_PLUGIN_SRC = """\
from typing import ClassVar
from datasentry_core.detectors.base import DetectionContext
from datasentry_core.models.detector import DetectorMeta, IssueCandidate
from datasentry_core.models.enums import QualityDimension

class LockedPluginDetector:
    detector_id: ClassVar[str] = "plugin_locked"
    detector_version: ClassVar[str] = "1.0.0"
    quality_dimension: ClassVar[QualityDimension] = QualityDimension.VALIDITY

    def supports(self, context: DetectionContext) -> bool:
        return True

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        return []

    def metadata(self) -> DetectorMeta:
        return DetectorMeta(
            detector_id=self.detector_id,
            display_name="Locked Plugin",
            description="plugin with integrity lock for tests",
            quality_dimension=self.quality_dimension,
        )
"""


@pytest.fixture()
def plugins_root(tmp_path: Path) -> Path:
    root = tmp_path / "plugins"
    root.mkdir()
    (root / "locked.py").write_text(_PLUGIN_SRC, encoding="utf-8")
    return root


# ---- 锁文件读写 -------------------------------------------------------


def test_compute_sha256_deterministic(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    assert compute_sha256(p) == compute_sha256(p)
    assert len(compute_sha256(p)) == 64


def test_compute_sha256_changes_with_content(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    before = compute_sha256(p)
    p.write_text("hello!", encoding="utf-8")
    assert compute_sha256(p) != before


def test_locks_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "plugin_locks.json"
    locks = PluginLocks()
    locks.set_plugin("demo", PluginLock(version="1.0", files={"a.py": "abc"}, installed_at="t"))
    locks.to_file(path)
    loaded = PluginLocks.from_file(path)
    expected = PluginLock(version="1.0", files={"a.py": "abc"}, installed_at="t")
    assert loaded.locks["demo"] == expected


def test_locks_from_file_missing_returns_empty(tmp_path: Path) -> None:
    assert PluginLocks.from_file(tmp_path / "nope.json").locks == {}


def test_locks_from_file_corrupt_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "plugin_locks.json"
    path.write_text("{not json", encoding="utf-8")
    assert PluginLocks.from_file(path).locks == {}


def test_build_lock_covers_all_files(tmp_path: Path) -> None:
    root = tmp_path / "plug"
    root.mkdir()
    (root / "a.py").write_text("x", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.py").write_text("y", encoding="utf-8")
    lock = build_lock(root, version="2.0")
    assert set(lock.files) == {"a.py", "sub/b.py"}
    assert lock.files["a.py"] == compute_sha256(root / "a.py")
    assert lock.version == "2.0"


# ---- 校验报告 ---------------------------------------------------------


def test_integrity_report_ok(plugins_root: Path) -> None:
    locks = PluginLocks()
    locks.set_plugin("locked", build_lock(plugins_root / "locked.py"))
    report = integrity_report(plugins_root, locks)
    assert report.status("locked") == "ok"
    assert report.tampered() == []


def test_integrity_report_tampered(plugins_root: Path) -> None:
    locks = PluginLocks()
    locks.set_plugin("locked", build_lock(plugins_root / "locked.py"))
    (plugins_root / "locked.py").write_text(_PLUGIN_SRC.replace("1.0.0", "9.9.9"), encoding="utf-8")
    report = integrity_report(plugins_root, locks)
    assert report.status("locked") == "tampered"
    assert [e.name for e in report.tampered()] == ["locked"]


def test_integrity_report_no_lock(plugins_root: Path) -> None:
    report = integrity_report(plugins_root, PluginLocks())
    assert report.status("locked") == "no_lock"


def test_integrity_report_empty_dir(tmp_path: Path) -> None:
    assert integrity_report(tmp_path / "missing", PluginLocks()).entries == []


def test_top_level_file_is_own_unit_not_tamper(plugins_root: Path) -> None:
    """顶层平铺 *.py 是独立插件单元：新增文件不影响既有单元（锁语义）。"""
    locks = PluginLocks()
    locks.set_plugin("locked", build_lock(plugins_root / "locked.py"))
    (plugins_root / "extra.py").write_text("print(1)", encoding="utf-8")
    report = integrity_report(plugins_root, locks)
    assert report.status("locked") == "ok"
    assert report.status("extra") == "no_lock"


def test_integrity_report_manifest_dir(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    d = root / "demo"
    d.mkdir()
    (d / "plugin.yaml").write_text("name: demo\nversion: 0.1.0\n", encoding="utf-8")
    (d / "det.py").write_text(_PLUGIN_SRC, encoding="utf-8")
    locks = PluginLocks()
    locks.set_plugin("demo", build_lock(d, version="0.1.0"))
    report = integrity_report(root, locks)
    assert report.status("demo") == "ok"


# ---- 加载排除 ---------------------------------------------------------


def test_excluding_skips_named_plugin(plugins_root: Path) -> None:
    from datasentry_core.detectors.base import DetectorRegistry

    registry = DetectorRegistry()
    loaded = load_plugin_detectors_excluding(registry, [plugins_root], exclude={"locked"})
    assert loaded == []
    assert [d.detector_id for d in registry.list()] == []


def test_excluding_keeps_others(tmp_path: Path) -> None:
    from datasentry_core.detectors.base import DetectorRegistry

    root = tmp_path / "plugins"
    root.mkdir()
    (root / "bad.py").write_text(_PLUGIN_SRC, encoding="utf-8")
    (root / "good.py").write_text(
        _PLUGIN_SRC.replace("plugin_locked", "plugin_good"), encoding="utf-8"
    )
    registry = DetectorRegistry()
    loaded = load_plugin_detectors_excluding(registry, [root], exclude={"bad"})
    assert loaded == ["plugin_good"]
    assert [d.detector_id for d in registry.list()] == ["plugin_good"]


# ---- client 集成 ------------------------------------------------------


def test_install_writes_lock(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "solo.py"
    src.write_text(_PLUGIN_SRC, encoding="utf-8")
    client = DataSentry(project)
    client.install_plugin(src)
    locks_path = project / ".datasentry" / "plugin_locks.json"
    assert locks_path.is_file()
    loaded = PluginLocks.from_file(locks_path)
    assert "solo" in loaded.locks
    assert loaded.locks["solo"].files["solo.py"] == compute_sha256(
        project / "plugins" / "solo" / "solo.py"
    )
    client.close()


def test_tampered_plugin_rejected_and_skipped(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "solo.py"
    src.write_text(_PLUGIN_SRC, encoding="utf-8")
    client = DataSentry(project)
    client.install_plugin(src)
    client.close()
    target = project / "plugins" / "solo" / "solo.py"
    target.write_text(_PLUGIN_SRC.replace("1.0.0", "9.9.9"), encoding="utf-8")
    fresh = DataSentry(project)
    result = fresh.list_plugins()
    assert [e["name"] for e in result["errors"]] == ["solo"]
    assert "integrity check failed" in result["errors"][0]["error"]
    assert all(p["source"] != "dir" for p in result["plugins"])
    assert result["manifests"][0]["integrity"] == "tampered"
    fresh.close()


def test_legacy_plugin_autolocks_on_first_load(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "plugins").mkdir()
    (project / "plugins" / "legacy.py").write_text(_PLUGIN_SRC, encoding="utf-8")
    client = DataSentry(project)
    locks_path = project / ".datasentry" / "plugin_locks.json"
    assert locks_path.is_file()
    loaded = PluginLocks.from_file(locks_path)
    assert "legacy" in loaded.locks
    detectors = client.list_detectors()
    assert [d["detector_id"] for d in detectors if d["source"] == "dir"] == ["plugin_locked"]
    client.close()


def test_reaccept_relocks_after_tamper(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "solo.py"
    src.write_text(_PLUGIN_SRC, encoding="utf-8")
    client = DataSentry(project)
    client.install_plugin(src)
    client.close()
    target = project / "plugins" / "solo" / "solo.py"
    target.write_text(_PLUGIN_SRC.replace("1.0.0", "9.9.9"), encoding="utf-8")
    reaccept = DataSentry(project)
    reaccept.reaccept_plugin("solo")
    reaccept.close()
    fresh = DataSentry(project)
    result = fresh.list_plugins()
    assert result["manifests"][0]["integrity"] == "ok"
    assert [d["detector_id"] for d in result["plugins"] if d["source"] == "dir"] == [
        "plugin_locked"
    ]
    fresh.close()


def test_uninstall_removes_lock_entry(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "solo.py"
    src.write_text(_PLUGIN_SRC, encoding="utf-8")
    client = DataSentry(project)
    client.install_plugin(src)
    client.uninstall_plugin("solo")
    loaded = PluginLocks.from_file(project / ".datasentry" / "plugin_locks.json")
    assert "solo" not in loaded.locks
    client.close()


def test_reaccept_unknown_plugin_raises(tmp_path: Path) -> None:
    client = DataSentry(tmp_path / "proj")
    with pytest.raises(FileNotFoundError, match="not installed"):
        client.reaccept_plugin("ghost")
    client.close()


def test_pycache_does_not_break_integrity(tmp_path: Path) -> None:
    """回归：插件 import 产生 __pycache__ 后，完整性校验仍为 ok（ADR-083）。"""
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "solo.py"
    src.write_text(_PLUGIN_SRC, encoding="utf-8")
    client = DataSentry(project)
    client.install_plugin(src)
    loaded = client.list_plugins()
    assert loaded["manifests"][0]["integrity"] == "ok"
    assert client.list_detectors() is not None
    client.close()
    reloaded = DataSentry(project)
    assert reloaded.list_plugins()["manifests"][0]["integrity"] == "ok"
    assert reloaded.list_plugins()["errors"] == []
    reloaded.close()
