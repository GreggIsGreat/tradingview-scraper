"""
Persistent TradingView WebSocket client.

Manages:
 - Single WebSocket connection with auto-reconnect
 - Quote session  → real-time price streaming, cached per symbol
 - Chart sessions → OHLCV candles, one session per fetch (on-demand)

FIXES v2.1:
 - _connected is set AFTER _on_open finishes (no race condition)
 - Duplicate quote_add_symbols prevented with _qs_sent tracking
 - "already in session" errors handled gracefully (no crash)
"""
from __future__ import annotations

import json
import logging
import random
import string
import threading
import time
from dataclasses import dataclass, field

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
    "rtc", "rch", "rchp", "is_tradable",
]


# ── Protocol helpers ─────────────────────────────────────────

def _rand(prefix: str = "cs_", n: int = 12) -> str:
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _pack(payload: str) -> str:
    return f"~m~{len(payload)}~m~{payload}"


def _msg(method: str, params: list) -> str:
    return _pack(json.dumps({"m": method, "p": params}, separators=(",", ":")))


def _split(raw: str) -> list[str]:
    msgs: list[str] = []
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


# ── Per-chart-session state ──────────────────────────────────

@dataclass
class _ChartCtx:
    chart_session: str
    sym_resolved: threading.Event = field(default_factory=threading.Event)
    series_done: threading.Event = field(default_factory=threading.Event)
    candles: list = field(default_factory=list)
    errors: list = field(default_factory=list)


# ══════════════════════════════════════════════════════════════
#  TvClient
# ══════════════════════════════════════════════════════════════

