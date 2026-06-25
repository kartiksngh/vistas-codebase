"""
Refresh the Vistas stock panel from the NSE BHAVCOPY (the exchange's own daily close) —
the decimal-accurate replacement for the yfinance adjusted-close source.

Pipeline (see vistas/bhav_prices.py for the full design):
  1. corporate actions  — fetch + cache NSE's official splits/bonuses/dividends (by year).
  2. price zips         — download + cache every NSE trading day's bhavcopy (resumable,
                          polite; only days NSE actually traded are requested).
  3. build the panel    — assemble raw closes by ISIN -> security master (PERMID lineage)
                          -> apply the verified corporate actions -> total-return level ->
                          NSE-align -> write "data/Stocks Data TR till <date>.csv".

The bhavcopy archive is PUBLIC (no login) and the run is polite + resumable, so it is safe
to re-run; cached days/years are skipped. Network is optional/graceful.

  python _refresh_bhav.py                 # full pipeline from 2000, promote live
  python _refresh_bhav.py --from 2018     # start the price history at 2018 (recent first)
  python _refresh_bhav.py --no-fetch      # skip the network; build from the existing cache
  python _refresh_bhav.py --no-build      # only fetch CAs + price zips (no panel build)
  python _refresh_bhav.py --no-promote    # write the panel to output/ (don't switch live)
"""
from __future__ import annotations

import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vistas import bhav_prices as bp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2000-01-01",
                    help="start date / year for the price history (default 2000)")
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip the network; build from the existing cache")
    ap.add_argument("--no-build", action="store_true",
                    help="only fetch corporate actions + price zips (no panel build)")
    ap.add_argument("--no-promote", action="store_true",
                    help="write the panel to output/ instead of switching the app live")
    a = ap.parse_args()
    start = a.start if len(a.start) >= 4 else "2000-01-01"
    log = lambda m: print(m, flush=True)

    if not a.no_fetch:
        log("=== [1/3] corporate actions ===")
        ca = bp.fetch_corp_actions(start_year=int(start[:4]), progress=log)
        log(f"  cached {ca['n_records']} CA records")
        log("=== [2/3] price zips (resumable; this is the slow, fetch-once step) ===")
        bp.build_cache(start=start, progress=log)
    else:
        log("=== [1-2/3] --no-fetch: using the existing cache ===")

    if a.no_build:
        log("=== --no-build: stopping after fetch ===")
        return

    log("=== [3/3] build the total-return panel ===")
    r = bp.build_panel(start=start, promote=(not a.no_promote), progress=log)
    print("\n=== RESULT ===")
    print(json.dumps({k: r[k] for k in ("ok", "panel", "promoted", "n_stocks", "n_days",
          "first", "last", "lineage", "ca") if k in r}, indent=2, default=str))
    rec = r.get("reconcile", {})
    if rec.get("ok") and rec.get("n"):
        print(f"\nReconcile vs yfinance: {rec['tight_corr_ge_0_99']}/{rec['n']} symbols "
              f"track tightly (return corr >= 0.99); median corr {rec['median_corr']:.4f}.")
        print("Biggest CAGR gaps (where the two sources most disagree — review "
              "output/bhav_ca_flags.csv + the audit):")
        for row in rec.get("biggest_cagr_gap", [])[:8]:
            print(f"  {row['sym']:12s} bhav {row['cagr_bhav']:7.2f}%  "
                  f"yf {row['cagr_yf']:7.2f}%  gap {row['cagr_gap_pp']:+6.2f}pp  "
                  f"corr {row['corr']:.3f}")


if __name__ == "__main__":
    main()
