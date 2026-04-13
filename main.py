from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import requests
import os
import yfinance as yf
import redis
import json
import pandas as pd
import time

app = FastAPI(
    title="Stock Edge API",
    description="Real-time stock signals with RSI, trend, and confidence scoring.",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ENV ---
AV_KEY         = os.getenv("AV_KEY")
TWELVEDATA_KEY = os.getenv("TWELVEDATA_KEY")
FINNHUB_KEY    = os.getenv("FINNHUB_KEY")
REDIS_URL      = os.getenv("REDIS_URL")
API_KEYS       = [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]
RATE_LIMIT     = int(os.getenv("RATE_LIMIT", "100"))
DEV_KEY        = os.getenv("DEV_KEY")  # Your private test key

# --- REDIS ---
try:
    if REDIS_URL and REDIS_URL.startswith(("redis://", "rediss://")):
        redis_client = redis.Redis.from_url(REDIS_URL)
    else:
        print("⚠️ Redis disabled")
        redis_client = None
except Exception as e:
    print("⚠️ Redis init failed:", e)
    redis_client = None

# -----------------------------
# AUTH
# -----------------------------
def validate_request(request: Request, api_key: str = None) -> bool:
    # ✅ X-API-Key header (RapidAPI injects this via Gateway Secret Headers)
    header_key = request.headers.get("X-API-Key")
    if header_key and header_key in API_KEYS:
        return True
    # ✅ Query param ?api_key=
    if api_key and api_key in API_KEYS:
        return True
    # ✅ Your own dev/test key
    if DEV_KEY and (api_key == DEV_KEY or header_key == DEV_KEY):
        return True
    return False

# -----------------------------
# RATE LIMIT
# -----------------------------
def get_identifier(request: Request, api_key: str = None) -> str:
    return request.headers.get("X-RapidAPI-User") or api_key or "anonymous"

def check_rate_limit(identifier: str) -> bool:
    if not redis_client:
        return True
    key = f"rate:{identifier}"
    try:
        count = redis_client.get(key)
        if count and int(count) >= RATE_LIMIT:
            return False
        redis_client.incr(key)
        redis_client.expire(key, 60)
        return True
    except:
        return True

# -----------------------------
# REDIS CACHE
# -----------------------------
def get_cached(key):
    if not redis_client:
        return None
    try:
        data = redis_client.get(key)
        return json.loads(data) if data else None
    except:
        return None

def set_cache(key, data, ttl=120):
    if not redis_client:
        return
    try:
        redis_client.setex(key, ttl, json.dumps(data))
    except:
        pass

# -----------------------------
# PRICE FETCHERS
# -----------------------------
def get_price_twelvedata(symbol):
    if not TWELVEDATA_KEY:
        return None
    try:
        url  = f"https://api.twelvedata.com/price?symbol={symbol}&apikey={TWELVEDATA_KEY}"
        resp = requests.get(url, timeout=5).json()
        price = resp.get("price")
        if price:
            return float(price)
    except:
        pass
    return None

def get_price_av(symbol):
    if not AV_KEY:
        return None
    try:
        url  = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={AV_KEY}"
        resp = requests.get(url, timeout=5).json()
        if "Note" in resp or "Error Message" in resp:
            return None
        quote     = resp.get("Global Quote", {})
        price_str = quote.get("05. price")
        if price_str and price_str != "0.0000":
            return float(price_str)
    except:
        pass
    return None

def get_price_yf(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except:
        pass
    return None

def get_price(symbol):
    price = get_price_twelvedata(symbol)
    if price:
        return price, "twelvedata"
    price = get_price_av(symbol)
    if price:
        return price, "alpha_vantage"
    time.sleep(1)
    price = get_price_yf(symbol)
    if price:
        return price, "yfinance"
    return None, None

# -----------------------------
# INDICATORS
# -----------------------------
def get_indicators(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="60d")
        if hist.empty or len(hist) < 14:
            return None

        close  = hist["Close"]
        volume = hist["Volume"]
        high   = hist["High"]
        low    = hist["Low"]

        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = -delta.clip(upper=0).rolling(14).mean()
        rs    = gain / loss
        rsi   = float((100 - (100 / (1 + rs))).iloc[-1])

        # Moving averages
        ma20  = float(close.rolling(20).mean().iloc[-1])
        ma50  = float(close.rolling(50).mean().iloc[-1]) if len(hist) >= 50 else ma20
        trend = "UPTREND" if ma20 > ma50 else "DOWNTREND"

        # ROC 10
        roc10 = float(((close.iloc[-1] - close.iloc[-11]) / close.iloc[-11]) * 100) if len(hist) >= 11 else 0.0

        # ATR %
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr     = float(tr.rolling(14).mean().iloc[-1])
        atr_pct = round((atr / close.iloc[-1]) * 100, 2)

        # Volume ratio
        avg_vol   = volume.rolling(20).mean().iloc[-1]
        vol_ratio = round(float(volume.iloc[-1] / avg_vol), 2) if avg_vol > 0 else 1.0

        # Composite score (-1 to 1)
        score  = 0.0
        score += (50 - rsi) / 100
        score += 0.2 if trend == "UPTREND" else -0.2
        score += min(max(roc10 / 100, -0.3), 0.3)
        score  = round(score, 3)

        return {
            "rsi":          round(rsi, 1),
            "ma20":         round(ma20, 2),
            "ma50":         round(ma50, 2),
            "trend":        trend,
            "roc10_pct":    round(roc10, 2),
            "atr_pct":      atr_pct,
            "volume_ratio": vol_ratio,
            "score":        score
        }
    except Exception as e:
        print("INDICATOR ERROR:", e)
        return None

# -----------------------------
# SIGNAL LOGIC
# -----------------------------
def calculate_signal(indicators):
    if not indicators:
        return "HOLD", 0.5, 2

    rsi   = indicators["rsi"]
    trend = indicators["trend"]
    score = indicators["score"]
    roc10 = indicators["roc10_pct"]

    if rsi < 30 and trend == "UPTREND":
        signal, strength = "BUY", 5
    elif rsi < 40 and trend == "UPTREND" and roc10 > 0:
        signal, strength = "BUY", 4
    elif rsi > 70 and trend == "DOWNTREND":
        signal, strength = "SELL", 5
    elif rsi > 60 and trend == "DOWNTREND" and roc10 < 0:
        signal, strength = "SELL", 4
    elif score > 0.2:
        signal, strength = "BUY", 3
    elif score < -0.2:
        signal, strength = "SELL", 3
    else:
        signal, strength = "HOLD", 2

    confidence = round(min(0.5 + abs(score), 0.95), 2)
    return signal, confidence, strength

# -----------------------------
# ROOT
# -----------------------------
@app.get("/")
def root():
    return {
        "status": "LIVE",
        "version": "2.0.0",
        "endpoints": {
            "price":  "/stock-edge/{symbol}",
            "signal": "/signal/{symbol}  (requires X-API-Key header or ?api_key=)",
            "batch":  "POST /signals/batch  (requires X-API-Key header or ?api_key=)",
            "stats":  "/signal-performance/{symbol}  (requires X-API-Key header or ?api_key=)",
            "docs":   "/docs"
        }
    }

# -----------------------------
# PRICE ENDPOINT (public)
# -----------------------------
@app.get("/stock-edge/{symbol}")
async def edge(symbol: str):
    symbol = symbol.upper()
    price, source = get_price(symbol)
    if price is None:
        return {
            "error":  "No data available",
            "symbol": symbol,
            "debug":  {"twelvedata_key": bool(TWELVEDATA_KEY), "av_key": bool(AV_KEY)}
        }
    return {"symbol": symbol, "price": round(price, 2), "source": source}

# -----------------------------
# SIGNAL ENDPOINT (protected)
# -----------------------------
@app.get("/signal/{symbol}")
async def signal(symbol: str, request: Request, api_key: str = None):
    if not validate_request(request, api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    identifier = get_identifier(request, api_key)
    if not check_rate_limit(identifier):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    symbol    = symbol.upper()
    cache_key = f"signal:{symbol}"
    cached    = get_cached(cache_key)
    if cached:
        return {**cached, "cached": True}

    price, source = get_price(symbol)
    if price is None:
        return {"error": "No data", "symbol": symbol}

    indicators                         = get_indicators(symbol)
    signal_value, confidence, strength = calculate_signal(indicators)

    response = {
        "symbol":          symbol,
        "signal":          signal_value,
        "confidence":      confidence,
        "signal_strength": strength,
        "price":           round(price, 2),
        "indicators":      indicators,
        "data_source":     source,
        "cached":          False
    }

    set_cache(cache_key, response, ttl=120)
    return response

# -----------------------------
# BATCH ENDPOINT (protected)
# -----------------------------
class BatchRequest(BaseModel):
    symbols: List[str]

@app.post("/signals/batch")
async def batch_signals(body: BatchRequest, request: Request, api_key: str = None):
    if not validate_request(request, api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    identifier = get_identifier(request, api_key)
    if not check_rate_limit(identifier):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if len(body.symbols) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 symbols per batch")

    results = []
    for sym in body.symbols:
        sym       = sym.upper()
        cache_key = f"signal:{sym}"
        cached    = get_cached(cache_key)
        if cached:
            results.append({**cached, "cached": True})
            continue

        price, source = get_price(sym)
        if price is None:
            results.append({"symbol": sym, "error": "No data"})
            continue

        indicators                         = get_indicators(sym)
        signal_value, confidence, strength = calculate_signal(indicators)

        result = {
            "symbol":          sym,
            "signal":          signal_value,
            "confidence":      confidence,
            "signal_strength": strength,
            "price":           round(price, 2),
            "indicators":      indicators,
            "data_source":     source,
            "cached":          False
        }
        set_cache(cache_key, result, ttl=120)
        results.append(result)

    return {"results": results, "count": len(results)}

# -----------------------------
# SIGNAL PERFORMANCE STATS (protected)
# -----------------------------
@app.get("/signal-performance/{symbol}")
async def signal_performance(symbol: str, request: Request, api_key: str = None):
    if not validate_request(request, api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    symbol = symbol.upper()
    try:
        ticker  = yf.Ticker(symbol)
        hist    = ticker.history(period="90d")
        if hist.empty:
            return {"error": "No data", "symbol": symbol}

        close   = hist["Close"]
        returns = close.pct_change().dropna()

        return {
            "symbol":               symbol,
            "period":               "90d",
            "avg_daily_return_pct": round(float(returns.mean()) * 100, 3),
            "volatility_pct":       round(float(returns.std()) * 100, 3),
            "max_drawdown_pct":     round(float((close / close.cummax() - 1).min()) * 100, 2),
            "total_return_pct":     round(float((close.iloc[-1] / close.iloc[0] - 1) * 100), 2),
            "data_points":          len(hist)
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}
