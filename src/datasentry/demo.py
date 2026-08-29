"""Self-contained DataSentry demo used by the CLI and repository examples.

The demo is intentionally offline and deterministic: it generates synthetic dirty
CSV data, scans it, exports reports, applies one safe repair to a copy, and verifies
the repaired copy. No LLM or external service is required.
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from datasentry.client import DataSentry
from datasentry_core.reporting.html import render_html

CATEGORIES = [f"cat_{i:02d}" for i in range(50)]
STATUSES = ["active", "pending", "inactive"]
MISSING_TOKENS = ["n/a", "N/A", "-", "unknown", "null"]


def generate_demo_csv(path: Path, rows: int, seed: int = 42) -> None:
    """Generate deterministic synthetic data with several common quality problems."""
    if rows < 1:
        raise ValueError("rows must be >= 1")

    rng = random.Random(seed)
    today = date.today()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("id,price,category,event_date,name,status\n")
        for i in range(rows):
            price = (
                rng.choice([10, 25, 50, 100, 250, 500, 9999])
                if rng.random() < 0.03
                else rng.randint(10, 500)
            )
            category = CATEGORIES[rng.randrange(len(CATEGORIES))]
            if rng.random() < 0.04:
                category = f"  {category} "
            if rng.random() < 0.05:
                event_date = rng.choice(["2024-02-30", "2024-13-01", "not-a-date"])
            else:
                event_date = (today - timedelta(days=rng.randint(0, 700))).isoformat()
            name = f"user_{i}"
            if rng.random() < 0.04:
                name = f" {name} "
            if rng.random() < 0.06:
                name = name.upper()
            status = rng.choice(STATUSES)
            if rng.random() < 0.05:
                status = rng.choice(MISSING_TOKENS)
            fh.write(f"{i},{price},{category},{event_date},{name},{status}\n")


def run_demo(
    *,
    rows: int = 1000,
    out: Path | None = None,
    project: str | Path | None = None,
    seed: int = 42,
) -> int:
    """Run the complete local demo and print a concise, user-facing summary."""
    out_dir = out.expanduser() if out is not None else Path(tempfile.mkdtemp(prefix="datasentry-demo-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace = Path(project).expanduser() if project is not None else out_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    data_csv = out_dir / "customers_dirty.csv"

    print("DataSentry demo — find → explain → safely fix → verify")
    print("No cloud services or LLM required.\n")

    started = time.monotonic()
    generate_demo_csv(data_csv, rows, seed=seed)
    print(f"1/4 Generated {rows:,} synthetic rows with intentional data problems")

    client = DataSentry(project=workspace)
    try:
        scan, runs, issues = client.scan_file(data_csv)
        before_quality = scan.quality_score.overall if scan.quality_score else None
        completed = sum(1 for run in runs if run.status == "completed")
        quality_text = f"{before_quality:.1f}/100" if before_quality is not None else "n/a"
        print(
            f"2/4 Scan complete: {len(issues)} issues · quality {quality_text} · "
            f"{completed}/{len(runs)} detectors completed"
        )
        for issue in sorted(issues, key=lambda item: item.priority_score, reverse=True)[:5]:
            print(
                f"    [{issue.severity.value.upper():8}] {issue.title} "
                f"({issue.affected_count} affected)"
            )

        report = client.export_report(scan.id)
        json_path = out_dir / "report.json"
        html_path = out_dir / "report.html"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(render_html(report), encoding="utf-8")
        print(f"3/4 Reports written: {json_path.name}, {html_path.name}")

        repair_run_id: str | None = None
        rollback_artifact: str | None = None
        for issue in sorted(issues, key=lambda item: item.priority_score, reverse=True):
            proposal = client.repair_propose(issue.id, data_csv)
            if proposal is None:
                continue
            preview = client.repair_preview(issue.id, data_csv)
            repair_run = client.repair_apply(issue.id, data_csv)
            verify_scan, verify = client.repair_verify(repair_run.id)
            after_quality = verify_scan.quality_score.overall if verify_scan.quality_score else None
            repair_run_id = repair_run.id
            rollback_artifact = repair_run.rollback_artifact

            before_text = f"{before_quality:.1f}" if before_quality is not None else "n/a"
            after_text = f"{after_quality:.1f}" if after_quality is not None else "n/a"
            preview_text = ""
            if preview is not None:
                preview_text = (
                    f" · preview failures {preview[1].rule_failures_before}"
                    f"→{preview[1].rule_failures_after}"
                )
            print(
                f"4/4 Repaired a copy: {issue.title} → {proposal.operation.value}{preview_text}"
            )
            print(
                f"    Verified quality: {before_text} → {after_text}; "
                f"new issue types: {len(verify['new_types'])}"
            )
            break

        if repair_run_id is None:
            print("4/4 No automatically repairable issue was found; scan and reports are still complete")

        elapsed = time.monotonic() - started
        print(f"\nDone in {elapsed:.1f}s")
        print(f"Artifacts: {out_dir}")
        print(f"HTML report: {html_path}")
        if repair_run_id is not None:
            if rollback_artifact:
                print(f"Rollback artifact: {rollback_artifact}")
            print(
                "Rollback command: "
                f"datasentry --project {workspace} repair rollback {repair_run_id}"
            )
        return 0
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    """Console entry point for a zero-config product demo."""
    parser = argparse.ArgumentParser(
        prog="datasentry-demo",
        description="Run DataSentry's offline find → fix → verify demo.",
    )
    parser.add_argument("--rows", type=int, default=1000, help="synthetic rows to generate")
    parser.add_argument("--out", type=Path, default=None, help="artifact output directory")
    parser.add_argument("--project", type=Path, default=None, help="DataSentry workspace")
    parser.add_argument("--seed", type=int, default=42, help="reproducibility seed")
    args = parser.parse_args(argv)
    return run_demo(rows=args.rows, out=args.out, project=args.project, seed=args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
