"""Construct providers only when requested."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import AzureOpenAI

from typing import TYPE_CHECKING

from kagent.config import Settings
from kagent.llm.base import ChatModel, Embedder
from kagent.llm.fakes import FakeEmbeddings, FakeLLM


def _azure_client(settings: Settings) -> "AzureOpenAI":
    from openai import AzureOpenAI

    if not all(
        (
            settings.azure_openai_endpoint,
            settings.azure_openai_api_key.get_secret_value(),
            settings.azure_openai_chat_deployment,
            settings.azure_openai_embed_deployment,
        )
    ):
        raise ValueError("Azure provider requires endpoint, key, and both deployments")
    return AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        max_retries=0,
    )


def get_chat_model(settings: Settings | None = None) -> ChatModel:
    """Select fake chat by default, otherwise Azure."""
    settings = settings or Settings()
    if settings.llm_provider == "fake":
        return FakeLLM()
    from kagent.llm.azure import AzureChatModel

    return AzureChatModel(_azure_client(settings), settings.azure_openai_chat_deployment)


def get_embedder(settings: Settings | None = None) -> Embedder:
    """Select fake embeddings by default, otherwise Azure."""
    settings = settings or Settings()
    if settings.llm_provider == "fake":
        return FakeEmbeddings()
    from kagent.llm.azure import AzureEmbedder

    return AzureEmbedder(_azure_client(settings), settings.azure_openai_embed_deployment)


def get_router_model(settings: Settings) -> ChatModel | None:
    """Build a separate router only for an explicitly configured deployment."""
    if not settings.opt_router_model or not settings.azure_openai_router_deployment:
        return None
    if settings.llm_provider == "fake":
        return FakeLLM()
    from kagent.llm.azure import AzureChatModel

    return AzureChatModel(_azure_client(settings), settings.azure_openai_router_deployment)
