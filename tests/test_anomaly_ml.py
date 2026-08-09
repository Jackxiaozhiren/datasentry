"""Step 42：IF/LOF 模型异常检测器。"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from datasentry import DataSentry
from datasentry_core.connectors.registry import default_registry
from datasentry_core.connectors.spec import DataSourceSpec
from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.initial.anomaly_ml import ModelOutlierDetector
from datasentry_core.models.enums import QualityDimension
from datasentry_core.models.scan import ScanConfig

DETECTOR = ModelOutlierDetector()


def _write_csv(path: Path, columns: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rows)


def _make_context(path: Path, config: ScanConfig | None = None) -> DetectionContext:
    spec = DataSourceSpec(source_type="csv", path=path, options={})
    handle = default_registry().open(spec)
    return DetectionContext(
        dataset_id="ml",
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
        config=config or ScanConfig(),
    )


@pytest.fixture()
def gaussian_with_outliers(tmp_path: Path) -> Path:
    path = tmp_path / "measurements.csv"
    rng = np.random.default_rng(7)
    normal = rng.normal(0.0, 1.0, size=200)
    outliers = np.array([50.0, -45.0, 40.0])
    values = np.concatenate([normal, outliers])
    rows = [[float(v)] for v in values]
    _write_csv(path, ["value"], rows)
    return path


def test_supports_numeric_columns(gaussian_with_outliers: Path) -> None:
    context = _make_context(gaussian_with_outliers)
    try:
        assert DETECTOR.supports(context)
    finally:
        context.handle.close()


def test_isolation_forest_detects_outliers(gaussian_with_outliers: Path) -> None:
    context = _make_context(gaussian_with_outliers)
    try:
        candidates = DETECTOR.detect(context)
        assert len(candidates) == 1
        issue = candidates[0]
        assert issue.issue_type == "model_outlier"
        assert issue.columns == ["value"]
        assert issue.affected_count >= 2
        data = issue.evidence[0].data
        assert data["model"] == "isolation_forest"
        assert data["anomaly_count"] == issue.affected_count
        assert abs(data["anomaly_ratio"] - issue.affected_count / 203) < 0.02
        assert max(abs(v) for v in data["examples"]) > 10
    finally:
        context.handle.close()


def test_local_outlier_factor_model(gaussian_with_outliers: Path) -> None:
    config = ScanConfig(detector_params={"model": "local_outlier_factor"})
    context = _make_context(gaussian_with_outliers, config)
    try:
        candidates = DETECTOR.detect(context)
        assert len(candidates) == 1
        assert candidates[0].evidence[0].data["model"] == "local_outlier_factor"
    finally:
        context.handle.close()


def test_too_few_rows_skipped(tmp_path: Path) -> None:
    path = tmp_path / "tiny.csv"
    _write_csv(path, ["v"], [[1.0], [2.0], [3.0]])
    context = _make_context(path)
    try:
        assert DETECTOR.detect(context) == []
    finally:
        context.handle.close()


def test_no_numeric_columns_not_supported(tmp_path: Path) -> None:
    path = tmp_path / "names.csv"
    _write_csv(path, ["name"], [["a"], ["b"], ["c"]])
    context = _make_context(path)
    try:
        assert not DETECTOR.supports(context)
        assert DETECTOR.detect(context) == []
    finally:
        context.handle.close()


def test_clean_data_no_issue(tmp_path: Path) -> None:
    path = tmp_path / "clean.csv"
    rng = np.random.default_rng(3)
    rows = [[float(v)] for v in rng.normal(0.0, 1.0, size=100)]
    _write_csv(path, ["v"], rows)
    context = _make_context(path)
    try:
        assert DETECTOR.detect(context) == []
    finally:
        context.handle.close()


def test_end_to_end_through_client(tmp_path: Path, gaussian_with_outliers: Path) -> None:
    client = DataSentry(project=tmp_path / "ws")
    try:
        scan, _, issues = client.scan_file(gaussian_with_outliers)
        assert scan.status == "completed"
        outliers = [i for i in issues if i.issue_type == "distribution_anomaly"]
        assert len(outliers) == 1
        assert outliers[0].affected_count >= 2
        assert QualityDimension.DISTRIBUTION_STABILITY in outliers[0].quality_dimensions
        assert outliers[0].severity == "low"
    finally:
        client.close()
