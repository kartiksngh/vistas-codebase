"""
skill_signals.py — Component A: high-breadth, bet-level skill signals (spec Component A, L13-213).

WHY THIS EXISTS (one paragraph, first principles)
-------------------------------------------------
The live verdict tests the NAV-level active return with t = IR·√years. The NAV collapses ~47
positions into ONE number per month, so its breadth is ~1-3 independent bets/yr → significance
needs 15-25 years. The fix is to measure skill where the breadth actually lives: the CROSS-SECTION
of holdings and the TRADES between snapshots (hundreds of bets/yr). The same Fundamental Law that
made NAV slow (t = IC·√(BR·years)) becomes a gift at high breadth — a real edge declares in ~1-2y,
and an NFO gets an honest (wide-error-bar) early read.

THE UNIFYING IDENTITY (every signal is a slice of this accounting identity, not a model):
    A = R_p − R_b = Σ_i a_i·r_i,   a_i ≡ w_i − W_i   (active weight; Σ a_i = 0)
We have w_i (renormalised equity weight, load_panel L106-107) and r_i (fwd TOTAL return,
tr_returns_monthly.parquet). We do NOT have point-in-time index weights W_i — the single biggest
honest gap (W-HIST). Every signal below stops collapsing the Σ into one number and scores the
PER-NAME terms directly, against forward total return, via a Fama-MacBeth t over any [start,end].

THE THREE SIGNALS (ranked by breadth / convergence speed):
  1. TRADE-LEVEL ALPHA  (breadth ~80-150/yr, converges ~1-2y) — THE fast signal. Built on the
     drift-adjusted active trade dw_active (= funds_flows net_active, weight-space, inflow-immune,
     CA-bridged, already computed in _pair_flows_active L224-229). Two scorings vs forward return:
       (A) Add-vs-Trim spread (event study, the legible headline) — conviction-weighted fwd-return
           spread of names ADDED (dw_active>+τ) minus names TRIMMED (dw_active<−τ), for k=1/3/6/12;
       (B) IC-of-trades (continuous, full breadth) — Spearman(dw_active_i, r_{i,t→t+k}) per month.
     ★ The TRIM leg is LONG-ONLY-TRUNCATED (the transfer-coefficient leak, TC≈0.3-0.6): a manager
       cannot fully express a sell view → trade-alpha is a LOWER BOUND on true forecasting skill.
       Report the asymmetry; do NOT "fix" it (real economics, not a bug).
  2. HOLDING-RANK IC  (breadth ~60-70/yr, ~1.5-2.5y) — the well-understood backbone. Spearman of
     weight-rank vs fwd-return-rank, monthly, Fama-MacBeth t. As-coded (_ic, L122-127) it correlates
     TOTAL weight w → CAP-TILT-CONTAMINATED (the code flags this, L254/292-293). Cleaning ladder:
       route 1 = fund-demean (weak), route 2 = PEER-CONSENSUS demean (â=w−Ŵ, Ŵ = ex-self AUM-weighted
       consensus, already built in funds_flows.build_active_share "cons=exagg/extotal" L699) ← SHIP
       THIS as the default cleaned IC, route 3 = true W_i (needs W-HIST, not available).
  3. STOCK BATTING & SLUGGING  (breadth ~60-70/yr, ~2-3y) — the legible companion (port_hit_* live,
     L142-168). Two flaws to fix: (i) WRONG NULL — the no-skill batting baseline is ~0.46-0.49 (the
     median stock lags a cap-weighted index), NOT 0.50 → report batting vs the EMPIRICAL bootstrap
     null, not 50%; (ii) SLUG HAS LOOK-AHEAD by construction (quartiles cut from the very returns
     scored, L151) → retire slug as a SIGNAL, keep as a labelled ex-post DIAGNOSTIC (never in the
     posterior).

WINDOWING CONTRACT (spec §5, L174-183): every signal is a monthly series X_t (an IC_t, an AvT_t(k),
a batting rate). To window to a tenure [start,end]: (1) slice X_t to the window (a FILTER, not a
re-fetch — the per-fund ts[] already carries "ic", flows carry net_active per month); (2) X̄=mean_t,
s=std_t, T=#months; (3) Fama-MacBeth t = X̄/(s/√T); cross-check vs t=IC·√(BR_eff·years); (4)
Newey-West the SE (lag ≈ k-month overlap) so overlapping-return autocorrelation doesn't inflate t.

DISPLAY-PLANE only — additive, no analytics.py touch, NO JS-parity port here.

--------------------------------------------------------------------------------------------------
SHARED DATA CONTRACT — the per-fund SIGNAL TUPLE (locked in SKILL_ENGINE_BUILD.md)
--------------------------------------------------------------------------------------------------
Every signal function returns a `signal` dict with EXACTLY these keys (the std skill-axis input that
Component B's posterior consumes; one tuple per signal per fund):
    {
      "name":         str,    # "trade_ic" | "holding_ic_cons" | "add_minus_trim" | "batting" | ...
      "x_hat":        float,  # the point estimate on the signal's native axis (an IC, a spread, a rate)
      "se":           float,  # its standard error — Newey-West HAC / block-bootstrap, NOT naive 1/√T
      "T":            int,    # number of monthly cross-sections in the window (effective sample, months)
      "n_bets_eff":   float,  # EFFECTIVE breadth = N/(1+(N-1)ρ̄) × periods   (independent bets, NOT N×T)
      "n_bets_naive": int,    # the raw bet count N×T (shown for honesty: how much breadth correlation ate)
      "rho_bar":      float,  # avg pairwise active-bet correlation used to deflate breadth
      "fm_t":         float,  # the Fama-MacBeth t-stat = x_hat/(std_t/√T)  on the RAW monthly series
      "route":        str,    # provenance: "peer-consensus-demeaned" | "raw-cap-tilt" | "dw_active" | ...
      "caveats":      list[str],  # honest gaps: ["cap-tilt-contaminated (no W-HIST)","trim-leg long-only
                                  #  lower-bound","slug=look-ahead diagnostic-only","scheme-level", ...]
    }
The per-month series (so Component B / the validator can re-window) is returned ALONGSIDE the tuple
as `series` = {"ym":[...], "x":[...]} when the caller asks (return_series=True).
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

# Trade thresholds / forward horizons (locked; mirrored in SKILL_ENGINE_BUILD.md)
TRADE_TAU = 0.002        # noise floor τ for ADD/TRIM classification (0.2% of book; _TOL-style)
FWD_HORIZONS = (1, 3, 6, 12)   # k-month forward windows for the trade-alpha term structure
HEADLINE_K = 3           # the headline forward horizon for the add-minus-trim / trade-IC tuples
NW_LAG_K = 3             # Newey-West lag ≈ the k-month forward overlap
BATTING_NULL_BOOT = 10000   # bootstrap draws for the empirical batting null (~0.46-0.49 baseline)
RHO_BAR_DEFAULT = 0.15   # fallback avg pairwise active-bet correlation when a residual-cov est is absent

_MIN_X_OBS = 5           # min covered names in a month for a cross-sectional stat to be defined
_MIN_T = 6               # min months for a windowed Fama-MacBeth t / a defined signal


# ==================================================================================================
# Low-level statistics (grounded in funds_attribution conventions; self-contained, no parity port)
# ==================================================================================================
def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation — EXACTLY funds_attribution._ic's rank().corr(rank()) convention
    (Pearson of ranks). NaN-robust on the paired finite subset; NaN if <_MIN_X_OBS or no variance."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < _MIN_X_OBS or len(np.unique(y)) < 3 or len(np.unique(x)) < 2:
        return np.nan
    rx = pd.Series(x).rank().values
    ry = pd.Series(y).rank().values
    if np.std(rx) == 0 or np.std(ry) == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def _newey_west_se(x: np.ndarray, lag: int) -> float:
    """Newey-West HAC standard error of the MEAN of a monthly series x_t (spec §5.4). The naive
    SE = s/√T assumes the X_t are independent; overlapping k-month forward returns make adjacent
    X_t autocorrelated, which inflates a naive t up to ~√(1+2·k) ≈ ×2-4. NW down-weights that.

    Var_HAC(mean) = (1/T²)·[ γ0 + 2·Σ_{j=1..L} (1 − j/(L+1))·γj ],  γj = autocovariance at lag j
    (Bartlett kernel). Returns √Var_HAC. Floors at the iid SE so HAC can only ever WIDEN (lower t) —
    never tighten it (honesty: a rail may only lower skill). lag=0 ⇒ the plain iid SE."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    T = len(x)
    if T < _MIN_T:
        return np.nan
    xc = x - x.mean()
    g0 = float(np.dot(xc, xc) / T)                      # γ0 (biased, /T — standard NW)
    s = g0
    L = max(0, int(lag))
    L = min(L, T - 1)
    for j in range(1, L + 1):
        gj = float(np.dot(xc[j:], xc[:-j]) / T)
        s += 2.0 * (1.0 - j / (L + 1.0)) * gj
    s = max(s, 0.0)                                     # truncate a negative HAC variance to 0
    var_mean = s / T
    iid_var = float(np.var(x, ddof=1)) / T              # the plain s²/T
    var_mean = max(var_mean, iid_var)                  # HAC may only WIDEN the band, never tighten
    return float(math.sqrt(var_mean)) if var_mean > 0 else np.nan


