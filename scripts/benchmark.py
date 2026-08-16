"""DataSentry 端到端性能基准（V27）：可复现的本地扫描耗时测量。

用法：
    uv run python scripts/benchmark.py                # markdown 表
    uv run python scripts/benchmark.py --json out.json
    uv run python scripts/benchmark.py --max-rows 100000   # 限制最大规模

输出为当前机器实测（Apple Silicon 参考值），非绝对性能承诺。
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
import time
from pathlib import Path

from datasentry import DataSentry


def make_dataset(path: Path, rows: int, seed: int = 7) -> None:
    rng = random.Random(seed)
    names = ["alice", "bob", "carol", "dave", "erin", "frank", "grace"]
    statuses = ["Active", "inactive", "pending", "n/a"]
    with path.open("w", encoding="utf-8") as fh:
        fh.write("name,status,price,total,created_at,email\n")
        for i in range(rows):
            name = rng.choice(names)
            status = rng.choice(statuses)
            price = "9999" if rng.random() < 0.01 else str(rng.randint(1, 5000))
            total = f"{rng.uniform(1, 1000):.2f}"
            created = (
                f"2026-0{rng.randint(1, 6)}-{rng.randint(1, 28):02d} "
                f"1{rng.randint(0, 2)}:{rng.randint(0, 59):02d}"
            )
            email = f"{name}{i}@example.com" if rng.random() > 0.03 else "not-an-email"
            fh.write(f"{name},{status},{price},{total},{created},{email}\n")


def run_benchmark(max_rows: int, seed: int = 7) -> list[dict[str, float | int]]:
    results: list[dict[str, float | int]] = []
    with tempfile.TemporaryDirectory(prefix="ds-bench-") as tmp:
        project = Path(tmp) / "project"
        project.mkdir()
        client = DataSentry(project)
        for rows in (10_000, 100_000, 300_000):
            if rows > max_rows:
                continue
            path = Path(tmp) / f"ds_{rows}.csv"
            make_dataset(path, rows, seed=seed)
            start = time.perf_counter()
            scan_run, _runs, _issues = client.scan_file(str(path))
            elapsed = time.perf_counter() - start
            results.append(
                {
                    "rows": rows,
                    "seconds": round(elapsed, 2),
                    "rows_per_second": round(rows / elapsed),
                    "score": round(scan_run.quality_score.overall, 1)
                    if scan_run.quality_score
                    else None,
                }
            )
    return results


def render_markdown(results: list[dict[str, float | int]]) -> str:
    lines = [
        "| rows | seconds | rows/s | overall |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        lines.append(
            f"| {r['rows']:,} | {r['seconds']:.2f} | {r['rows_per_second']:,} | {r['score']} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="DataSentry 端到端扫描基准")
    parser.add_argument("--max-rows", type=int, default=300_000)
    parser.add_argument("--json", type=str, default="")
    args = parser.parse_args()

    results = run_benchmark(args.max_rows)
    if args.json:
        Path(args.json).write_text(
            json.dumps({"machine": "local", "results": results}, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.json}")
    print(render_markdown(results))


if __name__ == "__main__":
    main()
