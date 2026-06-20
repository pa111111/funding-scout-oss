"""Сервис survival-оценки: история из БД → окна → Kaplan–Meier → SurvivalEstimate.

Импурный слой (БД + кэш). Чистая математика — в windows.py / estimator.py.

Поток (см. plan.md §3):
1. Один bulk-SQL за `history_days` дней по тикерам текущих setups → per-pair spread-серии.
2. `extract_windows` по каждой серии; left-truncated окна отбрасываем (v1 не моделирует).
3. KM по завершённым окнам пары. Если завершённых < `min_windows` → fallback на
   ГЛОБАЛЬНУЮ кривую (KM по окнам всех пар), помечаем `pooled=True`.
4. Из S(t) + текущего возраста окна → median_remaining / median_lifetime / p / curve.

Кэш по `latest_ts`: survival-кривая меняется только при смене снапшота, поэтому считаем
один раз на снапшот, и UI, и API читают из кэша. Polling агента безопасен.

НЕ импортирует web.* — чтобы web/data.py мог импортировать сервис без цикла.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..detectors.base import Setup
from ..ev.base import HOURS_PER_YEAR
from ..storage.models import FundingSnapshot
from .estimator import (
    conditional_survival,
    kaplan_meier,
    median_lifetime,
    median_residual_life,
)
from .windows import Window, extract_windows

# Дефолты из settings (override-able через env). Дублируем как модульные имена
# для удобства импорта и параметров функций.
SURVIVAL_HISTORY_DAYS = settings.survival_history_days
SURVIVAL_THRESHOLD_PCT = settings.survival_window_threshold_pct
SURVIVAL_MIN_WINDOWS = settings.survival_min_windows

# Горизонт условной кривой P(дожить ещё k часов), k = 1..CURVE_HORIZON_HOURS.
CURVE_HORIZON_HOURS = 24

# confidence-пороги по числу ЗАВЕРШЁННЫХ (наблюдённых до конца) окон пары.
CONFIDENCE_HIGH_MIN = 12

PairKey = tuple[str, str, str]  # (ticker, long_venue, short_venue)


@dataclass(frozen=True)
class SurvivalEstimate:
    """Survival-оценка для одной связки. Времена — часы; вероятности — decimal [0,1]."""

    current_age_h: int
    median_total_lifetime_h: float | None
    median_remaining_h: float | None
    p_survive_min_hold: float | None
    curve: list[tuple[int, float]]   # [(k, P(дожить ≥ age+k | дожить age))], k=1..horizon
    sample_size: int                 # число ЗАВЕРШЁННЫХ окон в выборке (наблюдённых смертей)
    pooled: bool
    confidence: str                  # "high" | "medium" | "low" | "none"


# Кэш по latest_ts. Держим только последний снапшот.
_cache: dict[int, dict[PairKey, SurvivalEstimate]] = {}


def reset_survival_cache() -> None:
    """Сбросить кэш. Для тестов / форс-пересчёта."""
    _cache.clear()


def _pair_key(s: Setup) -> PairKey:
    return (s.ticker, s.long_venue, s.short_venue)


def _load_spread_series(
    session: Session,
    setups: list[Setup],
    latest_ts: int,
    history_days: int,
) -> dict[PairKey, list[tuple[int, float | None]]]:
    """Per-pair серия `(ts, spread_apr_pct | None)` за history_days дней.

    Близнец web.data.compute_spread_history, но с привязкой ts и более широким окном.
    spread = (rate_short − rate_long) × 8760 × 100. None если на ts отсутствует нога.
    """
    min_ts = latest_ts - history_days * 86400

    timestamps = [
        row[0]
        for row in session.execute(
            select(FundingSnapshot.ts)
            .where(FundingSnapshot.ts >= min_ts, FundingSnapshot.ts <= latest_ts)
            .distinct()
            .order_by(FundingSnapshot.ts)
        ).all()
    ]
    keys = {_pair_key(s) for s in setups}
    if not timestamps:
        return {k: [] for k in keys}

    tickers = {s.ticker for s in setups}
    rate_rows = session.execute(
        select(
            FundingSnapshot.ts,
            FundingSnapshot.venue,
            FundingSnapshot.ticker,
            FundingSnapshot.funding_rate_1h,
        ).where(
            FundingSnapshot.ts >= min_ts,
            FundingSnapshot.ts <= latest_ts,
            FundingSnapshot.ticker.in_(tickers),
        )
    ).all()
    rates: dict[tuple[int, str, str], float] = {
        (ts, venue, ticker): rate for ts, venue, ticker, rate in rate_rows
    }

    series_by_pair: dict[PairKey, list[tuple[int, float | None]]] = {}
    for s in setups:
        series: list[tuple[int, float | None]] = []
        for ts in timestamps:
            long_rate = rates.get((ts, s.long_venue, s.ticker))
            short_rate = rates.get((ts, s.short_venue, s.ticker))
            if long_rate is None or short_rate is None:
                series.append((ts, None))
            else:
                series.append((ts, (short_rate - long_rate) * HOURS_PER_YEAR * 100.0))
        series_by_pair[_pair_key(s)] = series
    return series_by_pair


def _completed_windows(windows: list[Window]) -> list[Window]:
    """Окна, пригодные для KM: отбрасываем left-truncated (не видели старта)."""
    return [w for w in windows if not w.left_truncated]


def _current_age(windows: list[Window]) -> int:
    """Возраст текущего открытого окна = duration последнего окна, если оно
    упирается в правый край (censored). Иначе 0 (сейчас окна нет).

    Это ИСТИННЫЙ возраст (может превышать 24h, в отличие от capped Age h в UI)."""
    if windows and windows[-1].censored:
        return windows[-1].duration_h
    return 0


def _confidence(sample_size: int, pooled: bool) -> str:
    # Не pooled → sample уже ≥ min_windows (ветка пары), поэтому порог тут не нужен.
    if sample_size == 0:
        return "none"
    if pooled:
        return "low"
    if sample_size >= CONFIDENCE_HIGH_MIN:
        return "high"
    return "medium"


def _build_estimate(
    pair_windows: list[Window],
    pooled_curve: dict[int, float],
    pooled_sample: int,
    min_profitable_hours: float,
    min_windows: int,
) -> SurvivalEstimate:
    """Собрать SurvivalEstimate для пары из её окон + готовой глобальной кривой."""
    completed = _completed_windows(pair_windows)
    durations = [w.duration_h for w in completed]
    censored = [w.censored for w in completed]
    # sample_size = число наблюдённых до конца окон (смертей), не censored.
    pair_sample = sum(1 for w in completed if not w.censored)

    age = _current_age(pair_windows)

    if pair_sample >= min_windows:
        survival = kaplan_meier(durations, censored)
        sample_size = pair_sample
        pooled = False
    else:
        survival = pooled_curve
        sample_size = pooled_sample
        pooled = True

    confidence = _confidence(sample_size, pooled)

    # Нет данных вовсе → пустая оценка.
    if not survival or sample_size == 0:
        return SurvivalEstimate(
            current_age_h=age,
            median_total_lifetime_h=None,
            median_remaining_h=None,
            p_survive_min_hold=None,
            curve=[],
            sample_size=0,
            pooled=pooled,
            confidence="none",
        )

    median_life = median_lifetime(survival)

    # Остаточная жизнь / кривая / p — только при наличии активного окна (age ≥ 1).
    if age >= 1:
        median_remaining = median_residual_life(survival, age)
        curve = []
        for k in range(1, CURVE_HORIZON_HOURS + 1):
            p = conditional_survival(survival, age, k)
            if p is not None:
                curve.append((k, p))
        if math.isfinite(min_profitable_hours):
            hold = max(1, math.ceil(min_profitable_hours))
            p_min_hold = conditional_survival(survival, age, hold)
        else:
            p_min_hold = None
    else:
        median_remaining = None
        curve = []
        p_min_hold = None

    return SurvivalEstimate(
        current_age_h=age,
        median_total_lifetime_h=median_life,
        median_remaining_h=median_remaining,
        p_survive_min_hold=p_min_hold,
        curve=curve,
        sample_size=sample_size,
        pooled=pooled,
        confidence=confidence,
    )


def compute_survival_for_setups(
    session: Session,
    setups: list[Setup],
    latest_ts: int,
    *,
    history_days: int = SURVIVAL_HISTORY_DAYS,
    threshold: float = SURVIVAL_THRESHOLD_PCT,
    min_windows: int = SURVIVAL_MIN_WINDOWS,
    use_cache: bool = True,
) -> dict[PairKey, SurvivalEstimate]:
    """Survival-оценка по каждой связке из setups. Ключ — (ticker, long, short).

    Кэшируется по latest_ts (use_cache=False для тестов / форс-пересчёта).
    """
    if use_cache and latest_ts in _cache:
        return _cache[latest_ts]

    series_by_pair = _load_spread_series(session, setups, latest_ts, history_days)

    # Окна по каждой паре + глобальный пул для fallback.
    windows_by_pair: dict[PairKey, list[Window]] = {
        key: extract_windows(series, threshold) for key, series in series_by_pair.items()
    }
    pool_completed: list[Window] = []
    for wins in windows_by_pair.values():
        pool_completed.extend(_completed_windows(wins))
    pooled_curve = kaplan_meier(
        [w.duration_h for w in pool_completed],
        [w.censored for w in pool_completed],
    )
    pooled_sample = sum(1 for w in pool_completed if not w.censored)

    result: dict[PairKey, SurvivalEstimate] = {}
    for s in setups:
        key = _pair_key(s)
        result[key] = _build_estimate(
            pair_windows=windows_by_pair.get(key, []),
            pooled_curve=pooled_curve,
            pooled_sample=pooled_sample,
            min_profitable_hours=s.min_profitable_hours,
            min_windows=min_windows,
        )

    if use_cache:
        _cache.clear()
        _cache[latest_ts] = result
    return result
