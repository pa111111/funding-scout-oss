"""Shared pytest fixtures.

Важно: `os.environ["DATABASE_URL"]` ставится ДО импорта `funding_scout.*`,
иначе глобальный engine в `storage/db.py` уже создан с прод-URL.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# === BEFORE any funding_scout import ===
_tmpdir = Path(tempfile.mkdtemp(prefix="funding-scout-test-"))
_db_path = _tmpdir / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"

# === Now safe to import ===
import pytest  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from funding_scout.storage import Base, engine  # noqa: E402
from funding_scout.storage.models import FundingSnapshot, SetupSnapshot  # noqa: E402
from funding_scout.survival import reset_survival_cache  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Создаём схему один раз на сессию pytest."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_table():
    """Перед каждым тестом — пустые таблицы. Тесты не зависят друг от друга."""
    with engine.begin() as conn:
        conn.execute(delete(SetupSnapshot))
        conn.execute(delete(FundingSnapshot))
    reset_survival_cache()  # survival-кэш по latest_ts переживёт чистку БД — сбрасываем
    yield
