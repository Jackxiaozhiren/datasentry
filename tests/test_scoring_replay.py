"""O-D5-01 评分重放：验证「口径变更能检出」与「不误报」两个方向。

本文件的核心不是覆盖行数，而是证明探针**能变红**：若改了评分公式/权重后
``replay_score`` 仍报 delta=0，这个工具就等于一条不可验证的声明，比没有更糟。
"""

from __future__ import annotations

import pytest

from datasentry_core.models.enums import QualityDimension, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.scoring import quality as _quality
from datasentry_core.scoring.quality import DIMENSION_WEIGHTS, SCORE_VERSION, QualityScoreEngine
from datasentry_core.scoring.replay import DEFAULT_TOLERANCE, ScoreDrift, replay_score

_ALL_DIMS = set(DIMENSION_WEIGHTS)


def _issue(
    issue_id: str,
    dimension: QualityDimension,
    *,
    severity: Severity = Severity.HIGH,
) -> Issue:
    return Issue(
        id=issue_id,
        scan_run_id="scan_1",
        issue_type="numeric_outlier",
        title="t",
        dataset_id="ds",
        columns=["v"],
        quality_dimensions=[dimension],
        severity=severity,
        confidence=1.0,
        priority_score=0.0,
        affected_count=5,
        affected_ratio=0.5,
        detector_ids=["d1"],
    )


def _issues() -> list[Issue]:
    return [
        _issue("iss_1", QualityDimension.COMPLETENESS),
        _issue("iss_2", QualityDimension.VALIDITY),
        _issue("iss_3", QualityDimension.UNIQUENESS),
    ]


def _recorded(issues: list[Issue]) -> QualityScore:
    return QualityScoreEngine().score(issues, ran_dimensions=_ALL_DIMS)


class TestSameCodeDoesNotDrift:
    def test_identical_code_yields_zero_delta(self) -> None:
        issues = _issues()
        drift = replay_score(issues, _recorded(issues), run_id="run-A")
        assert drift.replayable
        assert drift.delta == 0.0
        assert not drift.exceeds()
        assert drift.changed_dimensions == ()
        assert not drift.version_changed

    def test_run_id_is_explicit_and_preserved(self) -> None:
        issues = _issues()
        drift = replay_score(issues, _recorded(issues), run_id="run-A")
        assert drift.run_id == "run-A"
        assert drift.recorded_score_version == SCORE_VERSION
        assert drift.current_score_version == SCORE_VERSION

    def test_injected_engine_is_used(self) -> None:
        """engine 参数可用于注入旧口径引擎；默认路径仍走当前模块全局。"""
        issues = _issues()
        recorded = _recorded(issues)
        drift = replay_score(issues, recorded, run_id="run-A", engine=QualityScoreEngine())
        assert drift.delta == 0.0


