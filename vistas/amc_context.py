"""
amc_context.py — assemble the REAL data context for the Digital AMC agents.

Reads the already-baked terminal data (no new fetch, no network):
  - output/terminal_site/data/_screens/smart_vs_street.json  (per-stock: sector, ARM, flows,
    growth, quadrant, ownership, mcap)  -> the ANALYST desks (one per sector group)
  - output/terminal_site/data/funds_attribution/<id>.json    (per-fund: IR, IC, TE, verdict,
    benchmark, category)                                       -> the FUND-MANAGER desks

Emits output/_amc/context.json = a compact, prompt-ready context per desk. Display-plane only;
touches no analytics.py formula. This is the input the agentic org (analysts -> FMs -> CIO) reads.

Run:  python -m vistas.amc_context
"""
from __future__ import annotations
import json, os, math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "output" / "terminal_site" / "data"
OUT  = ROOT / "output" / "_amc"
OUT.mkdir(parents=True, exist_ok=True)

# ── Analyst desks: consolidate the NSE macro-sectors into named coverage desks ───────────────
ANALYST_DESKS = {
    "Technology":        {"name": "IT & Technology",      "sectors": ["Information Technology"]},
    "Financials":        {"name": "Banking & Financials", "sectors": ["Financial Services"]},
    "Healthcare":        {"name": "Pharma & Healthcare",  "sectors": ["Healthcare"]},
    "Consumer":          {"name": "Consumer",             "sectors": ["Fast Moving Consumer Goods", "Consumer Services", "Consumer Durables", "Textiles"]},
    "Auto":              {"name": "Auto & Mobility",      "sectors": ["Automobile and Auto Components"]},
    "Industrials":       {"name": "Industrials & Capital Goods", "sectors": ["Capital Goods", "Construction", "Services"]},
    "Energy":            {"name": "Energy & Power",       "sectors": ["Oil Gas & Consumable Fuels", "Power", "Utilities"]},
    "Materials":         {"name": "Metals & Materials",   "sectors": ["Metals & Mining", "Chemicals", "Construction Materials", "Forest Materials"]},
    "RealtyInfra":       {"name": "Realty & Infrastructure", "sectors": ["Realty"]},
    "TelecomMedia":      {"name": "Telecom & Media",      "sectors": ["Telecommunication", "Media Entertainment & Publication"]},
    "Diversified":       {"name": "Diversified & Special Situations", "sectors": ["Other", "Diversified"]},
}

# ── FM desks: real SEBI categories (+ a name-matched Banking & Quant) ─────────────────────────
FM_DESKS = [
    {"key": "flexicap", "name": "Flexi Cap",  "match": {"category": ["Flexi Cap Fund"]}},
    {"key": "largecap", "name": "Large Cap",  "match": {"category": ["Large Cap Fund"]}},
    {"key": "midcap",   "name": "Mid Cap",    "match": {"category": ["Mid Cap Fund"]}},
    {"key": "multicap", "name": "Multi Cap",  "match": {"category": ["Multi Cap Fund"]}},
    {"key": "value",    "name": "Value",      "match": {"category": ["Value Fund", "Contra Fund"]}},
    {"key": "banking",  "name": "Banking & Financial Services", "match": {"category": ["Sectoral / Thematic"], "name_kw": ["bank", "financ", "bfsi"]}},
    {"key": "quant",    "name": "Quant / Systematic", "match": {"name_kw": ["quant"]}},
]

