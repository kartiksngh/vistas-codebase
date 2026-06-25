"""
Focused PR (price-return) + VALUATION (P/E, P/B, Div-Yield) backfill for Vistas.

SAFETY: this writes SEPARATE per-measure files — 'Indices Data PR/PE/PB/DY till
<date>.csv'. It reads the TR snapshot only to align column names + per-index
inception dates. It NEVER writes to or modifies your TR snapshot. So it cannot
harm the proven TR pipeline even if it fails midway (it checkpoints + aborts fast
on a throttle).

DEFAULT = build-small-then-scale: valuation (VAL) + price (PR) for ~25 MAJOR
indices (broad + size + sectors), full history. Once that proves the throttle is
beaten, re-run with --all for the full ~130-index universe.

  Run on your phone HOTSPOT (a fresh IP) to sidestep NSE's endpoint rate-limit.

  python _backfill_measures.py                 # focused majors, VAL+PR, from 2000
  python _backfill_measures.py --all           # full TR universe
  python _backfill_measures.py --groups VAL    # valuation only
  python _backfill_measures.py --start 2010-01-01 --limit 8   # quick proof
"""
from __future__ import annotations

import os
import sys
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vistas import fetch

# Most-watched NSE indices, in a sensible order. Matched case-insensitively against
# the actual TR snapshot column names, so only ones you already hold TR for are pulled
# (guarantees the new measures align with TR and the names are real).
PREFERRED = [
    "NIFTY 50", "NIFTY NEXT 50", "NIFTY 100", "NIFTY 200", "NIFTY 500",
    "NIFTY MIDCAP 150", "NIFTY MIDCAP 100", "NIFTY SMALLCAP 250", "NIFTY SMALLCAP 100",
    "NIFTY BANK", "NIFTY FINANCIAL SERVICES", "NIFTY IT", "NIFTY FMCG", "NIFTY AUTO",
    "NIFTY PHARMA", "NIFTY METAL", "NIFTY ENERGY", "NIFTY REALTY", "NIFTY MEDIA",
    "NIFTY PSU BANK", "NIFTY PRIVATE BANK", "NIFTY INFRASTRUCTURE", "NIFTY CONSUMPTION",
    "NIFTY MNC", "NIFTY COMMODITIES",
]


def focused_universe():
    cols = fetch._tr_snapshot_columns()
    up = {c.upper(): c for c in cols}
    return [up[p.upper()] for p in PREFERRED if p.upper() in up]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default="VAL,PR", help="comma list of report groups (VAL,PR)")
    ap.add_argument("--start", default="2000-01-01")
    ap.add_argument("--all", action="store_true", help="full TR universe instead of focused majors")
    ap.add_argument("--limit", type=int, default=0, help="cap the focused universe to N indices")
    a = ap.parse_args()

    groups = tuple(g.strip().upper() for g in a.groups.split(",") if g.strip())
    uni = None if a.all else focused_universe()
    if uni is not None and a.limit:
        uni = uni[:a.limit]

    where = "ALL TR-snapshot indices" if uni is None else f"{len(uni)} focused indices"
    print(f"Backfill: groups={groups}  start={a.start}  universe={where}")
    if uni is not None:
        print("   " + ", ".join(uni))
    print("\nWrites separate 'Indices Data <MEASURE> till *.csv'. TR snapshot is NOT modified.")
    print("(Slow/stealth pacing + patient 75s timeout + abort-fast on a throttle streak.)\n")

    res = fetch.build_measures(groups=groups, start=a.start, universe=uni)

    print("\n=== RESULT ===")
    for g, info in res.get("groups", {}).items():
        print(f"  {g}: ok={info.get('n_ok')} fail={info.get('n_fail')} "
              f"aborted={info.get('aborted')} files={info.get('files')}")
    any_files = any(info.get("files") for info in res.get("groups", {}).values())
    print()
    if any_files:
        print("Data written. Next: build the v2 terminal deck from it (no NSE needed):")
        print('   python -c "from vistas import deck; print(deck.save_terminal_deck(reason=\'backfill\'))"')
        print("or tell Claude 'measures pulled — build + verify the v2 deck'.")
    else:
        print("No files written (throttle/empty). Try again on a hotspot, or --limit 4 for a tiny test.")


if __name__ == "__main__":
    main()
