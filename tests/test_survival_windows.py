"""Unit-тесты извлечения окон. Чистые списки — без БД."""

from __future__ import annotations

from funding_scout.survival.windows import Window, extract_windows


def _series(values: list[float | None], start_ts: int = 1000, step: int = 3600):
    """Хелпер: список значений → серия [(ts, val)] с часовым шагом."""
    return [(start_ts + i * step, v) for i, v in enumerate(values)]


def test_empty_series_no_windows():
    assert extract_windows([]) == []


def test_all_below_threshold_no_windows():
    series = _series([10.0, 20.0, 5.0, 29.9])
    assert extract_windows(series, threshold=30.0) == []


def test_single_window_in_the_middle():
    # ниже, [выше, выше, выше], ниже → одно окно длиной 3, не censored, не truncated
    series = _series([10.0, 40.0, 50.0, 35.0, 10.0])
    windows = extract_windows(series, threshold=30.0)
    assert len(windows) == 1
    w = windows[0]
    assert w.duration_h == 3
    assert w.start_ts == series[1][0]
    assert w.end_ts == series[3][0]
    assert w.censored is False
    assert w.left_truncated is False


def test_threshold_is_inclusive():
    """Значение РОВНО на пороге считается «над порогом» (≥, как у Age h)."""
    series = _series([30.0])
    windows = extract_windows(series, threshold=30.0)
    assert len(windows) == 1
    assert windows[0].duration_h == 1


def test_none_breaks_window():
    # [выше, выше], None, [выше] → два отдельных окна
    series = _series([40.0, 40.0, None, 40.0])
    windows = extract_windows(series, threshold=30.0)
    assert len(windows) == 2
    assert windows[0].duration_h == 2
    assert windows[1].duration_h == 1
    # второе окно упирается в правый край → censored
    assert windows[1].censored is True


def test_right_censored_window_at_end():
    series = _series([10.0, 40.0, 50.0])
    windows = extract_windows(series, threshold=30.0)
    assert len(windows) == 1
    assert windows[0].censored is True
    assert windows[0].left_truncated is False


def test_left_truncated_window_at_start():
    series = _series([40.0, 50.0, 10.0])
    windows = extract_windows(series, threshold=30.0)
    assert len(windows) == 1
    assert windows[0].left_truncated is True
    assert windows[0].censored is False


def test_window_spanning_whole_series_is_both_flagged():
    series = _series([40.0, 50.0, 60.0])
    windows = extract_windows(series, threshold=30.0)
    assert len(windows) == 1
    w = windows[0]
    assert w.left_truncated is True
    assert w.censored is True
    assert w.duration_h == 3


def test_multiple_clean_windows():
    # ниже, [выше×2], ниже, [выше×1], ниже, [выше×3 до конца]
    series = _series([10, 40, 40, 10, 40, 10, 40, 40, 40])
    windows = extract_windows(series, threshold=30.0)
    assert [w.duration_h for w in windows] == [2, 1, 3]
    assert [w.censored for w in windows] == [False, False, True]
    assert [w.left_truncated for w in windows] == [False, False, False]


def test_window_is_frozen():
    w = Window(start_ts=1, end_ts=2, duration_h=2, censored=False, left_truncated=False)
    try:
        w.duration_h = 5  # type: ignore[misc]
    except Exception as e:  # frozen dataclass → FrozenInstanceError
        assert "FrozenInstanceError" in type(e).__name__ or "frozen" in str(e).lower()
    else:
        raise AssertionError("Window должен быть frozen")
