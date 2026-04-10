from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import os, json, time, redis
from typing import Optional

app = FastAPI(title="Stock Edge API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

analyzer = SentimentIntensityAnalyzer()
r = None
try:
    r = redis.from_url(os.getenv("REDIS_URL"))
except:
    print("No Redis - running uncached")

@app.get("/")
async def root():
    return {"live": True, "test": "/stock-edge/AAPL?api_key=demo"}

@app.get("/stock-edge/{symbol}")
async def edge(symbol: str, api_key: Optional[str] = "demo_key"):
    if api_key not in os.getenv("API_KEYS", "demo_key").split(","):
        raise HTTPException(401, "Invalid API key")
    
    symbol = symbol.upper()
    if r:
        cached = r.get(f"edge:{symbol}")
        if cached: return json.loads(cached)
    
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2d")
        news = ticker.news[:5]
        
        sentiment = sum([analyzer.polarity_scores(n['title'])['compound'] for n in news]) / 5
        latest = hist.iloc[-1]
        change = ((latest['Close'] - hist['Close'].iloc[-2]) / hist['Close'].iloc[-2]) * 100
        
        result = {
            "symbol": symbol,
            "price": round(latest['Close'], 2),
            "change": round(change, 2),
            "sentiment": round(sentiment * 100, 1),
            "volume_spike": latest['Volume'] > hist['Volume'].mean(),
            "rec": "BUY" if sentiment > 0.1 else "SELL"
        }
        
        if r: r.setex(f"edge:{symbol}", 300, json.dumps(result))
        return result
        
    except Exception as e:
        raise HTTPException(500, f"Error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
