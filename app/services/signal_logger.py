import json
import logging
import time
from typing import Optional

from app.core.cache import cache

logger = logging.getLogger(__name__)

# Redis key design:
#   signal_log:{symbol}:{timestamp_ms}  → individual signal entry
#   signal_log_index                    → sorted set: member=key, score=timestamp

LOG_TTL = 60 * 60 * 24 * 90  # keep 90 days of history
INDEX_KEY = "signal_log_index"


async def log_signal(
    symbol: str,
    signal: str,
    confidence: float,
    price: float,
    indicators: dict,
) -> None:
    """Fire-and-forget: log a signal emission to Redis."""
    client = cache._get_client()
    if not client:
        return
    try:
        ts = int(time.time() * 1000)  # ms timestamp
        entry_key = f"signal_log:{symbol}:{ts}"
        entry = {
            "symbol": symbol,
            "signal": signal,
            "confidence": confidence,
            "price_at_signal": price,
            "indicators": indicators,
            "timestamp": ts,
            "outcome": None,       # filled later by resolve_outcomes()
            "outcome_price": None,
            "outcome_pct": None,
        }
        client_obj = cache._get_client()
        pipe = client_obj.pipeline()
        await pipe.setex(entry_key, LOG_TTL, json.dumps(entry))
        await pipe.zadd(INDEX_KEY, {entry_key: ts})
        await pipe.execute()
        logger.info(f"signal_log: logged {signal} for {symbol} @ {price}")
    except Exception as e:
        logger.warning(f"signal_log: failed to log [{symbol}]: {e}")


async def resolve_outcomes(lookback_days: int = 1) -> int:
    """
    For every unresolved signal older than `lookback_days` days,
    fetch the current price and compute outcome.
    Returns number of signals resolved.
    """
    client = cache._get_client()
    if not client:
        return 0

    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (lookback_days * 86400 * 1000)
    # only resolve signals older than lookback_days
    min_ms = 0
    max_ms = cutoff_ms

    try:
        keys = await client.zrangebyscore(INDEX_KEY, min_ms, max_ms)
    except Exception as e:
        logger.warning(f"resolve_outcomes: zrangebyscore failed: {e}")
        return 0

    resolved = 0
    for key in keys:
        try:
            raw = await client.get(key)
            if not raw:
                continue
            entry = json.loads(raw)
            if entry.get("outcome") is not None:
                continue  # already resolved

            # Import here to avoid circular import
            from app.services.price_service import get_price
            result = await get_price(entry["symbol"])
            if not result:
                continue

            entry_price = float(entry["price_at_signal"])
            current_price = result.price
            pct_change = ((current_price - entry_price) / entry_price) * 100

            # Determine if signal was correct
            signal = entry["signal"]
            if signal == "BUY":
                outcome = "WIN" if pct_change > 0 else "LOSS"
            elif signal == "SELL":
                outcome = "WIN" if pct_change < 0 else "LOSS"
            else:
                outcome = "NEUTRAL"

            entry["outcome"] = outcome
            entry["outcome_price"] = round(current_price, 2)
            entry["outcome_pct"] = round(pct_change, 2)

            await client.setex(key, LOG_TTL, json.dumps(entry))
            resolved += 1
        except Exception as e:
            logger.warning(f"resolve_outcomes: error on {key}: {e}")
            continue

    logger.info(f"resolve_outcomes: resolved {resolved} signals")
    return resolved


async def get_performance_stats(symbol: Optional[str] = None) -> dict:
    """
    Aggregate stats across all logged signals (or a single symbol).
    """
    client = cache._get_client()
    if not client:
        return {"error": "Redis unavailable"}

    try:
        # Get all keys from the index
        all_keys = await client.zrangebyscore(INDEX_KEY, 0, "+inf")
    except Exception as e:
        logger.warning(f"get_performance_stats: failed: {e}")
        return {"error": "Could not fetch stats"}

    total = 0
    resolved = 0
    wins = 0
    losses = 0
    by_signal: dict[str, dict] = {
        "BUY":  {"total": 0, "wins": 0, "losses": 0},
        "SELL": {"total": 0, "wins": 0, "losses": 0},
        "HOLD": {"total": 0, "wins": 0, "losses": 0},
    }
    symbols_seen: set[str] = set()
    oldest_ts: Optional[int] = None

    for key in all_keys:
        try:
            raw = await client.get(key)
            if not raw:
                continue
            entry = json.loads(raw)

            # Filter by symbol if requested
            if symbol and entry.get("symbol") != symbol.upper():
                continue

            total += 1
            sym = entry.get("symbol", "")
            symbols_seen.add(sym)

            ts = entry.get("timestamp", 0)
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts

            sig = entry.get("signal", "HOLD")
            if sig in by_signal:
                by_signal[sig]["total"] += 1

            outcome = entry.get("outcome")
            if outcome == "WIN":
                resolved += 1
                wins += 1
                if sig in by_signal:
                    by_signal[sig]["wins"] += 1
            elif outcome == "LOSS":
                resolved += 1
                losses += 1
                if sig in by_signal:
                    by_signal[sig]["losses"] += 1

        except Exception:
            continue

    overall_accuracy = round(wins / resolved * 100, 1) if resolved > 0 else None

    def sig_accuracy(s: str) -> Optional[float]:
        d = by_signal[s]
        total_resolved = d["wins"] + d["losses"]
        return round(d["wins"] / total_resolved * 100, 1) if total_resolved > 0 else None

    import datetime
    tracking_since = (
        datetime.datetime.fromtimestamp(oldest_ts / 1000).strftime("%Y-%m-%d")
        if oldest_ts else None
    )

    return {
        "signals_tracked": total,
        "signals_resolved": resolved,
        "overall_accuracy_pct": overall_accuracy,
        "buy_accuracy_pct": sig_accuracy("BUY"),
        "sell_accuracy_pct": sig_accuracy("SELL"),
        "wins": wins,
        "losses": losses,
        "symbols_tracked": len(symbols_seen),
        "tracking_since": tracking_since,
        "note": "Outcome = price change after 1 trading day",
    }
