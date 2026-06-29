"""MoneyBall Layer D#1 — cross-AMC net flows / crowding per stock.

THE METRIC (corp-action- AND price-drift-immune, by construction):

    net active flow_i(t) = SUM over funds [ MV_end - MV_start * (1 + r_i) ]

where MV_start/MV_end are a fund's rupee holding of stock i at month t-1 / t, and
r_i is stock i's TOTAL return over month t (from tr_returns_monthly). The term
MV_start*(1+r_i) is what the holding would be worth if the manager did NOTHING -
"passive drift". Subtracting it leaves only the rupees the managers actually moved.

Why this beats differencing share counts:
  - Total return already absorbs splits / bonuses / mergers, so a corporate action
    contributes ZERO spurious flow (the LICI split that faked a "+71% buy" vanishes).
  - The price-driven value change is removed, so a stock rising doesn't look like buying.
  - An index/ETF fund trades ~0 net of drift, so passive beta self-cancels.

Units: rupees CRORE (market_value's verified unit). Coverage control: only funds present
in BOTH months are counted (so coverage drift doesn't masquerade as flow). Stocks with no
tr return that month are excluded (can't separate flow from drift) and the excluded value
is reported - never silently dropped.
"""
import os, glob, json, re
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
STORE = os.path.join(_ROOT, "data", "funds", "history", "holdings_history.parquet")
TR    = os.path.join(_ROOT, "data", "funds", "history", "tr_returns_monthly.parquet")
CA_DIR = os.path.join(_ROOT, "data", "_corpactions")

_TOL = 0.5  # Rs crore; per-fund |flow| below this is "no material trade" (rounding/noise floor)

# passive detection (for the breadth count; the metric itself self-cancels passive)
_PASSIVE_CAT = "index|etf|exchange traded"
_PASSIVE_NM  = r"index|etf|nifty|sensex|bse |s&p|bharat bond"

# STRUCTURAL (cross-identity) corporate actions that move value between vst_ids -> these
# confound the flow metric (the total-return drift only neutralises WITHIN-identity events
# like splits/bonuses). Dividends/AGM/buyback/bonus/split are NOT here (handled or harmless).
_STRUCTURAL_CA = re.compile(r"amalgam|merger|demerg|scheme of arrangement|slump", re.I)

# merger/successor DETECTOR thresholds (the backstop that supplies the A->B link)
_DET_MIN_START_FUNDS = 8     # predecessor must have been held by >= this many active funds
_DET_MAX_END_FUNDS   = 2     # ...and be near-fully exited (<= this many) at month end
_DET_MIN_START_CR    = 100   # ...with material value (Rs crore)
_DET_MIN_OVERLAP     = 5     # successor must be entered by >= this many of the predecessor's funds
_DET_MIN_OVERLAP_FRAC = 0.4  # ...and >= this fraction of them


def _load():
    h = pd.read_parquet(STORE, columns=["ym","navindia_code","scheme_name","amc","sebi_category",
                                        "investment_type","vst_id","nse_symbol","vid_name","market_value","pct"])
    h = h[h["investment_type"].astype(str).str.strip().str.lower() == "equity"].copy()
    h = h[h["vst_id"].notna() & (h["vst_id"].astype(str).str.strip() != "")]
    cat = h["sebi_category"].astype(str).str.lower()
    nm  = h["scheme_name"].astype(str).str.lower()
    h["is_passive"] = cat.str.contains(_PASSIVE_CAT, na=False) | nm.str.contains(_PASSIVE_NM, na=False)

    tr = pd.read_parquet(TR, columns=["date","vst_id","ret_1m"])
    tr = tr[tr["vst_id"].notna()].copy()
    tr["ym"] = tr["date"].dt.strftime("%Y-%m")
    ret = tr.dropna(subset=["ret_1m"]).set_index(["vst_id","ym"])["ret_1m"]
    ret = ret[~ret.index.duplicated(keep="last")]
    return h, ret


def _prev_ym(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y-1}-12" if m == 1 else f"{y}-{m-1:02d}"


def _next_ym(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y+1}-01" if m == 12 else f"{y}-{m+1:02d}"


def _load_ca_events(h):
    """PRIMARY source: the NSE corp-action feed. Returns {vst_id: set(ym)} for STRUCTURAL
    (cross-identity) events, mapped onto our vst_id by ISIN (preferred) then NSE symbol.
    A structural event on ex-date in month M is flagged for M and M+1 (the holding change
    lands on the month-end snapshot on/after the ex-date)."""
    # build isin/symbol -> vst_id from the store (latest label wins)
    hh = h.dropna(subset=["vst_id"])
    sym2v = (hh.dropna(subset=["nse_symbol"]).drop_duplicates("nse_symbol")
               .set_index("nse_symbol")["vst_id"].to_dict())
    out = {}
    for f in sorted(glob.glob(os.path.join(CA_DIR, "*.json"))):
        try:
            recs = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for r in recs:
            blob = f"{r.get('subject','')} {r.get('ind','')}"
            if not _STRUCTURAL_CA.search(blob):
                continue
            sym = r.get("symbol")
            vid = sym2v.get(sym)
            if not vid:
                continue
            ex = r.get("exDate") or ""
            try:
                d = pd.to_datetime(ex, dayfirst=True, errors="coerce")
                if pd.isna(d):
                    continue
                ym = d.strftime("%Y-%m")
            except Exception:
                continue
            out.setdefault(vid, set()).update({ym, _next_ym(ym)})
    return out


