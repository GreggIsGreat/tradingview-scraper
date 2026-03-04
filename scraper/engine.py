"""
Global TvClient instance + high-level fetch functions.
"""
from __future__ import annotations

import logging
import time

from .config import get_settings, calculate_bars
from .models import Candle
from .tv_ws import TvClient
from .symbols import resolve_input

logger = logging.getLogger(__name__)

_client: TvClient | None = None


def get_client() -> TvClient:
    if _client is None:
        raise RuntimeError("Client not started. Call start_client() first.")
    return _client


def start_client() -> None:
    global _client
    cfg = get_settings()
    _client = TvClient(auth_token=cfg.tv_auth_token)
    _client.start()

    # auto-subscribe configured symbols
    # subscribe_quote() is safe to call — it checks _qs_sent internally
    for sym in cfg.auto_subscribe_symbols:
        try:
            _client.subscribe_quote(sym)
        except Exception as exc:
            logger.warning("Auto-subscribe failed for %s: %s", sym, exc)

    logger.info(
        "Engine started. Auto-subscribed: %s",
        cfg.auto_subscribe_symbols,
    )


def stop_client() -> None:
    global _client
    if _client:
        _client.stop()
        _client = None


def fetch_price(symbol_input: str) -> tuple[str, dict]:
    resolved = resolve_input(symbol_input)
    client = get_client()
    data = client.get_quote(resolved)
    return resolved, data


def fetch_candles(
    symbol_input: str,
    timeframe: str,
    range_key: str,
    custom_bars: int | None = None,
) -> tuple[str, list[Candle]]:
    cfg = get_settings()
    resolved = resolve_input(symbol_input)

    if custom_bars:
        bars = custom_bars
    else:
        bars = calculate_bars(range_key, timeframe)
    bars = min(bars, cfg.max_bars_per_request)

    client = get_client()
    candles = client.fetch_candles(resolved, timeframe, bars)
    return resolved, candles


def fetch_multi_timeframe(
    symbol_input: str,
    timeframes: list[str],
    range_key: str,
    custom_bars: int | None = None,
) -> tuple[str, dict[str, list[Candle]]]:
    cfg = get_settings()
    resolved = resolve_input(symbol_input)
    client = get_client()
    result: dict[str, list[Candle]] = {}

    for i, tf in enumerate(timeframes):
        if custom_bars:
            bars = custom_bars
        else:
            bars = calculate_bars(range_key, tf)
        bars = min(bars, cfg.max_bars_per_request)

        candles = client.fetch_candles(resolved, tf, bars)
        result[tf] = candles

        if i < len(timeframes) - 1:
            time.sleep(cfg.request_delay)

    return resolved, result