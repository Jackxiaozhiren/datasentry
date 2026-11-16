"""The single JSON spelling of a repair diff, shared by CLI, REST and MCP.

`client.repair_diff()` returns raw rows as `(columns, before_rows, after_rows,
changed)`. Each face used to build its own JSON object from those rows, so a
one-sided change to the shape stayed invisible to the other two. R-09 pins
all three JSON faces to this one builder.

A key the record never carried is omitted rather than written as null: the
JSON object's own lack of the key is the honest spelling of "not present".
On this baseline `RepairEngine.table_diff` pads short rows with `None`; the
`ABSENT` sentinel that distinguishes "missing" from "null" belongs to a
later slice, so this module applies the same omission rule by position and
does not import a symbol that is not on main.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datasentry_core.models.repair import RepairRun

__all__ = ["build_repair_diff_payload"]


def _cell_map(columns: list[str], row: list[object]) -> dict[str, object]:
    """Map a diff row by position, omitting keys the row does not carry."""
    return {name: row[i] for i, name in enumerate(columns) if i < len(row)}


def build_repair_diff_payload(
    run: RepairRun,
    columns: list[str],
    before_rows: list[list[object]],
    after_rows: list[list[object]],
    changed: list[int],
) -> dict[str, Any]:
    """The one shape used by `repair diff --format json`, REST and MCP."""
    rows: list[dict[str, Any]] = []
    for i in changed:
        before = before_rows[i] if i < len(before_rows) else []
        after = after_rows[i] if i < len(after_rows) else []
        rows.append(
            {
                "line": i + 2,
                "before": _cell_map(columns, before),
                "after": _cell_map(columns, after),
            }
        )
    return {
        "run_id": run.id,
        "status": run.status.value,
        "columns": columns,
        "changed_rows": rows,
    }
