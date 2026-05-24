"""Round-trip costs per venue (one-side, входы +выходы обеих ног считаются как round_trip_a + round_trip_b).

ВАЖНО: эти числа — приближения. Точные комиссии зависят от tier'а аккаунта и от того,
открываешься ли maker/taker. Источник для v0.1:

- Hyperliquid: maker 0.015%, taker 0.045%. Считаем что мы заходим лимиткой
  (maker) и выходим тоже лимиткой → 2× 0.015% = 0.03% per side. Если придётся
  выходить маркетом — реально будет 0.06%. Берём pessimistic = 0.06% round-trip.
- Lighter: 0% maker и taker на free-tier. → 0%. Из расшифровки стрима подтверждено
  для всех типов ордеров. Если введут fees — обновить здесь.

Эти числа открыто отображаются в UI и должны переопределяться когда:
1. Биржа меняет fee schedule
2. У пользователя другой tier
3. Реальный backtest показывает большее расхождение (slippage, не учтённый газ и т.д.)

Из третьестороннего отчёта (см. docs/risk.md): round-trip 0.20–0.28% — это
агрегат на CEX с slippage; для DEX без газа на ноге Lighter числа будут ниже.
Для cash-and-carry на DEX где спот+перп в разных протоколах — ВЫШЕ (~0.4–1%) из-за газа.
"""

from __future__ import annotations

# One-side round-trip cost: суммарный fee % за вход + выход на одной ноге.
# Composite cost для cross-DEX связки = ROUND_TRIP_COST_PCT[long_venue] + ROUND_TRIP_COST_PCT[short_venue].
ROUND_TRIP_COST_PCT: dict[str, float] = {
    "hyperliquid": 0.06,   # 2 × maker 0.015% pessimistic = 0.06% (если пройдёт market — больше)
    "lighter": 0.00,       # zero-fee на момент 2026-05
    # Когда добавятся новые venues:
    # "edgex": ?,
    # "pacifica": ?,
}

# Default fallback для незнакомого venue: pessimistic 0.10% round-trip.
DEFAULT_ROUND_TRIP_COST_PCT = 0.10


def _venue_cost(venue: str) -> float:
    """Round-trip cost одной ноги по venue. Знает про HIP-3 builder-dex'ы."""
    cost = ROUND_TRIP_COST_PCT.get(venue)
    if cost is not None:
        return cost
    # HIP-3 builder-dex'ы именуются "hyperliquid-<dex>" и живут на HyperCore с той же
    # базовой fee-структурой, что основной HL → берём HL cost, а не pessimistic default.
    # ⚠️ ВНИМАНИЕ: deployer builder-dex'а может брать доп. builder-fee (feeRecipient в
    # perpDexs). Реальный round-trip может быть ВЫШЕ base 0.06%. Это floor — уточнить
    # реальной сделкой и при необходимости добавить explicit запись в ROUND_TRIP_COST_PCT.
    if venue.startswith("hyperliquid"):
        return ROUND_TRIP_COST_PCT["hyperliquid"]
    return DEFAULT_ROUND_TRIP_COST_PCT


def round_trip_cost_pair(long_venue: str, short_venue: str) -> float:
    """Суммарный round-trip cost для cross-DEX связки в %.

    Возвращает сумму costs обеих ног. Если venue неизвестен — pessimistic default.
    HIP-3 builder-dex'ы (hyperliquid-<dex>) наследуют base HL cost.
    """
    return _venue_cost(long_venue) + _venue_cost(short_venue)
