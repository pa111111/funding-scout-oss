"""Унифицированный интерфейс DEX-коннекторов.

Каждый коннектор реализует `fetch_snapshot()` → список `FundingTick` для всех перпов.
`raw` — обязательное поле, мы храним сырой ответ биржи в БД для будущего бэкфила.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class FundingTick:
    """Текущий снапшот по одному инструменту на одной бирже."""

    venue: str
    ticker: str
    funding_rate_1h: float  # decimal, не проценты (0.0001 = 0.01%/h)
    mark_price: float
    index_price: float | None = None
    oi_long: float | None = None  # not all venues split OI by side
    oi_short: float | None = None
    volume_24h: float | None = None  # 24h notional volume in USD
    raw: dict = field(default_factory=dict)

    @property
    def funding_apr(self) -> float:
        """Annualized funding (decimal). 0.10 = 10% APR."""
        return self.funding_rate_1h * 24 * 365


class Connector(ABC):
    """Базовый класс DEX-коннектора. venue — статический идентификатор биржи."""

    venue: str

    @abstractmethod
    async def fetch_snapshot(self) -> list[FundingTick]:
        """Снять снапшот по всем активным перпам биржи. Должно быть idempotent."""
        ...