def _block_bootstrap_mean_se(a: np.ndarray, n_boot: int = 2000, block: int = 3) -> tuple:
    """CIRCULAR block-bootstrap SE + percentile-that-mean>null — mirrors
    funds_attribution._block_bootstrap_mean (same circular wrap, same stable seed) but ALSO returns
    the bootstrap SE (std of resample means), which is the autocorrelation-honest SE the batting
    signal needs (NOT 1/√T). Returns (se, p_pos, lo, hi)."""
    a = np.asarray(a, float)
    a = a[~np.isnan(a)]
    n = len(a)
    if n < _MIN_T:
        return (np.nan, np.nan, np.nan, np.nan)
    nb = int(math.ceil(n / block))
    rng = np.random.default_rng(1234567 + n)            # reproducible across builds (matches the live fn)
    means = np.empty(n_boot)
    starts = rng.integers(0, n, size=(n_boot, nb))
    offs = np.arange(block)
    for b in range(n_boot):
        idx = ((starts[b][:, None] + offs).ravel()[:n]) % n
        means[b] = a[idx].mean()
    se = float(np.std(means, ddof=1))
    p_pos = float((means > 0).mean())
    lo, hi = (float(v) for v in np.percentile(means, [2.5, 97.5]))
    return (se, p_pos, lo, hi)


def _fama_macbeth(x_t: np.ndarray) -> tuple:
    """Fama-MacBeth reduction of a monthly cross-sectional statistic series X_t → (X̄, s, T, fm_t).
    fm_t = X̄ / (s/√T) on the RAW series (the spec's exact t, mirroring scheme_metrics ic_t L217).
    This is the t BEFORE the Newey-West HAC correction (which is applied to the SE separately)."""
    x = np.asarray(x_t, float)
    x = x[np.isfinite(x)]
    T = len(x)
    if T < _MIN_T:
        return (np.nan, np.nan, T, np.nan)
    xbar = float(np.mean(x))
    s = float(np.std(x, ddof=1))
    fm_t = (xbar / (s / math.sqrt(T))) if s > 0 else np.nan
    return (xbar, s, T, fm_t)


