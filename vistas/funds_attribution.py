"""
funds_attribution.py — the MoneyBall fund-manager analyser (Phase 1, scheme-level).

WHAT (first principles)
-----------------------
A fund's edge over its benchmark is, exactly, the dot product of its bets with what happened:
    A(t) = R_p(t) - R_b(t) = Σ_i w_i(t)·r_i(t→t+1)  - benchmark return
with w_i = the fund's start-of-month weight, r_i = the holding's TOTAL return next month.
This module computes, per scheme over its whole history, the holdings-based skill metrics from
FUND_ANALYSER_DESIGN.md Phase 1, on our verified panel:
  holdings_history.parquet  ⋈  tr_returns_monthly.parquet (TOTAL return)  ⋈  benchmark index TR.

Design choices that do the heavy lifting (see FUND_MANAGER_ANALYSER_DESIGN.md):
- TOTAL return throughout (not price return) — dividends count toward skill.
- Benchmark each fund against its SEBI-CATEGORY index (small-cap fund vs the small-cap index),
  which strips most of the cap-SIZE tilt so the residual excess is mostly selection+sizing skill.
- Equity sleeve only, renormalised to 100% (debt/cash excluded; ~10% of value).
- Significance via t = IR·√years + a block-bootstrap CI on mean active return; the tilt-matched
  random-portfolio null and the factor-α deflation are Phase 2 (flagged, not faked).

Display-plane: everything is precomputed here in Python and baked into one small per-scheme JSON;
no JS-parity port (analytics.py untouched).
"""
from __future__ import annotations
import os, json, math
import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HIST = os.path.join(_ROOT, "data", "funds", "history")
HOLDINGS = os.path.join(_HIST, "holdings_history.parquet")
TR = os.path.join(_HIST, "tr_returns_monthly.parquet")
OUTDIR = os.path.join(_ROOT, "data", "funds_attribution")

# SEBI category -> benchmark TR index (names must match vistas/data.py columns). Broad NIFTY 500
# for the diversified categories; cap-specific where SEBI defines a cap mandate. Hybrids are
# benchmarked on their EQUITY SLEEVE vs NIFTY 500 (flagged equity-sleeve attribution).
_CAT_BENCH = {
    "Large Cap Fund": "NIFTY 100",
    "Mid Cap Fund": "NIFTY MIDCAP 100",
    "Small Cap Fund": "NIFTY SMALLCAP 250",
    "Large & Mid Cap Fund": "NIFTY LARGEMIDCAP 250",
    "Flexi Cap Fund": "NIFTY 500", "Multi Cap Fund": "NIFTY 500", "Focused Fund": "NIFTY 500",
    "ELSS": "NIFTY 500", "Value Fund": "NIFTY 500", "Contra Fund": "NIFTY 500",
    "Dividend Yield Fund": "NIFTY 500", "Sectoral / Thematic": "NIFTY 500",
    "Retirement Fund": "NIFTY 500", "Childrens Fund": "NIFTY 500",
    "Multi Asset Allocation": "NIFTY 500",
    "Aggressive Hybrid Fund": "NIFTY 500", "Conservative Hybrid Fund": "NIFTY 500",
    "Balanced Hybrid Fund": "NIFTY 500", "Equity Savings": "NIFTY 500",
    "Dynamic Asset Allocation or Balanced Advantage": "NIFTY 500", "Arbitrage Fund": "NIFTY 500",
}
_DEFAULT_BENCH = "NIFTY 500"
_HYBRID = {"Aggressive Hybrid Fund", "Conservative Hybrid Fund", "Balanced Hybrid Fund",
           "Equity Savings", "Dynamic Asset Allocation or Balanced Advantage", "Arbitrage Fund",
           "Multi Asset Allocation"}

_RET_CLIP = (-0.80, 3.0)   # winsorise monthly TR: kill data errors / delisting stubs, keep real moves
_MIN_COVER = 0.80          # require ≥80% of equity weight to have a forward return that month
_MIN_MONTHS = 24           # below this: "insufficient history", never a skill verdict


def _bench_monthly_fwd() -> pd.DataFrame:
    """Forward 1-month TOTAL return of every benchmark index, indexed by ym (the return t→t+1
    stamped on month t). Returns a tidy {index_name: {ym: fwd_ret}} via a long frame."""
    from vistas import data as vdata
    px = vdata.load()
    me = px.resample("ME").last()
    ret = me.pct_change()                       # ret at ym = return ENDING that month
    ret.index = ret.index.strftime("%Y-%m")
    fwd = ret.shift(-1)                          # forward: stamp t+1's return onto month t
    fwd.index.name = "ym"
    return fwd


