#!/usr/bin/env python3
"""
TradingView Data Service — entry point.

  python main.py serve          →  Start API (default)
  python main.py price XAUUSD   →  Get current price
  python main.py candles XAUUSD →  Get candle data
  python main.py search gold    →  Search symbols
"""
import argparse
import json
import logging
import sys
import datetime as dt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-25s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def cmd_serve(args):
    import uvicorn
    from scraper.config import get_settings
    cfg = get_settings()
    logger.info("Starting on %s:%s", cfg.api_host, cfg.api_port)
    uvicorn.run("scraper.api:app", host=cfg.api_host, port=cfg.api_port,
                reload=args.reload, log_level="info")


def cmd_price(args):
    from scraper.engine import fetch_price
    try:
        resolved, q = fetch_price(args.symbol)
        print(f"\n{'='*50}")
        print(f"  {resolved}")
        print(f"{'='*50}")
        print(f"  Price:       {q.get('lp', '—')}")
        print(f"  Open:        {q.get('open_price', '—')}")
        print(f"  High:        {q.get('high_price', '—')}")
        print(f"  Low:         {q.get('low_price', '—')}")
        print(f"  Prev Close:  {q.get('prev_close_price', '—')}")
        print(f"  Change:      {q.get('ch', '—')}  ({q.get('chp', '—')}%)")
        print(f"  Volume:      {q.get('volume', '—')}")
        print(f"  Bid:         {q.get('bid', '—')}")
        print(f"  Ask:         {q.get('ask', '—')}")
        print(f"  Session:     {q.get('current_session', '—')}")
        print(f"{'='*50}")
        if args.json:
            print(json.dumps(q, indent=2))
    except Exception as exc:
        logger.error("Failed: %s", exc)
        sys.exit(1)


def cmd_candles(args):
    from scraper.engine import fetch_multi_timeframe
    try:
        resolved, data = fetch_multi_timeframe(args.symbol, args.timeframes, args.range, args.bars)
        print(f"\n{'='*60}")
        print(f"  {resolved}")
        print(f"{'='*60}")
        for tf, candles in data.items():
            print(f"\n  Timeframe: {tf}  ({len(candles)} candles)")
            print(f"  {'Datetime':<22} {'Open':>12} {'High':>12} {'Low':>12} {'Close':>12} {'Volume':>14}")
            print(f"  {'-'*86}")
            for c in candles[-15:]:
                ts = dt.datetime.utcfromtimestamp(c.timestamp).strftime("%Y-%m-%d %H:%M")
                print(f"  {ts:<22} {c.open:>12.4f} {c.high:>12.4f} {c.low:>12.4f} {c.close:>12.4f} {c.volume:>14.2f}")
            if len(candles) > 15:
                print(f"  … and {len(candles) - 15} more")
        if args.output:
            out = {tf: [c.to_dict() for c in cs] for tf, cs in data.items()}
            with open(args.output, "w") as f:
                json.dump(out, f, indent=2)
            print(f"\n  → Saved to {args.output}")
    except Exception as exc:
        logger.error("Failed: %s", exc)
        sys.exit(1)


def cmd_search(args):
    from scraper.symbols import search_symbols
    results = search_symbols(args.query)
    if not results:
        print("No results.")
        return
    print(f"\n{'Full Name':<30} {'Type':<10} Description")
    print("-" * 80)
    for r in results:
        print(f"{r.full_name:<30} {r.type:<10} {r.description}")


def main():
    p = argparse.ArgumentParser(description="TradingView Data Service")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve")
    s.add_argument("--reload", action="store_true")

    s = sub.add_parser("price")
    s.add_argument("symbol")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("candles")
    s.add_argument("symbol")
    s.add_argument("-t", "--timeframes", nargs="+", default=["1", "15"])
    s.add_argument("-r", "--range", default="1h")
    s.add_argument("-b", "--bars", type=int, default=None)
    s.add_argument("-o", "--output", type=str, default=None)

    s = sub.add_parser("search")
    s.add_argument("query")

    args = p.parse_args()
    if args.cmd == "price":
        cmd_price(args)
    elif args.cmd == "candles":
        cmd_candles(args)
    elif args.cmd == "search":
        cmd_search(args)
    else:
        cmd_serve(args if args.cmd == "serve" else argparse.Namespace(reload=False))


if __name__ == "__main__":
    main()