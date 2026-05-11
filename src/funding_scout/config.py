"""Env-driven application config. Loaded once at import time."""

from __future__ import annotations

import logging
from pathlib import Path

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All configuration. Override via environment variables or `.env` file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Storage. SQLite local by default; on VPS set DATABASE_URL=postgresql+psycopg://...
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'funding-scout.db').as_posix()}"

    # Logging
    log_level: str = "INFO"

    # Connector endpoints (overridable for testing / mocking)
    hyperliquid_api: str = "https://api.hyperliquid.xyz"
    lighter_api: str = "https://mainnet.zklighter.elliot.ai"
    pacifica_api: str = "https://api.pacifica.fi"
    edgex_api: str = "https://pro.edgex.exchange"

    # HTTP client
    http_timeout_seconds: float = 15.0


settings = Settings()


def configure_logging() -> None:
    """Idempotent structlog setup. Call once from entry points."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
