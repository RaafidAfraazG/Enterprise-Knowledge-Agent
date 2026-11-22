"""Application configuration; secrets are read only from the environment."""

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings with an offline default."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    llm_provider: Literal["fake", "azure"] = "fake"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = SecretStr("")
    azure_openai_chat_deployment: str = ""
    azure_openai_embed_deployment: str = ""
    azure_openai_api_version: str = "2024-02-01"
    qdrant_url: str = ""
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_collection: str = "company_docs"
    sqlite_path: str = "data/company.db"
    profile: Literal["baseline", "optimized"] = "baseline"
    opt_router_model: bool | None = None
    opt_cache: bool | None = None
    opt_parallel: bool | None = None
    opt_top_k: bool | None = None
    opt_short_prompts: bool | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)
    cache_ttl_seconds: float = Field(default=900, gt=0)
    cache_max_size: int = Field(default=1000, gt=0)
    azure_openai_router_deployment: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "https://cloud.langfuse.com"
    tracing_enabled: bool | None = None
    price_chat_input_per_m: float | None = Field(default=None, ge=0)
    price_chat_output_per_m: float | None = Field(default=None, ge=0)
    price_embed_per_m: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def profile_defaults(self) -> "Settings":
        """Resolve profile defaults while respecting explicit env/constructor overrides."""
        for flag in (
            "opt_router_model",
            "opt_cache",
            "opt_parallel",
            "opt_top_k",
            "opt_short_prompts",
        ):
            if getattr(self, flag) is None:
                object.__setattr__(self, flag, self.profile == "optimized")
        if self.top_k is None:
            object.__setattr__(self, "top_k", 3 if self.opt_top_k else 4)
        if self.tracing_enabled is None:
            object.__setattr__(
                self,
                "tracing_enabled",
                bool(self.langfuse_public_key and self.langfuse_secret_key.get_secret_value()),
            )
        return self
