"""Best-effort Langfuse 2.60.10 export, entirely outside the request path."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from queue import Full, Queue
from threading import Event, Thread
from typing import Any, Protocol

from kagent.config import Settings

log = logging.getLogger(__name__)


class Tracer(Protocol):
    """Small injectable sink contract; all sink methods run on a daemon worker."""

    def submit(self, trace: dict[str, Any]) -> None: ...
    def flush(self) -> None: ...


class NullTracer:
    """No thread, SDK import, or network when tracing is unconfigured."""

    def submit(self, trace: dict[str, Any]) -> None:
        """Discard a completed trace."""

    def flush(self, timeout: float = 0.2) -> None:
        """Nothing to flush."""

    def close(self, timeout: float = 0.2) -> None:
        """Nothing to close."""


class BackgroundTracer:
    """Nonblocking enqueue, bounded memory and bounded shutdown waiting."""

    def __init__(self, factory: Callable[[], Tracer], capacity: int = 256) -> None:
        self.queue: Queue[Any] = Queue(maxsize=capacity)
        self.closed = False
        self.worker = Thread(target=self._run, args=(factory,), daemon=True, name="kagent-tracing")
        self.worker.start()

    def _run(self, factory: Callable[[], Tracer]) -> None:
        sink = None
        try:
            sink = factory()
        except Exception:
            log.warning("Tracing initialization failed; continuing without export")
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    if sink is not None:
                        sink.flush()
                    return
                if isinstance(item, Event):
                    if sink is not None:
                        sink.flush()
                    item.set()
                elif sink is not None:
                    sink.submit(item)
            except Exception:
                log.warning("Tracing export failed; request unaffected")
                if isinstance(item, Event):
                    item.set()
            finally:
                self.queue.task_done()

    def submit(self, trace: dict[str, Any]) -> None:
        """Drop instead of waiting when an exporter is slow or its queue is full."""
        if not self.closed:
            try:
                self.queue.put_nowait(trace)
            except Full:
                log.warning("Tracing queue full; dropped trace")

    def flush(self, timeout: float = 0.2) -> None:
        """Request an SDK flush, waiting at most timeout seconds."""
        ready = Event()
        try:
            self.queue.put_nowait(ready)
        except Full:
            return
        ready.wait(timeout)

    def close(self, timeout: float = 0.2) -> None:
        """Signal shutdown without holding application shutdown for an outage."""
        self.closed = True
        try:
            self.queue.put_nowait(None)
        except Full:
            return
        self.worker.join(timeout)


class LangfuseSink:
    """Adapter for inspected 2.60.10 trace/span/generation/usage_details APIs."""

    def __init__(self, settings: Settings) -> None:
        from langfuse import Langfuse

        self.client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            host=settings.langfuse_host,
            timeout=1,
            max_retries=0,
            threads=1,
            flush_at=20,
            flush_interval=1,
        )

    def submit(self, payload: dict[str, Any]) -> None:
        """Export a finished request with original timestamps and parent node spans."""
        trace = self.client.trace(
            id=payload["id"],
            name="ask",
            input=payload["question"],
            output=payload["answer"],
            tags=[payload["profile"], payload["route"]],
            metadata=payload["metrics"],
        )
        nodes = {}
        origin = datetime.fromisoformat(payload["started_at"])
        for event in sorted(payload["events"], key=lambda value: value["kind"] != "node"):
            start = origin + timedelta(seconds=event["offset_seconds"])
            kwargs = dict(
                name=event["name"],
                start_time=start,
                end_time=start + timedelta(seconds=event["latency_seconds"]),
                input=event.get("input"),
                output=event.get("output"),
                metadata={
                    "cache_hit": event["cache_hit"],
                    "deployment": event["deployment"],
                    "estimated_cost_usd": event["estimated_cost_usd"],
                },
            )
            parent = nodes.get(event.get("parent"), trace)
            if event["kind"] in {"llm", "embedding"}:
                parent.generation(
                    **kwargs,
                    model=event["model"],
                    usage_details={
                        "input": event["prompt_tokens"],
                        "output": event["completion_tokens"],
                    },
                    cost_details={"total": event["estimated_cost_usd"]},
                )
            else:
                span = parent.span(**kwargs)
                if event["kind"] == "node":
                    nodes[event["name"]] = span

    def flush(self) -> None:
        """Flush only on the background worker (SDK network timeout is one second)."""
        self.client.flush()


def make_tracer(settings: Settings, sink: Tracer | None = None) -> BackgroundTracer | NullTracer:
    """Missing keys always mean no-op; an explicitly injected test sink is allowed."""
    if sink is not None:
        return BackgroundTracer(lambda: sink)
    if not (
        settings.tracing_enabled
        and settings.langfuse_public_key
        and settings.langfuse_secret_key.get_secret_value()
    ):
        return NullTracer()
    return BackgroundTracer(lambda: LangfuseSink(settings))
