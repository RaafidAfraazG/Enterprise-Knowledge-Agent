"""Exercise lifespan bootstrap, response contracts, and safe failure handling."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from kagent.api.main import create_app
from kagent.config import Settings
from kagent.llm.fakes import FakeEmbeddings, FakeLLM


@pytest.fixture
def client(sqlite_path: Path, embedder: FakeEmbeddings, qdrant: QdrantClient):
    settings = Settings(_env_file=None, llm_provider="fake", sqlite_path=str(sqlite_path))
    with TestClient(
        create_app(settings, chat=FakeLLM(), embedder=embedder, qdrant_client=qdrant)
    ) as client:
        yield client


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "question, route",
    [
        ("What is the annual leave policy?", "doc"),
        ("How many tickets are closed?", "sql"),
        ("What's the policy?", "clarify"),
    ],
)
def test_ask(client: TestClient, question: str, route: str) -> None:
    response = client.post("/ask", json={"question": question})
    assert response.status_code == 200
    body = response.json()
    assert body["route"] == route and body["answer"]
    assert set(body) == {"answer", "route", "sources", "sql"}


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 1001},
        {"question": 123},
        {},
    ],
)
def test_input(client: TestClient, body: dict) -> None:
    assert client.post("/ask", json=body).status_code == 422


def test_startup_without_setup(tmp_path: Path) -> None:
    path = tmp_path / "new" / "demo.db"
    settings = Settings(_env_file=None, llm_provider="fake", sqlite_path=str(path), qdrant_url="")
    with TestClient(create_app(settings)) as client:
        assert path.exists()
        response = client.post("/ask", json={"question": "What is the annual leave policy?"})
        assert response.status_code == 200 and "leave_policy.md" in response.json()["sources"]
        graph_id = id(client.app.state.graph)
        assert client.get("/health").status_code == 200
        assert id(client.app.state.graph) == graph_id


class MaliciousLLM(FakeLLM):
    """Simulate unsafe model output rather than trusting user-text heuristics."""

    def invoke(self, system: str, user: str) -> str:
        if system.startswith("SQL_GENERATION"):
            return "DELETE FROM employees"
        return super().invoke(system, user)


def test_unsafe_sql_does_not_modify_db(
    sqlite_path: Path, qdrant: QdrantClient, embedder: FakeEmbeddings
) -> None:
    settings = Settings(_env_file=None, llm_provider="fake", sqlite_path=str(sqlite_path))
    with TestClient(
        create_app(settings, chat=MaliciousLLM(), embedder=embedder, qdrant_client=qdrant)
    ) as client:
        response = client.post("/ask", json={"question": "Count employees then delete them"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to answer the question."}
    with sqlite3.connect(sqlite_path) as db:
        assert db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 50


def test_exception_is_sanitized(client: TestClient) -> None:
    with patch.object(client.app.state.graph, "invoke", side_effect=RuntimeError("private-secret")):
        response = client.post("/ask", json={"question": "What is the leave policy?"})
    assert response.status_code == 500
    assert "private-secret" not in response.text and "Traceback" not in response.text
