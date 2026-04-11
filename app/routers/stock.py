import logging

from fastapi import APIRouter, HTTPException

from app.services.price_service import get_price

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stock-edge/{symbol}", summary="Current stock price")
async def get_stock_price(symbol: str):
    """
    Returns the current price for a symbol.
    Tries Alpha Vantage → Twelve Data → Finnhub automatically.
    No API key required.
    """
    symbol = symbol.upper().strip()
    result = await get_price(symbol)
    if not result:
        raise HTTPException(
            status_code=503,
            detail={"error": "All price providers failed", "symbol": symbol},
        )
    return {
        "symbol": result.symbol,
        "price": round(result.price, 2),
        "source": result.source,
        "cached": False,
    }
