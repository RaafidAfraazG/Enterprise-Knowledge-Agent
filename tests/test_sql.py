"""Exercise both SQL guard layers and the SQLite read-only boundary."""

import sqlite3
from pathlib import Path

import pytest
import sqlglot

from kagent.sql.executor import _connect, execute_select
from kagent.sql.guard import UnsafeSQLError, validate_select


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1",
        "SELECT name FROM employees;",
        "WITH x AS (SELECT 1 AS value) SELECT value FROM x",
        "SELECT 'delete' AS harmless_text",
    ],
)
def test_select(query: str) -> None:
    validated = validate_select(query)
    assert sqlglot.parse_one(validated, read="sqlite").args["limit"].expression.this == "100"


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE employees",
        "DELETE FROM tickets",
        "UPDATE employees SET salary=1",
        "INSERT INTO employees(id) VALUES (99)",
        "SELECT 1; SELECT 2",
        "SELECT 1;;",
        "PRAGMA table_info(employees)",
        "ATTACH DATABASE ':memory:' AS x",
        "SELECT 1; -- hidden\nDROP TABLE tickets",
        "SELECT 1 /* harmless-looking comment */",
        "WITH x AS (DELETE FROM employees RETURNING *) SELECT * FROM x",
        "SELECT load_extension('bad')",
        "SELECT readfile('/etc/passwd')",
        "REPLACE INTO employees(id) VALUES(1)",
        "CREATE TABLE x(id)",
        "ALTER TABLE tickets ADD COLUMN x TEXT",
        "SELECT * INTO backup FROM tickets",
        "SELECT 1 LIMIT -1",
        "SELECT 1 LIMIT ?",
        "",
        "SELECT FROM",
    ],
)
def test_reject(query: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select(query)


def test_existing_limit_and_upper_bound() -> None:
    assert validate_select("SELECT 1 LIMIT 3").endswith("LIMIT 3")
    assert validate_select("SELECT 1 LIMIT 10000").endswith("LIMIT 100")


def test_executor(sqlite_path: Path) -> None:
    result = execute_select("SELECT COUNT(*) AS count FROM employees", sqlite_path)
    assert result == {"columns": ["count"], "rows": [[50]], "error": None}
    assert len(execute_select("SELECT * FROM tickets", sqlite_path)["rows"]) == 100


def test_database_itself_is_read_only(sqlite_path: Path) -> None:
    with _connect(sqlite_path) as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM employees")
    assert execute_select("DELETE FROM employees", sqlite_path)["error"]
    assert execute_select("SELECT COUNT(*) FROM employees", sqlite_path)["rows"] == [[50]]


def test_invalid_and_missing_database(sqlite_path: Path, tmp_path: Path) -> None:
    assert execute_select("SELECT missing FROM employees", sqlite_path)["error"]
    missing = tmp_path / "absent.db"
    assert execute_select("SELECT 1", missing)["error"]
    assert not missing.exists()


def test_query_budget(sqlite_path: Path) -> None:
    query = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000000) "
        "SELECT SUM(x) FROM n"
    )
    assert execute_select(query, sqlite_path)["error"]
