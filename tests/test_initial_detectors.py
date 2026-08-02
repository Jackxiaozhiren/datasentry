"""Step 6 首批确定性检测器测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasentry_core.connectors import CsvConnector, DataSourceSpec, DataSourceType
from datasentry_core.detectors import DetectionContext, DetectorRegistry
from datasentry_core.detectors.initial import register_default_detectors
from datasentry_core.detectors.initial.categorical import (
    CategoryExplosionDetector,
    InconsistentCaseDetector,
    RareCategoryDetector,
    SuspiciousPlaceholderDetector,
)
from datasentry_core.detectors.initial.formula import FormulaInjectionDetector
from datasentry_core.detectors.initial.missing import (
    ExcessiveNullRateDetector,
    SuspiciousMissingTokenDetector,
)
from datasentry_core.detectors.initial.numeric import (
    HistogramRarityDetector,
    IqrOutlierDetector,
    ModifiedZScoreDetector,
    PercentileOutlierDetector,
    TailProbabilityDetector,
)
from datasentry_core.detectors.initial.textual import (
    HiddenControlCharacterDetector,
    InvalidEmailDetector,
    InvalidIpDetector,
    InvalidPhoneDetector,
    InvalidUrlDetector,
    LeadingTrailingWhitespaceDetector,
    RepeatedWhitespaceDetector,
    UnusualLengthDetector,
)
from datasentry_core.detectors.initial.uniqueness import UniquenessViolationDetector

DETECTOR_CLASSES = [
    ExcessiveNullRateDetector,
    SuspiciousMissingTokenDetector,
    UniquenessViolationDetector,
    SuspiciousPlaceholderDetector,
    RareCategoryDetector,
    CategoryExplosionDetector,
    InconsistentCaseDetector,
    LeadingTrailingWhitespaceDetector,
    RepeatedWhitespaceDetector,
    HiddenControlCharacterDetector,
    UnusualLengthDetector,
    InvalidEmailDetector,
    InvalidPhoneDetector,
    InvalidUrlDetector,
    InvalidIpDetector,
    IqrOutlierDetector,
    PercentileOutlierDetector,
    ModifiedZScoreDetector,
    TailProbabilityDetector,
    HistogramRarityDetector,
    FormulaInjectionDetector,
]


@pytest.fixture
def registry() -> DetectorRegistry:
    reg = DetectorRegistry()
    register_default_detectors(reg)
    return reg


def _ctx(tmp_path: Path, csv_text: str, dataset_id: str = "ds_d") -> DetectionContext:
    p = tmp_path / "det.csv"
    p.write_text(csv_text, encoding="utf-8")
    spec = DataSourceSpec(
        source_type=DataSourceType.CSV, path=p, options={"dataset_id": dataset_id}
    )
    handle = CsvConnector().open(spec)
    return DetectionContext(
        dataset_id=dataset_id,
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
    )


def _detect(detector, ctx) -> list:
    try:
        return detector.detect(ctx)
    finally:
        ctx.handle.close()


class TestRegistryIntegration:
    def test_all_initial_detectors_registered(self, registry: DetectorRegistry) -> None:
        ids = [d.detector_id for d in registry.list()]
        assert len(ids) == 32
        assert "excessive_null_rate" in ids
        assert "suspicious_formula_injection" in ids
        assert "percentile_outlier" in ids
        assert "histogram_rarity" in ids
        assert "invalid_phone" in ids
        assert "invalid_url" in ids
        assert "invalid_ip" in ids
        assert "inconsistent_case" in ids
        assert "cross_field_rule" in ids
        assert "invalid_date" in ids
        assert "impossible_date" in ids
        assert "future_date" in ids
        assert "stale_date" in ids
        assert "mixed_date_format" in ids
        assert "duplicate_timestamp" in ids
        assert "sudden_missingness" in ids
        assert "group_missingness" in ids
        assert "conditional_missingness" in ids
        assert "correlated_missingness" in ids

    def test_metadata_shape(self, registry: DetectorRegistry) -> None:
        for d in registry.list():
            meta = d.metadata()
            assert meta.detector_id == d.detector_id
            assert meta.display_name
            assert meta.description
            assert meta.quality_dimension.value
            assert meta.capabilities.supports_sql_pushdown


class TestExcessiveNullRate:
    def test_reports_high_null_column(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "a,b\n1,x\n,x\n,x\n,x\n,x\n2,x\n")
        found = [c.columns[0] for c in _detect(ExcessiveNullRateDetector(), ctx)]
        assert "a" in found
        assert "b" not in found

    def test_clean_table_no_issues(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "a,b\n1,x\n2,y\n3,z\n")
        assert _detect(ExcessiveNullRateDetector(), ctx) == []


class TestSuspiciousMissingToken:
    def test_reports_missing_tokens(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nn/a\ntext\nN/A\n-\nok\nok\nok\n")
        candidates = _detect(SuspiciousMissingTokenDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].columns == ["v"]
        assert candidates[0].affected_count == 3

    def test_no_tokens_clean(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nalpha\nbeta\ngamma\n")
        assert _detect(SuspiciousMissingTokenDetector(), ctx) == []


class TestUniquenessViolation:
    def test_reports_duplicates(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "id\n1\n1\n2\n3\n3\n3\n")
        candidates = _detect(UniquenessViolationDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 3  # 1 重复 1 次 + 3 重复 2 次

    def test_unique_clean(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "id\n1\n2\n3\n4\n")
        assert _detect(UniquenessViolationDetector(), ctx) == []


class TestSuspiciousPlaceholder:
    def test_reports_placeholders(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nreal\ntest\nfoo\nreal\n")
        candidates = _detect(SuspiciousPlaceholderDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 2

    def test_clean(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nreal\ndata\n")
        assert _detect(SuspiciousPlaceholderDetector(), ctx) == []


class TestRareCategory:
    def test_reports_rare_categories(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "v\n" + "".join("common\n" for _ in range(6000)) + "rare1\nrare2\nrare1\n",
        )
        candidates = _detect(RareCategoryDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 3

    def test_single_value_column_skipped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\n" + "x\n" * 10)
        assert _detect(RareCategoryDetector(), ctx) == []


class TestCategoryExplosion:
    def test_reports_near_unique_column(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\n" + "".join(f"val{i}\n" for i in range(50)))
        candidates = _detect(CategoryExplosionDetector(), ctx)
        assert any(c.columns == ["v"] for c in candidates)

    def test_identifier_column_skipped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "user_id\n" + "".join(f"id{i}\n" for i in range(50)))
        assert _detect(CategoryExplosionDetector(), ctx) == []

    def test_clean_category_column(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\na\nb\na\nb\n")
        assert _detect(CategoryExplosionDetector(), ctx) == []


class TestWhitespaceDetectors:
    def test_leading_trailing(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nclean\n padded\nclean\n")
        candidates = _detect(LeadingTrailingWhitespaceDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1

    def test_repeated_whitespace(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nok\nbad  double\nok\n")
        candidates = _detect(RepeatedWhitespaceDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1


class TestHiddenControlCharacter:
    def test_reports_control_chars(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nok\nbad\x01char\nok\n")
        candidates = _detect(HiddenControlCharacterDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1


class TestUnusualLength:
    def test_reports_overlong(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, f"v\n{'x' * 2000}\nshort\n")
        candidates = _detect(UnusualLengthDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1

    def test_short_values_clean(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nhello\nworld\n")
        assert _detect(UnusualLengthDetector(), ctx) == []


class TestInvalidEmail:
    def test_reports_invalid_emails(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "email\nuser@example.com\nnot-an-email\nb@c.co\n")
        candidates = _detect(InvalidEmailDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1

    def test_skips_non_email_columns(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "name\nalice\nbob\n")
        assert _detect(InvalidEmailDetector(), ctx) == []


class TestNumericOutliers:
    def test_iqr(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\n" + "".join(f"{i}\n" for i in range(100)) + "100000\n")
        candidates = _detect(IqrOutlierDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1

    def test_modified_zscore(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\n" + "".join(f"{i}\n" for i in range(100)) + "100000\n")
        candidates = _detect(ModifiedZScoreDetector(), ctx)
        assert len(candidates) == 1

    def test_tail_probability_negative(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\n5\n-3\n10\n")
        candidates = _detect(TailProbabilityDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1

    def test_iqr_no_outliers(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\n1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n")
        assert _detect(IqrOutlierDetector(), ctx) == []


class TestPercentileOutlier:
    def test_reports_percentile_extremes(self, tmp_path: Path) -> None:
        rows = "".join(f"{i}\n" for i in range(2000))
        ctx = _ctx(tmp_path, "v\n" + rows + "99999\n-99999\n")
        candidates = _detect(PercentileOutlierDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count >= 2

    def test_clean_uniform_data(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\n" + "".join(f"{i % 50}\n" for i in range(500)))
        assert _detect(PercentileOutlierDetector(), ctx) == []


class TestHistogramRarity:
    def test_reports_rare_bins(self, tmp_path: Path) -> None:
        rows = "".join(f"{i % 100}\n" for i in range(200000))
        ctx = _ctx(tmp_path, "v\n" + rows + "2000\n3000\n")
        candidates = _detect(HistogramRarityDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 2

    def test_clean_small_dataset_no_issues(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\n1\n2\n3\n4\n")
        assert _detect(HistogramRarityDetector(), ctx) == []


class TestInvalidPhone:
    def test_reports_invalid_phones(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "phone\n+86 138-0013-8000\n12345\nnot-a-phone\n13800138000\n")
        candidates = _detect(InvalidPhoneDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 2

    def test_skips_non_phone_columns(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "name\nalice\nbob\n")
        assert _detect(InvalidPhoneDetector(), ctx) == []


class TestInvalidUrl:
    def test_reports_invalid_urls(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "website\nhttps://example.com\nnot-a-url\nftp://files.example.org\n")
        candidates = _detect(InvalidUrlDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1

    def test_skips_non_url_columns(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "name\nalice\nbob\n")
        assert _detect(InvalidUrlDetector(), ctx) == []


class TestInvalidIp:
    def test_reports_invalid_ips(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "ip_address\n192.168.1.1\n999.1.1.1\nnot-an-ip\n")
        candidates = _detect(InvalidIpDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 2

    def test_skips_zip_code_columns(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "zip\n12345\n54321\n")
        assert _detect(InvalidIpDetector(), ctx) == []


class TestInconsistentCase:
    def test_reports_case_variants(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nCalifornia\ncalifornia\nCALIFORNIA\nTexas\ntexas\n")
        candidates = _detect(InconsistentCaseDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].columns == ["v"]
        assert candidates[0].affected_count == 5

    def test_clean_single_case(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nalpha\nbeta\ngamma\n")
        assert _detect(InconsistentCaseDetector(), ctx) == []


class TestFormulaInjection:
    def test_reports_formula_prefixes(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nsafe\n=SUM(A1)\n+cmd\n-2\nsafe\n")
        candidates = _detect(FormulaInjectionDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 3

    def test_clean(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "v\nsafe\nvalue\n")
        assert _detect(FormulaInjectionDetector(), ctx) == []
