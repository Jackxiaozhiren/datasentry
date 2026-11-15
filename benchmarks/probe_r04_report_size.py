"""PRD R-04 feasibility probe: measure real HTML report size / render cost.

Run from the repository root, one arm per process so DuckDB memory is released
between arms::

    uv run --no-sync python benchmarks/probe_r04_report_size.py \
        --rows 1000000 --cols 6 \
        --out "$HOME/r04-report-size.jsonl" \
        --workdir "$HOME/r04-report-size-work"

This instrument only reports measurements. It deliberately declares no ceiling:
`docs/BENCHMARKS.md` must be written *after* reading this evidence (PRD R-04
requires probe first, target second). A deliberate snapshot of all seven arms is
committed as `benchmarks/r04_report_size_probe.jsonl`, which the test gate reads
back; re-measurements go to a scratch file so ambient-host numbers do not
silently edit the published evidence.

The committed snapshot predates the current `render_html` call path. This probe
renders through the public `datasentry_core.reporting.html.render_html` entry
point with the same trends / profiles / comparison inputs the CLI export uses.
Refreshing the snapshot therefore also requires re-checking the ceiling in
`docs/BENCHMARKS.md`.
"""

from __future__ import annotations

import argparse
import gc
import json
import random
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from datasentry import DataSentry
from datasentry.trends import build_comparison, build_trends
from datasentry_core.reporting.html import render_html

_BASE_COLS = ("id", "price", "category", "event_date", "name", "status")
_CATEGORIES = ("A", "B", "C", "D", "E")
_STATUSES = ("active", "pending", "closed")
_ISSUE_DATA_RE = re.compile(r'<script type="application/json" id="issue-data">(.*?)</script>', re.S)
_SENTINEL = "R04-RAW-CELL-DO-NOT-SHIP"


def _columns(cols: int) -> list[str]:
    return [*_BASE_COLS, *(f"extra_{i}" for i in range(len(_BASE_COLS), cols))]


def _write_csv(path: Path, n: int, cols: int, rng: random.Random) -> int:
    epoch = date(2023, 1, 1)
    span = (date(2024, 12, 31) - epoch).days
    with path.open("w", encoding="utf-8") as fh:  # NOSONAR S8707: local CLI path
        fh.write(",".join(_columns(cols)) + "\n")
        for i in range(n):
            price = max(0.0, round(rng.gauss(100.0, 20.0), 2))
            category = rng.choice(_CATEGORIES)
            status = rng.choice(_STATUSES)
            event_date = (epoch + timedelta(days=rng.randrange(span + 1))).isoformat()
            if rng.random() < 0.001:
                price = round(price + rng.choice([-1, 1]) * rng.gauss(0, 200.0), 2)
            if rng.random() < 0.0005:
                event_date = "2024-02-30"
            name = _SENTINEL if i == 0 else f"user_{i}"
            if rng.random() < 0.001:
                name = f" {name} "
            if rng.random() < 0.001:
                status = "n/a"
            row = [i, price, category, event_date, name, status]
            row.extend(f"v{j % 7}" for j in range(len(_BASE_COLS), cols))
            fh.write(",".join(str(v) for v in row) + "\n")
    return path.stat().st_size


def _payload_rows(html: str) -> tuple[int, int]:
    match = _ISSUE_DATA_RE.search(html)
    if match is None:
        return -1, -1
    raw = match.group(1)
    return len(json.loads(raw)["issues"]), len(raw.encode("utf-8"))


def _measure(rows: int, cols: int, seed: int, workdir: Path) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)  # NOSONAR S8707: local CLI path
    stem = f"r04-{rows}-{cols}-{seed}"
    csv_path = workdir / f"{stem}.csv"
    ws = workdir / f"ws-{stem}"
    t0 = time.perf_counter()
    csv_bytes = _write_csv(csv_path, rows, cols, random.Random(seed))
    gen_secs = time.perf_counter() - t0

    client = DataSentry(project=ws)
    try:
        t0 = time.perf_counter()
        scan, _, issues = client.scan_file(csv_path)
        scan_secs = time.perf_counter() - t0
        report = client.export_report(scan.id)
        t0 = time.perf_counter()
        trends = [t.to_report_dict() for t in build_trends(client.list_scan_runs())]
        profiles = client.load_profile(scan.id)
        dataset_id = str(report["scan"]["dataset_id"])
        comparison = build_comparison(client.list_scan_runs(), dataset_id, scan.id)
        html = render_html(
            report,
            trends=trends or None,
            profiles=profiles,
            comparison=comparison,
            lang="en",
        )
        render_secs = time.perf_counter() - t0
    finally:
        client.close()

    html_bytes = len(html.encode("utf-8"))
    payload_rows, payload_bytes = _payload_rows(html)
    result = {
        "rows": rows,
        "cols": cols,
        "seed": seed,
        "csv_bytes": csv_bytes,
        "gen_secs": round(gen_secs, 4),
        "scan_secs": round(scan_secs, 4),
        "render_secs": round(render_secs, 4),
        "html_bytes": html_bytes,
        "issue_count": len(report["issues"]),
        "issue_objects_scanned": len(issues),
        "payload_rows": payload_rows,
        "payload_bytes": payload_bytes,
        "sentinel_in_payload": _SENTINEL in (_ISSUE_DATA_RE.search(html).group(1) or ""),
        "sentinel_in_html": _SENTINEL in html,
        "row_count_in_report": report["scan"]["fingerprint"]["row_count"],
    }
    csv_path.unlink(missing_ok=True)  # NOSONAR S8707: local CLI path
    gc.collect()
    return result


# pythonsecurity:S8707 models CLI arguments as attacker-controlled HTTP input. This
# probe is a local measurement tool: --out and --workdir are typed by the operator,
# so that taint source does not exist. Paths are normalised before use, and every
# filesystem sink above and below carries an inline suppression marker recording
# that decision.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--cols", type=int, default=6)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--workdir", type=Path, default=Path.home() / "ds-probe-2026-10-15/r04-work"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    workdir = args.workdir.expanduser().resolve()
    out = args.out.expanduser().resolve()
    reading = _measure(args.rows, args.cols, args.seed, workdir)
    out.parent.mkdir(parents=True, exist_ok=True)  # NOSONAR S8707: local CLI path
    with out.open("a", encoding="utf-8") as fh:  # NOSONAR S8707: local CLI path
        fh.write(json.dumps(reading, sort_keys=True) + "\n")
    print(json.dumps(reading, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
