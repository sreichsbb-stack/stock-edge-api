from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PriceResult:
    symbol: str
    price: float
    source: str


@dataclass
class OHLCVBar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class BaseProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def get_price(self, symbol: str) -> Optional[PriceResult]:
        """Return current price or None on failure."""
        ...

    @abstractmethod
    async def get_history(self, symbol: str, bars: int = 60) -> list[OHLCVBar]:
        """Return list of daily OHLCV bars, oldest-first. Empty list on failure."""
        ...