def load_panel() -> pd.DataFrame:
    """Per (scheme, month) attribution panel: paper return, benchmark, active return, equal-weight
    counterfactual, breadth, concentration, and the monthly selection IC. One row = one scheme-month."""
    h = pd.read_parquet(HOLDINGS, columns=["navindia_code", "scheme_name", "amc", "sebi_category",
                                           "ym", "investment_type", "vst_id", "pct"])
    # SCHEME IDENTITY: fold re-code splits (audit 2026-06-24) so a re-coded fund's full history is one
    # series and it appears ONCE (e.g. Kotak Large Cap 262->1223, Canara Robeco ELSS 456->10291).
    from . import scheme_identity as _sid
    h["navindia_code"] = h["navindia_code"].map(_sid.canonical_code)
    # DOMESTIC equity sleeve only — exclude 'foreign equity' / 'foreign mutual funds (equity fund)'
    # (no NSE TR for them; folding them in would inflate weights and understate concentration).
    _it = h["investment_type"].astype(str).str.lower()
    h = h[_it.str.contains("equity", na=False) & ~_it.str.contains("foreign", na=False)].copy()
    h = h[h["vst_id"].notna() & h["pct"].notna()]
    h["pct"] = pd.to_numeric(h["pct"], errors="coerce")
    h = h[h["pct"] > 0]

    tr = pd.read_parquet(TR, columns=["vst_id", "ym", "ret_1m"]) if "ym" in pd.read_parquet(TR, columns=[]).columns \
        else pd.read_parquet(TR, columns=["vst_id", "date", "ret_1m"])
    if "ym" not in tr.columns:
        tr["ym"] = pd.to_datetime(tr["date"]).dt.strftime("%Y-%m")
    tr = tr[tr["vst_id"].notna()].copy()
    tr["ret_1m"] = pd.to_numeric(tr["ret_1m"], errors="coerce").clip(*_RET_CLIP)

    months = sorted(set(h["ym"]) | set(tr["ym"]))
    nxt = {m: months[i + 1] for i, m in enumerate(months[:-1])}
    h["fwd_ym"] = h["ym"].map(nxt)
    trf = tr.rename(columns={"ym": "fwd_ym", "ret_1m": "fwd_ret"})[["vst_id", "fwd_ym", "fwd_ret"]]
    j = h.merge(trf, on=["vst_id", "fwd_ym"], how="left")

    # per scheme-month weights (renormalised over the equity sleeve)
    j["wsum"] = j.groupby(["navindia_code", "ym"])["pct"].transform("sum")
    j["w"] = j["pct"] / j["wsum"]
    cov = j["fwd_ret"].notna()
    j["cw"] = np.where(cov, j["w"], 0.0)                 # covered weight
    j["contrib"] = j["cw"] * j["fwd_ret"].fillna(0.0)

    g = j.groupby(["navindia_code", "ym"])
    panel = g.agg(scheme_name=("scheme_name", "first"), amc=("amc", "first"),
                  sebi_category=("sebi_category", "first"),
                  cover=("cw", "sum"), n=("vst_id", "nunique"),
                  rp_raw=("contrib", "sum"), herf=("w", lambda s: float((s ** 2).sum()))).reset_index()
    # equal-weight counterfactual + selection IC need the covered names only
    def _ew(s):
        v = s.dropna()
        return float(v.mean()) if len(v) else np.nan
    ew = g.apply(lambda d: _ew(d.loc[d["fwd_ret"].notna(), "fwd_ret"]), include_groups=False).rename("ew")
    def _ic(d):
        dd = d.loc[d["fwd_ret"].notna(), ["w", "fwd_ret"]]
        if len(dd) < 5 or dd["fwd_ret"].nunique() < 3:
            return np.nan
        return float(dd["w"].rank().corr(dd["fwd_ret"].rank()))
    ic = g.apply(_ic, include_groups=False).rename("ic")
    panel = panel.merge(ew, on=["navindia_code", "ym"]).merge(ic, on=["navindia_code", "ym"])
    # renormalise paper return to covered weight (don't penalise for a few unmatched names)
    panel["rp"] = panel["rp_raw"] / panel["cover"].replace(0, np.nan)
    panel = panel[panel["cover"] >= _MIN_COVER].copy()

    # benchmark forward return per scheme-month
    fwd = _bench_monthly_fwd()
    panel["bench"] = panel["sebi_category"].map(lambda c: _CAT_BENCH.get(c, _DEFAULT_BENCH))
    fwd_long = fwd.reset_index().melt(id_vars="ym", var_name="bench", value_name="rb")
    panel = panel.merge(fwd_long, on=["ym", "bench"], how="left")
    panel = panel[panel["rb"].notna()].copy()
    panel["A"] = panel["rp"] - panel["rb"]                 # active return
    panel["sizing"] = panel["rp"] - panel["ew"]            # weighting vs equal-weighting the same names

    # ---- PORTFOLIO-LEVEL batting & slug (stock cross-section) — KV's MoneyBall "vantage point" defs ----
    # These read the manager's stock-picking DIRECTLY off the holdings each month (vs the NAV-level
    # batting/slug above, which read it through the aggregated fund return). Two truths, both wanted.
    #   HIT RATE  : per holding alpha_i = r_i − r_bench; count = share of held stocks with alpha_i ≥ 0;
    #               AUM = Σ wᵢ·1[alpha_i ≥ 0]. (AUM − count = "allocation benefit": did the PM overweight
    #               the winners?)  Threshold is the SEBI-category benchmark return, ≥ 0 (his exact rule).
    #   SLUG RATE : top/bottom QUARTILE of the FULL tradeable universe that month (his int(0.25·N), labelled
    #               "20%"); slug = (% of the book in the top quartile) − (% in the bottom quartile), both
    #               count- and AUM-weighted. Net positive = the book leaned toward the eventual winners.
    uq = tr.groupby("ym")["ret_1m"].quantile([0.25, 0.75]).unstack()   # universe return cut per month
    jb = j[j["fwd_ret"].notna()].copy()
    jb["bench"] = jb["sebi_category"].map(lambda c: _CAT_BENCH.get(c, _DEFAULT_BENCH))
    jb = jb.merge(fwd_long, on=["ym", "bench"], how="left")            # rb = category-bench fwd return
    jb = jb[jb["rb"].notna()].copy()
    jb["u25"] = jb["fwd_ym"].map(uq[0.25]); jb["u75"] = jb["fwd_ym"].map(uq[0.75])
    jb["wc"] = jb.groupby(["navindia_code", "ym"])["pct"].transform(lambda s: s / s.sum())  # renorm over covered
    jb["beat"] = (jb["fwd_ret"] - jb["rb"] >= 0).astype(float)
    jb["intop"] = (jb["fwd_ret"] >= jb["u75"]).astype(float)
    jb["inbot"] = (jb["fwd_ret"] <= jb["u25"]).astype(float)
    jb["_ha"] = jb["wc"] * jb["beat"]
    jb["_sa"] = jb["wc"] * (jb["intop"] - jb["inbot"])
    pg = jb.groupby(["navindia_code", "ym"])
    port = pg.agg(port_hit_cnt=("beat", "mean"), port_hit_aum=("_ha", "sum"),
                  port_slug_aum=("_sa", "sum"), _it=("intop", "mean"), _ib=("inbot", "mean")).reset_index()
    port["port_slug_cnt"] = port["_it"] - port["_ib"]
    panel = panel.merge(port[["navindia_code", "ym", "port_hit_cnt", "port_hit_aum",
                              "port_slug_cnt", "port_slug_aum"]], on=["navindia_code", "ym"], how="left")
    return panel.sort_values(["navindia_code", "ym"]).reset_index(drop=True)


