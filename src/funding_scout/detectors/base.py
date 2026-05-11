"""Setup — каноническое представление одной связки на выходе детекторов.

Парадигма: показываем всё (transparent risk disclosure). Никаких filter-by-risk
ни на стороне детектора, ни на стороне UI. Detector эмитит ВСЕ кандидаты,
у которых данные структурно валидны (есть обе ноги, цены > 0, ставки парсятся).
Пользователь сам решает что показывать через saved-filter-profiles в UI.
"""

from __future__ import annotations

from dataclasses import dataclass


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

    # Risk-метрики (β, σ, ADL, sim-flash-crash) появятся в v0.2 когда накопится история.
    # Сейчас в Setup их нет — добавим без breaking changes (frozen dataclass с default'ами).
