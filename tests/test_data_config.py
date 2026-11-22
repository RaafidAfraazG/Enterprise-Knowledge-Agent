"""Configuration defaults and deterministic demo data."""

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from kagent.config import Settings
from kagent.data import make_data
from kagent.llm.factory import get_chat_model, get_embedder
from kagent.llm.fakes import FakeEmbeddings, FakeLLM


def test_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in Settings.model_fields:
        monkeypatch.delenv(key.upper(), raising=False)
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "fake" and settings.top_k == 4
    assert settings.qdrant_collection == "company_docs" and not settings.qdrant_url
    assert isinstance(get_chat_model(settings), FakeLLM)
    assert isinstance(get_embedder(settings), FakeEmbeddings)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, top_k=0)


def test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOP_K", "2")
    monkeypatch.setenv("QDRANT_COLLECTION", "test_collection")
    assert Settings(_env_file=None).top_k == 2
    assert Settings(_env_file=None).qdrant_collection == "test_collection"


def test_demo_counts_and_repeatability(sqlite_path: Path) -> None:
    def snapshot() -> list[str]:
        with sqlite3.connect(sqlite_path) as db:
            counts = [
                db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("employees", "tickets", "sales", "leave_requests")
            ]
            assert counts == [50, 300, 200, 100]
            return list(db.iterdump())

    before = snapshot()
    make_data(sqlite_path)
    assert snapshot() == before


def test_policy_documents() -> None:
    docs = list((Path(__file__).resolve().parents[1] / "data" / "docs").glob("*.md"))
    assert len(docs) == 15
    assert all(200 <= len(path.read_text().split()) <= 400 for path in docs)
