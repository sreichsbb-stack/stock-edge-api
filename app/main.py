from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import setup_logging
from app.routers import signal, stock

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    import logging
    logging.getLogger(__name__).info("Stock Edge API starting up")
    yield
    # Shutdown
    logging.getLogger(__name__).info("Stock Edge API shutting down")


app = FastAPI(
    title="Stock Edge API",
    description="Production-grade trading signals API with multi-provider fallback.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(stock.router, tags=["Price"])
app.include_router(signal.router, tags=["Signals"])


@app.get("/", tags=["Health"])
def root():
    return {
        "status": "LIVE",
        "version": "2.0.0",
        "endpoints": {
            "price": "/stock-edge/{symbol}",
            "signal": "/signal/{symbol}  (requires X-API-Key header or ?api_key=)",
            "batch":  "POST /signals/batch  (requires X-API-Key header or ?api_key=)",
            "docs":   "/docs",
        },
    }


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}
