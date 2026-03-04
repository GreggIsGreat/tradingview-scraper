from __future__ import annotations
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # TradingView
    tv_auth_token: str = "unauthorized_user_token"
    tv_ws_url: str = "wss://data.tradingview.com/socket.io/websocket"
    tv_search_url: str = "https://symbol-search.tradingview.com/symbol_search/v3/"
    tv_origin: str = "https://www.tradingview.com"

    # Auto-subscribe symbols (comma-separated)
    auto_subscribe: str = "OANDA:XAUUSD,OANDA:NAS100USD,COINBASE:BTCUSD"

    # Network
    ws_timeout: float = 20.0
    request_delay: float = 1.5
    max_bars_per_request: int = 5000
    max_retries: int = 3
    reconnect_delay: float = 3.0
    max_concurrent_charts: int = 3

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8877

    @property
    def auto_subscribe_symbols(self) -> list[str]:
        return [s.strip() for s in self.auto_subscribe.split(",") if s.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ── Time-range → minutes lookup ────────────────────────────

RANGE_MINUTES: dict[str, int] = {
    "5m": 5, "10m": 10, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "8h": 480,
    "12h": 720, "1d": 1440, "2d": 2880, "3d": 4320,
    "5d": 7200, "1w": 10080, "2w": 20160, "1M": 43200,
}

TIMEFRAME_MINUTES: dict[str, int] = {
    "1": 1, "3": 3, "5": 5, "15": 15, "30": 30, "45": 45,
    "60": 60, "120": 120, "180": 180, "240": 240,
    "D": 1440, "W": 10080,
}


def calculate_bars(range_key: str, timeframe: str) -> int:
    total = RANGE_MINUTES.get(range_key)
    tf = TIMEFRAME_MINUTES.get(timeframe)
    if total is None:
        raise ValueError(f"Unknown range '{range_key}'. Options: {list(RANGE_MINUTES)}")
    if tf is None:
        raise ValueError(f"Unknown timeframe '{timeframe}'. Options: {list(TIMEFRAME_MINUTES)}")
    return max(total // tf, 1)