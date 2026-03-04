"""
Engine layer — stateless, no persistent client.
Every call opens a fresh WebSocket, gets data, closes.
Works on Vercel, locally, anywhere.
"""
from __future__ import annotations

import logging
import time

from .config import get_settings, calculate_bars
from .models import Candle
from .tv_ws import fetch_quote as _ws_fetch_quote, fetch_candles as _ws_fetch_candles
from .symbols import resolve_input

logger = logging.getLogger(__name__)


def fetch_price(symbol_input: str) -> tuple[str, dict]:
    """Resolve symbol → open WS → get quote → close WS → return."""
    resolved = resolve_input(symbol_input)
    data = _ws_fetch_quote(resolved)
    return resolved, data


def fetch_candles(
    symbol_input: str,
    timeframe: str,
    range_key: str,
    custom_bars: int | None = None,
) -> tuple[str, list[Candle]]:
    """Resolve symbol → open WS → get candles → close WS → return."""
    cfg = get_settings()
    resolved = resolve_input(symbol_input)

    if custom_bars:
        bars = custom_bars
    else:
        bars = calculate_bars(range_key, timeframe)
    bars = min(bars, cfg.max_bars_per_request)

    candles = _ws_fetch_candles(resolved, timeframe, bars)
    return resolved, candles


def fetch_multi_timeframe(
    symbol_input: str,
    timeframes: list[str],
    range_key: str,
    custom_bars: int | None = None,
) -> tuple[str, dict[str, list[Candle]]]:
    """Fetch candles for multiple timeframes (one WS connection each)."""
    cfg = get_settings()
    resolved = resolve_input(symbol_input)
    result: dict[str, list[Candle]] = {}

    for i, tf in enumerate(timeframes):
        if custom_bars:
            bars = custom_bars
        else:
            bars = calculate_bars(range_key, tf)
        bars = min(bars, cfg.max_bars_per_request)

        candles = _ws_fetch_candles(resolved, tf, bars)
        result[tf] = candles

        if i < len(timeframes) - 1:
            time.sleep(cfg.request_delay)

    return resolved, result