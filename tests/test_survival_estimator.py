"""Unit-тесты Kaplan–Meier и производных. Чистая арифметика — без БД."""

from __future__ import annotations

import pytest

from funding_scout.survival.estimator import (
    conditional_survival,
    kaplan_meier,
    median_lifetime,
    median_residual_life,
    survival_at,
)

# === kaplan_meier ===


def test_empty_input_trivial_curve():
    assert kaplan_meier([], []) == {0: 1.0}


def test_mismatched_lengths_raises():
    with pytest.raises(ValueError):
        kaplan_meier([1, 2], [False])


def test_all_deaths_no_censoring():
    """durations=[2,2,5], все смерти.
    Event times: 2 (d=2, n=3), 5 (d=1, n=1).
    surv(h)=P(T≥h)=∏_{t_i<h}(1-d/n).
    surv(1)=surv(2)=1, surv(3)=1-2/3=1/3, surv(5)=1/3, surv(6)=1/3*(1-1/1)=0."""
    surv = kaplan_meier([2, 2, 5], [False, False, False])
    assert surv[1] == pytest.approx(1.0)
    assert surv[2] == pytest.approx(1.0)
    assert surv[3] == pytest.approx(1 / 3)
    assert surv[5] == pytest.approx(1 / 3)
    assert surv[6] == pytest.approx(0.0)


def test_s0_and_s1_are_one():
    surv = kaplan_meier([3, 7, 7], [False, False, False])
    assert surv[0] == pytest.approx(1.0)
    assert surv[1] == pytest.approx(1.0)


def test_survival_is_non_increasing():
    surv = kaplan_meier([1, 3, 3, 8, 12], [False, False, True, False, False])
    keys = sorted(surv)
    vals = [surv[k] for k in keys]
    assert all(a >= b - 1e-12 for a, b in zip(vals, vals[1:], strict=False))


def test_censored_observation_stays_in_risk_set():
    """durations=[2,3], censored=[True, False].
    Censored в 2 не даёт смерти; смерть только в 3 (n=#{≥3}=1).
    surv(3)=1 (нет t_i<3), surv(4)=1*(1-1/1)=0."""
    surv = kaplan_meier([2, 3], [True, False])
    assert surv[2] == pytest.approx(1.0)
    assert surv[3] == pytest.approx(1.0)
    assert surv[4] == pytest.approx(0.0)


def test_single_window():
    surv = kaplan_meier([4], [False])
    assert surv[4] == pytest.approx(1.0)   # дожило до 4
    assert surv[5] == pytest.approx(0.0)   # умерло в 4 → P(T≥5)=0


# === survival_at ===


def test_survival_at_clamps_below_zero_and_above_max():
    surv = kaplan_meier([2, 2, 5], [False, False, False])
    assert survival_at(surv, 0) == pytest.approx(1.0)
    assert survival_at(surv, -10) == pytest.approx(1.0)
    # за пределами таблицы → хвост (0.0 здесь)
    assert survival_at(surv, 999) == pytest.approx(surv[max(surv)])


# === conditional_survival ===


def test_conditional_survival_basic():
    """surv из [2,2,5]: P(T≥5 | T≥3) = surv(5)/surv(3) = (1/3)/(1/3) = 1.0."""
    surv = kaplan_meier([2, 2, 5], [False, False, False])
    assert conditional_survival(surv, age=3, k=2) == pytest.approx(1.0)


def test_conditional_survival_none_when_base_zero():
    surv = kaplan_meier([2], [False])  # surv(3)=0
    assert conditional_survival(surv, age=3, k=1) is None


# === median_residual_life ===


def test_median_residual_life_drops_to_half():
    """Десять окон длиной 10, два — длиной 2 (всего 12), все смерти.
    В возрасте 0 медианная остаточная ~10 (большинство доживает до 10)."""
    durations = [2, 2] + [10] * 10
    surv = kaplan_meier(durations, [False] * len(durations))
    # при age=0: ищем k, где surv(k) ≤ 0.5. surv(3)=10/12≈0.833, surv(11)=0 → падение в 11.
    rl = median_residual_life(surv, age=0)
    assert rl is not None
    assert rl == pytest.approx(11.0)


def test_median_residual_life_none_when_horizon_too_short():
    surv = kaplan_meier([5], [False])  # max_h = 6
    # в возрасте 6 горизонта вперёд нет
    assert median_residual_life(surv, age=6) is None


def test_median_residual_life_none_when_never_reaches_half():
    """Сильное censoring: большинство окон ещё открыто → кривая не падает до 0.5."""
    surv = kaplan_meier([10, 10, 10], [True, True, True])  # все censored, смертей нет
    # surv везде 1.0 → остаточная жизнь не оценима
    assert median_residual_life(surv, age=2) is None


# === median_lifetime ===


def test_median_lifetime_basic():
    """[2,2,5]: surv(3)=1/3 ≤ 0.5 → медиана 3."""
    surv = kaplan_meier([2, 2, 5], [False, False, False])
    assert median_lifetime(surv) == pytest.approx(3.0)


def test_median_lifetime_none_under_heavy_censoring():
    surv = kaplan_meier([8, 8], [True, True])  # нет смертей → surv≡1
    assert median_lifetime(surv) is None
