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
import re
from collections import defaultdict

import numpy as np
import pandas as pd

from .funds_flows import _load, _pair_flows_active, _prev_ym

# a cell is kept in the output cube only if its peak monthly |gross| clears this (Rs cr) — prunes the
# long tail of near-zero AMC x sector pairs so the baked artifact stays small. Roll-up TOTALS are
# computed BEFORE pruning, so nothing is lost from the AMC/sector/market aggregates.
_KEEP_MIN_CR = 5.0
_RECON_TOL_CR = 0.5            # reconciliation must hold to well under a crore
# the per-AMC drill-down (P3): a SCHEME is kept in its AMC file only if its peak monthly |gross| clears
# this — drops debt/liquid/no-equity schemes so each lazy-loaded AMC file stays lean.
_DRILL_MIN_CR = 5.0
# the stock-leaf (P4): under each scheme, keep the top-N holdings by peak ownership UNION any holding whose
# peak ownership (MV held) exceeds the floor — bounds the per-AMC file while covering every big position.
_STOCK_TOPN = 15
_STOCK_MIN_CR = 100.0
# cross-AMC crowding (P4b): a STOCK gets a per-stock "who's tilting" file only if total MF ownership of it
# peaks above this (Rs cr) — keeps the lazy file set to the stocks that actually matter.
_CROWD_MIN_CR = 300.0


def _amc_of(h: pd.DataFrame) -> dict:
    """navindia_code -> AMC name (stable per scheme; last non-null wins)."""
    sub = h[["navindia_code", "amc"]].dropna().drop_duplicates(subset=["navindia_code"], keep="last")
    return dict(zip(sub["navindia_code"].astype(str), sub["amc"].astype(str)))


def _scheme_name_of(h: pd.DataFrame) -> dict:
    """navindia_code -> scheme display name (last non-null wins)."""
    sub = h[["navindia_code", "scheme_name"]].dropna().drop_duplicates(subset=["navindia_code"], keep="last")
    return dict(zip(sub["navindia_code"].astype(str), sub["scheme_name"].astype(str)))


def _stock_meta_of(h: pd.DataFrame) -> dict:
    """vst_id -> (display_name, nse_symbol) for the stock-leaf labels (last non-null wins)."""
    sub = h[["vst_id", "vid_name", "nse_symbol"]].copy()
    sub["vst_id"] = sub["vst_id"].astype(str)
    sub = sub[sub["vst_id"].str.strip() != ""].drop_duplicates(subset=["vst_id"], keep="last")
    out = {}
    for _, r in sub.iterrows():
        vid = r["vst_id"]
        nm = str(r["vid_name"]) if pd.notna(r["vid_name"]) else vid
        sym = str(r["nse_symbol"]) if pd.notna(r["nse_symbol"]) else ""
        out[vid] = (nm, sym)
    return out