def _f(x, d=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except Exception:
        return d

def _wmean(vals, wts):
    num = sum(v * w for v, w in zip(vals, wts)); den = sum(wts)
    return (num / den) if den else float("nan")

def build_analyst_context(rows):
    sec_to_desk = {}
    for dk, d in ANALYST_DESKS.items():
        for s in d["sectors"]:
            sec_to_desk[s] = dk
    desks = {dk: [] for dk in ANALYST_DESKS}
    for r in rows:
        dk = sec_to_desk.get(r.get("sector"))
        if dk:
            desks[dk].append(r)
    out = {}
    for dk, items in desks.items():
        if not items:
            continue
        items = [r for r in items if r.get("arm") is not None]
        arms = [_f(r.get("arm")) for r in items if r.get("arm") is not None]
        mcaps = [_f(r.get("mcap_cr")) for r in items]
        arm_ew = round(sum(arms) / len(arms), 1) if arms else None
        arm_ff = round(_wmean(arms, [_f(r.get("mcap_cr")) for r in items if r.get("arm") is not None]), 1) if arms else None
        by_mcap = sorted(items, key=lambda r: _f(r.get("mcap_cr")), reverse=True)
        by_arm  = sorted([r for r in items if r.get("arm") is not None], key=lambda r: _f(r.get("arm")), reverse=True)
        by_flow = sorted(items, key=lambda r: _f(r.get("flow_3m")), reverse=True)
        by_grow = sorted(items, key=lambda r: _f(r.get("pat_yoy")), reverse=True)
        def pack(r):
            return {
                "sym": r.get("symbol"), "name": r.get("name"),
                "mcap_cr": round(_f(r.get("mcap_cr"))), "arm": r.get("arm"),
                "flow_3m": round(_f(r.get("flow_3m")), 1), "pat_yoy": round(_f(r.get("pat_yoy")) * 100, 1),
                "quadrant": r.get("quadrant_3m"), "fii_chg": r.get("own_fii"),
            }
        quad = {}
        for r in items:
            q = r.get("quadrant_3m") or "?"; quad[q] = quad.get(q, 0) + 1
        recommending = sum(1 for r in items if r.get("recommending"))
        out[dk] = {
            "desk": ANALYST_DESKS[dk]["name"], "coverage_n": len(items),
            "arm_ew": arm_ew, "arm_ff": arm_ff, "recommending_n": recommending,
            "quadrants": quad,
            "top_by_mcap": [pack(r) for r in by_mcap[:12]],
            "arm_leaders": [pack(r) for r in by_arm[:6]],
            "arm_laggards": [pack(r) for r in by_arm[-6:]][::-1],
            "flow_accumulated": [pack(r) for r in by_flow[:6]],
            "flow_distributed": [pack(r) for r in by_flow[-6:]][::-1],
            "growth_leaders": [pack(r) for r in by_grow[:6]],
        }
    return out

def build_fm_context():
    fdir = SITE / "funds_attribution"
    funds = []
    for fp in fdir.glob("*.json"):
        if fp.name.startswith("_"):
            continue
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        funds.append(d)
    out = {}
    for desk in FM_DESKS:
        m = desk["match"]
        cats = m.get("category"); kws = m.get("name_kw")
        sel = []
        for d in funds:
            cat = str(d.get("sebi_category") or "")
            nm = str(d.get("scheme_name") or "").lower()
            ok = True
            if cats is not None:
                ok = ok and (cat in cats)
            if kws is not None:
                ok = ok and any(k in nm for k in kws)
            if ok:
                sel.append(d)
        if not sel:
            continue
        # exemplars: rank by info_ratio (skill), keep top few with enough history
        sel_h = [d for d in sel if _f(d.get("years")) >= 3]
        sel_h = sel_h or sel
        sel_h.sort(key=lambda d: _f(d.get("info_ratio")), reverse=True)
        irs = sorted(_f(d.get("info_ratio")) for d in sel if d.get("info_ratio") is not None)
        med_ir = round(irs[len(irs) // 2], 2) if irs else None
        def packf(d):
            return {
                "scheme": d.get("scheme_name"), "amc": d.get("amc"),
                "benchmark": d.get("benchmark"), "category": d.get("sebi_category"),
                "years": round(_f(d.get("years")), 1),
                "info_ratio": round(_f(d.get("info_ratio")), 2),
                "ic_mean": round(_f(d.get("ic_mean")), 3), "ic_t": round(_f(d.get("ic_t")), 1),
                "tracking_error": round(_f(d.get("tracking_error")) * 100, 1),
                "excess_cagr": round(_f(d.get("excess_cagr")) * 100, 2),
                "hit_rate": round(_f(d.get("hit_rate_monthly")) * 100, 1),
                "active_share": None,
                "verdict": d.get("verdict"), "verdict_why": d.get("verdict_why"),
            }
        out[desk["key"]] = {
            "desk": desk["name"], "n_funds": len(sel), "median_ir": med_ir,
            "benchmark": (sel_h[0].get("benchmark") if sel_h else None),
            "exemplars": [packf(d) for d in sel_h[:3]],
        }
    return out

def main():
    screens = json.loads((SITE / "_screens" / "smart_vs_street.json").read_text(encoding="utf-8"))
    rows = screens.get("rows") or screens.get("stocks") or []
    asof = screens.get("holdings_asof") or screens.get("default_window")
    ctx = {
        "generated_for": "Digital AMC",
        "data_asof": asof,
        "universe_n": len(rows),
        "analysts": build_analyst_context(rows),
        "fund_managers": build_fm_context(),
    }
    (OUT / "context.json").write_text(json.dumps(ctx, indent=2, default=str), encoding="utf-8")

    # per-desk slice files (each agent Reads only its own slice — avoids passing 136K as args)
    desks = OUT / "desks"; desks.mkdir(exist_ok=True)
    for k, d in ctx["analysts"].items():
        (desks / f"analyst_{k}.json").write_text(json.dumps({"key": k, **d}, indent=1, default=str), encoding="utf-8")
    for k, d in ctx["fund_managers"].items():
        (desks / f"fm_{k}.json").write_text(json.dumps({"key": k, **d}, indent=1, default=str), encoding="utf-8")
    market = {
        "data_asof": ctx["data_asof"], "universe_n": ctx["universe_n"],
        "sectors": {k: {"desk": v["desk"], "arm_ew": v["arm_ew"], "arm_ff": v["arm_ff"],
                        "coverage_n": v["coverage_n"], "recommending_n": v["recommending_n"],
                        "quadrants": v["quadrants"]} for k, v in ctx["analysts"].items()},
        "fm_categories": {k: {"desk": v["desk"], "median_ir": v["median_ir"], "n_funds": v["n_funds"],
                              "benchmark": v.get("benchmark")} for k, v in ctx["fund_managers"].items()},
    }
    (desks / "market.json").write_text(json.dumps(market, indent=1, default=str), encoding="utf-8")
    print(f"[amc_context] wrote {len(ctx['analysts'])+len(ctx['fund_managers'])+1} desk slice files -> {desks}")

    na = len(ctx["analysts"]); nf = len(ctx["fund_managers"])
    print(f"[amc_context] wrote {OUT/'context.json'}  ({na} analyst desks, {nf} FM desks, "
          f"{len(rows)} stocks, asof {asof})")
    for dk, d in ctx["analysts"].items():
        print(f"   analyst {d['desk']:34s} cov={d['coverage_n']:3d}  ARM ew={d['arm_ew']} ff={d['arm_ff']}")
    for k, d in ctx["fund_managers"].items():
        print(f"   FM      {d['desk']:34s} funds={d['n_funds']:3d}  medIR={d['median_ir']}")

if __name__ == "__main__":
    main()