def _slice_window(ym: list, start: str | None, end: str | None) -> np.ndarray:
    """Boolean mask selecting ym ∈ [start,end] (string YYYY-MM compares lexicographically = chrono)."""
    ym = np.asarray(ym, dtype=object)
    m = np.ones(len(ym), dtype=bool)
    if start is not None:
        m &= ym >= start
    if end is not None:
        m &= ym <= end
    return m


# ==================================================================================================
# Effective breadth (FUNDAMENTAL_LAW §5)
# ==================================================================================================
def effective_breadth(n_avg: float, months: int, rho_bar: float = RHO_BAR_DEFAULT) -> float:
    """BR_eff = (N / (1 + (N-1)·ρ̄)) per cross-section × months — the REAL independent bets (held
    names share beta/sectors so they are not independent). 47 names at ρ̄≈0.15 → ~6/month ≈ 70/yr,
    vs ~2/yr for the NAV. This is why the holdings/trades converge ~10× faster than the NAV-IR.
    Use the UPPER ρ̄ estimate (→ lower breadth → wider, more honest bars)."""
    n = float(n_avg)
    if not np.isfinite(n) or n <= 0 or months <= 0:
        return 0.0
    rho = float(rho_bar)
    per_month = n / (1.0 + max(n - 1.0, 0.0) * rho)     # de-correlated bets per cross-section
    return float(per_month * months)


def _breadth_block(n_avg: float, months: int, rho_bar: float = RHO_BAR_DEFAULT) -> dict:
    """Assemble the breadth fields for a signal tuple from the average cross-section size."""
    n = float(n_avg) if np.isfinite(n_avg) else 0.0
    return {
        "n_bets_eff": effective_breadth(n, months, rho_bar),
        "n_bets_naive": int(round(n * months)),
        "rho_bar": float(rho_bar),
    }


def _tuple(name, x_hat, se, T, n_avg, months, fm_t, route, caveats, rho_bar=RHO_BAR_DEFAULT):
    """Pack the LOCKED signal-tuple shape (SKILL_ENGINE_BUILD.md §2a). One place so every signal's
    output is byte-identical in structure. Non-finite floats are passed through as float('nan')
    (Component B / the engine clean to None at the JSON boundary, matching funds_attribution._clean_nan)."""
    br = _breadth_block(n_avg, months, rho_bar)
    return {
        "name": name,
        "x_hat": float(x_hat) if x_hat is not None and np.isfinite(x_hat) else float("nan"),
        "se": float(se) if se is not None and np.isfinite(se) else float("nan"),
        "T": int(T),
        "n_bets_eff": br["n_bets_eff"],
        "n_bets_naive": br["n_bets_naive"],
        "rho_bar": br["rho_bar"],
        "fm_t": float(fm_t) if fm_t is not None and np.isfinite(fm_t) else float("nan"),
        "route": route,
        "caveats": list(caveats),
    }


# ==================================================================================================
# Forward-return helpers (built from the SAME tr_returns_monthly substrate the live code uses)
# ==================================================================================================
def build_fwd_returns(tr: pd.DataFrame | None = None, months: list | None = None,
                      horizons=FWD_HORIZONS) -> dict:
    """Per (vst_id, ym) compounded FORWARD total return over k months, for each k in `horizons`.

    fwd_k(i, t) = Π_{j=1..k} (1 + r_i(t+j)) − 1   (the return from the END of month t over the next
    k months — exactly the t→t+1 convention funds_attribution uses for k=1, generalised to k).

    Returns {k: {(vst_id, ym): fwd_ret}}. `ym` is the DECISION month (the month whose weights/trades
    we score), so a holding/trade decided at month t is scored against the return it earns over t→t+k.
    Uses the project's winsorised monthly TR (clip -0.80..3.0, matching funds_attribution._RET_CLIP)."""
    import os
    if tr is None:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        TRP = os.path.join(_root, "data", "funds", "history", "tr_returns_monthly.parquet")
        tr = pd.read_parquet(TRP, columns=["vst_id", "date", "ret_1m"])
        tr["ym"] = pd.to_datetime(tr["date"]).dt.strftime("%Y-%m")
    tr = tr[tr["vst_id"].notna()].copy()
    if "ym" not in tr.columns:
        tr["ym"] = pd.to_datetime(tr["date"]).dt.strftime("%Y-%m")
    tr["ret_1m"] = pd.to_numeric(tr["ret_1m"], errors="coerce").clip(-0.80, 3.0)
    # the global month grid (so "t+j" is the j-th NEXT calendar month present in the data)
    allm = sorted(set(tr["ym"]))
    pos = {m: i for i, m in enumerate(allm)}
    r1 = tr.dropna(subset=["ret_1m"]).set_index(["vst_id", "ym"])["ret_1m"]
    r1 = r1[~r1.index.duplicated(keep="last")]
    # pivot to (vst_id × ym) for vectorised forward compounding
    rmat = r1.unstack("ym").reindex(columns=allm)        # rows = vst_id, cols = ym (ascending)
    out = {}
    for k in horizons:
        # forward k-month gross = Π_{j=1..k}(1+r_{t+j}); shift columns left by j and multiply
        gross = None
        for j in range(1, k + 1):
            shifted = rmat.shift(-j, axis=1) + 1.0       # (1 + r at t+j) stamped on month t
            gross = shifted if gross is None else gross * shifted
        fwd = gross - 1.0
        # to a dict keyed by (vst_id, decision-ym)
        s = fwd.stack(dropna=True)
        out[k] = {(vid, ym): float(v) for (vid, ym), v in s.items()}
    return out


