import logging
from typing import Optional

import httpx

from app.core.config import settings
from app.providers.base import BaseProvider, OHLCVBar, PriceResult

logger = logging.getLogger(__name__)

BASE = "https://api.twelvedata.com"


class TwelveDataProvider(BaseProvider):
    name = "twelvedata"

    async def get_price(self, symbol: str) -> Optional[PriceResult]:
        if not settings.TWELVEDATA_KEY:
            return None
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"{BASE}/price",
                    params={"symbol": symbol, "apikey": settings.TWELVEDATA_KEY},
                )
            data = r.json()
            if "price" not in data:
                logger.warning(f"TwelveData no price for {symbol}: {data.get('message', '')}")
                return None
            return PriceResult(
                symbol=symbol, price=float(data["price"]), source=self.name
            )
        except Exception as e:
            logger.warning(f"TwelveData get_price failed [{symbol}]: {e}")
            return None

    async def get_history(self, symbol: str, bars: int = 60) -> list[OHLCVBar]:
        if not settings.TWELVEDATA_KEY:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{BASE}/time_series",
                    params={
                        "symbol": symbol,
                        "interval": "1day",
                        "outputsize": bars,
                        "apikey": settings.TWELVEDATA_KEY,
                    },
                )
            data = r.json()
            if "values" not in data:
                logger.warning(f"TwelveData no history for {symbol}: {data.get('message', '')}")
                return []
            # API returns newest-first; reverse to oldest-first
            return [
                OHLCVBar(
                    timestamp=v["datetime"],
                    open=float(v["open"]),
                    high=float(v["high"]),
                    low=float(v["low"]),
                    close=float(v["close"]),
                    volume=float(v["volume"]),
                )
                for v in reversed(data["values"])
            ]
        except Exception as e:
            logger.warning(f"TwelveData get_history failed [{symbol}]: {e}")
            return []