def amc_slug(name: str) -> str:
    """AMC name -> a stable, filesystem/URL-safe slug for its lazy drill-down file."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s or "amc"


# ---- P4 THEME lens: NSE thematic-index membership (vst_id -> [themes]) ------------------------------
# Cross-sector NSE thematic indices (a PARALLEL lens to the macro-sector backbone). A stock can belong to
# several themes, so theme flows OVERLAP and are NOT additive to the market total (labeled in the UI).
# Membership is fetched ONCE (niftyindices.com via benchmarks.fetch_constituents) into a small committed
# data file, so the deck build needs no network. Refresh with `python -m vistas.flow_waterfall --themes`.
_THEME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "themes")
_THEME_FILE = os.path.join(_THEME_DIR, "theme_constituents.json")
_THEME_SLUGS = {
    "Nifty Energy": "ind_niftyenergylist",
    "Nifty Infrastructure": "ind_niftyinfralist",
    "Nifty Consumption": "ind_niftyconsumptionlist",
    "Nifty Commodities": "ind_niftycommoditieslist",
    "Nifty CPSE": "ind_niftycpselist",
    "Nifty PSU (PSE)": "ind_niftypselist",
    "Nifty Healthcare": "ind_niftyhealthcarelist",
    "Nifty MNC": "ind_niftymnclist",
    "Nifty Services": "ind_niftyservicelist",
    "Nifty India Manufacturing": "ind_niftyindiamanufacturinglist",
    "Nifty India Digital": "ind_niftyindiadigitallist",
    "Nifty India Defence": "ind_niftyindiadefencelist",
    "Nifty EV & New Age Auto": "ind_niftyevnewageautomotivelist",
    "Nifty Capital Markets": "ind_niftycapitalmarketslist",
    "Nifty Housing": "ind_niftyhousinglist",
}


def build_theme_map(out_path: str = _THEME_FILE, slugs: dict | None = None, log=print) -> dict:
    """Fetch NSE thematic-index constituents and write {vst_id: [theme names]} to a local file.
    Best-effort: a slug that fails to fetch (or maps to no held vst_id) is skipped + logged. Returns
    the written dict {themes, counts, vst2themes}."""
    from . import benchmarks
    slugs = slugs or _THEME_SLUGS
    by_isin, by_sym = benchmarks._load_identity()
    vst2themes, counts = defaultdict(list), {}
    for name, slug in slugs.items():
        try:
            df = benchmarks.fetch_constituents(slug)
        except Exception as e:
            if log:
                log(f"[themes] {name}: fetch error {str(e)[:80]}")
            continue
        if df is None or not len(df):
            if log:
                log(f"[themes] {name}: no constituents (skip)")
            continue
        vids = set()
        for _, r in df.iterrows():
            vid = by_isin.get(str(r.get("isin"))) or by_sym.get(str(r.get("symbol")))
            if vid:
                vids.add(vid)
        if not vids:
            continue
        counts[name] = len(vids)
        for vid in sorted(vids):
            vst2themes[vid].append(name)
        if log:
            log(f"[themes] {name}: {len(vids)} vst_ids")
    out = {"themes": sorted(counts.keys()), "counts": counts, "vst2themes": dict(vst2themes)}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    if log:
        log(f"[themes] wrote {len(counts)} themes covering {len(vst2themes)} stocks -> {out_path}")
    return out


def _load_theme_map(path: str = _THEME_FILE) -> dict:
    """vst_id -> [theme names], from the committed theme file. {} if absent (graceful-degrade)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("vst2themes", {}) or {}
    except Exception:
        return {}


def _theme_counts(path: str = _THEME_FILE) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("counts", {}) or {}
    except Exception:
        return {}


def _sector_map(explicit=None) -> dict:
    if explicit is not None:
        return explicit
    try:
        from .funds_portfolio_viz import canonical_vst_sector_map
        return canonical_vst_sector_map() or {}
    except Exception:
        return {}