class TvClient:
    """
    Persistent TradingView WebSocket client.

    Call ``start()`` once. Then:
      - ``get_quote(symbol)``            → cached real-time price dict
      - ``fetch_candles(symbol,tf,n)``   → list[Candle]
      - ``subscribe_quote(symbol)``      → pre-warm cache

    Call ``stop()`` to shut down.
    """

    def __init__(self, auth_token: str | None = None):
        cfg = get_settings()
        self._url = cfg.tv_ws_url
        self._token = auth_token or cfg.tv_auth_token
        self._origin = cfg.tv_origin
        self._timeout = cfg.ws_timeout
        self._reconnect_delay = cfg.reconnect_delay

        self._headers = {
            "Origin": self._origin,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        # connection state
        self._ws: websocket.WebSocketApp | None = None
        self._connected = threading.Event()
        self._running = False
        self._send_lock = threading.Lock()

        # quote session
        self._qs_id = _rand("qs_")
        self._quotes: dict[str, dict] = {}
        self._quote_ready: dict[str, threading.Event] = {}
        self._subscribed_symbols: set[str] = set()    # desired subscriptions
        self._qs_sent: set[str] = set()               # actually sent this session
        self._quote_lock = threading.Lock()

        # chart sessions
        self._charts: dict[str, _ChartCtx] = {}
        self._chart_lock = threading.Lock()
        self._chart_sem = threading.Semaphore(cfg.max_concurrent_charts)

    # ── lifecycle ────────────────────────────────────────────

    def start(self, timeout: float | None = None) -> None:
        timeout = timeout or self._timeout
        self._running = True
        self._conn_thread = threading.Thread(target=self._connection_loop, daemon=True)
        self._conn_thread.start()
        if not self._connected.wait(timeout):
            raise ConnectionError(
                "Initial connection to TradingView failed. "
                "Check your network and TV_AUTH_TOKEN."
            )
        logger.info("TvClient started")

    def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        logger.info("TvClient stopped")

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    @property
    def subscribed_symbols(self) -> list[str]:
        with self._quote_lock:
            return list(self._subscribed_symbols)

    @property
    def cached_quote_count(self) -> int:
        with self._quote_lock:
            return len(self._quotes)

    # ── auto-reconnect loop ──────────────────────────────────

    def _connection_loop(self) -> None:
        while self._running:
            self._connected.clear()
            self._qs_id = _rand("qs_")

            # clear the "sent" tracker — new session means fresh state
            with self._quote_lock:
                self._qs_sent.clear()

            ws = websocket.WebSocketApp(
                self._url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                header=self._headers,
            )
            self._ws = ws

            try:
                ws.run_forever(ping_interval=25, ping_timeout=10)
            except Exception as exc:
                logger.error("WS run_forever error: %s", exc)

            if self._running:
                logger.info(
                    "Connection lost. Reconnecting in %.0fs…",
                    self._reconnect_delay,
                )
                time.sleep(self._reconnect_delay)

    def _ensure_connected(self, timeout: float = 15.0) -> None:
        if not self._running:
            raise RuntimeError("TvClient not started. Call start() first.")
        if not self._connected.wait(timeout):
            raise ConnectionError("Not connected to TradingView")

    # ── quote API ────────────────────────────────────────────

    def subscribe_quote(self, symbol: str) -> None:
        """Subscribe to real-time quote stream for *symbol*."""
        self._ensure_connected()
        with self._quote_lock:
            # always add to desired set
            self._subscribed_symbols.add(symbol)

            # only send if not already sent in this session
            if symbol in self._qs_sent:
                logger.debug("Already subscribed in this session: %s", symbol)
                return
            self._qs_sent.add(symbol)
            self._quote_ready[symbol] = threading.Event()

        self._send("quote_add_symbols", [self._qs_id, symbol])
        logger.info("Subscribed to quote: %s", symbol)

    def get_quote(self, symbol: str, timeout: float = 10.0) -> dict:
        """
        Return latest quote data dict for *symbol*.

        First call for a new symbol subscribes and waits.
        Subsequent calls return from cache instantly.
        """
        self._ensure_connected()

        # fast path: already have cached data
        with self._quote_lock:
            cached = self._quotes.get(symbol)
            if cached:
                return dict(cached)

        # slow path: subscribe and wait for first push
        self.subscribe_quote(symbol)
        ev = self._quote_ready.get(symbol)
        if ev and not ev.wait(timeout):
            with self._quote_lock:
                cached = self._quotes.get(symbol)
                if cached:
                    return dict(cached)
            raise TimeoutError(
                f"No quote data for {symbol} within {timeout}s. "
                f"Is the symbol correct? Is the market open?"
            )

        with self._quote_lock:
            data = self._quotes.get(symbol, {})
            if not data:
                raise TimeoutError(f"Quote data empty for {symbol}")
            return dict(data)

    # ── candle API ───────────────────────────────────────────

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        num_bars: int,
        timeout: float = 60.0,
    ) -> list[Candle]:
        """
        Fetch OHLCV candles. Creates a temporary chart session,
        fetches data, paginates if needed, cleans up.
        """
        self._ensure_connected()

        cs = _rand("cs_")
        ctx = _ChartCtx(chart_session=cs)

        acquired = self._chart_sem.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError("Too many concurrent candle requests.")

        with self._chart_lock:
            self._charts[cs] = ctx

        try:
            return self._do_fetch(ctx, symbol, timeframe, num_bars, timeout)
        finally:
            self._chart_sem.release()
            try:
                self._send("chart_delete_session", [cs])
            except Exception:
                pass
            with self._chart_lock:
                self._charts.pop(cs, None)

    def _do_fetch(
        self,
        ctx: _ChartCtx,
        symbol: str,
        timeframe: str,
        num_bars: int,
        timeout: float,
    ) -> list[Candle]:
        cs = ctx.chart_session

        # 1. create chart session
        self._send("chart_create_session", [cs, ""])
        self._send("switch_timezone", [cs, "Etc/UTC"])

        # 2. resolve symbol
        sym_cfg = json.dumps({
            "symbol": symbol,
            "adjustment": "splits",
            "session": "extended",
        })
        self._send("resolve_symbol", [cs, "sds_sym_1", "=" + sym_cfg])

        if not ctx.sym_resolved.wait(timeout):
            raise TimeoutError(f"Symbol resolution timed out: {symbol}")
        if ctx.errors:
            raise ValueError(f"Symbol error: {ctx.errors[-1]}")

        # 3. create series
        self._send("create_series", [
            cs, "sds_1", "s1", "sds_sym_1", timeframe, num_bars, "",
        ])

        if not ctx.series_done.wait(timeout):
            logger.warning("series timed out, returning partial data")

        # 4. paginate
        count = len(ctx.candles)
        pages = 0
        while count < num_bars and pages < 10:
            remaining = num_bars - count
            if remaining <= 0:
                break
            ctx.series_done.clear()
            self._send("request_more_data", [cs, "sds_1", remaining])

            if not ctx.series_done.wait(min(timeout, 30)):
                break
            new_count = len(ctx.candles)
            if new_count <= count:
                break
            count = new_count
            pages += 1

        # 5. deduplicate + sort
        seen: set[float] = set()
        unique: list[Candle] = []
        for c in ctx.candles:
            if c.timestamp not in seen:
                seen.add(c.timestamp)
                unique.append(c)
        unique.sort(key=lambda c: c.timestamp)

        logger.info(
            "Fetched %d candles (requested %d)  tf=%s  session=%s",
            len(unique), num_bars, timeframe, cs,
        )
        return unique

    # ── WS callbacks ─────────────────────────────────────────

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        logger.info("WS connected")

        # ── 1. authenticate ──
        self._send("set_auth_token", [self._token])

        # ── 2. create quote session ──
        self._send("quote_create_session", [self._qs_id])
        self._send("quote_set_fields", [self._qs_id, *QUOTE_FIELDS])

        # ── 3. re-subscribe all desired symbols ──
        with self._quote_lock:
            self._qs_sent.clear()  # fresh session, nothing sent yet
            for sym in self._subscribed_symbols:
                self._qs_sent.add(sym)
                self._quote_ready[sym] = threading.Event()
                self._send("quote_add_symbols", [self._qs_id, sym])
            count = len(self._subscribed_symbols)

        logger.info(
            "Quote session %s ready, re-subscribed %d symbols",
            self._qs_id, count,
        )

        # ── 4. NOW signal connected ──
        # This MUST be last so that subscribe_quote() called from
        # engine.start_client() can't race with the re-subscribe loop above
        self._connected.set()

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        for payload in _split(raw):
            if payload.startswith("~h~"):
                try:
                    ws.send(_pack(payload))
                except Exception:
                    pass
                continue
            try:
                data = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(data, dict) and "m" in data:
                self._dispatch(data["m"], data.get("p", []))

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.error("WS error: %s", error)

    def _on_close(
        self, ws: websocket.WebSocketApp, code: int | None, msg: str | None,
    ) -> None:
        logger.info("WS closed  code=%s  msg=%s", code, msg)
        self._connected.clear()
        with self._chart_lock:
            for ctx in self._charts.values():
                ctx.errors.append("Connection closed")
                ctx.sym_resolved.set()
                ctx.series_done.set()

    # ── message dispatch ─────────────────────────────────────

    def _send(self, method: str, params: list) -> None:
        with self._send_lock:
            if self._ws:
                try:
                    self._ws.send(_msg(method, params))
                except Exception as exc:
                    logger.error("Send failed (%s): %s", method, exc)

    def _dispatch(self, method: str, params: list) -> None:
        handlers = {
            "qsd":              self._on_qsd,
            "timescale_update": self._on_chart_data,
            "du":               self._on_chart_data,
            "series_completed": self._on_series_completed,
            "symbol_resolved":  self._on_sym_resolved,
            "symbol_error":     self._on_sym_error,
            "series_error":     self._on_series_error,
            "critical_error":   self._on_critical_error,
        }
        h = handlers.get(method)
        if h:
            h(params)

    # ── quote handlers ───────────────────────────────────────

    def _on_qsd(self, params: list) -> None:
        if len(params) < 2 or not isinstance(params[1], dict):
            return
        qdata = params[1]
        symbol = qdata.get("n", "")
        values = qdata.get("v")
        if not symbol or not isinstance(values, dict):
            return

        with self._quote_lock:
            if symbol not in self._quotes:
                self._quotes[symbol] = {}
            self._quotes[symbol].update(values)
            ev = self._quote_ready.get(symbol)
            if ev:
                ev.set()

    # ── chart handlers ───────────────────────────────────────

    def _chart_ctx(self, params: list) -> _ChartCtx | None:
        if not params:
            return None
        with self._chart_lock:
            return self._charts.get(params[0])

    def _on_chart_data(self, params: list) -> None:
        ctx = self._chart_ctx(params)
        if not ctx or len(params) < 2 or not isinstance(params[1], dict):
            return
        for _key, sdata in params[1].items():
            if not isinstance(sdata, dict):
                continue
            bars = sdata.get("s") or sdata.get("st")
            if not bars:
                continue
            for bar in bars:
                v = bar.get("v", [])
                if len(v) < 5:
                    continue
                ctx.candles.append(Candle(
                    timestamp=v[0],
                    open=v[1],
                    high=v[2],
                    low=v[3],
                    close=v[4],
                    volume=v[5] if len(v) > 5 else 0.0,
                ))

    def _on_series_completed(self, params: list) -> None:
        ctx = self._chart_ctx(params)
        if ctx:
            ctx.series_done.set()

    def _on_sym_resolved(self, params: list) -> None:
        ctx = self._chart_ctx(params)
        if ctx:
            ctx.sym_resolved.set()

    def _on_sym_error(self, params: list) -> None:
        ctx = self._chart_ctx(params)
        err = f"symbol_error: {params}"
        logger.error(err)
        if ctx:
            ctx.errors.append(err)
            ctx.sym_resolved.set()
            ctx.series_done.set()

    def _on_series_error(self, params: list) -> None:
        ctx = self._chart_ctx(params)
        err = f"series_error: {params}"
        logger.error(err)
        if ctx:
            ctx.errors.append(err)
            ctx.series_done.set()

    def _on_critical_error(self, params: list) -> None:
        err_str = str(params)

        # ── Handle "already in session" gracefully ──
        # This is a quote-session error, NOT a real crash.
        # Just log it and move on — don't kill chart sessions.
        if "already in session" in err_str:
            logger.warning("Quote duplicate ignored: %s", err_str)
            return

        logger.error("critical_error: %s", err_str)

        # only propagate to chart sessions
        ctx = self._chart_ctx(params)
        if ctx:
            ctx.errors.append(f"critical_error: {err_str}")
            ctx.sym_resolved.set()
            ctx.series_done.set()
        else:
            # can't determine session — unblock all charts
            with self._chart_lock:
                for c in self._charts.values():
                    c.errors.append(f"critical_error: {err_str}")
                    c.sym_resolved.set()
                    c.series_done.set()