# ==================================================================================================
# Peer-consensus weights Ŵ (route-2 cleaner) — the EXACT cons=exagg/extotal recipe, per (ym, fund)
# ==================================================================================================
def build_consensus_by_ym(category: str, h: pd.DataFrame | None = None,
                         start: str | None = None, end: str | None = None) -> dict:
    """Ex-self, AUM-weighted PEER-CONSENSUS weight Ŵ_i^f per (ym, fund, stock) for ONE SEBI category
    — the route-2 cleaner for the holding-IC (spec §1.3.2). This is funds_flows.build_active_share's
    `cons = exagg / extotal` line (L698-699) computed for EVERY month, not just the latest:

        exagg_i^f = Σ_{peers≠f} mv_i,peer   (= category aggregate − the fund's own rupees in i)
        extotal^f = Σ_i exagg_i^f           (= category total − the fund's own equity book)
        Ŵ_i^f     = exagg_i^f / extotal^f   (ex-self peer consensus weight; Σ_i Ŵ_i^f ≈ 1)

    The fund's CLEANED active weight is then â_i = w_i − Ŵ_i^f, which strips the part of the book
    every category peer also holds (≈ most of the passive cap tilt). Returns
    {ym: {navindia_code(str): {vst_id: Ŵ}}}. Active equity peers only (drops index/ETF). HONEST
    CAVEAT: peer consensus ≠ true index (it is itself active), so it slightly OVER-strips → the
    cleaned IC is a (mild) lower bound on the true active-weight IC."""
    import os
    if h is None:
        from . import funds_flows as ff
        h, _ = ff._load()                                # active-equity store with is_passive flag
    else:
        h = h.copy()
        if "is_passive" not in h.columns:
            h["is_passive"] = False
    h = h[~h["is_passive"]] if "is_passive" in h.columns else h
    h = h[(h["sebi_category"].astype(str) == str(category)) & h["vst_id"].notna()].copy()
    if start is not None:
        h = h[h["ym"] >= start]
    if end is not None:
        h = h[h["ym"] <= end]
    out = {}
    for ym, sub in h.groupby("ym"):
        # category aggregate rupees per stock (incl. self), and each fund's own rupees per stock
        agg = sub.groupby("vst_id")["market_value"].sum()
        total = float(agg.sum())
        if total <= 0:
            continue
        cons_ym = {}
        for f, ff_g in sub.groupby("navindia_code"):
            ff_s = ff_g.groupby("vst_id")["market_value"].sum()      # this fund's rupees per stock
            af = float(ff_s.sum())
            extotal = total - af
            if af <= 0 or extotal <= 0:
                continue
            exagg = agg.sub(ff_s, fill_value=0.0).clip(lower=0.0)    # ex-self category rupees per stock
            cons = (exagg / extotal)
            cons = cons[cons > 0]
            cons_ym[str(f)] = {str(v): float(w) for v, w in cons.items()}
        if cons_ym:
            out[ym] = cons_ym
    return out


# ==================================================================================================
# Fund-panel helper — the per (ym, vst_id, w, fwd_ret) cross-section for ONE fund
# ==================================================================================================
def fund_holding_panel(code, h: pd.DataFrame | None = None,
                      fwd1: dict | None = None) -> pd.DataFrame:
    """The per-(ym, vst_id) held cross-section for ONE fund — renormalised equity weight w (over the
    fund's equity sleeve, exactly load_panel L106-107) + the 1-month forward total return. This is the
    substrate the holding-IC and batting signals score. Folds re-code splits via scheme_identity so a
    re-coded fund is ONE series (matches funds_attribution L82-83). Columns: ym, vst_id, w, fwd_ret."""
    import os
    if h is None:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        HOLD = os.path.join(_root, "data", "funds", "history", "holdings_history.parquet")
        h = pd.read_parquet(HOLD, columns=["navindia_code", "sebi_category", "ym",
                                           "investment_type", "vst_id", "pct"])
        _it = h["investment_type"].astype(str).str.lower()
        h = h[_it.str.contains("equity", na=False) & ~_it.str.contains("foreign", na=False)].copy()
    try:
        from . import scheme_identity as _sid
        h = h.copy()
        h["navindia_code"] = h["navindia_code"].map(_sid.canonical_code)
        code = _sid.canonical_code(code)
    except Exception:
        pass
    d = h[(h["navindia_code"].astype(str) == str(code)) & h["vst_id"].notna()].copy()
    d["pct"] = pd.to_numeric(d["pct"], errors="coerce")
    d = d[d["pct"] > 0]
    if d.empty:
        return pd.DataFrame(columns=["ym", "vst_id", "w", "fwd_ret"])
    d["wsum"] = d.groupby("ym")["pct"].transform("sum")
    d["w"] = d["pct"] / d["wsum"]
    if fwd1 is None:
        fwd1 = build_fwd_returns(horizons=(1,))[1]
    d["fwd_ret"] = [fwd1.get((str(v), ym)) for v, ym in zip(d["vst_id"].astype(str), d["ym"])]
    return d[["ym", "vst_id", "w", "fwd_ret"]].copy()


