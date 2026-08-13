"""修复建议预览（Step 62，V6，ADR-062）：无 server 场景的内联修复建议。

确定性映射（display 侧，镜像 repair/engine.py 的 `_PROPOSAL_MAP` /
`_CLIP_ISSUE_TYPES` 与 repair_ai.py 的 `_CONTEXT_OPS` 知识）：按 issue 的
detector_ids 反查建议操作，文本经 `mask_text_pii` 掩码后随 issue 行 JSON
下发给浏览器，详情行内联展示——不需要 server / LLM / 数据源句柄。

- 零存储：建议由报告数据纯函数推导（不落库、不进 26 章 JSON 契约）
- 漂移防护：test 遍历 repair.engine 的映射表断言每条检测器均有建议
- 未知检测器返回 []，UI 显示「无内置建议」文案（诚实降级）
"""

from __future__ import annotations

from typing import Any

from datasentry_core.reporting import mask_text_pii

_MAX_SUGGESTIONS = 3

_OperationView = dict[str, str]

_SUGGESTION_TABLE: dict[str, _OperationView] = {
    "leading_or_trailing_whitespace": {
        "operation": "trim_whitespace",
        "label": "trim whitespace",
        "rationale": "strip leading/trailing whitespace to a single canonical form",
        "risk": "low",
    },
    "inconsistent_case": {
        "operation": "normalize_case",
        "label": "normalize case",
        "rationale": "lowercase values to a single canonical form",
        "risk": "low",
    },
    "suspicious_missing_token": {
        "operation": "replace_missing_token",
        "label": "replace missing token",
        "rationale": "replace missing stand-ins (e.g. 'n/a', 'unknown') with NULL",
        "risk": "medium",
    },
    "invalid_date": {
        "operation": "set_null",
        "label": "set invalid values to NULL",
        "rationale": "values failing ISO date parsing are set to NULL (missing semantics)",
        "risk": "medium",
    },
    "impossible_date": {
        "operation": "set_null",
        "label": "set invalid values to NULL",
        "rationale": "values failing ISO date parsing are set to NULL (missing semantics)",
        "risk": "medium",
    },
    "iqr_outlier": {
        "operation": "clip_value",
        "label": "clip to outlier bounds",
        "rationale": "clip values outside the detected IQR bounds (bounds in evidence)",
        "risk": "medium",
    },
    "percentile_outlier": {
        "operation": "clip_value",
        "label": "clip to outlier bounds",
        "rationale": "clip values outside the detected percentile bounds (bounds in evidence)",
        "risk": "medium",
    },
    "modified_zscore": {
        "operation": "clip_value",
        "label": "clip to outlier bounds",
        "rationale": "clip values outside the detected z-score bounds (bounds in evidence)",
        "risk": "medium",
    },
}


def suggest_repairs(issue: dict[str, Any]) -> list[dict[str, Any]]:
    """issue（26 章 JSON 形状）→ 确定性修复建议（最多 3 条，按检测器顺序去重）。

    建议文本已 PII 掩码（人类可读面）；targetColumns 为模式名不做掩码。
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for detector_id in issue.get("detector_ids") or []:
        view = _SUGGESTION_TABLE.get(detector_id)
        if view is None or view["operation"] in seen:
            continue
        seen.add(view["operation"])
        out.append(
            {
                "operation": view["operation"],
                "label": mask_text_pii(view["label"]),
                "rationale": mask_text_pii(view["rationale"]),
                "risk": view["risk"],
                "targetColumns": list(issue.get("columns") or []),
            }
        )
        if len(out) >= _MAX_SUGGESTIONS:
            break
    return out


__all__ = ["_MAX_SUGGESTIONS", "suggest_repairs"]
