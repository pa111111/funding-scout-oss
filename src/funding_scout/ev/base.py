"""Базовая EV-арифметика для дельта-нейтральных связок.

Принципы:
- Чистые функции, без БД и сети. Тестируется напрямую цифрами.
- Все ставки funding_rate — hourly decimal (как мы их кладём в БД).
- APR считается × 24 × 365 (а не × 365×3 как для 8h-фандинга на CEX —
  у нас все DEX hourly).
- Base EV не штрафуется за риск (см. парадигму transparent risk disclosure
  в docs/product_concept.md). Risk-метрики живут отдельной осью в Setup'е.
"""

from __future__ import annotations

from dataclasses import dataclass

from .costs import round_trip_cost_pair

HOURS_PER_YEAR = 24 * 365


@dataclass(frozen=True)
class SetupEV:
    """Полный EV-расчёт для одной связки. Все цифры в decimal (0.10 = 10%).

    spread_apr — годовая премия carry (всегда ≥ 0 если связка корректно ориентирована).
    base_ev_per_dollar_per_day — что зарабатываем на $1 капитала за день в среднем,
                                 без учёта riskметрик.
    min_profitable_hours — сколько часов держать чтобы покрыть round-trip.
                           inf если spread = 0.
    """

    funding_long_apr: float
    funding_short_apr: float
    spread_apr: float
    base_ev_per_dollar_per_day: float
    round_trip_cost_pct: float
    min_profitable_hours: float


def compute_setup_ev(
    funding_rate_long_1h: float,
    funding_rate_short_1h: float,
    long_venue: str,
    short_venue: str,
) -> SetupEV:
    """Посчитать EV для cross-DEX связки.

    Параметры — hourly funding rates (decimal). Соглашение:
    - long_venue имеет МЕНЬШЕЕ (или более отрицательное) funding rate
      → за лонг там нам платят больше / отнимают меньше
    - short_venue имеет БОЛЬШЕЕ funding rate → за шорт там нам платят

    spread_apr вычисляется как (rate_short - rate_long) × HOURS_PER_YEAR.
    Должен быть ≥ 0 если стороны выбраны корректно (это контракт детектора).

    base_ev_per_dollar_per_day = spread_apr / 365.
    """
    funding_long_apr = funding_rate_long_1h * HOURS_PER_YEAR
    funding_short_apr = funding_rate_short_1h * HOURS_PER_YEAR
    spread_apr = funding_short_apr - funding_long_apr

    base_ev_per_dollar_per_day = spread_apr / 365

    rt_cost_pct = round_trip_cost_pair(long_venue, short_venue)

    # Min holding period в часах:
    # spread_apr — это годовая ставка (decimal), переводим в почасовую.
    # round-trip cost даём в процентах → переводим в decimal.
    rt_cost_decimal = rt_cost_pct / 100.0
    spread_per_hour = spread_apr / HOURS_PER_YEAR
    if spread_per_hour <= 0:
        min_profitable_hours = float("inf")
    else:
        min_profitable_hours = rt_cost_decimal / spread_per_hour

    return SetupEV(
        funding_long_apr=funding_long_apr,
        funding_short_apr=funding_short_apr,
        spread_apr=spread_apr,
        base_ev_per_dollar_per_day=base_ev_per_dollar_per_day,
        round_trip_cost_pct=rt_cost_pct,
        min_profitable_hours=min_profitable_hours,
    )
