"""契约导出（V1：Pandera / Great Expectations）。"""

from __future__ import annotations

from typing import Any

from datasentry_core.models.contract import Contract


def to_pandera(contract: Contract) -> str:
    """契约 → Pandera DataFrameSchema 代码字符串。"""
    from datasentry_core.contracts.exporters import to_pandera as _to

    return _to(contract)


def to_great_expectations(contract: Contract) -> dict[str, Any]:
    """契约 → Great Expectations ExpectationSuite 字典。"""
    from datasentry_core.contracts.exporters import to_great_expectations as _to

    return _to(contract)


__all__ = ["to_great_expectations", "to_pandera"]
