import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.providers.base import OHLCVBar

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    symbol: str
    signal: str        # BUY | SELL | HOLD
    confidence: float  # 0.0 – 1.0
    signal_strength: int  # 1–5 stars
    price: float
    indicators: dict
    data_source: str = "computed"


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    arr = np.array(closes, dtype=float)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[-period:]))
    avg_loss = float(np.mean(losses[-period:]))
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(bars: list[OHLCVBar], period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return float(np.mean(trs[-period:]))


def compute_signal(bars: list[OHLCVBar], current_price: float, symbol: str = "") -> Optional[SignalResult]:
    MIN_BARS = 52
    if len(bars) < MIN_BARS:
        logger.warning(f"Insufficient bars for signal [{symbol}]: {len(bars)} < {MIN_BARS}")
        return None

    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]

    rsi = _rsi(closes)
    ma20 = float(np.mean(closes[-20:]))
    ma50 = float(np.mean(closes[-50:]))
    atr = _atr(bars)
    atr_pct = (atr / current_price * 100) if current_price > 0 else 0

    # Momentum: 10-day rate of change
    roc10 = ((closes[-1] - closes[-11]) / closes[-11] * 100) if len(closes) > 11 else 0.0

    # Volume: recent 5-day average vs 20-day average
    vol_recent = float(np.mean(volumes[-5:])) if len(volumes) >= 5 else 0
    vol_avg = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else 0
    vol_ratio = (vol_recent / vol_avg) if vol_avg > 0 else 1.0

    # ── Scoring ──────────────────────────────────────────────
    # +/- range roughly -1.0 to +1.0; positive = bullish
    score = 0.0

    # RSI (weight 0.30)
    if rsi < 30:
        score += 0.30
    elif rsi < 45:
        score += 0.15
    elif rsi > 70:
        score -= 0.30
    elif rsi > 55:
        score -= 0.10

    # MA trend (weight 0.35)
    ma_diff_pct = ((ma20 - ma50) / ma50 * 100) if ma50 > 0 else 0
    if ma_diff_pct > 1.5:
        score += 0.35
    elif ma_diff_pct > 0:
        score += 0.15
    elif ma_diff_pct < -1.5:
        score -= 0.35
    elif ma_diff_pct < 0:
        score -= 0.15

    # Momentum ROC (weight 0.20)
    if roc10 > 3:
        score += 0.20
    elif roc10 > 1:
        score += 0.08
    elif roc10 < -3:
        score -= 0.20
    elif roc10 < -1:
        score -= 0.08

    # Volume confirmation (weight 0.15) — amplifies the existing direction
    if vol_ratio > 1.3:
        score = score * 1.15
    elif vol_ratio < 0.7:
        score = score * 0.85

    # ── Signal ───────────────────────────────────────────────
    if score >= 0.25:
        signal = "BUY"
    elif score <= -0.25:
        signal = "SELL"
    else:
        signal = "HOLD"

    # ── Confidence ───────────────────────────────────────────
    # Map |score| → 0.40–0.95, then penalise for high volatility
    vol_penalty = min(0.20, atr_pct * 0.02)
    raw_conf = min(0.95, 0.40 + abs(score) * 1.1)
    confidence = round(max(0.10, raw_conf - vol_penalty), 2)

    # ── Stars (1–5) ──────────────────────────────────────────
    stars = max(1, min(5, round(confidence * 5)))

    trend_label = "UPTREND" if ma_diff_pct > 0 else "DOWNTREND"

    return SignalResult(
        symbol=symbol,
        signal=signal,
        confidence=confidence,
        signal_strength=stars,
        price=current_price,
        indicators={
            "rsi": round(rsi, 1),
            "ma20": round(ma20, 2),
            "ma50": round(ma50, 2),
            "trend": trend_label,
            "roc10_pct": round(roc10, 2),
            "atr_pct": round(atr_pct, 2),
            "volume_ratio": round(vol_ratio, 2),
            "score": round(score, 3),
        },
    )
