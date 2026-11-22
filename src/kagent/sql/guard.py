"""Conservative SELECT-only validation, independent of the database connection."""

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType


class UnsafeSQLError(ValueError):
    """A query violates the read-only SQL policy."""


DENIED = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "ATTACH",
    "DETACH",
    "PRAGMA",
    "REPLACE",
    "VACUUM",
    "REINDEX",
    "ANALYZE",
    "INTO",
    "LOAD_EXTENSION",
    "WRITEFILE",
    "READFILE",
}


def validate_select(sql: str) -> str:
    """Accept exactly one SELECT (optionally WITH), bounded to at most 100 rows."""
    if not sql.strip() or len(sql) > 20_000 or re.search(r"--|/\*|\*/", sql):
        raise UnsafeSQLError("Empty, oversized, or commented SQL is not allowed")
    try:
        tokens = sqlglot.tokenize(sql, read="sqlite")
        if any(
            token.token_type != TokenType.STRING and token.text.upper() in DENIED
            for token in tokens
        ):
            raise UnsafeSQLError("Forbidden SQL keyword")
        statements = sqlglot.parse(sql, read="sqlite")
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise UnsafeSQLError("Exactly one SELECT is required")
        query = statements[0]
        # Every nested query must also be read-only, including CTE bodies.
        for node in query.walk():
            if isinstance(node, (exp.DDL, exp.DML, exp.Command, exp.Into)):
                raise UnsafeSQLError("Modifying SQL is not allowed")
        limit = query.args.get("limit")
        if limit is None:
            query = query.limit(100)
        else:
            value = limit.expression
            if not isinstance(value, exp.Literal) or not value.is_int:
                raise UnsafeSQLError("LIMIT must be a nonnegative integer")
            if int(value.this) < 0:
                raise UnsafeSQLError("LIMIT must be a nonnegative integer")
            if int(value.this) > 100:
                query = query.limit(100)
        return query.sql(dialect="sqlite")
    except SqlglotError as exc:
        raise UnsafeSQLError("SQL could not be parsed") from exc
