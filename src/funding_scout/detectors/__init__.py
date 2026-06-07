"""Детекторы связок. `detect_setups()` — единый источник истины «какие связки есть».

И Dash-UI (`web/data.py`), и persist (`snapshot/runner.py`), и JSON-API зовут
этот один список детекторов — чтобы вычисленный набор связок не разъехался между
витриной и историей (ключевой принцип концепта Hermes §4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Setup, make_candidate_id
from .cross_dex_same_ticker import CrossDexSameTickerDetector

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Канонический набор детекторов. Добавляешь новый тип связки — здесь, и он сразу
# виден и в UI, и в JSON-API, и в персисте setup_snapshot.
ALL_DETECTORS = (CrossDexSameTickerDetector(),)


def detect_setups(session: Session, ts: int) -> list[Setup]:
    """Все связки на снапшоте `ts` от всех детекторов. Единая точка расчёта."""
    setups: list[Setup] = []
    for detector in ALL_DETECTORS:
        setups.extend(detector.detect_for_snapshot(session, ts))
    return setups


__all__ = [
    "ALL_DETECTORS",
    "CrossDexSameTickerDetector",
    "Setup",
    "detect_setups",
    "make_candidate_id",
]
