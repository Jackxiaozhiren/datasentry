"""Versioned JSON Schema for the machine-readable quality report."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.scan import DetectorRun, ScanRun


class QualityReportV1(BaseModel):
    """Public envelope returned by reporting.build_report, schema v1.0."""

    model_config = ConfigDict(extra="forbid")

    report_schema_version: Literal["1.0"]
    datasentry_version: str
    scan_run_id: str
    generated_at: str = Field(
        description="ISO-8601 timestamp. Caller-supplied naive datetimes may omit the UTC offset."
    )
    reproducible: bool
    llm_used: bool
    scan: ScanRun
    detector_runs: list[DetectorRun]
    issues: list[Issue]
    quality: QualityScore | None


def report_v1_json_schema() -> dict[str, Any]:
    """Generate the portable contract using existing Pydantic models."""
    return QualityReportV1.model_json_schema(mode="serialization")
