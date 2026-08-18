"""Step 9 元数据存储测试：ADR-010 布局、Schema 迁移、扫描结果往返、级联删除。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from datasentry_core.models.enums import (
    EvidenceType,
    QualityDimension,
    RiskLevel,
    Severity,
)
from datasentry_core.models.evidence import Evidence, EvidenceProvenance
from datasentry_core.models.fingerprint import DatasetFingerprint
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import (
    DetectorRun,
    ReproducibilityInfo,
    ScanConfig,
    ScanRun,
)
from datasentry_core.storage import (
    MetadataStore,
    global_data_dir,
    project_data_dir,
    project_db_path,
)
from datasentry_core.storage.schema import PLACEHOLDER_TABLES, SCHEMA_VERSION


def _sample_scan() -> tuple[ScanRun, list[DetectorRun], list[Issue]]:
    scan = ScanRun(
        id="scan_1",
        dataset_id="ds_1",
        status="completed",
        config=ScanConfig(detectors=["iqr_outlier"]),
        fingerprint=DatasetFingerprint(
            dataset_id="ds_1",
            fingerprint_type="full",
            file_sha256="abc123",
            schema_hash="sch_1",
            row_count=100,
            column_count=3,
            column_signature=[("id", "BIGINT"), ("v", "DOUBLE"), ("x", "VARCHAR")],
        ),
        issues_count={
            Severity.INFO: 0,
            Severity.LOW: 1,
            Severity.MEDIUM: 0,
            Severity.HIGH: 0,
            Severity.CRITICAL: 0,
        },
        reproducibility=ReproducibilityInfo(
            datasentry_version="0.1.0",
            detector_versions={"iqr_outlier": "1.0.0"},
            seed=42,
        ),
    )
    runs = [
        DetectorRun(
            id="dr_1",
            scan_run_id="scan_1",
            detector_id="iqr_outlier",
            detector_version="1.0.0",
            status="completed",
            rows_scanned=100,
            duration_ms=12,
            issues_candidates=1,
        )
    ]
    issues = [
        Issue(
            id="iss_1",
            scan_run_id="scan_1",
            issue_type="numeric_outlier",
            title="Numeric outlier in v",
            dataset_id="ds_1",
            columns=["v"],
            quality_dimensions=[QualityDimension.VALIDITY],
            severity=Severity.LOW,
            confidence=0.9,
            priority_score=62.5,
            false_positive_risk=RiskLevel.MEDIUM,
            affected_count=2,
            affected_ratio=0.02,
            affected_row_ids=["1", "2"],
            detector_ids=["iqr_outlier"],
            evidence=[
                Evidence(
                    evidence_id="ev_1",
                    evidence_type=EvidenceType.STATISTICAL_MEASURE,
                    detector_id="iqr_outlier",
                    detector_version="1.0.0",
                    description="q75 + 1.5*IQR 越界",
                    data={"limit": 42.0, "value": 1000.0},
                    provenance=EvidenceProvenance(scan_run_id="scan_1"),
                )
            ],
        )
    ]
    return scan, runs, issues


class TestLayout:
    def test_project_data_dir(self, tmp_path: Path) -> None:
        assert project_data_dir(tmp_path) == tmp_path / ".datasentry"

    def test_project_db_path(self, tmp_path: Path) -> None:
        assert project_db_path(tmp_path) == tmp_path / ".datasentry" / "metadata.db"

    def test_global_data_dir_is_platform_scoped(self, monkeypatch) -> None:
        monkeypatch.setenv("DATASENTRY_HOME", "/custom/home")
        assert global_data_dir() == Path("/custom/home")


class TestSchema:
    def test_migrate_idempotent(self, tmp_path: Path) -> None:
        store = MetadataStore(tmp_path / "m.db")
        store.close()
        MetadataStore(tmp_path / "m.db").close()  # 二次打开不报错

    def test_schema_version(self, tmp_path: Path) -> None:
        conn = sqlite3.connect(tmp_path / "v.db")
        migrate_test = __import__("datasentry_core.storage.schema", fromlist=["migrate"]).migrate
        migrate_test(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        cols = {r[1] for r in conn.execute("PRAGMA table_info(repair_runs)")}
        assert "source_scan_run_id" in cols
        conn.close()

    def test_placeholder_tables_exist(self, tmp_path: Path) -> None:
        store = MetadataStore(tmp_path / "p.db")
        with store._conn:
            rows = store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        tables = {r[0] for r in rows}
        assert set(PLACEHOLDER_TABLES) <= tables
        store.close()

    def test_newer_schema_rejected(self, tmp_path: Path) -> None:
        conn = sqlite3.connect(tmp_path / "new.db")
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError):
            MetadataStore(tmp_path / "new.db")


class TestStoreRoundtrip:
    def test_save_and_load_scan(self, tmp_path: Path) -> None:
        store = MetadataStore(tmp_path / "meta.db")
        scan, runs, issues = _sample_scan()
        store.save_scan(scan, runs, issues)

        loaded = store.get_scan_run("scan_1")
        assert loaded is not None
        assert loaded.id == scan.id
        assert loaded.dataset_id == "ds_1"
        assert loaded.status == "completed"
        assert loaded.config.detectors == ["iqr_outlier"]
        assert loaded.issues_count[Severity.LOW] == 1
        assert loaded.reproducibility.datasentry_version == "0.1.0"
        assert loaded.started_at is not None

        loaded_runs = store.get_detector_runs("scan_1")
        assert len(loaded_runs) == 1
        assert loaded_runs[0].detector_id == "iqr_outlier"
        assert loaded_runs[0].duration_ms == 12

        loaded_issues = store.get_issues("scan_1")
        assert len(loaded_issues) == 1
        issue = loaded_issues[0]
        assert issue.priority_score == 62.5
        assert issue.columns == ["v"]
        assert issue.affected_row_ids == ["1", "2"]
        assert issue.false_positive_risk == RiskLevel.MEDIUM
        assert len(issue.evidence) == 1
        assert issue.evidence[0].data == {"limit": 42.0, "value": 1000.0}
        assert issue.evidence[0].provenance is not None
        assert issue.evidence[0].provenance.scan_run_id == "scan_1"
        store.close()

    def test_list_scan_runs_filter(self, tmp_path: Path) -> None:
        store = MetadataStore(tmp_path / "meta.db")
        scan, runs, issues = _sample_scan()
        store.save_scan(scan, runs, issues)
        assert len(store.list_scan_runs()) == 1
        assert len(store.list_scan_runs(dataset_id="ds_1")) == 1
        assert len(store.list_scan_runs(dataset_id="nope")) == 0
        assert store.get_scan_run("missing") is None
        store.close()

    def test_dataset_registered_implicitly(self, tmp_path: Path) -> None:
        store = MetadataStore(tmp_path / "meta.db")
        scan, runs, issues = _sample_scan()
        store.save_scan(scan, runs, issues)
        with store._conn:
            row = store._conn.execute(
                "SELECT id, project_id FROM datasets WHERE id = 'ds_1'"
            ).fetchone()
        assert row is not None
        assert row["project_id"] == "local"
        store.close()

    def test_cascade_delete_from_scan(self, tmp_path: Path) -> None:
        store = MetadataStore(tmp_path / "meta.db")
        scan, runs, issues = _sample_scan()
        store.save_scan(scan, runs, issues)
        with store._conn:
            store._conn.execute("DELETE FROM scan_runs WHERE id = 'scan_1'")
        assert store.get_issues("scan_1") == []
        assert store.get_detector_runs("scan_1") == []
        store.close()
