"""Compile the graph with opt-in runtime optimizations and node instrumentation."""

import logging
import time
from collections.abc import Callable
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from qdrant_client import QdrantClient

from kagent.cache import TTLCache, normalize
from kagent.config import Settings
from kagent.graph.nodes import Nodes
from kagent.graph.state import AgentState
from kagent.llm.base import ChatModel, Embedder
from kagent.observability.metrics import record, span
from kagent.observability.providers import ObservedChat, ObservedEmbedder
from kagent.retrieval.search import Retriever


def build_graph(
    chat: ChatModel,
    embedder: Embedder,
    qdrant_client: QdrantClient,
    sqlite_path: str | Path,
    *,
    collection: str = "company_docs",
    top_k: int | None = None,
    settings: Settings | None = None,
    router_chat: ChatModel | None = None,
) -> CompiledStateGraph:
    """Keep the baseline topology; add final-answer caching only when enabled."""
    settings = settings or Settings()
    answers = TTLCache(settings.cache_ttl_seconds)
    observed_embedder = ObservedEmbedder(embedder, settings)
    if not (settings.opt_router_model and settings.azure_openai_router_deployment):
        router_chat = None
    elif router_chat is None:
        from kagent.llm.factory import get_router_model

        router_chat = get_router_model(settings)
    if settings.opt_router_model and router_chat is None:
        logging.getLogger(__name__).info(
            "Router optimization inactive: no router deployment configured"
        )
    nodes = Nodes(
        ObservedChat(chat, settings),
        Retriever(observed_embedder, qdrant_client, collection),
        sqlite_path,
        top_k if top_k is not None else settings.top_k,
        settings,
        ObservedChat(router_chat, settings, max_tokens=8) if router_chat else None,
    )
    graph = StateGraph(AgentState)

    def timed(name: str, operation: Callable[[AgentState], AgentState]) -> Callable:
        def run(state: AgentState) -> AgentState:
            with span(name, "node", input=state["question"]):
                return operation(state)

        return run

    for name in ("router", "doc_agent", "sql_agent", "clarify_agent"):
        graph.add_node(name, timed(name, getattr(nodes, name)))

    def lookup(state: AgentState) -> AgentState:
        start = time.perf_counter()
        cached = answers.get(normalize(state["question"]))
        record("answer_cache", "span", start, cache_hit=cached is not None)
        if cached is not None:
            return AgentState(**{**cached, "question": state["question"], "cache_hit": True})
        return AgentState(cache_hit=False)

    def store(state: AgentState) -> AgentState:
        if not state.get("error") and state.get("route") in {"doc", "sql"}:
            answers.put(normalize(state["question"]), dict(state))
        return AgentState()

    if settings.opt_cache:
        graph.add_node("answer_cache", lookup)
        graph.add_node("store_cache", store)
        graph.add_edge(START, "answer_cache")
        graph.add_conditional_edges(
            "answer_cache", lambda state: bool(state.get("cache_hit")), {True: END, False: "router"}
        )
        graph.add_edge("store_cache", END)
    else:
        graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        lambda state: state["route"],
        {"doc": "doc_agent", "sql": "sql_agent", "clarify": "clarify_agent"},
    )
    for name in ("doc_agent", "sql_agent", "clarify_agent"):
        graph.add_edge(name, "store_cache" if settings.opt_cache else END)
    compiled = graph.compile()

    def clear_caches() -> None:
        answers.clear()
        observed_embedder.cache.clear()
        nodes.schema_cache.clear()

    compiled.clear_caches = clear_caches
    return compiled
