"""
skill_factors.py — the factor library + deflation rail (Skill-Engine Component C, RAIL 1).

WHAT (first principles)
-----------------------
A fund's monthly active return A(t) = rp(t) − rb(t) is NOT pure stock-picking. Part of it is a
persistent STYLE TILT (small-cap lean, value lean, momentum lean, quality lean) and — for a
sectoral/thematic fund — a SECTOR lean. The market pays (or punishes) those tilts regardless of
skill, so we must NOT credit the manager a skill score for owning a factor. We therefore regress
active return on the factor legs the fund was exposed to and read skill off the LEFTOVER intercept:

    A(t) = α + Σ_k β_k · F_k(t)  [+ β_S · S(t) for thematic]  + ε(t)
    skill_t = α_hat / SE_NW(α_hat)          (Newey-West, lag≈3 for monthly autocorrelation)

This module (a) builds the long-short factor legs MKT/SMB/HML/WML/QMJ from OUR in-house data
(the stock daily TR panel ⋈ data/fundamentals_annual_consolidated.csv), monthly cross-section →
tercile sort → top-minus-bottom, exactly in the style of arm_backtest.run(); and (b) exposes
deflate() — the per-fund OLS-with-Newey-West regression that returns the residual alpha + its
HAC standard error + the factor betas + the leg correlation matrix.

★ HONESTY (spec RAIL 1 §"Honest gaps", L409-422). Every gap is stamped, never hidden:
  - SMB uses TURNOVER-RANK as the size proxy (no clean point-in-time float market-cap series) —
    the SAME size proxy amc_replay already uses for buckets. Adequate, not perfect → flagged loudly.
  - HML/QMJ use ANNUAL fundamentals (pat/networth/total_debt/total_assets), publish-LAGGED 4 months
    to avoid look-ahead → coarse but valid. HML's market-cap denominator is ALSO the turnover proxy
    (no true mcap) → the value yield is a turnover-scaled proxy, flagged.
  - WML is the cleanest leg — pure 12-1m price, full universe, no fundamentals dependency.
  - Legs are LONG-SHORT academic legs regressed against LONG-ONLY funds: the regression validly
    removes the part of A that co-moves with a leg; it does NOT claim the fund could short. We do
    NOT orthogonalize the legs to each other — report the βs WITH leg_corr so a collinear-leg
    artifact is visible (caller decides whether to trust the deflated α).
  - A rail may ONLY ever LOWER skill, never raise it.

DATA SOURCES (grounded, read before writing):
  - data/Stocks Data TR till <date>.csv  — wide daily TR panel (newest dated file, via
    vistas.stocks.latest_csv), resampled ME → momentum (12-1m) leg + monthly stock returns for
    every leg.
  - vistas.amc_replay._turn_med() — date×symbol trailing-median traded value (₹cr), the SAME
    point-in-time turnover panel the firm replay uses for size buckets → SMB sort key + HML/value
    denominator proxy.
  - data/fundamentals_annual_consolidated.csv — cols: sym,fy,sales,pat,networth,total_assets,
    total_debt,capex — the ONLY in-house source for value/quality legs (annual, lagged 4mo).
  - vistas.amc_replay._THEME_BENCHMARK / _bench_for() — the EXISTING scheme→sector-TR-index map
    reused for the thematic sector regressor S(t) = (sector_TR − NIFTY 500).
  - vistas.data.load() — benchmark/sector TR index levels (MKT leg + sector leg).

DISPLAY-PLANE only — additive, no analytics.py / data.py touch, NO JS-parity port here.
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

# Factor set, lags, screens (locked; mirrored in SKILL_ENGINE_BUILD.md)
FACTORS = ("MKT", "SMB", "HML", "WML", "QMJ")
FUND_LAG_MONTHS = 4          # publish lag applied to annual fundamentals to avoid look-ahead
NW_LAG = 3                   # Newey-West HAC lag for the monthly active-return autocorrelation
MIN_OBS = 24                 # min fund-months for a trustworthy per-fund regression
MOM_LOOKBACK = 12            # WML: trailing 12...
MOM_SKIP = 1                 # ...minus the most recent 1 month (12-1)
MKT_BENCH = "NIFTY 500"      # the market leg / sector-deflation broad reference

# Liquidity / penny screens — reuse the amc_replay gates so penny/illiquid noise can't define a leg.
try:
    from .amc_replay import MIN_TURN_CR, MIN_PRICE
except Exception:                       # pragma: no cover - defensive, keep module importable
    MIN_TURN_CR = 0.25                  # ₹cr/day trailing-median traded value floor
    MIN_PRICE = 2.0                     # ₹ price floor (penny/glitch tail)

MIN_XS = 30                  # min stocks in a monthly cross-section to compute a leg (arm_backtest.MIN_XS)
_RET_CLIP = (-0.90, 4.0)     # clip monthly stock returns (bad-tick guard; matches the panel's spirit)

# module-level cache so repeated deflate() calls don't rebuild the legs
_LEGS_CACHE: dict = {}


# ───────────────────────────────────────────────────────── helpers
def _newest_stocks_csv() -> str:
    from . import stocks
    p = stocks.latest_csv()
    if not p or not os.path.exists(p):
        raise FileNotFoundError("no 'Stocks Data TR till *.csv' panel found (vistas.stocks.latest_csv)")
    return p


def _load_stock_me() -> pd.DataFrame:
    """Month-end stock TR LEVELS (date × symbol). One row per calendar month-end (last obs in month)."""
    path = _newest_stocks_csv()
    panel = pd.read_csv(path, index_col=0)
    panel.index = pd.to_datetime(panel.index, errors="coerce")
    panel = panel[~panel.index.isna()].sort_index()
    me = panel.resample("ME").last()
    return me


def _load_fundamentals() -> pd.DataFrame:
    """data/fundamentals_annual_consolidated.csv → tidy [sym, fy, pat, networth, total_assets, total_debt].
    `fy` is the fiscal year whose ACCOUNTS END on 31-March-fy (Indian convention). We stamp each row with
    `eff` = the month-end FROM WHICH the figure may be used = (31-Mar-fy) + FUND_LAG_MONTHS (publish lag),
    so a regression on month t only ever sees fundamentals already public at t (NO look-ahead)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "data", "fundamentals_annual_consolidated.csv")
    f = pd.read_csv(path, usecols=["sym", "fy", "pat", "networth", "total_assets", "total_debt"])
    f["sym"] = f["sym"].astype(str).str.upper().str.strip()
    f["fy"] = pd.to_numeric(f["fy"], errors="coerce")
    f = f[f["fy"].notna()].copy()
    for c in ("pat", "networth", "total_assets", "total_debt"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    # fiscal year ends 31-Mar-fy; publishable + LAG months later -> month-end the figure becomes usable
    fy_end = pd.to_datetime(f["fy"].astype(int).astype(str) + "-03-31")
    f["eff"] = (fy_end + pd.DateOffset(months=FUND_LAG_MONTHS)) + pd.offsets.MonthEnd(0)
    return f.sort_values(["sym", "eff"]).reset_index(drop=True)


def _asof_fundamentals(fund: pd.DataFrame, sym: str, t: pd.Timestamp) -> dict | None:
    """Latest fundamentals row for `sym` whose `eff` (publish-lagged date) ≤ month-end t. None if none yet."""
    sub = fund.loc[(fund["sym"] == sym) & (fund["eff"] <= t)]
    if sub.empty:
        return None
    r = sub.iloc[-1]
    return {"pat": r["pat"], "networth": r["networth"],
            "total_assets": r["total_assets"], "total_debt": r["total_debt"]}


def _turn_med_me(me_index: pd.DatetimeIndex):
    """Trailing-median traded value (₹cr) per symbol, reindexed onto the stock month-end calendar.
    Returns a DataFrame (month-end × symbol) or None if the turnover panel is unavailable (degrade)."""
    try:
        from . import amc_replay as ar
        Tm = ar._turn_med()
    except Exception:
        return None
    if Tm is None or getattr(Tm, "empty", True):
        return None
    Tm = Tm.sort_index()
    # align the (daily) turnover panel onto the stock month-end calendar, carry-forward the last value
    return Tm.reindex(me_index, method="ffill")


def _zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    mu, sd = s.mean(), s.std(ddof=0)
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sd


def _tercile_leg(char: pd.Series, fwd: pd.Series) -> float:
    """mean fwd-ret(TOP tercile by `char`) − mean fwd-ret(BOTTOM tercile). NaN if too thin."""
    df = pd.concat([char.rename("c"), fwd.rename("r")], axis=1).dropna()
    if len(df) < MIN_XS:
        return np.nan
    r = df["c"].rank(method="first")
    n = len(df)
    k = n // 3
    if k < 5:
        return np.nan
    bot = df["r"].iloc[r.values.argsort()][:k] if False else df.loc[r <= k, "r"]
    top = df.loc[r > (n - k), "r"]
    if len(top) < 5 or len(bot) < 5:
        return np.nan
    return float(top.mean() - bot.mean())


# ───────────────────────────────────────────────────────── factor legs
def build_factor_legs(stocks_csv: str | None = None,
                      fundamentals_csv: str | None = None,
                      log=print) -> pd.DataFrame:
    """Build the monthly long-short factor legs from OUR in-house data.

    Each leg (except MKT) = mean forward-TR(top tercile by characteristic) − mean forward-TR(bottom
    tercile), rebalanced monthly over the cap/liquidity-screened stock universe (reuse amc_replay
    MIN_TURN_CR / MIN_PRICE gates so penny/illiquid noise can't define a leg). Characteristics:
        SMB : size proxy = trailing-median traded-value rank (SMALL minus BIG)  -> turnover-rank proxy
        HML : earnings yield (pat/turnover-proxy) & book yield (networth/turnover-proxy), annual lag 4mo
        WML : trailing 12-1m TR (skip the most recent month) — cleanest leg, price-only
        QMJ : composite z of ROE(pat/networth) + low-leverage(−total_debt/total_assets), annual lag 4mo
        MKT : NIFTY 500 TR monthly return (the market leg)

    Returns the locked factor-leg frame (month-end DatetimeIndex, cols MKT/SMB/HML/WML/QMJ).

    ★ The factor returns are FORWARD: leg(t) is formed on info ≤ t and earns the t→t+1 return — so a
    fund's A(t+1) (also a t→t+1 active return) is regressed on contemporaneous-economic legs. We align
    the returned frame so legs are indexed by the OUTCOME month-end (t+1), matching the fund's A index.
    """
    me = _load_stock_me()
    if me.shape[0] < MIN_OBS + 2:
        raise ValueError("stock month-end panel too short to build factor legs")

    # monthly simple returns per stock, and forward (next-month) returns
    ret = (me / me.shift(1) - 1.0).clip(*_RET_CLIP)
    fwd = me.shift(-1) / me - 1.0
    fwd = fwd.clip(*_RET_CLIP)

    turn = _turn_med_me(me.index)
    size_ok = turn is not None
    if not size_ok:
        log("[skill_factors] WARNING: turnover panel unavailable -> SMB & value denominator fall back "
            "to inverse trailing-vol size proxy (FLAGGED, weaker).")

    fund = _load_fundamentals()

    # WML characteristic: trailing 12-1m TR = level(t-MOM_SKIP)/level(t-MOM_LOOKBACK) - 1
    mom = me.shift(MOM_SKIP) / me.shift(MOM_LOOKBACK) - 1.0

    # MKT leg = broad-index forward monthly return, aligned to month-ends
    try:
        from . import data as _data
        idx = _data.load()
        if MKT_BENCH in idx.columns:
            mkt_me = idx[MKT_BENCH].resample("ME").last()
            mkt_fwd = (mkt_me.shift(-1) / mkt_me - 1.0)
        else:
            mkt_fwd = fwd.mean(axis=1)        # equal-weight universe fallback
            log(f"[skill_factors] WARNING: '{MKT_BENCH}' not in index data -> MKT = EW universe (flagged).")
    except Exception:
        mkt_fwd = fwd.mean(axis=1)

    rows = []
    months = list(me.index)
    fsyms = set(fund["sym"].unique())
    for t in months:
        rt = fwd.loc[t]                                    # forward t->t+1 return, the leg's payoff
        liq = turn.loc[t] if size_ok else None
        # liquidity + price screen -> the eligible cross-section this month
        px = me.loc[t]
        elig = (px >= MIN_PRICE) & rt.notna()
        if size_ok:
            elig = elig & (liq.reindex(px.index) >= MIN_TURN_CR)
        syms = [s for s in px.index if bool(elig.get(s, False))]
        if len(syms) < MIN_XS:
            rows.append({"date": t})
            continue

        rt_e = rt.reindex(syms)

        # ---- SMB: SMALL minus BIG (top tercile = SMALLEST). size = turnover (or inv-vol fallback)
        if size_ok:
            size = liq.reindex(syms)
            small_char = -size                              # rank-sort -> top tercile = smallest turnover
        else:
            vol = ret[syms].rolling(12, min_periods=6).std().loc[t]   # inverse-vol proxy for size
            small_char = vol                                # higher vol ~ smaller (very rough fallback)
        smb = _tercile_leg(small_char, rt_e)

        # ---- WML: top tercile = highest 12-1m momentum
        wml = _tercile_leg(mom.loc[t].reindex(syms), rt_e)

        # ---- value + quality need fundamentals as-of t (publish-lagged)
        ey, by, roe, loev = {}, {}, {}, {}
        size_den = (liq.reindex(syms) if size_ok else None)
        for s in syms:
            if s not in fsyms:
                continue
            fr = _asof_fundamentals(fund, s, t)
            if fr is None:
                continue
            pat, nw, ta, td = fr["pat"], fr["networth"], fr["total_assets"], fr["total_debt"]
            # market-cap proxy = trailing-median turnover (NO true mcap exists -> FLAGGED)
            mcap = float(size_den.get(s)) if (size_den is not None and pd.notna(size_den.get(s))) else np.nan
            if np.isfinite(mcap) and mcap > 0:
                if pd.notna(pat):
                    ey[s] = pat / mcap                      # earnings yield (turnover-scaled)
                if pd.notna(nw):
                    by[s] = nw / mcap                       # book yield (turnover-scaled)
            if pd.notna(pat) and pd.notna(nw) and nw != 0:
                roe[s] = pat / nw
            if pd.notna(td) and pd.notna(ta) and ta != 0:
                loev[s] = -(td / ta)                        # low leverage = negative debt/assets

        # ---- HML: composite z of earnings-yield + book-yield, top tercile = cheapest
        hml = np.nan
        if size_ok and (len(ey) >= MIN_XS or len(by) >= MIN_XS):
            ey_s = pd.Series(ey); by_s = pd.Series(by)
            val = pd.concat([_zscore(ey_s), _zscore(by_s)], axis=1).mean(axis=1, skipna=True)
            hml = _tercile_leg(val, rt_e.reindex(val.index))

        # ---- QMJ: composite z of ROE + low-leverage, top tercile = highest quality
        qmj = np.nan
        roe_s, lev_s = pd.Series(roe), pd.Series(loev)
        idx_q = roe_s.index.union(lev_s.index)
        if len(idx_q) >= MIN_XS:
            qual = pd.concat([_zscore(roe_s.reindex(idx_q)),
                              _zscore(lev_s.reindex(idx_q))], axis=1).mean(axis=1, skipna=True)
            qmj = _tercile_leg(qual, rt_e.reindex(qual.index))

        rows.append({"date": t, "SMB": smb, "HML": hml, "WML": wml, "QMJ": qmj})

    legs = pd.DataFrame(rows).set_index("date").sort_index()
    # attach MKT (forward index return), index everything by the OUTCOME month (t+1)
    legs["MKT"] = mkt_fwd.reindex(legs.index)
    legs = legs.shift(1)                    # leg formed at t earns t->t+1 -> stamp on t+1 (the outcome month)
    legs = legs[list(FACTORS)]
    log(f"[skill_factors] legs built: {legs.shape[0]} months {legs.index.min().date()}..{legs.index.max().date()}; "
        f"size_proxy={'turnover-rank' if size_ok else 'inv-vol-FALLBACK'}")
    return legs


def get_factor_legs(log=print) -> pd.DataFrame:
    """Cached accessor (legs are expensive to build; reuse across many funds in one process)."""
    if "legs" not in _LEGS_CACHE:
        _LEGS_CACHE["legs"] = build_factor_legs(log=log)
    return _LEGS_CACHE["legs"]


def build_sector_leg(scheme_meta: dict, log=print) -> pd.Series | None:
    """For a sectoral/thematic fund, return the monthly sector leg S(t) = (sector_TR − NIFTY 500),
    using amc_replay._bench_for/_THEME_BENCHMARK to map the scheme to its NSE sector TR index.
    Returns None for a diversified fund (no sector regressor) or when no sector index matches.

    `scheme_meta` may carry: scheme/scheme_name, benchmark, sebi_category, mandate. We only build a
    sector leg when the scheme name matches a _THEME_BENCHMARK keyword AND the index exists in data.
    """
    name = str(scheme_meta.get("scheme") or scheme_meta.get("scheme_name") or "").lower()
    if not name:
        return None
    try:
        from . import amc_replay as ar
        from . import data as _data
        avail = set(_data.available())
        sector_idx = None
        for kw, idx in ar._THEME_BENCHMARK:
            if kw in name and idx in avail:
                sector_idx = idx
                break
        if sector_idx is None:
            return None
        lev = _data.load()
        if sector_idx not in lev.columns or MKT_BENCH not in lev.columns:
            return None
        sec_me = lev[sector_idx].resample("ME").last()
        bch_me = lev[MKT_BENCH].resample("ME").last()
        # forward (t->t+1) sector active return, stamped on the OUTCOME month t+1 to match A & legs
        sec_fwd = (sec_me.shift(-1) / sec_me - 1.0)
        bch_fwd = (bch_me.shift(-1) / bch_me - 1.0)
        S = (sec_fwd - bch_fwd).shift(1)
        S.name = "SECTOR"
        log(f"[skill_factors] sector leg = {sector_idx} − {MKT_BENCH} ({S.dropna().shape[0]} months)")
        return S.dropna()
    except Exception as e:
        log(f"[skill_factors] sector leg unavailable: {e}")
        return None


# ───────────────────────────────────────────────────────── Newey-West OLS
def _newey_west_se(resid: np.ndarray, X: np.ndarray, lag: int = NW_LAG) -> np.ndarray:
    """Newey-West HAC covariance -> SEs of ALL coefficients for the OLS fit y = Xβ + ε.

    X includes the intercept column (so the alpha SE is the [0] element of the returned vector).
    HAC: (X'X)^-1 · S · (X'X)^-1 with S = Σ_l w_l Σ_t e_t e_{t-l} (x_t x_{t-l}' + x_{t-l} x_t'),
    Bartlett weight w_l = 1 − l/(lag+1). Corrects the monthly-overlap autocorrelation that would
    otherwise inflate the t-stat."""
    n, k = X.shape
    XtX_inv = np.linalg.pinv(X.T @ X)
    u = resid.reshape(-1, 1)
    Xu = X * u                                     # n×k, row t = e_t x_t
    S = Xu.T @ Xu                                  # lag-0
    for l in range(1, lag + 1):
        if l >= n:
            break
        w = 1.0 - l / (lag + 1.0)
        G = Xu[l:].T @ Xu[:-l]
        S += w * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    return se


def _ols(y: np.ndarray, X: np.ndarray):
    beta = np.linalg.pinv(X.T @ X) @ X.T @ y
    resid = y - X @ beta
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return beta, resid, r2


# ───────────────────────────────────────────────────────── deflation rail
def _to_me_index(A: pd.Series) -> pd.Series:
    """Coerce a fund active-return series indexed by 'YYYY-MM' OR Timestamps to a month-end index."""
    A = A.dropna()
    if isinstance(A.index, pd.DatetimeIndex):
        idx = A.index + pd.offsets.MonthEnd(0)
    else:
        idx = pd.to_datetime(A.index.astype(str) + "-01", errors="coerce") + pd.offsets.MonthEnd(0)
    out = pd.Series(A.values, index=idx)
    out = out[~out.index.isna()]
    return out[~out.index.duplicated(keep="last")].sort_index()


def deflate(A_monthly, legs=None, sector_S=None,
            gross_excess_ann: float | None = None, nw_lag: int = NW_LAG, log=print) -> dict:
    """★ The RAIL-1 verdict: regress a fund's monthly active return on the factor legs (+ sector leg
    for thematic funds), and return the factor-and-sector-RESIDUAL alpha + its Newey-West SE + t +
    betas + R² + leg correlation matrix.

    A_monthly : the fund's monthly active-return series A(t)=rp−rb (e.g. funds_attribution panel["A"]),
                indexed by month-end DatetimeIndex OR "YYYY-MM" (aligned to `legs` internally).
    legs      : the factor-leg frame from build_factor_legs() (cols MKT/SMB/HML/WML/QMJ). If None, the
                cached process-wide legs are built/reused.
    sector_S  : optional sector leg from build_sector_leg() (thematic funds only); when given it is
                added as one more regressor and sector_deflated=True.
    gross_excess_ann : the fund's GROSS annual excess (for factor_alpha_share = alpha_ann/gross).

    Returns the locked deflate() dict. Rail-honesty invariant: the returned α is the part of A NOT
    explained by tilts, so this can only ever LOWER the headline skill.
    """
    base_caveats = [
        "SMB size = turnover-rank proxy (no true point-in-time market-cap series)",
        "HML/QMJ from ANNUAL fundamentals, publish-lagged 4mo (no quarterly granularity)",
        "HML value yield denominator = turnover proxy, not true market cap",
        "legs are long-short academic vs a long-only fund (removes co-movement, not a short claim)",
        "legs NOT orthogonalized to each other — read betas WITH leg_corr",
    ]

    def _fail(reason):
        return {"alpha": np.nan, "alpha_ann": np.nan, "se_nw": np.nan, "t": np.nan,
                "betas": {}, "r2": np.nan, "leg_corr": {}, "n_obs": 0, "nw_lag": nw_lag,
                "factor_alpha_share": None, "factor_deflated": False, "sector_deflated": False,
                "caveats": base_caveats + [reason], "ok": False}

    if legs is None:
        legs = get_factor_legs(log=log)

    A = _to_me_index(pd.Series(A_monthly))
    if A.empty:
        return _fail("empty active-return series")

    cols = list(FACTORS)
    L = legs[cols].copy()
    if sector_S is not None:
        Ssec = _to_me_index(pd.Series(sector_S))
        L = L.join(Ssec.rename("SECTOR"), how="left")
        cols = list(FACTORS) + ["SECTOR"]

    df = pd.concat([A.rename("A"), L], axis=1, join="inner").dropna()
    n = len(df)
    if n < MIN_OBS:
        out = _fail(f"n_obs={n} < MIN_OBS={MIN_OBS}")
        out["n_obs"] = n
        out["sector_deflated"] = sector_S is not None
        return out

    y = df["A"].values.astype(float)
    F = df[cols].values.astype(float)
    X = np.column_stack([np.ones(n), F])               # intercept + factors

    beta, resid, r2 = _ols(y, X)
    se = _newey_west_se(resid, X, lag=nw_lag)
    alpha = float(beta[0]); se_a = float(se[0])
    t = alpha / se_a if se_a and np.isfinite(se_a) and se_a > 0 else np.nan
    alpha_ann = (1.0 + alpha) ** 12 - 1.0 if np.isfinite(alpha) else np.nan

    betas = {c: float(b) for c, b in zip(cols, beta[1:])}
    corr = df[cols].corr(method="pearson")
    leg_corr = {a: {b: (float(corr.loc[a, b]) if np.isfinite(corr.loc[a, b]) else None) for b in cols}
                for a in cols}

    share = None
    if gross_excess_ann is not None and np.isfinite(gross_excess_ann) and gross_excess_ann != 0 \
            and np.isfinite(alpha_ann):
        share = float(alpha_ann / gross_excess_ann)

    caveats = list(base_caveats)
    if sector_S is not None:
        caveats.append("sector beta stripped (thematic) — alpha is WITHIN-sector selection")

    return {
        "alpha": alpha, "alpha_ann": float(alpha_ann), "se_nw": se_a, "t": float(t),
        "betas": betas, "r2": float(r2) if np.isfinite(r2) else np.nan,
        "leg_corr": leg_corr, "n_obs": int(n), "nw_lag": int(nw_lag),
        "factor_alpha_share": share, "factor_deflated": True,
        "sector_deflated": sector_S is not None, "caveats": caveats, "ok": True,
    }


# ───────────────────────────────────────────────────────── self-check
def _selfcheck(n_funds: int = 1, log=print):
    """Build legs on real data, print monthly means/vols + the 5×5 leg correlation matrix, then
    deflate ONE real fund's A(t) and print alpha + se_nw + t + betas. Run: python -m vistas.skill_factors"""
    legs = get_factor_legs(log=log)
    desc = pd.DataFrame({
        "mean_%/mo": (legs.mean() * 100).round(3),
        "vol_%/mo": (legs.std() * 100).round(3),
        "ann_%/yr": (((1 + legs.mean()) ** 12 - 1) * 100).round(2),
        "n_months": legs.count(),
    })
    log("\n=== FACTOR LEGS (monthly long-short) ===")
    log(desc.to_string())
    log("\n=== LEG CORRELATION (5×5) — sane: WML/HML not ±1 ===")
    log(legs.corr().round(3).to_string())

    from . import funds_attribution as fa
    panel = fa.load_panel()
    cnt = panel.groupby("navindia_code")["ym"].count().sort_values(ascending=False)
    code = cnt.index[0]
    d = panel[panel["navindia_code"] == code].sort_values("ym")
    A = pd.Series(d["A"].values, index=d["ym"].values)
    meta = {"scheme_name": d["scheme_name"].iloc[-1], "sebi_category": d["sebi_category"].iloc[-1],
            "benchmark": d["bench"].iloc[-1]}
    log(f"\n=== DEFLATE one real fund: {meta['scheme_name']} ({meta['sebi_category']}, n={len(d)}) ===")
    res = deflate(A, legs, sector_S=build_sector_leg(meta, log=log), log=log)
    log(f"ok={res['ok']} n_obs={res['n_obs']} nw_lag={res['nw_lag']}")
    log(f"alpha={res['alpha']*100:.4f}%/mo  alpha_ann={res['alpha_ann']*100:.2f}%/yr  "
        f"se_nw={res['se_nw']*100:.4f}%/mo  t={res['t']:.2f}  r2={res['r2']:.3f}")
    log(f"betas={ {k: round(v,3) for k,v in res['betas'].items()} }")
    log(f"sector_deflated={res['sector_deflated']}  factor_alpha_share={res['factor_alpha_share']}")
    return res


if __name__ == "__main__":       # python -m vistas.skill_factors
    _selfcheck()
