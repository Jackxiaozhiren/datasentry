"""Step 58 分层指纹基准（ADR-058）：百万行远程库统计层 vs 内容层耗时对比。

用法：uv run python benchmarks/bench_layered_fingerprint.py [rows]
前置：docker compose up -d（PG 服务，55432 端口），或 TEST_POSTGRES_DSN 指定 DSN。
输出：markdown 表格 + 判定（PASS/FAIL，统计层 < 内容层 + 宽容界）。

验证目标：统计层（DESCRIBE + count）比内容层（全表哈希）快一个量级，
变更感知 tick 在数据未变时的开销降到最小。
"""

from __future__ import annotations

import os
import sys
import time

from datasentry_core.connectors import (
    DataSourceSpec,
    DataSourceType,
    default_registry,
)

_DEFAULT_DSN = "postgresql://testuser:testpass@localhost:55432/testdb"
_TABLE = "bench_layered"


def _main() -> int:
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    dsn = os.environ.get("TEST_POSTGRES_DSN") or _DEFAULT_DSN
    spec = DataSourceSpec(
        source_type=DataSourceType.POSTGRESQL,
        table_name=_TABLE,
        options={"dsn": dsn},
    )
    import duckdb

    con = duckdb.connect(database=":memory:")
    try:
        con.execute("LOAD postgres")
        con.execute(f"ATTACH '{dsn}' AS w (TYPE postgres)")
        con.execute(f"DROP TABLE IF EXISTS w.public.{_TABLE}")
        con.execute(f"CREATE TABLE w.public.{_TABLE} (id INTEGER, name VARCHAR, value DOUBLE)")
        con.execute(
            f"INSERT INTO w.public.{_TABLE} "
            "SELECT i, 'name_' || i::VARCHAR, i * 1.5 "
            f"FROM generate_series(1, {rows}) AS t(i)"
        )
        print(f"seeded {rows:,} rows")
    finally:
        con.close()

    handle = default_registry().open(spec)
    try:
        # 预热（首次连接/元数据缓存）
        handle.stats_fingerprint()
        handle.content_fingerprint()

        t0 = time.perf_counter()
        stats = handle.stats_fingerprint()
        stats_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        content = handle.content_fingerprint()
        content_time = time.perf_counter() - t0
    finally:
        handle.close()

    ratio = content_time / stats_time if stats_time > 0 else float("inf")
    # 宽容界：统计层必须显著快于内容层（预期快一个量级；判据取 3× 宽容下限）
    passed = ratio >= 3.0
    print("\n| 层 | 耗时(s) | 指纹 |")
    print("|----|---------|------|")
    print(f"| 统计层（DESCRIBE+count） | {stats_time:.3f} | {stats[:16]}… |")
    print(f"| 内容层（全表哈希） | {content_time:.3f} | {content[:16]}… |")
    print(f"| 加速比 | {ratio:.1f}× | |")
    print(f"\n判定：{'PASS' if passed else 'FAIL'}（统计层 ≥3× 快于内容层）")

    con = duckdb.connect(database=":memory:")
    try:
        con.execute("LOAD postgres")
        con.execute(f"ATTACH '{dsn}' AS w (TYPE postgres)")
        con.execute(f"DROP TABLE IF EXISTS w.public.{_TABLE}")
    finally:
        con.close()
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(_main())
