"""Flags must preserve baseline behavior and optimize only their designated work."""

from pathlib import Path
from unittest.mock import patch

import pytest
from qdrant_client import QdrantClient

from kagent.cache import TTLCache
from kagent.config import Settings
from kagent.graph.build import build_graph
from kagent.llm.fakes import FakeEmbeddings, FakeLLM
from kagent.observability.metrics import request_metrics


class CountingLLM(FakeLLM):
    """Keep fake behavior while exposing provider call counts."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, system: str, user: str) -> str:
        self.calls.append(system.split(":", 1)[0])
        return super().invoke(system, user)


def test_profile_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROFILE", raising=False)
    assert Settings(_env_file=None).profile == "baseline"
    assert Settings(_env_file=None).opt_cache is False
    assert Settings(_env_file=None, profile="optimized").top_k == 3
    monkeypatch.setenv("OPT_CACHE", "false")
    monkeypatch.setenv("TOP_K", "7")
    settings = Settings(_env_file=None, profile="optimized")
    assert settings.opt_cache is False and settings.top_k == 7


def test_cache_skips_calls_and_records_hit(
    sqlite_path: Path, qdrant: QdrantClient, embedder: FakeEmbeddings
) -> None:
    chat = CountingLLM()
    graph = build_graph(
        chat, embedder, qdrant, sqlite_path, settings=Settings(_env_file=None, opt_cache=True)
    )
    first = graph.invoke({"question": "What is the annual leave policy?"})
    count = len(chat.calls)
    with request_metrics("repeat") as metrics:
        second = graph.invoke({"question": "  WHAT  is the annual leave policy!!! "})
    assert first["answer"] == second["answer"] and first["sources"] == second["sources"]
    assert len(chat.calls) == count and metrics.summary()["cache_hit"]
    assert metrics.summary()["total_tokens"] == 0
    graph.clear_caches()
    graph.invoke({"question": "What is the annual leave policy?"})
    assert len(chat.calls) > count


def test_never_cache_clarifications_or_errors(
    sqlite_path: Path, qdrant: QdrantClient, embedder: FakeEmbeddings
) -> None:
    chat = CountingLLM()
    graph = build_graph(
        chat, embedder, qdrant, sqlite_path, settings=Settings(_env_file=None, opt_cache=True)
    )
    for question in ["What's the policy?", "How many spaceships exist?"]:
        graph.invoke({"question": question})
        count = len(chat.calls)
        graph.invoke({"question": question})
        assert len(chat.calls) > count


@pytest.mark.parametrize(
    "question",
    ["What is the annual leave policy?", "How many tickets are closed?", "What's the policy?"],
)
def test_parallel_equals_sequential(
    question: str, sqlite_path: Path, qdrant: QdrantClient, embedder: FakeEmbeddings
) -> None:
    results = [
        build_graph(
            FakeLLM(),
            embedder,
            qdrant,
            sqlite_path,
            settings=Settings(_env_file=None, opt_parallel=enabled),
        ).invoke({"question": question})
        for enabled in (False, True)
    ]
    assert results[0] == results[1]


def test_router_model_only_if_enabled_and_configured(
    sqlite_path: Path, qdrant: QdrantClient, embedder: FakeEmbeddings
) -> None:
    for enabled, deployment in [(False, ""), (True, ""), (False, "small"), (True, "small")]:
        chat, router = CountingLLM(), CountingLLM()
        graph = build_graph(
            chat,
            embedder,
            qdrant,
            sqlite_path,
            router_chat=router,
            settings=Settings(
                _env_file=None, opt_router_model=enabled, azure_openai_router_deployment=deployment
            ),
        )
        graph.invoke({"question": "What is the leave policy?"})
        assert bool(router.calls) == bool(enabled and deployment)
        assert ("ROUTER" in chat.calls) != bool(enabled and deployment)


def test_ttl_lru_and_copy() -> None:
    clock = [0.0]
    cache = TTLCache(10, 2, clock=lambda: clock[0])
    cache.put("one", {"value": 1})
    cache.put("two", {"value": 2})
    value = cache.get("one")
    value["value"] = 9
    cache.put("three", 3)
    assert cache.get("two") is None and cache.get("one") == {"value": 1}
    clock[0] = 11
    assert cache.get("one") is None


def test_embedding_and_schema_cache(
    sqlite_path: Path, qdrant: QdrantClient, embedder: FakeEmbeddings
) -> None:
    from kagent.graph.nodes import Nodes
    from kagent.observability.providers import ObservedEmbedder
    from kagent.retrieval.search import Retriever

    settings = Settings(_env_file=None, opt_cache=True)
    observed = ObservedEmbedder(embedder, settings)
    with patch.object(embedder, "embed", wraps=embedder.embed) as embed:
        with request_metrics("embedding") as metrics:
            observed.embed(["leave"])
            observed.embed(["leave"])
        assert embed.call_count == 1
        assert any(event["cache_hit"] for event in metrics.events)
    nodes = Nodes(FakeLLM(), Retriever(observed, qdrant), sqlite_path, 4, settings)
    with patch(
        "kagent.graph.nodes.read_schema",
        wraps=__import__("kagent.sql.executor", fromlist=["read_schema"]).read_schema,
    ) as schema:
        assert nodes.get_schema() == nodes.get_schema()
        assert schema.call_count == 1
