"""Optional real Azure OpenAI providers; never instantiated by offline tests."""

from openai import AzureOpenAI

from kagent.llm.base import ChatResult, EmbeddingResult
from kagent.llm.retry import rate_limit_retry


class AzureChatModel:
    """Azure deployment-backed chat."""

    def __init__(self, client: AzureOpenAI, deployment: str) -> None:
        self.client = client
        self.deployment = deployment

    def invoke(self, system: str, user: str) -> str:
        """Generate a response with deterministic temperature."""
        return self.invoke_with_usage(system, user).text

    def invoke_with_usage(
        self, system: str, user: str, *, max_tokens: int | None = None
    ) -> ChatResult:
        """Return API token usage, preserving the original invoke interface."""
        options = {"max_tokens": max_tokens} if max_tokens is not None else {}
        response = rate_limit_retry(
            lambda: self.client.chat.completions.create(
                model=self.deployment,
                temperature=0,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                **options,
            )
        )
        usage = response.usage
        return ChatResult(
            response.choices[0].message.content or "",
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
            response.model,
        )


class AzureEmbedder:
    """Azure embeddings, using the deployment's observed vector dimension."""

    def __init__(self, client: AzureOpenAI, deployment: str) -> None:
        self.client = client
        self.deployment = deployment
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        """Return the deployment's vector dimension."""
        if self._dim is None:
            self.embed(["dimension probe"])
        assert self._dim is not None
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return vectors in input order."""
        return self.embed_with_usage(texts).vectors

    def embed_with_usage(self, texts: list[str]) -> EmbeddingResult:
        """Return vectors with API-reported input token usage."""
        if not texts:
            return EmbeddingResult([], 0, self.deployment)
        response = rate_limit_retry(
            lambda: self.client.embeddings.create(model=self.deployment, input=texts)
        )
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        self._dim = len(vectors[0])
        return EmbeddingResult(vectors, response.usage.prompt_tokens, response.model)
