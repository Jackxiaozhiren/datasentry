"""AC-11b 的留存层：源数据副本的写入、清单、留存策略，以及"留存后仍可重扫"。

与 ``test_rescan_issue_diff.py`` 的分工：那个证明"能比对", 这个证明"分母不会平白烂掉"。
核心判据是 ``test_rescan_survives_source_removal``——源文件删了, 重扫仍要给出 ``compared``
且 ``source=fixture``; 反过来 ``test_pruned_run_becomes_uncovered`` 证明删掉副本后必须退回
``uncovered``, 不许继续报"没差异"。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry.cli import EXIT_CONFIG, EXIT_OK, main
from datasentry_core.models.scan import ScanRun
from datasentry_core.storage.fixtures import (
    DEFAULT_MAX_BYTES,
    FixtureEntry,
    FixtureStore,
)

CSV = "id,amount,email\n1,10,a@x.co\n1,1000,b@x.co\n2,-5,not-an-email\n,500,c@x.co\n"
OTHER_CSV = "id,amount,email\n1,10,a@x.co\n2,-5,CHANGED@x.co\n"


@pytest.fixture
def csv_source(tmp_path: Path) -> Path:
    path = tmp_path / "orders.csv"
    path.write_text(CSV, encoding="utf-8")
    return path


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


@pytest.fixture
def store(tmp_path: Path) -> FixtureStore:
    return FixtureStore(tmp_path / "fixtures")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed(store: FixtureStore, run_id: str, captured_at: str, payload: str = "id\n1\n") -> None:
    """直接写一份清单：prune 的排序判据是 captured_at，不能靠连续两次 capture 的墙钟抢跑。"""
    run_dir = store.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "source.csv").write_text(payload, encoding="utf-8")
    entry = FixtureEntry(
        run_id=run_id,
        rel_path=f"{run_id}/source.csv",
        sha256=_sha(payload),
        size_bytes=len(payload.encode("utf-8")),
        source_path=f"/tmp/{run_id}.csv",
        captured_at=captured_at,
        fingerprint_type="full",
    )
    (run_dir / "manifest.json").write_text(json.dumps(entry.to_dict()), encoding="utf-8")


class TestCapture:
    """复制 + 记账：字节、清单、幂等与拒绝条件。"""

    def test_capture_writes_copy_and_manifest(self, store: FixtureStore, csv_source: Path) -> None:
        entry, created = store.capture(
            run_id="scan_a",
            source=csv_source,
            source_path=str(csv_source),
            fingerprint_type="full",
        )
        assert created
        copied = store.path_for(entry)
        assert copied.read_text(encoding="utf-8") == CSV
        assert entry.sha256 == _sha(CSV)
        assert entry.size_bytes == len(CSV.encode("utf-8"))
        assert entry.rel_path == "scan_a/source.csv"
        assert json.loads(store.manifest_path("scan_a").read_text(encoding="utf-8"))["run_id"] == (
            "scan_a"
        )

    def test_capture_is_idempotent_for_same_content(
        self, store: FixtureStore, csv_source: Path
    ) -> None:
        first, created = store.capture(
            run_id="scan_a",
            source=csv_source,
            source_path=str(csv_source),
            fingerprint_type="full",
        )
        again, created_again = store.capture(
            run_id="scan_a",
            source=csv_source,
            source_path=str(csv_source),
            fingerprint_type="full",
        )
        assert created and not created_again
        assert again == first
        assert len(store.entries()) == 1

    def test_capture_refuses_different_content_without_overwrite(
        self, store: FixtureStore, csv_source: Path
    ) -> None:
        entry, _ = store.capture(
            run_id="scan_a",
            source=csv_source,
            source_path=str(csv_source),
            fingerprint_type="full",
        )
        csv_source.write_text(OTHER_CSV, encoding="utf-8")
        with pytest.raises(FileExistsError):
            store.capture(
                run_id="scan_a",
                source=csv_source,
                source_path=str(csv_source),
                fingerprint_type="full",
            )
        # 拒绝必须发生在替换之前：旧副本与清单都不能被动过。
        assert store.path_for(entry).read_text(encoding="utf-8") == CSV
        assert store.get("scan_a") == entry

    def test_overwrite_replaces_copy(self, store: FixtureStore, csv_source: Path) -> None:
        first, _ = store.capture(
            run_id="scan_a",
            source=csv_source,
            source_path=str(csv_source),
            fingerprint_type="full",
        )
        csv_source.write_text(OTHER_CSV, encoding="utf-8")
        second, created = store.capture(
            run_id="scan_a",
            source=csv_source,
            source_path=str(csv_source),
            fingerprint_type="full",
            overwrite=True,
        )
        assert not created
        assert second.sha256 == _sha(OTHER_CSV) != first.sha256
        assert store.path_for(second).read_text(encoding="utf-8") == OTHER_CSV
        assert second.captured_at >= first.captured_at

    def test_oversized_source_leaves_nothing_behind(
        self, store: FixtureStore, csv_source: Path
    ) -> None:
        with pytest.raises(ValueError, match="budget"):
            store.capture(
                run_id="scan_a",
                source=csv_source,
                source_path=str(csv_source),
                fingerprint_type="full",
                max_bytes=8,
            )
        assert not store.run_dir("scan_a").exists()

    def test_missing_source_is_an_error(self, store: FixtureStore, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            store.capture(
                run_id="scan_a",
                source=tmp_path / "nope.csv",
                source_path="nope.csv",
                fingerprint_type="full",
            )

    def test_absent_suffix_yields_plain_source_name(
        self, store: FixtureStore, tmp_path: Path
    ) -> None:
        plain = tmp_path / "dataset"
        plain.write_text("id\n1\n", encoding="utf-8")
        entry, _ = store.capture(
            run_id="scan_a",
            source=plain,
            source_path=str(plain),
            fingerprint_type="full",
        )
        assert entry.rel_path == "scan_a/source"

    def test_verify_detects_a_replaced_copy(self, store: FixtureStore, csv_source: Path) -> None:
        entry, _ = store.capture(
            run_id="scan_a",
            source=csv_source,
            source_path=str(csv_source),
            fingerprint_type="full",
        )
        assert store.verify(entry)
        store.path_for(entry).write_text("id\n0\n", encoding="utf-8")
        assert not store.verify(entry)
        # 校验只回答"还能不能用"，不改清单：否则被改坏这件事就没有证据了。
        assert store.get("scan_a") == entry

    def test_verify_is_false_when_the_copy_is_gone(
        self, store: FixtureStore, csv_source: Path
    ) -> None:
        entry, _ = store.capture(
            run_id="scan_a",
            source=csv_source,
            source_path=str(csv_source),
            fingerprint_type="full",
        )
        store.path_for(entry).unlink()
        assert not store.verify(entry)

    def test_run_id_must_be_path_safe(self, store: FixtureStore) -> None:
        for bad in ("../x", "..", "a/b", "", ".hidden"):
            with pytest.raises(ValueError, match="unsafe scan run id"):
                store.run_dir(bad)

    def test_tampered_rel_path_cannot_escape_the_store(self, store: FixtureStore) -> None:
        entry = FixtureEntry(
            run_id="scan_a",
            rel_path="../../etc/passwd",
            sha256="0" * 64,
            size_bytes=1,
            source_path="/etc/passwd",
            captured_at="2026-01-01T00:00:00+00:00",
            fingerprint_type="full",
        )
        with pytest.raises(ValueError, match="escapes the store"):
            store.path_for(entry)


class TestListing:
    """清单是唯一索引：损坏要喊，孤儿要露头，排序要稳定。"""

    def test_corrupt_manifest_raises_instead_of_vanishing(self, store: FixtureStore) -> None:
        run_dir = store.run_dir("scan_a")
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            store.get("scan_a")
        with pytest.raises(ValueError):
            store.entries()

    def test_manifest_run_id_must_match_directory(self, store: FixtureStore) -> None:
        _seed(store, "scan_a", "2026-01-01T00:00:00+00:00")
        payload = json.loads(store.manifest_path("scan_a").read_text(encoding="utf-8"))
        payload["run_id"] = "scan_b"
        store.manifest_path("scan_a").write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="mismatch"):
            store.get("scan_a")

    def test_orphan_dirs_are_reported_not_silently_skipped(self, store: FixtureStore) -> None:
        (store.root / "scan_dead").mkdir(parents=True)
        _seed(store, "scan_a", "2026-01-01T00:00:00+00:00")
        assert store.orphan_dirs() == ["scan_dead"]
        assert [entry.run_id for entry in store.entries()] == ["scan_a"]

    def test_entries_are_ordered_by_capture_time(self, store: FixtureStore) -> None:
        _seed(store, "scan_b", "2026-02-01T00:00:00+00:00")
        _seed(store, "scan_a", "2026-01-01T00:00:00+00:00")
        assert [entry.run_id for entry in store.entries()] == ["scan_a", "scan_b"]

    def test_empty_store_lists_nothing(self, store: FixtureStore) -> None:
        assert store.entries() == []
        assert store.orphan_dirs() == []
        assert store.total_bytes() == 0


class TestPrune:
    """留存策略：按条数与字节两个维度，删除必须可预演。"""

    def test_keep_newest_n(self, store: FixtureStore) -> None:
        _seed(store, "old", "2026-01-01T00:00:00+00:00")
        _seed(store, "mid", "2026-02-01T00:00:00+00:00")
        _seed(store, "new", "2026-03-01T00:00:00+00:00")
        removed = store.prune(keep=2)
        assert [entry.run_id for entry in removed] == ["old"]
        assert not store.run_dir("old").exists()
        assert [entry.run_id for entry in store.entries()] == ["mid", "new"]

    def test_max_bytes_drops_oldest_first(self, store: FixtureStore) -> None:
        _seed(store, "old", "2026-01-01T00:00:00+00:00", payload="x" * 10)
        _seed(store, "new", "2026-02-01T00:00:00+00:00", payload="y" * 10)
        removed = store.prune(max_bytes=12)
        assert [entry.run_id for entry in removed] == ["old"]
        assert store.total_bytes() == 10

    def test_dry_run_deletes_nothing(self, store: FixtureStore) -> None:
        _seed(store, "old", "2026-01-01T00:00:00+00:00")
        _seed(store, "new", "2026-02-01T00:00:00+00:00")
        removed = store.prune(keep=1, dry_run=True)
        assert [entry.run_id for entry in removed] == ["old"]
        assert store.run_dir("old").is_dir()

    def test_keep_zero_removes_everything(self, store: FixtureStore) -> None:
        _seed(store, "old", "2026-01-01T00:00:00+00:00")
        _seed(store, "new", "2026-02-01T00:00:00+00:00")
        assert len(store.prune(keep=0)) == 2
        assert store.entries() == []

    def test_no_policy_removes_nothing(self, store: FixtureStore) -> None:
        _seed(store, "old", "2026-01-01T00:00:00+00:00")
        assert store.prune() == []
        assert store.run_dir("old").is_dir()

    def test_negative_policy_is_rejected(self, store: FixtureStore) -> None:
        with pytest.raises(ValueError, match="keep"):
            store.prune(keep=-1)
        with pytest.raises(ValueError, match="max_bytes"):
            store.prune(max_bytes=-1)


class TestRescanWithFixtures:
    """留存改变的是重扫的可用性，不是差异的判定标准。"""

    def _scan(self, workspace: Path, source: Path) -> ScanRun:
        client = DataSentry(project=workspace)
        try:
            scan, _runs, _issues = client.scan_file(source)
            return scan
        finally:
            client.close()

    def test_rescan_survives_source_removal(self, ws: Path, csv_source: Path) -> None:
        scan = self._scan(ws, csv_source)
        client = DataSentry(project=ws)
        try:
            assert client.capture_fixtures(limit=5).captured == 1
            csv_source.unlink()
            report = client.rescan_runs(limit=5)
        finally:
            client.close()
        assert report.checked == 1
        assert report.comparable == 1
        assert report.from_fixture == 1
        assert report.rows[0].source_kind == "fixture"
        assert not report.rows[0].drifted
        assert report.rows[0].run_id == scan.id

    def test_rescan_prefers_fixture_over_a_changed_origin(self, ws: Path, csv_source: Path) -> None:
        self._scan(ws, csv_source)
        client = DataSentry(project=ws)
        try:
            client.capture_fixtures(limit=5)
            csv_source.write_text(OTHER_CSV, encoding="utf-8")
            report = client.rescan_runs(limit=5)
        finally:
            client.close()
        # 原始文件已变，但留存副本仍是那次扫描的字节：差异应归因于检测器，不是数据。
        assert report.rows[0].status == "compared"
        assert report.rows[0].source_kind == "fixture"

    def test_capture_refuses_when_source_changed(self, ws: Path, csv_source: Path) -> None:
        self._scan(ws, csv_source)
        csv_source.write_text(OTHER_CSV, encoding="utf-8")
        client = DataSentry(project=ws)
        try:
            report = client.capture_fixtures(limit=5)
            retained = client.fixture_entries()
        finally:
            client.close()
        assert report.captured == 0
        assert report.skipped == 1
        assert report.rows[0].reason.startswith("source_changed:")
        assert retained == []

    def test_second_capture_reports_already(self, ws: Path, csv_source: Path) -> None:
        self._scan(ws, csv_source)
        client = DataSentry(project=ws)
        try:
            client.capture_fixtures(limit=5)
            again = client.capture_fixtures(limit=5)
        finally:
            client.close()
        assert again.already == 1
        assert again.captured == 0
        assert again.covered == 1

    def test_overwrite_repairs_a_tampered_copy(self, ws: Path, csv_source: Path) -> None:
        self._scan(ws, csv_source)
        client = DataSentry(project=ws)
        try:
            client.capture_fixtures(limit=5)
            (fixture,) = client.fixture_entries()
            target = client.fixtures_dir / fixture.rel_path
            target.write_text("id\n999\n", encoding="utf-8")
            report = client.capture_fixtures(limit=5, overwrite=True)
            (repaired,) = client.fixture_entries()
        finally:
            client.close()
        assert report.captured == 1
        assert repaired.sha256 == _sha(CSV)
        assert target.read_text(encoding="utf-8") == CSV

    def test_deleted_copy_falls_back_to_origin(self, ws: Path, csv_source: Path) -> None:
        self._scan(ws, csv_source)
        client = DataSentry(project=ws)
        try:
            client.capture_fixtures(limit=5)
            (fixture,) = client.fixture_entries()
            (client.fixtures_dir / fixture.rel_path).unlink()
            report = client.rescan_runs(limit=5)
        finally:
            client.close()
        assert report.rows[0].status == "compared"
        assert report.rows[0].source_kind == "origin"

    def test_corrupt_manifest_is_a_failed_row_not_a_crash(self, ws: Path, csv_source: Path) -> None:
        self._scan(ws, csv_source)
        client = DataSentry(project=ws)
        try:
            client.capture_fixtures(limit=5)
            for manifest in client.fixtures_dir.glob("*/manifest.json"):
                manifest.write_text("{not json", encoding="utf-8")
            report = client.rescan_runs(limit=5)
        finally:
            client.close()
        assert report.rows[0].status == "scan_failed"
        assert report.uncovered == 1
        assert "留存清单不可用" in report.rows[0].note

    def test_pruned_run_becomes_uncovered(self, ws: Path, csv_source: Path) -> None:
        self._scan(ws, csv_source)
        client = DataSentry(project=ws)
        try:
            client.capture_fixtures(limit=5)
            csv_source.unlink()
            client.prune_fixtures(keep=0)
            report = client.rescan_runs(limit=5)
        finally:
            client.close()
        assert report.uncovered == 1
        assert report.rows[0].status == "source_missing"
        assert report.from_fixture == 0


class TestFixturesCli:
    """命令面：存档、退出码、中英文案。"""

    def _scanned(self, ws: Path, csv_source: Path) -> None:
        client = DataSentry(project=ws)
        try:
            client.scan_file(csv_source)
        finally:
            client.close()

    def test_capture_and_list(
        self, ws: Path, csv_source: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._scanned(ws, csv_source)
        assert main(["--project", str(ws), "fixtures", "capture"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "| captured |" in out
        assert "checked 1 / captured 1" in out
        assert main(["--project", str(ws), "fixtures", "list"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "scan_" in out and "1 fixture(s)" in out

    def test_list_json_carries_bytes_and_orphans(
        self, ws: Path, csv_source: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._scanned(ws, csv_source)
        main(["--project", str(ws), "fixtures", "capture"])
        capsys.readouterr()
        assert main(["--project", str(ws), "--format", "json", "fixtures", "list"]) == EXIT_OK
        data = json.loads(capsys.readouterr().out)["data"]
        assert data["count"] == 1
        assert data["bytes"] > 0
        assert data["orphans"] == []

    def test_capture_without_runs_is_a_config_error(
        self, ws: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--project", str(ws), "--format", "json", "fixtures", "capture"])
        assert code == EXIT_CONFIG
        assert json.loads(capsys.readouterr().out)["data"]["error"] == "no scan runs yet"

    def test_prune_without_policy_is_a_config_error(
        self, ws: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--project", str(ws), "--format", "json", "fixtures", "prune"])
        assert code == EXIT_CONFIG
        assert "pass --keep" in json.loads(capsys.readouterr().out)["data"]["error"]

    def test_prune_dry_run_keeps_the_copy(
        self, ws: Path, csv_source: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._scanned(ws, csv_source)
        main(["--project", str(ws), "fixtures", "capture"])
        capsys.readouterr()
        assert main(["--project", str(ws), "fixtures", "prune", "--keep", "0", "--dry-run"]) == (
            EXIT_OK
        )
        assert "dry run" in capsys.readouterr().out
        client = DataSentry(project=ws)
        try:
            assert len(client.fixture_entries()) == 1
        finally:
            client.close()

    def test_zh_output_has_no_untranslated_keys(
        self, ws: Path, csv_source: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._scanned(ws, csv_source)
        assert main(["--project", str(ws), "--lang", "zh", "fixtures", "capture"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "cli.fixtures" not in out
        assert "留存目录" in out


def test_default_budget_is_64_mib() -> None:
    """上限是给人看的常量：改它必须同时改 CLI 帮助与报告，别让它悄悄漂移。"""
    assert DEFAULT_MAX_BYTES == 64 * 1024 * 1024
