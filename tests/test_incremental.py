"""Step 77（ADR-077）增量画像测试：client.scan_file(incremental=True)。

覆盖契约：未变更 → 复用上次 scan_run（id 相同、不建新 run、Issue 与
画像随 scan_run_id 复用）；变更 → 全量重扫（新 run）；无基准（首次
扫描）→ 全扫；远程 DSN → 降级全扫；上次为抽样（sampled 档指纹无
file_sha256）→ 全扫；默认 incremental=False 行为不变。
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


class TestNonFiniteNumbersScan:
    """G-2：一份含 `nan` 的普通 CSV 让 `scan_file` 整体抛异常，四个面都读到 500。

    画像由门面（`client._save_profile`）发起，所以这条必须在门面层钉：核心 `ScanRunner`
    自己不画像，只在核心层测会漏掉真实故障面。
    """

    def test_a_nan_bearing_numeric_column_scans_end_to_end(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        path = ws / "prices.csv"
        _write_csv(path, ["1,10.5", "2,nan", "3,7.5"])
        client = DataSentry(project=ws)
        try:
            run, _dataset, issues = _scan(client, path)
            assert run.status == "completed", run.status
            assert run.fingerprint.row_count == 3
            assert issues, "a file with a whitespace-free NaN column should still be scanned"
        finally:
            client.close()

    def test_the_stored_profile_sums_only_finite_values(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        path = ws / "prices.csv"
        _write_csv(path, ["1,10.5", "2,nan", "3,7.5"])
        client = DataSentry(project=ws)
        try:
            run, _dataset, _issues = _scan(client, path)
            sidecar = client.load_profile(run.id)
            assert sidecar is not None, "the profile sidecar was not written"
            cols = (
                {c["column_name"]: c for c in sidecar["column_profiles"].values()}
                if isinstance(sidecar["column_profiles"], dict)
                else {c["column_name"]: c for c in sidecar["column_profiles"]}
            )
            amount = cols["amount"]
            assert amount["mean"] == pytest.approx(9.0), f"NaN averaged into the mean: {amount}"
            assert amount["max"] == pytest.approx(10.5), amount
            assert amount["min"] == pytest.approx(7.5), amount
        finally:
            client.close()


class TestScanSurvivesNonFiniteNumbers:
    """G-2 的门面层判据：一份含 `nan` 的普通 CSV 必须能被扫完，而不是留下一个 failed run。

    画像（`Profiler`）与检测器（`numeric.py` 的 quantile/median/mad/min/max）各自算聚合，
    两侧都必须只在有限值上算——只修画像时扫描仍然失败，因为检测器把 `nan` 当边界拼进了 SQL
    （`Binder Error: Referenced column "nan" not found`）。
    """

    def _csv(self, tmp_path: Path) -> Path:
        path = tmp_path / "prices.csv"
        path.write_text(
            "name,price\n  Alice ,10.5\nBob,nan\nCarol,7.5\nDave,9.0\n",
            encoding="utf-8",
        )
        return path

    def test_a_nan_bearing_column_scans_to_completion(self, tmp_path: Path) -> None:
        client = DataSentry(project=tmp_path / "ws")
        try:
            run, _dataset, issues = client.scan_file(str(self._csv(tmp_path)))
            assert run.status == "completed", f"non-finite value failed the scan: {run.error}"
            assert sum(run.issues_count.values()) > 0
            assert issues
        finally:
            client.close()

    def test_the_outlier_detectors_do_not_splice_nan_into_their_bounds(
        self, tmp_path: Path
    ) -> None:
        client = DataSentry(project=tmp_path / "ws")
        try:
            run, _dataset, issues = client.scan_file(str(self._csv(tmp_path)))
            types = {i.issue_type for i in issues}
            assert (
                "leading_or_trailing_whitespace"
                in ",".join(",".join(i.detector_ids) for i in issues)
                or "string_format" in types
            )
            assert run.error in (None, ""), f"a detector still failed: {run.error}"
        finally:
            client.close()


class TestNaNMajority:
    """G-2 的第二个形态：NaN 占多数时 `median`/`mad` 本身就返回 NaN（本机实测）。

    `median([nan,1.0,nan,2.0,nan])` → `nan`、`mad(...)` → `nan`，于是 modified z-score 检测器
    把 `nan` 当尺度拼进 SQL；NaN 不到半数时它也不报错但**统计值是假的**
    （`median([10.5,nan,7.5,9.0])` 给 9.75，真值 9.0）。这两种错都不该由一个合法文件触发。
    """

    def test_a_column_mostly_nan_still_scans(self, tmp_path: Path) -> None:
        path = tmp_path / "temps.csv"
        path.write_text(
            "label,temp\n  A ,nan\nB,nan\n C ,2.0\nD,nan\n E ,1.0\n",
            encoding="utf-8",
        )
        client = DataSentry(project=tmp_path / "ws")
        try:
            run, _dataset, _issues = client.scan_file(str(path))
            assert run.status == "completed", f"NaN-majority column failed the scan: {run.error}"
            assert run.error in (None, ""), run.error
        finally:
            client.close()

    def test_the_median_of_a_nan_bearing_column_excludes_nan_from_the_value(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "amounts.csv"
        path.write_text("id,amount\n1,10.5\n2,nan\n3,7.5\n4,9.0\n", encoding="utf-8")
        client = DataSentry(project=tmp_path / "ws")
        try:
            run, _dataset, _issues = client.scan_file(str(path))
            sidecar = client.load_profile(run.id)
            assert sidecar is not None
            cols = {
                c["column_name"]: c
                for c in (
                    sidecar["column_profiles"].values()
                    if isinstance(sidecar["column_profiles"], dict)
                    else sidecar["column_profiles"]
                )
            }
            # unguarded DuckDB answers 9.75 for this column; the finite values median to 9.0
            assert cols["amount"]["median"] == pytest.approx(9.0), cols["amount"]
        finally:
            client.close()