def _detect_merger_pairs(a, b, det_min_overlap=_DET_MIN_OVERLAP):
    """BACKSTOP: data-driven A->B successor linkage. `a`/`b` are the common-fund active
    holdings at month t-1 / t. Returns list of dicts: predecessor A near-fully exited while
    >= overlap of the SAME funds entered successor B (a new position for them)."""
    PA = a.groupby("vst_id").agg(funds=("navindia_code", lambda x: set(x)),
                                 mv=("market_value", "sum"),
                                 name=("vid_name", "first"), sym=("nse_symbol", "first"))
    PA["n"] = PA["funds"].map(len)
    end_n = b.groupby("vst_id")["navindia_code"].nunique()
    cand = PA[(PA["n"] >= _DET_MIN_START_FUNDS) & (PA["mv"] >= _DET_MIN_START_CR)]
    cand = cand[end_n.reindex(cand.index).fillna(0) <= _DET_MAX_END_FUNDS]
    if cand.empty:
        return []
    a_by_fund = a.groupby("navindia_code")["vst_id"].apply(set)
    b_by_fund = b.groupby("navindia_code")["vst_id"].apply(set)
    bend = b.groupby("vst_id").agg(mv=("market_value", "sum"))
    pairs = []
    for A, row in cand.iterrows():
        FA = row["funds"]
        newcount = {}
        for fnd in FA:
            for s in b_by_fund.get(fnd, set()) - a_by_fund.get(fnd, set()):
                if s != A:
                    newcount[s] = newcount.get(s, 0) + 1
        if not newcount:
            continue
        B = max(newcount, key=newcount.get)
        ov = newcount[B]
        if ov >= max(det_min_overlap, _DET_MIN_OVERLAP_FRAC * len(FA)):
            pairs.append({"A": A, "B": B, "overlap": ov, "n_fa": len(FA),
                          "A_name": row["name"], "A_sym": row["sym"],
                          "A_mv_start": float(row["mv"]), "B_mv_end": float(bend["mv"].get(B, 0.0))})
    return pairs


def _pair_flows(ym_to, h, ret, active_only=True):
    """Per-(fund, stock) flow for the month ending ym_to. THE shared core used by both the
    stock-level and fund-level views (one source of truth for the flow formula):
        flow = end_value - start_value*(1 + the stock's total return)  [Rs crore].
    Returns (m, a, b, common, excl_val, tot_val); m has navindia_code, vst_id, mv_s, mv_e, r, flow."""
    ym_from = _prev_ym(ym_to)
    src = h[~h["is_passive"]] if active_only else h
    a = src[src["ym"] == ym_from]; b = src[src["ym"] == ym_to]
    if not len(a) or not len(b):
        raise ValueError(f"no data for {ym_from} or {ym_to}")
    common = set(a["navindia_code"]) & set(b["navindia_code"])
    a = a[a["navindia_code"].isin(common)]; b = b[b["navindia_code"].isin(common)]
    A = a.groupby(["navindia_code", "vst_id"])["market_value"].sum().rename("mv_s")
    B = b.groupby(["navindia_code", "vst_id"])["market_value"].sum().rename("mv_e")
    m = pd.concat([A, B], axis=1).fillna(0.0).reset_index()
    m["r"] = m["vst_id"].map(lambda v: ret.get((v, ym_to)))
    excl_val = m.loc[m["r"].isna(), ["mv_s", "mv_e"]].max(axis=1).sum()
    tot_val  = m[["mv_s", "mv_e"]].max(axis=1).sum()
    m = m.dropna(subset=["r"]).copy()
    m["flow"] = m["mv_e"] - m["mv_s"] * (1.0 + m["r"])
    return m, a, b, common, float(excl_val), float(tot_val)


def _pair_flows_active(ym_to, h, ret, active_only=True):
    """★ FLOW DECOMPOSITION (spec FLOW_DECOMPOSITION.md §2). Per-(fund, stock) the THREE flow
    figures for the month ending ym_to, computed over each fund's FULL equity book (NOT the
    common-stock intersection — so fresh BUYS and full EXITS, the largest active decisions, are
    INCLUDED). Returns (m, a, b, common, excl_val, tot_val); `m` has navindia_code, vst_id, mv_s,
    mv_e, r, plus:

        gross      = mv_e - mv_s                              # (1) raw rupee change: price+inflow+active
        price_adj  = mv_e - mv_s*(1+r)                        # (2) strips PRICE only (= existing _pair_flows)
        net_active = AUM_eq(t)*(1+R_p) * dw_active            # (3) the CONVICTION flow, weight-space

    where, per fund f:
        AUM_eq(t)  = Σ_i mv_s_i             (start equity book size; the full-book denominator)
        R_p        = Σ_i w_i(t)·r_i         (do-nothing book drift; w_i(t)=mv_s_i/AUM_eq, over priced names)
        w_i(t+1)   = mv_e_i / AUM_eq(t+1)   (end weight, over the END book)
        w_drift_i  = w_i(t)·(1+r_i)/(1+R_p) (weight if the manager did NOTHING but let prices drift)
        dw_active_i= w_i(t+1) − w_drift_i   (genuine reweighting; Σ_i dw_active ≈ 0, the zero-sum audit)

    net_active is INFLOW-IMMUNE by construction: a pro-rata inflow deployment leaves every weight
    unchanged ⇒ dw_active = 0 (price_adj would show a phantom F·w_i in every name). Entries register as
    w_i(t)=0 ⇒ dw_active = w_i(t+1) (full add); exits as w_i(t+1)=0 ⇒ dw_active = −w_drift (full cut).

    Only holdings whose stock return r is known enter the active figure (so price is cleanly removed);
    the book aggregates AUM_eq/R_p use the priced subset of each fund's book. Units: Rs crore.
    """
    ym_from = _prev_ym(ym_to)
    src = h[~h["is_passive"]] if active_only else h
    a = src[src["ym"] == ym_from]; b = src[src["ym"] == ym_to]
    if not len(a) or not len(b):
        raise ValueError(f"no data for {ym_from} or {ym_to}")
    common = set(a["navindia_code"]) & set(b["navindia_code"])
    a = a[a["navindia_code"].isin(common)]; b = b[b["navindia_code"].isin(common)]
    # FULL per-fund book on BOTH ends (outer join: keeps entries mv_s=0 and exits mv_e=0).
    A = a.groupby(["navindia_code", "vst_id"])["market_value"].sum().rename("mv_s")
    B = b.groupby(["navindia_code", "vst_id"])["market_value"].sum().rename("mv_e")
    m = pd.concat([A, B], axis=1).fillna(0.0).reset_index()
    m["r"] = m["vst_id"].map(lambda v: ret.get((v, ym_to)))
    excl_val = m.loc[m["r"].isna(), ["mv_s", "mv_e"]].max(axis=1).sum()
    tot_val  = m[["mv_s", "mv_e"]].max(axis=1).sum()
    m["gross"]     = m["mv_e"] - m["mv_s"]
    m["price_adj"] = m["mv_e"] - m["mv_s"] * (1.0 + m["r"])      # NaN where r unknown (kept for gross)

    # ---- per-fund book aggregates over the PRICED subset of each fund's full book ----
    priced = m[m["r"].notna()].copy()
    aum_s = priced.groupby("navindia_code")["mv_s"].sum().rename("aum_s")     # AUM_eq(t)
    aum_e = priced.groupby("navindia_code")["mv_e"].sum().rename("aum_e")     # AUM_eq(t+1)
    # R_p = Σ w_i(t)·r_i = (Σ mv_s_i·r_i) / Σ mv_s_i  over the priced book
    priced = priced.join(aum_s, on="navindia_code")
    rp = (priced.assign(_x=priced["mv_s"] * priced["r"]).groupby("navindia_code")["_x"].sum()
          / aum_s.replace(0.0, np.nan)).rename("rp")
    agg = pd.concat([aum_s, aum_e, rp], axis=1)
    pj = priced.join(agg[["aum_e", "rp"]], on="navindia_code")
    pj["w_s"]     = pj["mv_s"] / pj["aum_s"].replace(0.0, np.nan)
    pj["w_e"]     = pj["mv_e"] / pj["aum_e"].replace(0.0, np.nan)
    pj["w_drift"] = pj["w_s"] * (1.0 + pj["r"]) / (1.0 + pj["rp"])
    pj["dw_active"] = pj["w_e"].fillna(0.0) - pj["w_drift"].fillna(0.0)
    # value the reweighting at the post-drift book size  AUM_eq(t)·(1+R_p)
    pj["net_active"] = pj["aum_s"] * (1.0 + pj["rp"]) * pj["dw_active"]
    m = m.merge(pj[["navindia_code", "vst_id", "dw_active", "net_active"]],
                on=["navindia_code", "vst_id"], how="left")
    return m, a, b, common, float(excl_val), float(tot_val)


