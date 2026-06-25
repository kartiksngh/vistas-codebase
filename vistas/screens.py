"""
screens.py — cross-sectional STOCK SCREENS for the terminal. First screen: "Smart-money vs the Street".

SMART-MONEY vs STREET (Analyst × FM 4-quadrant), over the NSE-500, on a watchlist of stocks in BOTH price
correction AND deteriorating fundamentals — fusing two signals nothing else combines:
  • Analyst axis  = StarMine ARM (Analyst Revision Model, 0-100). >=50 = net upgrades = "recommending".
  • FM axis       = our CORP-ACTION-IMMUNE net active flow (end − start·(1+TR), merger-bridged). Default
                    window = TRAILING 3 MONTHS (conviction = persistence; one month is noise — a single
                    fund's rebalance/NFO/redemption dominates). Latest-month flow + breadth (buyers−sellers,
                    size-neutral) are carried alongside, and 3M-vs-1M AGREEMENT is flagged (confirmed vs
                    inflecting) so an inflection isn't hidden by the smoother.

Quadrants (on the chosen window): Q1 rec+buy · Q2 rec+not-buy · Q3 not-rec+buy (FM ahead of the street) ·
Q4 neither. Pre-filter: 6M total return < 0 AND >= DD_OFF_HIGH% off the 52w high AND TTM EPS|PAT YoY < 0.

★ The deterioration axis is made CORP-ACTION-AWARE here (the flow axis already is): EPS YoY alone is
distorted by demergers/one-offs (e.g. ITC hotels demerger), so each name is tagged operating-deteriorating
(EPS AND EBITDA both down = genuine) vs headline-only (EPS down but EBITDA up = below-the-line/one-off), and
an explicit one-off flag fires on |EPS YoY| > 80%.

Reads the deck's freshly-built per-stock quant + fundamentals JSONs (same process, so complete) + the
holdings store (for the per-stock AMC list that powers the cockpit's AMC filter). Display-plane: Python-baked
values, no JS-parity port. Never raises; returns {} on failure.
"""
from __future__ import annotations

import os
import json
import pandas as pd

RET6M_MAX = 0.0          # 6M total return < 0 = price correction
DD_OFF_HIGH = -10.0      # at least 10% below the 52-week high
ARM_REC = 50.0           # ARM >= 50 = analysts net-recommending
ONEOFF_EPS = 0.80        # |TTM EPS YoY| > 80% -> flag as one-off / corporate-action distorted
ARM_STALE_DAYS = 90      # ARM is a ~1-month signal; a score not revised in >90d is stale (NOT "recommending")


def _arm_is_stale(asof, ref, days=ARM_STALE_DAYS):
    """True if the ARM score's last change-point (asof) is more than `days` before the data's currency
    date (ref). ARM mean-reverts in weeks, so a months-old score is uninformative, not a live upgrade."""
    import datetime as _dt
    if not asof or not ref:
        return False
    try:
        return (_dt.date.fromisoformat(str(ref)[:10]) - _dt.date.fromisoformat(str(asof)[:10])).days > days
    except Exception:
        return False


