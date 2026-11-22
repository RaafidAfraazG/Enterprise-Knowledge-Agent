"""Offline metrics/tracing contracts, including failures and context isolation."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from kagent.api.main import create_app
from kagent.config import Settings
from kagent.llm.fakes import FakeEmbeddings, FakeLLM
from kagent.observability.metrics import record, request_metrics
from kagent.observability.pricing import estimate
from kagent.observability.tracing import LangfuseSink, NullTracer, make_tracer


class Sink:
    """Collect completed traces without accessing a real SDK transport."""

    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []

    def submit(self, trace: dict[str, Any]) -> None:
        self.traces.append(trace)

    def flush(self) -> None:
        pass


def test_debug_metrics_and_trace(
    sqlite_path: Path, qdrant: QdrantClient, embedder: FakeEmbeddings
) -> None:
    sink = Sink()
    settings = Settings(_env_file=None, sqlite_path=str(sqlite_path))
    app = create_app(settings, chat=FakeLLM(), embedder=embedder, qdrant_client=qdrant, tracer=sink)
    with TestClient(app) as client:
        response = client.post(
            "/ask",
            json={"question": "What is the annual leave policy?"},
            headers={"X-Debug-Metrics": "1"},
        )
        assert response.status_code == 200
        metrics = response.json()["metrics"]
        assert metrics["total_tokens"] > 0 and metrics["estimated_cost_usd"] == 0
        assert set(metrics["per_node_latency"]) == {"router", "doc_agent"}
        assert metrics["cache_hit"] is False
        app.state.tracer.flush()
        assert len(sink.traces) == 1
        trace = sink.traces[0]
        assert trace["profile"] == "baseline" and trace["route"] == "doc"
        generations = [event for event in trace["events"] if event["kind"] == "llm"]
        assert len(generations) == 2
        assert all(event["prompt_tokens"] > 0 and event["output"] for event in generations)
        assert {"retrieval", "qdrant", "query_embeddings"} <= {e["name"] for e in trace["events"]}
        response = client.post("/ask", json={"question": "How many tickets are closed?"})
        assert set(response.json()) == {"answer", "route", "sources", "sql"}
        app.state.tracer.flush()
        assert len(sink.traces) == 2
        assert "sql_execution" in {e["name"] for e in sink.traces[-1]["events"]}


def test_raising_tracer_never_breaks_api(sqlite_path: Path) -> None:
    sink = Mock(spec=Sink)
    sink.submit.side_effect = RuntimeError("outage")
    sink.flush.side_effect = RuntimeError("outage")
    app = create_app(Settings(_env_file=None, sqlite_path=str(sqlite_path)), tracer=sink)
    with TestClient(app) as client:
        assert (
            client.post("/ask", json={"question": "What is the leave policy?"}).status_code == 200
        )


def test_slow_tracer_runs_outside_request(sqlite_path: Path) -> None:
    entered, release = Event(), Event()
    sink = Sink()

    def blocked(trace: dict[str, Any]) -> None:
        entered.set()
        release.wait(5)

    sink.submit = blocked
    app = create_app(Settings(_env_file=None, sqlite_path=str(sqlite_path)), tracer=sink)
    try:
        with TestClient(app) as client:
            response = client.post("/ask", json={"question": "What's the policy?"})
            assert response.status_code == 200
            assert entered.wait(1) and not release.is_set()
            release.set()
    finally:
        release.set()


def test_unconfigured_noop() -> None:
    settings = Settings(_env_file=None, langfuse_public_key="", langfuse_secret_key="")
    assert isinstance(make_tracer(settings), NullTracer)


def test_context_isolation() -> None:
    def run(index: int) -> int:
        with request_metrics(str(index)) as recorder:
            record("example", "llm", recorder.start, prompt_tokens=index)
        return recorder.summary()["total_tokens"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(run, range(20))) == list(range(20))


def test_price_overrides_and_unknown(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(_env_file=None)
    assert estimate("gpt-4o-mini", 1_000_000, 0, settings) == 0.15
    assert estimate("unpriced-model", 100, 100, settings) == 0
    assert "Unknown model" in caplog.text
    settings = Settings(
        _env_file=None, price_chat_input_per_m=1, price_chat_output_per_m=2, price_embed_per_m=3
    )
    assert estimate("anything", 1_000_000, 1_000_000, settings) == 3
    assert estimate("anything", 1_000_000, 0, settings, embedding=True) == 3


def test_no_keys_no_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    assert isinstance(make_tracer(Settings(_env_file=None)), NullTracer)


def test_adapter_uses_inspected_api() -> None:
    sink = LangfuseSink.__new__(LangfuseSink)
    sink.client = Mock()
    with request_metrics("example") as recorder:
        record("router", "node", recorder.start)
        record(
            "router_call",
            "llm",
            recorder.start,
            prompt_tokens=3,
            completion_tokens=1,
            model="gpt-4o-mini",
            output="doc",
        )
    sink.submit(
        dict(
            id="test",
            question="example",
            answer="answer",
            route="doc",
            profile="baseline",
            started_at=recorder.started_at,
            events=recorder.events,
            metrics=recorder.summary(),
        )
    )
    sink.client.trace.assert_called_once()
    trace = sink.client.trace.return_value
    trace.span.assert_called_once()
    trace.generation.assert_called_once()
    assert trace.generation.call_args.kwargs["usage_details"] == {"input": 3, "output": 1}


def test_azure_usage_and_caps_without_network() -> None:
    from types import SimpleNamespace

    from kagent.llm.azure import AzureChatModel, AzureEmbedder
    from kagent.observability.providers import ObservedChat

    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))],
        usage=SimpleNamespace(prompt_tokens=31, completion_tokens=7),
        model="gpt-4o-mini",
    )
    model = AzureChatModel(client, "custom-deployment")
    with request_metrics("example") as recorder:
        assert (
            ObservedChat(model, Settings(_env_file=None, opt_short_prompts=True)).invoke(
                "ANSWER_DOC: only context", "question"
            )
            == "answer"
        )
    assert client.chat.completions.create.call_args.kwargs["max_tokens"] == 300
    assert recorder.summary()["total_tokens"] == 38
    assert recorder.summary()["estimated_cost_usd"] > 0
    assert recorder.events[0]["deployment"] == "custom-deployment"
    client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=[0.1, 0.2])],
        usage=SimpleNamespace(prompt_tokens=12),
        model="text-embedding-3-small",
    )
    embedding = AzureEmbedder(client, "embedding-deployment")
    result = embedding.embed_with_usage(["example"])
    assert result.prompt_tokens == 12 and result.vectors == [[0.1, 0.2]]
    assert embedding.dim == 2
    client.embeddings.create.assert_called_once()
