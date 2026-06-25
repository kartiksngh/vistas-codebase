"""
Quant analytics for Vistas — computed server-side so the terminal reuses the
project's EXACT formula conventions rather than re-deriving them in JavaScript.

PROVENANCE (every convention below is lifted verbatim from the research engine,
`strategy/fft_strategy_v1.py` / `strategy/backtest_excel_dump.py`):

  * Returns          : simple period returns `px.pct_change()` drive vol/Sharpe/
                       beta/capture (engine `nav.pct_change()`); the displayed
                       NAV is the actual total-return index level rebased to 100.
  * Weekly resample  : W-FRI, last price of the week  (engine load_data:145-147).
  * CAGR             : (last/first) ** (365 / calendar_days) - 1     (engine:626-631).
  * Annualization    : >=1Y windows -> CAGR via (1+r)**(ppy/w)-1; <1Y -> absolute
                       cumulative return  (KV units rule, 2026-06-15).
  * Volatility       : ret.std() * sqrt(252)                          (engine:827).
  * Sharpe           : (mean - rf_per) * ppy / (std * sqrt(ppy)), rf=0 default (engine:829).
  * Max drawdown     : (nav / nav.cummax() - 1).min()                 (engine:830).
  * Excess alpha     : CAGR(series) - CAGR(benchmark)                 (engine decomposition).
  * Capture (up/dn)  : sum(r_s | r_b>0) / sum(r_b | r_b>0)  (and <0)  (engine capture).
  * Tracking error   : (r_s - r_b).std() * sqrt(ppy)                  (engine:1081-1088).
  * Information ratio : (r_s - r_b).mean() * ppy / tracking_error.

ADDED for the terminal: Sortino, Calmar, Jensen's alpha + rolling beta,
calendar-year returns (+ %positive/%negative), monthly-return heatmap, and
rolling-horizon return / alpha DENSITY distributions (Gaussian KDE).

ROLLING SEEDING: rolling time-series (alpha/beta/vol/Sharpe/correlation/relative-
strength) are computed on an EXTENDED price series that includes a pre-window
buffer, then sliced to the display window, so they are populated from the window
start wherever prior history exists (not start + one rolling window). Drawdown
stays window-relative (peak resets at the window start). Distributions are
computed WITHIN the window only (a horizon longer than the window is omitted).

ppy = periods per year: 252 daily, 52 weekly. CAGR uses 365 calendar days.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

try:
    from scipy.stats import gaussian_kde
    _HAVE_KDE = True
except Exception:  # pragma: no cover
    _HAVE_KDE = False

PPY = {"daily": 252.0, "weekly": 52.0}

# A selected series whose IN-WINDOW observations contain a calendar gap larger than this is treated as
# non-continuous over that window and excluded from the comparison (see the windowed continuity gate in
# `analyze`). 90 days cleanly separates a catastrophic multi-year disconnect (dormant-then-relisted stock)
# from a normal long-weekend/holiday gap; it is a no-op for any continuous daily/weekly price series.
MAX_WINDOW_GAP_DAYS = 90

WINDOW_PERIODS = {
    "daily":  {"1M": 21, "3M": 63, "6M": 126, "1Y": 252, "2Y": 504, "3Y": 756, "5Y": 1260},
    "weekly": {"1M": 4,  "3M": 13, "6M": 26,  "1Y": 52,  "2Y": 104, "3Y": 156, "5Y": 260},
}

# Distribution horizons (label -> periods per frequency, annualize flag).
DIST_HORIZONS = {
    "daily":  [("Monthly", 21, False), ("Quarterly", 63, False), ("Yearly", 252, False), ("3Y CAGR", 756, True)],
    "weekly": [("Monthly", 4, False), ("Quarterly", 13, False), ("Yearly", 52, False), ("3Y CAGR", 156, True)],
}
_MIN_DENS_PTS = 12          # need at least this many overlapping windows to draw a density


# ----------------------------------------------------------------------------- helpers
def _no_inf(x):
    """Replace +/-inf with NaN so it is treated as MISSING. A return off a zero
    prior price is +inf in pandas and would survive .dropna() and poison
    mean/std/cov/corr; the JS port nulls that position, so we match by nulling it
    here too (a zero price is a data error either way)."""
    return x.replace([np.inf, -np.inf], np.nan)


def _clean(x):
    if x is None:
        return None
    if isinstance(x, (np.floating, float)):
        x = float(x)
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(x, (np.integer, int)):
        return int(x)
    return x


def _list(s: pd.Series):
    return [_clean(v) for v in s.to_numpy()]


def _resample(px: pd.DataFrame, freq: str) -> pd.DataFrame:
    return px.resample("W-FRI").last() if freq == "weekly" else px


def _cagr(level: pd.Series) -> float:
    s = level.dropna()
    if len(s) < 2:
        return float("nan")
    days = (s.index[-1] - s.index[0]).days
    if days <= 0 or s.iloc[0] <= 0:
        return float("nan")
    return (s.iloc[-1] / s.iloc[0]) ** (365.0 / days) - 1.0


# ----------------------------------------------------------------------------- per-series stats
def _stats_for(level: pd.Series, ret: pd.Series, ppy: float, rf_annual: float) -> dict:
    level = level.dropna()
    ret = ret.dropna()
    keys = ("total_return", "cagr", "vol", "sharpe", "sortino", "maxdd",
            "calmar", "best_1y", "worst_1y", "n_obs", "start", "end")
    if len(level) < 2 or len(ret) < 2:
        return {k: None for k in keys}
    rf_per = rf_annual / ppy
    mean, std = ret.mean(), ret.std()
    downside = np.sqrt(np.mean(np.minimum(ret - rf_per, 0.0) ** 2))
    nav = level
    maxdd = float((nav / nav.cummax() - 1.0).min())
    cagr = _cagr(nav)
    total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    vol = float(std * math.sqrt(ppy))
    sharpe = float((mean - rf_per) * ppy / (std * math.sqrt(ppy))) if std > 0 else float("nan")
    sortino = float((mean - rf_per) * ppy / (downside * math.sqrt(ppy))) if downside > 0 else float("nan")
    calmar = float(cagr / abs(maxdd)) if maxdd < 0 else float("nan")
    w1y = int(ppy)
    roll1y = _no_inf(nav / nav.shift(w1y) - 1.0)   # a lagged 0 price would give +inf
    best = float(roll1y.max()) if roll1y.notna().any() else float("nan")
    worst = float(roll1y.min()) if roll1y.notna().any() else float("nan")
    return {"total_return": _clean(total), "cagr": _clean(cagr), "vol": _clean(vol),
            "sharpe": _clean(sharpe), "sortino": _clean(sortino), "maxdd": _clean(maxdd),
            "calmar": _clean(calmar), "best_1y": _clean(best), "worst_1y": _clean(worst),
            "n_obs": int(len(ret)),
            "start": level.index[0].strftime("%Y-%m-%d"), "end": level.index[-1].strftime("%Y-%m-%d")}


def _pair_metrics(ret_s, ret_b, lvl_s, lvl_b, ppy, rf_annual) -> dict:
    j = pd.concat([ret_s, ret_b], axis=1, keys=["s", "b"]).dropna()
    out = {"alpha": None, "beta": None, "up_capture": None, "down_capture": None,
           "capture_ratio": None, "tracking_error": None, "info_ratio": None, "corr": None}
    out["alpha"] = _clean(_cagr(lvl_s) - _cagr(lvl_b))
    if len(j) < 2:
        return out
    rs, rb = j["s"], j["b"]
    var_b = rb.var()
    out["beta"] = _clean(rs.cov(rb) / var_b) if var_b > 0 else None
    up_b, dn_b = rb[rb > 0].sum(), rb[rb < 0].sum()
    out["up_capture"] = _clean(rs[rb > 0].sum() / up_b) if up_b != 0 else None
    out["down_capture"] = _clean(rs[rb < 0].sum() / dn_b) if dn_b != 0 else None
    if out["up_capture"] is not None and out["down_capture"] not in (None, 0):
        out["capture_ratio"] = _clean(out["up_capture"] / out["down_capture"])
    diff = rs - rb
    te = diff.std() * math.sqrt(ppy)
    out["tracking_error"] = _clean(te)
    out["info_ratio"] = _clean(diff.mean() * ppy / te) if te > 0 else None
    out["corr"] = _clean(rs.corr(rb))
    return out


# ----------------------------------------------------------------------------- rolling (seeded)
def _rolling(pxe, rete, pxw, widx, tickers, benchmarks, w, ppy, rf_annual, alpha_type) -> dict:
    """Rolling series computed on the EXTENDED frame `pxe`/`rete`, then sliced to
    the window mask `widx` so they are populated from the window start. Drawdown
    is window-relative (uses window-only prices `pxw`)."""
    rf_per = rf_annual / ppy
    # Mixed-calendar series (yfinance stocks vs NSE indices) miss a few non-overlapping
    # trading days a year, so the merged return frame carries scattered NaN. pandas rolling()
    # defaults to min_periods = w, so ONE NaN voids a whole window — which fragmented rolling
    # beta/vol/Sharpe/correlation (they only appeared in years with zero calendar gaps). Require
    # 80% of the window present instead: a stray gap no longer nulls the stat, while a window
    # genuinely short of data (true seeding edge) still stays blank.
    mp = max(2, int(round(w * 0.8)))
    out = {"alpha": {}, "beta": {}, "vol": {}, "sharpe": {}, "drawdown": {},
           "corr": {}, "relstrength": {}}

    def sl(s):  # full-index series -> window list
        return [_clean(v) for v in s[widx].to_numpy()]

    # single-series rolling stats for ALL selected series (so the benchmark's own
    # vol / Sharpe / drawdown are available — e.g. to draw benchmark drawdown).
    single_cols = list(dict.fromkeys(list(tickers) + list(benchmarks)))
    for t in single_cols:
        r = rete[t]
        out["vol"][t] = sl(r.rolling(w, min_periods=mp).std() * math.sqrt(ppy))
        rs_ann = (r - rf_per).rolling(w, min_periods=mp).mean() * ppy
        sd = r.rolling(w, min_periods=mp).std() * math.sqrt(ppy)
        out["sharpe"][t] = sl(rs_ann / sd.replace(0.0, np.nan))
        nav = pxw[t]                                    # window-relative underwater
        out["drawdown"][t] = _list(nav / nav.cummax() - 1.0)

    annualize_alpha = w >= ppy
    for t in tickers:
        for b in benchmarks:
            key = f"{t}|{b}"
            rs, rb = rete[t], rete[b]
            if alpha_type == "jensen":
                cov = rs.rolling(w, min_periods=mp).cov(rb)
                var = rb.rolling(w, min_periods=mp).var()
                beta = cov / var.replace(0.0, np.nan)
                a_per = (rs - rf_per).rolling(w, min_periods=mp).mean() - beta * (rb - rf_per).rolling(w, min_periods=mp).mean()
                out["alpha"][key] = sl(a_per * ppy)
                out["beta"][key] = sl(beta)
            else:
                tr_s = _no_inf(pxe[t] / pxe[t].shift(w) - 1.0)
                tr_b = _no_inf(pxe[b] / pxe[b].shift(w) - 1.0)
                if annualize_alpha:
                    tr_s = (1.0 + tr_s).where(1.0 + tr_s > 0) ** (ppy / w) - 1.0
                    tr_b = (1.0 + tr_b).where(1.0 + tr_b > 0) ** (ppy / w) - 1.0
                out["alpha"][key] = sl(tr_s - tr_b)
                cov = rs.rolling(w, min_periods=mp).cov(rb)
                var = rb.rolling(w, min_periods=mp).var()
                out["beta"][key] = sl(cov / var.replace(0.0, np.nan))
            out["corr"][key] = sl(rs.rolling(w, min_periods=mp).corr(rb))
            rstr = (pxe[t] / pxe[b])[widx]
            first = rstr.dropna()
            base = first.iloc[0] if len(first) else np.nan
            out["relstrength"][key] = [_clean(v) for v in (rstr / base * 100.0).to_numpy()] \
                if base and not math.isnan(base) else [_clean(v) for v in (rstr * np.nan).to_numpy()]
    out["alpha_annualized"] = bool(annualize_alpha or alpha_type == "jensen")
    return out


# ----------------------------------------------------------------------------- calendar / monthly / distributions
def _calendar_year(level: pd.DataFrame, tickers, benchmarks):
    cols = list(dict.fromkeys(list(tickers) + list(benchmarks)))
    ye = level[cols].resample("YE").last()
    start_row = level[cols].dropna(how="all").iloc[[0]]
    base = pd.concat([start_row, ye]).sort_index()
    base = base[~base.index.duplicated(keep="last")]
    rets = _no_inf(base.pct_change(fill_method=None)).dropna(how="all")
    years = [d.year for d in rets.index]
    partial_first = level.index[0].month != 1 or level.index[0].day > 5
    labels = [str(y) for y in years]
    if labels and partial_first:
        labels[0] = labels[0] + "*"
    series = {c: [_clean(v) for v in rets[c].to_numpy()] for c in cols}
    bench0 = benchmarks[0] if benchmarks else None
    alpha = {}
    if bench0 is not None:
        for t in tickers:
            # guard None (a late-inception index has no return in early years)
            alpha[f"{t}|{bench0}"] = [_clean(a - b) if (a is not None and b is not None) else None
                                      for a, b in zip(series[t], series[bench0])]

    def hit(arr):
        v = [x for x in arr if x is not None]
        n = len(v)
        if not n:
            return {"pos": None, "neg": None, "n": 0}
        return {"pos": sum(1 for x in v if x > 0) / n, "neg": sum(1 for x in v if x < 0) / n, "n": n}

    return {"years": labels, "series": series, "alpha": alpha, "primary_benchmark": bench0,
            "stats_return": {c: hit(series[c]) for c in cols},
            "stats_alpha": {k: hit(alpha[k]) for k in alpha}}


def _monthly_heatmap(level: pd.DataFrame, tickers):
    out = {}
    for t in tickers:
        m = _no_inf(level[t].resample("ME").last().pct_change(fill_method=None)).dropna()
        if m.empty:
            continue
        df = pd.DataFrame({"y": m.index.year, "m": m.index.month, "r": m.to_numpy()})
        piv = df.pivot_table(index="y", columns="m", values="r", aggfunc="last").reindex(columns=range(1, 13))
        out[t] = {"years": [int(y) for y in piv.index],
                  "z": [[_clean(v) for v in row] for row in piv.to_numpy()]}
    return out


def _density(vals: np.ndarray, n_grid: int = 120):
    """A smooth density curve (Gaussian KDE) for a 1-D sample, or None if too few
    points / no variance. Returns {x, y} on a grid spanning the sample range."""
    vals = vals[np.isfinite(vals)]
    if len(vals) < _MIN_DENS_PTS or np.std(vals) < 1e-9:
        return None
    lo, hi = float(np.min(vals)), float(np.max(vals))
    pad = (hi - lo) * 0.08 if hi > lo else abs(lo) * 0.1 + 1e-6
    grid = np.linspace(lo - pad, hi + pad, n_grid)
    try:
        if _HAVE_KDE:
            dens = gaussian_kde(vals)(grid)
        else:
            counts, edges = np.histogram(vals, bins=30, density=True)
            centers = (edges[:-1] + edges[1:]) / 2.0
            dens = np.interp(grid, centers, counts)
    except Exception:
        return None
    return {"x": [_clean(v) for v in grid], "y": [_clean(v) for v in dens],
            "mean": _clean(float(np.mean(vals))), "std": _clean(float(np.std(vals))),
            "n": int(len(vals))}


def _distributions(pxw, tickers, benchmarks, freq, ppy):
    """Rolling-horizon return AND alpha DENSITY curves (overlapping windows).
    A horizon longer than the window is omitted (only that chart)."""
    horizons = DIST_HORIZONS.get(freq, DIST_HORIZONS["daily"])
    cols = list(dict.fromkeys(list(tickers) + list(benchmarks)))
    bench0 = benchmarks[0] if benchmarks else None
    ret = {}        # {horizon_label: {series: {x,y}}}
    alp = {}        # {horizon_label: {ticker: {x,y}}}
    avail_ret, avail_alp = [], []
    n = len(pxw)
    for label, h, ann in horizons:
        if n - h < _MIN_DENS_PTS:        # window too short for this horizon
            continue
        # returns
        d = {}
        for c in cols:
            rr = _no_inf(pxw[c] / pxw[c].shift(h) - 1.0)
            if ann:
                rr = (1.0 + rr).where(1.0 + rr > 0) ** (ppy / h) - 1.0
            dens = _density(rr.dropna().to_numpy())
            if dens:
                d[c] = dens
        if d:
            ret[label] = d
            avail_ret.append(label)
        # alpha vs primary
        if bench0 is not None:
            da = {}
            for t in tickers:
                rs = _no_inf(pxw[t] / pxw[t].shift(h) - 1.0)
                rb = _no_inf(pxw[bench0] / pxw[bench0].shift(h) - 1.0)
                if ann:
                    rs = (1.0 + rs).where(1.0 + rs > 0) ** (ppy / h) - 1.0
                    rb = (1.0 + rb).where(1.0 + rb > 0) ** (ppy / h) - 1.0
                dens = _density((rs - rb).dropna().to_numpy())
                if dens:
                    da[t] = dens
            if da:
                alp[label] = da
                avail_alp.append(label)
    return {"return": ret, "alpha": alp, "horizons_return": avail_ret,
            "horizons_alpha": avail_alp, "primary_benchmark": bench0,
            "units": {lbl: ("cagr" if ann else "cumulative") for lbl, h, ann in horizons}}


# ----------------------------------------------------------------------------- entrypoint
def analyze(px_ext: pd.DataFrame, tickers, benchmarks, window_start=None, freq="daily",
            rolling_window="1Y", alpha_type="excess", rf_annual=0.0) -> dict:
    """Compute the full Vistas analytics bundle.

    px_ext       : DAILY price-level frame from (window_start - rolling buffer) to
                   end (so rolling series can be seeded). Window-only outputs are
                   sliced to >= window_start.
    window_start : the user's actual 'from' date (defaults to the first row).
    """
    freq = freq if freq in PPY else "daily"
    ppy = PPY[freq]
    tickers = [c for c in tickers if c in px_ext.columns]
    benchmarks = [c for c in benchmarks if c in px_ext.columns]
    all_cols = list(dict.fromkeys(tickers + benchmarks))
    if not all_cols:
        return {"error": "No valid series selected."}

    # WINDOWED CONTINUITY GATE (calendar-based): a series whose in-window history has a multi-month
    # hole is not a continuous daily series over THIS window — chaining CAGR / total return across the
    # hole fabricates a return (e.g. a stock that traded in 2001, went dormant ~20y, then relisted near
    # a very different price). Such a series is excluded from this window's analysis (never deleted from
    # the data) and reported in meta.excluded_noncontinuous; the survivors proceed. Calendar-day gaps are
    # used (not row gaps) so this fires correctly even when a single series is selected. A no-op for clean
    # daily/weekly series (max real gap is a long-weekend/holiday << MAX_WINDOW_GAP_DAYS).
    _present = px_ext[all_cols].dropna(how="all")
    ws_req = pd.Timestamp(window_start) if window_start else (
        _present.index.min() if len(_present) else px_ext.index.min())
    excluded_noncont = []
    for c in all_cols:
        s = px_ext[c]
        s = s.loc[s.index >= ws_req].dropna()
        if len(s) >= 2 and s.index.to_series().diff().dt.days.max() > MAX_WINDOW_GAP_DAYS:
            excluded_noncont.append(c)
    if excluded_noncont:
        ex = set(excluded_noncont)
        tickers = [c for c in tickers if c not in ex]
        benchmarks = [c for c in benchmarks if c not in ex]
        all_cols = [c for c in all_cols if c not in ex]
    if not all_cols:
        return {"error": "The selected series are non-continuous over this window (a multi-month "
                         "trading gap) — pick a shorter, continuous window.",
                "excluded_noncontinuous": excluded_noncont}

    pxe = _resample(px_ext[all_cols], freq).dropna(how="all")
    rete = _no_inf(pxe.pct_change(fill_method=None))
    inwin = pxe.loc[pxe.index >= ws_req]
    if inwin.empty:
        return {"error": "No data in the selected window."}
    # FAIR cross-series comparison: anchor the comparison at the LATEST date on which
    # EVERY selected series has data (the common overlap), within the requested
    # window. Comparing series rebased to 100 at DIFFERENT inception dates is
    # apples-to-oranges — a series that began at an earlier market bottom carries
    # that head-start inside its base=100 and looks like a fake outlier. Starting at
    # the common date makes the chart, total return, CAGR and alpha all like-for-like.
    common = inwin.dropna(how="any")
    if common.empty:
        return {"error": "The selected series have no overlapping dates in this window."}
    ws = common.index[0]
    widx = pxe.index >= ws
    pxw = pxe.loc[widx]
    retw = _no_inf(pxw.pct_change(fill_method=None))
    dates = [d.strftime("%Y-%m-%d") for d in pxw.index]

    levels, raw = {}, {}
    for c in all_cols:
        s = pxw[c]
        f = s.dropna()
        base = f.iloc[0] if len(f) else np.nan
        levels[c] = _list(s / base * 100.0) if base and not math.isnan(base) else _list(s * np.nan)
        raw[c] = _list(s)

    bench0 = benchmarks[0] if benchmarks else None
    stats = []
    for c in all_cols:
        row = {"name": c, "is_benchmark": c in benchmarks and c not in tickers}
        row.update(_stats_for(pxw[c], retw[c], ppy, rf_annual))
        # alpha is now a fair same-period excess CAGR: pxw spans the common window,
        # so both CAGRs are measured over the identical [common_start, end] span.
        row["alpha_vs_primary"] = _clean(_cagr(pxw[c].dropna()) - _cagr(pxw[bench0].dropna())) \
            if (bench0 is not None and c != bench0) else None
        # the series' TRUE first available date (may predate the comparison start)
        incept = px_ext[c].dropna()
        row["inception"] = incept.index[0].strftime("%Y-%m-%d") if len(incept) else None
        stats.append(row)

    pairs = []
    for t in tickers:
        for b in benchmarks:
            m = _pair_metrics(retw[t], retw[b], pxw[t], pxw[b], ppy, rf_annual)
            m.update({"name": t, "benchmark": b})
            pairs.append(m)

    w = WINDOW_PERIODS[freq].get(rolling_window, WINDOW_PERIODS[freq]["1Y"])
    rolling = _rolling(pxe, rete, pxw, widx, tickers, benchmarks, w, ppy, rf_annual, alpha_type)

    # pairwise return-correlation matrix across ALL selected series (window, freq)
    cm = retw[all_cols].corr()
    corr_matrix = {"labels": all_cols,
                   "z": [[_clean(v) for v in cm.loc[r, all_cols].to_numpy()] for r in all_cols]}

    return {
        "meta": {"freq": freq, "ppy": ppy, "rolling_window": rolling_window,
                 "rolling_periods": int(w), "alpha_type": alpha_type, "rf_annual": rf_annual,
                 "tickers": tickers, "benchmarks": benchmarks,
                 "start": dates[0] if dates else None, "end": dates[-1] if dates else None,
                 "n_obs": len(dates),
                 "requested_start": ws_req.strftime("%Y-%m-%d"),
                 "common_start": ws.strftime("%Y-%m-%d"),
                 "truncated": bool(ws > ws_req),
                 "excluded_noncontinuous": excluded_noncont},
        "dates": dates, "levels": levels, "raw_levels": raw, "stats": stats, "pairs": pairs,
        "rolling": rolling,
        "calendar_year": _calendar_year(pxw, tickers, benchmarks) if tickers
                         else _calendar_year(pxw, all_cols, []),
        "monthly": _monthly_heatmap(pxw, all_cols),
        "distribution": _distributions(pxw, tickers, benchmarks, freq, ppy),
        "corr_matrix": corr_matrix,
    }


# ----------------------------------------------------------------------------- VALUATION (ratio/yield)
# A valuation ratio (P/E, P/B) or yield (Div Yield) is NOT a compounding wealth level,
# so CAGR / Sharpe / drawdown are meaningless on it. The right reads are: the actual
# level over time, where TODAY sits in its OWN history (percentile + z-score = the
# cheap/rich gauge), mean +/- sigma bands, a cross-index "who's cheapest now" snapshot,
# and the spread vs a primary benchmark. NO rebasing — the number itself is the info.
#
# CONVENTION (audit me, parity != correctness): percentile/z are each series vs ITS OWN
# window history (NIFTY IT P/E judged against NIFTY IT's range, not the panel's range) —
# that is the meaningful "is this expensive for THIS index" question. Window = the user's
# selected window (honest: "within this window"). No common-overlap truncation is needed
# because nothing is rebased, so a longer-history series carries no unfair head start.
def _percentile_of(vals: np.ndarray, x: float) -> float:
    """Percentile RANK of x within vals = fraction of observations <= x, in %."""
    v = vals[np.isfinite(vals)]
    if len(v) == 0 or not np.isfinite(x):
        return float("nan")
    return float((v <= x).sum()) / len(v) * 100.0


def _cheap_rich(pct, kind: str) -> str:
    """Label the current percentile: ratios (P/E,P/B) -> high % = 'rich'; a yield
    (Div Yield) inverts (high % = 'cheap'). Bands at 20/80."""
    if pct is None or (isinstance(pct, float) and math.isnan(pct)):
        return "—"
    hi, lo = ("cheap", "rich") if kind == "yield" else ("rich", "cheap")
    if pct >= 80:
        return hi
    if pct <= 20:
        return lo
    return "mid"


def valuation_analyze(pxv: pd.DataFrame, tickers, benchmarks, measure="PE", kind="ratio",
                      window_start=None, freq="daily") -> dict:
    """Valuation bundle for one ratio/yield `measure`. pxv = daily frame of that measure
    for the selected series. Returns: dates + raw level series (NOT rebased), per-series
    stats (current/mean/median/std/min/max/zscore/percentile/cheap_rich), mean+/-sigma
    bands, a current cross-section ranked by value, the spread vs the primary benchmark,
    and a KDE distribution of the window values."""
    freq = freq if freq in PPY else "daily"
    tickers = [c for c in tickers if c in pxv.columns]
    benchmarks = [c for c in benchmarks if c in pxv.columns]
    all_cols = list(dict.fromkeys(tickers + benchmarks))
    if not all_cols:
        return {"error": f"No selected series has {measure} data."}
    pv = _resample(pxv[all_cols], freq).dropna(how="all")
    ws_req = pd.Timestamp(window_start) if window_start else (pv.index[0] if len(pv) else None)
    if ws_req is None:
        return {"error": f"No {measure} data."}
    pvw = pv.loc[pv.index >= ws_req]
    if pvw.empty:
        return {"error": f"No {measure} data in the selected window."}
    dates = [d.strftime("%Y-%m-%d") for d in pvw.index]
    series = {c: _list(pvw[c]) for c in all_cols}
    bench0 = benchmarks[0] if benchmarks else None

    stats, bands, xsec, dist = [], {}, [], {}
    for c in all_cols:
        s = pvw[c].dropna()
        if len(s) < 1:
            stats.append({"name": c, "current": None, "mean": None, "median": None,
                          "std": None, "min": None, "max": None, "zscore": None,
                          "percentile": None, "cheap_rich": "—", "n_obs": 0,
                          "start": None, "end": None})
            continue
        arr = s.to_numpy(dtype=float)
        cur = float(arr[-1])
        mean = float(np.mean(arr))
        sd = float(np.std(arr, ddof=1)) if len(arr) > 1 else float("nan")
        pctile = _percentile_of(arr, cur)
        # MIN-HISTORY GUARD (safety suppression, not a value change): with too few observations a
        # percentile is fabricated (e.g. 100 = "richest ever" off 2-3 points). Below 8 obs, suppress
        # the percentile and the cheap/rich label so the UI shows "n/a — too little history".
        if len(arr) < 8:
            pctile = float("nan")
        z = (cur - mean) / sd if (sd and sd > 0) else float("nan")
        cr = _cheap_rich(pctile, kind)
        stats.append({"name": c, "current": _clean(cur), "mean": _clean(mean),
                      "median": _clean(float(np.median(arr))), "std": _clean(sd),
                      "min": _clean(float(np.min(arr))), "max": _clean(float(np.max(arr))),
                      "zscore": _clean(z), "percentile": _clean(pctile), "cheap_rich": cr,
                      "n_obs": int(len(arr)),
                      "start": s.index[0].strftime("%Y-%m-%d"),
                      "end": s.index[-1].strftime("%Y-%m-%d")})
        bands[c] = {"mean": _clean(mean), "sd1_lo": _clean(mean - sd), "sd1_hi": _clean(mean + sd),
                    "sd2_lo": _clean(mean - 2 * sd), "sd2_hi": _clean(mean + 2 * sd)}
        xsec.append({"name": c, "value": _clean(cur), "percentile": _clean(pctile),
                     "zscore": _clean(z), "cheap_rich": cr,
                     "date": s.index[-1].strftime("%Y-%m-%d"), "is_benchmark": c in benchmarks and c not in tickers})
        d = _density(arr)
        if d:
            dist[c] = d
    # cross-section ranked most-expensive-first (for a yield, highest yield = cheapest
    # first); name as a deterministic tiebreak so the order is reproducible (parity).
    xsec.sort(key=lambda r: (r["value"] is None,
                             -(r["value"] if r["value"] is not None else 0.0), r["name"]))

    spread = None
    if bench0 is not None:
        sp_series, sp_stats = {}, {}
        b = pvw[bench0]
        for t in tickers:
            diff = pvw[t] - b
            sp_series[t] = _list(diff)
            dd = diff.dropna()
            if len(dd):
                a = dd.to_numpy(dtype=float)
                sp_stats[t] = {"current": _clean(float(a[-1])), "mean": _clean(float(np.mean(a))),
                               "percentile": _clean(_percentile_of(a, float(a[-1])))}
            else:
                sp_stats[t] = {"current": None, "mean": None, "percentile": None}
        spread = {"primary": bench0, "series": sp_series, "stats": sp_stats}

    return {
        "meta": {"measure": measure, "kind": kind, "freq": freq, "tickers": tickers,
                 "benchmarks": benchmarks, "primary_benchmark": bench0,
                 "start": dates[0] if dates else None, "end": dates[-1] if dates else None,
                 "n_obs": len(dates), "requested_start": ws_req.strftime("%Y-%m-%d")},
        "dates": dates, "series": series, "stats": stats, "bands": bands,
        "cross_section": {"rows": xsec}, "spread": spread, "distribution": dist,
    }
