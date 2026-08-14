"""Step 77（ADR-077）增量画像测试：client.scan_file(incremental=True)。

覆盖契约：未变更 → 复用上次 scan_run（id 相同、不建新 run、Issue 与
画像随 scan_run_id 复用）；变更 → 全量重扫（新 run）；无基准（首次
扫描）→ 全扫；远程 DSN → 降级全扫；上次为抽样（sampled 档指纹无
file_sha256）→ 全扫；默认 incremental=False 行为不变。
"""

from __future__ import annotations

from pathlib import Path

from datasentry.client import DataSentry


def _write_csv(path: Path, rows: list[str]) -> None:
    path.write_text("id,amount\n" + "\n".join(rows) + "\n", encoding="utf-8")


def _scan(client: DataSentry, path: Path, incremental: bool = False):
    return client.scan_file(str(path), config=None, incremental=incremental)


class TestIncremental:
    def test_unchanged_reuses_previous_scan(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        path = tmp_path / "data.csv"
        _write_csv(path, ["1,10", "2,20"])
        client = DataSentry(project=ws)
        try:
            first, _, first_issues = _scan(client, path)
            assert first.status == "completed"
            runs_before = client._store.list_scan_runs()
            assert len(runs_before) == 1

            second, runs, issues = _scan(client, path, incremental=True)
            assert second.id == first.id
            assert client._store.list_scan_runs().__len__() == 1
            assert sorted(i.id for i in issues) == sorted(i.id for i in first_issues)
            assert len(issues) == len(first_issues)
            assert runs
        finally:
            client.close()

    def test_changed_rescans_new_run(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        path = tmp_path / "data.csv"
        _write_csv(path, ["1,10", "2,20"])
        client = DataSentry(project=ws)
        try:
            first, _, _ = _scan(client, path)
            _write_csv(path, ["1,10", "2,20", "3,30"])
            second, _, _ = _scan(client, path, incremental=True)
            assert second.id != first.id
            assert len(client._store.list_scan_runs()) == 2
        finally:
            client.close()

    def test_first_scan_no_baseline_full_scan(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        path = tmp_path / "data.csv"
        _write_csv(path, ["1,10"])
        client = DataSentry(project=ws)
        try:
            scan, runs, _ = _scan(client, path, incremental=True)
            assert scan.status == "completed"
            assert runs
            assert len(client._store.list_scan_runs()) == 1
        finally:
            client.close()

    def test_remote_dsn_incremental_cached_none(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        client = DataSentry(project=ws)
        try:
            assert (
                client._incremental_cached("postgresql://u:p@localhost:5432/db", "db", "t") is None
            )
            assert client._incremental_cached("s3://bucket/f.csv", "ds", None) is None
        finally:
            client.close()

    def test_sampled_previous_fingerprint_falls_back_full_scan(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        path = tmp_path / "data.csv"
        _write_csv(path, ["1,10", "2,20"])
        client = DataSentry(project=ws)
        try:
            from datasentry_core.models.scan import SamplingConfig, ScanConfig

            first, _, _ = client.scan_file(
                str(path),
                config=ScanConfig(sampling=SamplingConfig(method="reservoir", sample_size=1)),
            )
            assert first.fingerprint.file_sha256 is None
            second, _, _ = _scan(client, path, incremental=True)
            assert second.id != first.id
            assert second.fingerprint.file_sha256 is not None
        finally:
            client.close()

    def test_default_incremental_false_full_scan(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        path = tmp_path / "data.csv"
        _write_csv(path, ["1,10", "2,20"])
        client = DataSentry(project=ws)
        try:
            first, _, _ = _scan(client, path)
            second, _, _ = _scan(client, path)
            assert second.id != first.id
            assert len(client._store.list_scan_runs()) == 2
        finally:
            client.close()
