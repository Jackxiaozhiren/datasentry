"""Step 34 回归：evidence.data 必须 JSON 可序列化（date 列 → DuckDB DATE 类型）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from datasentry import DataSentry
from datasentry_core.storage import project_db_path


def _date_csv(tmp_path: Path, rows: int = 300) -> Path:
    p = tmp_path / "events.csv"
    lines = ["event_id,event_date,category"]
    for i in range(rows):
        d = f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        lines.append(f"e{i},{d},cat{i % 3}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_evidence_json_safe_with_date_columns(tmp_path: Path) -> None:
    csv = _date_csv(tmp_path)
    client = DataSentry(project=tmp_path)
    try:
        scan, _, issues = client.scan_file(csv)
    finally:
        client.close()
    types = {i.issue_type for i in issues}
    assert "uniqueness" in types  # 重复日期列必须被检测
    assert scan is not None
    with sqlite3.connect(project_db_path(tmp_path)) as conn:
        rows = conn.execute("SELECT data FROM evidence").fetchall()
    assert rows, "evidence rows must be persisted"
    for (data,) in rows:
        parsed = json.loads(data)  # date/datetime 泄漏时这里抛 TypeError
        assert isinstance(parsed, dict)


def test_date_evidence_values_are_strings(tmp_path: Path) -> None:
    csv = _date_csv(tmp_path)
    client = DataSentry(project=tmp_path)
    try:
        client.scan_file(csv)
    finally:
        client.close()
    with sqlite3.connect(project_db_path(tmp_path)) as conn:
        rows = conn.execute("SELECT data FROM evidence").fetchall()
    for (data,) in rows:
        parsed = json.loads(data)
        for values in parsed.values():
            if isinstance(values, list) and values and isinstance(values[0], tuple):
                for pair in values:
                    for item in pair:
                        assert not hasattr(item, "year"), f"raw date leaked: {item!r}"
