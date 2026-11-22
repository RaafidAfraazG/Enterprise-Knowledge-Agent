"""Bounded read-only SQLite access and schema inspection."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypedDict

from kagent.sql.guard import UnsafeSQLError, validate_select


class SQLResult(TypedDict):
    """JSON-compatible query result or safe error."""

    columns: list[str]
    rows: list[list[Any]]
    error: str | None


@contextmanager
def _connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        db.execute("PRAGMA query_only = ON")
        db.enable_load_extension(False)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
        yield db
    finally:
        db.close()


def execute_select(sql: str, path: str | Path) -> SQLResult:
    """Validate and execute with a read-only URI and VM instruction budget."""
    try:
        validated = validate_select(sql)
        with _connect(path) as db:
            # Deny all non-query operations even if a future parser rule regresses.
            def authorize(
                action: int,
                arg1: str | None,
                arg2: str | None,
                database: str | None,
                trigger: str | None,
            ) -> int:
                if action == sqlite3.SQLITE_FUNCTION:
                    return (
                        sqlite3.SQLITE_DENY
                        if (arg2 or "").upper() in {"LOAD_EXTENSION", "READFILE", "WRITEFILE"}
                        else sqlite3.SQLITE_OK
                    )
                return (
                    sqlite3.SQLITE_OK
                    if action
                    in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}
                    else sqlite3.SQLITE_DENY
                )

            db.set_authorizer(authorize)
            steps = 0

            def progress() -> int:
                nonlocal steps
                steps += 1
                return int(steps > 1000)

            db.set_progress_handler(progress, 1000)
            cursor = db.execute(validated)
            rows = [list(row) for row in cursor.fetchmany(100)]
            columns = [description[0] for description in cursor.description or []]
        return SQLResult(columns=columns, rows=rows, error=None)
    except (UnsafeSQLError, sqlite3.Error, ValueError):
        return SQLResult(columns=[], rows=[], error="The SQL query could not be executed safely.")


def read_schema(path: str | Path) -> str:
    """Read table definitions without opening the database for writes."""
    with _connect(path) as db:
        rows = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    return "\n".join(row[0] for row in rows if row[0])
