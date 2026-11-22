"""Offline fixtures: no sockets, real temporary SQLite, and shared in-memory Qdrant."""

import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from kagent.data import make_data
from kagent.graph.build import build_graph
from kagent.llm.fakes import FakeEmbeddings, FakeLLM
from kagent.retrieval.ingest import ingest_docs

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
DOCS = Path(__file__).resolve().parents[1] / "data" / "docs"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail immediately if a test attempts any real socket connection."""

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


@pytest.fixture
def sqlite_path(tmp_path: Path) -> Path:
    """Create a fresh, deterministic database per test."""
    path = tmp_path / "company.db"
    make_data(path)
    return path


@pytest.fixture(scope="session")
def embedder() -> FakeEmbeddings:
    """Share deterministic embeddings."""
    return FakeEmbeddings()


@pytest.fixture(scope="session")
def qdrant(embedder: FakeEmbeddings) -> Iterator[QdrantClient]:
    """Ingest policy markdown once into a shared in-memory instance."""
    client = QdrantClient(":memory:")
    ingest_docs(DOCS, embedder, client)
    yield client
    client.close()


@pytest.fixture
def graph(sqlite_path: Path, embedder: FakeEmbeddings, qdrant: QdrantClient):
    """Compile a graph with only offline dependencies."""
    return build_graph(FakeLLM(), embedder, qdrant, sqlite_path)
