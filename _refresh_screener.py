"""
Refresh the Screener.in fundamentals cache — INCREMENTAL by default.

Each company's bundle (valuation history + quarterly/annual P&L, balance sheet, cash
flow, ratios, shareholding) is stored as data/screener/<SYM>.json. A normal refresh
pulls only NEW companies + STALE ones (older than the statements TTL) and SKIPS the
fresh ones — so re-running is cheap. Use --full to refetch everything (after results-
season corrections).

CREDENTIALS: env SCREENER_EMAIL/SCREENER_PASSWORD if set, else prompted here (password
hidden). Used only for this run; never written to disk (only the cookie jar is cached).
The chart/statement data is largely PUBLIC, so --no-login still pulls the core.

  python _refresh_screener.py                       # incremental, NIFTY 500 universe
  python _refresh_screener.py --universe all         # EVERY NSE-listed company (~2000) — heavy, staged
  python _refresh_screener.py --universe watchlist    # ~40 large/mid caps
  python _refresh_screener.py TCS RELIANCE             # just these symbols
  python _refresh_screener.py --full                   # refetch everything (corrections)
"""
from __future__ import annotations

import os
import sys
import json
import time
import getpass
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vistas import screener, stocks


def resolve_universe(name: str):
    if name == "all":
        return screener.all_nse_symbols()
    if name == "nifty500":
        return stocks.nifty500_symbols()
    if name == "watchlist":
        return list(stocks.WATCHLIST)
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", help="explicit NSE symbols (overrides --universe)")
    ap.add_argument("--universe", choices=["watchlist", "nifty500", "all"], default="nifty500",
                    help="which company set to ensure is cached (default: nifty500)")
    ap.add_argument("--full", action="store_true", help="refetch everything (corrections), not just new/stale")
    ap.add_argument("--standalone", action="store_true", help="standalone statements (default: consolidated)")
    ap.add_argument("--no-login", action="store_true", help="skip login; pull only public/limited data")
    a = ap.parse_args()

    if not a.no_login and not screener.have_credentials():
        # a cached cookie may already authenticate — only prompt if it doesn't
        if not screener._logged_in(screener._session()):
            print("Screener login (press Enter at email to use public data):")
            email = input("  email: ").strip()
            if email:
                os.environ["SCREENER_EMAIL"] = email
                os.environ["SCREENER_PASSWORD"] = getpass.getpass("  password (hidden): ")

    print("status:", json.dumps(screener.status()))
    if not a.no_login and screener.have_credentials():
        print("login:", json.dumps(screener.login()))

    syms = a.symbols if a.symbols else resolve_universe(a.universe)
    if not syms:
        print(f"No symbols (universe '{a.universe}' unavailable — check network)."); return
    label = "explicit" if a.symbols else a.universe
    print(f"\n{'REFETCH ALL' if a.full else 'Incremental refresh'} · {len(syms)} symbols ({label}) · "
          f"consolidated={not a.standalone}", flush=True)
    print("AUTO-RESUME: keeps going across the per-run cap until every company is pulled. "
          "Leave it running; Ctrl-C any time and just re-launch to continue (done companies are skipped).\n", flush=True)

    consolidated = not a.standalone
    pulled = set()
    def remaining():
        # incremental: anything not cached/fresh; full: anything not yet (re)pulled this run
        if a.full:
            return [s for s in syms if str(s).upper() not in pulled]
        return [s for s in syms if not screener._fresh(screener._cache_path(s), screener.TTL_STATEMENTS_DAYS)]

    total_added = total_refreshed = 0
    rnd, stale_rounds = 0, 0
    while True:
        batch = remaining()
        if not batch:
            print("\nAll requested companies are cached and fresh.")
            break
        rnd += 1
        print(f"\n--- round {rnd}: {len(batch)} still to pull ({len(screener.available())} cached so far) ---", flush=True)
        screener._REQ["count"] = 0          # reset the per-run circuit-breaker so the next round can proceed
        screener._REQ["blocks"] = 0
        res = screener.refresh(batch, full=a.full, consolidated=consolidated,
                               progress=lambda m: print(m, flush=True))
        total_added += res.get("n_added", 0)
        total_refreshed += res.get("n_refreshed", 0)
        for s in res.get("added", []) + res.get("refreshed", []):
            pulled.add(str(s).upper())
        progressed = (res.get("n_added", 0) + res.get("n_refreshed", 0)) > 0
        aborted = str(res.get("aborted") or "")

        if "streak" in aborted.lower():     # genuine server push-back (429/403) -> long backoff, give up after a few
            stale_rounds += 1
            if stale_rounds >= 4:
                print("\nScreener is blocking (repeated 429/403). Stopping — re-launch later to continue.")
                break
            wait = 120 * stale_rounds
            print(f"  block streak from Screener — backing off {wait}s, then resuming…", flush=True)
            time.sleep(wait)
            continue
        if not progressed:                  # nothing pulled this round (all-fail / blocked) -> back off, bail after a few
            stale_rounds += 1
            if stale_rounds >= 4:
                print("\nNo progress over several rounds. Stopping — re-launch later to continue.")
                break
            time.sleep(60)
            continue
        stale_rounds = 0
        time.sleep(8)                       # small breather between rounds (stay polite)

    print(f"\n=== DONE === +{total_added} new, {total_refreshed} updated this run · "
          f"{len(screener.available())} cached total")


if __name__ == "__main__":
    main()
