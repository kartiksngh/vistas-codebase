"""Ownership & Flow WATERFALL (KV vision 2026-06-27; blueprint OWNERSHIP_FLOW.md) — P1 cube engine.

The money chain — an **AMC** raises capital via its **schemes** -> the schemes **deploy** that capital
into **sectors/themes** -> into **stocks** — decomposed EVERY month into the three additive components
and rolled up the aggregation lattice (AMC x sector, AMC totals, sector totals, the market total).

Stands ENTIRELY on the existing flow cube (`funds_flows._pair_flows_active`): no new licensed data, no
new modelling. That core already gives, per (fund, stock, month), the three figures
(FLOW_DECOMPOSITION.md / [[vistas-flow-decomposition]]):

    gross      = MV_end - MV_start                 (raw rupee change: price + inflow + active)
    price_adj  = MV_end - MV_start*(1+r)           (price stripped)
    net_active = AUM*(1+R_p)*dw_active             (price AND scheme-inflow stripped -> conviction)

from which the THREE ADDITIVE components (they reconcile EXACTLY on the priced subset; sum == gross):

    price_action   = gross - price_adj             (holdings simply moved with the market)
    implied_inflow = price_adj - net_active         (fresh scheme money deployed pro-rata; no view change)
    net_active     = net_active                     (genuine reweighting -> the smart-money signal)

This module attaches **AMC** (from the holdings table) + macro **sector** (the canonical vst_id->sector
map) to that cube and sums to (AMC x sector) cells with monthly history, plus the AMC / sector / market
roll-ups. Every level is a group-by of the SAME cube, so every level reconciles with every other.

P1 = the engine + a reconciliation self-audit (this file). The Ownership & Flow TAB (P2) reads the baked
cube. Display-plane only: AGGREGATES (no per-stock licensed ARM rides here) -> safe to bake + publish.

  python -m vistas.flow_waterfall            # build + print the reconciliation audit + top cells
"""
from __future__ import annotations
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

from .funds_flows import _load, _pair_flows_active, _prev_ym

# a cell is kept in the output cube only if its peak monthly |gross| clears this (Rs cr) — prunes the
# long tail of near-zero AMC x sector pairs so the baked artifact stays small. Roll-up TOTALS are
# computed BEFORE pruning, so nothing is lost from the AMC/sector/market aggregates.
_KEEP_MIN_CR = 5.0
_RECON_TOL_CR = 0.5            # reconciliation must hold to well under a crore


def _amc_of(h: pd.DataFrame) -> dict:
    """navindia_code -> AMC name (stable per scheme; last non-null wins)."""
    sub = h[["navindia_code", "amc"]].dropna().drop_duplicates(subset=["navindia_code"], keep="last")
    return dict(zip(sub["navindia_code"].astype(str), sub["amc"].astype(str)))


def _sector_map(explicit=None) -> dict:
    if explicit is not None:
        return explicit
    try:
        from .funds_portfolio_viz import canonical_vst_sector_map
        return canonical_vst_sector_map() or {}
    except Exception:
        return {}


