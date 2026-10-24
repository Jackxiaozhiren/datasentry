"""DataSentry from Python, end to end, in twenty lines.

Run it from this directory:

    python quickstart.py

It writes its own synthetic CSV, scans it, and prints the score and the issues.
No credentials, no network, no LLM.
"""

from datasentry import DataSentry

client = DataSentry(project=".")
try:
    scan, _detector_runs, issues = client.scan_file("orders.csv")

    score = client.quality_score(scan.id)
    print(f"quality score: {score.overall} / 100" if score else "no score")
    print(f"{scan.fingerprint.row_count} rows, {len(issues)} issues\n")

    for issue in sorted(issues, key=lambda i: -i.priority_score)[:5]:
        print(f"  [{issue.severity.value:<8}] {issue.title}")
        print(f"             {issue.affected_count} affected · {', '.join(issue.columns)}")
finally:
    client.close()
