# DataSentry benchmarks

DataSentry treats performance claims as reproducible engineering evidence rather than fixed marketing numbers.

## Run the benchmark

```bash
uv sync
uv run python benchmarks/bench_scan.py 1000000 42
```

Optional sampling benchmark:

```bash
uv run python benchmarks/bench_scan.py 1000000 42 --sampling-size 200000
```

The benchmark generates synthetic data with a stable seed and injects a small amount of dirty data. It measures the major execution stages rather than timing only file loading.

## What is measured

The current benchmark reports:

- data generation and input size;
- schema open time;
- profiling time;
- per-detector timing and the slowest detectors;
- aggregate numeric-outlier detector time;
- full scan time, including detection, evidence fusion, and scoring;
- JSONL streaming read time;
- process RSS high-water tracking;
- optional reservoir-sampling scan time;
- full-vs-sampled quality-score drift.

## Performance budgets

The source of truth for acceptance and optimization budgets is `benchmarks/bench_scan.py`.

Do not copy a benchmark number into README, release notes, or social posts without also recording:

1. DataSentry version or commit SHA.
2. Dataset dimensions and file size.
3. Benchmark command and seed.
4. CPU / machine model.
5. Memory.
6. Operating system.
7. Python version.
8. Median of repeated runs when publishing comparative results.

## Why there is no single “1M rows in X seconds” promise

A scan depends on column types, cardinality, active detectors, storage format, hardware, and operating-system caching. A number without those conditions is easy to quote but difficult for another user to reproduce.

DataSentry therefore keeps executable acceptance gates in the repository and encourages users to run the same workload on their own hardware.

## Publishing benchmark results

When publishing a release benchmark, use a table similar to this:

| Field | Value |
|---|---|
| DataSentry | `vX.Y.Z` / commit SHA |
| Machine | model |
| CPU | model / cores |
| Memory | GB |
| OS | version |
| Python | version |
| Rows × columns | value |
| Input size | MB |
| Command | exact command |
| Profile | seconds |
| Full scan | seconds |
| Numeric outliers | seconds |
| Peak RSS delta | MB |
| Runs | N |

If sampling is enabled, include sample size, sampling method, seed, and quality-score drift.

## Regression policy

Performance changes should be reviewed like functional regressions:

- changes that cross an acceptance threshold should block release until understood;
- changes that materially improve or worsen a detector should include before/after measurements;
- benchmark methodology changes must be documented so historical numbers are not compared as if they used the same workload;
- memory high-water measurements should be treated carefully because process RSS can retain allocator and DuckDB buffers after a stage completes.
