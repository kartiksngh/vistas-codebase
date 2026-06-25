"""
funds_bridge.py — bridge the LIVE-AMC holdings universe (funds_portfolio/<slug>.json, name-keyed,
latest-month only) to the 13-yr STORE universe (funds_attribution/<navindia_code>.json) by HOLDINGS
FINGERPRINT (symbol-set overlap), NOT fuzzy names — data over labels (KV).

WHY: the merged Funds cockpit is store-canonical (navindia_code → full skill + history + month
dropdown). Live-AMC funds that ALSO exist in the store (active equity funds) must collapse to the
store entry (no duplicate in the picker). Live-AMC funds NOT in the store (passive index/ETF + debt/
liquid + a couple of fringe active) get a HOLDINGS-ONLY cockpit (latest book, no skill — they have no
13-yr active history). This module returns:
  bridge   = {slug: navindia_code}   for the matched (so the cockpit can redirect / dedup),
  holdonly = {slug: {name, amc}}      for the unmatched (the cockpit lists them as holdings-only).

Display-plane only; no analytics, no JS-parity port.
"""
from __future__ import annotations
import os, json, glob
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLDINGS = os.path.join(_ROOT, "data", "funds", "history", "holdings_history.parquet")
_MIN_JACCARD = 0.5      # symbol-set overlap to call two funds the same scheme


def _store_latest_symbols() -> dict:
    """{navindia_code: set(nse_symbol)} for each store scheme's LATEST disclosed month."""
    from . import scheme_identity as sid
    h = pd.read_parquet(HOLDINGS, columns=["navindia_code", "ym", "nse_symbol", "pct"])
    h["navindia_code"] = h["navindia_code"].map(sid.canonical_code)
    h = h[h["pct"].notna() & (h["pct"] > 0)]
    out = {}
    for code, d in h.groupby("navindia_code"):
        dl = d[d["ym"] == d["ym"].max()]
        syms = set(s for s in dl["nse_symbol"].dropna().astype(str) if s and s != "nan")
        if syms:
            out[str(code)] = syms
    return out


def build(portfolio_dir: str, attr_manifest: dict | None = None) -> dict:
    """Match each funds_portfolio/<slug>.json to a store scheme by symbol-set Jaccard.
    Returns {"bridge": {slug: navindia_code}, "holdonly": {slug: {name, amc}}, "stats": {...}}."""
    attr_codes = set(map(str, (attr_manifest or {}).keys()))
    try:
        store = _store_latest_symbols()
    except Exception as e:
        print(f"[funds_bridge] store symbols unavailable ({e}); all live funds -> holdonly")
        store = {}
    # restrict store match-targets to schemes that actually have a skill record (in the cockpit)
    store_items = [(c, s) for c, s in store.items() if (not attr_codes or c in attr_codes)]

    bridge, holdonly = {}, {}
    n_files = 0
    for f in sorted(glob.glob(os.path.join(portfolio_dir, "*.json"))):
        slug = os.path.basename(f)[:-5]
        if slug.startswith("_"):
            continue
        n_files += 1
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        syms = set(str(x.get("symbol")) for x in (j.get("holdings") or []) if x.get("symbol"))
        best_code, best_j = None, 0.0
        if syms:
            for code, ssy in store_items:
                inter = len(syms & ssy)
                if not inter:
                    continue
                jac = inter / len(syms | ssy)
                if jac > best_j:
                    best_j, best_code = jac, code
        if best_j >= _MIN_JACCARD:
            bridge[slug] = best_code                      # same fund as a store scheme → dedup
        else:
            holdonly[slug] = {"name": j.get("name") or slug, "amc": j.get("amc") or ""}
    stats = {"live_funds": n_files, "matched": len(bridge), "holdonly": len(holdonly),
             "store_schemes": len(store)}
    print(f"[funds_bridge] live={n_files} matched-to-store={len(bridge)} holdonly={len(holdonly)}", flush=True)
    return {"bridge": bridge, "holdonly": holdonly, "stats": stats}
