"""
Watch the Screener fundamentals pull; when it goes IDLE (no new cache file for ~25 min),
rebuild BOTH the single-file offline deck and the hosted hybrid site once, then exit.

Used to auto-refresh the deck/site when a long pull finishes, with no babysitting.

  python _watch_and_rebuild.py            # 25-min idle threshold, poll every 4 min
  python _watch_and_rebuild.py --idle 900 --poll 120
"""
from __future__ import annotations

import os
import sys
import glob
import time
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle", type=int, default=1500, help="seconds of no new cache file = pull finished")
    ap.add_argument("--poll", type=int, default=240, help="seconds between checks")
    a = ap.parse_args()
    sdir = os.path.join("data", "screener")
    print(f"[watch] waiting for the fundamentals pull to go idle ({a.idle}s) …", flush=True)
    while True:
        files = glob.glob(os.path.join(sdir, "*.json"))
        n = len(files)
        age = (time.time() - max(os.path.getmtime(f) for f in files)) if files else 0
        if files and age > a.idle:
            print(f"[watch] pull idle for {int(age)}s at {n} companies -> rebuilding deck + site…", flush=True)
            from vistas import deck
            r = deck.rebuild_all(reason="auto-final")
            d, s = r.get("deck", {}), r.get("site", {})
            print(f"[watch] DONE. single-file deck: {d.get('size_mb')} MB · "
                  f"hosted shell: {s.get('shell_mb')} MB ({s.get('n_stock_files')} stock + "
                  f"{s.get('n_fundamental_files')} fundamentals files) · {n} companies cached", flush=True)
            return
        print(f"[watch] {n} cached, last write {int(age)}s ago — still pulling; check again in {a.poll}s", flush=True)
        time.sleep(a.poll)


if __name__ == "__main__":
    main()
