from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for Tweaker.

    All application settings use the ``TWEAKER_`` prefix. Standard provider
    variables (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ...) are also accepted
    so a deployment can reuse credentials it already has.
    """

    model_config = SettingsConfigDict(
        env_prefix="TWEAKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Tweaker"
    version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    public_url: str = "http://localhost:8080"
    max_request_bytes: int = 50_000_000

    # Storage
    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/tweaker.db"
    static_dir: Path = Path("./web/dist")
    secret_key: str | None = None

    # Access control. When unset, the API is open (intended for local/demo use).
    auth_token: str | None = None
    cors_origins: str = "*"

    # Background execution
    runner: str = "inline"  # inline | arq
    redis_url: str = "redis://localhost:6379/0"
    poll_interval_seconds: int = 120
    max_run_seconds: int = 600

    # Demo mode seeds a realistic organization, issues, and local repository.
    demo_mode: bool = False
    demo_repo_path: Path = Path("./examples/demo-repo")

    # Sentry
    sentry_request_timeout: float = 20.0
    sentry_verify_ssl: bool = True
    sentry_events_per_issue: int = 8
    sentry_page_size: int = 25

    # Drop-in Seer service contract. Point Sentry's SEER_AUTOFIX_URL at this
    # service and set the same shared secrets on both sides.
    sentry_url: str = Field(
        default="http://sentry:9000",
        validation_alias=AliasChoices("TWEAKER_SENTRY_URL", "SENTRY_URL"),
    )
    seer_api_shared_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SEER_API_SHARED_SECRET", "TWEAKER_SEER_API_SHARED_SECRET"
        ),
    )
    seer_rpc_shared_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SEER_RPC_SHARED_SECRET", "TWEAKER_SEER_RPC_SHARED_SECRET"
        ),
    )
    seer_allow_unsigned: bool = False
    seer_rate_limit_per_minute: int = 600
    seer_request_timeout: float = 30.0
    seer_repos_dir: Path = Path("./data/seer-repos")
    github_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TWEAKER_GITHUB_TOKEN", "GITHUB_TOKEN"),
    )
    github_enterprise_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TWEAKER_GITHUB_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"
        ),
    )
    gitlab_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TWEAKER_GITLAB_TOKEN", "GITLAB_TOKEN"),
    )
    gitlab_self_hosted_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TWEAKER_GITLAB_SELF_HOSTED_TOKEN", "GITLAB_SELF_HOSTED_TOKEN"
        ),
    )
    github_base_url: str = Field(
        default="https://github.com",
        validation_alias=AliasChoices(
            "TWEAKER_GITHUB_BASE_URL",
            "GITHUB_BASE_URL",
            "TWEAKER_GITHUB_ENTERPRISE_URL",
            "GITHUB_ENTERPRISE_URL",
        ),
    )
    gitlab_base_url: str = Field(
        default="https://gitlab.com",
        validation_alias=AliasChoices(
            "TWEAKER_GITLAB_BASE_URL",
            "GITLAB_BASE_URL",
            "TWEAKER_GITLAB_SELF_HOSTED_URL",
            "GITLAB_SELF_HOSTED_URL",
            "GITLAB_URL",
        ),
    )
    # JSON object mapping "owner/name" to an existing checkout path.
    repo_map: str = "{}"

    # AI provider. ``auto`` uses Anthropic when a key is available, then an
    # OpenAI-compatible endpoint, then the deterministic local analyzer.
    ai_provider: str = "auto"
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "TWEAKER_ANTHROPIC_API_KEY"),
    )
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "high"
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "TWEAKER_OPENAI_API_KEY"),
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("OPENAI_BASE_URL", "TWEAKER_OPENAI_BASE_URL"),
    )
    openai_model: str = Field(
        default="gpt-4.1",
        validation_alias=AliasChoices("OPENAI_MODEL", "TWEAKER_OPENAI_MODEL"),
    )
    agent_tool_loop: bool = True
    max_context_chars: int = 60_000
    max_patch_chars: int = 80_000
    max_event_chars: int = 40_000

    # Behaviour
    default_fixability_threshold: int = 70
    enable_pr_publishing: bool = True
    allow_local_repositories: bool = True
    run_tests_on_fix: bool = False

    # Slack
    slack_signing_secret: str | None = None
    slack_bot_token: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def resolved_ai_provider(self) -> str:
        provider = self.ai_provider.lower().strip()
        if provider == "auto":
            if self.anthropic_api_key:
                return "anthropic"
            if self.openai_api_key or "localhost" in self.openai_base_url or "127.0.0.1" in self.openai_base_url:
                return "openai"
            return "demo"
        if provider in {"anthropic", "openai", "demo", "none"}:
            return provider
        raise ValueError(f"Unknown AI provider: {self.ai_provider}")

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "repos").mkdir(parents=True, exist_ok=True)
        self.seer_repos_dir.mkdir(parents=True, exist_ok=True)
        if self.is_sqlite:
            # Make the parent of the sqlite file exist.
            raw = self.database_url.removeprefix("sqlite:///")
            if raw and raw != ":memory:":
                Path(raw).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def secret_material(self) -> bytes:
        """Return the key material used to encrypt integration credentials."""
        if self.secret_key:
            return self.secret_key.encode("utf-8")
        # A generated key is persisted with 0600 permissions. Losing it makes
        # stored integration tokens unreadable; production deployments should
        # set TWEAKER_SECRET_KEY explicitly.
        self.ensure_directories()
        key_path = self.data_dir / "secret.key"
        if key_path.exists():
            return key_path.read_bytes().strip()
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        key_path.write_bytes(key)
        os.chmod(key_path, 0o600)
        return key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