# ==================================================================================================
# SIGNAL 2 — holding-rank IC, cleaned (peer-consensus route 2)
# ==================================================================================================
def holding_ic_signal(panel_fund, consensus_by_ym=None, route: str = "peer-consensus",
                      start: str | None = None, end: str | None = None,
                      return_series: bool = False, code: str | None = None,
                      rho_bar: float = RHO_BAR_DEFAULT) -> dict:
    """SIGNAL 2 — holding-rank IC, cleaned (spec §1, L40-83).

    route="peer-consensus" (DEFAULT, the cleaned IC): â_i = w_i − Ŵ_i where Ŵ_i is the ex-self
        AUM-weighted peer consensus (funds_flows.build_active_share "cons" line, passed in via
        `consensus_by_ym` = {ym: {vst_id: Ŵ}}); IC_t = Spearman(â_i, r_{i,t→t+1}); Fama-MacBeth t.
        Strips most of the passive cap tilt → caveat "peer consensus ≠ true index (slightly over-strips)".
    route="raw-cap-tilt": the as-coded Spearman(w, r) (funds_attribution._ic) — kept for back-compare;
        caveat "cap-tilt-contaminated (needs point-in-time benchmark weights W-HIST)".
    Windowable to [start,end] (a filter on the monthly IC series). Returns the locked signal tuple.

    `panel_fund` = the fund_holding_panel DataFrame (cols ym, vst_id, w, fwd_ret).
    `consensus_by_ym` may be the WHOLE-category map {ym:{navindia_code:{vst_id:Ŵ}}} (then pass
    `code`), OR the fund-specific {ym:{vst_id:Ŵ}} map. Required for route="peer-consensus"."""
    d = panel_fund
    if d is None or len(d) == 0:
        return _tuple("holding_ic_cons", np.nan, np.nan, 0, 0, 0, np.nan,
                      "peer-consensus-demeaned", ["no holdings panel"], rho_bar)
    mask = _slice_window(d["ym"].values, start, end)
    d = d[mask]
    is_cons = (route == "peer-consensus")
    name = "holding_ic_cons" if is_cons else "holding_ic_raw"
    route_str = "peer-consensus-demeaned" if is_cons else "raw-cap-tilt"
    caveats = ["scheme-level (may blend managers across tenure)"]
    if is_cons:
        caveats.append("peer-consensus ≠ true index — slightly over-strips (mild lower bound)")
        caveats.append("cleaned active-weight proxy; true IC needs point-in-time index weights (W-HIST)")
    else:
        caveats.append("cap-tilt-contaminated (no W-HIST: correlates TOTAL weight, not active weight)")

    # resolve a per-(ym) consensus accessor (whole-category map vs fund-specific map)
    def _cons_for(ym):
        if consensus_by_ym is None:
            return None
        cm = consensus_by_ym.get(ym)
        if cm is None:
            return None
        if code is not None and isinstance(cm, dict) and str(code) in cm:
            return cm[str(code)]
        # already fund-specific {vst_id: Ŵ}
        return cm

    ics, yms, ns = [], [], []
    for ym, g in d.groupby("ym"):
        gg = g[g["fwd_ret"].notna()]
        if len(gg) < _MIN_X_OBS:
            continue
        w = gg["w"].astype(float).values
        if is_cons:
            cons = _cons_for(ym)
            if not cons:
                continue                                  # no consensus this month → skip (don't fake)
            wh = np.array([float(cons.get(str(v), 0.0)) for v in gg["vst_id"].astype(str)])
            x = w - wh                                    # â = w − Ŵ  (cleaned active weight)
        else:
            x = w
        ic = _spearman(x, gg["fwd_ret"].astype(float).values)
        if np.isfinite(ic):
            ics.append(ic); yms.append(ym); ns.append(len(gg))
    if len(ics) < _MIN_T:
        out = _tuple(name, np.nan, np.nan, len(ics), float(np.mean(ns)) if ns else 0,
                     len(ics), np.nan, route_str, caveats + ["insufficient months (<6)"], rho_bar)
        if return_series:
            out_series = {"ym": yms, "x": [float(v) for v in ics]}
            return out, out_series
        return out
    ics = np.array(ics, float)
    xbar, s, T, fm_t = _fama_macbeth(ics)
    se = _newey_west_se(ics, NW_LAG_K)                    # holding IC scored on 1-mo fwd → modest HAC lag
    out = _tuple(name, xbar, se, T, float(np.mean(ns)), T, fm_t, route_str, caveats, rho_bar)
    if return_series:
        return out, {"ym": yms, "x": [float(v) for v in ics]}
    return out


# ==================================================================================================
# Fund-level trade panel (dw_active per ym from funds_flows._pair_flows_active) + forward returns
# ==================================================================================================
def fund_trade_panel(code, months: list | None = None, h=None, ret=None,
                    fwd_by_k: dict | None = None, horizons=FWD_HORIZONS) -> pd.DataFrame:
    """The per-(ym, vst_id) DRIFT-ADJUSTED ACTIVE TRADE (dw_active) for ONE fund, across every month
    it traded, joined to the forward k-month total returns. dw_active comes straight from
    funds_flows._pair_flows_active (L224-229) — inflow-immune, corporate-action-immune. The decision
    month is the ENDING month ym_to of each pair; the trade is scored against the return over the
    NEXT k months. Columns: ym, vst_id, dw_active, fwd_1, fwd_3, fwd_6, fwd_12 (only requested k)."""
    from . import funds_flows as ff
    if h is None or ret is None:
        h, ret = ff._load()
    try:
        from . import scheme_identity as _sid
        code_c = str(_sid.canonical_code(code))
        h = h.copy()
        h["navindia_code"] = h["navindia_code"].map(lambda c: _sid.canonical_code(c)).astype(str)
    except Exception:
        code_c = str(code)
        h = h.copy(); h["navindia_code"] = h["navindia_code"].astype(str)
    allm = sorted(h["ym"].unique())
    pairs = allm[1:] if months is None else [m for m in months if m in allm]
    if fwd_by_k is None:
        fwd_by_k = build_fwd_returns(horizons=horizons)
    rows = []
    for ym_to in pairs:
        try:
            m, a, b, common, _, _ = ff._pair_flows_active(ym_to, h, ret, active_only=True)
        except ValueError:
            continue
        sub = m[m["navindia_code"].astype(str) == code_c]
        if sub.empty:
            continue
        for r in sub.itertuples():
            dw = getattr(r, "dw_active", np.nan)
            if not np.isfinite(dw):
                continue
            row = {"ym": ym_to, "vst_id": str(r.vst_id), "dw_active": float(dw)}
            for k in horizons:
                row[f"fwd_{k}"] = fwd_by_k.get(k, {}).get((str(r.vst_id), ym_to), np.nan)
            rows.append(row)
    cols = ["ym", "vst_id", "dw_active"] + [f"fwd_{k}" for k in horizons]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


