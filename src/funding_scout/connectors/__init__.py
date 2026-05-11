from .base import Connector, FundingTick
from .edgex import EdgeXConnector
from .hyperliquid import HyperliquidConnector
from .lighter import LighterConnector
from .pacifica import PacificaConnector

# Реестр активных коннекторов. Snapshot-runner итерирует по нему.
# Когда добавляется новая DEX — её инстанс кладётся сюда.
ALL_CONNECTORS: list[Connector] = [
    HyperliquidConnector(),
    LighterConnector(),
    PacificaConnector(),
    EdgeXConnector(),
]

__all__ = [
    "ALL_CONNECTORS",
    "Connector",
    "EdgeXConnector",
    "FundingTick",
    "HyperliquidConnector",
    "LighterConnector",
    "PacificaConnector",
]
