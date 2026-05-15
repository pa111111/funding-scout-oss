"""Cross-DEX same-ticker detector.

Тип 1 из таксономии (см. docs/strategies.md):
лонг по тикеру X на venue A, шорт того же X на venue B.
Подходит когда X листится на обеих биржах и funding-ставки расходятся.

Алгоритм:
1. Берём latest snapshot ts из БД.
2. Группируем все ticks этого ts по тикеру.
3. Для каждого тикера, у которого ≥ 2 venue в этом снапшоте,
   генерируем все попарные комбинации (n*(n-1)/2 для n venues).
4. Для каждой пары: ту venue, у которой funding_rate ниже — лонгуем
   (нам платят за лонг там), другую — шортим (нам платят за шорт там).
5. Считаем EV через ev.base.compute_setup_ev и собираем Setup.

Никакой фильтрации по EV/риску/объёму. Эмитим всё, пользователь сортирует в UI.
"""

from __future__ import annotations

from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ev.base import compute_setup_ev
from ..storage.models import FundingSnapshot
from .base import Setup


class CrossDexSameTickerDetector:
    type_id = "cross-dex-same-ticker"

    def detect_for_snapshot(self, session: Session, ts: int) -> list[Setup]:
        """Прогнать детектор по конкретному snapshot timestamp."""
        rows = (
            session.execute(
                select(FundingSnapshot).where(FundingSnapshot.ts == ts)
            )
            .scalars()
            .all()
        )

        # Группировка по тикеру → list[(venue, row)]
        by_ticker: dict[str, list[FundingSnapshot]] = {}
        for r in rows:
            by_ticker.setdefault(r.ticker, []).append(r)

        setups: list[Setup] = []
        for ticker, snaps in by_ticker.items():
            if len(snaps) < 2:
                continue
            # Все попарные комбинации (для 2 venues = 1 пара, для 3 = 3 пары, ...)
            for a, b in combinations(snaps, 2):
                setup = self._build_setup(ticker, a, b, ts)
                if setup is not None:
                    setups.append(setup)

        return setups

    def detect_latest(self, session: Session) -> tuple[int | None, list[Setup]]:
        """Прогнать детектор по последнему ts в БД. Возвращает (ts, setups).
        Если снапшотов нет — (None, [])."""
        from sqlalchemy import func

        latest_ts = session.scalar(select(func.max(FundingSnapshot.ts)))
        if latest_ts is None:
            return None, []
        return latest_ts, self.detect_for_snapshot(session, latest_ts)

    @staticmethod
    def _build_setup(
        ticker: str,
        a: FundingSnapshot,
        b: FundingSnapshot,
        ts: int,
    ) -> Setup | None:
        # Структурная валидация: должны быть валидные цены на обеих ногах.
        if a.mark_price <= 0 or b.mark_price <= 0:
            return None

        # Распределение long/short: лонгуем там, где funding ниже (нам там лучше платят за лонг,
        # либо мы меньше платим). Шортим там, где funding выше.
        if a.funding_rate_1h <= b.funding_rate_1h:
            long_snap, short_snap = a, b
        else:
            long_snap, short_snap = b, a

        ev = compute_setup_ev(
            funding_rate_long_1h=long_snap.funding_rate_1h,
            funding_rate_short_1h=short_snap.funding_rate_1h,
            long_venue=long_snap.venue,
            short_venue=short_snap.venue,
        )

        # Price spread: насколько лонг-нога дороже шорт-ноги (в %).
        # Если +ve — мы покупаем дороже чем продаём → convergence работает против нас.
        # Если -ve — спред в нашу пользу при сходимости.
        price_spread_pct = (long_snap.mark_price - short_snap.mark_price) / short_snap.mark_price * 100.0

        long_vol = long_snap.volume_24h
        short_vol = short_snap.volume_24h
        min_vol: float | None
        if long_vol is None or short_vol is None:
            min_vol = None
        else:
            min_vol = min(long_vol, short_vol)

        return Setup(
            type=CrossDexSameTickerDetector.type_id,
            ticker=ticker,
            long_venue=long_snap.venue,
            short_venue=short_snap.venue,
            spread_apr_pct=ev.spread_apr * 100.0,
            base_ev_per_dollar_per_day=ev.base_ev_per_dollar_per_day,
            long_funding_apr_pct=ev.funding_long_apr * 100.0,
            short_funding_apr_pct=ev.funding_short_apr * 100.0,
            round_trip_cost_pct=ev.round_trip_cost_pct,
            min_profitable_hours=ev.min_profitable_hours,
            long_mark_price=long_snap.mark_price,
            short_mark_price=short_snap.mark_price,
            price_spread_pct=price_spread_pct,
            min_volume_24h_usd=min_vol,
            long_volume_24h_usd=long_vol,
            short_volume_24h_usd=short_vol,
            snapshot_ts=ts,
        )
