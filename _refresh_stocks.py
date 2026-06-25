"""
Refresh the Vistas stock-price snapshot (Yahoo Finance via yfinance, split/bonus/
dividend-ADJUSTED close). Writes data/Stocks Data PX till <date>.csv. Network is
optional/graceful — on failure it leaves the existing snapshot untouched.

  python _refresh_stocks.py             # large/mid-cap WATCHLIST (~40), full history
  python _refresh_stocks.py --all       # full current NIFTY 500
  python _refresh_stocks.py --all-nse   # EVERY NSE-listed stock (~2000) — chunked + resumable
  python _refresh_stocks.py --update    # append only the new daily tail for held symbols
"""
from __future__ import annotations

import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vistas import stocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="full NIFTY 500 (else the watchlist)")
    ap.add_argument("--all-nse", dest="all_nse", action="store_true",
                    help="EVERY NSE-listed stock (~2000), chunked + resumable")
    ap.add_argument("--update", action="store_true", help="append only the new tail for held symbols")
    ap.add_argument("--chunk", type=int, default=200, help="symbols per checkpoint chunk for --all-nse")
    ap.add_argument("--refetch", action="store_true", help="with --all-nse, re-pull symbols already held")
    ap.add_argument("--start", default="2000-01-01")
    a = ap.parse_args()
    log = lambda m: print(m, flush=True)

    if a.update:
        res = stocks.update_stocks(progress=log)
    elif a.all_nse:
        syms = stocks.all_nse_symbols()
        if not syms:
            print("Could not load the NSE equity list (check network)."); return
        # resumable: skip symbols already in the snapshot unless --refetch
        have = set(s.upper() for s in stocks.available())
        todo = syms if a.refetch else [s for s in syms if stocks._clean_sym(s) not in have]
        log(f"ALL NSE: {len(syms)} listed · {len(todo)} to pull · {len(have)} already held · "
            f"chunk={a.chunk} (each chunk writes+merges; Ctrl-C and re-run to continue).")
        res = {"ok": True, "n_symbols": len(have)}
        for i in range(0, len(todo), a.chunk):
            ch = todo[i:i + a.chunk]
            log(f"\n--- chunk {i // a.chunk + 1}/{(len(todo) + a.chunk - 1) // a.chunk}: "
                f"symbols {i + 1}-{i + len(ch)} of {len(todo)} ---")
            res = stocks.build_snapshot(ch, start=a.start, progress=log)
            log("  " + json.dumps({k: res.get(k) for k in ("ok", "n_symbols", "n_days", "asof")}))
            if not res.get("ok"):
                log("  (chunk fetched nothing — yfinance may be throttling; continuing)")
        log(f"\nALL NSE done: {res.get('n_symbols')} symbols now in the snapshot.")
    elif a.all:
        log("Loading NIFTY 500 constituents…")
        res = stocks.build_full_snapshot(start=a.start, progress=log)
    else:
        res = stocks.build_snapshot(start=a.start, progress=log)

    print("\n=== RESULT ===")
    print(json.dumps(res, indent=2))
    if res.get("ok"):
        print(f"\nSnapshot: {res.get('file')} — {res.get('n_symbols')} symbols, "
              f"{res.get('n_days')} days, {res.get('start')}..{res.get('asof')}")


if __name__ == "__main__":
    main()
