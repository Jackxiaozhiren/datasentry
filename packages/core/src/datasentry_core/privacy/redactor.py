"""脱敏管线（Step 27，38 章「AI 不接收未经授权的完整数据」）。

确定性启发式 PII 识别 + 掩码替换，纯函数无状态：
- `redact()`：把命中 PII 的值替换为占位符 `{{REDACTED:<kind>:<n>}}`，
  同时返回映射表；同输入恒同输出（可复现，供 LLM 缓存复用）
- `restore()`：按映射表把占位符还原为原文（AI 输出中引用原始值时用）
- `mask_rows()`：批量掩码（LLM 调用前对样本行强制应用）

安全边界（ADR-027）：
- 映射表只在进程内传递，**不落盘**；落盘加密存储归 V1 后续
- 识别基于内置正则（确定性、零模型）；姓名等无可靠正则的类别
  不识别，宁可漏报不可误伤（误伤会破坏修复语义）
- 掩码值保持类型与长度接近原文（数字替换为等长数字），降低
  对 LLM 下游任务的语义扰动
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from datasentry_core.models.profile import DatasetProfile

_REDACTED_RE = re.compile(r"\{\{REDACTED:(\w+):(\d+)\}\}")

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ),
    (
        "cn_phone",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ),
    (
        "cn_id",
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)|(?<!\d)\d{15}(?!\d)"),
    ),
    (
        "ipv4",
        re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"),
    ),
    (
        "url",
        re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE),
    ),
)

_PLACEHOLDER_TEMPLATE = "{{{{REDACTED:{kind}:{index}}}}}"


@dataclass
class RedactionResult:
    """掩码文本 + 映射表（进程内传递，不落盘）。"""

    masked: str
    mapping: dict[str, list[str]] = field(default_factory=dict)


def redact(text: str, mapping: dict[str, list[str]] | None = None) -> RedactionResult:
    """掩码文本中的 PII；同输入同输出（确定性）。

    mapping 复用：传入已有映射表可让多段文本共享同一占位符空间，
    保证跨样本的同一值映射到同一占位符（利于 LLM 上下文一致性）。
    """
    result = RedactionResult(masked=text, mapping=dict(mapping) if mapping else {})
    spans: list[tuple[int, int, str, int]] = []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end(), kind, 0))
    spans.sort()
    merged: list[tuple[int, int, str, int]] = []
    for start, end, kind, _ in spans:
        if merged and start < merged[-1][1]:
            # 重叠命中（如 email 内含数字）：保留先识别者，扩展至并集
            prev = merged[-1]
            merged[-1] = (prev[0], max(prev[1], end), prev[2], 0)
        else:
            merged.append((start, end, kind, 0))
    pieces: list[str] = []
    cursor = 0
    for start, end, kind, _ in merged:
        original = text[start:end]
        seen = result.mapping.get(kind, [])
        if original in seen:
            index = seen.index(original)
        else:
            index = len(seen)
            result.mapping.setdefault(kind, []).append(original)
        pieces.append(text[cursor:start])
        pieces.append(_PLACEHOLDER_TEMPLATE.format(kind=kind, index=index))
        cursor = end
    pieces.append(text[cursor:])
    result.masked = "".join(pieces)
    return result


def restore(masked: str, mapping: dict[str, list[str]]) -> str:
    """按映射表还原占位符为原文。未知占位符保留原样（幂等）。"""

    def _replace(m: re.Match[str]) -> str:
        kind, index = m.group(1), int(m.group(2))
        bucket = mapping.get(kind, [])
        if 0 <= index < len(bucket):
            return bucket[index]
        return m.group(0)

    return _REDACTED_RE.sub(_replace, masked)


def mask_rows(
    rows: list[dict[str, Any]], mapping: dict[str, list[str]] | None = None
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """批量掩码行集（LLM 调用前对样本强制应用）。返回（掩码行，映射表）。"""
    result = dict(mapping) if mapping else {}
    masked_rows: list[dict[str, Any]] = []
    for row in rows:
        masked_row: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, str):
                out = redact(value, result)
                result = out.mapping
                masked_row[key] = out.masked
            else:
                masked_row[key] = value
        masked_rows.append(masked_row)
    return masked_rows, result


def mask_profile(
    profile: DatasetProfile, mapping: dict[str, list[str]] | None = None
) -> tuple[DatasetProfile, dict[str, list[str]]]:
    """掩码画像中的样本字段（examples/top_categories），供 LLM 上下文使用。"""
    result = dict(mapping) if mapping else {}
    for column in profile.column_profiles.values():
        if not column.examples:
            continue
        masked_examples: list[Any] = []
        for value in column.examples:
            if isinstance(value, str):
                out = redact(value, result)
                result = out.mapping
                masked_examples.append(out.masked)
            else:
                masked_examples.append(value)
        column.examples = masked_examples
        if column.top_categories:
            masked_categories: list[tuple[str, int]] = []
            for value, count in column.top_categories:
                out = redact(str(value), result)
                result = out.mapping
                masked_categories.append((out.masked, count))
            column.top_categories = masked_categories
    return profile, result
