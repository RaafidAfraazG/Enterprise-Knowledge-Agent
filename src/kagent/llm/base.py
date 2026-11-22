"""Minimal provider contracts for dependency injection."""

from dataclasses import dataclass
from typing import Protocol


class ChatModel(Protocol):
    """A synchronous chat model."""

    def invoke(self, system: str, user: str) -> str:
        """Return a response to a system and user prompt."""
        ...


class Embedder(Protocol):
    """A batch text embedder with a known vector dimension."""

    @property
    def dim(self) -> int:
        """Return vector dimensionality."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in input order."""
        ...


@dataclass(frozen=True)
class ChatResult:
    """Optional usage-aware result; legacy invoke still returns a string."""

    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str


@dataclass(frozen=True)
class EmbeddingResult:
    """Optional usage-aware embedding result; legacy embed still returns vectors."""

    vectors: list[list[float]]
    prompt_tokens: int
    model: str


class UsageChatModel(ChatModel, Protocol):
    """Optional extension; old providers need only implement invoke."""

    def invoke_with_usage(
        self, system: str, user: str, *, max_tokens: int | None = None
    ) -> ChatResult: ...


class UsageEmbedder(Embedder, Protocol):
    """Optional extension for actual embedding token accounting."""

    def embed_with_usage(self, texts: list[str]) -> EmbeddingResult: ...
