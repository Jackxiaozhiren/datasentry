"""R-22: cold start budget, derived from committed probe evidence and gated here.

The PRD asks for a ceiling on the **median** of at least three clean virtualenv
runs, plus a record of the network condition, the Python version and whether a
mirror was used. Everything below re-reads the committed evidence instead of
trusting a number typed into prose.

Two properties of this metric are encoded on purpose:

* Failed arms stay in the evidence file. A budget that deletes its failures is
  not a budget, so only successful arms feed the median while failed ones must
  still be well-formed records.
* `datasentry demo` writes to `tempfile.mkdtemp()`, so a probe that globs the
  cwd sees no report and reports "none produced" while one exists (that mistake
  was made, fixed, and documented in `docs/BENCHMARKS.md`). Successful arms must
  therefore carry the byte size of the report the demo actually wrote.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[1]
_DOC = _REPO / "docs" / "BENCHMARKS.md"
_EVIDENCE = _REPO / "benchmarks" / "r22_cold_start_probe.jsonl"
_OFFICIAL_INDEX = "https://pypi.org/simple/"

_ROW = re.compile(r"^\|[^|\n]*Clean venv[^|\n]*\|\s*<=\s*(\d+)\s*s\s*\|\s*$", re.MULTILINE)


def _arms() -> list[dict[str, Any]]:
    lines = [line for line in _EVIDENCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    arms: list[dict[str, Any]] = [json.loads(line) for line in lines]
    return arms


def _successful(arms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Successful = the whole path ran: venv, install, demo, and a real report."""
    assert len(arms) >= 4, f"evidence should keep failed arms too, found only {len(arms)} lines"
    ok: list[dict[str, Any]] = []
    for arm in arms:
        label = f"attempt {arm.get('attempt')} arm {arm.get('arm')}"
        network = arm.get("network") or {}
        assert network.get("index_url") == _OFFICIAL_INDEX, f"{label}: not the official index"
        assert network.get("mirror_used") is False, f"{label}: a mirror invalidates this arm"
        assert arm.get("python_version"), f"{label}: Python version must be recorded"
        if arm.get("install_rc") != 0 or arm.get("demo_rc") != 0:
            assert isinstance(arm.get("install_rc"), int), f"{label}: a failed arm needs a real rc"
            continue
        assert isinstance(arm.get("total_secs"), (int, float)), f"{label}: no total wall clock"
        assert arm["total_secs"] > 0, f"{label}: a zero reading is not a measurement"
        assert (arm.get("report_bytes") or 0) > 0, f"{label}: no report byte size was recorded"
        ok.append(arm)
    assert len(ok) >= 3, f"R-22 needs at least 3 successful arms, found {len(ok)}"
    return ok


def _ceiling() -> int:
    match = _ROW.search(_DOC.read_text(encoding="utf-8"))
    assert match is not None, "docs/BENCHMARKS.md has no R-22 cold-start budget row"
    return int(match.group(1))


