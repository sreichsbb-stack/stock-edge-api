from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import redis
import os
import time
import json
from functools import lru_cache
import logging
from fastapi.middleware.cors import CORSMiddleware

# Config
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
API_KEYS = os.getenv("API_KEYS", "demo_key").split(",")
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "100"))  # calls/hour

app = FastAPI(title="Stock Edge API v2.0", version="2.0")
analyzer = SentimentIntensityAnalyzer()
redis_client = redis.from_url(REDIS_URL)
logging.basicConfig(level=logging.INFO)

# CORS for frontend
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class StockRequest(BaseModel):
    symbol: str
    period: Optional[str] = "1d"

class APIKeyHeader(Header):
    api_key: str = "demo_key"

# Rate limiting
user_requests = {}

def rate_limit_check(api_key: str):
    now = time.time()
    user_requests.setdefault(api_key, [])
    # Keep last hour
    user_requests[api_key] = [t for t in user_requests[api_key] if now - t < 3600]
    if len(user_requests[api_key]) >= RATE_LIMIT:
        raise HTTPException(429, "Rate limit exceeded")
    user_requests[api_key].append(now)

# Auth dependency
def verify_api_key(api_key: str = Depends(APIKeyHeader)):
    if api_key not in API_KEYS:
        raise HTTPException(401, "Invalid API key")
    rate_limit_check(api_key)
    return api_key

@app.get("/")
def root():
    return {"message": "Stock Edge API v2.0 LIVE", "docs": "/docs", "test": "/stock-edge/AAPL"}

@app.post("/stock-edge", dependencies=[Depends(verify_api_key)])
@app.get("/stock-edge/{symbol}")
async def get_edge(symbol: str, period: Optional[str] = "1d", api_key: str = Depends(verify_api_key)):
    symbol = symbol.upper()
    cache_key = f"edge:{symbol}:{period}"
    
    # Redis cache (5 min TTL)
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    
    try:
        stock = yf.Ticker(symbol)
        info = stock.info
        hist = stock.history(period=period)
        
        if hist.empty:
            raise HTTPException(404, f"No data for {symbol}")
        
        # News sentiment
        news = stock.news[:5]
        sentiments = [analyzer.polarity_scores(n.get('title', ''))['compound'] for n in news]
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0
        
        # LATEST: Price, volume, change
        latest = hist.iloc[-1]
        prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else latest['Close']
        price_change = (latest['Close'] - prev_close) / prev_close * 100
        
        volume_spike = latest['Volume'] > hist['Volume'].rolling(20).mean().iloc[-1]
        
        # OPTIONS + IV (NEW!)
        options_edge = {}
        try:
            expirations = stock.options
            if expirations:
                opt_chain = stock.option_chain(expirations[0])  # Nearest expiry
                calls = opt_chain.calls.head(1)
                if not calls.empty:
                    iv = calls['impliedVolatility'].iloc[0]
                    options_edge = {
                        "nearest_iv": round(iv * 100, 1),
                        "call_volume": int(calls['volume'].iloc[0]) if 'volume' in calls else 0
                    }
        except:
            pass  # Graceful fallback
        
        # RECOMMENDATION ENGINE
        score = avg_sentiment * 100
        if score > 15 and volume_spike and options_edge.get('nearest_iv', 0) < 50:
            rec = "STRONG_BUY"
        elif score > 0 and volume_spike:
            rec = "BUY"
        elif score > -10:
            rec = "HOLD"
        else:
            rec = "SELL"
        
        result = {
            "symbol": symbol,
            "price": round(latest['Close'], 2),
            "change_pct": round(price_change, 2),
            "sentiment_score": round(score, 1),
            "volume_spike": volume_spike,
            "recommendation": rec,
            "market_cap": info.get('marketCap', 0),
            "options": options_edge,
            "timestamp": int(time.time())
        }
        
        # Cache 5 mins
        redis_client.setex(cache_key, 300, json.dumps(result))
        
        # Analytics
        redis_client.incr(f"calls:{symbol}")
        
        logging.info(f"Edge served: {symbol} -> {rec}")
        return result
        
    except Exception as e:
        logging.error(f"Error {symbol}: {str(e)}")
        raise HTTPException(500, f"API error: {str(e)}")

@app.get("/analytics/top-symbols")
async def top_symbols(api_key: str = Depends(verify_api_key)):
    """RapidAPI analytics hook"""
    top = redis_client.zrevrange("symbol_rank", 0, 9, withscores=True)
    return [{"symbol": s.decode(), "calls": int(score)} for s, score in top]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
