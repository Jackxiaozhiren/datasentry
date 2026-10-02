"""D3-06：hypothesis 属性测试——脱敏往返/无泄漏/确定性 + 门禁单调性。

边界形态（空/极值/超长/多轮）是这类工具最易出错处，点采样覆盖不到，
故用属性钉住不变量。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from datasentry_core.models.contract import QualityGate
from datasentry_core.models.enums import QualityDimension, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.privacy.redactor import redact, restore
from datasentry_core.scoring import QualityGateEvaluator

_SAFE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="{}"),
    max_size=300,
)


def _issue(ratio: float) -> Issue:
    return Issue(
        id="i1",
        scan_run_id="scan_1",
        issue_type="numeric_outlier",
        title="t",
        dataset_id="ds",
        columns=["v"],
        quality_dimensions=[QualityDimension.VALIDITY],
        severity=Severity.CRITICAL,
        confidence=1.0,
        priority_score=0.0,
        affected_count=int(ratio * 100),
        affected_ratio=ratio,
        detector_ids=["d1"],
    )


class TestRedactorProperties:
    @given(_SAFE_TEXT)
    @settings(max_examples=100)
    def test_roundtrip(self, text: str) -> None:
        """无占位符语法的任意文本：redact → restore 为恒等（无 `{}` 故无歧义）。"""
        result = redact(text)
        assert restore(result.masked, result.mapping) == text

    @given(_SAFE_TEXT, st.integers(min_value=0, max_value=10_000))
    @settings(max_examples=100)
    def test_detected_email_never_leaks(self, surround: str, n: int) -> None:
        """注入的邮箱原文永不出现在掩码输出中，且占位符可还原。"""
        email = f"user{n}@example.com"
        result = redact(f"{surround} {email} {surround}")
        assert email not in result.masked
        assert "{{REDACTED:email:" in result.masked
        assert email in restore(result.masked, result.mapping)

    @given(_SAFE_TEXT)
    @settings(max_examples=60)
    def test_deterministic(self, text: str) -> None:
        first, second = redact(text), redact(text)
        assert first.masked == second.masked
        assert first.mapping == second.mapping


class TestGateProperties:
    @given(st.floats(min_value=0.0, max_value=1.0))
    @settings(max_examples=100)
    def test_single_issue_exact_semantics(self, ratio: float) -> None:
        """单 critical issue：通过 ⟺ ratio ≤ 阈值（钉死 `>` 语义，D3-04 呼应）。

        直接蕴含单调性：ratio 更大不可能把失败翻成通过。
        """
        gate = QualityGate()
        passed = QualityGateEvaluator().evaluate([_issue(ratio)], gate).passed
        assert passed == (ratio <= gate.maximum_failed_rows_ratio)
