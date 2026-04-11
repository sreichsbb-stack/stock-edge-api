import logging
from typing import Optional

import httpx

from app.core.config import settings
from app.providers.base import BaseProvider, OHLCVBar, PriceResult

logger = logging.getLogger(__name__)

BASE = "https://www.alphavantage.co/query"


class AlphaVantageProvider(BaseProvider):
    name = "alphavantage"

    async def get_price(self, symbol: str) -> Optional[PriceResult]:
        if not settings.AV_KEY:
            return None
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    BASE,
                    params={
                        "function": "GLOBAL_QUOTE",
                        "symbol": symbol,
                        "apikey": settings.AV_KEY,
                    },
                )
            data = r.json()
            # AV signals rate limit with a "Note" key
            if "Note" in data or "Information" in data or "Error Message" in data:
                logger.warning(f"AlphaVantage rate-limited or error for {symbol}")
                return None
            price_str = data.get("Global Quote", {}).get("05. price")
            if not price_str or float(price_str) == 0:
                return None
            return PriceResult(symbol=symbol, price=float(price_str), source=self.name)
        except Exception as e:
            logger.warning(f"AlphaVantage get_price failed [{symbol}]: {e}")
            return None

    async def get_history(self, symbol: str, bars: int = 60) -> list[OHLCVBar]:
        """AV daily time series - used as last-resort history fallback."""
        if not settings.AV_KEY:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    BASE,
                    params={
                        "function": "TIME_SERIES_DAILY",
                        "symbol": symbol,
                        "outputsize": "compact",  # last 100 days
                        "apikey": settings.AV_KEY,
                    },
                )
            data = r.json()
            if "Note" in data or "Information" in data or "Error Message" in data:
                return []
            ts = data.get("Time Series (Daily)", {})
            result = []
            for date_str in sorted(ts.keys())[-bars:]:
                v = ts[date_str]
                result.append(
                    OHLCVBar(
                        timestamp=date_str,
                        open=float(v["1. open"]),
                        high=float(v["2. high"]),
                        low=float(v["3. low"]),
                        close=float(v["4. close"]),
                        volume=float(v["5. volume"]),
                    )
                )
            return result
        except Exception as e:
            logger.warning(f"AlphaVantage get_history failed [{symbol}]: {e}")
            return []