def build_waterfall(months_back: int = 36, end_ym=None, sector_map=None, log=print) -> dict:
    """Build the AMC x sector money-flow waterfall cube over the trailing `months_back` months.

    Returns a baked-ready dict:
      {
        "months":  ["2023-07", ..., "2026-06"],          # the months that produced flow
        "amcs":    [...sorted by |net_active| over window...],
        "sectors": [...],
        "cube":    {amc: {sector: {gross:[], price:[], inflow:[], net_active:[]}, "__total__": {...}}},
        "sector_total": {sector: {...}},                 # market, by sector (Σ over AMCs)
        "market_total": {...},                            # the whole market (Σ over everything)
        "meta": {...coverage, reconciliation, caveats...}
      }
    Each array is aligned to `months`. Components reconcile: price + inflow + net_active == gross."""
    h, ret = _load()
    code2amc = _amc_of(h)
    smap = _sector_map(sector_map)

    all_months = sorted(h["ym"].unique())
    if end_ym:
        all_months = [m for m in all_months if m <= end_ym]
    use = all_months[-months_back:] if months_back else all_months

    months_axis = []
    # key=(amc, sector) -> component -> {ym: value};  derive arrays at the end
    cells = defaultdict(lambda: {"gross": {}, "price": {}, "inflow": {}, "net_active": {}})
    # per-month coverage: priced MV that entered the decomposition vs total MV touched
    cov_rows = []

    for ym in use:
        try:
            m, _a, _b, _common, excl_val, tot_val = _pair_flows_active(ym, h, ret)
        except ValueError:
            continue
        priced = m[m["net_active"].notna()].copy()       # the subset where ALL THREE reconcile
        if not len(priced):
            continue
        months_axis.append(ym)
        priced["amc"] = priced["navindia_code"].astype(str).map(code2amc).fillna("Unknown AMC")
        priced["sector"] = priced["vst_id"].map(lambda v: smap.get(v) or "Unclassified")
        priced["price_action"] = priced["gross"] - priced["price_adj"]
        priced["implied_inflow"] = priced["price_adj"] - priced["net_active"]
        grp = priced.groupby(["amc", "sector"]).agg(
            gross=("gross", "sum"), price=("price_action", "sum"),
            inflow=("implied_inflow", "sum"), na=("net_active", "sum")).reset_index()
        for _, r in grp.iterrows():
            c = cells[(r["amc"], r["sector"])]
            c["gross"][ym] = round(float(r["gross"]), 1)
            c["price"][ym] = round(float(r["price"]), 1)
            c["inflow"][ym] = round(float(r["inflow"]), 1)
            c["net_active"][ym] = round(float(r["na"]), 1)
        cov_rows.append({"ym": ym, "priced_mv_cr": round(float(tot_val - excl_val), 1),
                         "excl_mv_cr": round(float(excl_val), 1)})
        if log and len(months_axis) % 6 == 0:
            log(f"[flow_waterfall] {ym}: {len(grp)} AMC x sector cells")

    if not months_axis:
        return {"months": [], "amcs": [], "sectors": [], "cube": {}, "sector_total": {},
                "market_total": {}, "meta": {"error": "no flow months produced"}}

    def _arr(d):   # {ym: v} -> list aligned to months_axis (missing month = 0.0)
        return [d.get(ym, 0.0) for ym in months_axis]

    COMPS = ("gross", "price", "inflow", "net_active")

    # ---- materialise the full cube (pre-pruning) so roll-ups are exact ----
    amc_set = sorted({a for (a, _s) in cells})
    sec_set = sorted({s for (_a, s) in cells})
    full = {a: {} for a in amc_set}
    for (a, s), c in cells.items():
        full[a][s] = {k: _arr(c[k]) for k in COMPS}

    def _sum_arrays(list_of_arr):
        if not list_of_arr:
            return [0.0] * len(months_axis)
        return [round(float(sum(col)), 1) for col in zip(*list_of_arr)]

    # AMC totals (Σ over its sectors); sector totals (Σ over AMCs); market total (Σ over everything)
    for a in amc_set:
        full[a]["__total__"] = {k: _sum_arrays([full[a][s][k] for s in full[a]]) for k in COMPS}
    sector_total = {s: {k: _sum_arrays([full[a][s][k] for a in amc_set if s in full[a]]) for k in COMPS}
                    for s in sec_set}
    market_total = {k: _sum_arrays([sector_total[s][k] for s in sec_set]) for k in COMPS}

    # ---- reconciliation self-audit: price + inflow + net_active == gross, every month ----
    # The identity is EXACT by algebra per (fund,stock) row: (gross-price_adj) + (price_adj-net_active)
    # + net_active == gross. The only residual is DISPLAY ROUNDING — each of ~900 cells/month is rounded
    # to 0.1 cr before summing, so ~sqrt(n_cells)*0.1 cr of random rounding accumulates. We therefore
    # verify the BAKED (rounded) arrays still reconcile to within rounding: |residual| < max(1 cr, 0.05%
    # of the month's gross). A genuine decomposition bug would be a MEANINGFUL fraction of gross, not 1e-4.
    recon = [round(market_total["gross"][i]
                   - (market_total["price"][i] + market_total["inflow"][i] + market_total["net_active"][i]), 2)
             for i in range(len(months_axis))]
    recon_rel = [abs(recon[i]) / (abs(market_total["gross"][i]) + 1.0) for i in range(len(months_axis))]
    recon_ok = all((abs(recon[i]) <= max(_RECON_TOL_CR, 1.0) or recon_rel[i] < 5e-4)
                   for i in range(len(months_axis)))

    # ---- prune the long tail for the BAKED cube (totals already computed) ----
    def _peak_abs_gross(node):  # node = {sector: {comp: arr}} excluding __total__
        peaks = []
        for s, comp in node.items():
            if s == "__total__":
                continue
            peaks.append(max((abs(v) for v in comp["gross"]), default=0.0))
        return max(peaks, default=0.0)

    cube = {}
    for a in amc_set:
        kept = {s: comp for s, comp in full[a].items()
                if s != "__total__" and max((abs(v) for v in comp["gross"]), default=0.0) >= _KEEP_MIN_CR}
        if not kept:
            continue
        kept["__total__"] = full[a]["__total__"]
        cube[a] = kept

    # rank AMCs / sectors by absolute net-active over the window (the conviction read)
    def _abs_na(node_total):
        return float(sum(abs(v) for v in node_total["net_active"]))
    amcs_ranked = sorted(cube.keys(), key=lambda a: _abs_na(cube[a]["__total__"]), reverse=True)
    secs_ranked = sorted(sector_total.keys(), key=lambda s: _abs_na(sector_total[s]), reverse=True)

    meta = {
        "months_back": months_back, "n_months": len(months_axis),
        "n_amcs": len(cube), "n_sectors": len(sector_total),
        "reconciles": recon_ok, "recon_max_abs_cr": (max(abs(x) for x in recon) if recon else 0.0),
        "keep_min_cr": _KEEP_MIN_CR,
        "coverage": cov_rows[-6:],
        "components": {
            "price_action": "gross - price_adj : holdings moved with the market (no decision)",
            "implied_inflow": "price_adj - net_active : fresh scheme money deployed pro-rata (no view change)",
            "net_active": "weight-space conviction trade (inflow-immune) -> the smart-money signal",
        },
        "caveats": [
            "Aggregates only (no per-stock licensed ARM). Safe to bake + publish.",
            "'implied_inflow' is inflow DEPLOYMENT inferred from holdings, NOT raw AMFI subscriptions.",
            "Reconciles on the PRICED subset (holdings whose stock return is known); unpriced MV excluded.",
            "Monthly cadence (disclosures are monthly). Survivorship-aware via the holdings panel.",
        ],
    }
    return {"months": months_axis, "amcs": amcs_ranked, "sectors": secs_ranked,
            "cube": cube, "sector_total": sector_total, "market_total": market_total, "meta": meta}


