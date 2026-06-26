"""
funds_portfolio_viz.py — per-scheme PORTFOLIO COMPOSITION display-plane for the Fund cockpit.

WHAT (first principles)
-----------------------
The attribution engine answers "is the manager skilled?".  This answers the prior, simpler question
an analyst actually wants to SEE first: "what does this fund actually OWN, and how has that changed?"
From the 13-year monthly look-through (holdings_history.parquet) we precompute, per scheme:
  - asset_alloc   : equity / debt / cash split at the latest disclosure (% of the fund),
  - sector_now    : the equity book's SECTOR mix at the latest disclosure (% of equity) — for a donut,
  - by_sector     : every equity holding grouped BY SECTOR with subtotals — the categorized book,
  - top_holdings  : the largest positions (% of the fund),
  - rotation      : a 13-year SEMIANNUAL sector-weight matrix — for a stacked-area "how it rotated",
  - conc_ts       : concentration over time (top-10 weight + number of names).

Sector = NSE macro-industry (one of ~18 groups) via data/stock_industry.json — the Nifty-500 list.
Names outside that list (delisted / smaller mid-caps) fall to 'Unclassified', shown as its own band
with the classified-coverage % surfaced, so nothing is silently hidden. Everything is precomputed in
Python and baked into the same per-scheme JSON the Fund-Skill tab already lazy-loads — no new wiring,
no JS-parity port (analytics.py untouched).
"""
from __future__ import annotations
import os, json
import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HIST = os.path.join(_ROOT, "data", "funds", "history")
HOLDINGS = os.path.join(_HIST, "holdings_history.parquet")
SECMAP = os.path.join(_ROOT, "data", "stock_industry.json")

_N_TOP_HOLD = 15        # top positions to show
_N_BANDS = 9            # sector bands in the rotation chart (rest -> 'Other')
_MAX_SAMPLES = 28       # semiannual samples in the rotation/concentration time series


def _load_secmap() -> dict:
    try:
        d = json.load(open(SECMAP, encoding="utf-8"))
        return d.get("industry", d) if isinstance(d, dict) else {}
    except Exception:
        return {}


def _funds_portfolio_dir():
    """The directory of per-scheme monthly-portfolio JSONs (each holding carries the AMC-disclosed
    industry). Prefer the freshly-built site, fall back to the published copy. None if absent."""
    for base in (os.path.join(_ROOT, "output", "terminal_site", "data", "funds_portfolio"),
                 os.path.join(_ROOT, "_pages", "terminal", "data", "funds_portfolio")):
        if os.path.isdir(base):
            return base
    return None


def _disclosed_industry_map() -> dict:
    """{SYMBOL: AMC-disclosed industry} unioned across every scheme's portfolio JSON. The SEBI
    disclosure tags EVERY holding (including the small/micro-caps the Nifty-Total-Market list misses),
    so this is the broad-coverage source — but in the granular SEBI taxonomy, not the NSE macro one."""
    import glob
    d = _funds_portfolio_dir()
    if not d:
        return {}
    out = {}
    for fp in glob.glob(os.path.join(d, "*.json")):
        if os.path.basename(fp).startswith("_"):
            continue
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for hd in (data.get("holdings") or []):
            s = str(hd.get("symbol") or "").strip().upper()
            ind = str(hd.get("industry") or "").strip()
            if s and ind and s not in out:
                out[s] = ind
    return out


def _extended_secmap(log=None) -> dict:
    """The fund-holdings sector map, broadened well beyond the Nifty-Total-Market base so a small-cap
    book isn't dominated by 'Unclassified'. Two tiers, BOTH resolved to the NSE macro-sector taxonomy:
      1. data/stock_industry.json — the authoritative NSE macro sector for ~750 Total-Market names.
      2. the AMC-DISCLOSED industry (every scheme's monthly portfolio) crosswalked to that taxonomy —
         and the crosswalk (e.g. 'Banks'→'Financial Services', 'Pharmaceuticals & Biotechnology'→
         'Healthcare') is LEARNED by majority vote from the symbols present in BOTH tiers, so it needs
         no hand-maintained table and tracks the data. Tier 2 covers the small/micro-caps.
    Returns {SYMBOL: macro_sector}. Degrades to tier-1 only if the portfolios aren't available."""
    import collections
    macro = _load_secmap()
    disc = _disclosed_industry_map()
    if not disc:
        return macro
    votes = collections.defaultdict(collections.Counter)
    for s, m in macro.items():
        if s in disc:
            votes[disc[s]][m] += 1
    xwalk = {ind: c.most_common(1)[0][0] for ind, c in votes.items() if c}
    out = dict(macro)
    added = 0
    for s, ind in disc.items():
        if s not in out and ind in xwalk:
            out[s] = xwalk[ind]
            added += 1
    if log:
        log(f"[funds_portfolio_viz] sector map: {len(macro)} (Total-Market) + {added} "
            f"(disclosed→macro via {len(xwalk)} learned crosswalks) = {len(out)} symbols")
    return out


