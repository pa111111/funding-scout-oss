"""Database engine + session factory. One process-wide engine."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..config import PROJECT_ROOT, settings


def _ensure_sqlite_dir(url: str) -> None:
    """SQLite-only: гарантируем что директория существует. Иначе SQLite ругнётся."""
    if not url.startswith("sqlite:///"):
        return
    path_part = url.removeprefix("sqlite:///")
    db_path = Path(path_part)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    # SQLite specific: pool_pre_ping не нужен; для Postgres бы понадобился.
    pool_pre_ping=not settings.database_url.startswith("sqlite"),
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def init_db() -> None:
    """Create all tables. Idempotent. Use Alembic later for proper migrations."""
    from .models import Base

    Base.metadata.create_all(bind=engine)
