"""Validate every committed reference against real local source data."""

import json
from collections import Counter
from pathlib import Path

from kagent.sql.executor import execute_select
from kagent.sql.guard import validate_select
from scripts.build_eval_set import build

ROOT = Path(__file__).resolve().parents[1]


def test_corpus(sqlite_path: Path) -> None:
    lines = (ROOT / "eval/questions.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert len(lines) == 200
    assert Counter(row["route"] for row in rows) == {"doc": 100, "sql": 70, "clarify": 30}
    assert len({row["id"] for row in rows}) == 200
    assert len({row["question"].casefold() for row in rows}) == 200
    sources = Counter(row["source_doc"] for row in rows if row["route"] == "doc")
    assert len(sources) == 15 and set(sources.values()) <= {6, 7}
    for row in rows:
        assert set(row) == {
            "id",
            "question",
            "route",
            "expected_answer",
            "must_contain",
            "gold_sql",
            "source_doc",
        }
        if row["route"] == "sql":
            validate_select(row["gold_sql"])
            result = execute_select(row["gold_sql"], sqlite_path)
            assert result["error"] is None
            assert json.loads(row["expected_answer"])["rows"] == result["rows"]
        elif row["route"] == "doc":
            text = (ROOT / "data/docs" / row["source_doc"]).read_text().casefold()
            assert 1 <= len(row["must_contain"]) <= 3
            assert all(anchor.casefold() in text for anchor in row["must_contain"])
            assert row["gold_sql"] is None and row["expected_answer"]
        else:
            assert row["gold_sql"] is None and row["expected_answer"] == ""


def test_regeneration(sqlite_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "questions.jsonl"
    build(sqlite_path, output)
    assert output.read_bytes() == (ROOT / "eval/questions.jsonl").read_bytes()


def test_rebuild_preserves_curated_cases(sqlite_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "questions.jsonl"
    rows = build(sqlite_path, output)
    assert output.read_text() == (ROOT / "eval/questions.jsonl").read_text()
    assert len(rows) == 200
