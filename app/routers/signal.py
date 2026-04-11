import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import require_api_key
from app.core.cache import cache
from app.services.price_service import get_history, get_price
from app.services.signal_service import compute_signal

logger = logging.getLogger(__name__)
router = APIRouter()

SIGNAL_TTL = 300  # 5 minutes


@router.get("/signal/{symbol}", summary="Trading signal for a symbol")
async def get_signal(symbol: str, _: str = Depends(require_api_key)):
    """
    Returns BUY / SELL / HOLD signal with confidence score and full indicators.
    Requires API key via `X-API-Key` header or `?api_key=` query param.

    Degrades gracefully: if history is unavailable, returns price + INSUFFICIENT_DATA
    rather than a hard 500.
    """
    symbol = symbol.upper().strip()
    cache_key = f"signal:{symbol}"

    cached = await cache.get(cache_key)
    if cached:
        return {**cached, "cached": True}

    # Fetch price + history concurrently to minimise latency
    price_result, bars = await asyncio.gather(
        get_price(symbol),
        get_history(symbol, bars=60),
        return_exceptions=True,
    )

    # Handle exceptions from gather
    if isinstance(price_result, Exception):
        logger.error(f"price gather exception [{symbol}]: {price_result}")
        price_result = None
    if isinstance(bars, Exception):
        logger.error(f"history gather exception [{symbol}]: {bars}")
        bars = []

    if price_result is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "All price providers failed", "symbol": symbol},
        )

    # Graceful degradation: no history → return price without signal
    if not bars or len(bars) < 30:
        logger.warning(f"Insufficient history for signal [{symbol}]: {len(bars) if bars else 0} bars")
        return {
            "symbol": symbol,
            "signal": "INSUFFICIENT_DATA",
            "confidence": 0,
            "signal_strength": 0,
            "price": round(price_result.price, 2),
            "data_source": price_result.source,
            "cached": False,
            "note": "Not enough historical data to compute signal. Try again shortly.",
        }

    signal_result = compute_signal(bars, price_result.price, symbol)
    if not signal_result:
        raise HTTPException(
            status_code=503,
            detail={"error": "Signal computation failed", "symbol": symbol},
        )

    response = {
        "symbol": symbol,
        "signal": signal_result.signal,
        "confidence": signal_result.confidence,
        "signal_strength": signal_result.signal_strength,
        "price": round(price_result.price, 2),
        "indicators": signal_result.indicators,
        "data_source": price_result.source,
        "cached": False,
    }

    await cache.set(cache_key, response, ttl=SIGNAL_TTL)
    return response


@router.post("/signals/batch", summary="Signals for multiple symbols")
async def batch_signals(symbols: list[str], _: str = Depends(require_api_key)):
    """
    Fetch signals for up to 10 symbols in one call.
    Ideal for portfolio monitoring.
    """
    if len(symbols) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 symbols per batch request")

    symbols = [s.upper().strip() for s in symbols]

    async def _fetch_one(sym: str):
        cache_key = f"signal:{sym}"
        cached = await cache.get(cache_key)
        if cached:
            return {**cached, "cached": True}

        price_result, bars = await asyncio.gather(
            get_price(sym),
            get_history(sym, bars=60),
            return_exceptions=True,
        )

        if isinstance(price_result, Exception) or price_result is None:
            return {"symbol": sym, "error": "price_unavailable"}
        if isinstance(bars, Exception):
            bars = []
        if not bars or len(bars) < 30:
            return {"symbol": sym, "signal": "INSUFFICIENT_DATA", "price": round(price_result.price, 2)}

        result = compute_signal(bars, price_result.price, sym)
        if not result:
            return {"symbol": sym, "error": "computation_failed"}

        response = {
            "symbol": sym,
            "signal": result.signal,
            "confidence": result.confidence,
            "signal_strength": result.signal_strength,
            "price": round(price_result.price, 2),
            "indicators": result.indicators,
            "data_source": price_result.source,
            "cached": False,
        }
        await cache.set(cache_key, response, ttl=SIGNAL_TTL)
        return response

    results = await asyncio.gather(*[_fetch_one(s) for s in symbols])
    return {"results": list(results), "count": len(results)}
