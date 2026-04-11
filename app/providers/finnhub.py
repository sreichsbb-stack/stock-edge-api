import logging
import time
from typing import Optional

import httpx

from app.core.config import settings
from app.providers.base import BaseProvider, OHLCVBar, PriceResult

logger = logging.getLogger(__name__)

BASE = "https://finnhub.io/api/v1"


class FinnhubProvider(BaseProvider):
    name = "finnhub"

    async def get_price(self, symbol: str) -> Optional[PriceResult]:
        if not settings.FINNHUB_KEY:
            return None
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"{BASE}/quote",
                    params={"symbol": symbol, "token": settings.FINNHUB_KEY},
                )
            data = r.json()
            price = data.get("c")  # current price
            if not price or price == 0:
                return None
            return PriceResult(symbol=symbol, price=float(price), source=self.name)
        except Exception as e:
            logger.warning(f"Finnhub get_price failed [{symbol}]: {e}")
            return None

    async def get_history(self, symbol: str, bars: int = 60) -> list[OHLCVBar]:
        if not settings.FINNHUB_KEY:
            return []
        try:
            to_ts = int(time.time())
            # Add buffer days for weekends/holidays
            from_ts = to_ts - (bars * 2 * 86400)
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{BASE}/stock/candle",
                    params={
                        "symbol": symbol,
                        "resolution": "D",
                        "from": from_ts,
                        "to": to_ts,
                        "token": settings.FINNHUB_KEY,
                    },
                )
            data = r.json()
            if data.get("s") != "ok":
                logger.warning(f"Finnhub candle status not ok for {symbol}: {data.get('s')}")
                return []
            candles = list(
                zip(data["t"], data["o"], data["h"], data["l"], data["c"], data["v"])
            )
            # Take last `bars` trading days
            return [
                OHLCVBar(
                    timestamp=str(t),
                    open=float(o),
                    high=float(h),
                    low=float(l),
                    close=float(c),
                    volume=float(v),
                )
                for t, o, h, l, c, v in candles[-bars:]
            ]
        except Exception as e:
            logger.warning(f"Finnhub get_history failed [{symbol}]: {e}")
            return []
