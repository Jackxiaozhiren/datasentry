"""Step 17 表示变体与编码检测器族测试（11.7/11.9，ADR-018）。"""

from __future__ import annotations

from pathlib import Path

from datasentry_core.connectors import CsvConnector, DataSourceSpec, DataSourceType
from datasentry_core.detectors import DetectionContext
from datasentry_core.detectors.textual_variants import (
    FullwidthCharacterDetector,
    InvalidNumericDetector,
    MojibakeCharacterDetector,
    SpellingVariantDetector,
)


def _ctx(tmp_path: Path, csv_text: str) -> DetectionContext:
    p = tmp_path / "data.csv"
    p.write_text(csv_text, encoding="utf-8")
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=p, options={"dataset_id": "test"})
    handle = CsvConnector().open(spec)
    return DetectionContext(
        dataset_id="test",
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
    )


def _detect(detector: object, ctx: DetectionContext):
    try:
        return detector.detect(ctx)  # type: ignore[attr-defined]
    finally:
        ctx.handle.close()


class TestSpellingVariant:
    def test_reports_separator_variants(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "sku\n"
            + "\n".join(f"A-1000{i}" for i in range(5) for _ in range(3))
            + "\n"
            + "\n".join(f"A1000{i}" for i in range(5) for _ in range(1))
            + "\n",
        )
        candidates = _detect(SpellingVariantDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 20
        variants = candidates[0].evidence[0].data["variants"]
        assert "A-10000" in variants and "A10000" in variants

    def test_reports_nothing_when_distinct(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "code\n" + "\n".join(f"P-{i:04d}" for i in range(50)) + "\n",
        )
        candidates = _detect(SpellingVariantDetector(), ctx)
        assert candidates == []


class TestFullwidthCharacter:
    def test_reports_fullwidth_mixing(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "name\n"
            + "\n".join(f"用户{i:02d}" for i in range(20))
            + "\n"
            + "\n".join(f"用户{chr(0xFF10 + i)}" for i in range(5))
            + "\n",
        )
        candidates = _detect(FullwidthCharacterDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 5
        assert candidates[0].suggested_severity == "low"

    def test_reports_nothing_for_plain_cjk(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "name\n" + "\n".join(f"用户{i}" for i in range(20)) + "\n",
        )
        candidates = _detect(FullwidthCharacterDetector(), ctx)
        assert candidates == []


class TestMojibakeCharacter:
    def test_reports_replacement_char(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "note\n" + "\n".join(f"ok-{i}" for i in range(20)) + "\n" + "bad\ufffdvalue\n",
        )
        candidates = _detect(MojibakeCharacterDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1

    def test_reports_nothing_when_clean(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "note\n" + "\n".join(f"ok-{i}" for i in range(20)) + "\n",
        )
        candidates = _detect(MojibakeCharacterDetector(), ctx)
        assert candidates == []


class TestInvalidNumeric:
    def test_reports_text_in_price_column(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "price\n"
            + "\n".join(f"{i * 10}" for i in range(30))
            + "\n"
            + "\n".join("面议" for _ in range(3))
            + "\n",
        )
        candidates = _detect(InvalidNumericDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 3
        assert candidates[0].suggested_severity == "medium"

    def test_reports_nothing_for_numeric_values(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "amount\n" + "\n".join(f"{i * 10}.5" for i in range(30)) + "\n",
        )
        candidates = _detect(InvalidNumericDetector(), ctx)
        assert candidates == []

    def test_reports_nothing_for_plain_text_column(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "description\n" + "\n".join(f"some text {i}" for i in range(30)) + "\n",
        )
        candidates = _detect(InvalidNumericDetector(), ctx)
        assert candidates == []
