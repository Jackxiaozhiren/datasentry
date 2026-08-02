"""Step 3 执行层测试：sql_guard + DuckDBExecutor。"""

from __future__ import annotations

import duckdb
import pytest

from datasentry_core.connectors.errors import UnsafeSqlError
from datasentry_core.engine import DuckDBExecutor
from datasentry_core.engine.sql_guard import assert_read_only_sql

ALLOWED = [
    "SELECT 1",
    "SELECT * FROM data WHERE id = ?",
    "SELECT * FROM data WHERE id = $1",
    "SELECT count(*) FROM data;",
    "  SELECT 1  ",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "SHOW TABLES",
    "DESCRIBE SELECT * FROM data",
    "EXPLAIN SELECT 1",
    "EXPLAIN ANALYZE SELECT 1",
]

DENIED = [
    "",
    "   ",
    "UPDATE data SET a = 1",
    "DELETE FROM data",
    "INSERT INTO data VALUES (1)",
    "DROP TABLE data",
    "ALTER TABLE data ADD COLUMN x INT",
    "CREATE TABLE x (a INT)",
    "COPY data TO 'out.csv'",
    "ATTACH 'db.duckdb' AS other",
    "PRAGMA version",
    "CALL some_function()",
    "SELECT 1; DROP TABLE data",
    "SELECT 1; SELECT 2",
    "GRANT SELECT ON data TO u",
    "REVOKE SELECT ON data FROM u",
    "TRUNCATE data",
    "-- comment\nDROP TABLE data",
]


class TestSqlGuard:
    @pytest.mark.parametrize("sql", ALLOWED)
    def test_allows_readonly(self, sql: str) -> None:
        assert_read_only_sql(sql)

    @pytest.mark.parametrize("sql", DENIED)
    def test_denies_tampering(self, sql: str) -> None:
        with pytest.raises(UnsafeSqlError):
            assert_read_only_sql(sql)


class TestDuckDBExecutor:
    def _setup(self) -> DuckDBExecutor:
        ex = DuckDBExecutor()
        ex.execute_setup(
            "CREATE TABLE t (id INTEGER, name VARCHAR); "
            "INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, 'c')"
        )
        return ex

    def test_positional_params(self) -> None:
        ex = self._setup()
        try:
            table = ex.execute("SELECT * FROM t WHERE id >= ?", [2])
            assert table.num_rows == 2
        finally:
            ex.close()

    def test_named_params(self) -> None:
        ex = self._setup()
        try:
            table = ex.execute("SELECT * FROM t WHERE id = $1", {"1": 2})
            assert table.num_rows == 1
            assert table.column("name").to_pylist() == ["b"]
        finally:
            ex.close()

    def test_returns_arrow_table(self) -> None:
        ex = self._setup()
        try:
            table = ex.execute("SELECT count(*) AS n FROM t")
            assert table.schema.names == ["n"]
            assert table.column("n").to_pylist() == [3]
        finally:
            ex.close()

    def test_execute_denies_tampering(self) -> None:
        ex = self._setup()
        try:
            with pytest.raises(UnsafeSqlError):
                ex.execute("DELETE FROM t")
        finally:
            ex.close()

    def test_execute_denies_multi_statement(self) -> None:
        ex = self._setup()
        try:
            with pytest.raises(UnsafeSqlError):
                ex.execute("SELECT 1; SELECT 2")
        finally:
            ex.close()

    def test_guard_applied_after_setup(self) -> None:
        """execute_setup 是内部受信路径，execute 永远走守卫。"""
        ex = self._setup()
        try:
            ex.execute_setup("CREATE VIEW v AS SELECT * FROM t")
            assert ex.execute("SELECT count(*) FROM v").column("count_star()").to_pylist() == [3]
        finally:
            ex.close()

    def test_execute_after_close_raises(self) -> None:
        ex = self._setup()
        ex.close()
        with pytest.raises(UnsafeSqlError):
            ex.execute("SELECT 1")
        with pytest.raises(UnsafeSqlError):
            ex.execute_setup("SELECT 1")

    def test_duckdb_rejects_nonexistent_table(self) -> None:
        ex = self._setup()
        try:
            with pytest.raises(duckdb.Error):
                ex.execute("SELECT * FROM missing")
        finally:
            ex.close()
