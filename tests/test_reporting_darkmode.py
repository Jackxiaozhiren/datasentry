"""Step 63（ADR-063）深色模式测试：变量化不变量、媒体块、SVG 类化。

强不变量：`_CSS` 中任何 hex 颜色只允许出现在变量定义行（`--` 开头）——
一旦新增硬编码色值即失败；`@media (prefers-color-scheme: dark)` 覆盖
暗色板、`@media print` 强制亮色；趋势 SVG 走 `.trend-line`/`.trend-dot`
类（跟随 `var(--accent)`），不再硬编码。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from datasentry_core import __version__
from datasentry_core.models.enums import Severity
from datasentry_core.models.scan import (
    ReproducibilityInfo,
    ScanConfig,
    ScanRun,
)
from datasentry_core.reporting import build_report
from datasentry_core.reporting.html import _CSS, render_html
from datasentry_core.reporting.interactive import render_trend_svg

_VARS = {
    "--fg",
    "--fg-muted",
    "--fg-subtle",
    "--accent",
    "--border",
    "--surface",
    "--surface-strong",
    "--surface-nav",
    "--on-accent",
    "--critical",
    "--high",
    "--medium",
    "--ok",
    "--highlight",
    "--semantic",
}


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


class TestCssVariables:
    def test_root_defines_all_vars(self) -> None:
        root = _CSS.split("@media")[0]
        for var in _VARS:
            assert f"{var}:" in root, f"{var} 缺默认值"

    def test_no_hex_outside_variable_definitions(self) -> None:
        hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")
        for line in _CSS.splitlines():
            if not hex_re.search(line):
                continue
            assert line.lstrip().startswith("--"), f"硬编码颜色泄漏: {line.strip()}"

    def test_dark_media_block_overrides_palette(self) -> None:
        dark = _CSS.split("@media (prefers-color-scheme: dark)")[1].split("@media print")[0]
        assert "color-scheme: dark" in dark
        assert "--fg: #e6edf3" in dark
        assert "--surface-strong: #161b22" in dark
        assert "--border: #30363d" in dark

    def test_print_block_forces_light(self) -> None:
        printer = _CSS.split("@media print")[1]
        assert "color-scheme: light" in printer
        assert "--fg: #1f2328" in printer
        assert "--surface: #f6f8fa" in printer

    def test_dark_block_covers_all_vars(self) -> None:
        dark = _CSS.split("@media (prefers-color-scheme: dark)")[1].split("@media print")[0]
        for var in _VARS:
            assert f"{var}:" in dark, f"暗色未覆盖 {var}"


class TestTrendSvg:
    def test_svg_uses_classes_not_hex(self) -> None:
        svg = render_trend_svg(
            {"dataset_id": "orders", "points": [{"score": 80.0}, {"score": 90.0}]}
        )
        assert 'class="trend-line"' in svg
        assert 'class="trend-dot"' in svg
        assert "stroke=" not in svg
        assert "#" not in svg


class TestRenderHtml:
    def test_report_carries_dark_and_print_blocks(self) -> None:
        html = render_html(_report())
        assert "@media (prefers-color-scheme: dark)" in html
        assert "@media print" in html
        assert "var(--fg)" in html

    def test_css_is_inline_single_style_tag(self) -> None:
        html = render_html(_report())
        assert html.count("<style>") == 1
        assert "<link" not in html
