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

## Cold start budget (R-22)

The first screen of this product is not a scan, it is "how long until I see a report". This section measures exactly that on a clean machine with the published package, because the install path had been an anecdote with no reading behind it.

Instrument (`benchmarks/probe_r22_cold_start.py`), one arm = one brand-new virtualenv:

```bash
python3 benchmarks/probe_r22_cold_start.py --arms 3 \
    --out "$HOME/r22-cold-start.jsonl" \
    --workdir "$HOME/r22-cold-start-work"
```

Each arm creates a venv, runs `pip install --no-cache-dir --index-url https://pypi.org/simple datasentry-ai`, then `datasentry demo` in an isolated directory with `TMPDIR` pointed at scratch, and records the `venv` / `install` / `demo` wall clocks, the network condition read at probe time (not recalled afterwards), and the byte size of the report the demo says it wrote.

Committed evidence: `benchmarks/r22_cold_start_probe.jsonl` (4 lines: 3 successful arms plus 1 arm whose package download broke, kept rather than deleted). Environment: macOS 27.2 / arm64, CPython 3.13.12, package `datasentry-ai==2.0.0` from the **official** index with `PIP_INDEX_URL` and `PIP_EXTRA_INDEX_URL` unset, so no mirror was in play.

| Fixture | Ceiling (median of >= 3 successful arms) |
|---|---|
| Clean venv -> `datasentry demo` report, official PyPI, no mirror | <= 1800 s |

The three successful arms: install 977.8 / 1052.2 / 1070.2 s, demo 7.6 / 4.9 / 6.6 s, total 988.3 / 1058.3 / 1077.9 s, so the **median is 1058.3 s (17.6 min)** and the worst arm is 1077.9 s. All three recorded a **36 875-byte** report. The ceiling is ~1.7x the median and above the worst arm; the test rejects a ceiling above 4x the median, so "declare it enormous and it can never go red" fails too.

This number is a function of the hour, not only of the code, and the evidence says so:

- An earlier 3-arm run on the same host and index, taken before the measurement bug below was fixed, came in at install 764.2 / 324.0 / 233.1 s and total 769.9 / 362.9 / 276.2 s (median 362.9 s). Its three reports were verified afterwards by matching each arm's start and end time to a 36 875-byte `report.html` on disk (16:06:09, 16:12:12, 16:16:49 local). The same path has therefore been observed at a 6.0-minute median and at a 17.6-minute median within a few hours; the budget is set on the slower window.
- One arm in attempt B never finished installing: `incomplete-download` after 483 s (pyarrow 37.3 MB, 3.1 MB received, `SSL: UNEXPECTED_EOF_WHILE_READING`, six attempts). It is line 1 of the evidence file with `install_rc: 1`, because a budget that hides its failures is not a budget.
- The official index, not the code, is the dominant term. That is why this stays an on-demand instrument rather than a CI gate.

Measurement bug worth naming: `datasentry demo` writes its artefacts to `tempfile.mkdtemp()`, **not** the current directory. The first version of this probe counted `*.html` under the cwd, recorded `report_count=0`, and would have "proved" that no report was produced while three existed. It now parses the `HTML report:` line and stats the file, which is where `report_bytes` comes from.

What is deliberately **not** claimed: a per-region or per-network promise; a Chinese-mainland reading through a mirror (mirrors were deliberately not used, because the official source is the worst realistic case the PRD asks to cover); a warm-cache reading (`--no-cache-dir` was used); or that the 4.9-7.6 s spread in `demo` is code variance rather than disk and CPU contention on a shared host.

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
