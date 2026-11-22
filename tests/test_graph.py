"""Routing and end-to-end branches through the actual compiled LangGraph."""

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from qdrant_client import QdrantClient

from kagent.graph.build import build_graph
from kagent.graph.nodes import parse_route
from kagent.llm.fakes import FakeEmbeddings, FakeLLM


@pytest.mark.parametrize(
    "question, route",
    [
        ("What is the annual leave policy?", "doc"),
        ("Can I work remotely?", "doc"),
        ("Explain expense reimbursement", "doc"),
        ("How many tickets are closed?", "sql"),
        ("What is the total sales amount?", "sql"),
        ("List employees", "sql"),
        ("What's the policy?", "clarify"),
        ("Help", "clarify"),
        ("Can you explain?", "clarify"),
    ],
)
def test_router(graph, question: str, route: str) -> None:
    assert graph.invoke({"question": question})["route"] == route


@pytest.mark.parametrize(
    "output, expected",
    [
        ('{"route":"sql"}', "sql"),
        ('```json\n{"route":"doc"}\n```', "doc"),
        ("DOC", "doc"),
        ("doc or sql", "clarify"),
        ("nonsense", "clarify"),
        ("[]", "clarify"),
        ('{"route": ["sql"]}', "clarify"),
    ],
)
def test_parse_route(output: str, expected: str) -> None:
    assert parse_route(output) == expected


def test_document_answer(graph) -> None:
    result = graph.invoke({"question": "What is the annual leave policy?"})
    assert result["route"] == "doc" and "25" in result["answer"]
    assert "leave_policy.md" in result["sources"] and result["sql"] is None


def test_sql_answer(graph, sqlite_path: Path) -> None:
    with sqlite3.connect(sqlite_path) as db:
        count = db.execute("SELECT COUNT(*) FROM tickets WHERE status='closed'").fetchone()[0]
    result = graph.invoke({"question": "How many tickets are closed?"})
    assert result["route"] == "sql" and str(count) in result["answer"]
    assert result["sql_result"]["rows"] == [[count]] and result["sql"].endswith("LIMIT 100")


def test_clarification_never_runs_sql(graph) -> None:
    with patch("kagent.graph.nodes.read_schema", side_effect=AssertionError("SQL touched")):
        result = graph.invoke({"question": "What's the policy?"})
    assert result["clarification_needed"] and result["answer"].count("?") == 1
    assert result["sql"] is None


def test_no_context(sqlite_path: Path, embedder: FakeEmbeddings) -> None:
    with closing(QdrantClient(":memory:")) as client:
        graph = build_graph(FakeLLM(), embedder, client, sqlite_path)
        result = graph.invoke({"question": "What is the annual leave policy?"})
    assert "could not find" in result["answer"] and result["sources"] == []


def test_unrelated_context(graph) -> None:
    assert (
        "could not find"
        in graph.invoke({"question": "What is the teleportation policy?"})["answer"]
    )
