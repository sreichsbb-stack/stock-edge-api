import asyncio
import logging
from typing import Optional

from app.core.cache import cache
from app.providers.alphavantage import AlphaVantageProvider
from app.providers.base import OHLCVBar, PriceResult
from app.providers.finnhub import FinnhubProvider
from app.providers.twelvedata import TwelveDataProvider

logger = logging.getLogger(__name__)

# Order matters: AV for price (most reliable quote), TD for history, Finnhub last resort
PRICE_PROVIDERS = [
    AlphaVantageProvider(),
    TwelveDataProvider(),
    FinnhubProvider(),
]

HISTORY_PROVIDERS = [
    TwelveDataProvider(),   # best free-tier history
    FinnhubProvider(),
    AlphaVantageProvider(), # costs an AV call, save for last
]

PRICE_TTL = 60       # seconds
HISTORY_TTL = 900    # 15 min — daily bars don't change intraday


async def get_price(symbol: str) -> Optional[PriceResult]:
    cache_key = f"price:{symbol}"
    cached = await cache.get(cache_key)
    if cached:
        return PriceResult(**cached)

    for provider in PRICE_PROVIDERS:
        try:
            result = await asyncio.wait_for(
                provider.get_price(symbol), timeout=6.0
            )
            if result:
                await cache.set(cache_key, result.__dict__, ttl=PRICE_TTL)
                logger.info(f"price:{symbol} from {provider.name}")
                return result
        except asyncio.TimeoutError:
            logger.warning(f"{provider.name} timed out for price:{symbol}")
        except Exception as e:
            logger.warning(f"{provider.name} error for price:{symbol}: {e}")

    logger.error(f"All providers failed for price:{symbol}")
    return None


async def get_history(symbol: str, bars: int = 60) -> list[OHLCVBar]:
    cache_key = f"history:{symbol}:{bars}"
    cached = await cache.get(cache_key)
    if cached:
        return [OHLCVBar(**b) for b in cached]

    for provider in HISTORY_PROVIDERS:
        try:
            result = await asyncio.wait_for(
                provider.get_history(symbol, bars), timeout=10.0
            )
            if result and len(result) >= 30:  # minimum bars needed for indicators
                await cache.set(
                    cache_key, [b.__dict__ for b in result], ttl=HISTORY_TTL
                )
                logger.info(f"history:{symbol} ({len(result)} bars) from {provider.name}")
                return result
        except asyncio.TimeoutError:
            logger.warning(f"{provider.name} timed out for history:{symbol}")
        except Exception as e:
            logger.warning(f"{provider.name} error for history:{symbol}: {e}")

    logger.error(f"All providers failed for history:{symbol}")
    return []
