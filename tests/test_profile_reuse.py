"""Step 80（ADR-080）增量画像列级 diff 测试。

覆盖验收标准：Profiler.reuse 复用列原样保留（对象同一性证明复制而非
重算）、仅新列重算、行数始终最新、被删列剔除、同名类型变更列重算、
dataset_id 漂移重建；client 侧加列场景仅重算新列（旧列字段与上次
sidecar 逐字段一致）、列集合一致全量、无 sidecar 全量、删列场景。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry_core.connectors import CsvConnector, DataSourceSpec, DataSourceType
from datasentry_core.engine import Profiler
from datasentry_core.models.profile import DatasetProfile


def _handle(path: Path, dataset_id: str = "ds_p"):
    spec = DataSourceSpec(
        source_type=DataSourceType.CSV, path=path, options={"dataset_id": dataset_id}
    )
    return CsvConnector().open(spec)


def _profile(path: Path, dataset_id: str = "ds_p", **kw) -> DatasetProfile:
    handle = _handle(path, dataset_id)
    try:
        return Profiler(handle, dataset_id).profile(**kw)
    finally:
        handle.close()


@pytest.fixture
def csv3(tmp_path: Path) -> Path:
    p = tmp_path / "base.csv"
    p.write_text("id,amount,label\n1,10.5,a\n2,,b\n3,7.5,a\n4,10.5,c\n,5.0,\n", encoding="utf-8")
    return p


class TestProfilerReuse:
    def test_reuse_keeps_columns_identical(self, csv3: Path) -> None:
        full = _profile(csv3)
        reuse = dict(full.column_profiles)
        p = _profile(csv3, reuse=reuse)
        assert p.column_profiles["id"] is reuse["id"]
        assert p.column_profiles["amount"] is reuse["amount"]
        assert p.column_profiles["label"] is reuse["label"]
        assert p.row_count == 5
        assert p.column_count == 3

    def test_reuse_only_computes_fresh_columns(self, csv3: Path) -> None:
        full = _profile(csv3)
        reuse = {"id": full.column_profiles["id"]}
        p = _profile(csv3, reuse=reuse)
        assert p.column_profiles["id"] is reuse["id"]
        assert p.column_profiles["amount"] is not reuse["id"]
        assert p.column_profiles["amount"].mean == pytest.approx((10.5 + 7.5 + 10.5 + 5.0) / 4)
        assert p.column_profiles["label"].distinct_count == 3

    def test_reuse_drops_deleted_columns(self, csv3: Path, tmp_path: Path) -> None:
        full = _profile(csv3)
        p = tmp_path / "dropped.csv"
        p.write_text("id,amount\n1,10.5\n2,7.5\n", encoding="utf-8")
        out = _profile(p, reuse=dict(full.column_profiles))
        assert set(out.column_profiles) == {"id", "amount"}
        assert out.column_profiles["id"] is full.column_profiles["id"]

    def test_reuse_updates_dataset_id(self, csv3: Path) -> None:
        full = _profile(csv3, dataset_id="old_ds")
        out = _profile(csv3, dataset_id="new_ds", reuse=dict(full.column_profiles))
        assert out.column_profiles["id"].dataset_id == "new_ds"
        assert full.column_profiles["id"].dataset_id == "old_ds"

    def test_reuse_empty_acts_as_full(self, csv3: Path) -> None:
        p = _profile(csv3, reuse={})
        assert p.column_count == 3
        assert p.column_profiles["amount"].mean == pytest.approx((10.5 + 7.5 + 10.5 + 5.0) / 4)


class TestClientColumnReuse:
    def _scan(self, ds: DataSentry, csv: Path) -> str:
        run, _runs, _issues = ds.scan_file(str(csv))
        return run.id

    def test_add_column_recomputes_only_new_column(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        csv = tmp_path / "data.csv"
        csv.write_text("id,amount\n1,10.5\n2,7.5\n3,\n4,10.5\n", encoding="utf-8")
        ds = DataSentry(project=ws)
        try:
            first_id = self._scan(ds, csv)
            first = ds.load_profile(first_id)
            assert first is not None
            old_amount = first["column_profiles"]["amount"]

            csv.write_text(
                "id,amount,category\n1,10.5,red\n2,7.5,red\n3,,blue\n4,10.5,blue\n",
                encoding="utf-8",
            )
            second_id = self._scan(ds, csv)
            second = ds.load_profile(second_id)
            assert second is not None
            cols = second["column_profiles"]
            assert set(cols) == {"id", "amount", "category"}
            # 旧列逐字段复用（与上次 sidecar 完全一致）
            assert cols["amount"] == old_amount
            assert cols["id"] == first["column_profiles"]["id"]
            assert cols["category"]["distinct_count"] == 2
        finally:
            ds.close()

    def test_drop_column_keeps_remaining_columns(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        csv = tmp_path / "data.csv"
        csv.write_text("id,amount,label\n1,10.5,a\n2,7.5,b\n", encoding="utf-8")
        ds = DataSentry(project=ws)
        try:
            first_id = self._scan(ds, csv)
            first = ds.load_profile(first_id)
            assert first is not None

            csv.write_text("id,amount\n1,10.5\n2,7.5\n", encoding="utf-8")
            second_id = self._scan(ds, csv)
            second = ds.load_profile(second_id)
            assert second is not None
            assert set(second["column_profiles"]) == {"id", "amount"}
            assert second["column_profiles"]["amount"] == first["column_profiles"]["amount"]
        finally:
            ds.close()

    def test_same_columns_full_profile(self, tmp_path: Path) -> None:
        """列集合一致（数据已变）→ 全量画像：旧列值更新而非复用旧快照。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        csv = tmp_path / "data.csv"
        csv.write_text("id,amount\n1,10.5\n2,7.5\n", encoding="utf-8")
        ds = DataSentry(project=ws)
        try:
            first_id = self._scan(ds, csv)
            first = ds.load_profile(first_id)
            assert first is not None

            csv.write_text("id,amount\n1,10.5\n2,99.0\n", encoding="utf-8")
            second_id = self._scan(ds, csv)
            second = ds.load_profile(second_id)
            assert second is not None
            assert second["column_profiles"]["amount"] != first["column_profiles"]["amount"]
        finally:
            ds.close()

    def test_no_sidecar_falls_back_to_full(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        csv = tmp_path / "data.csv"
        csv.write_text("id,amount\n1,10.5\n", encoding="utf-8")
        ds = DataSentry(project=ws)
        try:
            run_id = self._scan(ds, csv)
            profile = ds.load_profile(run_id)
            assert profile is not None
            assert profile["column_count"] == 2
        finally:
            ds.close()

    def test_sidecar_json_schema_stable(self, tmp_path: Path) -> None:
        """sidecar JSON 契约：DatasetProfile 序列化结构不变（model_dump）。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        csv = tmp_path / "data.csv"
        csv.write_text("id\n1\n2\n", encoding="utf-8")
        ds = DataSentry(project=ws)
        try:
            run_id = self._scan(ds, csv)
            raw = (ds.profiles_dir / f"{run_id}.json").read_text(encoding="utf-8")
            assert json.loads(raw)["column_count"] == 1
            assert json.loads(raw)["row_count"] == 2
        finally:
            ds.close()

    def test_type_changed_column_is_recomputed(self, tmp_path: Path) -> None:
        """同名不同类型列 → 排除在复用候选外（重算），未变列仍复用。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        csv = tmp_path / "data.csv"
        csv.write_text("id,amount\n1,10.5\n2,7.5\n", encoding="utf-8")
        ds = DataSentry(project=ws)
        try:
            r1 = self._scan(ds, csv)
            r2 = self._scan(ds, csv)
            first = ds.load_profile(r1)
            assert first is not None
            # 伪造上次 sidecar：id 物理类型改为 VARCHAR（模拟类型变更）
            sidecar = ds.profiles_dir / f"{r1}.json"
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            data["column_profiles"]["id"]["physical_type"] = "VARCHAR"
            sidecar.write_text(json.dumps(data), encoding="utf-8")

            handle = _handle(csv)
            try:
                cands = ds._column_reuse_candidates(first["dataset_id"], handle, scan_run_id=r2)
            finally:
                handle.close()
            assert cands is not None
            assert "id" not in cands
            assert "amount" in cands
            assert cands["amount"].physical_type == "DOUBLE"
        finally:
            ds.close()

    def test_unchanged_schema_returns_none(self, tmp_path: Path) -> None:
        """列集合一致 → 返回 None（全量画像，数据可能变更）。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        csv = tmp_path / "data.csv"
        csv.write_text("id,amount\n1,10.5\n", encoding="utf-8")
        ds = DataSentry(project=ws)
        try:
            r1 = self._scan(ds, csv)
            r2 = self._scan(ds, csv)
            handle = _handle(csv)
            try:
                cands = ds._column_reuse_candidates(
                    ds.get_scan(r1).dataset_id, handle, scan_run_id=r2
                )
            finally:
                handle.close()
            assert cands is None
        finally:
            ds.close()
