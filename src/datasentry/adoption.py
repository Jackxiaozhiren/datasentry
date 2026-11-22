"""Local adoption / confirmation log (PRD R-14, R-16; D-1 decision: opt-in).

The log is a local append-only JSONL file at ``<workspace>/.datasentry/adoption.jsonl``.
It is **off by default**: set ``DATASENTRY_ADOPTION_LOG=1`` to enable it. See
``AUDIT/FIX_PRD.md`` B0 for the delegated decision and its rationale.

Invariant 5 (no secret/DSN/PII in logs) is enforced structurally, not by a regex
blacklist: every field is either a fixed enum, a constrained identifier, an issue id
of a fixed shape, or a non-negative integer. No free text, no paths, no column values
and no source attribute ever reaches the file. This is why a DSN passed as
``issue_type`` becomes an empty string instead of being stored and then masked.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENV_ADOPTION_LOG = "DATASENTRY_ADOPTION_LOG"
LOG_RELPATH = Path(".datasentry") / "adoption.jsonl"

_ACTIONS = frozenset({"propose", "approve", "reject", "rollback", "confirm"})
_SURFACES = frozenset({"cli", "ui", "rest", "mcp", "tui"})
_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
_VERDICTS = frozenset({"true", "false"})
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,63}\Z")
_ISSUE_ID = re.compile(r"iss_[0-9a-f]{8,16}\Z")

_SCOPE = (
    "denominator = proposals (propose actions); adoption_rate = approvals / proposals; "
    "false_positive_rate = confirmed-false / confirmed; unconfirmed issues are excluded "
    "from every confirmation denominator; this is a local opt-in log, not a user sample."
)


def log_path(workspace: Path) -> Path:
    """Where the append-only decision log lives for a workspace."""
    return workspace / LOG_RELPATH


def enabled() -> bool:
    """D-1: opt-in. Only an explicit ``1`` turns the local persistence on."""
    return os.environ.get(ENV_ADOPTION_LOG) == "1"


def _token(value: Any, allowed: frozenset[str]) -> str:
    return value if isinstance(value, str) and value in allowed else ""


def _identifier(value: Any, pattern: re.Pattern[str]) -> str:
    return value if isinstance(value, str) and pattern.fullmatch(value) else ""


def _rows(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def build_record(
    *,
    action: Any,
    issue_type: Any,
    rows_changed: Any,
    surface: Any,
    issue_id: Any = "",
    severity: Any = "",
    verdict: Any = "",
) -> dict[str, Any]:
    """Assemble exactly the whitelisted fields. Never accepts unbounded text."""
    return {
        "ts": datetime.now(UTC).isoformat(),
        "action": _token(action, _ACTIONS),
        "issue_type": _identifier(issue_type, _IDENTIFIER),
        "rows_changed": _rows(rows_changed),
        "surface": _token(surface, _SURFACES),
        "issue_id": _identifier(issue_id, _ISSUE_ID),
        "severity": _token(severity, _SEVERITIES),
        "verdict": _token(verdict, _VERDICTS),
    }


def record(workspace: Path, **fields: Any) -> dict[str, Any] | None:
    """Append one decision line. Returns ``None`` when the log is not enabled.

    ``fields`` are the keyword arguments of :func:`build_record`. A malformed or
    non-whitelisted value is scrubbed to a safe empty token, never written verbatim.
    """
    if not enabled():
        return None
    entry = build_record(**fields)
    path = log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def read_records(workspace: Path) -> list[dict[str, Any]]:
    path = log_path(workspace)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def summarize(workspace: Path) -> dict[str, Any]:
    """Pure-local aggregation with an explicit denominator and scope statement."""
    records = read_records(workspace)
    actions = [r.get("action") for r in records]
    proposals = actions.count("propose")
    approvals = actions.count("approve")
    confirms = [r for r in records if r.get("action") == "confirm"]
    confirmed_false = sum(r.get("verdict") == "false" for r in confirms)
    confirmed_true = sum(r.get("verdict") == "true" for r in confirms)
    confirmed = confirmed_true + confirmed_false
    return {
        "proposals": proposals,
        "approvals": approvals,
        "rejections": actions.count("reject"),
        "rollbacks": actions.count("rollback"),
        "confirmed": confirmed,
        "confirmed_true": confirmed_true,
        "confirmed_false": confirmed_false,
        "adoption_rate": approvals / proposals if proposals else 0.0,
        "false_positive_rate": confirmed_false / confirmed if confirmed else 0.0,
        "scope": _SCOPE,
        "log_enabled": enabled(),
    }
