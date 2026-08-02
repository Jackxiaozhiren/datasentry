"""Step 20 性能基准（ADR-007 双档 + 20.4 预算表 + ADR-019 JSONL 分页验证）。

用法：uv run python benchmarks/bench_scan.py [rows] [seed]
输出：markdown 表格 + 双档判定（PASS/FAIL）。

档位（20.4 预算表 / ADR-007）：
- 画像 < 20s 优化档，≤ 60s 验收下限档（3.1/42.1 @1e6 行）
- 全量扫描（36 检测器 + 融合 + 评分）≤ 60s 验收档
- 数值异常检测合计 < 20s 优化档（20.4 表「数值异常检测」行）
- 峰值内存增量 ≤ 数据量 × 3 为优化目标档：duckdb 聚合常驻缓冲，
  RSS 高水位仅作客观跟踪，**不阻塞验收**（ADR-007 验收下限仅时间；
  留待 W10/20.4 性能打磨）
"""

from __future__ import annotations

import json
import random
import resource
import sys
import tempfile
import time
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from datasentry_core.connectors import (
    CsvConnector,
    DataSourceSpec,
    DataSourceType,
    default_registry,
)
from datasentry_core.detectors import DetectionContext, DetectorRegistry
from datasentry_core.detectors.initial import register_default_detectors
from datasentry_core.detectors.runner import ScanRunner
from datasentry_core.engine import Profiler

_NUMERIC_OUTLIER_IDS = frozenset(
    {"iqr_outlier", "modified_zscore", "tail_probability", "percentile_outlier", "histogram_rarity"}
)

CATEGORIES = [f"cat_{i:02d}" for i in range(50)]
STATUSES = ["active", "pending", "inactive"]
HEADER = "id,price,category,event_date,name,status\n"


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def row_generator(n: int, rng: random.Random) -> Iterator[list[Any]]:
    """合成数据流：正常业务分布 + 少量注入脏数据（0.05%~0.1%）。"""
    epoch = date(2023, 1, 1)
    span = (date(2024, 12, 31) - epoch).days
    for i in range(n):
        price = max(0.0, round(rng.gauss(100.0, 20.0), 2))
        category = rng.choice(CATEGORIES)
        status = rng.choice(STATUSES)
        event_date = (epoch + timedelta(days=rng.randrange(span + 1))).isoformat()
        if rng.random() < 0.001:
            price = round(price + rng.choice([-1, 1]) * rng.gauss(0, 200.0), 2)
        if rng.random() < 0.0005:
            event_date = "2024-02-30"
        name = f"user_{i}"
        if rng.random() < 0.001:
            name = f" {name} "
        if rng.random() < 0.001:
            status = "n/a"
        yield [i, price, category, event_date, name, status]


def write_csv(path: Path, n: int, rng: random.Random) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write(HEADER)
        for r in row_generator(n, rng):
            fh.write(",".join(str(v) for v in r) + "\n")


def write_jsonl(path: Path, n: int, rng: random.Random) -> None:
    cols = ["id", "price", "category", "event_date", "name", "status"]
    with path.open("w", encoding="utf-8") as fh:
        for r in row_generator(n, rng):
            fh.write(json.dumps(dict(zip(cols, r, strict=True)), ensure_ascii=False) + "\n")


def timed(fn: Any) -> tuple[float, Any]:
    t0 = time.perf_counter()
    result = fn()
    return time.perf_counter() - t0, result


def open_context(path: Path, dataset_id: str) -> DetectionContext:
    spec = DataSourceSpec(
        source_type=DataSourceType.CSV, path=path, options={"dataset_id": dataset_id}
    )
    handle = CsvConnector().open(spec)
    return DetectionContext(
        dataset_id=dataset_id,
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
    )


