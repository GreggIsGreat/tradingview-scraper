from __future__ import annotations
from typing import Optional
from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field


# ── Internal ─────────────────────────────────────────────────

@dataclass
class Candle:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict:
        return asdict(self)


# ── API Requests ─────────────────────────────────────────────

class CandleRequest(BaseModel):
    symbol: str = Field(..., description="e.g. OANDA:XAUUSD, BTCUSD, or TradingView URL")
    timeframe: str = Field(default="1", description="1, 5, 15, 60, D …")
    range: str = Field(default="1h", description="1h, 4h, 1d, 1w …")
    bars: Optional[int] = Field(default=None, description="Override: exact bar count")


class BatchPriceRequest(BaseModel):
    symbols: list[str]


class BatchCandleRequest(BaseModel):
    requests: list[CandleRequest]


class SubscribeRequest(BaseModel):
    symbols: list[str]


# ── API Responses ────────────────────────────────────────────

class PriceResponse(BaseModel):
    symbol: str
    price: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    prev_close: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    timestamp: Optional[float] = None
    description: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None
    type: Optional[str] = None
    is_market_open: Optional[bool] = None


class CandleOut(BaseModel):
    t: float           # timestamp
    dt: str            # ISO datetime
    o: float           # open
    h: float           # high
    l: float           # low
    c: float           # close
    v: float           # volume


class CandleResponse(BaseModel):
    symbol: str
    timeframe: str
    range: str
    count: int
    candles: list[CandleOut]


class SymbolSearchResult(BaseModel):
    symbol: str
    description: str
    exchange: str
    type: str
    full_name: str


class HealthResponse(BaseModel):
    status: str
    ws_connected: bool
    subscribed_symbols: list[str]
    cached_quotes: int