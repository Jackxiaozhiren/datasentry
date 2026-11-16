"""Minimal MCP Apps surface for `report_export` (PRD R-10 / R-11 / R-13).

No new dependency. The "app" is a ``text/html`` resource embedded in the tool
result, which is the smallest thing a host that declares support can render.

The hard constraint is the negotiation rule: a client that does **not** declare
``mcp-apps`` must receive exactly the bytes it receives today. That is enforced
by ``tests/test_mcp_apps.py`` against a snapshot committed *before* this module
existed, so any added key, block or ``_meta`` turns the gate red.

The card is a projection: every number it shows is read from the report payload,
and ``tests/test_mcp_apps.py`` asserts the body contains no numeric token that is
not in that payload (R-13: no second domain logic, no invented figures).
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping
from typing import Any

#: Capability key a host uses to declare MCP Apps support.
APPS_KEY = "mcp-apps"
#: Where the card lives. Stable so a host can cache it across calls.
REPORT_CARD_URI = "ui://datasentry/report.html"
REPORT_CARD_MIME = "text/html"

#: Order matters for stable rendering; only severities present in the payload are shown.
SEVERITIES = ("critical", "high", "medium", "low", "info")

#: CSS carries the only numeric literals allowed outside the payload. The R-13 gate
#: strips the <style> block before comparing body numbers with payload numbers.
_CARD_CSS = """
body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
color:#1f2328;background:#ffffff}
.card{padding:16px 18px;max-width:520px;box-sizing:border-box;width:100%}
h1{margin:0 0 2px;font-size:16px;font-weight:600}
.meta{margin:0 0 12px;font-size:12px;color:#59636e}
.score{display:flex;align-items:baseline;gap:8px;margin:0 0 12px}
.score .label{font-size:12px;color:#59636e}
.score .value{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums}
ul.counts{list-style:none;margin:0;padding:0;display:grid;gap:4px}
ul.counts li{display:flex;justify-content:space-between;font-variant-numeric:tabular-nums}
.sev{color:#59636e}
.note{margin:14px 0 0;font-size:12px;color:#59636e}
"""


def client_declares_apps(capabilities: object) -> bool:
    """True only when the client explicitly declares MCP Apps support.

    Both spellings in the wild are accepted (``extensions`` is the current
    convention, ``experimental`` the older one). Anything else, including a
    malformed value, means "not declared" — the legacy path, byte for byte.
    """
    if not isinstance(capabilities, dict):
        return False
    for block in ("extensions", "experimental"):
        declared = capabilities.get(block)
        if isinstance(declared, dict) and declared.get(APPS_KEY) is not None:
            return True
    return False


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def card_numbers(report: Mapping[str, Any]) -> list[str]:
    """The numbers the card may display, formatted exactly as JSON writes them.

    Shared with :func:`report_card_html` so the gate and the renderer cannot
    drift: a number that is not in this list cannot appear in the body.
    """
    numbers: list[str] = []
    quality = report.get("quality")
    if isinstance(quality, dict) and _is_number(quality.get("overall")):
        numbers.append(json.dumps(quality["overall"]))
    scan = report.get("scan")
    counts = scan.get("issues_count") if isinstance(scan, dict) else None
    if isinstance(counts, dict):
        for severity in SEVERITIES:
            if _is_number(counts.get(severity)):
                numbers.append(json.dumps(counts[severity]))
    return numbers


def _card_body(report: Mapping[str, Any]) -> str:
    scan = report.get("scan")
    scan = scan if isinstance(scan, dict) else {}
    quality = report.get("quality")
    quality = quality if isinstance(quality, dict) else {}
    dataset = html.escape(str(scan.get("dataset_id") or "dataset"), quote=True)
    run_id = html.escape(str(report.get("scan_run_id") or scan.get("id") or ""), quote=True)
    overall = quality.get("overall")
    score = json.dumps(overall) if _is_number(overall) else "&mdash;"
    rows: list[str] = []
    counts = scan.get("issues_count")
    counts = counts if isinstance(counts, dict) else {}
    for severity in SEVERITIES:
        value = counts.get(severity)
        if _is_number(value):
            rows.append(
                f'<li><span class="sev">{severity}</span>'
                f'<span class="n">{json.dumps(value)}</span></li>'
            )
    listed = f'<ul class="counts">{"".join(rows)}</ul>' if rows else ""
    return (
        '<article class="card">'
        "<h1>DataSentry quality report</h1>"
        f'<p class="meta">{dataset} &middot; {run_id}</p>'
        '<p class="score"><span class="label">Quality score</span>'
        f'<span class="value">{score}</span></p>'
        f"{listed}"
        '<p class="note">Read-only projection of the report_export JSON. '
        "Nothing on this card changes state.</p>"
        "</article>"
    )


def report_card_html(report: Mapping[str, Any]) -> str:
    """A self-contained card. No script, no form, no link, no action."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>DataSentry quality report</title>"
        f"<style>{_CARD_CSS}</style></head><body>{_card_body(report)}</body></html>"
    )


def report_card_resource(report: Mapping[str, Any]) -> dict[str, str]:
    """The ``resource`` block appended for clients that declared support."""
    return {
        "uri": REPORT_CARD_URI,
        "mimeType": REPORT_CARD_MIME,
        "text": report_card_html(report),
    }


def card_css() -> str:
    """Exposed so the R-13 gate can strip exactly what the renderer injected."""
    return _CARD_CSS