def stock_active_flows(ym_to, h=None, ret=None, active_only=True, apply_bridge=True, ca=None):
    """★ Cross-AMC stock-level flow with ALL THREE figures (FLOW_DECOMPOSITION §3). Like
    `stock_flows` but returns, per vst_id, the market-wide sum of each fund's gross / price-adjusted
    / net-active rupee trade — `net_active` is the conviction signal (inflow-immune). Reuses the same
    merger-bridge + corporate-action quarantine as `stock_flows` (net-active inherits CA immunity).

    Returns a DataFrame indexed by vst_id with: name, sym, gross_cr, price_adj_cr, net_active_cr,
    mv_start_cr, mv_end_cr, breadth_start, breadth_end, dbreadth, buyers, sellers (by net_active),
    ret_1m, merged_from, ca_flag, plus a `coverage` dict in .attrs."""
    if h is None or ret is None:
        h, ret = _load()
    ym_from = _prev_ym(ym_to)
    m, a, b, common, excl_val, tot_val = _pair_flows_active(ym_to, h, ret, active_only)

    g = m.groupby("vst_id").agg(
        gross_cr=("gross", "sum"),
        price_adj_cr=("price_adj", "sum"),
        net_active_cr=("net_active", "sum"),
        mv_start_cr=("mv_s", "sum"),
        mv_end_cr=("mv_e", "sum"),
        breadth_start=("mv_s", lambda x: (x > 0).sum()),
        breadth_end=("mv_e", lambda x: (x > 0).sum()),
        buyers=("net_active", lambda x: (x > _TOL).sum()),
        sellers=("net_active", lambda x: (x < -_TOL).sum()),
        g_buyers=("gross", lambda x: (x > _TOL).sum()),       # gross-basis headcount (price-inflated) for the #106 breadth toggle
        g_sellers=("gross", lambda x: (x < -_TOL).sum()),
    )
    g["dbreadth"] = (g["breadth_end"] - g["breadth_start"]).astype(int)
    g["ret_1m"] = g.index.map(lambda v: ret.get((v, ym_to)))
    lab = b.groupby("vst_id").agg(name=("vid_name", "first"), sym=("nse_symbol", "first"))
    g = g.join(lab, how="left")
    g["merged_from"] = None
    g["ca_flag"] = False

    n_pairs = 0
    n_ca = 0
    if apply_bridge:
        # Reuse the SAME merger-pair detector + structural-CA quarantine as stock_flows so the three
        # figures inherit corp-action immunity. The merger SWAP nets out by combining A->B; the
        # price-drift basis r_B reprices the combined start for both price_adj and net_active.
        pairs = _detect_merger_pairs(a, b)
        for p in pairs:
            A, B = p["A"], p["B"]
            if B not in g.index:
                continue
            r_b = g.at[B, "ret_1m"]
            r_b = 0.0 if pd.isna(r_b) else r_b
            comb_start = p["A_mv_start"] + g.at[B, "mv_start_cr"]
            comb_end   = p["B_mv_end"]
            g.at[B, "gross_cr"]      = comb_end - comb_start
            g.at[B, "price_adj_cr"]  = comb_end - comb_start * (1.0 + r_b)
            # net_active for the merged entity falls back to the price-adjusted combine (we lack the
            # per-fund post-swap weights for A); keeps the swap from faking a giant active trade.
            g.at[B, "net_active_cr"] = comb_end - comb_start * (1.0 + r_b)
            g.at[B, "mv_start_cr"]   = comb_start
            g.at[B, "merged_from"]   = p["A_name"]
            if A in g.index:
                g = g.drop(index=A)
            n_pairs += 1
        if ca is None:
            ca = _load_ca_events(h)
        flagged = {v for v in g.index if ym_to in ca.get(v, ())}
        flagged -= {p["B"] for p in pairs}
        g.loc[g.index.isin(flagged), "ca_flag"] = True
        n_ca = int(g["ca_flag"].sum())

    g.attrs["coverage"] = {"ym_to": ym_to, "ym_from": ym_from, "common_funds": len(common),
                           "excluded_value_cr": round(float(excl_val), 1),
                           "excluded_pct": round(100.0 * excl_val / max(tot_val, 1e-9), 2),
                           "merger_pairs": n_pairs, "ca_flagged": n_ca}
    return g.sort_values("net_active_cr", ascending=False)


