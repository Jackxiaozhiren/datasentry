"""Step 22 存储路径解析测试（ADR-010 三平台布局 + override）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasentry_core.storage.paths import (
    global_data_dir,
    project_data_dir,
    project_db_path,
    project_repairs_dir,
    project_reports_dir,
)


class TestGlobalDataDir:
    def test_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_HOME", "~/alt-home")
        assert global_data_dir() == Path("~/alt-home").expanduser()

    def test_macos_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATASENTRY_HOME", raising=False)
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setenv("HOME", "/Users/tester")
        assert global_data_dir() == Path("/Users/tester/Library/Application Support/datasentry")

    def test_linux_xdg_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATASENTRY_HOME", raising=False)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", "/var/data")
        assert global_data_dir() == Path("/var/data/datasentry")

    def test_linux_fallback_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATASENTRY_HOME", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("HOME", "/home/tester")
        assert global_data_dir() == Path("/home/tester/.local/share/datasentry")

    def test_windows_localappdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATASENTRY_HOME", raising=False)
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\tester\\AppData\\Local")
        result = global_data_dir()
        assert str(result).startswith("C:\\Users\\tester\\AppData\\Local")
        assert result.name == "datasentry"


class TestProjectDataDir:
    def test_project_layout(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        assert project_data_dir(workspace) == workspace / ".datasentry"
        assert project_db_path(workspace) == workspace / ".datasentry" / "metadata.db"
        assert project_reports_dir(workspace) == workspace / ".datasentry" / "reports"
        assert project_repairs_dir(workspace) == workspace / ".datasentry" / "repairs"

    def test_expanduser_applies(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        assert project_data_dir(Path("~/ws")) == home / "ws" / ".datasentry"
