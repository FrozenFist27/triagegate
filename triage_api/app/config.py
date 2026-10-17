"""Runtime settings loaded from environment / .env (never logged)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_provider: str = "anthropic"
    llm_model: str = "claude-haiku-5-5"
    anthropic_api_key: str = ""
    llm_max_attempts: int = 3
    llm_fault_injection: str = "off"  # off | first_attempt_invalid | always_invalid
    llm_price_in_per_mtok: float = 0.10
    llm_price_out_per_mtok: float = 0.50

    # Internal auth
    triage_api_token: str = "dev-token"
    n8n_ingest_token: str = "dev-ingest-token"

    # Approvals / storage
    approval_ttl_minutes: int = 1440
    db_path: str = "data/triagegate.db"

    # n8n
    n8n_public_url: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_approval_channel_id: str = ""

    # HubSpot
    hubspot_service_key: str = ""

    # IMAP (read-only; live demo)
    imap_host: str = "imap.gmail.com"
    imap_user: str = ""
    imap_app_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def resolve_db_path(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    p = Path(s.db_path)
    if not p.is_absolute():
        # container working dir is /app; local dev is project root
        p = Path.cwd() / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
