"""Usage-aware wrappers compatible with legacy string/list-returning providers."""

import time

from kagent.cache import TTLCache
from kagent.config import Settings
from kagent.llm.base import ChatModel, ChatResult, Embedder, EmbeddingResult
from kagent.observability.metrics import record
from kagent.observability.pricing import estimate


class ObservedChat:
    """Record provider usage while preserving invoke's string return type."""

    def __init__(
        self, model: ChatModel, settings: Settings, *, max_tokens: int | None = None
    ) -> None:
        self.model = model
        self.settings = settings
        self.max_tokens = max_tokens

    def invoke(self, system: str, user: str) -> str:
        """Invoke once and account for actual API usage or deterministic fake usage."""
        start = time.perf_counter()
        result = ChatResult("", 0, 0, getattr(self.model, "deployment", "unknown"))
        try:
            if method := getattr(self.model, "invoke_with_usage", None):
                cap = (
                    300
                    if self.settings.opt_short_prompts and system.startswith("ANSWER_")
                    else self.max_tokens
                )
                result = method(system, user, max_tokens=cap)
            else:
                result = ChatResult(self.model.invoke(system, user), 0, 0, "unknown")
            return result.text
        finally:
            record(
                system.split(":", 1)[0].lower(),
                "llm",
                start,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                estimated_cost_usd=estimate(
                    result.model, result.prompt_tokens, result.completion_tokens, self.settings
                ),
                model=result.model,
                deployment=getattr(self.model, "deployment", result.model),
                input={"system": system, "user": user},
                output=result.text,
            )


class ObservedEmbedder:
    """Record embeddings and optionally memoize query vectors per graph."""

    def __init__(self, embedder: Embedder, settings: Settings) -> None:
        self.embedder = embedder
        self.settings = settings
        self.cache = TTLCache(settings.cache_ttl_seconds, 1000)

    @property
    def dim(self) -> int:
        """Forward the provider's dimension."""
        return self.embedder.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed with optional TTL/LRU reuse; cache hits consume no provider tokens."""
        start = time.perf_counter()
        key = tuple(texts)
        cached = self.cache.get(key) if self.settings.opt_cache else None
        if cached is not None:
            record(
                "query_embeddings",
                "embedding",
                start,
                cache_hit=True,
                model=getattr(self.embedder, "deployment", "fake"),
                input=texts,
            )
            return cached
        result = EmbeddingResult([], 0, getattr(self.embedder, "deployment", "unknown"))
        try:
            if method := getattr(self.embedder, "embed_with_usage", None):
                result = method(texts)
            else:
                result = EmbeddingResult(self.embedder.embed(texts), 0, "unknown")
            if self.settings.opt_cache:
                self.cache.put(key, result.vectors)
            return result.vectors
        finally:
            record(
                "query_embeddings",
                "embedding",
                start,
                prompt_tokens=result.prompt_tokens,
                estimated_cost_usd=estimate(
                    result.model, result.prompt_tokens, 0, self.settings, embedding=True
                ),
                model=result.model,
                deployment=getattr(self.embedder, "deployment", result.model),
                input=texts,
                output={"vectors": len(result.vectors)},
            )