def _block_bootstrap_mean(a: np.ndarray, n_boot: int = 2000, block: int = 3) -> tuple:
    """CIRCULAR block-bootstrap CI + percentile that mean(active) > 0 (handles autocorrelation).
    Seeded from a STABLE integer (built-in hash() is PYTHONHASHSEED-salted → flickers across runs)."""
    a = a[~np.isnan(a)]
    n = len(a)
    if n < 6:
        return (np.nan, np.nan, np.nan)
    nb = int(math.ceil(n / block))
    rng = np.random.default_rng(1234567 + n)               # reproducible across builds
    means = np.empty(n_boot)
    starts = rng.integers(0, n, size=(n_boot, nb))         # circular: any start, wrap mod n (no edge under-weight)
    offs = np.arange(block)
    for b in range(n_boot):
        idx = ((starts[b][:, None] + offs).ravel()[:n]) % n
        means[b] = a[idx].mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    p_pos = float((means > 0).mean())
    return (float(lo), float(hi), p_pos)


def scheme_metrics(panel: pd.DataFrame, ppy: int = 12) -> pd.DataFrame:
    """Reduce the scheme-month panel to ONE row of skill metrics per scheme, with a verdict."""
    out = []
    for code, d in panel.groupby("navindia_code"):
        d = d.sort_values("ym")
        n = len(d)
        A = d["A"].values
        rp, rb = d["rp"].values, d["rb"].values
        # years from the actual CALENDAR span — a gappy monthly panel would over-annualise on n/12
        oms = [int(s[:4]) * 12 + int(s[5:7]) for s in d["ym"]]
        span_m = (max(oms) - min(oms) + 1) if oms else n
        years = span_m / ppy
        gappy = span_m > n
        # compounded paper vs benchmark -> annualised excess (geometric, honest)
        cum_p = float(np.prod(1 + rp)); cum_b = float(np.prod(1 + rb))
        cagr_p = cum_p ** (1 / years) - 1 if years > 0 and cum_p > 0 else np.nan
        cagr_b = cum_b ** (1 / years) - 1 if years > 0 and cum_b > 0 else np.nan
        excess = (cagr_p - cagr_b) if (np.isfinite(cagr_p) and np.isfinite(cagr_b)) else np.nan
        mA, sA = np.nanmean(A), np.nanstd(A, ddof=1)
        ir = (mA * ppy) / (sA * math.sqrt(ppy)) if sA and sA > 0 else np.nan      # = mean/sd*sqrt(ppy)
        t = ir * math.sqrt(years) if np.isfinite(ir) else np.nan
        years_needed = (1.96 / ir) ** 2 if np.isfinite(ir) and ir > 0 else np.nan
        te = sA * math.sqrt(ppy)                                                   # tracking error (ann)
        ic = d["ic"].dropna().values
        ic_mean = float(np.mean(ic)) if len(ic) else np.nan
        ic_t = (np.mean(ic) / (np.std(ic, ddof=1) / math.sqrt(len(ic)))) if len(ic) > 3 and np.std(ic, ddof=1) > 0 else np.nan
        # sizing edge = the fund's ACTUAL-weighted book vs an EQUAL-weighting of the SAME names each month.
        # Reported ANNUALISED (per-year, scale-free) — the raw cumulative gap grows with history length, so it
        # over/under-states the same yearly drag for a long/short track record. sizing_cum (absolute) is kept
        # only because the verdict reads its SIGN (sign(sizing_cagr) == sign(sizing_cum)).
        if d["ew"].notna().all():
            _pr = float(np.prod(1 + d["rp"].values)); _pe = float(np.prod(1 + d["ew"].values))
            sizing_cum = _pr - _pe
            sizing_cagr = ((_pr / _pe) ** (1.0 / years) - 1.0) if (_pe > 0 and years > 0) else np.nan
        else:
            sizing_cum = float(np.nansum(d["sizing"].values))     # cumulative sizing edge (approx if gaps)
            sizing_cagr = (sizing_cum / years) if years > 0 else np.nan   # arithmetic per-year fallback
        hit_m = float((A > 0).mean())                         # BATTING AVERAGE: share of months the fund beat the bench
        # magnitude-weighted hit: share of |active| from positive months (dollar-weighted intuition)
        mag_hit = float(np.nansum(np.clip(A, 0, None)) / np.nansum(np.abs(A))) if np.nansum(np.abs(A)) > 0 else np.nan
        # SLUGGING: how big the wins are vs the losses — avg up-month active ÷ |avg down-month active|.
        # >1 = winning months outweigh losing months (a manager can win on frequency OR magnitude).
        _Aok = A[np.isfinite(A)]; _up = _Aok[_Aok > 0]; _dn = _Aok[_Aok < 0]
        avg_win = float(np.mean(_up)) if len(_up) else np.nan
        avg_loss = float(np.mean(_dn)) if len(_dn) else np.nan          # negative
        slugging = float(avg_win / abs(avg_loss)) if (len(_up) and len(_dn) and avg_loss != 0) else np.nan
        # PORTFOLIO-level (stock cross-section) batting & slug — period mean of the monthly series
        def _nm(col):
            v = d[col].values; v = v[np.isfinite(v)]
            return float(np.mean(v)) if len(v) else np.nan
        ph_cnt, ph_aum = _nm("port_hit_cnt"), _nm("port_hit_aum")
        ps_cnt, ps_aum = _nm("port_slug_cnt"), _nm("port_slug_aum")
        herf = float(d["herf"].iloc[-1]); herf_avg = float(d["herf"].mean())
        effN = 1 / herf if herf > 0 else np.nan
        lo, hi, p_pos = _block_bootstrap_mean(A)
        is_thematic = (d["sebi_category"].iloc[-1] == "Sectoral / Thematic")

        # --- verdict (honest, gated) ---
        # "skilled" = the CATEGORY-benchmark excess is positive AND statistically real: t≥2 on the
        # ARITHMETIC active series (the SAME series the t is built from — avoids a geometric/arithmetic
        # sign-mismatch) AND the bootstrap null clears (mean active > 0 in ≥95% of resamples). Excess
        # shown is GROSS (pre-cost) and pre-factor-deflation. "holding-rank IC" tags the source but is a
        # cap-tilt-contaminated proxy (true active-weight IC needs point-in-time benchmark weights).
        _src = ("holding-rank-driven" if np.isfinite(ic_t) and ic_t >= 2 else
                ("sizing-aided" if sizing_cum > 0 and (not np.isfinite(ic_t) or ic_t < 1) else "mixed-source"))
        _them = " — but vs the broad market, so largely a sector bet, not pure selection" if is_thematic else ""
        sig = np.isfinite(t) and t >= 2 and mA > 0 and np.isfinite(p_pos) and p_pos >= 0.95
        if n < _MIN_MONTHS:
            verdict, vwhy = "insufficient history", f"only {n} months — no skill verdict"
        elif not np.isfinite(t):
            verdict, vwhy = "undefined", "no active-return variance"
        elif te < 0.02:
            verdict, vwhy = "index-like", f"tracking error {te*100:.1f}% — little active risk to judge"
        elif sig:
            verdict, vwhy = "skilled", f"+{excess*100:.1f}%/yr gross, t={t:.1f}, bootstrap {p_pos*100:.0f}% ({_src}){_them}"
        elif np.isfinite(ic_t) and ic_t >= 2 and sizing_cum < 0:
            verdict, vwhy = "good selector, weak sizer", f"holding-IC-t={ic_t:.1f} but sizing drag {sizing_cagr*100:.1f}%/yr"
        elif np.isfinite(excess) and excess > 0:
            _need = f" (need t≥2 & bootstrap≥95%; ~{years_needed:.0f}y more)" if np.isfinite(years_needed) else ""
            verdict, vwhy = "ahead but not yet significant", f"+{excess*100:.1f}%/yr, t={t:.1f}{_need}"
        elif np.isfinite(excess) and excess <= 0:
            verdict, vwhy = "lagging benchmark", f"{excess*100:.1f}%/yr"
        else:
            verdict, vwhy = "inconclusive", ""

        rec = dict(
            navindia_code=str(code), scheme_name=d["scheme_name"].iloc[-1], amc=d["amc"].iloc[-1],
            sebi_category=d["sebi_category"].iloc[-1], benchmark=d["bench"].iloc[-1],
            n_months=int(n), years=round(years, 1), gappy=bool(gappy), is_thematic=bool(is_thematic),
            is_hybrid=bool(d["sebi_category"].iloc[-1] in _HYBRID),
            excess_cagr=_r(excess), cagr_paper=_r(cagr_p), cagr_bench=_r(cagr_b),
            info_ratio=_r(ir), t_stat=_r(t), years_needed=_r(years_needed), tracking_error=_r(te),
            ic_mean=_r(ic_mean), ic_t=_r(ic_t), sizing_edge_cum=_r(sizing_cum), sizing_drag_cagr=_r(sizing_cagr, 4),
            hit_rate_monthly=_r(hit_m), mag_hit=_r(mag_hit),
            slugging=_r(slugging, 2), avg_win=_r(avg_win, 5), avg_loss=_r(avg_loss, 5),
            port_hit_cnt=_r(ph_cnt), port_hit_aum=_r(ph_aum), port_slug_cnt=_r(ps_cnt), port_slug_aum=_r(ps_aum),
            herfindahl=_r(herf), herfindahl_avg=_r(herf_avg), eff_n=_r(effN, 1), avg_names=_r(d["n"].mean(), 1),
            boot_meanA_lo=_r(lo, 5), boot_meanA_hi=_r(hi, 5), boot_p_positive=_r(p_pos),
            verdict=verdict, verdict_why=vwhy,
            basis="GROSS holdings-implied total-return excess vs the SEBI-category benchmark "
                  "(pre-cost, pre-cash-drag, pre-factor-deflation); domestic equity sleeve only, month-end snapshots. "
                  "‘Holding-rank IC’ is a cap-tilt-contaminated proxy, not pure active-weight selection (needs point-in-time benchmark weights).",
            # ts carries the FULL monthly series so the browser can recompute EVERY metric over ANY
            # start→end window (a manager's tenure) — A/rp/rb drive return+batting+IR; herf→eff_n;
            # sz (=rp−ew, the sizing edge per month)→cumulative sizing over the window.
            ts=[{"ym": r.ym, "A": _r(r.A, 5), "rp": _r(r.rp, 5), "rb": _r(r.rb, 5),
                 "ic": _r(r.ic), "n": int(r.n), "herf": _r(r.herf, 5), "sz": _r(r.sizing, 5),
                 "hc": _r(r.port_hit_cnt, 4), "ha": _r(r.port_hit_aum, 4),
                 "sc": _r(r.port_slug_cnt, 4), "sa2": _r(r.port_slug_aum, 4)} for r in d.itertuples()],
        )
        # null the numeric SKILL fields for too-short history so a downstream UI can't surface a
        # 1-month fund's IR/t/excess as meaningful (concentration + descriptive fields stay).
        if n < _MIN_MONTHS:
            for k in ("excess_cagr", "cagr_paper", "cagr_bench", "info_ratio", "t_stat", "years_needed",
                      "ic_mean", "ic_t", "sizing_edge_cum", "sizing_drag_cagr", "hit_rate_monthly", "mag_hit",
                      "slugging", "avg_win", "avg_loss",
                      "port_hit_cnt", "port_hit_aum", "port_slug_cnt", "port_slug_aum",
                      "boot_meanA_lo", "boot_meanA_hi", "boot_p_positive"):
                rec[k] = None
        out.append(rec)
    return out                                          # list[dict] — None preserved (no DataFrame NaN round-trip)


