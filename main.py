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

def get_price_av(symbol):
    if not AV_KEY:
        return None

    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={AV_KEY}"
        resp = requests.get(url, timeout=5).json()

        print("FULL AV RESPONSE:", resp)  # 👈 VERY IMPORTANT

        # ❌ Handle rate limit / errors
        if "Note" in resp or "Error Message" in resp:
            print("AV LIMIT OR ERROR")
            return None

        quote = resp.get("Global Quote", {})

        # ❌ Handle empty quote
        if not quote:
            print("EMPTY QUOTE")
            return None

        price_str = quote.get("05. price")

        # ❌ Handle missing price
        if not price_str or price_str == "0.0000":
            print("INVALID PRICE")
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


@app.get("/stock-edge/{symbol}")
async def edge(symbol: str):
    symbol = symbol.upper()

    # 🔥 PRIMARY: Alpha Vantage
    price = get_price_av(symbol)
    source = "alpha_vantage"

    # 🔁 FALLBACK: Yahoo
    if price is None:
        price = get_price_yf(symbol)
        source = "yfinance"

    if price is None:
        return {
            "error": "No data available",
            "symbol": symbol,
            "av_key_present": bool(AV_KEY)
        }

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "source": source
    }
