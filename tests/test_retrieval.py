"""Deterministic vectors and actual in-memory Qdrant search."""

import math
from contextlib import closing
from pathlib import Path

from qdrant_client import QdrantClient

from kagent.llm.fakes import FakeEmbeddings
from kagent.retrieval.ingest import chunk_text, ingest_docs
from kagent.retrieval.search import Retriever


def test_embeddings(embedder: FakeEmbeddings) -> None:
    vectors = embedder.embed(["annual leave", "annual leave", "annual leave days", "password"])
    assert len(vectors[0]) == 64 and vectors[0] == vectors[1]
    assert math.isclose(sum(value * value for value in vectors[0]), 1.0)

    def similarity(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert similarity(vectors[0], vectors[2]) > similarity(vectors[0], vectors[3])
    assert embedder.embed([""]) == [[0.0] * 64]


def test_leave_search(embedder: FakeEmbeddings, qdrant: QdrantClient) -> None:
    hits = Retriever(embedder, qdrant).search("how many days of annual leave", 3)
    assert "leave_policy.md" in [hit["source"] for hit in hits]
    assert all(hit["text"] and isinstance(hit["score"], float) for hit in hits)


def test_chunking() -> None:
    text = "First sentence. " * 120
    chunks = chunk_text(text)
    assert len(chunks) > 1 and all(len(chunk) <= 500 for chunk in chunks)
    assert chunks[0][-79:] in chunks[1][:81]


def test_idempotent_ingestion(tmp_path: Path, embedder: FakeEmbeddings) -> None:
    path = tmp_path / "example.md"
    path.write_text("Annual leave. " * 200)
    with closing(QdrantClient(":memory:")) as client:
        count = ingest_docs(tmp_path, embedder, client)
        assert ingest_docs(tmp_path, embedder, client) == count
        assert client.count("company_docs").count == count
        path.write_text("Annual leave is 25 days.")
        assert ingest_docs(tmp_path, embedder, client) == 1
        assert client.count("company_docs").count == 1


def test_empty_collection(embedder: FakeEmbeddings) -> None:
    with closing(QdrantClient(":memory:")) as client:
        assert Retriever(embedder, client).search("annual leave", 4) == []