def build_waterfall(months_back: int = 36, end_ym=None, sector_map=None, with_drilldown: bool = False,
                    theme_map=None, log=print) -> dict:
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
    code2name = _scheme_name_of(h)
    vst_meta = _stock_meta_of(h) if with_drilldown else {}
    tmap = theme_map if theme_map is not None else (_load_theme_map() if with_drilldown else {})
    smap = _sector_map(sector_map)

    all_months = sorted(h["ym"].unique())
    if end_ym:
        all_months = [m for m in all_months if m <= end_ym]
    use = all_months[-months_back:] if months_back else all_months

    months_axis = []
    # key=(amc, sector) -> component -> {ym: value};  derive arrays at the end
    cells = defaultdict(lambda: {"gross": {}, "price": {}, "inflow": {}, "net_active": {}, "mv": {}})
    # P3 drill-down: key=(navindia_code, sector) -> component -> {ym: value} (per-SCHEME, same loop)
    scheme_cells = defaultdict(lambda: {"gross": {}, "price": {}, "inflow": {}, "net_active": {}, "mv": {}})
    # P4 stock leaf: key=(navindia_code, vst_id) -> component -> {ym: value} (per-SCHEME x stock, same loop)
    stock_cells = defaultdict(lambda: {"gross": {}, "price": {}, "inflow": {}, "net_active": {}, "mv": {}})
    # P4 theme lens: theme -> component -> {ym: running sum} (OVERLAPPING; NOT additive to the market)
    theme_cells = defaultdict(lambda: {"gross": defaultdict(float), "price": defaultdict(float),
                                       "inflow": defaultdict(float), "net_active": defaultdict(float),
                                       "mv": defaultdict(float)})
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
            inflow=("implied_inflow", "sum"), na=("net_active", "sum"), mv=("mv_e", "sum")).reset_index()
        for _, r in grp.iterrows():
            c = cells[(r["amc"], r["sector"])]
            c["gross"][ym] = round(float(r["gross"]), 1)
            c["price"][ym] = round(float(r["price"]), 1)
            c["inflow"][ym] = round(float(r["inflow"]), 1)
            c["net_active"][ym] = round(float(r["na"]), 1)
            c["mv"][ym] = round(float(r["mv"]), 1)
        if with_drilldown:                                   # per-SCHEME (navindia_code) x sector, same priced rows
            sgrp = priced.groupby(["navindia_code", "sector"]).agg(
                gross=("gross", "sum"), price=("price_action", "sum"),
                inflow=("implied_inflow", "sum"), na=("net_active", "sum"), mv=("mv_e", "sum")).reset_index()
            for _, r in sgrp.iterrows():
                sc = scheme_cells[(str(r["navindia_code"]), r["sector"])]
                sc["gross"][ym] = round(float(r["gross"]), 1)
                sc["price"][ym] = round(float(r["price"]), 1)
                sc["inflow"][ym] = round(float(r["inflow"]), 1)
                sc["net_active"][ym] = round(float(r["na"]), 1)
                sc["mv"][ym] = round(float(r["mv"]), 1)
            tgrp = priced.groupby(["navindia_code", "vst_id"]).agg(   # per-SCHEME x STOCK (the P4 leaf)
                gross=("gross", "sum"), price=("price_action", "sum"),
                inflow=("implied_inflow", "sum"), na=("net_active", "sum"), mv=("mv_e", "sum")).reset_index()
            for _, r in tgrp.iterrows():
                tc = stock_cells[(str(r["navindia_code"]), str(r["vst_id"]))]
                tc["gross"][ym] = round(float(r["gross"]), 1)
                tc["price"][ym] = round(float(r["price"]), 1)
                tc["inflow"][ym] = round(float(r["inflow"]), 1)
                tc["net_active"][ym] = round(float(r["na"]), 1)
                tc["mv"][ym] = round(float(r["mv"]), 1)
            if tmap:                                              # market-by-stock -> distribute to themes
                mstock = priced.groupby("vst_id").agg(
                    gross=("gross", "sum"), price=("price_action", "sum"),
                    inflow=("implied_inflow", "sum"), na=("net_active", "sum"), mv=("mv_e", "sum")).reset_index()
                for _, r in mstock.iterrows():
                    ths = tmap.get(str(r["vst_id"]))
                    if not ths:
                        continue
                    g, p, inf, na, mvv = (float(r["gross"]), float(r["price"]), float(r["inflow"]),
                                          float(r["na"]), float(r["mv"]))
                    for th in ths:
                        tc = theme_cells[th]
                        tc["gross"][ym] += g
                        tc["price"][ym] += p
                        tc["inflow"][ym] += inf
                        tc["net_active"][ym] += na
                        tc["mv"][ym] += mvv
        cov_rows.append({"ym": ym, "priced_mv_cr": round(float(tot_val - excl_val), 1),
                         "excl_mv_cr": round(float(excl_val), 1)})
        if log and len(months_axis) % 6 == 0:
            log(f"[flow_waterfall] {ym}: {len(grp)} AMC x sector cells")

    if not months_axis:
        return {"months": [], "amcs": [], "sectors": [], "cube": {}, "sector_total": {},
                "market_total": {}, "drilldown": {}, "drill_index": {}, "crowd": {}, "crowd_index": [],
                "theme_total": {}, "themes": [], "theme_meta": {},
                "meta": {"error": "no flow months produced"}}

    def _arr(d):   # {ym: v} -> list aligned to months_axis (missing month = 0.0)
        return [d.get(ym, 0.0) for ym in months_axis]

    COMPS = ("gross", "price", "inflow", "net_active", "mv")

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

    # ---- P3 drill-down: per-AMC {scheme: {sector arrays + total}}, written to lazy files by the deck ----
    # Every level still reconciles (each scheme total = Σ its sectors; each a Σ of per-row identities).
    # `mv` = priced ownership value (Σ mv_end), consistent with the flow rows shown alongside it.
    drilldown, drill_index = {}, {}
    crowd, crowd_index = {}, []
    if with_drilldown:
        cells_by_code = defaultdict(dict)
        for (code, sec), comp in scheme_cells.items():
            cells_by_code[code][sec] = comp
        stock_by_code = defaultdict(dict)                     # P4: per-scheme stock cells, by scheme code
        for (code, vid), comp in stock_cells.items():
            stock_by_code[code][vid] = comp
        by_amc = defaultdict(list)
        for code in cells_by_code:
            by_amc[code2amc.get(code, "Unknown AMC")].append(code)
        for amc, clist in by_amc.items():
            schemes = []
            for code in clist:
                secs = cells_by_code[code]
                sec_out = {s: {k: _arr(secs[s][k]) for k in COMPS} for s in secs}
                total = {k: _sum_arrays([sec_out[s][k] for s in sec_out]) for k in COMPS}
                if max((abs(v) for v in total["gross"]), default=0.0) < _DRILL_MIN_CR:
                    continue                                  # drop debt/liquid/no-equity schemes
                # P4 stock leaves: top-N holdings by peak ownership UNION any peak MV > floor, nested by sector
                ranked = []
                for vid, comp in stock_by_code.get(code, {}).items():
                    peak_mv = max((abs(v) for v in comp["mv"].values()), default=0.0)
                    ranked.append((vid, peak_mv, comp))
                ranked.sort(key=lambda x: x[1], reverse=True)
                keep = set(v for v, _pk, _c in ranked[:_STOCK_TOPN]) | set(v for v, pk, _c in ranked if pk > _STOCK_MIN_CR)
                for vid, _pk, comp in ranked:
                    if vid not in keep:
                        continue
                    sec = smap.get(vid) or "Unclassified"
                    if sec not in sec_out:
                        continue
                    nm, sym = vst_meta.get(vid, (vid, ""))
                    leaf = {"name": nm, "sym": sym, "vst_id": vid}
                    for k in COMPS:
                        leaf[k] = _arr(comp[k])
                    sec_out[sec].setdefault("stocks", []).append(leaf)
                schemes.append({"name": code2name.get(code, code), "code": code,
                                "total": total, "sectors": sec_out})
            if not schemes:
                continue
            schemes.sort(key=lambda x: sum(abs(v) for v in x["total"]["net_active"]), reverse=True)
            slug = amc_slug(amc)
            drilldown[amc] = {"amc": amc, "slug": slug, "months": months_axis, "schemes": schemes}
            drill_index[amc] = slug

        # ---- P4b cross-AMC crowding: per STOCK, the AMCs trading it (lazy per-stock files) ----
        # Aggregate the per-scheme stock cells up to AMC level, then group by stock -> "who is
        # buying/selling this stock?" (the inverse of the per-AMC drill-down). Sector crowding needs
        # no extra data — it reads the inline AMC×sector cube directly in the front-end.
        # vid -> amc -> comp -> {ym: sum}  (four levels; leaf is a float accumulator)
        amc_stock = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float))))
        for (code, vid), comp in stock_cells.items():
            dst = amc_stock[vid][code2amc.get(code, "Unknown AMC")]
            for k in COMPS:
                for ym, v in comp[k].items():
                    dst[k][ym] += v
        for vid, amcs in amc_stock.items():
            amcs_out = {a: {k: [round(comp[k].get(ym, 0.0), 1) for ym in months_axis] for k in COMPS}
                        for a, comp in amcs.items()}
            peak_own = max((sum(amcs_out[a]["mv"][i] for a in amcs_out) for i in range(len(months_axis))), default=0.0)
            if peak_own < _CROWD_MIN_CR:
                continue                                  # only stocks with material total MF ownership
            nm, sym = vst_meta.get(vid, (vid, ""))
            crowd[vid] = {"vst_id": vid, "name": nm, "sym": sym, "sector": smap.get(vid) or "Unclassified",
                          "months": months_axis, "amcs": amcs_out}
            crowd_index.append({"vst_id": vid, "name": nm, "sym": sym, "sector": smap.get(vid) or "Unclassified"})
        crowd_index.sort(key=lambda x: x["name"])

    # ---- P4 theme totals (OVERLAPPING NSE thematic indices — a parallel lens, NOT additive) ----
    theme_total, themes_ranked = {}, []
    if theme_cells:
        theme_total = {th: {k: [round(comp[k].get(ym, 0.0), 1) for ym in months_axis] for k in COMPS}
                       for th, comp in theme_cells.items()}
        themes_ranked = sorted(theme_total.keys(),
                               key=lambda t: sum(abs(v) for v in theme_total[t]["net_active"]), reverse=True)

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
            "cube": cube, "sector_total": sector_total, "market_total": market_total,
            "drilldown": drilldown, "drill_index": drill_index,
            "crowd": crowd, "crowd_index": crowd_index,
            "theme_total": theme_total, "themes": themes_ranked,
            "theme_meta": {"counts": _theme_counts(),
                           "caveat": ("NSE thematic indices OVERLAP — a stock counts in every theme it "
                                      "belongs to, so theme rows are NOT additive to the market total. "
                                      "Flow into a theme = the 3-way decomposition summed over the funds' "
                                      "holdings of that theme's constituents.")},
            "meta": meta}


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
    import sys
    if "--themes" in sys.argv:        # refresh the NSE thematic-index membership file (needs network)
        build_theme_map()
    else:
        _audit()
