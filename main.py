from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import os, json, time
import redis

app = FastAPI(title="Stock Edge API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

analyzer = SentimentIntensityAnalyzer()
r = None
try:
    r = redis.from_url(os.getenv("REDIS_URL"))
except:
    pass

@app.get("/")
def root():
    return {"status": "LIVE", "endpoints": ["/stock-edge/AAPL", "/stock-edge/BTC-USD"]}

@app.get("/stock-edge/{symbol}")
async def edge(symbol: str):
    symbol = symbol.upper()
    cache_key = f"edge:{symbol}"
    
    if r:
        cached = r.get(cache_key)
        if cached:
            return json.loads(cached)
    
    symbols_to_try = [symbol, symbol.replace("=", "-"), f"{symbol}=X"]
    
    for sym in symbols_to_try:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="5d", timeout=10)
            
            if len(hist) > 0:
                info = ticker.info
                news = ticker.news[:3]
                
                sentiment = 0
                if news:
                    sentiment = sum(analyzer.polarity_scores(n.get('title', ''))['compound'] for n in news) / len(news)
                
                latest = hist.iloc[-1]
                change = 0
                if len(hist) > 1:
                    change = ((latest['Close'] - hist['Close'].iloc[-2]) / hist['Close'].iloc[-2]) * 100
                
                result = {
                    "symbol": symbol,
                    "company": info.get('longName', symbol),
                    "price": round(float(latest['Close']), 2),
                    "change_pct": round(change, 2),
                    "sentiment": round(sentiment * 100, 1),
                    "volume": int(latest['Volume']),
                    "volume_spike": latest['Volume'] > hist['Volume'].mean(),
                    "recommendation": "BUY" if sentiment > 0.1 else ("SELL" if sentiment < -0.1 else "HOLD")
                }
                
                if r:
                    r.setex(cache_key, 600, json.dumps(result))
                
                return result
                
        except:
            continue
    
    return {"error": "No data found", "tried": symbols_to_try, "status": "Try SPY or QQQ"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
