"""SQLAlchemy ORM models. Compatible with SQLite (local) and Postgres (VPS).

Schema принципы:
- Composite PK (ts, venue, ticker) → один и тот же снапшот не дублируется при ретраях.
- Поле `raw` — JSON-дамп исходного ответа от биржи. Если потом понадобится поле,
  которое мы сегодня не извлекаем, можно бэкфилить из `raw` без перезапроса API.
- Все nullable поля — потому что не каждая биржа отдаёт OI/index_price/volume.
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, JSON, PrimaryKeyConstraint, String
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
