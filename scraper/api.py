"""
FastAPI app — works locally (uvicorn) and on Vercel (mangum).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings, RANGE_MINUTES, TIMEFRAME_MINUTES
from .models import (
    PriceResponse, CandleResponse, CandleOut, CandleRequest,
    BatchPriceRequest, BatchCandleRequest, SymbolSearchResult, HealthResponse,
)
from . import engine
from .symbols import search_symbols

logger = logging.getLogger(__name__)

# ── Resolve paths that work both locally and on Vercel ──
_THIS_DIR = Path(__file__).resolve().parent          # scraper/
_PROJECT_ROOT = _THIS_DIR.parent                      # tradingview-scraper/

# Try multiple possible template locations
_TEMPLATE_DIRS = [
    _PROJECT_ROOT / "templates",
    _THIS_DIR / "templates",
    Path(os.getcwd()) / "templates",
]

_TEMPLATE_DIR = None
for d in _TEMPLATE_DIRS:
    if d.exists() and (d / "index.html").exists():
        _TEMPLATE_DIR = d
        break

if _TEMPLATE_DIR is None:
    # Fallback: create inline template
    _TEMPLATE_DIR = _PROJECT_ROOT / "templates"
    _TEMPLATE_DIR.mkdir(exist_ok=True)

TEMPLATES = Jinja2Templates(directory=str(_TEMPLATE_DIR))

app = FastAPI(
    title="TradingView Data Service",
    version="2.2.0",
    description="Real-time price & OHLCV candle data from TradingView. Serverless compatible.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _quote_to_price(symbol: str, q: dict) -> PriceResponse:
    session = q.get("current_session", "")
    return PriceResponse(
        symbol=symbol,
        price=q.get("lp"),
        open=q.get("open_price"),
        high=q.get("high_price"),
        low=q.get("low_price"),
        close=q.get("lp"),
        prev_close=q.get("prev_close_price"),
        change=q.get("ch"),
        change_percent=q.get("chp"),
        volume=q.get("volume"),
        bid=q.get("bid"),
        ask=q.get("ask"),
        timestamp=q.get("lp_time"),
        description=q.get("description"),
        exchange=q.get("exchange"),
        currency=q.get("currency_code"),
        type=q.get("type"),
        is_market_open=session == "market" if session else None,
    )


def _candle_out(c) -> CandleOut:
    return CandleOut(
        t=c.timestamp,
        dt=dt.datetime.utcfromtimestamp(c.timestamp).isoformat() + "Z",
        o=c.open, h=c.high, l=c.low, c=c.close, v=c.volume,
    )


# ── Dashboard ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    cfg = get_settings()
    try:
        return TEMPLATES.TemplateResponse("index.html", {
            "request": request,
            "symbols": cfg.auto_subscribe_symbols,
            "ranges": list(RANGE_MINUTES.keys()),
            "timeframes": list(TIMEFRAME_MINUTES.keys()),
        })
    except Exception as exc:
        # If template not found, return a simple working page
        logger.error("Template error: %s", exc)
        symbols = cfg.auto_subscribe_symbols
        return HTMLResponse(f"""
        <html><head><title>TV Data Service</title></head>
        <body style="background:#0b0d11;color:#e0e0e0;font-family:sans-serif;padding:40px;text-align:center">
        <h1>📊 TradingView Data Service</h1>
        <p>API is running. Template not found.</p>
        <h3>Endpoints:</h3>
        <p><a href="/docs" style="color:#2962ff">/docs</a> — Swagger UI</p>
        <p><a href="/api/health" style="color:#2962ff">/api/health</a></p>
        <p><a href="/api/price?symbol={symbols[0]}" style="color:#2962ff">/api/price?symbol={symbols[0]}</a></p>
        </body></html>
        """)


# ── Price ────────────────────────────────────────────────────

@app.get("/api/price", response_model=PriceResponse)
async def get_price(symbol: str = Query(...)):
    try:
        resolved, q = await asyncio.to_thread(engine.fetch_price, symbol)
        return _quote_to_price(resolved, q)
    except TimeoutError as exc:
        raise HTTPException(408, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error("price error: %s", exc, exc_info=True)
        raise HTTPException(500, str(exc))


# ── Candles ──────────────────────────────────────────────────

@app.get("/api/candles", response_model=CandleResponse)
async def get_candles(
    symbol: str = Query(...),
    timeframe: str = Query("1"),
    range: str = Query("1h"),
    bars: int | None = Query(None, ge=1, le=50000),
):
    try:
        resolved, candles = await asyncio.to_thread(
            engine.fetch_candles, symbol, timeframe, range, bars,
        )
        return CandleResponse(
            symbol=resolved, timeframe=timeframe, range=range,
            count=len(candles), candles=[_candle_out(c) for c in candles],
        )
    except (TimeoutError, ConnectionError) as exc:
        raise HTTPException(408, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error("candles error: %s", exc, exc_info=True)
        raise HTTPException(500, str(exc))


@app.post("/api/candles")
async def post_candles(req: CandleRequest):
    try:
        resolved, candles = await asyncio.to_thread(
            engine.fetch_candles, req.symbol, req.timeframe, req.range, req.bars,
        )
        return CandleResponse(
            symbol=resolved, timeframe=req.timeframe, range=req.range,
            count=len(candles), candles=[_candle_out(c) for c in candles],
        )
    except (TimeoutError, ConnectionError) as exc:
        raise HTTPException(408, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Batch ────────────────────────────────────────────────────

@app.post("/api/batch/prices")
async def batch_prices(req: BatchPriceRequest):
    results = {}
    for sym in req.symbols:
        try:
            resolved, q = await asyncio.to_thread(engine.fetch_price, sym)
            results[resolved] = _quote_to_price(resolved, q)
        except Exception as exc:
            results[sym] = {"error": str(exc)}
    return {"prices": results}


@app.post("/api/batch/candles")
async def batch_candles(req: BatchCandleRequest):
    results = []
    for r in req.requests:
        try:
            resolved, candles = await asyncio.to_thread(
                engine.fetch_candles, r.symbol, r.timeframe, r.range, r.bars,
            )
            results.append(CandleResponse(
                symbol=resolved, timeframe=r.timeframe, range=r.range,
                count=len(candles), candles=[_candle_out(c) for c in candles],
            ))
        except Exception as exc:
            results.append({"symbol": r.symbol, "error": str(exc)})
    return {"results": results}


# ── Search + Health ──────────────────────────────────────────

@app.get("/api/symbols/search", response_model=list[SymbolSearchResult])
async def symbol_search(q: str = Query(..., min_length=1)):
    return await asyncio.to_thread(search_symbols, q)


@app.get("/api/health", response_model=HealthResponse)
async def health():
    cfg = get_settings()
    return HealthResponse(
        status="ok",
        mode="serverless",
        subscribed_symbols=cfg.auto_subscribe_symbols,
    )