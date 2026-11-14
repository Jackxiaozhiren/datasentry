#!/usr/bin/env python
"""Detector golden baseline gate: fail CI when a detector change alters the issue set.

Why this exists (AC-11b, CI form): ``rescan`` compares a stored run against the current
detectors, but CI has no historical run database. A committed baseline of *issue signatures*
gives the same property without a database: if a PR touches detectors or scoring and the
issue set on a fixed dataset moves, the gate goes red and the author must say whether the
move is intended.

What it is not: the baseline is not a quality target and not a regression oracle. A green
run only means "this dataset yields the same signatures as when the baseline was written".
Detector bugs that already exist in the baseline stay green, by construction.

Projection = ``(issue_type, table_name, sorted(columns), sorted(detector_ids))`` plus the two
values a reviewer reads: ``severity`` and ``affected_count``. It mirrors
``datasentry_core.rescan.signature`` on purpose; ``tests/test_detector_baseline.py`` asserts
the two agree whenever that module is importable, so the mirror cannot silently drift.
This file does **not** import ``rescan``: the gate must run on branches where the rescan
tooling has not landed yet.

Issue ids are excluded (regenerated on every scan), as are evidence payloads and timestamps.

Exit codes: 0 match, 1 drift, 2 usage/config error (missing baseline, corrupt JSON, or a
dataset whose sha256 no longer matches the baseline).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from datasentry import DataSentry
from datasentry_core.models.issue import Issue

SCHEMA_VERSION = 1

Key = tuple[str, str, tuple[str, ...], tuple[str, ...]]
Record = dict[str, object]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def project(issue: Issue) -> Record:
    """Issue -> comparable projection. Keep in step with ``rescan.signature``."""
    severity = getattr(issue.severity, "value", issue.severity)
    return {
        "issue_type": issue.issue_type,
        "table_name": issue.table_name,
        "columns": sorted(issue.columns),
        "detectors": sorted(issue.detector_ids),
        "severity": str(severity),
        "affected_count": issue.affected_count,
    }


def key(record: Record) -> Key:
    return (
        str(record["issue_type"]),
        str(record["table_name"] or ""),
        _strings(record, "columns"),
        _strings(record, "detectors"),
    )


def _scan(data: Path) -> list[Record]:
    """Scan ``data`` in a throwaway workspace and return projected issues, sorted by key."""
    with tempfile.TemporaryDirectory(prefix="ds-baseline-") as tmp:
        client = DataSentry(project=Path(tmp) / "ws")
        try:
            _run, _runs, issues = client.scan_file(data)
        finally:
            client.close()
    records = [project(issue) for issue in issues]
    return sorted(records, key=key)


def _payload(data: Path, records: list[Record]) -> dict[str, object]:
    """Build the baseline document. No timestamp: two writes must be byte-identical."""
    return {
        "schema_version": SCHEMA_VERSION,
        "data": data.as_posix(),
        "data_sha256": _sha256(data),
        "issue_count": len(records),
        "issues": records,
    }


def _text(raw: Record, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"baseline field {key!r} must be a string")
    return value


def _optional_text(raw: Record, key: str) -> str | None:
    value = raw.get(key)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"baseline field {key!r} must be a string or null")


def _integer(raw: Record, key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"baseline field {key!r} must be an integer")
    return value


def _strings(raw: Record, key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"baseline field {key!r} must be an array of strings")
    return tuple(str(item) for item in value)


def _validate(raw: Record) -> None:
    _text(raw, "issue_type")
    _optional_text(raw, "table_name")
    _strings(raw, "columns")
    _strings(raw, "detectors")
    _text(raw, "severity")
    _integer(raw, "affected_count")


def _index(records: list[object]) -> dict[Key, tuple[str, int]]:
    """Key -> (severity, affected_count); duplicate keys are a baseline defect, not a merge."""
    index: dict[Key, tuple[str, int]] = {}
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("baseline issues must be JSON objects")
        record = {str(key): value for key, value in item.items()}
        _validate(record)
        signature_key = key(record)
        if signature_key in index:
            raise ValueError(f"duplicate signature in baseline: {signature_key!r}")
        index[signature_key] = (_text(record, "severity"), _integer(record, "affected_count"))
    return index


def _render(key: Key) -> str:
    issue_type, table, columns, detectors = key
    column_text = ",".join(columns) or "-"
    detector_text = ",".join(detectors) or "-"
    return f"{issue_type}[{table or '-'}:{column_text}] detectors={detector_text}"


def _diff(baseline: dict[Key, tuple[str, int]], current: dict[Key, tuple[str, int]]) -> list[str]:
    lines: list[str] = []
    for key in sorted(current.keys() - baseline.keys()):
        severity, affected = current[key]
        lines.append(f"+ added: {_render(key)} severity={severity} affected={affected}")
    for key in sorted(baseline.keys() - current.keys()):
        severity, affected = baseline[key]
        lines.append(f"- removed: {_render(key)} severity={severity} affected={affected}")
    for key in sorted(baseline.keys() & current.keys()):
        old_severity, old_affected = baseline[key]
        new_severity, new_affected = current[key]
        fields: list[str] = []
        if old_severity != new_severity:
            fields.append(f"severity {old_severity}->{new_severity}")
        if old_affected != new_affected:
            fields.append(f"affected {old_affected}->{new_affected}")
        if fields:
            lines.append(f"~ changed: {_render(key)} {'; '.join(fields)}")
    return lines


def _load(path: Path) -> Record:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"baseline not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"baseline is not valid JSON: {path}: {exc}") from None
    if not isinstance(raw, dict):
        raise SystemExit(f"baseline must be a JSON object: {path}")
    record = {str(key): value for key, value in raw.items()}
    version = record.get("schema_version")
    if version != SCHEMA_VERSION:
        raise SystemExit(
            f"unsupported baseline schema_version {version!r} (expected {SCHEMA_VERSION})"
        )
    return record


def _cmd_write(args: argparse.Namespace) -> int:
    data = Path(str(args.data))
    if not data.is_file():
        raise SystemExit(f"data file not found: {data}")
    records = _scan(data)
    target = Path(str(args.baseline))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_payload(data, records), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(records)} signature(s) to {target}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    data = Path(str(args.data))
    if not data.is_file():
        raise SystemExit(f"data file not found: {data}")
    baseline = Path(str(args.baseline))
    raw = _load(baseline)
    raw_issues = raw.get("issues")
    if not isinstance(raw_issues, list):
        raise SystemExit("baseline field 'issues' must be an array")
    try:
        baseline_index = _index(raw_issues)
    except ValueError as exc:
        print(f"baseline is malformed: {exc}", file=sys.stderr)
        return 2

    expected_sha = str(raw.get("data_sha256", ""))
    actual_sha = _sha256(data)
    if expected_sha and expected_sha != actual_sha:
        print(
            "dataset changed since the baseline was written "
            f"({expected_sha[:12]} -> {actual_sha[:12]}); update the baseline deliberately",
            file=sys.stderr,
        )
        return 2

    current_index = {
        key(record): (str(record["severity"]), int(str(record["affected_count"])))
        for record in _scan(data)
    }
    drift = _diff(baseline_index, current_index)
    if drift:
        print(
            f"detector drift: {len(baseline_index)} baseline issue(s) -> "
            f"{len(current_index)} current issue(s)"
        )
        for line in drift:
            print(line)
        print(
            "\nIf the change is intended, regenerate the baseline and explain it in the PR:\n"
            f"  python scripts/detector_baseline.py write --data {data} "
            f"--baseline {baseline}"
        )
        return 1
    print(f"ok: {len(current_index)} issue signature(s) match {baseline}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    commands = (
        ("write", _cmd_write, "regenerate the baseline from the current detectors"),
        ("check", _cmd_check, "compare the current issue set against the baseline"),
    )
    for name, handler, help_text in commands:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--data", required=True, help="dataset to scan (CSV/Parquet/JSONL)")
        command.add_argument("--baseline", required=True, help="baseline JSON path")
        command.set_defaults(func=handler)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