def stock_flows(ym_to, h=None, ret=None, active_only=True, apply_bridge=True, ca=None):
    """Net active flow per stock for the month ENDING ym_to (vs the prior month).

    Returns a DataFrame indexed by vst_id with: name, sym, net_flow_cr, mv_start_cr,
    mv_end_cr, breadth_start, breadth_end, dbreadth, buyers, sellers, ret_1m, plus a
    `coverage` dict attached as .attrs.
    """
    if h is None or ret is None:
        h, ret = _load()
    ym_from = _prev_ym(ym_to)
    m, a, b, common, excl_val, tot_val = _pair_flows(ym_to, h, ret, active_only)

    g = m.groupby("vst_id").agg(
        net_flow_cr=("flow","sum"),
        mv_start_cr=("mv_s","sum"),
        mv_end_cr=("mv_e","sum"),
        breadth_start=("mv_s", lambda x:(x>0).sum()),
        breadth_end=("mv_e", lambda x:(x>0).sum()),
        buyers=("flow", lambda x:(x>_TOL).sum()),
        sellers=("flow", lambda x:(x<-_TOL).sum()),
    )
    g["dbreadth"] = (g["breadth_end"] - g["breadth_start"]).astype(int)
    g["ret_1m"] = g.index.map(lambda v: ret.get((v, ym_to)))
    # labels (latest known)
    lab = b.groupby("vst_id").agg(name=("vid_name","first"), sym=("nse_symbol","first"))
    g = g.join(lab, how="left")
    g["merged_from"] = None
    g["ca_flag"] = False

    n_pairs = 0
    n_ca = 0
    if apply_bridge:
        # (1) BACKSTOP detector: combine each merger pair A->B into the successor B so the
        #     share-SWAP nets out, leaving only real discretionary buying/selling of the
        #     combined entity. Uses r_B as the post-merger drift proxy (needs no r_A).
        pairs = _detect_merger_pairs(a, b)
        for p in pairs:
            A, B = p["A"], p["B"]
            if B not in g.index:
                continue
            r_b = g.at[B, "ret_1m"]
            r_b = 0.0 if pd.isna(r_b) else r_b
            comb_start = p["A_mv_start"] + g.at[B, "mv_start_cr"]
            comb_end   = p["B_mv_end"]  # A is ~fully exited, so combined end ~= B's end
            g.at[B, "net_flow_cr"] = comb_end - comb_start * (1.0 + r_b)
            g.at[B, "mv_start_cr"] = comb_start
            g.at[B, "merged_from"] = p["A_name"]
            if A in g.index:
                g = g.drop(index=A)
            n_pairs += 1
        # (2) PRIMARY feed: flag any stock with a legally-reported STRUCTURAL corporate action
        #     this month (e.g. a demerger like VEDL whose successors are new/unresolved) -
        #     its flow is not a clean discretionary signal, so quarantine it from the rankings.
        if ca is None:
            ca = _load_ca_events(h)
        flagged = {v for v in g.index if ym_to in ca.get(v, ())}
        # a merger-resolved successor stays clean; don't double-flag it
        flagged -= {p["B"] for p in pairs}
        g.loc[g.index.isin(flagged), "ca_flag"] = True
        n_ca = int(g["ca_flag"].sum())

    g.attrs["coverage"] = {"ym_to": ym_to, "ym_from": ym_from, "common_funds": len(common),
                           "excluded_value_cr": round(float(excl_val), 1),
                           "excluded_pct": round(100.0*excl_val/max(tot_val,1e-9), 2),
                           "merger_pairs": n_pairs, "ca_flagged": n_ca}
    return g.sort_values("net_flow_cr", ascending=False)


def clean_flows(g):
    """Discretionary flows only: drop stocks quarantined for a structural corporate action."""
    return g[~g["ca_flag"]]


