"""Step 71（ADR-071）抽样扫描测试：SampledDataHandle + runner 调度 + 报告标注。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry.cli import main
from datasentry_core.connectors import DataSourceSpec, DataSourceType, default_registry
from datasentry_core.connectors.sampling import SampledDataHandle
from datasentry_core.detectors import DetectionContext, DetectorRegistry
from datasentry_core.detectors.runner import ScanRunner
from datasentry_core.models.detector import (
    DetectorCapabilities,
    DetectorMeta,
    IssueCandidate,
)
from datasentry_core.models.enums import QualityDimension
from datasentry_core.models.scan import SamplingConfig, ScanConfig
from datasentry_core.reporting import build_report
from datasentry_core.reporting.html import render_html
from datasentry_core.reporting.markdown import render_markdown


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    p = tmp_path / "orders.csv"
    p.write_text(
        "id,amount,email\n"
        "1,10,a@x.co\n"
        "2,20,b@x.co\n"
        "3,30,c@x.co\n"
        "4,40,d@x.co\n"
        "5,50,e@x.co\n"
        "6,60,f@x.co\n"
        "7,70,g@x.co\n"
        "8,80,h@x.co\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def csv_handle(sample_csv: Path):
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=sample_csv)
    return default_registry().open(spec)


class FakeDetector:
    detector_id = "fake_count"
    detector_version = "1.0.0"

    def __init__(self, *, supports_sampling: bool = True) -> None:
        self._meta = DetectorMeta(
            detector_id=self.detector_id,
            display_name="FakeCount",
            description="counts rows via pushdown",
            quality_dimension=QualityDimension.COMPLETENESS,
            capabilities=DetectorCapabilities(
                supports_sampling=supports_sampling,
                supports_sql_pushdown=True,
            ),
        )

    def supports(self, context: DetectionContext) -> bool:
        return True

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        n = context.handle.sql_aggregate("SELECT count(*) FROM data").table.column(0).to_pylist()[0]
        return [
            IssueCandidate(
                issue_type="fake_issue",
                detector_id=self.detector_id,
                detector_version=self.detector_version,
                dataset_id=context.dataset_id,
                columns=context.columns,
                affected_count=int(n),
                raw_score=0.5,
                confidence=0.9,
                estimated_false_positive_risk=0.1,
                suggested_severity="low",
            )
        ]

    def metadata(self) -> DetectorMeta:
        return self._meta


def make_runner(detector: FakeDetector) -> ScanRunner:
    reg = DetectorRegistry()
    reg.register(detector)
    return ScanRunner(reg)


def _ctx(handle) -> DetectionContext:
    return DetectionContext(
        dataset_id="ds_1",
        table_name=None,
        columns=["id", "amount", "email"],
        handle=handle,
        config=ScanConfig(),
    )


class TestSampledDataHandle:
    def test_rewrite_top_level_from_data(self, csv_handle) -> None:
        sampled = SampledDataHandle(csv_handle, n=3, seed=7)
        rewritten = sampled._rewrite("SELECT count(*) FROM data WHERE amount IS NULL")
        assert "reservoir(3 ROWS)" in rewritten
        assert "REPEATABLE (7)" in rewritten
        assert rewritten.startswith("SELECT count(*) FROM (SELECT * FROM data USING SAMPLE")

    def test_rewrite_nested_from_data_also_rewritten(self, csv_handle) -> None:
        sampled = SampledDataHandle(csv_handle, n=3)
        rewritten = sampled._rewrite("SELECT * FROM (SELECT * FROM data) t")
        assert rewritten.count("reservoir(3 ROWS)") == 1

    def test_sql_aggregate_returns_sampled_rows(self, csv_handle) -> None:
        sampled = SampledDataHandle(csv_handle, n=3, seed=7)
        n = sampled.sql_aggregate("SELECT count(*) FROM data").table.column(0).to_pylist()[0]
        assert int(n) == 3
        csv_handle.close()

    def test_sampling_reproducible(self, csv_handle) -> None:
        first = SampledDataHandle(csv_handle, n=4, seed=7)
        second = SampledDataHandle(csv_handle, n=4, seed=7)
        a = first.sql_aggregate("SELECT id FROM data ORDER BY id").table
        b = second.sql_aggregate("SELECT id FROM data ORDER BY id").table
        assert a.to_pylist() == b.to_pylist()
        csv_handle.close()

    def test_count_rows_min_of_n_and_full(self, csv_handle) -> None:
        assert SampledDataHandle(csv_handle, n=3).count_rows() == 3
        assert SampledDataHandle(csv_handle, n=10**6).count_rows() == 8
        csv_handle.close()


class TestRunnerSamplingDispatch:
    def test_sampling_enabled_detector_gets_sampled_handle(self, csv_handle) -> None:
        runner = make_runner(FakeDetector(supports_sampling=True))
        config = ScanConfig(sampling=SamplingConfig(method="reservoir", sample_size=3, seed=7))
        runs, issues = runner.run(_ctx(csv_handle), config, "scan_1")
        assert runs[0].sampling is not None
        assert runs[0].sampling.sampled is True
        assert runs[0].sampling.method == "reservoir"
        assert runs[0].sampling.sample_size == 3
        assert runs[0].sampling.full_size == 8
        assert runs[0].sampling.generalizable is True
        assert issues[0].affected_count == 3
        csv_handle.close()

    def test_non_sampling_detector_stays_full(self, csv_handle) -> None:
        runner = make_runner(FakeDetector(supports_sampling=False))
        config = ScanConfig(sampling=SamplingConfig(method="reservoir", sample_size=3))
        runs, issues = runner.run(_ctx(csv_handle), config, "scan_1")
        assert runs[0].sampling is None
        assert issues[0].affected_count == 8
        csv_handle.close()

    def test_default_config_is_full_scan(self, csv_handle) -> None:
        runner = make_runner(FakeDetector())
        runs, issues = runner.run(_ctx(csv_handle), ScanConfig(), "scan_1")
        assert runs[0].sampling is None
        assert issues[0].affected_count == 8
        csv_handle.close()

    def test_sampling_method_none_disables(self, csv_handle) -> None:
        runner = make_runner(FakeDetector())
        config = ScanConfig(sampling=SamplingConfig(method="none", sample_size=3))
        runs, issues = runner.run(_ctx(csv_handle), config, "scan_1")
        assert runs[0].sampling is None
        assert issues[0].affected_count == 8
        csv_handle.close()

    def test_sampling_ratio_resolution(self, csv_handle) -> None:
        runner = make_runner(FakeDetector())
        config = ScanConfig(sampling=SamplingConfig(method="reservoir", ratio=0.5))
        runs, _ = runner.run(_ctx(csv_handle), config, "scan_1")
        assert runs[0].sampling is not None
        assert runs[0].sampling.sample_size == 4
        csv_handle.close()


class TestSamplingEndToEnd:
    def test_scan_file_with_sampling_marks_detector_runs(
        self, sample_csv: Path, tmp_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        client = DataSentry(project=ws)
        config = ScanConfig(sampling=SamplingConfig(method="reservoir", sample_size=2, seed=7))
        scan, runs, _ = client.scan_file(sample_csv, config=config)
        assert scan.status == "completed"
        sampled_runs = [r for r in runs if r.sampling is not None]
        assert sampled_runs, "抽样支撑检测器应带 SamplingInfo"
        assert all(r.sampling.sample_size == 2 for r in sampled_runs)
        assert all(r.sampling.full_size == 8 for r in sampled_runs)
        client.close()

    def test_report_html_and_markdown_annotate_sampling(
        self, sample_csv: Path, tmp_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        client = DataSentry(project=ws)
        config = ScanConfig(sampling=SamplingConfig(method="reservoir", sample_size=2, seed=7))
        scan, runs, issues = client.scan_file(sample_csv, config=config)
        report = build_report(scan, runs, issues, scan.quality_score)
        html = render_html(report)
        md = render_markdown(report)
        assert "sampled" in html
        assert "sampling" in md
        json_text = json.dumps(report)
        assert json_text.count('"sampling"') > 0
        client.close()


class TestCliSampling:
    def test_cli_scan_sampling_size(self, sample_csv: Path, workspace: Path, capsys) -> None:
        rc = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "scan",
                str(sample_csv),
                "--sampling-size",
                "2",
                "--sampling-seed",
                "7",
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        summary = json.loads(out)
        assert summary["data"]["status"] == "completed"
        assert summary["data"]["row_count"] == 8

    def test_cli_scan_without_sampling_is_full(
        self, sample_csv: Path, workspace: Path, capsys
    ) -> None:
        rc = main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out)["data"]["row_count"] == 8


class TestPipelineSlimming:
    class CountingHandle:
        """记录 count_rows 调用次数的包装句柄（Step 72/ADR-072）。"""

        def __init__(self, inner) -> None:
            self._inner = inner
            self.count_calls = 0

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def count_rows(self) -> int:
            self.count_calls += 1
            return self._inner.count_rows()

    def test_count_rows_called_once_per_scan(self, csv_handle) -> None:
        runner = make_runner(FakeDetector())
        counting = self.CountingHandle(csv_handle)
        ctx = _ctx(counting)
        ctx.handle = counting
        runner.run(ctx, ScanConfig(), "scan_1")
        assert counting.count_calls == 1
        csv_handle.close()

    def test_sampling_scan_count_rows_once(self, csv_handle) -> None:
        runner = make_runner(FakeDetector())
        counting = self.CountingHandle(csv_handle)
        ctx = _ctx(counting)
        ctx.handle = counting
        config = ScanConfig(sampling=SamplingConfig(method="reservoir", sample_size=3))
        runner.run(ctx, config, "scan_1")
        assert counting.count_calls == 1
        csv_handle.close()

    def test_sampled_detector_rows_scanned_is_sample_size(self, csv_handle) -> None:
        runner = make_runner(FakeDetector())
        config = ScanConfig(sampling=SamplingConfig(method="reservoir", sample_size=3))
        runs, _ = runner.run(_ctx(csv_handle), config, "scan_1")
        assert runs[0].rows_scanned == 3
        csv_handle.close()

    def test_profiler_reuses_provided_row_count(self, sample_csv: Path) -> None:
        from datasentry_core.engine.profiler import Profiler

        spec = DataSourceSpec(source_type=DataSourceType.CSV, path=sample_csv)
        handle = default_registry().open(spec)
        profile = Profiler(handle, dataset_id="ds").profile(row_count=123)
        assert profile.row_count == 123
        handle.close()

    def test_anomaly_ml_sql_side_sampling_reproducible(self, tmp_path: Path) -> None:
        from datasentry_core.detectors.initial.anomaly_ml import ModelOutlierDetector

        path = tmp_path / "big.csv"
        lines = ["value"] + [str(1 + (i % 7)) for i in range(1000)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        spec = DataSourceSpec(source_type=DataSourceType.CSV, path=path)
        handle = default_registry().open(spec)
        ctx = DetectionContext(
            dataset_id="ml",
            table_name=None,
            columns=["value"],
            handle=handle,
            config=ScanConfig(detector_params={"max_samples": 50}, seed=7),
        )
        try:
            first = ModelOutlierDetector().detect(ctx)
            second = ModelOutlierDetector().detect(ctx)
            assert [c.model_dump() for c in first] == [c.model_dump() for c in second]
        finally:
            handle.close()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws
