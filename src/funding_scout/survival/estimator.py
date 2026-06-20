"""Kaplan–Meier оценка функции выживания окна + производные метрики.

Чистая арифметика, без зависимостей (numpy/scipy не нужны). Все времена — целые часы.

Конвенция функции выживания
---------------------------
`kaplan_meier` возвращает dict `S`, где **S[h] = P(T ≥ h)** — вероятность, что окно
проживёт НЕ МЕНЬШЕ h часов. S[0] = S[1] = 1.0 (окно живёт ≥ 1 часа по построению),
функция невозрастающая. Это «P(T ≥ h)»-конвенция (а не текстбучная P(T > t)) —
выбрана потому, что наши вопросы формулируются как «доживёт ли ещё k часов»
(P(T ≥ age+k | T ≥ age)). Расхождение с текстбуком — ровно 1 час сдвига, для
эвристики на 45-дневной истории с флагом confidence несущественно. Документируем.

Right-censoring учитывается корректно (продукт-лимит по риск-множествам):
censored-наблюдение остаётся в риск-множестве до своего времени, но не даёт «смерти».
Left-truncated окна должен отфильтровать вызывающий ДО передачи сюда (см. windows.py).

Формула: S(h) = ∏_{death-time t_i < h} (1 − d_i / n_i),
где n_i = #{наблюдений с duration ≥ t_i}, d_i = #{смертей ровно в t_i}.
"""

from __future__ import annotations

MEDIAN_SURVIVAL = 0.5


def kaplan_meier(
    durations: list[int],
    censored: list[bool],
) -> dict[int, float]:
    """KM-оценка S[h] = P(T ≥ h) для h в 0..max_duration+1.

    durations — длительности окон в часах (≥ 1). censored[i] — True если окно i
    right-censored (ещё открыто). Списки одной длины.

    Пустой ввод → {0: 1.0} (нет данных = тривиальная единичная кривая).
    """
    if not durations:
        return {0: 1.0}
    if len(durations) != len(censored):
        raise ValueError("durations и censored должны быть одной длины")

    max_d = max(durations)
    death_times = sorted({d for d, c in zip(durations, censored, strict=True) if not c})

    # Множитель (1 - d_i/n_i) для каждого времени смерти.
    factor: dict[int, float] = {}
    for t in death_times:
        at_risk = sum(1 for d in durations if d >= t)
        deaths = sum(1 for d, c in zip(durations, censored, strict=True) if d == t and not c)
        factor[t] = (1.0 - deaths / at_risk) if at_risk > 0 else 1.0

    # S(h) = ∏_{t_i < h} factor[t_i]. Накапливаем по возрастанию h.
    survival: dict[int, float] = {}
    cur = 1.0
    ti = 0
    for h in range(0, max_d + 2):  # +2 чтобы S мог дойти до 0 после последней смерти
        while ti < len(death_times) and death_times[ti] < h:
            cur *= factor[death_times[ti]]
            ti += 1
        survival[h] = cur
    return survival


def survival_at(survival: dict[int, float], t: int) -> float:
    """P(T ≥ t). Кламп: t ≤ 0 → 1.0; t за пределами таблицы → хвостовое значение."""
    if t <= 0:
        return 1.0
    if not survival:
        return 1.0
    max_h = max(survival)
    if t > max_h:
        return survival[max_h]
    return survival[t]


def conditional_survival(survival: dict[int, float], age: int, k: int) -> float | None:
    """P(T ≥ age+k | T ≥ age) = S(age+k) / S(age).

    None если базовая вероятность S(age) == 0 (условие невозможно — окно по оценке
    уже не должно было дожить до age).
    """
    base = survival_at(survival, age)
    if base <= 0.0:
        return None
    return survival_at(survival, age + k) / base


def median_residual_life(survival: dict[int, float], age: int) -> float | None:
    """Медианная ОСТАТОЧНАЯ жизнь: наименьшее k ≥ 1, при котором
    P(T ≥ age+k | T ≥ age) ≤ 0.5.

    None если:
    - S(age) == 0 (см. conditional_survival), либо
    - условная вероятность не падает до 0.5 в пределах наблюдённого горизонта
      (окно живёт дольше, чем мы можем оценить — честное «не знаю», UI покажет —).
    """
    base = survival_at(survival, age)
    if base <= 0.0:
        return None
    max_h = max(survival)
    horizon = max_h - age
    if horizon < 1:
        return None
    for k in range(1, horizon + 1):
        if survival_at(survival, age + k) / base <= MEDIAN_SURVIVAL:
            return float(k)
    return None


def median_lifetime(survival: dict[int, float]) -> float | None:
    """Медианная ПОЛНАЯ длительность окна: наименьшее t, при котором S(t) ≤ 0.5.

    None если кривая не опускается до 0.5 в пределах горизонта (тяжёлое censoring —
    больше половины окон ещё не закрылись).
    """
    for t in sorted(survival):
        if survival[t] <= MEDIAN_SURVIVAL:
            return float(t)
    return None
