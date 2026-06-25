"""
Refresh the Vistas world / cross-asset snapshot (Yahoo Finance — no API key).

Pulls the curated WORLD_CATALOG — global equity indices, commodities, FX, bond
yields, credit/rate ETF proxies, volatility and crypto — into
data/World Data PX till <date>.csv. Network is optional/graceful.

  python _refresh_world.py            # full catalog, full history (merges into snapshot)
  python _refresh_world.py --update   # append only the new daily tail
  python _refresh_world.py --list     # print the catalog and exit (no fetch)
"""
from __future__ import annotations

import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vistas import world


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="append only the new daily tail")
    ap.add_argument("--list", action="store_true", help="print the catalog and exit")
    ap.add_argument("--start", default="2000-01-01")
    a = ap.parse_args()

    if a.list:
        by = {}
        for sym, name, grp in world.WORLD_CATALOG:
            by.setdefault(grp, []).append((sym, name))
        for grp, items in by.items():
            print(f"\n## {grp} ({len(items)})")
            for sym, name in items:
                print(f"  {sym:12s} {name}")
        print(f"\nTotal: {len(world.WORLD_CATALOG)} instruments")
        return

    log = lambda m: print(m, flush=True)
    res = world.update_world(progress=log) if a.update else world.build_snapshot(start=a.start, progress=log)
    print("\n=== RESULT ===")
    print(json.dumps(res, indent=2))
    if res.get("ok"):
        print(f"\nSnapshot: {res.get('file')} — {res.get('n_symbols')} instruments, "
              f"{res.get('n_days')} days, {res.get('start')}..{res.get('asof')}")


if __name__ == "__main__":
    main()
