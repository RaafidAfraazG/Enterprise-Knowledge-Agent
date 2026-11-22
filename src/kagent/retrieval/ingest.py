"""Markdown chunking and idempotent Qdrant ingestion."""

import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from kagent.config import Settings
from kagent.llm.base import Embedder


def make_client(settings: Settings) -> QdrantClient:
    """Create a remote or ephemeral client from settings."""
    if not settings.qdrant_url:
        return QdrantClient(":memory:")
    return QdrantClient(
        url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value() or None
    )


def chunk_text(text: str, size: int = 500, overlap: int = 80) -> list[str]:
    """Split at nearby paragraph/sentence boundaries with bounded overlap."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("Require size > overlap >= 0")
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundaries = list(re.finditer(r"\n\n|(?<=[.!?])\s+", text[start:end]))
            candidates = [start + match.end() for match in boundaries if match.end() >= size // 2]
            if candidates:
                end = candidates[-1]
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def ingest_docs(
    directory: str | Path,
    embedder: Embedder,
    client: QdrantClient,
    collection: str = "company_docs",
) -> int:
    """Upsert all markdown chunks; unchanged files retain stable point IDs."""
    if not client.collection_exists(collection):
        client.create_collection(
            collection,
            vectors_config=models.VectorParams(size=embedder.dim, distance=models.Distance.COSINE),
        )
    count = 0
    for path in sorted(Path(directory).glob("*.md")):
        source = path.name
        chunks = chunk_text(path.read_text(encoding="utf-8"))
        vectors = embedder.embed(chunks)
        if len(vectors) != len(chunks):
            raise ValueError("Embedding count does not match chunk count")
        # Replace a source's previous chunks so shrinking files leave no stale points.
        client.delete(
            collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(key="source", match=models.MatchValue(value=source))
                    ]
                )
            ),
            wait=True,
        )
        points = [
            models.PointStruct(
                id=str(uuid5(NAMESPACE_URL, f"{source}:{index}")),
                vector=vector,
                payload={"source": source, "chunk_id": index, "text": text},
            )
            for index, (text, vector) in enumerate(zip(chunks, vectors, strict=True))
        ]
        if points:
            client.upsert(collection, points=points, wait=True)
        count += len(points)
    return count
