"""
Resolves user input (name, URL, or EXCHANGE:SYMBOL) into canonical format.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse, parse_qs, unquote

import httpx
from .config import get_settings
from .models import SymbolSearchResult

logger = logging.getLogger(__name__)

# Headers needed to avoid 403 from TradingView search API
_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}


def resolve_input(user_input: str) -> str:
    text = user_input.strip()

    # ── TradingView URL ──
    if "tradingview.com" in text:
        sym = _extract_from_url(text)
        if sym:
            logger.info("Extracted from URL: %s", sym)
            return sym

    # ── Already EXCHANGE:SYMBOL ──
    if ":" in text:
        return text.upper()

    # ── Search TradingView ──
    results = search_symbols(text)
    if not results:
        raise ValueError(
            f"Could not resolve '{text}'. "
            f"Try EXCHANGE:SYMBOL format, e.g. OANDA:XAUUSD"
        )
    best = results[0]
    logger.info("Resolved '%s' → %s", text, best.full_name)
    return best.full_name


def search_symbols(query: str, limit: int = 10) -> list[SymbolSearchResult]:
    cfg = get_settings()

    # Try v3 first, fall back to v2 if 403
    urls = [
        "https://symbol-search.tradingview.com/symbol_search/v3/",
        "https://symbol-search.tradingview.com/symbol_search/",
    ]

    for url in urls:
        try:
            with httpx.Client(timeout=10, headers=_SEARCH_HEADERS) as client:
                resp = client.get(url, params={
                    "text": query,
                    "hl": "1",
                    "exchange": "",
                    "lang": "en",
                    "search_type": "",
                    "domain": "production",
                    "sort_by_country": "US",
                })

                if resp.status_code == 403:
                    logger.debug("Search 403 on %s, trying next URL", url)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # v3 wraps results in {"symbols": [...]}
                if isinstance(data, dict) and "symbols" in data:
                    data = data["symbols"]

                results: list[SymbolSearchResult] = []
                for item in data[:limit]:
                    ex = item.get("exchange", "")
                    sym = item.get("symbol", "")
                    results.append(SymbolSearchResult(
                        symbol=sym,
                        description=item.get("description", ""),
                        exchange=ex,
                        type=item.get("type", ""),
                        full_name=f"{ex}:{sym}" if ex else sym,
                    ))
                return results

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                logger.debug("Search 403 on %s, trying next", url)
                continue
            logger.error("Symbol search failed: %s", exc)
            return []
        except Exception as exc:
            logger.error("Symbol search failed: %s", exc)
            return []

    logger.warning("All search URLs returned 403")
    return []


def _extract_from_url(url: str) -> str | None:
    parsed = urlparse(url)

    # ?symbol=EXCHANGE%3ASYMBOL  or  ?symbol=EXCHANGE:SYMBOL
    qs = parse_qs(parsed.query)
    if "symbol" in qs:
        return unquote(qs["symbol"][0])

    # /symbols/EXCHANGE-SYMBOL/
    m = re.search(r"/symbols/([A-Za-z0-9_]+)-([A-Za-z0-9_.]+)", parsed.path)
    if m:
        return f"{m.group(1)}:{m.group(2)}"

    if parsed.fragment:
        m = re.search(r"symbol=([^&]+)", parsed.fragment)
        if m:
            return unquote(m.group(1))

    return None