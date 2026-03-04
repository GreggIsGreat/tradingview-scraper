"""
FastAPI application — REST API + web dashboard.
All endpoints return data directly — no storage.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings, RANGE_MINUTES, TIMEFRAME_MINUTES
from .models import (
    PriceResponse,
    CandleResponse,
    CandleOut,
    CandleRequest,
    BatchPriceRequest,
    BatchCandleRequest,
    SubscribeRequest,
    SymbolSearchResult,
    HealthResponse,
)
from . import engine
from .symbols import search_symbols, resolve_input

logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.start_client()
    yield
    engine.stop_client()


app = FastAPI(
    title="TradingView Data Service",
    version="2.1.0",
    description="Real-time price & OHLCV candle data from TradingView.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ──────────────────────────────────────────────────

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
        o=c.open,
        h=c.high,
        l=c.low,
        c=c.close,
        v=c.volume,
    )


# ── Dashboard ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    cfg = get_settings()
    return TEMPLATES.TemplateResponse("index.html", {
        "request": request,
        "symbols": cfg.auto_subscribe_symbols,
        "ranges": list(RANGE_MINUTES.keys()),
        "timeframes": list(TIMEFRAME_MINUTES.keys()),
    })


# ── GET /api/price ───────────────────────────────────────────

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


# ── GET /api/candles ─────────────────────────────────────────

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
            symbol=resolved,
            timeframe=timeframe,
            range=range,
            count=len(candles),
            candles=[_candle_out(c) for c in candles],
        )
    except (TimeoutError, ConnectionError) as exc:
        raise HTTPException(408, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error("candles error: %s", exc, exc_info=True)
        raise HTTPException(500, str(exc))


# ── POST /api/candles ────────────────────────────────────────

@app.post("/api/candles")
async def post_candles(req: CandleRequest):
    try:
        resolved, candles = await asyncio.to_thread(
            engine.fetch_candles, req.symbol, req.timeframe, req.range, req.bars,
        )
        return CandleResponse(
            symbol=resolved,
            timeframe=req.timeframe,
            range=req.range,
            count=len(candles),
            candles=[_candle_out(c) for c in candles],
        )
    except (TimeoutError, ConnectionError) as exc:
        raise HTTPException(408, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── POST /api/batch/prices ───────────────────────────────────

@app.post("/api/batch/prices")
async def batch_prices(req: BatchPriceRequest):
    results = {}
    for sym in req.symbols:
        try:
            resolved, q = await asyncio.to_thread(engine.fetch_price, sym)
            results[resolved] = _quote_to_price(resolved, q)
        except Exception as exc:
            results[sym] = f"error: {exc}"
    return {"prices": results}


# ── POST /api/batch/candles ──────────────────────────────────

@app.post("/api/batch/candles")
async def batch_candles(req: BatchCandleRequest):
    results = []
    for r in req.requests:
        try:
            resolved, candles = await asyncio.to_thread(
                engine.fetch_candles, r.symbol, r.timeframe, r.range, r.bars,
            )
            results.append(CandleResponse(
                symbol=resolved,
                timeframe=r.timeframe,
                range=r.range,
                count=len(candles),
                candles=[_candle_out(c) for c in candles],
            ))
        except Exception as exc:
            results.append({"symbol": r.symbol, "error": str(exc)})
    return {"results": results}


# ── POST /api/subscribe ─────────────────────────────────────

@app.post("/api/subscribe")
async def subscribe_symbols(req: SubscribeRequest):
    client = engine.get_client()
    subscribed = []
    for sym in req.symbols:
        try:
            resolved = resolve_input(sym)
            await asyncio.to_thread(client.subscribe_quote, resolved)
            subscribed.append(resolved)
        except Exception as exc:
            subscribed.append(f"{sym}: error — {exc}")
    return {"subscribed": subscribed}


# ── GET /api/subscriptions ───────────────────────────────────

@app.get("/api/subscriptions")
async def list_subscriptions():
    client = engine.get_client()
    return {"symbols": client.subscribed_symbols}


# ── Symbol search ────────────────────────────────────────────

@app.get("/api/symbols/search", response_model=list[SymbolSearchResult])
async def symbol_search(q: str = Query(..., min_length=1)):
    return await asyncio.to_thread(search_symbols, q)


# ── Health ───────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
async def health():
    client = engine.get_client()
    return HealthResponse(
        status="ok" if client.is_connected else "degraded",
        ws_connected=client.is_connected,
        subscribed_symbols=client.subscribed_symbols,
        cached_quotes=client.cached_quote_count,
    )