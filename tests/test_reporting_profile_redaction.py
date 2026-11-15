"""B3-e：列画像的统计字段不得把 PII 原文写进内嵌 JSON。

`DatasetProfile` 会为 VARCHAR 列记录文本极值，`min`/`max` 完全可能是一条 DSN、
一个邮箱或手机号。`_fmt()` 历史上对非数值直接 `str(v)`，于是原文进入已落盘
报告的 `profiles-data`；而同一行的 `top_categories` 早已走 `mask_text_pii`，
这是该视图唯一漏掉的展示字段（AGENTS.md 不变量 5）。

这里断言的是可观测结果，不是"已全覆盖"：`mask_text_pii` 是正则级掩码，
对 DSN 只抹掉口令段（`postgresql://user:[REDACTED]/prod`），保留 scheme 与
用户名前缀。测试因此只要求原文与完整 DSN 不出现，且掩码后仍留下可见标记。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from datasentry_core.reporting import column_profiles as cp
from datasentry_core.reporting.html import render_html
from test_reporting_profiles import _report

_DSN = "postgresql://user:s3cr3t@db.internal/prod"
_SECRET = "s3cr3t"
_EMAIL = "alice@example.com"
_STATS = ("min", "median", "mean", "max", "std")
_MARKER = 'id="profiles-data">'


def _col(*, text: str | None = None, numeric: bool = False) -> dict[str, Any]:
    values: dict[str, Any] = {
        "physical_type": "DOUBLE" if numeric else "VARCHAR",
        "semantic_type": "unknown",
        "contains_pii": False,
        "null_ratio": 0.05,
        "unique_ratio": 0.95,
        "distinct_count": 95,
        "top_categories": [],
    }
    for key in _STATS:
        values[key] = 42.5 if numeric else text
    return values


def _profiles(**columns: dict[str, Any]) -> dict[str, Any]:
    return {"column_profiles": columns}


def _payload_raw(html: str) -> str:
    assert _MARKER in html, "Column Profiles 章节缺失: 断言将没有分母"
    return html.split(_MARKER)[1].split("</script>")[0]


def _cells(html: str, name: str) -> dict[str, Any]:
    rows = json.loads(_payload_raw(html))["columns"]
    matched = [row for row in rows if row["name"] == name]
    assert len(matched) == 1, f"expected exactly one row for {name}, got {len(matched)}"
    return {key: matched[0][key] for key in _STATS}


def _assert_no_secret(html: str) -> None:
    raw = _payload_raw(html)
    assert raw, "空 payload 会让这条断言空转"
    assert _SECRET not in raw, "明文口令泄露进 profiles-data"
    assert _DSN not in raw, "完整 DSN 泄露进 profiles-data"


class TestStatisticsRedaction:
    def test_five_stat_fields_never_carry_the_dsn_plain_value(self) -> None:
        html = cp.render_column_profiles(_profiles(ds=_col(text=_DSN)))
        cells = _cells(html, "ds")
        assert cells, "五个统计单元格必须存在: 断言才有分母"
        _assert_no_secret(html)
        for key, value in cells.items():
            assert value is not None, f"{key} 被静默丢弃而非掩码"
            assert _SECRET not in str(value), f"{key} 仍带明文口令"
            assert "[REDACTED]" in str(value), f"{key} 变了但没有掩码标记"

    def test_full_report_does_not_embed_the_secret(self) -> None:
        """B3-e 的原始复现路径：render_html + 带 DSN 文本极值的 profile。"""
        html = render_html(_report(), profiles=_profiles(ds=_col(text=_DSN)))
        assert _MARKER in html, "profile 章节必须真的渲染出来"
        assert _DSN not in html, "完整 DSN 泄露进已落盘报告"
        assert _SECRET not in html, "明文口令泄露进已落盘报告"

    def test_email_extreme_is_masked_in_all_five_fields(self) -> None:
        html = cp.render_column_profiles(_profiles(mail=_col(text=_EMAIL)))
        assert _EMAIL not in _payload_raw(html), "email 极值泄露进 profiles-data"
        cells = _cells(html, "mail")
        assert all("[REDACTED]" in str(cells[key]) for key in _STATS), (
            "文本 email 极值必须在五个字段里都被掩码"
        )

    def test_plain_text_extremes_are_preserved_not_blindly_redacted(self) -> None:
        """掩码按片段命中，不是把非数值一律抹掉：否则 min/max 会失去信息量。"""
        cells = _cells(cp.render_column_profiles(_profiles(city=_col(text="Zebra"))), "city")
        assert set(cells.values()) == {"Zebra"}, "非 PII 文本极值应原样保留"

    def test_masking_must_not_be_a_silent_value_drop(self) -> None:
        """把值删掉同样能让"原文不出现"通过，所以单独钉住"仍有可见内容"。"""
        cells = _cells(cp.render_column_profiles(_profiles(ds=_col(text=_DSN))), "ds")
        assert all(value not in (None, "", "-") for value in cells.values()), (
            "掩码必须留下标记, 不能把统计量擦掉"
        )

    def test_numeric_stats_keep_their_exact_display_strings(self) -> None:
        """棘轮：掩码不得触碰数值渲染路径。"""
        cells = _cells(cp.render_column_profiles(_profiles(amount=_col(numeric=True))), "amount")
        assert cells == dict.fromkeys(_STATS, "42.5")

    def test_none_stats_stay_none(self) -> None:
        cells = _cells(cp.render_column_profiles(_profiles(empty=_col(text=None))), "empty")
        assert cells == dict.fromkeys(_STATS)

    def test_top_categories_masking_does_not_regress(self) -> None:
        data = _profiles(email=_col(text=None))
        data["column_profiles"]["email"]["top_categories"] = [(_EMAIL, 3)]
        row = json.loads(_payload_raw(cp.render_column_profiles(data)))["columns"][0]
        assert row["topCategories"] and _EMAIL not in str(row)
        assert "[REDACTED]" in str(row["topCategories"])


class TestGateCanGoRed:
    """控制组：把掩码换成恒等函数后必须看得见泄露，否则上面的断言没有分母。"""

    def test_injected_identity_mask_exposes_the_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cp, "mask_text_pii", lambda text: text)
        raw = _payload_raw(cp.render_column_profiles(_profiles(ds=_col(text=_DSN))))
        assert _DSN in raw, "注入的泄露必须可见"
        assert _SECRET in raw

    def test_bound_fires_when_masking_is_bypassed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cp, "mask_text_pii", lambda text: text)
        html = cp.render_column_profiles(_profiles(ds=_col(text=_DSN)))
        with pytest.raises(AssertionError, match="明文口令"):
            _assert_no_secret(html)
