"""
Refresh the Vistas India-native MACRO snapshot (official free sources).

Pulls the wired MACRO_CATALOG — CPI/WPI inflation, policy & market rates, money &
credit, the external sector, real activity, and FII/DII flows — into
data/India Macro till <date>.csv. Network is optional/graceful: a source that's
unreachable or not-yet-wired is simply skipped; existing history is preserved.

  python _refresh_macro.py            # fetch every wired series, merge into snapshot
  python _refresh_macro.py --list     # print the catalog (id/name/group/source/status)
  python _refresh_macro.py --probe    # quick reachability check of the live sources

Set DATA_GOV_API_KEY (free at data.gov.in) to lift the OGD sample-key rate limit.
"""
from __future__ import annotations

import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vistas import macro


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="print the catalog and exit")
    ap.add_argument("--probe", action="store_true", help="check live sources and exit")
    a = ap.parse_args()

    if a.list:
        by = {}
        for c in macro.MACRO_CATALOG:
            wired = c["spec"].get("kind") not in (None, "none")
            by.setdefault(c["group"], []).append((c["name"], c["source"], "WIRED" if wired else "pending"))
        for grp, items in by.items():
            print(f"\n## {grp} ({len(items)})")
            for name, src, st in items:
                print(f"  [{st:7s}] {name:38s} <- {src}")
        n_wired = sum(1 for c in macro.MACRO_CATALOG if c["spec"].get("kind") not in (None, "none"))
        print(f"\nTotal: {len(macro.MACRO_CATALOG)} series, {n_wired} wired")
        return

    if a.probe:
        print("OGD key:", "custom" if os.environ.get("DATA_GOV_API_KEY") else "public sample (rate-limited)")
        tod = macro.fetch_fiidii_today(log=lambda m: print(" ", m))
        print("NSE FII/DII latest:", tod if tod else "unreachable")
        return

    log = lambda m: print(m, flush=True)
    res = macro.build_snapshot(progress=log)
    print("\n=== RESULT ===")
    print(json.dumps(res, indent=2, default=str))
    if res.get("ok"):
        print(f"\nSnapshot: {res.get('file')} — {res.get('n_series')} series, "
              f"{res.get('n_rows')} rows, {res.get('start')}..{res.get('asof')}")
        print("Series:", ", ".join(res.get("series", [])))


if __name__ == "__main__":
    main()
