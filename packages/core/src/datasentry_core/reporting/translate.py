"""Issue 级正文翻译（V10，Step 75，ADR-075）：渲染层映射。

零数据面改动：fusion / suggestions 输出的英文原文不动，HTML / Markdown /
UI 渲染前经本模块映射到目标语言；en 短路原文（逐字不变），zh 查 i18n
键域（families.* / issue_types.* / suggestions.* / issue.* 模板），
键完全缺失时回退英文原文（_lookup 区别于 t() 的返回键名语义）。

范围（ADR-069 边界更新）：issue title / 融合 issue description /
修复建议 label·rationale 翻译；证据级动态描述（含计数 f-string）不译。
"""

from __future__ import annotations

import re
from typing import Any

from datasentry_core.reporting.i18n import L10N, t

_TITLE_RE = re.compile(r"^(?P<phrase>.+?) in (?P<cols>.+)$")
_DESC_RE = re.compile(r"^\[(?P<did>[^\]]+) v(?P<ver>[\d.]+)\] (?P<itype>[^:]+): (?P<count>\d+)$")


def _lookup(lang: str, key: str, fallback: str) -> str:
    """查 i18n 键：zh/en 表命中取表值，完全缺失回退原文。"""
    table = L10N.get(lang) or L10N["en"]
    if key in table:
        return table[key]
    if key in L10N["en"]:
        return L10N["en"][key]
    return fallback


def translate_title(lang: str, title: str, issue_type: str | None = None) -> str:
    """issue title → 目标语言；非 zh 或无法识别时返回原文。"""
    if lang != "zh":
        return title
    m = _TITLE_RE.match(title)
    if not m:
        return title
    family = (
        _lookup(lang, f"families.{issue_type}", m.group("phrase"))
        if issue_type
        else m.group("phrase")
    )
    return t(lang, "issue.title_template").format(family=family, cols=m.group("cols"))


def translate_description(lang: str, description: str) -> str:
    """融合 issue description（`[detector_id vX.Y] issue_type: count`）→ 目标语言。"""
    if lang != "zh":
        return description
    m = _DESC_RE.match(description)
    if not m:
        return description
    return t(lang, "issue.description_template").format(
        detector_id=m.group("did"),
        version=m.group("ver"),
        issue_type=_lookup(lang, f"issue_types.{m.group('itype')}", m.group("itype")),
        count=m.group("count"),
    )


def translate_suggestion(lang: str, suggestion: dict[str, Any]) -> dict[str, Any]:
    """修复建议 label/rationale → 目标语言（operation/risk/targetColumns 不译）。"""
    if lang != "zh":
        return suggestion
    op = suggestion.get("operation", "")
    return {
        **suggestion,
        "label": _lookup(lang, f"suggestions.label.{op}", suggestion.get("label", "")),
        "rationale": _lookup(lang, f"suggestions.rationale.{op}", suggestion.get("rationale", "")),
    }
