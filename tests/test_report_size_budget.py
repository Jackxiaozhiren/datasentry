"""PRD R-04: report size ceiling and the "no raw rows in payload" ratchet.

Kept out of `tests/test_reporting_html.py` on purpose: that file is the HANDOFF
section 0 baseline instrument (9 passed), and adding cases there would change
the preflight reading every future session relies on.

The budget ceiling lives in exactly one place, the R-04 table row in
`docs/BENCHMARKS.md`. The probe evidence in `benchmarks/` is read back, so
a ceiling written before measuring (or a ceiling quietly shrunk later) fails.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from datasentry_core import __version__
from datasentry_core.models.enums import QualityDimension, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.scan import DetectorRun, ReproducibilityInfo, ScanConfig, ScanRun
from datasentry_core.reporting import build_report
from datasentry_core.reporting.html import render_html

_REPO = Path(__file__).resolve().parents[1]
_BENCHMARKS = _REPO / "docs" / "BENCHMARKS.md"
_EVIDENCE = _REPO / "benchmarks" / "r04_report_size_probe.jsonl"
_ISSUE_DATA_RE = re.compile(r'<script type="application/json" id="issue-data">(.*?)</script>', re.S)
_BUDGET_ROW_RE = re.compile(
    r"^\|\s*1 000 000\s*x\s*6\s*\|\s*<=\s*(\d+(?:\.\d+)?)\s*KiB\s*"
    r"\|\s*<=\s*(\d+(?:\.\d+)?)\s*\|",
    re.M,
)
_BARE_SECONDS_RE = re.compile(r"\b\d+\.\d+\s*s\b")


def _report() -> dict[str, Any]:
    """A self-contained report fixture built only from public core models.

    Kept local on purpose: the richer ``tests/test_reporting_html.py`` helper in
    the working tree also asserts layout invariants that are not on ``main`` yet.
    When that batch lands, this constructor can be replaced by a shared fixture.
    """
    scan = ScanRun(
        id="scan_abc",
        dataset_id="orders",
        status="completed",
        config=ScanConfig(),
        fingerprint={
            "dataset_id": "orders",
            "fingerprint_type": "full",
            "schema_hash": "h1",
            "row_count": 100,
            "column_count": 2,
            "column_signature": [["id", "BIGINT"], ["email", "VARCHAR"]],
        },
        issues_count={s: 0 for s in Severity},
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        reproducibility=ReproducibilityInfo(
            datasentry_version=__version__,
            detector_versions={"numeric_outlier": "1.0.0"},
            seed=42,
            scanned_at=datetime.now(UTC),
        ),
    )
    runs = [
        DetectorRun(
            id="dr_1",
            scan_run_id="scan_abc",
            detector_id="numeric_outlier",
            detector_version="1.0.0",
            status="completed",
            rows_scanned=100,
            duration_ms=5,
            issues_candidates=1,
        )
    ]
    issues = [
        Issue(
            id="iss_1",
            scan_run_id="scan_abc",
            issue_type="numeric_outlier",
            title="Outlier in amount",
            dataset_id="orders",
            columns=["amount"],
            quality_dimensions=[QualityDimension.VALIDITY],
            severity=Severity.HIGH,
            confidence=0.9,
            priority_score=78.5,
            affected_count=2,
            affected_ratio=0.02,
            detector_ids=["numeric_outlier"],
        )
    ]
    quality = QualityScore(
        overall=88.2,
        dimensions={"validity": 76.6},
        weights={"validity": 1.0},
        calculation_notes="dimension = 100 * (1 - ...)",
        dimension_contributions={"validity": {"iss_1": 0.375}},
    )
    return build_report(scan, runs, issues, quality, generated_at=datetime(2026, 8, 2))


def _payload(html: str) -> list[dict[str, Any]]:
    match = _ISSUE_DATA_RE.search(html)
    assert match is not None, "issue-data payload is missing from the report"
    rows = json.loads(match.group(1))["issues"]
    assert isinstance(rows, list)
    return rows


def _budget_from(text: str) -> tuple[float, float]:
    match = _BUDGET_ROW_RE.search(text)
    assert match is not None, "docs/BENCHMARKS.md has no R-04 report budget row yet"
    return float(match.group(1)), float(match.group(2))


def _budget() -> tuple[float, float]:
    return _budget_from(_BENCHMARKS.read_text(encoding="utf-8"))


def _assert_ceiling_covers_evidence(text: str) -> None:
    """A budget row is only honest if it brackets every measured arm."""
    kib, ratio = _budget_from(text)
    arms = _evidence()
    largest_kib = max(a["html_bytes"] for a in arms) / 1024
    largest_ratio = max(a["render_secs"] / a["scan_secs"] for a in arms)
    assert kib >= largest_kib, f"ceiling {kib} KiB is below measured {largest_kib:.1f} KiB"
    assert ratio >= largest_ratio, f"ratio ceiling {ratio} is below measured {largest_ratio}"
    assert kib <= 4 * largest_kib, "a ceiling far above every reading is not a gate"
    assert ratio <= 10 * largest_ratio, "a ratio far above every reading is not a gate"


def _evidence() -> list[dict[str, Any]]:
    assert _EVIDENCE.exists(), "probe evidence must land in the repo before a ceiling is declared"
    rows = [
        json.loads(line)
        for line in _EVIDENCE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) >= 3, "at least three measured arms are needed to call this a distribution"
    return rows


def _assert_payload_bounded(report: dict[str, Any], html: str) -> None:
    """The ratchet: one payload element per issue, each shaped like an issue."""
    rows = _payload(html)
    assert rows, "an empty payload would make the bound vacuous"
    assert len(rows) == len(report["issues"]), (
        f"payload carries {len(rows)} rows for {len(report['issues'])} issues"
    )
    for row in rows:
        assert {"id", "issue_type", "affected", "severity"} <= set(row), (
            "a raw data row was inlined into the issue payload"
        )


def _assert_bytes_within_ceiling(html: str) -> None:
    kib, _ = _budget()
    size = len(html.encode("utf-8"))
    assert size <= kib * 1024, f"report is {size} bytes, above the {kib} KiB ceiling"


class TestPayloadBound:
    def test_payload_row_count_equals_issue_count(self) -> None:
        report = _report()
        _assert_payload_bounded(report, render_html(report))

    def test_payload_is_independent_of_dataset_size(self) -> None:
        small = _report()
        large = copy.deepcopy(small)
        fingerprint = large["scan"]["fingerprint"]
        fingerprint["row_count"] = 1_000_000
        fingerprint["column_count"] = 30
        assert fingerprint["row_count"] >= 100 * small["scan"]["fingerprint"]["row_count"]

        a = _payload(render_html(small))
        b = _payload(render_html(large))
        assert a and b
        assert len(a) == len(b) == len(small["issues"]), (
            "the payload must not grow with the source table"
        )

    def test_probe_evidence_confirms_the_constant_across_two_orders(self) -> None:
        arms = _evidence()
        assert max(a["rows"] for a in arms) >= 100 * min(a["rows"] for a in arms)
        for arm in arms:
            assert arm["payload_rows"] == arm["issue_count"], arm
            assert arm["row_count_in_report"] == arm["rows"], arm

    def test_bound_fires_when_raw_rows_are_injected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Injection control: the bound must be able to go red, not just pass."""
        from datasentry_core.reporting import interactive

        report = _report()
        original = interactive.issue_rows

        def bloated(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return original(*args, **kwargs) * 50

        monkeypatch.setattr(interactive, "issue_rows", bloated)
        with pytest.raises(AssertionError, match="payload carries"):
            _assert_payload_bounded(report, render_html(report))

    def test_bound_fires_when_a_raw_row_shape_is_injected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datasentry_core.reporting import interactive

        report = _report()

        def raw_shaped(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            # Build the same number of rows as there are issues, so this control
            # isolates the *shape* guard from any product-side row inflation.
            source = args[0] if args else kwargs["report"]
            return [
                {"id": i, "price": 1.0, "name": "raw cell", "dimensions": []}
                for i in range(len(source["issues"]))
            ]

        monkeypatch.setattr(interactive, "issue_rows", raw_shaped)
        with pytest.raises(AssertionError, match="raw data row"):
            _assert_payload_bounded(report, render_html(report))


class TestBudgetRow:
    def test_budget_row_exists_and_is_parsed(self) -> None:
        kib, ratio = _budget()
        assert kib > 0 and ratio > 0

    def test_evidence_is_committable_not_ignored(self) -> None:
        """A gate reading an ignored local file passes here, fails on a clean clone.

        This is a measured correction, not a hypothetical: the first version of
        this gate read `AUDIT/artifacts/...`, and `.gitignore` excludes the whole
        `AUDIT/` tree (zero files under it are tracked).
        """
        assert _EVIDENCE.exists(), "the evidence the gate reads back must exist"
        rel = _EVIDENCE.relative_to(_REPO).as_posix()
        assert not rel.startswith("AUDIT/"), rel
        ignore_entries = {
            line.strip()
            for line in (_REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        assert "AUDIT/" in ignore_entries, "the reason for this guard disappeared"
        if (_REPO / ".git").exists():
            ignored = subprocess.run(
                ["git", "check-ignore", "-q", rel],
                cwd=_REPO,
                capture_output=True,
                check=False,
            )
            assert ignored.returncode == 1, f"{rel} is ignored; a clean clone loses the gate"

        text = _BENCHMARKS.read_text(encoding="utf-8")
        start = text.find("## Report size")
        assert start >= 0
        end = text.find("\n## ", start + 3)
        section = text[start:] if end < 0 else text[start:end]
        assert rel in section, "the documented evidence path drifted from the gate's path"
        assert "AUDIT/artifacts/r04_report_size_probe.jsonl" not in section

    def test_ceiling_is_derived_from_the_probe_not_guessed(self) -> None:
        _assert_ceiling_covers_evidence(_BENCHMARKS.read_text(encoding="utf-8"))

    def test_budget_section_publishes_ratios_not_bare_seconds(self) -> None:
        text = _BENCHMARKS.read_text(encoding="utf-8")
        start = text.find("## Report size")
        assert start >= 0, "the report-size section is missing"
        end = text.find("\n## ", start + 3)
        section = text[start:] if end < 0 else text[start:end]
        assert not _BARE_SECONDS_RE.search(section), (
            "publish the ceiling and the ratio, not ambient-host seconds"
        )

    def test_budget_drift_is_detected(self) -> None:
        """A shrunk ceiling must fail the same check the real document passes."""
        text = _BENCHMARKS.read_text(encoding="utf-8")
        match = _BUDGET_ROW_RE.search(text)
        assert match is not None
        drifted = text[: match.start(1)] + "1" + text[match.end(1) :]
        assert _budget_from(drifted)[0] == 1.0
        with pytest.raises(AssertionError, match="below measured"):
            _assert_ceiling_covers_evidence(drifted)

    def test_bytes_ceiling_fires_on_an_oversized_report(self) -> None:
        report = _report()
        report["issues"] = report["issues"] * 200
        with pytest.raises(AssertionError, match="above the"):
            _assert_bytes_within_ceiling(render_html(report))
