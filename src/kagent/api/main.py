"""FastAPI application with lifespan-owned graph and automatic demo bootstrap."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from qdrant_client import QdrantClient

from kagent.config import Settings
from kagent.data import make_data
from kagent.graph.build import build_graph
from kagent.llm.base import ChatModel, Embedder
from kagent.llm.factory import get_chat_model, get_embedder
from kagent.observability.metrics import request_metrics
from kagent.observability.tracing import Tracer, make_tracer
from kagent.retrieval.ingest import ingest_docs, make_client


class AskRequest(BaseModel):
    """A trimmed, nonempty question with a bounded input length."""

    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question")
    @classmethod
    def nonempty(cls, value: str) -> str:
        """Reject whitespace-only input."""
        if not value.strip():
            raise ValueError("Question must not be empty")
        return value.strip()


class AskResponse(BaseModel):
    """Public branch result, without internal state or error details."""

    answer: str
    route: str
    sources: list[str]
    sql: str | None
    metrics: dict[str, Any] | None = None


def create_app(
    settings: Settings | None = None,
    *,
    chat: ChatModel | None = None,
    embedder: Embedder | None = None,
    qdrant_client: QdrantClient | None = None,
    docs_path: str | Path = "data/docs",
    tracer: Tracer | None = None,
) -> FastAPI:
    """Create an app; injected clients remain owned by their caller."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        config = settings or Settings()
        application.state.settings = config
        application.state.tracer = make_tracer(config, tracer)
        model = chat or get_chat_model(config)
        embeddings = embedder or get_embedder(config)
        client = qdrant_client if qdrant_client is not None else make_client(config)
        try:
            if not Path(config.sqlite_path).exists():
                make_data(config.sqlite_path)
            if (
                not client.collection_exists(config.qdrant_collection)
                or client.count(config.qdrant_collection, exact=True).count == 0
            ):
                if not list(Path(docs_path).glob("*.md")):
                    raise RuntimeError(
                        "No company markdown documents found; run from repository root"
                    )
                ingest_docs(docs_path, embeddings, client, config.qdrant_collection)
            application.state.graph = build_graph(
                model,
                embeddings,
                client,
                config.sqlite_path,
                collection=config.qdrant_collection,
                top_k=config.top_k,
                settings=config,
            )
            yield
        finally:
            application.state.tracer.flush()
            application.state.tracer.close() if hasattr(application.state.tracer, "close") else None
            if qdrant_client is None:
                client.close()

    application = FastAPI(title="Enterprise Knowledge Agent", lifespan=lifespan)

    @application.get("/health")
    def health() -> dict[str, str]:
        """Report service health after lifespan initialization."""
        return {"status": "ok"}

    @application.post("/ask", response_model=AskResponse, response_model_exclude_unset=True)
    def ask(
        body: AskRequest, debug_metrics: str | None = Header(default=None, alias="X-Debug-Metrics")
    ) -> AskResponse:
        """Run the graph once; never expose internal exception messages."""
        try:
            with request_metrics(
                body.question, application.state.settings.profile, application.state.tracer
            ) as recorder:
                result = application.state.graph.invoke({"question": body.question})
                recorder.finish(result)
            if result.get("error"):
                raise RuntimeError("Graph returned an error")
            extra = {"metrics": recorder.summary()} if debug_metrics == "1" else {}
            return AskResponse(
                answer=result["answer"],
                route=result["route"],
                sources=result.get("sources", []),
                sql=result.get("sql"),
                **extra,
            )
        except Exception:
            raise HTTPException(status_code=500, detail="Unable to answer the question.") from None

    return application


app = create_app()
