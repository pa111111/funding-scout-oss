"""Извлечение «окон» из истории спреда связки.

Окно = максимальный непрерывный ран часов, где spread_apr_pct ≥ threshold.
Семантика порога та же, что у `window_age_hours` в web/data.py
(`count_consecutive_hours_above_threshold`): дыра в данных (None — пропущенный
snapshot или отсутствие одной ноги) ОБРЫВАЕТ окно. Это консервативно занижает
длительность (длинное окно с дырой посередине считается как два коротких), но
консистентно с уже показываемой колонкой Age h. Документируем как ограничение v1.

Censoring (для корректного Kaplan–Meier на стороне estimator):
- `censored=True` — окно тянется до ПРАВОГО края серии → ещё не закончилось
  (right-censored, его длительность — нижняя граница истинной жизни).
- `left_truncated=True` — окно начинается на ЛЕВОМ крае серии → было уже открыто,
  когда наши данные начались (мы не видели старта). Вызывающий (survival/service.py)
  ИСКЛЮЧАЕТ такие окна из выборки KM (v1 не моделирует left-truncation).

Чистый модуль: list → list, без БД и сети.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_THRESHOLD_PCT = 30.0


@dataclass(frozen=True)
class Window:
    """Одно окно жизни связки над порогом.

    duration_h — число часовых наблюдений в окне (включая первый и последний час).
    Для непрерывного hourly-рана из k точек duration_h == k.
    """

    start_ts: int
    end_ts: int
    duration_h: int
    censored: bool          # right-censored: тянется до правого края серии (ещё открыто)
    left_truncated: bool    # начинается на левом крае серии (старт не наблюдали)


def extract_windows(
    series: list[tuple[int, float | None]],
    threshold: float = DEFAULT_THRESHOLD_PCT,
) -> list[Window]:
    """Извлечь все окна `spread ≥ threshold` из серии.

    series — список `(ts, spread_apr_pct | None)`, ОТСОРТИРОВАННЫЙ по возрастанию ts.
    Каждый элемент = один час. `None` обрывает текущее окно (см. модульный docstring).

    Возвращает окна в порядке появления. Окно, упирающееся в правый край серии,
    помечается `censored=True`; упирающееся в левый край — `left_truncated=True`.
    Окно во всю серию (всё над порогом) получит оба флага.

    Пустая серия → [].
    """
    n = len(series)
    windows: list[Window] = []
    run_start: int | None = None  # индекс начала текущего рана

    for i, (_ts, val) in enumerate(series):
        above = val is not None and val >= threshold
        if above and run_start is None:
            run_start = i
        elif not above and run_start is not None:
            windows.append(_make_window(series, run_start, i - 1, n))
            run_start = None

    if run_start is not None:
        windows.append(_make_window(series, run_start, n - 1, n))

    return windows


def _make_window(
    series: list[tuple[int, float | None]],
    i: int,
    j: int,
    n: int,
) -> Window:
    """Собрать Window из ран-индексов [i, j] (включительно). n — длина серии."""
    return Window(
        start_ts=series[i][0],
        end_ts=series[j][0],
        duration_h=j - i + 1,
        censored=(j == n - 1),
        left_truncated=(i == 0),
    )
