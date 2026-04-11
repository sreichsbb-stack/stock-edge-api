import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import require_api_key
from app.core.cache import cache
from app.services.price_service import get_history, get_price
from app.services.signal_logger import (
    get_performance_stats,
    log_signal,
    resolve_outcomes,
)
from app.services.signal_service import compute_signal

logger = logging.getLogger(__name__)
router = APIRouter()

SIGNAL_TTL = 300  # 5 minutes


@router.get("/signal/{symbol}", summary="Trading signal for a symbol")
async def get_signal(symbol: str, _: str = Depends(require_api_key)):
    """
    Returns BUY / SELL / HOLD with confidence score and full indicators.
    Requires API key via X-API-Key header or ?api_key= query param.
    """
    symbol = symbol.upper().strip()
    cache_key = f"signal:{symbol}"

    cached = await cache.get(cache_key)
    if cached:
        return {**cached, "cached": True}

    price_result, bars = await asyncio.gather(
        get_price(symbol),
        get_history(symbol, bars=60),
        return_exceptions=True,
    )

    if isinstance(price_result, Exception):
        price_result = None
    if isinstance(bars, Exception):
        bars = []

    if price_result is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "All price providers failed", "symbol": symbol},
        )

    if not bars or len(bars) < 30:
        return {
            "symbol": symbol,
            "signal": "INSUFFICIENT_DATA",
            "confidence": 0,
            "signal_strength": 0,
            "price": round(price_result.price, 2),
            "data_source": price_result.source,
            "cached": False,
            "note": "Not enough historical data. Try again shortly.",
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

    # Log every fresh signal for performance tracking (fire and forget)
    asyncio.create_task(
        log_signal(
            symbol=symbol,
            signal=signal_result.signal,
            confidence=signal_result.confidence,
            price=price_result.price,
            indicators=signal_result.indicators,
        )
    )

    return response


@router.post("/signals/batch", summary="Signals for multiple symbols")
async def batch_signals(symbols: list[str], _: str = Depends(require_api_key)):
    """
    Fetch signals for up to 10 symbols in one call.
    Ideal for portfolio monitoring.
    """
    if len(symbols) > 10:
        raise HTTPException(
            status_code=400, detail="Maximum 10 symbols per batch request"
        )

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
            return {
                "symbol": sym,
                "signal": "INSUFFICIENT_DATA",
                "price": round(price_result.price, 2),
            }

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

        asyncio.create_task(
            log_signal(
                symbol=sym,
                signal=result.signal,
                confidence=result.confidence,
                price=price_result.price,
                indicators=result.indicators,
            )
        )

        return response

    results = await asyncio.gather(*[_fetch_one(s) for s in symbols])
    return {"results": list(results), "count": len(results)}


@router.get("/performance", summary="Signal accuracy stats (public)")
async def performance_stats(symbol: Optional[str] = Query(default=None)):
    """
    Public endpoint showing tracked signal accuracy.
    Use this on your RapidAPI listing and landing page.
    Optional ?symbol=AAPL to filter by ticker.
    """
    stats = await get_performance_stats(symbol=symbol)
    return stats


@router.post("/performance/resolve", summary="Resolve pending signal outcomes")
async def resolve(days: int = 1, _: str = Depends(require_api_key)):
    """
    Resolves unresolved signals older than `days` by checking current price.
    Call this daily via a cron job or Render cron service.
    """
    resolved = await resolve_outcomes(lookback_days=days)
    return {"resolved": resolved, "lookback_days": days}
