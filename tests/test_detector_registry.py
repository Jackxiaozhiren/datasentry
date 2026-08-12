"""Step 5 Detector registry 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasentry_core.connectors import (
    CsvConnector,
    DataSourceSpec,
    DataSourceType,
    default_registry,
)
from datasentry_core.detectors import DetectionContext, DetectorRegistry, filter_by_config
from datasentry_core.models.detector import (
    DetectorCapabilities,
    DetectorMeta,
    IssueCandidate,
)
from datasentry_core.models.enums import QualityDimension


class FakeDetector:
    detector_id = "fake_detector"
    detector_version = "1.0.0"

    def __init__(self, dimension: QualityDimension = QualityDimension.COMPLETENESS) -> None:
        self._meta = DetectorMeta(
            detector_id=self.detector_id,
            display_name="Fake",
            description="test",
            quality_dimension=dimension,
            capabilities=DetectorCapabilities(supports_streaming=True, supports_sql_pushdown=True),
        )

    def supports(self, context: DetectionContext) -> bool:
        return len(context.columns) > 0

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        return [
            IssueCandidate(
                issue_type="fake_issue",
                detector_id=self.detector_id,
                detector_version=self.detector_version,
                dataset_id=context.dataset_id,
                columns=context.columns,
                affected_count=1,
                raw_score=0.5,
                confidence=0.9,
                estimated_false_positive_risk=0.1,
                suggested_severity="low",
            )
        ]

    def metadata(self) -> DetectorMeta:
        return self._meta


@pytest.fixture
def ctx(tmp_path: Path) -> DetectionContext:
    p = tmp_path / "d.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=p)
    return DetectionContext(
        dataset_id="ds_1",
        table_name=None,
        columns=["a", "b"],
        handle=CsvConnector().open(spec),
    )


class TestDetectorRegistry:
    def test_register_and_get(self) -> None:
        reg = DetectorRegistry()
        d = FakeDetector()
        reg.register(d)
        assert reg.get("fake_detector") is d

    def test_register_duplicate_rejected(self) -> None:
        reg = DetectorRegistry()
        reg.register(FakeDetector())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(FakeDetector())

    def test_get_missing_raises(self) -> None:
        reg = DetectorRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.get("nope")

    def test_list_preserves_order(self) -> None:
        reg = DetectorRegistry()
        d1, d2 = make_fake("d1"), make_fake("d2")
        reg.register(d1)
        reg.register(d2)
        assert reg.list() == [d1, d2]

    def test_list_filters_by_capability(self) -> None:
        reg = DetectorRegistry()
        streaming = make_fake("streaming")
        plain = make_fake("plain")
        plain._meta.capabilities = DetectorCapabilities()
        reg.register(streaming)
        reg.register(plain)
        filtered = reg.list(capability="supports_streaming")
        assert filtered == [streaming]

    def test_enable_disable(self) -> None:
        reg = DetectorRegistry()
        d = FakeDetector()
        reg.register(d)
        assert reg.is_enabled("fake_detector")
        reg.enable("fake_detector", False)
        assert not reg.is_enabled("fake_detector")
        assert reg.list_active() == []
        reg.enable("fake_detector", True)
        assert reg.list_active() == [d]

    def test_enable_unknown_raises(self) -> None:
        reg = DetectorRegistry()
        with pytest.raises(KeyError):
            reg.enable("nope", False)

    def test_filter_by_config(self) -> None:
        d1, d2 = FakeDetector(), FakeDetector()
        assert filter_by_config([d1, d2], None) == [d1, d2]
        assert filter_by_config([d1, d2], ["fake_detector"]) == [d1, d2]
        assert filter_by_config([d1, d2], []) == []

    def test_detect_roundtrip(self, ctx: DetectionContext) -> None:
        reg = DetectorRegistry()
        reg.register(FakeDetector())
        d = reg.get("fake_detector")
        assert d.supports(ctx)
        candidates = d.detect(ctx)
        assert len(candidates) == 1
        assert candidates[0].detector_id == "fake_detector"
        assert candidates[0].columns == ["a", "b"]
        assert candidates[0].confidence == 0.9
        ctx.handle.close()

    def test_default_registry_not_contaminated(self) -> None:
        """默认连接器注册表（函数工厂）不受测试污染：仅含内置连接器。"""
        assert [c.connector_id for c in default_registry().list()] == [
            "csv",
            "parquet",
            "jsonl",
            "xlsx",
            "duckdb",  # Step 38：DuckDB 文件连接器入默认注册表
            "sqlite",  # Step 54：SQLite 文件连接器入默认注册表
            "postgres",  # Step 55：PostgreSQL 连接器入默认注册表（V4）
            "mysql",  # Step 56：MySQL 连接器入默认注册表（V5）
        ]


def make_fake(detector_id: str) -> FakeDetector:
    """构造指定 detector_id 的 FakeDetector 子类（类属性协议）。"""
    cls = type(f"FakeDetector_{detector_id}", (FakeDetector,), {"detector_id": detector_id})
    return cls()