# ==================================================================================================
# SIGNAL 1 — trade-level alpha (IC-of-trades) — the breadth king
# ==================================================================================================
def trade_alpha_signal(flows_fund, fwd_ret=None, k: int = HEADLINE_K, tau: float = TRADE_TAU,
                       start: str | None = None, end: str | None = None,
                       return_series: bool = False, rho_bar: float = 0.10) -> dict:
    """SIGNAL 1 — trade-level alpha (spec §2, L87-126): the highest-breadth, fastest signal.

    Scores the drift-adjusted active trade dw_active (funds_flows net_active, already built) against
    forward TOTAL return over k months, the CONTINUOUS way:
      (B) IC-of-trades: IC^trade_t(k) = Spearman(dw_active_i, r_{i,t→t+k}); Fama-MacBeth t.
    (The Add-vs-Trim event-study spread is the companion tuple add_minus_trim_signal().)

    `flows_fund` = the fund_trade_panel DataFrame (cols ym, vst_id, dw_active, fwd_k). MUST stamp the
    TRIM-leg long-only truncation as a LOWER-BOUND caveat (transfer-coefficient leak). Trades are MORE
    independent than static holdings → lower default ρ̄ (0.10) → higher honest breadth. Windowable."""
    d = flows_fund
    col = f"fwd_{k}"
    caveats = ["dw_active = month-end snapshot delta (misses intra-month round-trips)",
               "trim-leg long-only LOWER BOUND on forecasting skill (transfer-coefficient leak, TC≈0.3-0.6)",
               "scheme-level (may blend managers across tenure)"]
    if d is None or len(d) == 0 or col not in d.columns:
        return _tuple("trade_ic", np.nan, np.nan, 0, 0, 0, np.nan, "dw_active", caveats, rho_bar)
    mask = _slice_window(d["ym"].values, start, end)
    d = d[mask]
    ics, yms, ns = [], [], []
    for ym, g in d.groupby("ym"):
        gg = g[g[col].notna() & g["dw_active"].notna()]
        # only score genuine trades (|dw_active| above the noise floor τ) — tiny drifts aren't decisions
        gg = gg[gg["dw_active"].abs() >= tau]
        if len(gg) < _MIN_X_OBS:
            continue
        ic = _spearman(gg["dw_active"].astype(float).values, gg[col].astype(float).values)
        if np.isfinite(ic):
            ics.append(ic); yms.append(ym); ns.append(len(gg))
    if len(ics) < _MIN_T:
        out = _tuple("trade_ic", np.nan, np.nan, len(ics), float(np.mean(ns)) if ns else 0,
                     len(ics), np.nan, "dw_active", caveats + ["insufficient trade-months (<6)"], rho_bar)
        return (out, {"ym": yms, "x": [float(v) for v in ics]}) if return_series else out
    ics = np.array(ics, float)
    xbar, s, T, fm_t = _fama_macbeth(ics)
    se = _newey_west_se(ics, lag=k)                      # NW lag ≈ the k-month forward overlap
    out = _tuple("trade_ic", xbar, se, T, float(np.mean(ns)), T, fm_t, "dw_active", caveats, rho_bar)
    return (out, {"ym": yms, "x": [float(v) for v in ics]}) if return_series else out


