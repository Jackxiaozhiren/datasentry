"""Step 59 凭据管理（ADR-059）测试：secrets.env 解析/权限/统一解析链。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from datasentry_core.secrets import (
    SecretsFileError,
    load_secrets,
    lookup_secret,
    remove_secret,
    secrets_path,
    set_secret,
    write_secrets,
)


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "config"
    home.mkdir()
    monkeypatch.setenv("DATASENTRY_CONFIG_HOME", str(home))
    return home


class TestSecretsPath:
    def test_defaults_to_home_config(self) -> None:
        assert secrets_path() == Path.home() / ".config" / "datasentry" / "secrets.env"

    def test_uses_datasentry_config_home(self, config_home: Path) -> None:
        assert secrets_path() == config_home / "datasentry" / "secrets.env"

    def test_xdg_config_home_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATASENTRY_CONFIG_HOME", raising=False)
        xdg = tmp_path / "xdg"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        assert secrets_path() == xdg / "datasentry" / "secrets.env"


class TestLoadSecrets:
    def test_missing_file_empty(self, config_home: Path) -> None:
        assert load_secrets() == {}

    def test_roundtrip_parse(self, config_home: Path) -> None:
        path = set_secret("A", "v1")
        set_secret("DATASENTRY_PG_DSN", "postgresql://u:p@h/db")
        assert load_secrets(path) == {"A": "v1", "DATASENTRY_PG_DSN": "postgresql://u:p@h/db"}

    def test_value_may_contain_equals(self, config_home: Path) -> None:
        path = set_secret("B", "a=b=c")
        assert load_secrets(path)["B"] == "a=b=c"

    def test_comments_and_blanks_ignored(self, config_home: Path) -> None:
        path = secrets_path()
        path.parent.mkdir(parents=True)
        path.write_text("# comment\n\nA=1\nB=2\n", encoding="utf-8")
        os.chmod(path, 0o600)
        assert load_secrets() == {"A": "1", "B": "2"}

    def test_malformed_line_raises(self, config_home: Path) -> None:
        path = secrets_path()
        path.parent.mkdir(parents=True)
        path.write_text("A=1\nNOEQUALS\n", encoding="utf-8")
        os.chmod(path, 0o600)
        with pytest.raises(SecretsFileError, match=r"secrets\.env:2"):
            load_secrets()

    def test_invalid_key_raises(self, config_home: Path) -> None:
        path = secrets_path()
        path.parent.mkdir(parents=True)
        path.write_text("lowercase=1\n", encoding="utf-8")
        os.chmod(path, 0o600)
        with pytest.raises(SecretsFileError, match="invalid secret key"):
            load_secrets()

    def test_loose_permissions_rejected(self, config_home: Path) -> None:
        path = secrets_path()
        path.parent.mkdir(parents=True)
        path.write_text("A=1\n", encoding="utf-8")
        os.chmod(path, 0o644)
        with pytest.raises(SecretsFileError, match="permissions too open"):
            load_secrets()


class TestPermissions:
    def test_write_enforces_600(self, config_home: Path) -> None:
        path = set_secret("A", "v1")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_dir_mode_700(self, config_home: Path) -> None:
        set_secret("A", "v1")
        assert stat.S_IMODE(secrets_path().parent.stat().st_mode) == 0o700

    def test_rewrite_fixes_loose_permissions(self, config_home: Path) -> None:
        path = secrets_path()
        path.parent.mkdir(parents=True)
        path.write_text("A=1\n", encoding="utf-8")
        os.chmod(path, 0o644)
        set_secret("B", "2")  # set 触发整体重写 → 权限自动修正
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert load_secrets() == {"A": "1", "B": "2"}


class TestLookupChain:
    def test_env_wins_over_file(self, config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        set_secret("DATASENTRY_PG_DSN", "postgresql://file:p@h/db")
        monkeypatch.setenv("DATASENTRY_PG_DSN", "postgresql://env:p@h/db")
        assert lookup_secret("DATASENTRY_PG_DSN") == "postgresql://env:p@h/db"

    def test_file_fallback(self, config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        set_secret("DATASENTRY_PG_DSN", "postgresql://file:p@h/db")
        monkeypatch.delenv("DATASENTRY_PG_DSN", raising=False)
        assert lookup_secret("DATASENTRY_PG_DSN") == "postgresql://file:p@h/db"

    def test_missing_returns_none(self, config_home: Path) -> None:
        assert lookup_secret("DATASENTRY_PG_DSN") is None


class TestMutation:
    def test_set_update_keeps_others(self, config_home: Path) -> None:
        set_secret("A", "1")
        set_secret("A", "2")
        assert load_secrets() == {"A": "2"}

    def test_remove(self, config_home: Path) -> None:
        set_secret("A", "1")
        set_secret("B", "2")
        assert remove_secret("A") is True
        assert load_secrets() == {"B": "2"}
        assert remove_secret("A") is False

    def test_write_secrets_empty_file(self, config_home: Path) -> None:
        path = write_secrets({})
        assert path.read_text(encoding="utf-8") == ""
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
