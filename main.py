from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import json
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import os
from typing import Optional

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

analyzer = SentimentIntensityAnalyzer()

# FREE Alpha Vantage (sign up: alphavantage.co - 25 calls/min)
AV_KEY = os.getenv("AV_KEY", "demo")  # Add later

@app.get("/")
def root():
    return {"status": "LIVE", "demo": "/stock-edge/AAPL"}

@app.get("/stock-edge/{symbol}")
async def edge(symbol: str):
    symbol = symbol.upper()
    
    try:
        # Alpha Vantage (more reliable)
        if AV_KEY != "demo":
            url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={AV_KEY}"
            resp = requests.get(url, timeout=5).json()
            price = float(resp['Global Quote']['05. price'])
        else:
            # Yahoo fallback with headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            ticker = yf.Ticker(symbol, session=requests.Session())
            hist = ticker.history(period="2d", headers=headers)
            
            if hist.empty:
                return {"error": "No data", "symbol": symbol}
            
            price = float(hist['Close'].iloc[-1])
        
        # News sentiment (FinancialModelingPrep free)
        news_url = f"https://financialmodelingprep.com/api/v3/stock_news?tickers={symbol}&limit=3&apikey=demo"
        news = requests.get(news_url).json()
        sentiment = sum(analyzer.polarity_scores(n['title'])['compound'] for n in news[:3]) / 3
        
        return {
            "symbol": symbol,
            "price": round(price, 2),
            "sentiment": round(sentiment * 100, 1),
            "recommendation": "BUY" if sentiment > 0.1 else "SELL"
        }
        
    except Exception as e:
        return {"error": str(e), "status": "service ready"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0"
