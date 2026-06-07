"""SQLAlchemy ORM models. Compatible with SQLite (local) and Postgres (VPS).

Schema принципы:
- Composite PK (ts, venue, ticker) → один и тот же снапшот не дублируется при ретраях.
- Поле `raw` — JSON-дамп исходного ответа от биржи. Если потом понадобится поле,
  которое мы сегодня не извлекаем, можно бэкфилить из `raw` без перезапроса API.
- Все nullable поля — потому что не каждая биржа отдаёт OI/index_price/volume.
"""

from __future__ import annotations

from sqlalchemy import JSON, Float, Index, Integer, PrimaryKeyConstraint, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class FundingSnapshot(Base):
    __tablename__ = "funding_snapshot"

    ts: Mapped[int] = mapped_column(Integer, nullable=False, doc="Unix timestamp UTC")
    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    ticker: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="Base symbol, no '-PERP' suffix (e.g. 'BTC', 'NVDA')"
    )

    funding_rate_1h: Mapped[float] = mapped_column(
        Float, nullable=False, doc="Hourly funding rate as decimal (0.0001 = 0.01%/h)"
    )
    mark_price: Mapped[float] = mapped_column(Float, nullable=False)

    index_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_long: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_short: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        PrimaryKeyConstraint("ts", "venue", "ticker", name="pk_funding_snapshot"),
        Index("idx_funding_snapshot_venue_ticker_ts", "venue", "ticker", "ts"),
        Index("idx_funding_snapshot_ts", "ts"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"FundingSnapshot(ts={self.ts}, venue={self.venue!r}, "
            f"ticker={self.ticker!r}, funding_rate_1h={self.funding_rate_1h:.6f})"
        )


class SetupSnapshot(Base):
    """Вычисленная связка, персистнутая на каденции снапшота.

    В отличие от `funding_snapshot` (сырьё), здесь лежит ВЫВОД детекторов — то, что
    раньше считалось на лету в `get_latest_setups()` и нигде не сохранялось. Без этой
    истории не работает decay/staleness (концепт Hermes §4.4): нельзя сказать «связка
    X протухла», не имея её прошлых значений. Пишется тем же runner'ом, что и сырьё,
    на тот же `ts` — один снапшот = одна согласованная пара (funding + setups).

    Композитный PK (ts, candidate_id) → идемпотентность при ретраях, как у сырья.
    """

    __tablename__ = "setup_snapshot"

    ts: Mapped[int] = mapped_column(Integer, nullable=False, doc="Unix ts UTC, == funding_snapshot.ts")
    candidate_id: Mapped[str] = mapped_column(
        String(160), nullable=False, doc="Стабильный id связки: TICKER:LONG:SHORT"
    )

    type: Mapped[str] = mapped_column(String(48), nullable=False)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    long_venue: Mapped[str] = mapped_column(String(32), nullable=False)
    short_venue: Mapped[str] = mapped_column(String(32), nullable=False)

    spread_apr_pct: Mapped[float] = mapped_column(Float, nullable=False)
    base_ev_per_dollar_per_day: Mapped[float] = mapped_column(Float, nullable=False)
    long_funding_apr_pct: Mapped[float] = mapped_column(Float, nullable=False)
    short_funding_apr_pct: Mapped[float] = mapped_column(Float, nullable=False)
    round_trip_cost_pct: Mapped[float] = mapped_column(Float, nullable=False)
    price_spread_pct: Mapped[float] = mapped_column(Float, nullable=False)

    # nullable: inf (spread<=0) и отсутствие volume хранятся как NULL, не как inf/nan.
    min_profitable_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_volume_24h_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("ts", "candidate_id", name="pk_setup_snapshot"),
        # decay-запросы: история одной связки во времени.
        Index("idx_setup_snapshot_candidate_ts", "candidate_id", "ts"),
        Index("idx_setup_snapshot_ts", "ts"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SetupSnapshot(ts={self.ts}, candidate_id={self.candidate_id!r}, "
            f"spread_apr_pct={self.spread_apr_pct:.2f})"
        )
