"""`atomic_write_text` 的契约测试（阶段 3 第 9 项切片 a）。

这四段契约原本分别活在四个调用点的失败注入测试里（`secrets.write_secrets` 的 `Q-10`、
`PluginLocks.to_file` 与 `DataSentry._save_profile` 的 `Q-5`、`pii_vault.rotate_key` 的 `Q-21`）。
共享件抽出后，契约在这里钉一次，调用点自己的测试继续不改一行地通过——哪天某个调用点悄悄
退回就地写，那边自己的测试先红。
"""

from __future__ import annotations

import os
import pathlib

import pytest

from datasentry_core.atomicio import atomic_write_text


def test_round_trip_publishes_content_at_the_requested_mode(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "book.json"
    atomic_write_text(path, '{"a": 1}\n')
    assert path.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert (path.stat().st_mode & 0o777) == 0o600
    residue = sorted(p.name for p in tmp_path.iterdir() if p.name != "book.json")
    assert not residue, f"staging residue: {residue}"


def test_a_failed_publish_keeps_the_previous_bytes_and_removes_the_staging(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "book.json"
    atomic_write_text(path, "previous\n")
    before = path.read_bytes()

    def boom(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("datasentry_core.atomicio.os.replace", boom)
    with pytest.raises(OSError) as caught:
        atomic_write_text(path, "new\n")
    assert caught.value.errno == 28, repr(caught.value)
    assert path.read_bytes() == before, "a failed publish rewrote the live file"
    residue = [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert not residue, f"staging left behind: {residue}"


def test_staging_that_cannot_be_removed_is_named_not_swallowed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "book.json"

    def failing_replace(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    def failing_unlink(self: pathlib.Path) -> None:
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr("datasentry_core.atomicio.os.replace", failing_replace)
    monkeypatch.setattr(pathlib.Path, "unlink", failing_unlink)
    with pytest.raises(OSError) as caught:
        atomic_write_text(path, "secret\n")
    notes = " ".join(getattr(caught.value, "__notes__", []))
    assert "could not be removed" in notes, (
        f"the cleanup failure was swallowed instead of noted: {notes!r}"
    )
    assert caught.value.errno == 28, repr(caught.value)


def test_content_never_hits_disk_under_a_looser_mode(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[int, int]] = []
    real_open = pathlib.Path.open

    def spy_open(self: pathlib.Path, mode: str, encoding: str) -> object:
        fh = real_open(self, mode, encoding=encoding)

        class Spy:
            def __enter__(self) -> Spy:
                return self

            def __exit__(self, *exc: object) -> None:
                fh.__exit__(*exc)

            def write(self, data: str) -> int:
                seen.append((os.fstat(fh.fileno()).st_mode & 0o777, len(data)))
                return fh.write(data)

            def fileno(self) -> int:
                return fh.fileno()

        return Spy()

    monkeypatch.setattr(pathlib.Path, "open", spy_open)
    atomic_write_text(tmp_path / "k.txt", "material\n")
    assert seen, "the spy never observed a write, so this test measured nothing"
    assert all(mode == 0o600 for mode, _ in seen), (
        f"bytes reached disk before the mode was tightened: {seen!r}"
    )


def test_the_staging_name_can_be_made_unique_for_concurrent_writers(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "vault.key"
    atomic_write_text(path, "material\n", staging=".vault.key.tmp.abc123")
    assert path.read_text(encoding="utf-8") == "material\n"
    assert not (tmp_path / ".vault.key.tmp.abc123").exists(), "the named staging survived"


def test_the_helper_does_not_create_parent_directories(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "not-created" / "book.json"
    with pytest.raises(FileNotFoundError):
        atomic_write_text(missing, "x\n")
    assert not (tmp_path / "not-created").exists(), (
        "the helper created a directory the caller never asked for"
    )
