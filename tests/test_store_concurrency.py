"""V19（Step 105，ADR-105）：MetadataStore 跨进程并发写测试。

用 subprocess + sys.executable 起真实子进程（不用 multiprocessing——
pytest 下 spawn 会递归 re-import 测试模块），证明：
- busy_timeout 生效：并发写不抛 "database is locked"
- 并发写不丢行：最终计数 = 各进程写入之和
- 并发写 + 并发删交叉安全
- pii 会话列表同秒稳定顺序（created_at DESC, rowid DESC 二级排序）
- WAL 模式保持（migrate 设置）
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from datasentry_core.storage.store import MetadataStore

_PII_WRITER = r"""
import sys
from pathlib import Path

from datasentry_core.storage.store import MetadataStore

store = MetadataStore(Path(sys.argv[1]))
for i in range(int(sys.argv[2])):
    store.save_pii_mapping(f"w_{sys.argv[3]}_{i}", "ct", key_version="env")
store.close()
"""

_DELETER = r"""
import sys
from pathlib import Path

from datasentry_core.storage.store import MetadataStore

store = MetadataStore(Path(sys.argv[1]))
for session_id in sys.argv[3].split(","):
    store.delete_pii_mapping(session_id)
store.close()
"""

_SCAN_WRITER = r"""
import sys
from pathlib import Path

from datasentry_core.models.enums import Severity
from datasentry_core.models.fingerprint import DatasetFingerprint
from datasentry_core.models.scan import ReproducibilityInfo, ScanConfig, ScanRun
from datasentry_core.storage.store import MetadataStore

prefix = sys.argv[3]
store = MetadataStore(Path(sys.argv[1]))
store.save_scan(
    ScanRun(
        id=f"scan_{prefix}",
        dataset_id=f"ds_{prefix}",
        status="completed",
        config=ScanConfig(detectors=["iqr_outlier"]),
        fingerprint=DatasetFingerprint(
            dataset_id=f"ds_{prefix}",
            fingerprint_type="full",
            file_sha256="abc",
            schema_hash="sch",
            row_count=1,
            column_count=1,
            column_signature=[("v", "DOUBLE")],
        ),
        issues_count={Severity.INFO: 0},
        reproducibility=ReproducibilityInfo(
            datasentry_version="0.1.0", detector_versions={}, seed=1
        ),
    ),
    [],
    [],
)
store.close()
"""

_PRAGMA = r"""
import sys
from pathlib import Path

from datasentry_core.storage.store import MetadataStore

store = MetadataStore(Path(sys.argv[1]))
print(store._conn.execute("PRAGMA busy_timeout").fetchone()[0])
store.close()
"""


def _run(code: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _spawn(code: str, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", code, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_concurrent_writers_no_busy_no_loss(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    a = _spawn(_PII_WRITER, str(db), "40", "a")
    b = _spawn(_PII_WRITER, str(db), "40", "b")
    out_a, err_a = a.communicate(timeout=120)
    out_b, err_b = b.communicate(timeout=120)
    assert a.returncode == 0, out_a + err_a
    assert b.returncode == 0, out_b + err_b
    assert "locked" not in (err_a + err_b).lower()
    store = MetadataStore(db)
    try:
        assert store.count_pii_mappings() == 80
    finally:
        store.close()


def test_concurrent_scan_writers_no_busy(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    a = _spawn(_SCAN_WRITER, str(db), "0", "alpha")
    b = _spawn(_SCAN_WRITER, str(db), "0", "beta")
    out_a, err_a = a.communicate(timeout=120)
    out_b, err_b = b.communicate(timeout=120)
    assert a.returncode == 0, out_a + err_a
    assert b.returncode == 0, out_b + err_b
    assert "locked" not in (err_a + err_b).lower()
    store = MetadataStore(db)
    try:
        assert len(store.list_scan_runs()) == 2
    finally:
        store.close()


def test_busy_timeout_configured(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    result = _run(_PRAGMA, str(db))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "5000"


def test_wal_mode_enabled(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "meta.db"
    MetadataStore(db).close()
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_concurrent_delete_and_write(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    store = MetadataStore(db)
    try:
        for i in range(60):
            store.save_pii_mapping(f"seed_{i}", "ct", key_version="env")
    finally:
        store.close()
    victims = ",".join(f"seed_{i}" for i in range(20))
    deleter = _spawn(_DELETER, str(db), "0", victims)
    writer = _spawn(_PII_WRITER, str(db), "30", "c")
    _, err_d = deleter.communicate(timeout=120)
    _, err_w = writer.communicate(timeout=120)
    assert deleter.returncode == 0, err_d
    assert writer.returncode == 0, err_w
    assert "locked" not in (err_d + err_w).lower()
    store = MetadataStore(db)
    try:
        assert store.count_pii_mappings() == 60 - 20 + 30
    finally:
        store.close()


def test_list_pii_mappings_same_second_stable_order(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    store = MetadataStore(db)
    try:
        same = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(3):
            store.save_pii_mapping(f"same_{i}", "ct", key_version="env", created_at=same)
        listed = [r["session_id"] for r in store.list_pii_mappings()]
        assert listed == ["same_2", "same_1", "same_0"]
    finally:
        store.close()
