from __future__ import annotations
from typing import Optional
from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field


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


class CandleRequest(BaseModel):
    symbol: str = Field(...)
    timeframe: str = Field(default="1")
    range: str = Field(default="1h")
    bars: Optional[int] = Field(default=None)


class BatchPriceRequest(BaseModel):
    symbols: list[str]


class BatchCandleRequest(BaseModel):
    requests: list[CandleRequest]


class SubscribeRequest(BaseModel):
    symbols: list[str]


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
    t: float
    dt: str
    o: float
    h: float
    l: float
    c: float
    v: float


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
    mode: str
    subscribed_symbols: list[str]