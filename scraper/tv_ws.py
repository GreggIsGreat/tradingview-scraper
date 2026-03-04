"""
TradingView WebSocket client - serverless compatible.
Each call opens a fresh WS, gets data, closes.
"""
from __future__ import annotations

import json
import logging
import random
import string
import threading

import websocket

from .config import get_settings
from .models import Candle

logger = logging.getLogger(__name__)

QUOTE_FIELDS = [
    "lp", "ch", "chp",
    "open_price", "high_price", "low_price", "prev_close_price",
    "volume", "bid", "ask",
    "lp_time", "short_name", "description", "exchange",
    "type", "currency_code", "current_session", "status",
]


def _rand(prefix="cs_", n=12):
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _pack(payload):
    return "~m~" + str(len(payload)) + "~m~" + payload


def _msg(method, params):
    return _pack(json.dumps({"m": method, "p": params}, separators=(",", ":")))


def _split(raw):
    msgs = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i:i + 3] != "~m~":
            break
        i += 3
        j = i
        while j < n and raw[j].isdigit():
            j += 1
        if j == i:
            break
        length = int(raw[i:j])
        if raw[j:j + 3] != "~m~":
            break
        start = j + 3
        msgs.append(raw[start:start + length])
        i = start + length
    return msgs


_WS_HEADERS = {
    "Origin": "https://www.tradingview.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def fetch_quote(symbol, timeout=15.0):
    cfg = get_settings()
    qs = _rand("qs_")
    result = {}
    connected = threading.Event()
    got_data = threading.Event()
    errors = []

    logger.info("fetch_quote: connecting for %s", symbol)

    def on_open(ws):
        logger.info("fetch_quote: connected")
        try:
            ws.send(_msg("set_auth_token", [cfg.tv_auth_token]))
            ws.send(_msg("quote_create_session", [qs]))
            ws.send(_msg("quote_set_fields", [qs] + QUOTE_FIELDS))
            ws.send(_msg("quote_add_symbols", [qs, symbol]))
        except Exception as exc:
            errors.append(str(exc))
        connected.set()

    def on_message(ws, raw):
        for payload in _split(raw):
            if payload.startswith("~h~"):
                try:
                    ws.send(_pack(payload))
                except Exception:
                    pass
                continue
            try:
                data = json.loads(payload)
            except Exception:
                continue
            if not isinstance(data, dict) or "m" not in data:
                continue
            method = data.get("m", "")
            params = data.get("p", [])
            if method == "qsd":
                if len(params) >= 2 and isinstance(params[1], dict):
                    vals = params[1].get("v")
                    if isinstance(vals, dict):
                        result.update(vals)
                        if vals.get("lp") is not None:
                            logger.info("fetch_quote: price=%s", vals.get("lp"))
                            got_data.set()
            elif method == "critical_error":
                if "already in session" not in str(params):
                    errors.append(str(params))
                    got_data.set()

    def on_error(ws, error):
        logger.error("fetch_quote: error %s", error)
        errors.append(str(error))
        connected.set()
        got_data.set()

    def on_close(ws, code, msg):
        connected.set()
        got_data.set()

    ws = websocket.WebSocketApp(
        cfg.tv_ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        header=_WS_HEADERS,
    )
    thread = threading.Thread(target=ws.run_forever, daemon=True)
    thread.start()

    if not connected.wait(timeout):
        ws.close()
        raise ConnectionError("WS connection timed out")

    if not got_data.wait(timeout):
        ws.close()
        if result and result.get("lp") is not None:
            return result
        raise TimeoutError("No quote data for " + symbol)

    ws.close()
    thread.join(timeout=3)

    if errors and not result:
        raise ConnectionError("WS error: " + errors[0])

    return result


def fetch_candles(symbol, timeframe, num_bars, timeout=20.0):
    cfg = get_settings()
    cs = _rand("cs_")
    candles = []
    connected = threading.Event()
    sym_resolved = threading.Event()
    series_done = threading.Event()
    errors = []
    lock = threading.Lock()

    logger.info("fetch_candles: %s tf=%s bars=%d", symbol, timeframe, num_bars)

    def on_open(ws):
        logger.info("fetch_candles: connected")
        try:
            ws.send(_msg("set_auth_token", [cfg.tv_auth_token]))
            ws.send(_msg("chart_create_session", [cs, ""]))
            ws.send(_msg("switch_timezone", [cs, "Etc/UTC"]))
            sym_cfg = json.dumps({"symbol": symbol, "adjustment": "splits", "session": "extended"})
            ws.send(_msg("resolve_symbol", [cs, "sds_sym_1", "=" + sym_cfg]))
        except Exception as exc:
            errors.append(str(exc))
        connected.set()

    def on_message(ws, raw):
        for payload in _split(raw):
            if payload.startswith("~h~"):
                try:
                    ws.send(_pack(payload))
                except Exception:
                    pass
                continue
            try:
                data = json.loads(payload)
            except Exception:
                continue
            if not isinstance(data, dict) or "m" not in data:
                continue
            method = data["m"]
            params = data.get("p", [])

            if method == "symbol_resolved":
                logger.info("fetch_candles: symbol resolved")
                sym_resolved.set()
                try:
                    ws.send(_msg("create_series", [cs, "sds_1", "s1", "sds_sym_1", timeframe, num_bars, ""]))
                except Exception as exc:
                    errors.append(str(exc))
                    series_done.set()

            elif method == "symbol_error":
                errors.append("symbol_error: " + str(params))
                sym_resolved.set()
                series_done.set()

            elif method in ("timescale_update", "du"):
                if len(params) >= 2 and isinstance(params[1], dict):
                    for _key, sdata in params[1].items():
                        if not isinstance(sdata, dict):
                            continue
                        bars_data = sdata.get("s") or sdata.get("st")
                        if not bars_data:
                            continue
                        with lock:
                            for bar in bars_data:
                                v = bar.get("v", [])
                                if len(v) >= 5:
                                    candles.append(Candle(
                                        timestamp=v[0], open=v[1], high=v[2],
                                        low=v[3], close=v[4],
                                        volume=v[5] if len(v) > 5 else 0.0,
                                    ))
                    logger.info("fetch_candles: have %d candles", len(candles))

            elif method == "series_completed":
                logger.info("fetch_candles: series done %d candles", len(candles))
                series_done.set()

            elif method == "critical_error":
                if "already in session" not in str(params):
                    errors.append("critical_error: " + str(params))
                    series_done.set()

            elif method == "series_error":
                errors.append("series_error: " + str(params))
                series_done.set()

    def on_error(ws, error):
        logger.error("fetch_candles: error %s", error)
        errors.append(str(error))
        connected.set()
        sym_resolved.set()
        series_done.set()

    def on_close(ws, code, msg):
        connected.set()
        sym_resolved.set()
        series_done.set()

    ws = websocket.WebSocketApp(
        cfg.tv_ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        header=_WS_HEADERS,
    )
    thread = threading.Thread(target=ws.run_forever, daemon=True)
    thread.start()

    if not connected.wait(timeout):
        ws.close()
        raise ConnectionError("WS connection timed out")

    if not sym_resolved.wait(timeout):
        ws.close()
        raise TimeoutError("Symbol resolution timed out: " + symbol)

    if errors:
        ws.close()
        raise ValueError(errors[0])

    series_done.wait(timeout)

    pages = 0
    while len(candles) < num_bars and pages < 5:
        remaining = num_bars - len(candles)
        if remaining <= 0:
            break
        count_before = len(candles)
        series_done.clear()
        try:
            ws.send(_msg("request_more_data", [cs, "sds_1", remaining]))
        except Exception:
            break
        if not series_done.wait(min(timeout, 15)):
            break
        if len(candles) <= count_before:
            break
        pages += 1

    try:
        ws.send(_msg("chart_delete_session", [cs]))
    except Exception:
        pass
    ws.close()
    thread.join(timeout=3)

    seen = set()
    unique = []
    for c in candles:
        if c.timestamp not in seen:
            seen.add(c.timestamp)
            unique.append(c)
    unique.sort(key=lambda x: x.timestamp)

    logger.info("fetch_candles: returning %d candles tf=%s", len(unique), timeframe)
    return unique