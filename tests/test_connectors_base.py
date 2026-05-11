"""Тесты дата-классов и базового интерфейса коннекторов."""

from __future__ import annotations

import pytest

from funding_scout.connectors.base import Connector, FundingTick


def test_funding_tick_apr_positive():
    """0.0001/h × 24 × 365 = 0.876 (87.6% APR)."""
    t = FundingTick(venue="hl", ticker="BTC", funding_rate_1h=0.0001, mark_price=60000)
    assert t.funding_apr == pytest.approx(0.876, rel=1e-9)


def test_funding_tick_apr_negative():
    """Отрицательный funding (платят лонгам) → отрицательный APR."""
    t = FundingTick(venue="hl", ticker="ETH", funding_rate_1h=-0.0001, mark_price=3000)
    assert t.funding_apr == pytest.approx(-0.876, rel=1e-9)


def test_funding_tick_apr_zero():
    t = FundingTick(venue="hl", ticker="BTC", funding_rate_1h=0.0, mark_price=60000)
    assert t.funding_apr == 0.0


def test_funding_tick_optional_fields_default_none():
    t = FundingTick(venue="hl", ticker="BTC", funding_rate_1h=0.0, mark_price=60000)
    assert t.index_price is None
    assert t.oi_long is None
    assert t.oi_short is None
    assert t.volume_24h is None
    assert t.raw == {}


def test_connector_is_abstract():
    """Нельзя инстанцировать Connector напрямую — это ABC."""
    with pytest.raises(TypeError):
        Connector()  # type: ignore[abstract]