def build_stock_series(months_back=36, end_ym=None):
    """Per-stock net-active-flow SERIES for the deck (display-plane, baked as values).

    Returns {nse_symbol: {months:[ym...], flow:[Rs cr...], intensity:[% of avg position...],
    breadth:[#active funds...], buyers:[...], sellers:[...], ca:[bool...], rank:[# this month
    or None...], nclean:[# ranked stocks...], name, vst_id}}. Rank is by INTENSITY (net flow as
    a % of the average MF position), SIZE-NEUTRAL -- not raw rupees (audit 2026-06-26: a raw-rupee
    rank tilts mechanically to mega-caps). Over the CLEAN (CA-quarantined-out) cross-section;
    1 = strongest net accumulation.

    ★ FLOW DECOMPOSITION (FLOW_DECOMPOSITION.md): three switchable flow figures are ALSO emitted —
    `gross` (raw ₹ change), `price_adj` (= the legacy `flow`, strips price only), and `net_active`
    (the CONVICTION flow, weight-space, also strips scheme inflows). Each carries a history array
    aligned to `months` plus a `decomp` block with the current-month snapshot scalars. The legacy
    `flow`/`intensity`/`rank` keys are UNCHANGED so existing panels are bit-for-bit identical."""
    h, ret = _load()
    ca = _load_ca_events(h)
    months = sorted(h["ym"].unique())
    if end_ym:
        months = [m for m in months if m <= end_ym]
    use = months[-months_back:] if months_back else months

    series, meta = {}, {}
    for ym in use:
        try:
            g = stock_flows(ym, h=h, ret=ret, ca=ca)
        except ValueError:
            continue
        # the three-figure decomposition for the SAME month (full-book, so entries/exits included)
        try:
            ga = stock_active_flows(ym, h=h, ret=ret, ca=ca)
        except ValueError:
            ga = None
        clean = g[~g["ca_flag"]].copy()
        # SIZE-NEUTRAL conviction (audit 2026-06-26): rank by net flow as a fraction of the average
        # MF position that month, NOT by raw rupees (raw ₹ tilts mechanically to mega-caps). The
        # start/end average avoids div-by-zero on entries/exits; require a material base (>= Rs 1 cr)
        # so a tiny position can't manufacture a huge intensity.
        base = 0.5 * (clean["mv_start_cr"].abs() + clean["mv_end_cr"].abs())
        clean["intensity"] = np.where(base >= 1.0, clean["net_flow_cr"] / base, np.nan)
        rank = clean["intensity"].rank(ascending=False, method="min")   # 1 = strongest net accumulation
        nclean = int(clean["intensity"].notna().sum())
        intens = clean["intensity"]
        # NET-ACTIVE size-neutral intensity + rank over the clean cross-section (for the conviction view)
        rank_na, intens_na, nclean_na = None, None, 0
        if ga is not None:
            cln = ga[~ga["ca_flag"]].copy()
            base_na = 0.5 * (cln["mv_start_cr"].abs() + cln["mv_end_cr"].abs())
            cln["int_na"] = np.where(base_na >= 1.0, cln["net_active_cr"] / base_na, np.nan)
            rank_na = cln["int_na"].rank(ascending=False, method="min")
            intens_na = cln["int_na"]
            nclean_na = int(cln["int_na"].notna().sum())
        for vid, row in g.iterrows():
            d = series.setdefault(vid, {"months": [], "flow": [], "intensity": [], "breadth": [],
                                        "buyers": [], "sellers": [], "ca": [], "rank": [], "nclean": [],
                                        "gross": [], "price_adj": [], "net_active": [],
                                        "na_intensity": [], "na_rank": [], "na_nclean": [],
                                        # per-basis buyer/seller headcounts so Breadth can follow the #106 flow-basis
                                        # toggle. buyers/sellers above = price-adjusted (legacy default, unchanged);
                                        # na_* = net-active (conviction); g_* = gross (raw, price-inflated).
                                        "na_buyers": [], "na_sellers": [], "g_buyers": [], "g_sellers": []})
            d["months"].append(ym)
            d["flow"].append(round(float(row["net_flow_cr"]), 1))
            iv = intens.get(vid)
            d["intensity"].append(None if (iv is None or pd.isna(iv)) else round(float(iv) * 100, 1))
            d["breadth"].append(int(row["breadth_end"]))
            d["buyers"].append(int(row["buyers"]))
            d["sellers"].append(int(row["sellers"]))
            d["ca"].append(bool(row["ca_flag"]))
            rv = rank.get(vid)
            d["rank"].append(int(rv) if (rv is not None and not pd.isna(rv)) else None)
            d["nclean"].append(nclean)
            # --- three-figure decomposition (aligned to the same months axis) ---
            ar = ga.loc[vid] if (ga is not None and vid in ga.index) else None
            d["gross"].append(None if ar is None else round(float(ar["gross_cr"]), 1))
            # price_adj over the FULL book; equals legacy `flow` up to entries/exits the legacy
            # intersection dropped — keep it as its own series so the toggle is self-consistent.
            d["price_adj"].append(None if ar is None else round(float(ar["price_adj_cr"]), 1))
            d["net_active"].append(None if (ar is None or pd.isna(ar["net_active_cr"]))
                                   else round(float(ar["net_active_cr"]), 1))
            ivn = None if intens_na is None else intens_na.get(vid)
            d["na_intensity"].append(None if (ivn is None or pd.isna(ivn)) else round(float(ivn) * 100, 1))
            rvn = None if rank_na is None else rank_na.get(vid)
            d["na_rank"].append(int(rvn) if (rvn is not None and not pd.isna(rvn)) else None)
            d["na_nclean"].append(nclean_na)
            # per-basis headcounts (net-active + gross) from the full-book active frame `ar`
            d["na_buyers"].append(None if (ar is None or pd.isna(ar["buyers"])) else int(ar["buyers"]))
            d["na_sellers"].append(None if (ar is None or pd.isna(ar["sellers"])) else int(ar["sellers"]))
            d["g_buyers"].append(None if (ar is None or pd.isna(ar["g_buyers"])) else int(ar["g_buyers"]))
            d["g_sellers"].append(None if (ar is None or pd.isna(ar["g_sellers"])) else int(ar["g_sellers"]))
            meta.setdefault(vid, {"name": row.get("name"), "sym": row.get("sym")})

    out = {}
    for vid, d in series.items():
        sym = meta[vid]["sym"]
        if not sym:
            continue
        # current-month snapshot scalars for the three figures (last non-empty month)
        decomp = {
            "ym": (d["months"][-1] if d["months"] else None),
            "gross_cr": (d["gross"][-1] if d["gross"] else None),
            "price_adj_cr": (d["price_adj"][-1] if d["price_adj"] else None),
            "net_active_cr": (d["net_active"][-1] if d["net_active"] else None),
            "na_intensity": (d["na_intensity"][-1] if d["na_intensity"] else None),
            "na_rank": (d["na_rank"][-1] if d["na_rank"] else None),
            "na_nclean": (d["na_nclean"][-1] if d["na_nclean"] else None),
        }
        out[str(sym)] = {**d, "name": meta[vid]["name"], "vst_id": vid, "decomp": decomp}
    return out


