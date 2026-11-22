"""Retrieve relevant chunks with sources and cosine scores."""

from typing import TypedDict

from qdrant_client import QdrantClient

from kagent.llm.base import Embedder
from kagent.observability.metrics import span


class SearchHit(TypedDict):
    """A retrieved chunk and its provenance."""

    text: str
    source: str
    score: float


class Retriever:
    """A search interface sharing an injected Qdrant client."""

    def __init__(
        self, embedder: Embedder, client: QdrantClient, collection: str = "company_docs"
    ) -> None:
        self.embedder = embedder
        self.client = client
        self.collection = collection

    def search(self, query: str, k: int, *, vector: list[float] | None = None) -> list[SearchHit]:
        """Return positive-scoring hits, or no context for an empty collection."""
        with span("retrieval", input=query):
            return self._search(query, k, vector=vector)

    def _search(self, query: str, k: int, *, vector: list[float] | None) -> list[SearchHit]:
        with span("qdrant.collection_exists"):
            exists = self.client.collection_exists(self.collection)
        if not exists:
            return []
        vector = vector if vector is not None else self.embedder.embed([query])[0]
        with span("qdrant", input=query):
            points = self.client.query_points(
                self.collection,
                query=vector,
                limit=k,
                with_payload=True,
                score_threshold=0.15,
            ).points
        return [
            SearchHit(
                text=str(point.payload["text"]),
                source=str(point.payload["source"]),
                score=point.score,
            )
            for point in points
            if point.payload
        ]