# ==================================================================================================
# SIGNAL 1A — Add-vs-Trim forward spread (the legible event study)
# ==================================================================================================
def add_minus_trim_signal(flows_fund, fwd_ret=None, k: int = HEADLINE_K, tau: float = TRADE_TAU,
                          start: str | None = None, end: str | None = None,
                          return_series: bool = False, rho_bar: float = 0.10) -> dict:
    """SIGNAL 1A — the Add-vs-Trim forward spread as its own legible tuple (name="add_minus_trim"):
    do the names the manager ADDED beat the names they TRIMMED over the next k months? The purest,
    most communicable read of selection skill.

        AvT_t(k) = [ Σ_{adds} |dw|·r_{t→t+k} / Σ_{adds} |dw| ]  −  [ Σ_{trims} |dw|·r / Σ_{trims} |dw| ]

    adds = dw_active>+τ, trims = dw_active<−τ; conviction weight = |dw_active|. x_hat = mean_t AvT_t(k)
    ANNUALISED ((1+x)^(12/k)−1, so the k-month spread reads per-year), fm_t = Fama-MacBeth t on the
    RAW (un-annualised) AvT_t(k) series. Caveat: trim leg long-only lower-bound. Windowable."""
    d = flows_fund
    col = f"fwd_{k}"
    caveats = ["trim-leg long-only LOWER BOUND (transfer-coefficient leak — added side is the clean read)",
               "dw_active = month-end snapshot delta (misses intra-month round-trips)",
               "scheme-level (may blend managers across tenure)"]
    if d is None or len(d) == 0 or col not in d.columns:
        return _tuple("add_minus_trim", np.nan, np.nan, 0, 0, 0, np.nan, "dw_active(event-study)",
                      caveats, rho_bar)
    mask = _slice_window(d["ym"].values, start, end)
    d = d[mask]
    avts, yms, ns = [], [], []
    for ym, g in d.groupby("ym"):
        gg = g[g[col].notna() & g["dw_active"].notna()]
        if len(gg) < _MIN_X_OBS:
            continue
        adds = gg[gg["dw_active"] > tau]
        trims = gg[gg["dw_active"] < -tau]
        if len(adds) == 0 or len(trims) == 0:
            continue
        wa = adds["dw_active"].abs().values; ra = adds[col].astype(float).values
        wt = trims["dw_active"].abs().values; rt = trims[col].astype(float).values
        if wa.sum() <= 0 or wt.sum() <= 0:
            continue
        avt = float(np.average(ra, weights=wa) - np.average(rt, weights=wt))
        if np.isfinite(avt):
            avts.append(avt); yms.append(ym); ns.append(len(adds) + len(trims))
    if len(avts) < _MIN_T:
        out = _tuple("add_minus_trim", np.nan, np.nan, len(avts), float(np.mean(ns)) if ns else 0,
                     len(avts), np.nan, "dw_active(event-study)",
                     caveats + ["insufficient trade-months (<6)"], rho_bar)
        return (out, {"ym": yms, "x": [float(v) for v in avts]}) if return_series else out
    avts = np.array(avts, float)
    xbar, s, T, fm_t = _fama_macbeth(avts)                # t on the RAW monthly spread
    se = _newey_west_se(avts, lag=k)
    x_ann = (1.0 + xbar) ** (12.0 / k) - 1.0 if xbar > -1.0 else np.nan   # annualise the spread for display
    se_ann = se * abs(12.0 / k) if np.isfinite(se) else np.nan           # delta-method scale of the SE
    out = _tuple("add_minus_trim", x_ann, se_ann, T, float(np.mean(ns)), T, fm_t,
                 "dw_active(event-study)", caveats, rho_bar)
    out["x_hat_monthly"] = float(xbar)                   # the un-annualised per-month spread (for cross-check)
    return (out, {"ym": yms, "x": [float(v) for v in avts]}) if return_series else out


# ==================================================================================================
# SIGNAL 3 — stock batting vs the EMPIRICAL null (not 0.50)
# ==================================================================================================
def batting_signal(panel_fund, universe_by_ym=None, start: str | None = None,
                   end: str | None = None, return_series: bool = False,
                   bench_fwd_by_ym=None, n_boot: int = BATTING_NULL_BOOT,
                   rho_bar: float = RHO_BAR_DEFAULT) -> dict:
    """SIGNAL 3 — stock batting vs the EMPIRICAL null (spec §3, L130-157).

    Batting = port_hit_cnt = share of HELD stocks that beat the category benchmark over the next month
    (the live def, L142-168). Flaw fixed: the no-skill baseline is ~0.46-0.49 (NOT 0.50), because the
    median stock LAGS a cap-weighted index. So x_hat is the batting rate vs the EMPIRICAL null: each
    month bootstrap RANDOM same-size baskets from the eligible universe (`universe_by_ym` = {ym:[fwd
    rets of all eligible stocks]}) and beat the SAME benchmark; the null batting per month is the mean
    of those random baskets, and x_hat = mean_t(port_batting_t − null_batting_t) (the EXCESS hit rate
    over luck). SE from the circular block-bootstrap of the monthly excess series (block=3) — NOT 1/√T.
    The legible companion, NOT the statistical core.

    `panel_fund` = fund_holding_panel (ym, vst_id, w, fwd_ret). `bench_fwd_by_ym` = {ym: bench_fwd_ret}
    (the category-benchmark forward return). `universe_by_ym` = {ym: np.array(all eligible fwd rets)}."""
    d = panel_fund
    caveats = ["null is the empirical same-size random basket (~0.46-0.49), not 0.50 (legible companion)",
               "scheme-level (may blend managers across tenure)"]
    if d is None or len(d) == 0 or bench_fwd_by_ym is None or universe_by_ym is None:
        return _tuple("batting", np.nan, np.nan, 0, 0, 0, np.nan, "empirical-null", caveats, rho_bar)
    mask = _slice_window(d["ym"].values, start, end)
    d = d[mask]
    excess, yms, ns = [], [], []
    rng = np.random.default_rng(99)
    for ym, g in d.groupby("ym"):
        gg = g[g["fwd_ret"].notna()]
        rb = bench_fwd_by_ym.get(ym)
        uni = universe_by_ym.get(ym)
        if rb is None or uni is None or len(gg) < _MIN_X_OBS or len(uni) < 20:
            continue
        n_held = len(gg)
        port_bat = float((gg["fwd_ret"].astype(float).values - rb >= 0).mean())
        # empirical null: random same-size baskets from the eligible universe, beating the SAME bench
        uni = np.asarray(uni, float)
        draws = min(n_boot, 2000)                         # 2000 random baskets/month is plenty for the mean null
        idx = rng.integers(0, len(uni), size=(draws, n_held))
        null_bat = float((uni[idx] - rb >= 0).mean())     # mean hit-rate of a random basket
        excess.append(port_bat - null_bat); yms.append(ym); ns.append(n_held)
    if len(excess) < _MIN_T:
        out = _tuple("batting", np.nan, np.nan, len(excess), float(np.mean(ns)) if ns else 0,
                     len(excess), np.nan, "empirical-null", caveats + ["insufficient months (<6)"], rho_bar)
        return (out, {"ym": yms, "x": [float(v) for v in excess]}) if return_series else out
    excess = np.array(excess, float)
    xbar = float(np.mean(excess))
    se_boot, p_pos, _, _ = _block_bootstrap_mean_se(excess)   # autocorr-honest SE (block bootstrap, NOT 1/√T)
    _, _, T, fm_t = _fama_macbeth(excess)
    out = _tuple("batting", xbar, se_boot, T, float(np.mean(ns)), T, fm_t, "empirical-null", caveats, rho_bar)
    out["p_beats_null"] = p_pos                           # bootstrap P(excess batting > 0)
    return (out, {"ym": yms, "x": [float(v) for v in excess]}) if return_series else out


