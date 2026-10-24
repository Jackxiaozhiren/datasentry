"""Verify the published JSON Schema does not drift from real report models."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.resources import files

import pytest
from pydantic import ValidationError

from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.scan import DetectorRun, ReproducibilityInfo, ScanConfig, ScanRun
from datasentry_core.reporting import REPORT_SCHEMA_VERSION, build_report
from datasentry_core.reporting.schema import QualityReportV1, report_v1_json_schema

SCHEMA_PATH = files("datasentry_core.reporting").joinpath("report-v1.schema.json")


def _sample_report() -> dict:
    scan = ScanRun(
        id="scan_example",
        dataset_id="example",
        status="completed",
        config=ScanConfig(),
        fingerprint={
            "dataset_id": "example",
            "fingerprint_type": "full",
            "schema_hash": "test-sha256",
            "row_count": 3,
            "column_count": 1,
            "column_signature": [["number", "BIGINT"]],
        },
        reproducibility=ReproducibilityInfo(datasentry_version="2.0.0", seed=42),
    )
    return build_report(scan, [], [], None, generated_at=datetime(2026, 10, 24, 8, 0, tzinfo=UTC))


def test_schema_matches_typed_models_and_report_header() -> None:
    published = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert published == report_v1_json_schema()
    assert published["properties"]["report_schema_version"]["const"] == REPORT_SCHEMA_VERSION
    assert set(published["required"]) == set(_sample_report())


def test_real_report_validates_under_public_contract() -> None:
    report = _sample_report()
    validated = QualityReportV1.model_validate(report)
    assert validated.scan_run_id == report["scan"]["id"]
    assert validated.quality is None
    assert validated.model_dump(mode="json")["scan"]["dataset_id"] == "example"


def test_nonempty_detector_issue_and_quality_shapes_validate() -> None:
    report = _sample_report()
    report["detector_runs"] = [
        DetectorRun(
            id="dr_a",
            scan_run_id="scan_example",
            detector_id="null",
            detector_version="1.0",
            rows_scanned=3,
            duration_ms=1,
            issues_candidates=1,
        ).model_dump(mode="json")
    ]
    report["issues"] = [
        Issue(
            id="issue_a",
            scan_run_id="scan_example",
            issue_type="missing",
            title="Missing values",
            dataset_id="example",
            columns=["number"],
            confidence=1.0,
            priority_score=70.0,
            affected_count=1,
            affected_ratio=1 / 3,
        ).model_dump(mode="json")
    ]
    report["quality"] = QualityScore(overall=90.0).model_dump(mode="json")
    validated = QualityReportV1.model_validate(report)
    assert len(validated.issues) == len(validated.detector_runs) == 1
    assert validated.quality is not None and validated.quality.overall == 90.0


def test_naive_timestamp_output_is_backward_compatible() -> None:
    report = _sample_report()
    report["generated_at"] = datetime(2026, 8, 2).isoformat()
    assert QualityReportV1.model_validate(report).generated_at == report["generated_at"]


@pytest.mark.parametrize(
    ("key", "wrong_value"),
    [
        ("report_schema_version", "2.0"),
        ("reproducible", "not-a-boolean"),
        ("scan", {}),
    ],
)
def test_invalid_report_fields_are_rejected(key: str, wrong_value: object) -> None:
    report = _sample_report()
    report[key] = wrong_value
    with pytest.raises(ValidationError):
        QualityReportV1.model_validate(report)


def test_undocumented_envelope_fields_are_rejected() -> None:
    report = _sample_report()
    report["undocumented_field"] = "surprise"
    with pytest.raises(ValidationError):
        QualityReportV1.model_validate(report)