class TestColdStartBudget:
    def test_evidence_is_committed_next_to_the_instrument(self) -> None:
        assert _EVIDENCE.is_file(), _EVIDENCE
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(_EVIDENCE)],
            cwd=_REPO,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1, (
            "the evidence path is ignored, so a clean clone cannot read it"
        )

    def test_document_points_at_the_same_evidence_file(self) -> None:
        text = _DOC.read_text(encoding="utf-8")
        assert "benchmarks/r22_cold_start_probe.jsonl" in text
        assert "probe_r22_cold_start.py" in text

    def test_at_least_three_successful_arms(self) -> None:
        successful = _successful(_arms())
        keys = {(arm["attempt"], arm["arm"]) for arm in successful}
        assert len(keys) == len(successful), "duplicated arm ids"

    def test_every_successful_arm_recorded_a_real_report(self) -> None:
        for arm in _successful(_arms()):
            assert arm["report_bytes"] > 0, arm
            assert arm["report_path"], "the report path must be recorded, not inferred"

    def test_network_python_and_mirror_are_recorded(self) -> None:
        for arm in _successful(_arms()):
            network = arm["network"]
            assert network.get("simple_page_http") == 200, network
            assert "pip_index_url_env" in network and "pip_extra_index_url_env" in network

    def test_median_is_within_the_declared_ceiling(self) -> None:
        successful = _successful(_arms())
        median = statistics.median(arm["total_secs"] for arm in successful)
        ceiling = _ceiling()
        assert median <= ceiling, f"median {median:.1f}s exceeds the declared ceiling {ceiling}s"

    def test_ceiling_is_bounded_not_inflated(self) -> None:
        successful = _successful(_arms())
        median = statistics.median(arm["total_secs"] for arm in successful)
        ceiling = _ceiling()
        assert ceiling <= 4 * median, (
            f"ceiling {ceiling}s is more than 4x the measured median {median:.1f}s, "
            "which is not a gate"
        )

    def test_document_states_the_worst_arm_and_the_median(self) -> None:
        """The published numbers must be the ones in the evidence, not remembered ones."""
        successful = _successful(_arms())
        median = statistics.median(arm["total_secs"] for arm in successful)
        worst = max(arm["total_secs"] for arm in successful)
        text = _DOC.read_text(encoding="utf-8")
        assert f"{median:.1f} s" in text, f"the median {median:.1f}s is not published"
        assert f"{worst:.1f} s" in text, f"the worst arm {worst:.1f}s is not published"


class TestGateCanGoRed:
    def _arm(self, **overrides: Any) -> dict[str, Any]:
        arm: dict[str, Any] = {
            "attempt": "X",
            "arm": 1,
            "venv_rc": 0,
            "install_rc": 0,
            "demo_rc": 0,
            "total_secs": 300.0,
            "report_bytes": 36875,
            "report_path": "/scratch/report.html",
            "python_version": "Python 3.13.12",
            "network": {
                "index_url": _OFFICIAL_INDEX,
                "simple_page_http": 200,
                "pip_index_url_env": None,
                "pip_extra_index_url_env": None,
                "mirror_used": False,
            },
        }
        arm.update(overrides)
        return arm

    def test_failed_arms_do_not_count_towards_the_three(self) -> None:
        with pytest.raises(AssertionError, match="at least 3 successful"):
            _successful(
                [
                    self._arm(attempt="A", arm=1, install_rc=1, total_secs=None),
                    self._arm(attempt="A", arm=2, demo_rc=1),
                    self._arm(attempt="A", arm=3),
                    self._arm(attempt="A", arm=4),
                ]
            )

    def test_a_mirror_arm_is_rejected_outright(self) -> None:
        mirrored = self._arm(arm=2, network={**self._arm()["network"], "mirror_used": True})
        with pytest.raises(AssertionError, match="mirror"):
            _successful([self._arm(), mirrored, self._arm(arm=3), self._arm(arm=4)])

    def test_a_successful_arm_without_a_report_is_rejected(self) -> None:
        with pytest.raises(AssertionError, match="no report byte size"):
            _successful(
                [
                    self._arm(),
                    self._arm(arm=2, report_bytes=0),
                    self._arm(arm=3),
                    self._arm(arm=4),
                ]
            )

    def test_two_arms_are_not_enough(self) -> None:
        with pytest.raises(AssertionError, match=r"at least 3 successful|keep failed arms"):
            _successful([self._arm(), self._arm(arm=2)])

    def test_a_shrunk_ceiling_on_a_copy_fails(self, tmp_path: Path) -> None:
        text = _DOC.read_text(encoding="utf-8")
        match = _ROW.search(text)
        assert match is not None
        copy_path = tmp_path / "BENCHMARKS.md"
        copy_path.write_text(text.replace(match.group(1), "1", 1), encoding="utf-8")
        shrunk = _ROW.search(copy_path.read_text(encoding="utf-8"))
        assert shrunk is not None and int(shrunk.group(1)) == 1
        median = statistics.median(arm["total_secs"] for arm in _successful(_arms()))
        assert int(shrunk.group(1)) < median, "a shrunk ceiling must sit below the measured median"
