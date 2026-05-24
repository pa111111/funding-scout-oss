from ..config import settings
from .base import Connector, FundingTick
from .edgex import EdgeXConnector
from .hyperliquid import HyperliquidConnector
from .lighter import LighterConnector
from .pacifica import PacificaConnector

# Реестр активных коннекторов. Snapshot-runner итерирует по нему.
# Когда добавляется новая DEX — её инстанс кладётся сюда.
#
# HyperliquidConnector() — основной perp-dex (крипта).
# HyperliquidConnector(dex=...) — по одному на каждый HIP-3 builder-dex из конфига
# (RWA: нефть/металлы/акции/индексы). venue = hyperliquid-<dex>, тикеры без префикса.
ALL_CONNECTORS: list[Connector] = [
    HyperliquidConnector(),
    *[HyperliquidConnector(dex=d) for d in settings.hyperliquid_builder_dexs],
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
