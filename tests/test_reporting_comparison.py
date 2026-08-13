"""Step 64（ADR-064）报告间对比测试：对比节渲染标记、转义、Δ 类、当前行。

`_comparison_section` 直接以 dict 行喂入（与 `build_comparison` 输出契约
一致）；`render_html(comparison=...)` 集成：节 + 导航锚点条件出现、缺省
不渲染、CSS 只用变量（无新增 hex）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from datasentry_core import __version__
from datasentry_core.models.enums import Severity
from datasentry_core.models.scan import (
    ReproducibilityInfo,
    ScanConfig,
    ScanRun,
)
from datasentry_core.reporting import build_report
from datasentry_core.reporting.html import render_html


def _report() -> dict:
    scan = ScanRun(
        id="scan_abc",
        dataset_id="orders",
        status="completed",
        config=ScanConfig(),
        fingerprint={
            "dataset_id": "orders",
            "fingerprint_type": "full",
            "schema_hash": "h1",
            "row_count": 0,
            "column_count": 0,
            "column_signature": [],
        },
        issues_count={s: 0 for s in Severity},
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        reproducibility=ReproducibilityInfo(
            datasentry_version=__version__,
            detector_versions={},
            seed=42,
            scanned_at=datetime.now(UTC),
        ),
    )
    return build_report(scan, [], [], None, generated_at=datetime(2026, 8, 2))


def _comparison() -> list[dict]:
    return [
        {
            "run_id": "run_old",
            "finished_at": "2026-08-01T10:00:00+00:00",
            "overall": 80.0,
            "delta": None,
            "dimensions": {"completeness": 90.2, "accuracy": None},
            "issues": {"critical": 1, "high": 2},
            "current": False,
        },
        {
            "run_id": "run_new",
            "finished_at": "2026-08-02T10:00:00+00:00",
            "overall": 85.4,
            "delta": 5.4,
            "dimensions": {"completeness": 95.0, "consistency": 100.0},
            "issues": {"high": 0, "low": 3},
            "current": True,
        },
    ]


class TestComparisonSection:
    def test_renders_section_and_table(self) -> None:
        html = render_html(_report(), comparison=_comparison())
        assert '<h2 id="comparison">Run Comparison</h2>' in html
        assert "<table>" in html
        assert "<th>Overall</th>" in html
        assert "<th>Scanned at</th>" in html
        assert "<th>Completeness score</th>" in html
        assert "<th>Accuracy score</th>" in html
        assert "<th>Consistency score</th>" in html

    def test_severity_columns_only_for_present_severities(self) -> None:
        html = render_html(_report(), comparison=_comparison())
        assert "<th>Critical issues</th>" in html
        assert "<th>High issues</th>" in html
        assert "<th>Low issues</th>" in html
        assert "<th>Medium issues</th>" not in html
        assert "<th>Info issues</th>" not in html

    def test_current_run_highlighted_with_badge(self) -> None:
        html = render_html(_report(), comparison=_comparison())
        assert 'class="cmp-current"' in html
        assert 'class="cmp-badge">current</span>' in html
        assert "<code>run_new</code>" in html
        assert "<code>run_old</code>" in html

    def test_delta_colors_by_sign(self) -> None:
        comparison = _comparison()
        comparison[0]["delta"] = -3.2
        html = render_html(_report(), comparison=comparison)
        assert '<span class="cmp-down">(-3.2)</span>' in html
        assert '<span class="cmp-up">(+5.4)</span>' in html

    def test_zero_delta_and_first_row_plain(self) -> None:
        comparison = _comparison()
        comparison[0]["delta"] = 0.0
        comparison[1]["delta"] = 0.0
        html = render_html(_report(), comparison=comparison)
        assert '<span class="meta">(0.0)</span>' in html
        assert 'class="cmp-up"' not in html
        assert 'class="cmp-down"' not in html

    def test_escapes_user_controlled_cells(self) -> None:
        comparison = _comparison()
        comparison[0]["run_id"] = '<script>alert("x")</script>'
        comparison[0]["finished_at"] = "2026-08-01T10:00:00+00:00"
        comparison[0]["dimensions"] = {"<d&im>": 1.0}
        html = render_html(_report(), comparison=comparison)
        assert "<script>alert" not in html
        assert "&lt;script&gt;alert" in html
        assert "&lt;d&amp;im&gt;" in html

    def test_absent_or_empty_comparison_omits_section_and_nav(self) -> None:
        html = render_html(_report())
        assert 'id="comparison"' not in html
        assert 'href="#comparison"' not in html
        html_empty = render_html(_report(), comparison=[])
        assert 'id="comparison"' not in html_empty

    def test_nav_anchor_only_with_comparison(self) -> None:
        html = render_html(_report(), comparison=_comparison())
        assert 'href="#comparison"' in html
        assert '<a href="#comparison">comparison</a>' in html

    def test_section_placed_after_profiles(self) -> None:
        profiles = {"column_profiles": {"id": {"physical_type": "BIGINT"}}}
        html = render_html(
            _report(),
            profiles=profiles,
            comparison=_comparison(),
        )
        assert html.index('id="column_profiles"') < html.index('id="comparison"')


class TestComparisonCss:
    def test_new_classes_use_variables_only(self) -> None:
        for cls in (".cmp-up", ".cmp-down", ".cmp-current", ".cmp-badge"):
            for line in render_html(_report(), comparison=_comparison()).splitlines():
                if cls in line and line.lstrip().startswith("."):
                    assert "--" in line and "#" not in line, f"{cls}: 硬编码色值"
