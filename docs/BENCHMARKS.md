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

## Repair path

`benchmarks/bench_scan.py` budgets the **scan** only. The repair chain
(`propose → preview → apply → diff/verify/rollback`) has no published acceptance budget, and
this section deliberately does not invent one. Invariant 6 requires a published performance
statement to be reproducible with its methodology written down; a number that can only be
reproduced from an untracked local audit workspace does not meet that bar.

What is known, without a ceiling:

- The stage worth budgeting is `apply`, reached through `client.repair_apply`. Its window covers
  `repair_open` + `engine.propose` + `engine.apply` + the store write. `preview`, `verify` and
  `rollback` are not timed by any instrument in this repository; `scan_file` and `repair_diff`
  are timed only to show their share.
- A local, on-demand instrument once measured that window over generated CSV/JSONL fixtures at
  50 000 / 200 000 / 1 000 000 rows with paired repeats. The instrument, its readings and the
  review notes that discussed them are **not tracked in this repository**: the audit workspace is
  excluded by `.gitignore`, and no file under it is versioned. Those readings are historical
  context, not a budget, and cannot be reproduced from a clean clone.
- Two measurement pitfalls are recorded so a future attempt does not repeat them. A bare linear
  ceiling breaches at small row counts because opening two DuckDB views is a fixed cost; adding a
  constant floor suppresses that false breach but also mutes regressions at small n. Under
  concurrent load, repeated arms on the same machine varied enough to produce a breach with no
  code change, so a single-sample "median" is not evidence of a regression in either direction.

To publish a repair-path budget, the instrument and its raw evidence must first be committed
under `benchmarks/`, with a gate that reads them back — the same contract R-04 and R-22 use.
Until then, no absolute repair time is claimed here, and nothing in this section is wired into
`make bench` or CI.

## Report size and render budget (R-04)

`interactive.py` inlines one `issue-data` element per issue and nothing from the source table. That architecture is correct but had no guard and no budget, so a future change could inline the whole table without any check turning red. Both were added *after* measurement: the ceilings below are derived from seven probe arms, never chosen first and then justified.

Instrument, run from the repository root with one arm per process so DuckDB memory is released between arms:

```bash
uv run --no-sync python benchmarks/probe_r04_report_size.py \
    --rows 1000000 --cols 6 \
    --out "$HOME/r04-report-size.jsonl" \
    --workdir "$HOME/r04-report-size-work"
```

Committed evidence: `benchmarks/r04_report_size_probe.jsonl` (7 arms at 1 000 / 50 000 / 200 000 / 1 000 000 rows x 6 and 30 columns, seed 42). Environment: DataSentry `0.8.6` / HEAD `7020b11` on a shared dirty tree, macOS 27.2 / arm64, CPython 3.12.14. Raw readings stay in that file so they can be re-read instead of re-remembered; `tests/test_report_size_budget.py` reads it back. To refresh it, regenerate all seven arms into a fresh file and replace the snapshot deliberately — the one-off command above writes to scratch, so an ambient-host reading cannot silently edit published evidence. The snapshot predates the current `render_html` call path; refreshing it also requires re-checking the ceiling below.

Both the instrument and its evidence live under `benchmarks/`, **not** `AUDIT/`: `.gitignore` excludes the whole `AUDIT/` tree, and no file under it is tracked. The first version of this section claimed its evidence was committed from `AUDIT/artifacts/` and the gate read it from there; that would pass on this machine and fail on a clean clone. `tests/test_report_size_budget.py` now asserts the evidence path is not ignored and that this section points at the same path.

What the arms actually showed:

- HTML stayed between 37 917 and 44 043 bytes across a 1000x range of source rows. The largest arm was 1 000 000 x 30 columns; the 1 000-row control arm was *larger* than every 1 000 000-row arm because it produced 11 issues instead of 8. Report size tracks **issue count**, not source row count. A claim of the form "1M rows produce an X-byte report" would be false on this data.
- `issue-data` carried exactly one element per issue in all seven arms. Two arms 1000x apart in row count produced the same payload row count, which is the constant R-04 asks for.
- Render wall-clock varied about 4x across arms with no corresponding change in the report, while scan wall-clock grew past 40x as columns were added. An absolute render promise on this shared host would be noise, so the accepted statement is the **render / scan ratio**. The largest observed ratio was 0.002742.

Budget row (the single source of truth for both ceilings):

| Fixture | HTML bytes ceiling | Render / scan ratio ceiling |
|---|---|---|
| 1 000 000 x 6 | <= 64 KiB | <= 0.01 |

64 KiB is about 1.45x the largest measured report (43.01 KiB) and 0.01 is about 3.6x the largest measured ratio. The padding is deliberate: the machine was shared with other sessions, and the regression policy below says an ambient-host reading is not a defect. The padding is also *bounded*: the test rejects a bytes ceiling above 4x every reading and a ratio ceiling above 10x every reading, so "make it enormous and it can never go red" fails too.

`tests/test_report_size_budget.py` is the gate. It re-reads this row and the probe evidence, asserts the payload row bound on a real rendered report, and carries two injection controls (inflating `issue_rows` 50x and replacing issue shapes with raw-row shapes) plus an oversized-report arm that must fail the bytes ceiling. A copy of this document with a shrunk ceiling must also fail. None of it is wired into `make bench` or CI: like the repair-path section above, this is an on-demand measurement with a re-runnable command, not a wall-clock assertion in the unit suite.

What is deliberately **not** claimed: any absolute render time, any cross-machine promise, or that no raw cell value appears anywhere in the HTML. A sentinel cell injected by this probe did not surface in either the payload or the HTML, which is an observation about that one fixture, not a proof of absence.

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

## Test-suite judgements that are not timing judgements

Hypothesis property tests carry a per-example wall-clock deadline, and the default (200 ms) was
not chosen for this suite. Under concurrent load, single examples have been observed exceeding it
without any product defect: Hypothesis reports such a case as `FlakyFailure` wrapping
`DeadlineExceeded`. A wall-clock deadline is therefore a load detector, not a correctness
judgement.

Methodology for sizing one, when the change is made:

- Measure the test body over repeated warm replays and record min / p50 / p90 / max. Choose a
  deadline that still leaves visible headroom for a genuine pathological slowdown, rather than
  `deadline=None`, which deletes the guard instead of sizing it.
- Keep the probe and the replay logs in the repository next to the gate that reads them. A margin
  report that cannot be re-run is not evidence.
- Do not turn an ambient-host red reading into a defect, or a green reading into proof that
  nothing regressed. Record the load and repeat the run.

No deadline change is claimed for the current `main` tree here, and no absolute test-suite
wall-clock number is published: the same gate has been measured at widely different totals within
one hour on this host. Timing claims belong to `benchmarks/bench_scan.py` and its budgets, not to
unit-test deadlines.
