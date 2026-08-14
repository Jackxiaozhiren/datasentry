"""证据级动态描述（Step 78，V11，ADR-078）：模板化 + zh 镜像。

- ev(key, base_data, **params)：生成端统一入口。en 文本 = i18n en
  模板 .format(**params)（与原 f-string 逐字一致，快照零变化）；
  返回 (en_text, merged_data)——merged_data 在调用方 data 基础上并入
  _text_key/_params，zh 渲染时按 key 查 zh 模板、以同源参数填充
  （en/zh 数值逐字一致）。
- translate_evidence_desc(lang, data, description)：渲染端。data 含
  _text_key 且 zh → zh 模板填充；无 meta（历史数据/外部证据）→
  回退原文。
- 边界：JSON/Markdown/JUnit/SARIF 机器契约不译（V10 边界延续，
  description 字段始终 en 原文）。
"""

from __future__ import annotations

from typing import Any

from datasentry_core.reporting.i18n import L10N


class EvText(str):
    """携带翻译 meta 与数据 base 的 en 渲染文本（str 子类，Description 兼容）。

    make_evidence 识别后自动把 base + _text_key/_params 并入
    evidence.data；直接落库/外部消费时行为与普通 str 完全一致。
    """

    key: str
    params: dict[str, Any]
    base: dict[str, Any]

    def __new__(cls, text: str, key: str, params: dict[str, Any], base: dict[str, Any]) -> EvText:
        obj = str.__new__(cls, text)
        obj.key = key
        obj.params = params
        obj.base = base
        return obj


def ev(key: str, base: dict[str, Any] | None = None, **params: Any) -> EvText:
    """生成端：en 渲染文本（EvText，携带 base + _text_key/_params meta）。

    make_evidence 会把 base 与 meta 并入 data（原 data 语义零变化，
    zh 渲染同源参数）；en 模板缺失时回退英文键名（不中断扫描，
    与 t() 语义一致）。
    """
    template = L10N["en"].get(f"evidence_desc.{key}")
    text = key if template is None else template.format(**params)
    return EvText(text, key, dict(params), dict(base or {}))


def translate_evidence_desc(lang: str, data: dict[str, Any], description: str) -> str:
    """渲染端：按 data 携带的 _text_key/_params 渲染 zh 模板。

    未知语言/en/无 meta → 原文（en 逐字不变；历史数据回退）。
    模板或参数不完整时回退原文（诚实降级，绝不抛异常）。
    """
    if not lang or lang == "en":
        return description
    key = data.get("_text_key")
    params = data.get("_params")
    if not isinstance(key, str) or not isinstance(params, dict):
        return description
    zh_table = L10N.get("zh") or {}
    template = zh_table.get(f"evidence_desc.{key}")
    if template is None:
        return description
    try:
        return template.format(**params)
    except (KeyError, ValueError, IndexError):
        return description