# ==================================================================================================
# SLUG — retired to a labelled diagnostic ONLY (look-ahead by construction)
# ==================================================================================================
def slug_diagnostic(panel_fund, start: str | None = None, end: str | None = None,
                   slug_series=None) -> dict:
    """SLUG — RETIRED AS A SIGNAL (spec §3.3, L152): the quartiles are cut from the very returns being
    scored (look-ahead by construction, L151). Returns a LABELLED ex-post DIAGNOSTIC ONLY — it must
    NEVER enter the skill posterior.

    `slug_series` (optional) = the live per-month port_slug_cnt / port_slug_aum already in the fund's
    ts[] array, as {"ym":[...],"sc":[...],"sa":[...]}; if given, this windows + averages it (a filter,
    not a recompute). Shape: {"slug_cnt":float,"slug_aum":float,"diagnostic_only":True,"caveat":...}."""
    caveat = "look-ahead by construction (quartiles cut from the scored returns) — descriptive only, NOT a skill input"
    sc = sa = np.nan
    if slug_series is not None and slug_series.get("ym"):
        ym = np.asarray(slug_series["ym"], dtype=object)
        m = _slice_window(ym, start, end)
        def _avg(key):
            v = np.asarray(slug_series.get(key, []), float)
            v = v[m] if len(v) == len(ym) else v
            v = v[np.isfinite(v)]
            return float(np.mean(v)) if len(v) else np.nan
        sc, sa = _avg("sc"), _avg("sa")
    return {"slug_cnt": (float(sc) if np.isfinite(sc) else None),
            "slug_aum": (float(sa) if np.isfinite(sa) else None),
            "diagnostic_only": True, "caveat": caveat}


# ==================================================================================================
# Universe / benchmark forward-return tables for the batting null (built once, shared)
# ==================================================================================================
def build_universe_fwd(fwd1: dict | None = None) -> dict:
    """{ym: np.array(all eligible 1-month forward total returns that month)} — the empirical no-skill
    basket the batting null samples from (every stock with a known forward return that month). Built
    from the SAME tr_returns_monthly forward returns the signals score against."""
    if fwd1 is None:
        fwd1 = build_fwd_returns(horizons=(1,))[1]
    by_ym = {}
    for (vid, ym), v in fwd1.items():
        if np.isfinite(v):
            by_ym.setdefault(ym, []).append(v)
    return {ym: np.asarray(vs, float) for ym, vs in by_ym.items()}


def build_bench_fwd(category: str) -> dict:
    """{ym: category-benchmark 1-month FORWARD total return} for SIGNAL 3, via the EXACT live map
    (funds_attribution._CAT_BENCH + _bench_monthly_fwd, the t→t+1 convention). The benchmark a held
    stock must beat to count as a 'hit'."""
    from . import funds_attribution as fa
    fwd = fa._bench_monthly_fwd()                          # DataFrame {index_name: {ym: fwd_ret}}
    bench = fa._CAT_BENCH.get(category, fa._DEFAULT_BENCH)
    if bench not in fwd.columns:
        return {}
    s = fwd[bench]
    return {ym: float(v) for ym, v in s.items() if np.isfinite(v)}


# ==================================================================================================
# Convenience: ALL Component-A signal tuples for ONE fund (what the engine will call)
# ==================================================================================================
def all_signals_for_fund(code, category: str, start: str | None = None, end: str | None = None,
                        shared: dict | None = None) -> dict:
    """Run every Component-A signal for ONE fund and return {signal_name: tuple, ..., "_diag":{slug}}.
    `shared` lets the caller pass pre-built heavy tables ONCE across many funds:
        {"h":holdings_df, "ret":ret_series, "fwd_by_k":dict, "consensus":{ym:{code:{vst:Ŵ}}},
         "universe":{ym:arr}, "bench_fwd":{ym:rb}}.  Anything missing is built here (slower)."""
    shared = shared or {}
    fwd_by_k = shared.get("fwd_by_k") or build_fwd_returns()
    fwd1 = fwd_by_k.get(1)
    h_hold = shared.get("h_hold")
    hp = fund_holding_panel(code, h=h_hold, fwd1=fwd1)
    consensus = shared.get("consensus")
    if consensus is None:
        consensus = build_consensus_by_ym(category)
    universe = shared.get("universe") or build_universe_fwd(fwd1)
    bench_fwd = shared.get("bench_fwd") or build_bench_fwd(category)
    tp = fund_trade_panel(code, h=shared.get("h_flow"), ret=shared.get("ret"), fwd_by_k=fwd_by_k)

    out = {
        "holding_ic_cons": holding_ic_signal(hp, consensus, route="peer-consensus",
                                             start=start, end=end, code=str(code)),
        "holding_ic_raw": holding_ic_signal(hp, None, route="raw-cap-tilt", start=start, end=end),
        "trade_ic": trade_alpha_signal(tp, k=HEADLINE_K, start=start, end=end),
        "add_minus_trim": add_minus_trim_signal(tp, k=HEADLINE_K, start=start, end=end),
        "batting": batting_signal(hp, universe_by_ym=universe, bench_fwd_by_ym=bench_fwd,
                                  start=start, end=end),
        "_diag": {"slug": slug_diagnostic(hp, start=start, end=end)},
    }
    return out
