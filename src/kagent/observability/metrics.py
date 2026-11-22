"""Context-local request accounting independent of external telemetry."""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

_current: ContextVar["Recorder | None"] = ContextVar("request_metrics", default=None)
_parent: ContextVar[str | None] = ContextVar("metric_parent", default=None)


class Recorder:
    """An individual request's events; child worker threads may share this recorder."""

    def __init__(self, question: str, profile: str) -> None:
        self.id = uuid4().hex
        self.question = question
        self.profile = profile
        self.started_at = datetime.now(UTC).isoformat()
        self.start = time.perf_counter()
        self.duration: float | None = None
        self.events: list[dict[str, Any]] = []
        self.lock = Lock()
        self.route = "unknown"
        self.answer = ""

    def summary(self) -> dict[str, Any]:
        """Sum call usage only; node spans do not double-count token/cost totals."""
        with self.lock:
            events = list(self.events)
        prompt = sum(item["prompt_tokens"] for item in events)
        completion = sum(item["completion_tokens"] for item in events)
        return dict(
            latency_seconds=self.duration
            if self.duration is not None
            else time.perf_counter() - self.start,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            estimated_cost_usd=sum(item["estimated_cost_usd"] for item in events),
            per_node_latency={
                item["name"]: item["latency_seconds"] for item in events if item["kind"] == "node"
            },
            cache_hit=any(item["cache_hit"] for item in events if item["name"] == "answer_cache"),
        )

    def finish(self, result: dict[str, Any]) -> None:
        """Attach the request outcome without copying internal application state."""
        self.route = result.get("route", "unknown")
        self.answer = result.get("answer", "")


def record(
    name: str,
    kind: str,
    start: float,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    estimated_cost_usd: float = 0,
    cache_hit: bool = False,
    model: str = "",
    deployment: str = "",
    input: Any = None,
    output: Any = None,
) -> None:
    """Record one timed operation if a request recorder is active."""
    current = _current.get()
    if current is None:
        return
    event = dict(
        name=name,
        kind=kind,
        latency_seconds=time.perf_counter() - start,
        offset_seconds=start - current.start,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost_usd,
        cache_hit=cache_hit,
        model=model,
        deployment=deployment,
        parent=_parent.get(),
        input=input,
        output=output,
    )
    with current.lock:
        current.events.append(event)


@contextmanager
def span(name: str, kind: str = "span", *, input: Any = None) -> Iterator[None]:
    """Time a node or storage span, restoring context after completion/errors."""
    start = time.perf_counter()
    token = _parent.set(name) if kind == "node" else None
    try:
        yield
    finally:
        if token is not None:
            _parent.reset(token)
        record(name, kind, start, input=input)


@contextmanager
def request_metrics(
    question: str, profile: str = "baseline", tracer: Any = None
) -> Iterator[Recorder]:
    """Create exactly one isolated recorder and export one completed request trace."""
    recorder = Recorder(question, profile)
    token = _current.set(recorder)
    try:
        yield recorder
    finally:
        recorder.duration = time.perf_counter() - recorder.start
        _current.reset(token)
        if tracer is not None:
            try:
                tracer.submit(
                    dict(
                        id=recorder.id,
                        question=question,
                        profile=profile,
                        answer=recorder.answer,
                        route=recorder.route,
                        started_at=recorder.started_at,
                        events=recorder.events,
                        metrics=recorder.summary(),
                    )
                )
            except Exception:
                logging.getLogger(__name__).warning("Trace enqueue failed; continuing")