def _asset_class(it: str) -> str:
    """Coarse asset class from the disclosed investment_type string (diagnostic buckets)."""
    s = str(it).lower()
    if "foreign" in s and "equity" in s:
        return "Foreign equity"
    if "equity" in s:
        return "Equity"
    if any(k in s for k in ("repo", "cblo", "trep", "t bill", "tbill", "commercial paper",
                            "certificate of deposit", "net ca", "net current", "cash", "margin",
                            "money market")):
        return "Cash & equiv"
    if "mutual fund" in s or "reit" in s or "invit" in s:
        return "Other"
    return "Debt"   # ncd / corporate debt / govt securities / debenture / bond / sdl / pass-through


def _r(x, nd=2):
    try:
        v = float(x)
        return round(v, nd) if np.isfinite(v) else None
    except Exception:
        return None


def build_viz(holdings_path: str = HOLDINGS) -> dict:
    """Return {navindia_code: portfolio_viz_dict} for every scheme in the history."""
    h = pd.read_parquet(holdings_path, columns=[
        "navindia_code", "period_date", "ym", "investment_type",
        "company_name", "nse_symbol", "vst_id", "pct", "market_value"])
    h["pct"] = pd.to_numeric(h["pct"], errors="coerce")
    h["market_value"] = pd.to_numeric(h["market_value"], errors="coerce")
    h = h[h["pct"].notna() & (h["pct"] > 0)].copy()
    # fold re-code splits so the merged scheme's portfolio uses its full history (matches attribution)
    from . import scheme_identity as _sid
    h["navindia_code"] = h["navindia_code"].map(_sid.canonical_code)

    sec = _extended_secmap(log=lambda m: print(m, flush=True))
    h["asset_class"] = h["investment_type"].map(_asset_class)
    is_eq = h["asset_class"].values == "Equity"
    sym = h["nse_symbol"].astype("string")
    mapped = sym.map(sec)
    h["sector"] = np.where(is_eq, mapped.fillna("Unclassified").values, None)

    out = {}
    for code, d in h.groupby("navindia_code", sort=False):
        months = sorted(d["ym"].dropna().unique())
        if not months:
            continue
        latest = months[-1]
        dl = d[d["ym"] == latest]
        fund_total = float(dl["pct"].sum())          # ~ % of fund disclosed (≈100)

        # --- asset allocation (% of fund) ---
        aa = (dl.groupby("asset_class")["pct"].sum()).to_dict()
        asset_alloc = {k: _r(v, 1) for k, v in sorted(aa.items(), key=lambda kv: -kv[1])}

        eq = dl[dl["asset_class"] == "Equity"].copy()
        eq_pct_fund = float(eq["pct"].sum())
        n_now = int(eq["nse_symbol"].nunique()) if len(eq) else 0

        rec = {"asof": str(d["period_date"].max())[:10], "n_equity": n_now,
               "equity_pct_fund": _r(eq_pct_fund, 1), "asset_alloc": asset_alloc}

        if eq_pct_fund > 0 and len(eq):
            # classified coverage of the equity book (by weight)
            classified_w = float(eq.loc[eq["sector"] != "Unclassified", "pct"].sum())
            rec["sector_cover"] = _r(100 * classified_w / eq_pct_fund, 1)

            # --- sector mix, % OF EQUITY (normalised so the donut sums to 100) ---
            smix = eq.groupby("sector")["pct"].sum().sort_values(ascending=False)
            rec["sector_now"] = [{"sector": s, "pct": _r(100 * v / eq_pct_fund, 1)}
                                 for s, v in smix.items()]

            # --- top holdings (% of fund) ---
            topn = eq.sort_values("pct", ascending=False).head(_N_TOP_HOLD)
            rec["top_holdings"] = [{"name": str(r.company_name), "symbol": (None if pd.isna(r.nse_symbol) else str(r.nse_symbol)),
                                    "pct": _r(r.pct, 2), "sector": r.sector,
                                    "vst_id": (None if pd.isna(r.vst_id) else str(r.vst_id))}
                                   for r in topn.itertuples()]

            # --- categorized book: every equity holding grouped by sector, with subtotals (% of fund) ---
            by_sector = []
            for s, sd in eq.groupby("sector"):
                sd = sd.sort_values("pct", ascending=False)
                by_sector.append({"sector": s, "pct": _r(float(sd["pct"].sum()), 2),
                                  "names": [{"name": str(rr.company_name),
                                             "symbol": (None if pd.isna(rr.nse_symbol) else str(rr.nse_symbol)),
                                             "pct": _r(rr.pct, 2)} for rr in sd.itertuples()]})
            by_sector.sort(key=lambda x: -(x["pct"] or 0))
            rec["by_sector"] = by_sector

            # --- 13-year SECTOR ROTATION (semiannual; % of equity) ---
            samp = [m for m in months if m[5:7] in ("03", "09")]
            if months[-1] not in samp:
                samp.append(months[-1])
            if len(samp) > _MAX_SAMPLES:                       # thin evenly, keep first+last
                idx = np.linspace(0, len(samp) - 1, _MAX_SAMPLES).round().astype(int)
                samp = [samp[i] for i in sorted(set(idx))]
            de = d[(d["asset_class"] == "Equity") & (d["ym"].isin(samp))]
            if len(de):
                g = de.groupby(["ym", "sector"])["pct"].sum().reset_index(name="raw")
                g["msum"] = g.groupby("ym")["raw"].transform("sum")       # equity total that month
                g["w"] = 100 * g["raw"] / g["msum"]                       # normalise each month to 100
                piv = g.pivot(index="ym", columns="sector", values="w").reindex(samp).fillna(0.0)
                avg = piv.mean().sort_values(ascending=False)
                bands = [b for b in avg.index if b != "Unclassified"][: _N_BANDS]
                order = bands + (["Unclassified"] if "Unclassified" in piv.columns else [])
                other = [c for c in piv.columns if c not in order]
                M = piv[order].copy()
                if other:
                    M["Other"] = piv[other].sum(axis=1)
                    order = order + ["Other"]
                rec["rotation"] = {"dates": list(M.index),
                                   "sectors": order,
                                   "matrix": [[_r(v, 1) for v in M[c].tolist()] for c in order]}

                # --- concentration over time (% of equity within each sampled month) ---
                conc = []
                for m in samp:
                    em = d[(d["asset_class"] == "Equity") & (d["ym"] == m)]
                    tw = float(em["pct"].sum())
                    if tw <= 0:
                        continue
                    top10 = float(em["pct"].sort_values(ascending=False).head(10).sum())
                    conc.append({"ym": m, "top10": _r(100 * top10 / tw, 1), "n": int(em["nse_symbol"].nunique())})
                rec["conc_ts"] = conc

        # --- per-MONTH FULL book for the cockpit's month dropdown (compact, name-deduped) ---
        #   names = [[company, symbol, sector_or_assetclass], ...]   (deduped over the scheme's history)
        #   by_month = {ym: [[name_idx, pct, mktval_cr], ...]}       (every holding that month, weight-sorted)
        # Sector label = the equity industry; for non-equity rows it falls back to the asset class
        # (Debt / Cash & equiv / Other) so the table groups the WHOLE book, not just the equity sleeve.
        d = d.assign(_k=d["company_name"].astype(str) + "|" + d["nse_symbol"].astype(str))
        uq = d.drop_duplicates("_k")
        names_tbl, nidx = [], {}
        for rr in uq.itertuples():
            nidx[str(rr.company_name) + "|" + str(rr.nse_symbol)] = len(names_tbl)
            names_tbl.append([str(rr.company_name),
                              (None if pd.isna(rr.nse_symbol) else str(rr.nse_symbol)),
                              (rr.sector if rr.sector else rr.asset_class)])
        bym = {}
        for m, md in d.groupby("ym"):
            bym[m] = [[nidx[str(rr.company_name) + "|" + str(rr.nse_symbol)], _r(rr.pct, 2),
                       (_r(rr.market_value / 100.0, 1) if pd.notna(rr.market_value) else None)]
                      for rr in md.sort_values("pct", ascending=False).itertuples()]
        rec["months"] = list(months)
        rec["names"] = names_tbl
        rec["by_month"] = bym

        out[str(code)] = rec
    return out


if __name__ == "__main__":
    viz = build_viz()
    print(f"built portfolio-viz for {len(viz)} schemes")
    # eyeball a few
    for code in list(viz)[:1]:
        import pprint
        pprint.pprint({k: (v if k not in ("by_sector", "rotation") else f"<{len(v) if isinstance(v, list) else 'matrix'}>")
                       for k, v in viz[code].items()})