def _jload(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def _amc_holdings(root):
    """ONE read of holdings_history (latest month) -> (amc_list, amc_cr, amc_aum, held_syms, ym).
      amc_list  : vst_id -> sorted [AMC short names] holding it
      amc_cr    : vst_id -> {AMC -> ₹cr held in THIS stock (summed across that AMC's funds)}
      amc_aum   : AMC -> total disclosed AUM (₹cr; sum of market_value across ALL its holdings)
      held_syms : set of nse_symbol held by any MF this month (the screen universe)
    market_value is ₹ crore (verified). Powers the AMC magnitude filters + the MF-ownership drill-down."""
    try:
        h = pd.read_parquet(os.path.join(root, "data", "funds", "history", "holdings_history.parquet"),
                            columns=["ym", "amc", "vst_id", "nse_symbol", "market_value"])
    except Exception:
        return {}, {}, {}, set(), None
    ym = h["ym"].max()
    b = h[h["ym"] == ym]
    amc_aum = {str(a): round(float(g["market_value"].sum()))
               for a, g in b.dropna(subset=["amc"]).groupby("amc") if float(g["market_value"].sum()) > 0}
    held = b.dropna(subset=["vst_id", "amc"])
    amc_list, amc_cr = {}, {}
    for vid, g in held.groupby("vst_id"):
        cr = {str(a): round(float(v), 1) for a, v in g.groupby("amc")["market_value"].sum().items() if v and v > 0}
        if cr:
            amc_cr[vid] = cr
            amc_list[vid] = sorted(cr.keys())
    held_syms = set(str(s) for s in b["nse_symbol"].dropna().unique() if str(s).strip())
    return amc_list, amc_cr, amc_aum, held_syms, ym


def build_smart_vs_street(site_data_dir, root, nse500=None, progress=None):
    """Build the Smart-money-vs-Street screen JSON. `site_data_dir` = the built site's data/ dir (holds
    quant/ + fundamentals/); `root` = repo root (for the holdings store + benchmark fallback)."""
    log = progress or (lambda m: None)
    if nse500 is None:
        nse500 = (_jload(os.path.join(site_data_dir, "benchmarks", "ind_nifty500list.json"))
                  or _jload(os.path.join(root, "data", "benchmarks", "ind_nifty500list.json")))
    if not nse500:
        log("[screens] no NSE-500 constituents — skipped")
        return {}
    cons = {c["symbol"]: c for c in nse500.get("constituents", [])}
    amc_list, amc_cr, amc_aum, held_syms, hold_ym = _amc_holdings(root)
    try:                                          # market cap (AMFI 6-mo avg) for MF-ownership-%-of-mcap
        from . import shares as _sh
        mcap = _sh.mcap_resolved() or {}
    except Exception:
        mcap = {}
    try:                                          # ARM data-currency date, to flag stale per-stock scores
        from . import arm as _armmod
        arm_ref = (_armmod.compiled_meta() or {}).get("date_max")
    except Exception:
        arm_ref = None

    # universe = NSE-500 constituents UNION every stock any MF holds this month (the names with an FM axis).
    universe = sorted(set(cons.keys()) | set(held_syms))
    rows = []
    for sym in universe:
        q = _jload(os.path.join(site_data_dir, "quant", sym + ".json"))
        fu = _jload(os.path.join(site_data_dir, "fundamentals", sym + ".json"))
        if not q and not fu:
            continue
        q = q or {}
        fu = fu or {}
        c = cons.get(sym) or {                    # held-only (non-NSE-500): synthesize identity from the JSONs
            "symbol": sym, "name": q.get("name") or fu.get("name") or sym,
            "sector": ((q.get("market") or {}).get("industry") or (q.get("market") or {}).get("sector_index") or "Other"),
            "vst_id": fu.get("vst_id") or ((q.get("smart_money_flow") or {}).get("vst_id")),
        }
        mk = q.get("market", {}) or {}
        ret6 = (mk.get("returns") or {}).get("6M")
        dd = (mk.get("drawdown") or {}).get("from_52w_high")
        gr = (fu.get("analytics", {}) or {}).get("growth", {}) or {}
        eps = (gr.get("EPS") or {}).get("ttm_yoy")
        pat = (gr.get("PAT") or {}).get("ttm_yoy")
        ebitda = (gr.get("EBITDA") or {}).get("ttm_yoy")
        sales = (gr.get("Sales") or {}).get("ttm_yoy")
        arm = ((fu.get("starmine") or {}).get("headline", {}) or {}).get("score")
        arm_asof = (fu.get("starmine") or {}).get("asof")
        arm_stale = _arm_is_stale(arm_asof, arm_ref)
        smf = q.get("smart_money_flow") or {}
        flowser = [x for x in (smf.get("flow") or []) if isinstance(x, (int, float))]
        flow_1m = flowser[-1] if flowser else 0.0
        flow_3m = sum(flowser[-3:]) if flowser else 0.0
        flow_6m = sum(flowser[-6:]) if flowser else 0.0
        flow_12m = sum(flowser[-12:]) if flowser else 0.0
        buyers = (smf.get("buyers") or [None])[-1]
        sellers = (smf.get("sellers") or [None])[-1]
        vid = c.get("vst_id")
        # ownership (latest quarterly %), MF holdings + MF-as-%-of-mcap, key 3-statement growth (cagr)
        holders = ((q.get("ownership") or {}).get("holders") or {})
        def _ownpct(cat):
            return (holders.get(cat) or {}).get("latest_pct")
        own_prom, own_fii, own_dii, own_pub = _ownpct("Promoter"), _ownpct("FII"), _ownpct("DII"), _ownpct("Public")
        pledge = (q.get("ownership") or {}).get("pledge")
        fh = q.get("fund_holders") or {}
        mf_cr, mf_nfunds = fh.get("total_cr"), fh.get("n_funds")
        mc = (mcap.get(sym) or {}).get("mcap_cr")
        mf_pct_mcap = round(mf_cr / mc * 100, 2) if (mf_cr and mc) else None
        def _cagr(metric, key):
            return ((gr.get(metric) or {}).get("cagr") or {}).get(key)
        # "troubled" preset (price correction + deteriorating earnings) — now an OPTIONAL column, not a gate
        corr = (ret6 is not None and ret6 < RET6M_MAX) and (dd is not None and dd <= DD_OFF_HIGH)
        det_vals = [v for v in (eps, pat) if v is not None]
        deteriorating = any(v < 0 for v in det_vals) if det_vals else False
        troubled = bool(corr and deteriorating)
        operating = (eps is not None and eps < 0) and (ebitda is not None and ebitda < 0)
        headline_only = (eps is not None and eps < 0) and (ebitda is not None and ebitda >= 0)
        oneoff = (eps is not None and abs(eps) > ONEOFF_EPS)
        rec = (arm is not None and arm >= ARM_REC and not arm_stale)   # a stale ARM is not a live upgrade
        buy3 = flow_3m > 0
        buy1 = flow_1m > 0
        nb = (None if buyers is None or sellers is None else int(buyers) - int(sellers))
        def quad(buy):
            if rec and buy: return 1
            if rec and not buy: return 2
            if (not rec) and buy: return 3
            return 4
        rows.append({
            "symbol": sym, "name": c.get("name"), "sector": c.get("sector"), "vst_id": vid,
            "ret_6m": ret6, "dd_52w": dd,
            "eps_yoy": eps, "pat_yoy": pat, "ebitda_yoy": ebitda, "sales_yoy": sales,
            "deterioration": ("operating" if operating else ("headline-only" if headline_only else "mixed")),
            "oneoff_flag": bool(oneoff),
            "arm": arm, "arm_asof": arm_asof, "arm_stale": bool(arm_stale), "recommending": bool(rec),
            "flow_1m": round(flow_1m, 1), "flow_3m": round(flow_3m, 1),
            "flow_6m": round(flow_6m, 1), "flow_12m": round(flow_12m, 1),
            "buyers": buyers, "sellers": sellers, "net_breadth": nb,
            "buying_3m": bool(buy3), "buying_1m": bool(buy1),
            "flow_agreement": ("confirmed" if buy3 == buy1 else "inflecting"),
            "quadrant_3m": quad(buy3), "quadrant_1m": quad(buy1),
            # ownership %, MF holdings & MF-%-of-mcap, market cap, key growth (3y/5y CAGR, decimals)
            "own_promoter": own_prom, "own_fii": own_fii, "own_dii": own_dii, "own_public": own_pub, "pledge": pledge,
            "mf_cr": (round(mf_cr, 1) if mf_cr else None), "mf_nfunds": mf_nfunds,
            "mf_pct_mcap": mf_pct_mcap, "mcap_cr": (round(mc) if mc else None),
            "sales_cagr3": _cagr("Sales", "3y"), "sales_cagr5": _cagr("Sales", "5y"),
            "ebitda_cagr3": _cagr("EBITDA", "3y"), "ebitda_cagr5": _cagr("EBITDA", "5y"),
            "pat_cagr3": _cagr("PAT", "3y"), "pat_cagr5": _cagr("PAT", "5y"),
            "troubled": troubled,
            "amcs": amc_list.get(vid, []),
            "amc_cr": amc_cr.get(vid, {}),     # {AMC -> ₹cr held here} — powers the AMC magnitude filters
        })
    rows.sort(key=lambda r: -(r.get("mcap_cr") or 0))   # all-stocks default: biggest first
    from collections import Counter
    qc = Counter(r["quadrant_3m"] for r in rows)
    out = {
        "title": "Smart-money vs the Street",
        "universe": "All MF-held stocks (+ NSE 500)", "n_universe": len(universe), "n_screened": len(rows),
        "n_troubled": sum(1 for r in rows if r["troubled"]), "n_with_arm": sum(1 for r in rows if r["arm"] is not None),
        "filter": {"ret_6m_max": RET6M_MAX, "off_52w_high_pct": DD_OFF_HIGH, "fundamentals": "TTM EPS or PAT YoY < 0"},
        "axes": {"analyst": "StarMine ARM score >=50 = recommending", "fm": "corp-action-adjusted net active flow >0 = buying"},
        "default_window": "3m", "holdings_asof": hold_ym,
        "amc_aum": amc_aum,                     # AMC -> total disclosed AUM (₹cr) for the % -of-AMC-AUM filter
        "quadrant_labels": {"1": "Recommending + Buying", "2": "Recommending + Not buying",
                            "3": "Not recommending + Buying (FM ahead of street)", "4": "Neither"},
        "note": ("Universe = every stock an MF holds this month (the names with an FM/flow axis) ∪ the NSE-500; "
                 "NO pre-filter — slice it with the AMC-holding and per-column filters. Analyst = LSEG StarMine ARM "
                 "(0-100, >=50 recommending; stale >90d not counted). FM = corp-action-immune net active flow "
                 "(>0 buying); 3M default, 1M + breadth + 3M-vs-1M agreement alongside. 'Troubled' (optional filter) "
                 "= price correction (6M<0 & >=10% off high) AND TTM EPS|PAT YoY<0; deterioration tag is "
                 "corp-action-aware (operating vs headline-only). MF holdings/AUM are reconstructed from the "
                 "disclosed monthly portfolios."),
        "rows": rows,
    }
    os.makedirs(os.path.join(site_data_dir, "_screens"), exist_ok=True)
    with open(os.path.join(site_data_dir, "_screens", "smart_vs_street.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[screens] smart-money-vs-street: {len(rows)} stocks (troubled {out['n_troubled']}, with-ARM {out['n_with_arm']}; "
        f"Q1={qc[1]} Q2={qc[2]} Q3={qc[3]} Q4={qc[4]}); {len(amc_aum)} AMCs; holdings {hold_ym}")
    return out
