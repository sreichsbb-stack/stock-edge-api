from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
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
AV_KEY              = os.getenv("AV_KEY")
REDIS_URL           = os.getenv("REDIS_URL")
RATE_LIMIT          = int(os.getenv("RATE_LIMIT", "100"))
RAPIDAPI_SECRET     = os.getenv("RAPIDAPI_PROXY_SECRET")       # 🔐 RapidAPI Proxy Secret
DEV_KEY             = os.getenv("DEV_KEY")                     # 🔧 Optional: your own test key

# --- INIT ---
analyzer = SentimentIntensityAnalyzer()

# --- REDIS ---
try:
    if REDIS_URL and REDIS_URL.startswith(("redis://", "rediss://")):
        redis_client = redis.Redis.from_url(REDIS_URL)
    else:
        print("⚠️ Redis disabled (invalid URL)")
        redis_client = None
except Exception as e:
    print("⚠️ Redis init failed:", e)
    redis_client = None

# -----------------------------
# AUTH
# -----------------------------
def validate_request(request: Request, api_key: str = None) -> bool:
    """
    Accept requests from:
    1. RapidAPI gateway (X-RapidAPI-Proxy-Secret header)
    2. Your own dev/test key (DEV_KEY env var) — for curl testing
    """
    # ✅ RapidAPI proxy secret (all real users come through here)
    incoming_secret = request.headers.get("X-RapidAPI-Proxy-Secret")
    if RAPIDAPI_SECRET and incoming_secret == RAPIDAPI_SECRET:
        return True

    # ✅ Dev key fallback (for your own testing only)
    if DEV_KEY and api_key == DEV_KEY:
        return True

    return False

# -----------------------------
# RATE LIMIT
# -----------------------------
def get_rapidapi_key(request: Request) -> str:
    """Use the RapidAPI user key as the rate limit identifier."""
    return request.headers.get("X-RapidAPI-User", "anonymous")

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
        if data:
            return json.loads(data)
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
def get_price_av(symbol):
    if not AV_KEY:
        return None
    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={AV_KEY}"
        resp = requests.get(url, timeout=5).json()
        if "Note" in resp or "Error Message" in resp:
            return None
        quote = resp.get("Global Quote", {})
        price_str = quote.get("05. price")
        if not price_str or price_str == "0.0000":
            return None
        return float(price_str)
    except Exception as e:
        print("AV ERROR:", e)
        return None

def get_price_yf(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        print("YF ERROR:", e)
        return None

# -----------------------------
# INDICATORS
# -----------------------------
def calculate_rsi(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="14d")
        if hist.empty:
            return None
        delta = hist["Close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = -delta.clip(upper=0).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        value = rsi.iloc[-1]
        if pd.isna(value):
            return None
        return float(value)
    except:
        return None

def get_trend(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="50d")
        if hist.empty:
            return "UNKNOWN"
        ma20 = hist["Close"].rolling(20).mean().iloc[-1]
        ma50 = hist["Close"].rolling(50).mean().iloc[-1]
        return "UPTREND" if ma20 > ma50 else "DOWNTREND"
    except:
        return "UNKNOWN"

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
            "signal": "/signal/{symbol}  (requires X-RapidAPI-Key via RapidAPI)",
            "docs":   "/docs"
        }
    }

# -----------------------------
# PRICE ENDPOINT (public)
# -----------------------------
@app.get("/stock-edge/{symbol}")
async def edge(symbol: str):
    symbol = symbol.upper()
    price = None
    source = None

    for attempt in range(2):
        price = get_price_av(symbol)
        if price:
            source = "alpha_vantage"
            break
        print(f"AV attempt {attempt + 1} failed")
        time.sleep(1)

    if price is None:
        price = get_price_yf(symbol)
        if price:
            source = "yfinance"

    if price is None:
        return {
            "error": "No data available",
            "symbol": symbol,
            "debug": {
                "av_key_present": bool(AV_KEY),
                "note": "Alpha Vantage rate limit or Yahoo blocked"
            }
        }

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "source": source
    }

# -----------------------------
# SIGNAL ENDPOINT (protected)
# -----------------------------
@app.get("/signal/{symbol}")
async def signal(symbol: str, request: Request, api_key: str = None):

    # 🔐 AUTH — RapidAPI proxy secret or dev key
    if not validate_request(request, api_key):
        raise HTTPException(status_code=401, detail="Unauthorized. Subscribe via RapidAPI to access this endpoint.")

    # 🚫 RATE LIMIT (per RapidAPI user)
    identifier = get_rapidapi_key(request)
    if not check_rate_limit(identifier):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Upgrade your plan on RapidAPI.")

    symbol = symbol.upper()
    cache_key = f"signal:{symbol}"

    # 🔥 CACHE
    cached = get_cached(cache_key)
    if cached:
        return {**cached, "cached": True}

    # 🔥 PRICE
    price = get_price_av(symbol)
    source = "alpha_vantage"
    if price is None:
        time.sleep(1)
        price = get_price_av(symbol)
    if price is None:
        price = get_price_yf(symbol)
        source = "yfinance"
    if price is None:
        return {"error": "No data", "symbol": symbol}

    # 🔥 INDICATORS
    rsi = calculate_rsi(symbol)
    trend = get_trend(symbol)

    # 🔥 SIGNAL LOGIC
    signal_value = "HOLD"
    confidence = 50

    if rsi:
        if rsi < 30 and trend == "UPTREND":
            signal_value = "BUY"
            confidence = 80
        elif rsi > 70 and trend == "DOWNTREND":
            signal_value = "SELL"
            confidence = 80
        elif rsi > 60:
            signal_value = "SELL"
            confidence = 65
        elif rsi < 40:
            signal_value = "BUY"
            confidence = 65

    response = {
        "symbol": symbol,
        "price": round(price, 2),
        "rsi": round(rsi, 2) if rsi else None,
        "trend": trend,
        "signal": signal_value,
        "confidence": confidence,
        "source": source
    }

    set_cache(cache_key, response, ttl=120)
    return response