def build_fund_series(months_back=18, end_ym=None, min_trade_cr=_TOL):
    """Per-FUND crowd-alignment ("herding") + latest active trades, for the Funds cockpit.

    Herding (per fund-month) = trade-size-weighted sign-agreement of the fund's per-stock trades
    (net of drift) with the CONTEMPORANEOUS crowd's trades in the SAME stocks, EXCLUDING the fund
    itself (no self-correlation). +1 = always trades WITH the contemporaneous crowd; -1 = always
    AGAINST it. AUDIT 2026-06-26 (independently re-verified): herding is a PERSISTENT STYLE TRAIT
    (per-fund split-half rank-corr +0.32, YoY +0.31 across 769 funds) but it is NOT a forward signal
    -- forward category-excess IC ~0 at 3/6/12m (t<1) and the contrarian-minus-consensus return spread
    ~0. The earlier "lower herding ~ higher excess return, Spearman -0.10, ~2-3%/yr, consistent with
    Verardo" line was a CONTEMPORANEOUS whole-history fund-AVERAGE artifact (reproduces as +0.12,
    OPPOSITE sign) that was never forward-tested in code -- REMOVED. No leadership claim either:
    contrarians do NOT lead the crowd (V3 lead score 0.062 < followers' 0.120). Positioning/diagnostic.

    Returns {navindia_code: {herding_avg, contrarian_pctile (CATEGORY-relative), pctile_basis, style
    ('against'|'balanced'|'with', category terciles), turnover_annual, turnover_pctile, months[],
    herding[], latest:{buys,sells,n_buys,n_sells}, name, amc, category, ym}}.
    """
    h, ret = _load()
    months = sorted(h["ym"].unique())
    if end_ym:
        months = [x for x in months if x <= end_ym]
    use = months[-(months_back + 1):] if months_back else months
    last_ym = use[-1]
    label = (h.dropna(subset=["vst_id"]).drop_duplicates("vst_id")
               .set_index("vst_id")[["vid_name", "nse_symbol"]])

    by_fund, fmeta, latest, turn_acc = {}, {}, {}, {}
    for i in range(1, len(use)):
        ym_to = use[i]
        try:
            m, a, b, common, _, _ = _pair_flows(ym_to, h, ret, active_only=True)
        except ValueError:
            continue
        # TURNOVER: one-way fraction of the equity book actively traded (gross |flow| net of drift,
        # halved) over end AUM. A process descriptor (how active is the manager?), not a skill claim.
        aum = b.groupby("navindia_code")["market_value"].sum()
        gross = m.groupby("navindia_code")["flow"].apply(lambda x: x.abs().sum())
        for fund, gv in gross.items():
            A = float(aum.get(fund, 0.0))
            if A > 0:
                turn_acc.setdefault(fund, []).append(0.5 * float(gv) / A)
        m = m.assign(crowd=m.groupby("vst_id")["flow"].transform("sum") - m["flow"])
        mm = m[(m["flow"].abs() > min_trade_cr) & (m["crowd"].abs() > min_trade_cr)].copy()
        mm["agree"] = np.sign(mm["flow"]) * np.sign(mm["crowd"])
        mm["w"] = mm["flow"].abs()
        for fund, d in mm.groupby("navindia_code"):
            rec = by_fund.setdefault(fund, {"months": [], "herding": [], "traded": []})
            rec["months"].append(ym_to)
            rec["herding"].append(round(float(np.average(d["agree"], weights=d["w"])), 3))
            rec["traded"].append(round(float(d["w"].sum()), 1))
        for fund, d in b.groupby("navindia_code"):
            fmeta.setdefault(fund, {"name": str(d["scheme_name"].iloc[0]), "amc": str(d["amc"].iloc[0]),
                                    "category": str(d["sebi_category"].iloc[0])})
        if ym_to == last_ym:
            mc = m.copy()
            mc["name"] = mc["vst_id"].map(label["vid_name"]); mc["sym"] = mc["vst_id"].map(label["nse_symbol"])
            mc["with_crowd"] = (np.sign(mc["flow"]) == np.sign(mc.groupby("vst_id")["flow"].transform("sum")))
            for fund, d in mc.groupby("navindia_code"):
                dd = d[d["flow"].abs() > min_trade_cr]
                pack = lambda x: [{"sym": (None if pd.isna(r.sym) else str(r.sym)),
                                   "name": (None if pd.isna(r.name) else str(r.name)),
                                   "cr": round(float(r.flow), 1), "crowd": bool(r.with_crowd)}
                                  for r in x.itertuples()]
                latest[fund] = {"buys": pack(dd[dd["flow"] > 0].sort_values("flow", ascending=False).head(8)),
                                "sells": pack(dd[dd["flow"] < 0].sort_values("flow").head(8)),
                                "n_buys": int((dd["flow"] > 0).sum()), "n_sells": int((dd["flow"] < 0).sum())}

    out = {}
    for fund, rec in by_fund.items():
        ha = round(float(np.mean(rec["herding"])), 3) if rec["herding"] else None
        tv = turn_acc.get(fund, [])
        turn = round(float(np.mean(tv)) * 12 * 100, 1) if tv else None   # % per year, one-way
        fm = fmeta.get(fund, {})
        out[str(fund)] = {"herding_avg": ha, "months": rec["months"], "herding": rec["herding"],
                          "turnover_annual": turn, "latest": latest.get(fund), "ym": last_ym,
                          "name": fm.get("name"), "amc": fm.get("amc"), "category": fm.get("category")}

    # CONTRARIAN PERCENTILE + STYLE BAND -- CATEGORY-RELATIVE, data-derived terciles (audit 2026-06-26).
    # Old code ranked over ALL funds and banded with hand-picked -0.15/+0.25 cut-offs (which caught only
    # ~0.4% as "contrarian" because the distribution centres at +0.21, not 0). We rank each fund's
    # herding_avg WITHIN its SEBI category (the fair, like-for-like peer set; a small-cap and a large-cap
    # book face different crowds) when the category has >= MIN_PEERS funds, else fall back to the pooled
    # set; the 3 style bands are that reference set's own terciles. This is POSITIONING, not a quality
    # tier (herding does not predict forward return) -- the render must not colour it good/bad.
    MIN_PEERS = 6
    POOL_LO, POOL_HI = 0.168, 0.252        # pooled full-history terciles (fallback for thin categories)
    by_cat = {}
    for v in out.values():
        if v["herding_avg"] is not None:
            by_cat.setdefault(v.get("category") or "", []).append(v["herding_avg"])
    pooled = sorted(v["herding_avg"] for v in out.values() if v["herding_avg"] is not None)
    tvals = sorted(v["turnover_annual"] for v in out.values() if v["turnover_annual"] is not None)

    def _pctile_geq(arr, x):               # 100 = MOST contrarian (lowest herding) among the peer set
        return round(100.0 * sum(1 for y in arr if y >= x) / len(arr), 0) if arr else None

    def _terciles(arr):
        s = sorted(arr); n = len(s)
        return (s[max(0, n // 3 - 1)], s[max(0, (2 * n) // 3 - 1)]) if n >= 3 else (POOL_LO, POOL_HI)

    for v in out.values():
        ha = v["herding_avg"]
        if ha is not None:
            peers = by_cat.get(v.get("category") or "", [])
            catrel = len(peers) >= MIN_PEERS
            ref = peers if catrel else pooled
            v["contrarian_pctile"] = _pctile_geq(ref, ha)
            v["pctile_basis"] = "category" if catrel else "all-funds"
            lo, hi = _terciles(ref)
            v["style"] = "against" if ha < lo else ("with" if ha > hi else "balanced")
        tu = v["turnover_annual"]
        if tu is not None and tvals:
            v["turnover_pctile"] = round(100.0 * sum(1 for x in tvals if x <= tu) / len(tvals), 0)
    return out


def _as_cat_type(cat):
    """Classify a SEBI category for Active-Share interpretation (adversarial-verify 2026-06-25):
    'thematic' (sector funds hold mechanically-disjoint stocks -> AS inflated by mandate not skill),
    'hybrid' (store is equity-only, so a debt/cash bet -- often the fund's main lever -- is invisible),
    or 'diversified' (the clean set where peer-relative AS is meaningful)."""
    cl = str(cat).lower()
    if "sector" in cl or "thematic" in cl:
        return "thematic"
    if any(k in cl for k in ("hybrid", "balanced advantage", "dynamic asset", "multi asset",
                             "equity savings", "retirement", "children")):
        return "hybrid"
    return "diversified"


# categories where high active share EMPIRICALLY predicted higher subsequent category-relative excess
# (confound-hunter, 2026-06-25): Large Cap +0.52, ELSS +0.45, Focused +0.44, Flexi +0.35 (all p<.05).
_AS_PREDICTIVE_OK = {"Large Cap Fund", "ELSS", "Focused Fund", "Flexi Cap Fund"}
# diversified categories where the predictive link VANISHED (rho ~0): do NOT market AS as a selector.
_AS_PREDICTIVE_NULL = {"Mid Cap Fund", "Small Cap Fund", "Multi Cap Fund", "Value Fund", "Large & Mid Cap Fund"}


def build_active_share(end_ym=None, min_peers=6, aum_conc_thresh=0.40):
    """PEER-RELATIVE Active Share (Cremers-Petajisto proxy): how differently a fund is positioned
    vs its SEBI-category peers' collective book. We lack official index constituent WEIGHTS, so the
    'benchmark' = the EX-SELF, AUM-weighted aggregate equity portfolio of the fund's active peers.

        Active Share = 0.5 * Σ_i | w_fund,i − w_peers,i |   (equity sleeve, each renormalised to 1)

    Low (<~50%) = closet peer-hugger; high (>~70%) = differentiated. EX-SELF removes the fund's own
    weight from the consensus (no self-bias).

    GUARDS (adversarial verification, 2026-06-25 — the metric is correct & predictive on diversified
    funds, rho=+0.20, but it MISLEADS without scoping):
      • percentile is WITHIN sebi_category (raw AS is mechanically inflated by peer-pool size, so a
        245-peer thematic fund's 99% is NOT comparable to a 35-peer large-cap's 33%);
      • cat_type flags thematic (mandate-driven, not skill) and hybrid (equity-sleeve-only — cash/debt
        bet invisible) so the UI can suppress / footnote them;
      • aum_concentrated flags categories where one giant >40% of AUM dominates the 'peer consensus';
      • predictive_validated marks the categories where high AS actually predicted outperformance;
      • insufficient-peer funds are EMITTED with active_share=None (not silently dropped);
      • 'reliable' = a diversified, well-peered, non-concentrated fund whose AS is safe to show bare.

    Returns {navindia_code: {active_share (%|None), active_share_pctile (within-cat|None), n_peers,
    category, cat_type, ym, low_confidence, aum_concentrated, dominant_peer, dominant_share (%),
    predictive_validated (bool|None), reliable, caveat}}. Honest caveat: this is differentiation from
    PEERS, not from the cap-weighted index (which needs niftyindices weights)."""
    h, _ = _load()
    months = sorted(h["ym"].unique())
    if end_ym:
        months = [x for x in months if x <= end_ym]
    ym = months[-1]
    b = h[(h["ym"] == ym) & (~h["is_passive"])]            # active-equity peer universe
    fm = b.groupby(["navindia_code", "vst_id"])["market_value"].sum().reset_index()
    cat_of = b.groupby("navindia_code")["sebi_category"].first()
    name_of = b.groupby("navindia_code")["scheme_name"].first()

    out = {}
    for category, sub in fm.merge(cat_of.rename("cat"), on="navindia_code").groupby("cat"):
        funds = sub["navindia_code"].unique()
        npeers = len(funds)
        ctype = _as_cat_type(category)
        agg = sub.groupby("vst_id")["market_value"].sum()    # category aggregate rupees per stock (incl. self)
        total = float(agg.sum())
        # AUM-concentration: is one fund a giant share of the category's equity book?
        aum_by_fund = sub.groupby("navindia_code")["market_value"].sum()
        topshare = float(aum_by_fund.max() / aum_by_fund.sum()) if aum_by_fund.sum() > 0 else 0.0
        dominant_code = aum_by_fund.idxmax() if len(aum_by_fund) else None
        concentrated = bool(topshare > aum_conc_thresh)
        if category in _AS_PREDICTIVE_OK:
            pred = True
        elif category in _AS_PREDICTIVE_NULL:
            pred = False
        else:
            pred = None                                       # thematic / hybrid / untested -> unknown
        for f in funds:
            ff = sub[sub["navindia_code"] == f].set_index("vst_id")["market_value"]
            af = float(ff.sum())
            extotal = total - af
            rec = {"active_share": None, "active_share_pctile": None, "n_peers": int(npeers - 1),
                   "category": str(category), "cat_type": ctype, "ym": ym,
                   "low_confidence": True, "aum_concentrated": concentrated,
                   "dominant_peer": (str(name_of.get(dominant_code)) if (concentrated and dominant_code and dominant_code != f) else None),
                   "dominant_share": (round(topshare * 100, 0) if concentrated else None),
                   "predictive_validated": pred, "reliable": False, "caveat": ""}
            if af <= 0 or extotal <= 0:                       # too few peers to build a benchmark -> emit, don't drop
                rec["caveat"] = "Too few category peers to build a reliable peer benchmark."
                out[str(f)] = rec
                continue
            wf = ff / af                                      # fund weights (sum 1)
            exagg = agg.sub(ff, fill_value=0.0).clip(lower=0.0)  # ex-self category rupees per stock
            cons = exagg / extotal                            # ex-self peer consensus (sum ~1)
            idx = wf.index.union(cons.index)
            as_ = 0.5 * float((wf.reindex(idx, fill_value=0.0) - cons.reindex(idx, fill_value=0.0)).abs().sum())
            rec["active_share"] = round(as_ * 100, 1)
            rec["low_confidence"] = bool(npeers - 1 < min_peers)
            # honest, precomputed UI caveat — most-specific wins
            if ctype == "thematic":
                rec["caveat"] = "Sector/thematic mandate — high active share reflects the sector tilt, not stock-selection skill; not comparable to diversified funds."
            elif ctype == "hybrid":
                rec["caveat"] = "Equity sleeve only — this fund's debt/cash allocation (often its main active lever) is not captured."
            elif concentrated:
                rec["caveat"] = f"Category peer-benchmark dominated by {rec['dominant_peer'] or 'one giant fund'} ({rec['dominant_share']:.0f}% of category AUM) — reads as distance from that fund, not a broad benchmark."
            elif rec["low_confidence"]:
                rec["caveat"] = "Few category peers — interpret with caution."
            rec["reliable"] = bool(ctype == "diversified" and not concentrated and not rec["low_confidence"])
            out[str(f)] = rec
    # WITHIN-CATEGORY percentile (removes pool-size inflation; comparable only inside a category)
    bycat = {}
    for k, v in out.items():
        if v["active_share"] is not None:
            bycat.setdefault(v["category"], []).append(v["active_share"])
    for k, v in out.items():
        cat = v["category"]
        vals = sorted(bycat.get(cat, []))
        if v["active_share"] is not None and len(vals) >= 5:
            v["active_share_pctile"] = round(100.0 * sum(1 for x in vals if x <= v["active_share"]) / len(vals), 0)
    return out


def build_equity_books(sector_map=None, end_ym=None):
    """Per-fund EQUITY book for the latest store month: {navindia_code: [{vst_id, pct, sector, name}]}.
    Powers the cockpit's benchmark-relative active share (½·Σ|w_fund−w_bench|) — the client renormalises
    this to the equity sleeve and diffs it against a chosen benchmark portfolio by vst_id. `sector_map`
    (vst_id->sector, from the benchmark constituents) classifies the holdings for the sector-tilt view."""
    h, _ = _load()
    months = sorted(h["ym"].unique())
    ym = end_ym or months[-1]
    sm = sector_map or {}
    b = h[(h["ym"] == ym) & h["investment_type"].astype(str).str.contains("equ", case=False, na=False)]
    out = {}
    for code, g in b.groupby("navindia_code"):
        items = []
        for r in g.itertuples():
            if pd.isna(getattr(r, "vst_id", None)) or pd.isna(getattr(r, "pct", None)):
                continue
            vid = str(r.vst_id)
            nm = getattr(r, "company_name", None) or getattr(r, "vid_name", None) or getattr(r, "nse_symbol", None) or vid
            items.append({"vst_id": vid, "pct": round(float(r.pct), 3),
                          "sector": sm.get(vid) or "Unclassified", "name": str(nm)})
        if items:
            out[str(code)] = items
    return out


def build_stock_holders(end_ym=None, top=8):
    """Per-stock 'who owns it' for the Quant cockpit (analyst/PM lens): the mutual funds holding
    the stock in the latest month, ranked by rupee value. Returns {nse_symbol: {n_funds, total_cr,
    ym, top:[{name, amc, pct (% of that fund), cr}]}}. From the full equity store (active+passive)."""
    h, _ = _load()
    months = sorted(h["ym"].unique())
    if end_ym:
        months = [x for x in months if x <= end_ym]
    ym = months[-1]
    b = h[h["ym"] == ym]
    out = {}
    for vid, d in b.groupby("vst_id"):
        sym = d["nse_symbol"].iloc[0]
        if pd.isna(sym):
            continue
        dd = d.sort_values("market_value", ascending=False)
        top_h = [{"name": str(r.scheme_name), "amc": str(r.amc),
                  "pct": (None if pd.isna(r.pct) else round(float(r.pct), 2)),
                  "cr": round(float(r.market_value), 1)} for r in dd.head(top).itertuples()]
        out[str(sym)] = {"n_funds": int(d["navindia_code"].nunique()),
                         "total_cr": round(float(d["market_value"].sum()), 1),
                         "ym": ym, "top": top_h}
    return out


def build_market_summary(end_ym=None, top=15):
    """Market-wide cross-AMC flow snapshot for the latest month (CIO/analyst lens), small enough
    to embed inline. Top net bought/sold (clean), most broadly-held, biggest breadth gainers/
    losers, and the corporate-action quarantine list."""
    h, ret = _load()
    months = sorted(h["ym"].unique())
    if end_ym:
        months = [x for x in months if x <= end_ym]
    ym = months[-1]
    g = stock_flows(ym, h=h, ret=ret)
    c = clean_flows(g)
    def pack(df, cols=("net_flow_cr", "dbreadth", "breadth_end", "buyers", "sellers")):
        out = []
        for vid, r in df.iterrows():
            out.append({"sym": (None if pd.isna(r.get("sym")) else str(r.get("sym"))),
                        "name": (None if pd.isna(r.get("name")) else str(r.get("name"))),
                        "cr": round(float(r["net_flow_cr"]), 1), "dbreadth": int(r["dbreadth"]),
                        "breadth": int(r["breadth_end"]), "buyers": int(r["buyers"]), "sellers": int(r["sellers"])})
        return out
    qn = g[g["ca_flag"]].sort_values("net_flow_cr")
    return {
        "ym": ym,
        "top_bought": pack(c.sort_values("net_flow_cr", ascending=False).head(top)),
        "top_sold": pack(c.sort_values("net_flow_cr").head(top)),
        "most_crowded": pack(c.sort_values("breadth_end", ascending=False).head(top)),
        "breadth_gainers": pack(c.sort_values("dbreadth", ascending=False).head(top)),
        "breadth_losers": pack(c.sort_values("dbreadth").head(top)),
        "ca_quarantined": [{"sym": (None if pd.isna(r.get("sym")) else str(r.get("sym"))),
                            "name": (None if pd.isna(r.get("name")) else str(r.get("name"))),
                            "cr": round(float(r["net_flow_cr"]), 1)} for _, r in qn.head(10).iterrows()],
        "coverage": g.attrs.get("coverage", {}),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "market":
        import json as _j
        print(_j.dumps(build_market_summary(), indent=1)[:1500])
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "funds":
        fs = build_fund_series(months_back=18)
        print(f"built fund crowd-alignment for {len(fs)} schemes")
        ex = [v for v in fs.values() if v.get("herding_avg") is not None]
        ex.sort(key=lambda v: v["herding_avg"])
        print("\nMost CONTRARIAN (lowest herding):")
        for v in ex[:6]:
            print(f"  {v['herding_avg']:+.2f}  pctile={v.get('contrarian_pctile')}  {v['name']}")
        print("\nMost HERDING (follows the crowd):")
        for v in ex[-6:]:
            print(f"  {v['herding_avg']:+.2f}  pctile={v.get('contrarian_pctile')}  {v['name']}")
        sys.exit(0)
    ym = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
    g = stock_flows(ym)
    print("coverage:", g.attrs["coverage"])
    cols = ["name","sym","net_flow_cr","dbreadth","breadth_end","buyers","sellers","ret_1m","merged_from"]
    fmt = {"net_flow_cr":"{:+,.0f}".format,"ret_1m":"{:+.1%}".format}
    c = clean_flows(g)
    print("\nTOP 12 NET BOUGHT (Rs cr, active mgrs; drift + corp-actions neutralised):")
    print(c.head(12)[cols].to_string(formatters=fmt))
    print("\nTOP 12 NET SOLD:")
    print(c.tail(12)[cols].iloc[::-1].to_string(formatters=fmt))
    qn = g[g["ca_flag"]]
    if len(qn):
        print(f"\nQUARANTINED — structural corporate action this month ({len(qn)}), flow not a clean signal:")
        print(qn[["name","sym","net_flow_cr","dbreadth","ret_1m"]].head(10).to_string(formatters=fmt))
