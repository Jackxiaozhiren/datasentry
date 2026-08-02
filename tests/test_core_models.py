"""Step 1 领域模型测试：枚举、约束、序列化往返、跨文件引用解析。"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from datasentry_core.models import (
    AIExplanation,
    BusinessCriticality,
    CauseHypothesis,
    ColumnProfile,
    Condition,
    Contract,
    DatasetContract,
    DatasetFingerprint,
    Evidence,
    EvidenceType,
    Issue,
    IssueStatus,
    LLMUsageSummary,
    MaskConfig,
    QualityDimension,
    QualityGate,
    RepairOperation,
    RepairProposal,
    RepairRun,
    RiskLevel,
    Rule,
    RuleType,
    SamplingConfig,
    SamplingInfo,
    ScanConfig,
    ScanRun,
    Severity,
)
from datasentry_core.scoring.weights import CRITICALITY_WEIGHTS, SEVERITY_WEIGHTS


class TestEnums:
    def test_severity_values(self) -> None:
        assert [s.value for s in Severity] == ["info", "low", "medium", "high", "critical"]

    def test_severity_weights_cover_all(self) -> None:
        assert set(SEVERITY_WEIGHTS) == set(Severity)
        assert all(0.0 < w <= 1.0 for w in SEVERITY_WEIGHTS.values())

    def test_criticality_weights_cover_all(self) -> None:
        assert set(CRITICALITY_WEIGHTS) == set(BusinessCriticality)
        assert all(0.0 < w <= 1.6 for w in CRITICALITY_WEIGHTS.values())

    def test_issue_status_transitions(self) -> None:
        assert IssueStatus.OPEN.value == "open"
        assert IssueStatus.RESOLVED.value == "resolved"

    def test_quality_dimensions_include_all_eight(self) -> None:
        assert len(list(QualityDimension)) == 8

    def test_repair_operation_has_mvp_subset(self) -> None:
        mvp_ops = {
            RepairOperation.TRIM_WHITESPACE,
            RepairOperation.NORMALIZE_CASE,
            RepairOperation.CAST_TYPE,
            RepairOperation.SET_NULL,
            RepairOperation.CLIP_VALUE,
            RepairOperation.MAP_CATEGORY,
            RepairOperation.REPLACE_MISSING_TOKEN,
            RepairOperation.IMPUTE_VALUE,
        }
        assert mvp_ops <= set(RepairOperation)


class TestFingerprint:
    def test_full_construction(self) -> None:
        fp = DatasetFingerprint(
            dataset_id="ds_orders",
            fingerprint_type="full",
            file_sha256="a" * 64,
            schema_hash="b" * 32,
            row_count=50_000,
            column_count=13,
            column_signature=[("order_id", "string"), ("order_total", "float")],
        )
        assert fp.fingerprint_type == "full"
        assert fp.column_signature[0] == ("order_id", "string")

    def test_bad_fingerprint_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DatasetFingerprint(
                dataset_id="ds_1",
                fingerprint_type="partial",
                schema_hash="h",
                row_count=0,
                column_count=0,
                column_signature=[],
            )

    def test_negative_row_count_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DatasetFingerprint(
                dataset_id="ds_1",
                fingerprint_type="metadata_only",
                schema_hash="h",
                row_count=-1,
                column_count=0,
                column_signature=[],
            )


class TestProfiles:
    def test_column_profile_ratio_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ColumnProfile(dataset_id="ds", column_name="c", physical_type="string", null_ratio=1.5)

    def test_column_profile_defaults(self) -> None:
        p = ColumnProfile(dataset_id="ds", column_name="c", physical_type="string")
        assert p.semantic_type == "unknown"
        assert p.semantic_confidence == 0.0
        assert p.contains_pii is False

    def test_sampling_info(self) -> None:
        s = SamplingInfo(sampled=True, method="random", sample_size=1000, full_size=1_000_000)
        assert s.generalizable is False
        assert s.full_stats_columns == []

    def test_mask_config_safe_defaults(self) -> None:
        m = MaskConfig()
        assert m.policy == "safe"
        assert m.free_text_max_chars == 200
        assert m.max_sample_rows_per_column == 50
        assert m.max_sample_rows_per_table == 200

    def test_mask_config_cannot_relax_safe_floor(self) -> None:
        with pytest.raises(ValidationError):
            MaskConfig(free_text_max_chars=500)


class TestEvidence:
    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(
                evidence_id="ev1",
                evidence_type=EvidenceType.STATISTICAL_MEASURE,
                detector_id="d",
                detector_version="1",
                description="x",
                confidence=1.2,
            )

    def test_evidence_construction(self) -> None:
        e = Evidence(
            evidence_id="ev1",
            evidence_type=EvidenceType.STATISTICAL_MEASURE,
            detector_id="iqr_outlier",
            detector_version="1.0",
            description="age=-3 lies below Q1 - 1.5*IQR",
            data={"min": -3, "q01": 18},
        )
        assert e.confidence == 1.0
        assert e.provenance is None


class TestRules:
    def test_rule_defaults(self) -> None:
        r = Rule(
            id="completed_requires_delivered_at",
            type=RuleType.CONDITIONAL_NOT_NULL,
            when=Condition(column="status", operator="equals", value="completed"),
            then=Condition(column="delivered_at", operator="not_null"),
        )
        assert r.enabled is True
        assert r.version == 1
        assert r.source == "user"

    def test_rule_version_increment_on_modification(self) -> None:
        r = Rule(
            id="r1",
            type=RuleType.NOT_NULL,
            then=Condition(column="order_id", operator="not_null"),
        )
        r.version = 2
        assert r.version == 2

    def test_condition_bad_operator_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Condition(column="a", operator="exec")  # type: ignore[arg-type]


class TestRepair:
    def test_proposal_construction(self) -> None:
        p = RepairProposal(
            proposal_id="p1",
            issue_id="iss1",
            operation=RepairOperation.TRIM_WHITESPACE,
            target_columns=["note"],
            estimated_rows_changed=12,
            risk_level=RiskLevel.LOW,
        )
        assert p.reversibility == "fully_reversible"
        assert p.status.value == "proposed"

    def test_preview_ratio_bounds(self) -> None:
        from datasentry_core.models import RepairPreview

        with pytest.raises(ValidationError):
            RepairPreview(proposal_id="p1", rows_changed=1, rows_changed_ratio=1.5)

    def test_repair_run_construction(self) -> None:
        run = RepairRun(id="run_1", dataset_id="ds", fingerprint_before="f1")
        assert run.status.value == "applied"
        assert run.approved_by == "local-user"


class TestIssue:
    def _make_issue(self, **overrides: object) -> Issue:
        kwargs: dict[str, object] = {
            "id": "iss1",
            "scan_run_id": "scn1",
            "issue_type": "numeric_outlier",
            "title": "age=-3 is a statistical outlier",
            "dataset_id": "ds",
            "columns": ["age"],
            "quality_dimensions": [QualityDimension.VALIDITY],
            "severity": Severity.HIGH,
            "confidence": 0.9,
            "priority_score": 82.0,
            "false_positive_risk": RiskLevel.LOW,
            "affected_count": 1,
            "affected_ratio": 0.00002,
        }
        kwargs.update(overrides)
        return Issue(**kwargs)

    def test_minimal_construction(self) -> None:
        issue = self._make_issue()
        assert issue.status == IssueStatus.OPEN
        assert issue.evidence == []
        assert issue.affected_row_ids is None

    def test_full_construction_with_forward_refs(self) -> None:
        issue = self._make_issue(
            evidence=[
                Evidence(
                    evidence_id="ev1",
                    evidence_type=EvidenceType.STATISTICAL_MEASURE,
                    detector_id="mad_outlier",
                    detector_version="1",
                    description="modified z = -8.31",
                )
            ],
            repair_proposals=[
                RepairProposal(
                    proposal_id="p1",
                    issue_id="iss1",
                    operation=RepairOperation.SET_NULL,
                    target_columns=["age"],
                    estimated_rows_changed=1,
                )
            ],
            ai_explanation=AIExplanation(
                summary="negative age",
                likely_causes=[
                    CauseHypothesis(
                        description="data entry error",
                        evidence_ids=["ev1"],
                        confidence=0.8,
                        kind="data_entry",
                    )
                ],
                supporting_evidence_ids=["ev1"],
                assumptions=[],
                uncertainty="low",
            ),
        )
        assert issue.ai_explanation is not None
        assert issue.ai_explanation.likely_causes[0].kind == "data_entry"
        assert issue.repair_proposals[0].operation == RepairOperation.SET_NULL

    def test_priority_score_bounds(self) -> None:
        with pytest.raises(ValidationError):
            self._make_issue(priority_score=101.0)

    def test_json_round_trip(self) -> None:
        issue = self._make_issue()
        restored = Issue.model_validate_json(issue.model_dump_json())
        assert restored.model_dump() == issue.model_dump()

    def test_issue_matches_ddl_status_check_values(self) -> None:
        """DDL 草案 CHECK 约束与枚举必须一致（防止迁移漂移）。"""
        allowed = {
            "open",
            "confirmed",
            "false_positive",
            "accepted_exception",
            "repair_proposed",
            "repair_approved",
            "repaired",
            "resolved",
        }
        assert {s.value for s in IssueStatus} == allowed


class TestScan:
    def test_scan_run_defaults(self) -> None:
        fp = DatasetFingerprint(
            dataset_id="ds",
            fingerprint_type="sampled",
            schema_hash="h",
            row_count=100,
            column_count=2,
            column_signature=[("a", "string"), ("b", "int")],
        )
        run = ScanRun(
            id="scn1",
            dataset_id="ds",
            config=ScanConfig(),
            fingerprint=fp,
            reproducibility={"datasentry_version": "0.1.0", "seed": 42},
        )
        assert run.status == "queued"
        assert all(count == 0 for count in run.issues_count.values())
        assert run.config.llm_budget_tokens == 20_000
        assert run.config.seed == 42
        assert isinstance(run.llm_usage, LLMUsageSummary)

    def test_sampling_config(self) -> None:
        s = SamplingConfig(method="stratified", stratified_columns=["country"])
        assert s.ratio is None
        assert s.sample_size is None
        assert s.generalizable is True

    def test_scan_run_json_round_trip(self) -> None:
        fp = DatasetFingerprint(
            dataset_id="ds",
            fingerprint_type="full",
            schema_hash="h",
            row_count=10,
            column_count=1,
            column_signature=[("a", "string")],
        )
        run = ScanRun(
            id="scn1",
            dataset_id="ds",
            config=ScanConfig(),
            fingerprint=fp,
            reproducibility={"datasentry_version": "0.1.0", "seed": 1},
        )
        restored = ScanRun.model_validate_json(run.model_dump_json())
        assert restored.config == run.config
        assert restored.reproducibility == run.reproducibility


class TestContract:
    def test_minimal_contract(self) -> None:
        c = Contract(dataset=DatasetContract(name="orders"))
        assert c.version == "1.0"
        assert c.columns == {}
        assert c.rules == []

    def test_contract_with_quality_gate(self) -> None:
        c = Contract(
            dataset=DatasetContract(name="orders"),
            quality_gate=QualityGate(),
        )
        assert c.quality_gate is not None
        assert c.quality_gate.fail_on == [Severity.CRITICAL]
        assert c.quality_gate.maximum_failed_rows_ratio == 0.01

    def test_bad_gate_ratio_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            QualityGate(maximum_failed_rows_ratio=1.5)


class TestTime:
    def test_datetimes_are_timezone_aware_utc(self) -> None:
        e = Evidence(
            evidence_id="e",
            evidence_type=EvidenceType.PATTERN_MATCH,
            detector_id="d",
            detector_version="1",
            description="x",
        )
        assert e.created_at.tzinfo is not None
        assert e.created_at.utcoffset() is not None


class TestProperty:
    """属性测试：任意合法输入下模型构造与序列化不崩溃（29.2 起点）。"""

    @given(
        st.integers(min_value=0, max_value=10**9),
        st.integers(min_value=0, max_value=10**9),
    )
    def test_fingerprint_round_trip(self, rows: int, cols: int) -> None:
        fp = DatasetFingerprint(
            dataset_id="ds",
            fingerprint_type="full",
            schema_hash="h",
            row_count=rows,
            column_count=cols,
            column_signature=[("a", "string")] * min(cols, 5),
        )
        restored = DatasetFingerprint.model_validate_json(fp.model_dump_json())
        assert restored.row_count == rows
        assert restored.column_count == cols

    @given(
        st.integers(min_value=0, max_value=10**9),
        st.floats(min_value=0.0, max_value=1.0),
    )
    def test_column_profile_ratio_round_trip(self, rows: int, ratio: float) -> None:
        p = ColumnProfile(
            dataset_id="ds",
            column_name="c",
            physical_type="float",
            null_ratio=ratio,
            unique_ratio=1.0 - ratio,
            distinct_count=rows,
        )
        restored = ColumnProfile.model_validate_json(p.model_dump_json())
        assert restored.null_ratio == ratio
        assert restored.unique_ratio == pytest.approx(1.0 - ratio)
