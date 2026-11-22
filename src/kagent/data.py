"""Deterministic fictional company data shared by CLI and API."""

import random
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path


def make_data(path: str | Path = "data/company.db") -> None:
    """Replace the four demo tables with a reproducible, relational dataset."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            DROP TABLE IF EXISTS leave_requests;
            DROP TABLE IF EXISTS tickets;
            DROP TABLE IF EXISTS sales;
            DROP TABLE IF EXISTS employees;
            CREATE TABLE employees(id INTEGER PRIMARY KEY, name TEXT, department TEXT,
                role TEXT, hire_date TEXT, salary REAL, location TEXT);
            CREATE TABLE tickets(id INTEGER PRIMARY KEY, title TEXT, status TEXT,
                priority TEXT, created_at TEXT, closed_at TEXT,
                assignee_id INTEGER REFERENCES employees(id));
            CREATE TABLE sales(id INTEGER PRIMARY KEY, region TEXT, product TEXT,
                amount REAL, sale_date TEXT);
            CREATE TABLE leave_requests(id INTEGER PRIMARY KEY,
                employee_id INTEGER REFERENCES employees(id), type TEXT,
                start_date TEXT, end_date TEXT, status TEXT);
        """)
        for i in range(1, 51):
            db.execute(
                "INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    i,
                    f"Employee {i:02d}",
                    rng.choice(["Engineering", "Sales", "People", "Support"]),
                    rng.choice(["Associate", "Specialist", "Manager"]),
                    str(date(2020, 1, 1) + timedelta(days=rng.randrange(1500))),
                    rng.randrange(50_000, 150_001, 1000),
                    rng.choice(["London", "Chennai", "Boston"]),
                ),
            )
        for i in range(1, 301):
            created = date(2025, 1, 1) + timedelta(days=rng.randrange(300))
            status = rng.choice(["open", "pending", "closed"])
            db.execute(
                "INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    i,
                    f"Support request {i:03d}",
                    status,
                    rng.choice(["low", "medium", "high"]),
                    str(created),
                    str(created + timedelta(days=3)) if status == "closed" else None,
                    rng.randrange(1, 51),
                ),
            )
        for i in range(1, 201):
            db.execute(
                "INSERT INTO sales VALUES (?, ?, ?, ?, ?)",
                (
                    i,
                    rng.choice(["EMEA", "APAC", "Americas"]),
                    rng.choice(["Atlas", "Beacon", "Compass"]),
                    rng.randrange(100, 10001),
                    str(date(2025, 1, 1) + timedelta(days=rng.randrange(365))),
                ),
            )
        for i in range(1, 101):
            start = date(2025, 1, 1) + timedelta(days=rng.randrange(350))
            db.execute(
                "INSERT INTO leave_requests VALUES (?, ?, ?, ?, ?, ?)",
                (
                    i,
                    rng.randrange(1, 51),
                    rng.choice(["annual", "sick", "parental"]),
                    str(start),
                    str(start + timedelta(days=rng.randrange(1, 6))),
                    rng.choice(["approved", "pending", "rejected"]),
                ),
            )
