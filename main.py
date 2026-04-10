from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import os
import yfinance as yf

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

analyzer = SentimentIntensityAnalyzer()
AV_KEY = os.getenv("AV_KEY")

@app.get("/")
def root():
    return {"status": "LIVE", "demo": "/stock-edge/AAPL"}

def get_price_yf(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2d")

        if hist.empty:
            return None

        return float(hist["Close"].iloc[-1])
    except:
        return None

def get_price_av(symbol):
    if not AV_KEY:
        return None

    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={AV_KEY}"
        resp = requests.get(url, timeout=5).json()

        if "Global Quote" not in resp:
            return None

        return float(resp["Global Quote"]["05. price"])
    except:
        return None

@app.get("/stock-edge/{symbol}")
async def edge(symbol: str):
    symbol = symbol.upper()

    price = get_price_yf(symbol)
    source = "yfinance"

    if price is None:
        price = get_price_av(symbol)
        source = "alpha_vantage"

    if price is None:
        return {"error": "No data available", "symbol": symbol}

    # Sentiment
    try:
        news_url = f"https://financialmodelingprep.com/api/v3/stock_news?tickers={symbol}&limit=3&apikey=demo"
        news = requests.get(news_url, timeout=5).json()

        if isinstance(news, list) and len(news) > 0:
            sentiment = sum(
                analyzer.polarity_scores(n["title"])["compound"]
                for n in news[:3]
            ) / min(len(news), 3)
        else:
            sentiment = 0
    except:
        sentiment = 0

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "sentiment": round(sentiment * 100, 1),
        "recommendation": "BUY" if sentiment > 0.1 else "SELL",
        "source": source
    }