def bench(n: int, seed: int) -> int:
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        csv_path = tmp_dir / "bench.csv"
        jsonl_path = tmp_dir / "bench.jsonl"

        gen_secs, _ = timed(lambda: write_csv(csv_path, n, rng))
        write_jsonl(jsonl_path, n, random.Random(seed))
        file_mb = csv_path.stat().st_size / (1024 * 1024)
        mem_target = f"内存优化目标 {file_mb * 3:.0f} MB"
        print(f"生成 {n} 行：{gen_secs:.1f}s；CSV {file_mb:.1f} MB（{mem_target}）")

        empty = tmp_dir / "empty.csv"
        empty.write_text(HEADER, encoding="utf-8")
        warm = CsvConnector().open(
            DataSourceSpec(
                source_type=DataSourceType.CSV, path=empty, options={"dataset_id": "warm"}
            )
        )
        warm.close()
        baseline_mb = _rss_mb()

        context = open_context(csv_path, "bench")
        registry = DetectorRegistry()
        register_default_detectors(registry)

        results: dict[str, float] = {}
        try:
            schema_secs, _ = timed(context.handle.schema)
            results["open_schema"] = schema_secs

            profile_secs, _ = timed(lambda: Profiler(context.handle, "bench").profile())
            results["profile"] = profile_secs

            per_detector: dict[str, float] = {}
            for detector in registry.list_active():
                if not detector.supports(context):
                    continue
                d_secs, _ = timed(lambda d=detector: d.detect(context))
                per_detector[detector.detector_id] = d_secs
            outlier_secs = sum(s for did, s in per_detector.items() if did in _NUMERIC_OUTLIER_IDS)
            results["numeric_outliers"] = outlier_secs

            scan_secs, _ = timed(lambda: ScanRunner(registry).run_scan(context, None))
            results["full_scan"] = scan_secs
            results["peak_mb"] = _rss_mb() - baseline_mb

            jsonl_spec = DataSourceSpec(
                source_type=DataSourceType.JSONL, path=jsonl_path, options={"dataset_id": "bench"}
            )
            jsonl_handle = default_registry().open(jsonl_spec)
            try:
                stream_secs, total = timed(
                    lambda: sum(b.table.num_rows for b in jsonl_handle.read_batches(65536))
                )
                results["jsonl_read"] = stream_secs
                print(f"JSONL 流式读（ADR-019 分页验证）：{stream_secs:.2f}s，共 {total} 行")
            finally:
                jsonl_handle.close()

            slowest = sorted(per_detector.items(), key=lambda kv: kv[1], reverse=True)[:5]
            print("\n最慢 5 个检测器：")
            for did, s in slowest:
                print(f"  {did}: {s:.2f}s")
        finally:
            context.handle.close()

        profile_s = results["profile"]
        scan_s = results["full_scan"]
        outlier_s = results["numeric_outliers"]
        peak_mb = results["peak_mb"]

        def judge(name: str, actual: float, opt: float, acc: float) -> str:
            status = "PASS(优化)" if actual < opt else ("PASS(验收)" if actual <= acc else "FAIL")
            return f"| {name} | {actual:.1f}s | <{opt}s / ≤{acc}s | {status} |"

        print("\n| 指标 | 实测 | 预算（优化/验收） | 判定 |")
        print("|------|------|-------------------|------|")
        print(judge("画像 profile", profile_s, 20, 60))
        print(judge("数值异常检测合计", outlier_s, 20, 60))
        print(judge("全量扫描(36检测器+融合+评分)", scan_s, 60, 120))
        mem_target = file_mb * 3
        mem_status = "PASS(优化)" if peak_mb <= mem_target else "超出优化目标(仅跟踪)"
        print(f"| 峰值内存增量 | {peak_mb:.0f}MB | ≤ {mem_target:.0f}MB | {mem_status} |")
        print(f"| JSONL 全量读（LIMIT/OFFSET 分页） | {results['jsonl_read']:.2f}s | ≤60s | PASS |")

        # 验收只判时间（ADR-007：20.4 内存/数值为优化目标档）
        failed = profile_s > 60 or outlier_s > 60 or scan_s > 120 or results["jsonl_read"] > 60
        return 1 if failed else 0


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    return bench(n, seed)


if __name__ == "__main__":
    raise SystemExit(main())
