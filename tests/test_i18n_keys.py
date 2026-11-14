"""每个 UI 面引用的 i18n 键都必须在两种语言的表里存在。

`datasentry_core.reporting.i18n.t()`（i18n.py:726-729）在查不到键时**返回键名本身**：
写错一个键、或只往一处面加了键却没进表，浏览器里就渲染成 `ui.some_key`，而没有任何测试会红。
r6 正是这样踩上的（新加的 `ui.no_registered_sources` 引用先于键定义落盘）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from datasentry_core.reporting.i18n import L10N

SRC = Path(__file__).resolve().parents[1] / "src" / "datasentry"
FACES = ("ui.py", "api.py")
TRANSLATORS = {"t", "_t"}


def _literal_keys(module: Path) -> set[str]:
    """Collect the 2nd positional literal of every `t(lang, key)` call, by parsing not grepping."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id in TRANSLATORS):
            continue
        if len(node.args) != 2:
            continue
        key = node.args[1]
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


@pytest.mark.parametrize("face", FACES)
def test_every_referenced_key_exists_in_both_tables(face: str) -> None:
    keys = _literal_keys(SRC / face)
    assert keys, f"{face} references no i18n key at all -- the probe stopped measuring"
    missing_en = sorted(k for k in keys if k not in L10N["en"])
    missing_zh = sorted(k for k in keys if k not in L10N["zh"])
    assert not missing_en, f"{face} asks for keys absent from the en table: {missing_en}"
    assert not missing_zh, f"{face} asks for keys absent from the zh table: {missing_zh}"


def test_zh_table_does_not_add_keys_the_default_language_lacks() -> None:
    """zh 独有键永远取不到（未知语言回退 en），等于悄悄丢文案。"""
    only_zh = sorted(set(L10N["zh"]) - set(L10N["en"]))
    assert not only_zh, f"zh-only keys unreachable through fallback: {only_zh}"