class TestCodeCaliberChangeIsDetected:
    def test_default_weight_change_produces_nonzero_delta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RED 对照：默认权重是代码口径的一部分，改了必须被检出。"""
        issues = _issues()
        recorded = _recorded(issues)
        assert replay_score(issues, recorded, run_id="run-A").delta == 0.0

        monkeypatch.setitem(_quality.DIMENSION_WEIGHTS, QualityDimension.COMPLETENESS, 0.01)
        fresh = QualityScoreEngine().score(issues, ran_dimensions=_ALL_DIMS)
        assert fresh.overall != recorded.overall  # 前置：口径确实变了

        drift = replay_score(issues, recorded, run_id="run-A")
        assert drift.replayable
        assert drift.delta is not None and abs(drift.delta) > DEFAULT_TOLERANCE
        assert drift.exceeds()
        # 权重只改 overall，不改各维度分：两个信号正交，不得混淆。
        assert drift.changed_dimensions == ()

    def test_severity_weight_change_changes_dimension_scores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        issues = _issues()
        recorded = _recorded(issues)
        monkeypatch.setitem(_quality.SEVERITY_WEIGHTS, Severity.HIGH, 0.1)

        drift = replay_score(issues, recorded, run_id="run-A")
        assert drift.exceeds()
        assert set(drift.changed_dimensions) == {"completeness", "validity", "uniqueness"}

    def test_delta_is_signed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """delta = 重算 − 存档；方向不能反，否则差异表无法解读。"""
        issues = _issues()
        recorded = _recorded(issues)
        monkeypatch.setitem(_quality.DIMENSION_WEIGHTS, QualityDimension.COMPLETENESS, 0.01)
        drift = replay_score(issues, recorded, run_id="run-A")
        assert drift.delta is not None
        assert drift.replayed_overall is not None
        assert drift.delta == drift.replayed_overall - drift.recorded_overall


class TestConfigurationIsNotCodeCaliber:
    def test_custom_weights_are_not_reported_as_code_drift(self) -> None:
        """历史 run 用了自定义配置：不传 weights 会有假阳性，显式传入即归零。"""
        issues = _issues()
        custom = {d: 1.0 for d in DIMENSION_WEIGHTS}
        custom[QualityDimension.COMPLETENESS] = 3.0
        recorded = QualityScoreEngine().score(issues, ran_dimensions=_ALL_DIMS, weights=custom)

        without = replay_score(issues, recorded, run_id="run-A")
        assert without.exceeds()  # 假阳性确实存在，不能假装没有
        assert "自定义配置" in without.note

        with_config = replay_score(issues, recorded, run_id="run-A", weights=custom)
        assert with_config.delta == 0.0
        assert not with_config.exceeds()
        assert "剥离" in with_config.note

    def test_recorded_weights_are_never_read_back_as_input(self) -> None:
        """回归护栏：存档 weights 是归一化输出，回灌成输入会锁死口径。

        用一份 recorded.weights 与默认权重明显不同的存档，仍必须按当前默认重放。
        """
        issues = _issues()
        custom = {d: 1.0 for d in DIMENSION_WEIGHTS}
        custom[QualityDimension.TIMELINESS] = 9.0
        recorded = QualityScoreEngine().score(issues, ran_dimensions=_ALL_DIMS, weights=custom)
        assert recorded.weights != {d.value: w for d, w in DIMENSION_WEIGHTS.items()}

        drift = replay_score(issues, recorded, run_id="run-A")
        assert drift.exceeds()  # 若回灌了 recorded.weights，这里会是 0.0


class TestUnreplayableArchives:
    def test_no_scored_dimension_is_marked_unreplayable(self) -> None:
        recorded = QualityScore(
            overall=88.4,
            dimensions={"completeness": None, "validity": None},
        )
        drift = replay_score([], recorded, run_id="run-A")
        assert not drift.replayable
        assert drift.delta is None
        assert drift.replayed_overall is None
        assert not drift.exceeds()  # 不可重算不得被当作「零差异通过」
        assert "不可重算" in drift.note

    def test_unknown_dimension_name_is_reported_not_guessed(self) -> None:
        recorded = QualityScore(
            overall=90.0,
            dimensions={"validity": 80.0, "future_dimension": 70.0},
        )
        drift = replay_score(_issues(), recorded, run_id="run-A")
        assert drift.replayable
        assert "future_dimension" in drift.note
        assert "future_dimension" in drift.changed_dimensions

    def test_legacy_eight_dimension_archive_is_not_replayable(self) -> None:
        """ADR-001 归并前的 8 维存档：当前引擎无对应权重，必须显式报不可重算。"""
        recorded = QualityScore(
            overall=88.4,
            dimensions={"accuracy_proxy": 80.0, "distribution_stability": 90.0},
        )
        drift = replay_score(_issues(), recorded, run_id="run-A")
        assert not drift.replayable
        assert "无法重放" in drift.note


class TestVersionAndTolerance:
    def test_version_change_is_flagged_even_with_zero_delta(self) -> None:
        issues = _issues()
        recorded = _recorded(issues).model_copy(update={"score_version": "0"})
        drift = replay_score(issues, recorded, run_id="run-A")
        assert drift.version_changed
        assert drift.delta == 0.0  # 只改了标记，公式没变

    def test_tolerance_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        issues = _issues()
        recorded = _recorded(issues)
        monkeypatch.setitem(_quality.DIMENSION_WEIGHTS, QualityDimension.COMPLETENESS, 0.01)
        drift = replay_score(issues, recorded, run_id="run-A")
        assert drift.exceeds(0.0)
        assert not drift.exceeds(100.0)

    def test_score_drift_is_immutable(self) -> None:
        drift = ScoreDrift(
            run_id="r",
            recorded_overall=90.0,
            replayed_overall=88.0,
            delta=-2.0,
            changed_dimensions=(),
            recorded_score_version="1",
            current_score_version="1",
        )
        assert drift.exceeds()
        with pytest.raises(AttributeError):
            drift.delta = 0.0
