"""Setup — каноническое представление одной связки на выходе детекторов.

Парадигма: показываем всё (transparent risk disclosure). Никаких filter-by-risk
ни на стороне детектора, ни на стороне UI. Detector эмитит ВСЕ кандидаты,
у которых данные структурно валидны (есть обе ноги, цены > 0, ставки парсятся).
Пользователь сам решает что показывать через saved-filter-profiles в UI.
"""

from __future__ import annotations

from dataclasses import dataclass


def make_candidate_id(ticker: str, long_venue: str, short_venue: str) -> str:
    """Стабильный идентификатор связки: `TICKER:LONG_VENUE:SHORT_VENUE`.

    Детерминирован и стабилен между снапшотами — построен на том же натуральном
    ключе `(ticker, long_venue, short_venue)`, на котором уже матчатся Δ Spread и
    sparkline-история (см. `_spread_index`, `compute_spread_deltas` в web/data.py).
    Это даёт Hermes/боту ссылаться на «ту самую связку» во времени и сопоставлять
    её с реально открытой позицией. Если funding меняет направление и long/short
    меняются местами — это уже другая торговая связка, и id честно меняется.
    """
    return f"{ticker}:{long_venue}:{short_venue}"


@dataclass(frozen=True)
class Setup:
    """Связка для отображения. Заморожена для безопасной передачи между слоями."""

    # === Identity ===
    type: str  # "cross-dex-same-ticker", "single-venue-pair", ...
    ticker: str
    long_venue: str
    short_venue: str

    # === EV (см. ev/base.py для подробностей расчёта) ===
    spread_apr_pct: float            # годовая премия carry, %. Может быть 0 или отрицательной если
                                     # rate_short < rate_long (детектор всё равно эмитит — пусть видно).
    base_ev_per_dollar_per_day: float  # средний $/день на $1 капитала, без риск-штрафа
    long_funding_apr_pct: float
    short_funding_apr_pct: float
    round_trip_cost_pct: float       # суммарный round-trip обеих ног в %
    min_profitable_hours: float      # inf если spread <= 0

    # === Цены ===
    long_mark_price: float
    short_mark_price: float
    price_spread_pct: float          # (long_mark - short_mark) / short_mark × 100.
                                     # +ve = лонгуем дороже (cost convergence работает против нас).

    # === Capacity / liquidity (для информирования, не для фильтрации) ===
    min_volume_24h_usd: float | None  # min из двух venues. None если хоть один не отдал volume.
    long_volume_24h_usd: float | None
    short_volume_24h_usd: float | None

    # === Метаданные ===
    snapshot_ts: int                  # unix timestamp снапшота, по которому посчитано

    @property
    def candidate_id(self) -> str:
        """Стабильный id связки — единый ключ для UI, JSON-API и setup_snapshot."""
        return make_candidate_id(self.ticker, self.long_venue, self.short_venue)

    # Risk-метрики (β, σ, ADL, sim-flash-crash) появятся в v0.2 когда накопится история.
    # Сейчас в Setup их нет — добавим без breaking changes (frozen dataclass с default'ами).
