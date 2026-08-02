"""Step 14 安全表达式求值器测试（11.10 + ADR-015）。"""

from __future__ import annotations

import pytest

from datasentry_core.detectors.safe_eval import (
    ExpressionSecurityError,
    SafeExpressionEvaluator,
)


class TestValidate:
    def test_allows_whitelisted_expressions(self) -> None:
        ev = SafeExpressionEvaluator()
        for expr in [
            "a <= b",
            "a + b == c",
            "(a is not None) and (b > 0)",
            'country == "US"',
            "len(x) > 3",
            "s.lower() == 'us'",
            "a < b < c",
            "x in (1, 2, 3)",
            "min(a, b) >= 0",
            "a if b else c",
        ]:
            ev.validate(expr)  # 不抛即通过

    def test_rejects_import_statements(self) -> None:
        ev = SafeExpressionEvaluator()
        with pytest.raises(ExpressionSecurityError):
            ev.validate("import os")

    def test_rejects_forbidden_functions(self) -> None:
        ev = SafeExpressionEvaluator()
        for expr in [
            'eval("1")',
            'exec("1")',
            'open("x")',
            'compile("1", "", "eval")',
            "__import__('os')",
            "getattr(x, 'y')",
            "setattr(x, 'y', 1)",
        ]:
            with pytest.raises(ExpressionSecurityError):
                ev.validate(expr)

    def test_rejects_non_whitelisted_functions(self) -> None:
        ev = SafeExpressionEvaluator()
        with pytest.raises(ExpressionSecurityError):
            ev.validate("map(str, a)")

    def test_rejects_attribute_access_outside_whitelist(self) -> None:
        ev = SafeExpressionEvaluator()
        for expr in ["a.__class__", "a.b.c", "s.strip().__class__"]:
            with pytest.raises(ExpressionSecurityError):
                ev.validate(expr)

    def test_rejects_statements_and_loops(self) -> None:
        ev = SafeExpressionEvaluator()
        with pytest.raises(ExpressionSecurityError):
            ev.validate("while True: pass")
        with pytest.raises(ExpressionSecurityError):
            ev.validate("x = 1")


class TestEvaluate:
    def test_evaluates_rows_in_order(self) -> None:
        ev = SafeExpressionEvaluator()
        result = ev.evaluate(
            "a <= b",
            ["a", "b"],
            [(1, 2), (3, 2), (None, None), (5, 5)],
        )
        assert list(result.values) == [True, False, None, True]
        assert result.evaluated == 4
        assert not result.timed_out

    def test_unknown_identifier_rejected(self) -> None:
        ev = SafeExpressionEvaluator()
        with pytest.raises(ExpressionSecurityError):
            ev.evaluate("a <= zzz", ["a", "b"], [(1, 2)])

    def test_result_cache_hits_same_values(self) -> None:
        ev = SafeExpressionEvaluator()
        rows = [(1, 2)] * 3
        result = ev.evaluate("a <= b", ["a", "b"], rows)
        assert list(result.values) == [True, True, True]
        assert len(ev._result_cache) == 1  # 同一表达式哈希 + 同一行值只算一次

    def test_different_expressions_different_cache_keys(self) -> None:
        ev = SafeExpressionEvaluator()
        ev.evaluate("a <= b", ["a", "b"], [(1, 2)])
        ev.evaluate("b >= a", ["a", "b"], [(1, 2)])
        assert len(ev._result_cache) == 2

    def test_string_methods_allowed(self) -> None:
        ev = SafeExpressionEvaluator()
        result = ev.evaluate(
            's.lower() == "us"',
            ["s"],
            [("US",), ("CN",), (None,)],
        )
        assert list(result.values) == [True, False, None]
