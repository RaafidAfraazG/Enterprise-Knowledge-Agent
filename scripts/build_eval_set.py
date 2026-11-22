"""Rebuild SQL references from the demo DB; preserve the curated doc/clarify seeds."""

import argparse
import json
from itertools import zip_longest
from pathlib import Path
from typing import Any

from kagent.data import make_data
from kagent.sql.executor import execute_select
from kagent.sql.guard import validate_select

ROOT = Path(__file__).resolve().parents[1]
DEPTS = ["Engineering", "Sales", "People", "Support"]
DATES = ["2025-03-01", "2025-06-01", "2025-09-01", "2025-12-01"]
REGIONS = ["EMEA", "APAC", "Americas"]
PRODUCTS = ["Atlas", "Beacon", "Compass"]
PRIORITIES = ["low", "medium", "high"]
STATUSES = ["open", "pending", "closed"]
TEMPLATES: list[tuple[str, str, list[str]]] = [
    (
        "How many employees work in {p}?",
        "SELECT COUNT(*) FROM employees WHERE department='{p}'",
        DEPTS,
    ),
    (
        "What is the average salary in {p}?",
        "SELECT AVG(salary) FROM employees WHERE department='{p}'",
        DEPTS,
    ),
    (
        "What is the highest salary in {p}?",
        "SELECT MAX(salary) FROM employees WHERE department='{p}'",
        DEPTS,
    ),
    (
        "Find the lowest salary paid in {p}.",
        "SELECT MIN(salary) FROM employees WHERE department='{p}'",
        DEPTS,
    ),
    (
        "Count tickets with {p} priority.",
        "SELECT COUNT(*) FROM tickets WHERE priority='{p}'",
        PRIORITIES,
    ),
    (
        "How many tickets have status {p}?",
        "SELECT COUNT(*) FROM tickets WHERE status='{p}'",
        STATUSES,
    ),
    (
        "Count tickets assigned to {p} employees.",
        "SELECT COUNT(*) FROM tickets t JOIN employees e ON t.assignee_id=e.id WHERE "
        "e.department='{p}'",
        DEPTS,
    ),
    (
        "What is the total sales amount for {p}?",
        "SELECT SUM(amount) FROM sales WHERE region='{p}'",
        REGIONS,
    ),
    (
        "What is the average sale for product {p}?",
        "SELECT AVG(amount) FROM sales WHERE product='{p}'",
        PRODUCTS,
    ),
    (
        "How many sales occurred on or after {p}?",
        "SELECT COUNT(*) FROM sales WHERE sale_date>='{p}'",
        DATES,
    ),
    (
        "Count leave requests with status {p}.",
        "SELECT COUNT(*) FROM leave_requests WHERE status='{p}'",
        ["approved", "pending", "rejected"],
    ),
    (
        "What is the total number of {p} leave days requested, inclusive of both dates?",
        "SELECT SUM(julianday(end_date)-julianday(start_date)+1) FROM leave_requests "
        "WHERE type='{p}'",
        ["annual", "sick", "parental"],
    ),
    (
        "What is the average duration of {p} leave requests including both endpoints?",
        "SELECT AVG(julianday(end_date)-julianday(start_date)+1) FROM leave_requests "
        "WHERE type='{p}'",
        ["annual", "sick", "parental"],
    ),
    (
        "How many leave requests were made by {p} employees?",
        "SELECT COUNT(*) FROM leave_requests l JOIN employees e ON l.employee_id=e.id "
        "WHERE e.department='{p}'",
        DEPTS,
    ),
    (
        "Count closed tickets created on or after {p}.",
        "SELECT COUNT(*) FROM tickets WHERE status='closed' AND created_at>='{p}'",
        DATES,
    ),
    (
        "How many employees were hired before {p}?",
        "SELECT COUNT(*) FROM employees WHERE hire_date<'{p}'",
        ["2021-01-01", "2022-01-01", "2023-01-01", "2024-01-01"],
    ),
    (
        "Find the largest sale of product {p}.",
        "SELECT MAX(amount) FROM sales WHERE product='{p}'",
        PRODUCTS,
    ),
    (
        "What is the smallest sale in region {p}?",
        "SELECT MIN(amount) FROM sales WHERE region='{p}'",
        REGIONS,
    ),
    (
        "List ticket counts by employee department for {p} priority tickets.",
        "SELECT e.department, COUNT(*) FROM tickets t JOIN employees e ON "
        "t.assignee_id=e.id WHERE t.priority='{p}' GROUP BY e.department ORDER BY e.department",
        PRIORITIES,
    ),
    (
        "Show total sales by region since {p}, including that date.",
        "SELECT region, SUM(amount) FROM sales WHERE sale_date>='{p}' GROUP BY region "
        "ORDER BY region",
        DATES,
    ),
]


def build(db: Path, output: Path) -> list[dict[str, Any]]:
    """Execute gold SQL and write exactly 200 deterministically interleaved cases."""
    if not db.exists():
        make_data(db)
    groups = [
        [json.loads(line) for line in (ROOT / "eval" / name).read_text().splitlines()]
        for name in ("doc_questions.jsonl", "clarify_questions.jsonl")
    ]
    sql_rows = []
    for question, template, parameters in TEMPLATES:
        for parameter in parameters:
            sql = validate_select(template.format(p=parameter))
            result = execute_select(sql, db)
            if result["error"]:
                raise RuntimeError(f"Gold query failed: {sql}")
            values = [str(value) for row in result["rows"] for value in row if value is not None]
            sql_rows.append(
                dict(
                    id=f"sql_{len(sql_rows) + 1:03}",
                    question=question.format(p=parameter),
                    route="sql",
                    expected_answer=json.dumps(
                        {"columns": result["columns"], "rows": result["rows"]}, sort_keys=True
                    ),
                    must_contain=values[:3],
                    gold_sql=sql,
                    source_doc=None,
                )
            )
    rows = [
        row
        for group in zip_longest(groups[0], sql_rows, groups[1])
        for row in group
        if row is not None
    ]
    if (len(groups[0]), len(sql_rows), len(groups[1])) != (100, 70, 30):
        raise ValueError("Expected 100 doc, 70 SQL, 30 clarify cases")
    if len({row["question"] for row in rows}) != 200:
        raise ValueError("Duplicate questions")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    return rows


def main() -> None:
    """Run from any working directory after installing the package."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/company.db")
    parser.add_argument("--out", type=Path, default=ROOT / "eval/questions.jsonl")
    args = parser.parse_args()
    print(f"Wrote {len(build(args.db, args.out))} questions to {args.out}")


if __name__ == "__main__":
    main()