def _fmt(v):
    return f"{v:+,.0f}"


def _audit(months_back=36):
    """Build + print the reconciliation self-audit and the headline reads (for validation)."""
    wf = build_waterfall(months_back=months_back)
    m = wf["meta"]
    mo = wf["months"]
    print(f"\n=== Ownership & Flow waterfall — P1 cube ===")
    print(f"months: {len(mo)} ({mo[0] if mo else '-'}..{mo[-1] if mo else '-'})  "
          f"AMCs(kept): {m['n_amcs']}  sectors: {m['n_sectors']}")
    print(f"RECONCILES (price+inflow+net_active == gross, every month): {m['reconciles']}  "
          f"(max abs residual {m['recon_max_abs_cr']} cr)")
    if not mo:
        return wf
    # latest-month market split
    i = len(mo) - 1
    mt = wf["market_total"]
    print(f"\nMARKET, latest month {mo[i]} (Rs cr):")
    print(f"  gross {_fmt(mt['gross'][i])}  =  price {_fmt(mt['price'][i])}  "
          f"+ implied-inflow {_fmt(mt['inflow'][i])}  + net-active {_fmt(mt['net_active'][i])}")
    # trailing-3M net-active by sector (compare to the consensus bake's read)
    def _t3(arr):
        return round(float(sum(arr[-3:])), 0)
    print(f"\nTrailing-3M NET-ACTIVE by sector (Rs cr) — the conviction tilt:")
    rows = sorted(wf["sector_total"].items(), key=lambda kv: _t3(kv[1]["net_active"]), reverse=True)
    for s, comp in rows:
        print(f"  {s:<26} {_fmt(_t3(comp['net_active'])):>12}   "
              f"(price {_fmt(_t3(comp['price'])):>12}  inflow {_fmt(_t3(comp['inflow'])):>12})")
    # top AMC x sector net-active cells (latest month)
    print(f"\nTop AMC x sector NET-ACTIVE bets, latest month {mo[i]} (Rs cr):")
    flat = []
    for a, secs in wf["cube"].items():
        for s, comp in secs.items():
            if s == "__total__":
                continue
            flat.append((a, s, comp["net_active"][i]))
    for a, s, v in sorted(flat, key=lambda r: abs(r[2]), reverse=True)[:15]:
        print(f"  {a:<28} {s:<22} {_fmt(v):>12}")
    return wf


if __name__ == "__main__":
    _audit()
