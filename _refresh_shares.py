"""
Refresh the Vistas issued-shares / market-cap data — COLLECTED from official sources, NEVER estimated.

market cap = our (bhavcopy-accurate, Bloomberg-validated) close x issued shares. Two collectors, no
fundamentals anywhere (see vistas/shares.py):
  * AMFI bulk XLSX  — published FULL market cap + the SEBI Large/Mid/Small label for every NSE name, one
                      plain-GET file (no login/WAF). Validated: ranks Bloomberg cap at rho 0.9947.
  * NSE issuedSize  — the exact raw issued-share INTEGER per symbol (point-in-time), from the NSE quote
                      API behind the exchange WAF. Throttled + resumable; refresh weekly off-hours.

  python _refresh_shares.py                 # AMFI refresh + issuedSize for the full universe (resumable)
  python _refresh_shares.py --amfi-only     # only refresh the AMFI bulk mcap / cohort file
  python _refresh_shares.py --issued-only   # only pull NSE issuedSize (exact shares)
  python _refresh_shares.py --limit 600     # cap issuedSize to the top-600 names by market cap
  python _refresh_shares.py --new-only      # issuedSize only for names not already cached (incremental)
  python _refresh_shares.py --list          # show what is cached, then exit
  python _refresh_shares.py --validate      # (local) rank-check our mcap vs Bloomberg cap, if available
"""
from __future__ import annotations

import os
import sys
import json
import argparse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vistas import shares


def _print_cache():
    amfi = shares.load_amfi()
    issued = shares.shares_by_symbol()
    print(f"\nAMFI mcap rows .......... {len(amfi)}"
          f"   (period {next(iter(amfi.values()), {}).get('period', '—') if amfi else '—'})")
    from collections import Counter
    cats = Counter(r.get("category") for r in amfi.values() if r.get("category"))
    if cats:
        print("  cohorts ............... " + ", ".join(f"{k}={v}" for k, v in cats.most_common()))
    print(f"issuedSize names ........ {len(issued)}")
    if issued:
        top = sorted(issued.items(), key=lambda kv: kv[1], reverse=True)[:5]
        print("  largest by shares ..... " + ", ".join(f"{s}({v/1e7:.0f}cr sh)" for s, v in top))


def _validate(log):
    """Local-only cross-check: our mcap (latest close x collected shares) vs Bloomberg cap rank."""
    try:
        from vistas_gated import audit
    except Exception as e:
        print(f"[validate] vistas_gated not available ({e}); skipping the Bloomberg rank-check.")
        return
    import numpy as np
    px = audit.our_raw_close()
    last = px.apply(lambda s: s.dropna().iloc[-1] if s.notna().any() else np.nan, axis=0)
    sh = shares.shares_by_symbol()                         # exact issuedSize where pulled
    our = {s: float(last[s]) * sh[s] for s in sh if s in last.index and last[s] and last[s] > 0}
    if len(our) < 30:                                       # fall back to AMFI mcap if issuedSize thin
        amfi = shares.amfi_mcap_by_symbol()
        our = {s: v * 1e7 for s, v in amfi.items()}
        log(f"[validate] using AMFI mcap ({len(our)} names) — issuedSize cache is thin")
    out = audit.audit_mcap(our)
    audit.print_mcap_card(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--amfi-only", action="store_true", help="only refresh the AMFI bulk mcap/cohort file")
    ap.add_argument("--issued-only", action="store_true", help="only pull NSE issuedSize")
    ap.add_argument("--limit", type=int, default=None, help="cap issuedSize to the top-N names by mcap")
    ap.add_argument("--new-only", action="store_true", help="issuedSize only for names not already cached")
    ap.add_argument("--pace", type=float, default=0.6, help="seconds between NSE calls (jittered)")
    ap.add_argument("--list", action="store_true", help="show cache status and exit")
    ap.add_argument("--validate", action="store_true", help="rank-check vs Bloomberg cap (local only)")
    a = ap.parse_args()
    log = lambda m: print(m, flush=True)

    if a.list:
        _print_cache()
        return

    # 1) AMFI bulk (full mcap + cohorts) — the always-available no-WAF backbone
    if not a.issued_only:
        log("== AMFI bulk market cap + SEBI cohort ==")
        res = shares.build_from_amfi(progress=log)
        print(json.dumps(res, indent=2))

    # 2) NSE issuedSize (exact shares) — throttled + resumable
    if not a.amfi_only:
        syms = shares.target_symbols(limit=a.limit, new_only=a.new_only)
        log(f"\n== NSE issuedSize pull: {len(syms)} symbols "
            f"(biggest-first{'; new-only' if a.new_only else ''}) ==")
        if syms:
            res = shares.build(syms, pace=a.pace, progress=log,
                               asof=datetime.now().strftime("%Y-%m-%d"))
            print(json.dumps(res, indent=2))
            if res.get("collected", 0) == 0 and res.get("blocked", 0):
                print("\nNOTE: every call was blocked by NSE's WAF. This happens from datacenter / VPN IPs; "
                      "run it from the same machine/network that serves the terminal (where the NSE fetch works).")

    if a.validate:
        print("\n== validation: our mcap vs Bloomberg cap (rank) ==")
        _validate(log)

    print("\nDone. mcap = our close x collected shares is now available to the analytics/dashboard layer.")


if __name__ == "__main__":
    main()