def _clean_nan(o):
    """Recursively replace any non-finite float with None so json.dump(allow_nan=False) is safe."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean_nan(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean_nan(v) for v in o]
    return o


def _r(x, nd=4):
    try:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return None
        return round(float(x), nd)
    except Exception:
        return None


# ---- PEER-ENVELOPE (KV's MoneyBall "vantage point") -----------------------------------------------
# For each SEBI category and each metric, the cross-section ACROSS FUNDS per month: min / 25th / 50th /
# 75th / max — so a fund is read against its peers, not in a vacuum (his groupby(['Category',date]).quantile).
# Two metric families, each as a per-fund monthly series (mirrored byte-for-byte in vistas.js so the fund's
# own line sits exactly inside the band):
#   PORTFOLIO (stock cross-section, 3-month smoothed, his 60-trading-day≈3mo rolling mean):
#       port_hit_aum  = AUM-weighted % of holdings beating the bench   (×100, a %)
#       port_slug_aum = net AUM in the top minus bottom universe-quartile (×100, % points)
#   NAV (fund-return, 36-month rolling — the skill-proving horizon):
#       nav_bat = % of the trailing 36 months the fund beat its bench
#       nav_slug = avg up-month active ÷ |avg down-month active| over the trailing 36 months
_ENV_ROLL = 36          # NAV rolling window (months)
_ENV_SMOOTH = 3         # portfolio smoothing (months), min_periods=2

def _ma(x, win=_ENV_SMOOTH, minp=2):
    out = []
    for i in range(len(x)):
        w = [v for v in x[max(0, i - win + 1):i + 1] if v is not None and np.isfinite(v)]
        out.append(float(np.mean(w)) if len(w) >= minp else None)
    return out

def _roll_bat(x, win=_ENV_ROLL):
    out = []
    for i in range(len(x)):
        if i < win - 1: out.append(None); continue
        w = [v for v in x[i - win + 1:i + 1] if v is not None and np.isfinite(v)]
        out.append(round(100.0 * sum(1 for v in w if v > 0) / len(w), 3) if w else None)
    return out

def _roll_slug(x, win=_ENV_ROLL):
    out = []
    for i in range(len(x)):
        if i < win - 1: out.append(None); continue
        w = [v for v in x[i - win + 1:i + 1] if v is not None and np.isfinite(v)]
        up = [v for v in w if v > 0]; dn = [v for v in w if v < 0]
        out.append(round((np.mean(up)) / abs(np.mean(dn)), 4) if (up and dn) else None)
    return out

def fund_vantage_series(rec) -> tuple:
    """Per-fund monthly metric series for the vantage envelope (same math the JS fund-line uses)."""
    ts = rec.get("ts", [])
    ym = [p["ym"] for p in ts]
    ha = [p.get("ha") for p in ts]; sa = [p.get("sa2") for p in ts]; A = [p.get("A") for p in ts]
    pct = lambda v: round(v * 100.0, 3) if (v is not None and np.isfinite(v)) else None
    return ym, {
        "port_hit_aum": [pct(v) for v in _ma(ha)],
        "port_slug_aum": [pct(v) for v in _ma(sa)],
        "nav_bat": _roll_bat(A),
        "nav_slug": _roll_slug(A),
    }

def build_envelopes(recs) -> dict:
    """Cross-sectional peer envelope per (category, metric, month) across the supplied funds."""
    from collections import defaultdict
    bycat = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for rec in recs:
        cat = rec.get("sebi_category") or "Uncategorized"
        ym, series = fund_vantage_series(rec)
        for metric, vals in series.items():
            for t, v in zip(ym, vals):
                if v is not None and np.isfinite(v):
                    bycat[cat][metric][t].append(v)
    out = {}
    for cat, metrics in bycat.items():
        out[cat] = {}
        for metric, bydate in metrics.items():
            dates = sorted(bydate)
            mn, p25, p50, p75, mx, nn = [], [], [], [], [], []
            for t in dates:
                arr = bydate[t]
                mn.append(round(float(np.min(arr)), 3)); mx.append(round(float(np.max(arr)), 3))
                p25.append(round(float(np.percentile(arr, 25)), 3))
                p50.append(round(float(np.percentile(arr, 50)), 3))
                p75.append(round(float(np.percentile(arr, 75)), 3))
                nn.append(len(arr))
            out[cat][metric] = {"dates": dates, "min": mn, "p25": p25, "p50": p50, "p75": p75, "max": mx, "n": nn}
    return out


def _build_skill_context(recs, panel) -> dict:
    """ADDITIVE skill-engine substrate, built ONCE per build (only when posterior=True): the category
    prior table, the factor legs, the forward-return tables, the universe/flows stores, and per-category
    peer-consensus + benchmark-forward maps (lazily filled). Returns the `ctx` dict passed to
    _attach_skill. Heavy (legs + flows + fwd) — built once, reused across all funds."""
    from . import skill_engine as _se
    from . import skill_factors as _sf
    from . import skill_signals as _ss
    from . import funds_flows as _ff
    prior_table = _se.build_prior_table(recs)
    legs = _sf.get_factor_legs(log=lambda *a, **k: None)
    fwd_by_k = _ss.build_fwd_returns()
    universe = _ss.build_universe_fwd(fwd_by_k.get(1))
    h_flow, ret = _ff._load()
    return {"prior_table": prior_table, "legs": legs, "fwd_by_k": fwd_by_k,
            "universe": universe, "h_flow": h_flow, "ret": ret,
            "consensus": {}, "bench_fwd": {}}   # per-category caches, lazily filled in _attach_skill


def _attach_skill(record, panel, ctx, build_id) -> dict:
    """ADDITIVE per-fund wiring: build the fund's panel slice, lazily build its category's peer-consensus
    + benchmark-forward maps (cached in ctx), call skill_engine.compute_skill(...), attach record["skill"]
    and return the skill dict (so the book-level FDR/rank pass can finish it). Mutates ONLY record["skill"]."""
    from . import skill_engine as _se
    from . import skill_signals as _ss
    code = str(record["navindia_code"])
    cat = record.get("sebi_category")
    d = panel[panel["navindia_code"].astype(str) == code]
    if cat not in ctx["consensus"]:
        ctx["consensus"][cat] = _ss.build_consensus_by_ym(cat)
        ctx["bench_fwd"][cat] = _ss.build_bench_fwd(cat)
    shared = {"fwd_by_k": ctx["fwd_by_k"], "ret": ctx["ret"], "h_flow": ctx["h_flow"], "h_hold": None,
              "consensus": ctx["consensus"][cat], "universe": ctx["universe"],
              "bench_fwd": ctx["bench_fwd"][cat]}
    sk = _se.compute_skill(record, d, None, ctx["legs"], ctx["prior_table"],
                           build_id=build_id, shared=shared)
    _se.apply_to_record(record, sk)     # record["skill"] = sk (+ _category for the book-level pass)
    return record["skill"]


def build_all(outdir: str = OUTDIR, flows_by_fund=None, posterior: bool = False,
              build_id=None) -> dict:
    """Compute every scheme's attribution → one JSON per scheme + a manifest/summary.
    Each scheme JSON also carries a `portfolio` block (asset/sector mix, categorized book,
    top holdings, 13-yr sector rotation, concentration) from funds_portfolio_viz — so the
    Fund-cockpit can SHOW the actual book alongside the skill verdict, no extra deck wiring.
    `flows_by_fund` (optional, from funds_flows.build_fund_series) attaches the per-fund
    crowd-alignment / herding + latest active trades as `crowd_flow`.

    ★ ADDITIVE SKILL-ENGINE HOOK (posterior=False by DEFAULT → the live build is byte-identical).
    When posterior=True, AFTER the legacy scheme_metrics for each kept fund we attach
    record["skill"] = skill_engine.compute_skill(...) (the schema_version:2 posterior block) and
    mirror its headline fields into _manifest.json. ALL legacy keys + the legacy `verdict` string
    are RETAINED untouched; the manifest's legacy `verdict`/`excess_cagr`/`t_stat`/`ic_t` stay, with
    the posterior fields ADDED alongside. Nothing is removed; no consumer default is flipped."""
    os.makedirs(outdir, exist_ok=True)
    panel = load_panel()
    recs = scheme_metrics(panel)
    try:
        from . import funds_portfolio_viz as _fpv
        viz = _fpv.build_viz()
    except Exception as _e:
        print(f"[funds_attribution] portfolio-viz unavailable ({_e}); shipping skill-only JSON")
        viz = {}
    from . import scheme_identity as _sid

    # ── ADDITIVE skill-engine substrate (built ONCE; only when posterior=True) ───────────────────
    _skill_ctx = None
    if posterior:
        _skill_ctx = _build_skill_context(recs, panel)

    manifest = {}
    kept = []
    skill_by_fund = {}            # {code: skill_dict} → book-level FDR + rank after the loop
    n_gated = 0
    for r in recs:
        # gate non-equity-skill noise out of the leaderboard (arbitrage / one-month ingestion fragments)
        if not _sid.in_skill_universe(r.get("sebi_category"), r.get("n_months")):
            n_gated += 1
            continue
        key0 = str(r["navindia_code"])
        if viz.get(key0):
            r["portfolio"] = viz[key0]
        if flows_by_fund and flows_by_fund.get(key0):
            r["crowd_flow"] = flows_by_fund[key0]
        # ADDITIVE: attach the schema_version:2 skill block (rank/passes_fdr filled post-loop)
        if posterior and _skill_ctx is not None:
            try:
                sk = _attach_skill(r, panel, _skill_ctx, build_id)
                skill_by_fund[key0] = sk
            except Exception as _se:
                print(f"[funds_attribution] skill compute failed for {key0}: {_se}")
        r = _clean_nan(r)
        key = r["navindia_code"]
        with open(os.path.join(outdir, f"{key}.json"), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, allow_nan=False)
        kept.append(r)
        manifest[key] = {"name": r["scheme_name"], "amc": r["amc"], "category": r["sebi_category"],
                         "verdict": r["verdict"], "excess_cagr": r["excess_cagr"], "t_stat": r["t_stat"],
                         "ic_t": r["ic_t"], "n_months": r["n_months"]}

    # ── ADDITIVE book-level pass: FDR + within-category rank, then RE-WRITE the touched files +
    #    mirror the headline posterior fields into the manifest (legacy keys retained) ───────────
    if posterior and skill_by_fund:
        from . import skill_engine as _se
        _se.fdr_and_rank(skill_by_fund)
        # re-attach the (now FDR/rank-complete) skill block to each kept record, re-write its JSON,
        # and add the posterior headline fields to the manifest alongside the legacy ones.
        kept_by_code = {rr["navindia_code"]: rr for rr in kept}
        for code, sk in skill_by_fund.items():
            rr = kept_by_code.get(code)
            if rr is None:
                continue
            sk.pop("_category", None)
            rr["skill"] = _clean_nan(sk)
            with open(os.path.join(outdir, f"{code}.json"), "w", encoding="utf-8") as f:
                json.dump(rr, f, ensure_ascii=False, allow_nan=False)
            if code in manifest:
                mf = _clean_nan(_se.manifest_fields(sk))
                # legacy `verdict` stays the legacy string; the tag mirror is added under `tag`
                mf.pop("verdict", None)
                manifest[code].update(mf)

    with open(os.path.join(outdir, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, allow_nan=False)
    # PEER-ENVELOPE: per-category cross-sectional vantage bands over the kept (skill-universe) funds.
    envelopes = build_envelopes(kept)
    with open(os.path.join(outdir, "_envelopes.json"), "w", encoding="utf-8") as f:
        json.dump(_clean_nan(envelopes), f, ensure_ascii=False, allow_nan=False)
    # remove ORPHANS — files from a prior build whose code is no longer in the manifest (merged
    # successors like 262/456, now-gated arbitrage/fragments) so they never ship or get lazy-fetched.
    keep = set(manifest) | {"_manifest", "_envelopes"}
    n_orphan = 0
    for fn in os.listdir(outdir):
        if fn.endswith(".json") and fn[:-5] not in keep:
            try:
                os.remove(os.path.join(outdir, fn)); n_orphan += 1
            except OSError:
                pass
    print(f"[funds_attribution] wrote {len(manifest)} schemes; gated {n_gated}; removed {n_orphan} orphan files")
    return {"n_schemes": len(manifest), "outdir": outdir, "manifest": manifest, "summary_df": pd.DataFrame(recs)}


if __name__ == "__main__":
    import sys
    res = build_all()
    m = res["summary_df"]
    print(f"\n=== built {res['n_schemes']} schemes -> {res['outdir']} ===")
    # cross-section sanity
    eq = m[~m["is_hybrid"]]
    print("\nverdict distribution (equity funds):")
    print(eq["verdict"].value_counts().to_string())
    print(f"\nexcess_cagr: median={eq.excess_cagr.median():.4f}  "
          f"%>0={100*(eq.excess_cagr>0).mean():.0f}%   IR median={eq.info_ratio.median():.2f}")
    print(f"t_stat: %|t|>=2={100*(m.t_stat.abs()>=2).mean():.0f}%   ic_t median={eq.ic_t.median():.2f}")
    print("\nTOP 12 by t_stat (skill significance):")
    cols = ["scheme_name", "sebi_category", "n_months", "excess_cagr", "info_ratio", "t_stat", "ic_t", "verdict"]
    print(m.sort_values("t_stat", ascending=False).head(12)[cols].to_string(index=False))
    print("\nBOTTOM 6 by excess_cagr:")
    print(m.sort_values("excess_cagr").head(6)[cols].to_string(index=False))
