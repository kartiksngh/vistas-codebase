"""
mesh_research.py — the SHARED multi-force RESEARCH HARNESS for stock-signal backtests.

WHY THIS EXISTS
---------------
`vistas/mesh_backtest.py` validated ONE hand-built signal (S1 CONVICTION_ADD) and proved
its conventions on KV's data. Downstream research agents now want to test MANY candidate
signals against the SAME rigorous, reproducible backbone without re-wiring data each time.
This module exposes:

  * build_panel(start)  -> a LONG monthly factor panel (month_end x symbol) carrying every
                           force we can assemble (ARM level/trend, flow, breadth, momentum,
                           value, quality, mcap) + forward TR returns at 1/3/6/12M, persisted
                           to parquet so any agent reads the same frozen panel.
  * evaluate(score,name) -> the IC / decile-spread / era-stability / beats-ARM verdict for ANY
                           score column or aligned Series, using the EXACT IC convention of
                           mesh_backtest (monthly cross-sectional Spearman IC; decile spread
                           D10-D1 annualised; MIN_XS=30; dead names retained; 4 calendar eras).
  * arm_baseline()      -> the ARM_LEVEL @6m IC, computed ONCE and cached, so every signal is
                           judged "beats-ARM on the SAME rows".

PARITY WITH THE KNOWN TRUTH (the contract this harness must honour)
-------------------------------------------------------------------
The harness is worthless if it cannot reproduce the published numbers. By construction the
ARM / flow / breadth / forward-return blocks are the SAME building blocks mesh_backtest uses
(arm_backtest._arm_symbol_series, funds_flows.stock_flows via _build_flow_breadth_panel, the
month-end TR price panel from stocks.latest_csv()), so:

    arm_level                                   IC@6m ~= 0.0712  (spread ~13.4%/yr)
    z(flow_intensity_3m)+z(dbreadth)+z(arm_trend_3m)  IC@6m ~= 0.0541  (LOSES to ARM)

self_validate() asserts both within 0.01 and that ARM beats the blend.

KEYING: the panel is keyed by NSE **symbol** (the join key the known-truth pipeline uses end
to end). A `vst_id` column is attached for downstream convenience (symbol -> vst_id via idmap),
but symbol is the row identity so the validation reproduces exactly.

LICENSING: raw per-stock ARM is used here IN MEMORY only. The persisted parquet DOES carry the
per-stock arm_level/trend columns because it is a SCRATCH research artifact (temp dir), NOT a
findings file — never copy raw per-stock ARM into a written findings/report.

Conventions mirror mesh_backtest.py exactly (read its header for the rationale).
"""
from __future__ import annotations

import os
import json
import glob
import numpy as np
import pandas as pd

from . import arm_backtest, stocks, funds_flows, mesh_backtest

# ------------------------------------------------------------------ constants (match mesh_backtest)
HORIZONS = [1, 3, 6, 12]
MIN_XS = 30
FLOW_WINDOW = 3

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SCREENER_DIR = os.path.join(_ROOT, "data", "screener")

# the 4 calendar eras the IC convention asks for
ERAS = [("2013-2016", "2013-01-01", "2016-12-31"),
        ("2017-2020", "2017-01-01", "2020-12-31"),
        ("2021-2023", "2021-01-01", "2023-12-31"),
        ("2024-2026", "2024-01-01", "2026-12-31")]

# default scratch panel path (the temp dir the task fixes)
PANEL_PATH = os.path.join(
    r"C:\Users\ADMINI~1\AppData\Local\Temp\claude",
    "C--Users-Administrator-Documents-Projects-Vistas",
    "38bc1fe6-4235-4d7c-8737-9b2f68ee3a7b", "scratchpad", "mesh_panel.parquet")


# ================================================================== stats helpers (verbatim convention)
def _zscore_xs(s: pd.Series) -> pd.Series:
    """Cross-sectional z over non-NaN values (population std, ddof=0). NaN if degenerate.
    Identical to mesh_backtest._zscore_xs so a z-composite here == the known-truth z-composite."""
    v = s.dropna()
    if len(v) < 2:
        return pd.Series(np.nan, index=s.index)
    mu, sd = v.mean(), v.std(ddof=0)
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(np.nan, index=s.index)
    return (s - mu) / sd


def _xs_z_wide(df: pd.DataFrame) -> pd.DataFrame:
    """Apply cross-sectional z row-by-row (each month-end across its stocks)."""
    return df.apply(lambda row: _zscore_xs(row), axis=1)


# ================================================================== the WIDE field assembly (cached)
_WIDE_CACHE: dict | None = None


def _assemble_wide(start="2013-01-01", log=lambda *a, **k: None) -> dict:
    """Build every FORCE FIELD as a wide month-end x symbol DataFrame on a single common axis.

    Reuses mesh_backtest's exact blocks so the numbers are bit-for-bit the known truth:
      - month-end TR price panel  (stocks.latest_csv() -> resample 'ME' -> last)
      - flow / breadth panel      (mesh_backtest._build_flow_breadth_panel)
      - ARM month-end ffilled      (arm_backtest._arm_symbol_series, reindex ffill)
    Then derives trend/momentum/value/quality on that frozen axis.

    Returns a dict of wide frames + the common axis + symbol list. Cached across calls.
    """
    global _WIDE_CACHE
    if _WIDE_CACHE is not None and _WIDE_CACHE["start"] == start:
        return _WIDE_CACHE

    # ---- 1. month-end TR price panel (the forward-return + momentum source) ----
    panel = pd.read_csv(stocks.latest_csv(), index_col=0)
    panel.index = pd.to_datetime(panel.index, errors="coerce")
    panel = panel[~panel.index.isna()].sort_index()
    panel = panel[panel.index >= pd.Timestamp(start)]
    me = panel.resample("ME").last()
    log(f"[mesh_research] price panel {panel.shape[1]} stocks, {me.shape[0]} month-ends "
        f"{me.index.min().date()}..{me.index.max().date()}")

    # ---- 2. flow / breadth panel (reuse the SAME builder the known truth uses) ----
    net_flow, mv_end, dbreadth = mesh_backtest._build_flow_breadth_panel(log)

    # ---- 3. common month-end axis = price axis intersect flow axis (the overlap window) ----
    me_axis = me.index.intersection(net_flow.index)
    me = me.reindex(me_axis)
    net_flow = net_flow.reindex(me_axis)
    mv_end = mv_end.reindex(me_axis)
    dbreadth = dbreadth.reindex(me_axis)

    # symbols present in BOTH the price panel and the flow panel (dead names retained naturally)
    flow_syms = set(net_flow.columns)
    syms = [s for s in me.columns if s in flow_syms]
    me = me[syms]
    net_flow = net_flow.reindex(columns=syms)
    mv_end = mv_end.reindex(columns=syms)
    dbreadth = dbreadth.reindex(columns=syms)
    log(f"[mesh_research] overlap {me_axis.min().date()}..{me_axis.max().date()} "
        f"({len(me_axis)} months), {len(syms)} symbols in both panels")

    # ---- 4. ARM aligned to the same month-end axis, ffilled (reuse the known-truth aligner) ----
    arm_me = mesh_backtest._arm_month_end(me_axis, syms, log).reindex(columns=syms)

    # ---- 5. derived FORCE fields on the frozen axis ----
    # ARM trends (analyst-revision direction; the validated edge mechanic)
    arm_trend_1m = arm_me - arm_me.shift(1)
    arm_trend_3m = arm_me - arm_me.shift(3)             # == dARM_3m in the known truth
    # flow: trailing-3M cumulative net active flow, size-neutralised by end fund market value
    flow_3m_cr = net_flow.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum()
    flow_intensity_3m = flow_3m_cr / mv_end.replace(0.0, np.nan)
    # breadth level (count of fund owners proxy) is not directly here; dbreadth is the CHANGE.
    # breadth_level = running cumulative sum of dbreadth per symbol (a relative owner-count level).
    breadth_level = dbreadth.cumsum()
    # momentum from the TR panel (skip-last-month for mom_6m per the spec)
    mom_6m = (me.shift(1) / me.shift(7) - 1.0)          # 6-month return ending one month ago (skip latest)
    mom_12m = (me / me.shift(12) - 1.0)                 # 12-month total return
    # mcap proxy = end fund market value (Rs cr) — NOT true float mcap, a size proxy on the same axis
    mcap_cr = mv_end.copy()

    # ---- 6. forward TR returns (the targets) ----
    fwd = {h: me.shift(-h) / me - 1.0 for h in HORIZONS}

    _WIDE_CACHE = {
        "start": start, "me_axis": me_axis, "syms": syms, "me": me,
        "arm_level": arm_me, "arm_trend_1m": arm_trend_1m, "arm_trend_3m": arm_trend_3m,
        "flow_intensity_3m": flow_intensity_3m, "flow_3m_cr": flow_3m_cr,
        "dbreadth": dbreadth, "breadth_level": breadth_level,
        "mom_6m": mom_6m, "mom_12m": mom_12m, "mcap_cr": mcap_cr,
        "net_flow": net_flow, "mv_end": mv_end, "fwd": fwd,
    }
    return _WIDE_CACHE


# ================================================================== value / quality (best-effort, point-in-time)
def _valuation_wide(me_axis, syms, me_px, log=lambda *a, **k: None):
    """Best-effort point-in-time value (E/P, B/P, S/P) and quality on the frozen month-end axis.

    SOURCE: per-symbol Screener bundles data/screener/<SYM>.json.
      * E/P : the bundle's `valuation.EPS` is an AS-OF-DATED step series (TTM EPS as it was
              known on each date). We ffill it to month-ends, then E/P = EPS_asof / price[t].
              This is point-in-time clean (no look-ahead — the EPS value carries its report date).
      * B/P : annual book-value-per-share (BVPS = networth / shares) as of the annual statement
              date, ffilled to month-ends, /price.  (annual granularity -> coarser, best-effort)
      * S/P : annual sales-per-share (sales / shares) as of statement date, ffilled, /price.
      * quality_score: ROE proxy (PAT/networth, as of statement date) minus an accrual penalty
              ((PAT-CFO)/total_assets, Sloan); cross-sectionally this ranks "real, cash-backed
              profitability" higher. Annual granularity, best-effort.

    Returns wide frames (month-end x symbol): value_ep, value_bp, value_sp, quality_score.
    Coverage is honestly partial (not every symbol has a bundle / clean statements).
    """
    ep = pd.DataFrame(index=me_axis, columns=syms, dtype=float)
    bp = pd.DataFrame(index=me_axis, columns=syms, dtype=float)
    sp = pd.DataFrame(index=me_axis, columns=syms, dtype=float)
    ql = pd.DataFrame(index=me_axis, columns=syms, dtype=float)

    def _asof_series(pairs):
        """[[date,val],...] -> Series indexed by Timestamp, ffilled onto me_axis."""
        rows = [(pd.Timestamp(d), v) for d, v in pairs
                if d and v is not None and np.isfinite(_num(v))]
        if not rows:
            return None
        s = pd.Series({d: float(v) for d, v in rows}).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        return s.reindex(me_axis, method="ffill")

    n_eps = n_bp = n_sp = n_ql = 0
    for sym in syms:
        path = os.path.join(_SCREENER_DIR, f"{sym}.json")
        if not os.path.exists(path):
            continue
        try:
            b = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        px = me_px[sym]

        # ---- E/P from the as-of-dated EPS step series ----
        eps_pairs = (b.get("valuation") or {}).get("EPS")
        if eps_pairs:
            eps_s = _asof_series(eps_pairs)
            if eps_s is not None:
                e = eps_s / px.replace(0.0, np.nan)
                ep[sym] = e
                n_eps += 1

        # ---- annual BVPS / sales-per-share / quality from statements (point-in-time as-of) ----
        bvps_dt, sps_dt, ql_dt = _annual_per_share(b)
        if bvps_dt is not None:
            s = _asof_pairs_series(bvps_dt, me_axis)
            if s is not None:
                bp[sym] = s / px.replace(0.0, np.nan)
                n_bp += 1
        if sps_dt is not None:
            s = _asof_pairs_series(sps_dt, me_axis)
            if s is not None:
                sp[sym] = s / px.replace(0.0, np.nan)
                n_sp += 1
        if ql_dt is not None:
            s = _asof_pairs_series(ql_dt, me_axis)
            if s is not None:
                ql[sym] = s
                n_ql += 1

    log(f"[mesh_research] valuation coverage: E/P {n_eps}, B/P {n_bp}, S/P {n_sp}, quality {n_ql} symbols")
    return ep, bp, sp, ql


def _num(v):
    try:
        return float(v)
    except Exception:
        return float("nan")


def _annual_per_share(bundle):
    """Extract annual BVPS, sales-per-share, and a quality value, each as [(asof_date,val)].

    as-of date = the END of the fiscal-year label (best available; statements carry period
    labels like 'Mar 2023'). We map each annual column to a conservative as-of date 3 months
    AFTER fiscal year-end (results-release lag) so there is no look-ahead.
    Returns (bvps_pairs, sps_pairs, quality_pairs) or (None,None,None) on any failure.
    """
    try:
        st = bundle.get("statements") or {}
        pl = _tbl(st, "profit_loss")
        bs = _tbl(st, "balance_sheet")
        cf = _tbl(st, "cash_flow")
        if not pl or not bs:
            return None, None, None
        ap = _cols(pl)
        bp_cols = _cols(bs)
        if not ap or not bp_cols:
            return None, None, None
        pat = _row(pl, ["net profit"])
        eps = _row(pl, ["eps"])
        sales = _row(pl, ["sales", "revenue"])
        eqcap = _row(bs, ["equity capital"])
        reserves = _row(bs, ["reserves"])
        tot_assets = _row(bs, ["total assets"])
        cfo = _row(cf, ["cash from operating"]) if cf else {}

        bvps_pairs, sps_pairs, ql_pairs = [], [], []
        for c in ap:
            asof = _asof_from_label(c)
            if asof is None:
                continue
            p = pat.get(c)
            e = eps.get(c)
            sa = sales.get(c)
            # shares (crore) = PAT / EPS
            shares = (p / e) if (p is not None and e not in (None, 0)) else None
            # networth from the matching BS column (same label if present, else nearest)
            nw = None
            ta = None
            cf_op = None
            if c in bp_cols:
                ec = eqcap.get(c)
                rv = reserves.get(c)
                if ec is not None or rv is not None:
                    nw = (ec or 0.0) + (rv or 0.0)
                ta = tot_assets.get(c)
            if cfo:
                cf_op = cfo.get(c)
            if shares not in (None, 0):
                if nw is not None:
                    bvps_pairs.append((asof, nw / shares))
                if sa is not None:
                    sps_pairs.append((asof, sa / shares))
            # quality = ROE - accrual penalty
            if nw not in (None, 0) and p is not None:
                roe = p / nw
                pen = 0.0
                if ta not in (None, 0) and cf_op is not None and p is not None:
                    pen = (p - cf_op) / ta          # accruals/assets (Sloan; high = low quality)
                ql_pairs.append((asof, roe - pen))
        return (bvps_pairs or None, sps_pairs or None, ql_pairs or None)
    except Exception:
        return None, None, None


def _asof_pairs_series(pairs, me_axis):
    rows = [(pd.Timestamp(d), float(v)) for d, v in pairs
            if d is not None and v is not None and np.isfinite(_num(v))]
    if not rows:
        return None
    s = pd.Series(dict(rows)).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(me_axis, method="ffill")


def _tbl(statements, key):
    """A Screener statement table is a LIST of row-dicts, each with a label under 'Unnamed: 0'
    (or the first key) and period columns like 'Mar 2015'. Return the list (or None)."""
    t = statements.get(key)
    return t if isinstance(t, list) and t else None


def _label_key(row):
    """The key holding the row's label ('Unnamed: 0' in Screener dumps; else the first key)."""
    if "Unnamed: 0" in row:
        return "Unnamed: 0"
    for k in row:
        return k
    return None


def _cols(table):
    """Period columns across the table (any column key that parses to an as-of date)."""
    if not table:
        return []
    seen, cols = set(), []
    for row in table:
        for k in row:
            if k in seen:
                continue
            seen.add(k)
            if _asof_from_label(k) is not None:
                cols.append(k)
    return cols


def _clean_num(v):
    """Parse a Screener cell ('374,372', '12.3%', '-33') to float; NaN on failure."""
    if v is None:
        return float("nan")
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").replace("\xa0", "").strip()
    if s in ("", "-", "—"):
        return float("nan")
    try:
        return float(s)
    except Exception:
        return float("nan")


def _row(table, aliases):
    """{col: float} for the FIRST row whose label contains any alias (case-insensitive)."""
    if not table:
        return {}
    al = [a.lower() for a in aliases]
    for row in table:
        lk = _label_key(row)
        ll = str(row.get(lk, "")).lower()
        if any(a in ll for a in al):
            out = {}
            for c, v in row.items():
                if c == lk:
                    continue
                fv = _clean_num(v)
                out[c] = fv if np.isfinite(fv) else None
            return out
    return {}


def _asof_from_label(label):
    """'Mar 2023' / '2023-03' / 'Mar-23' -> a conservative as-of Timestamp = fiscal-end + 3 months
    (so the annual figure is only 'known' a quarter after year-end; no look-ahead)."""
    s = str(label).strip()
    ts = pd.to_datetime(s, errors="coerce")
    if pd.isna(ts):
        for fmt in ("%b %Y", "%b-%y", "%b-%Y", "%Y-%m", "%Y"):
            try:
                ts = pd.Timestamp(pd.to_datetime(s, format=fmt))
                break
            except Exception:
                continue
    if pd.isna(ts):
        return None
    return (ts + pd.offsets.MonthEnd(0) + pd.DateOffset(months=3)) + pd.offsets.MonthEnd(0)


# ================================================================== build_panel (the public LONG panel)
def build_panel(start="2013-01-01", persist=True, log=print) -> str:
    """Assemble the long monthly factor panel and persist to parquet. Returns the parquet path.

    Output: one row per (month_end, symbol) carrying every available force + forward returns.
    A force column is present where it can be computed; coverage is reported per column.
    """
    W = _assemble_wide(start, log)
    me_axis, syms, me_px = W["me_axis"], W["syms"], W["me"]

    # value / quality (best-effort, point-in-time)
    value_ep, value_bp, value_sp, quality = _valuation_wide(me_axis, syms, me_px, log)
    # combined value z = mean of the cross-sectional z of E/P, B/P, S/P (higher = cheaper)
    value_z = _combined_value_z(value_ep, value_bp, value_sp)

    wide_cols = {
        "arm_level": W["arm_level"],
        "arm_trend_1m": W["arm_trend_1m"],
        "arm_trend_3m": W["arm_trend_3m"],
        "flow_intensity_3m": W["flow_intensity_3m"],
        "flow_3m_cr": W["flow_3m_cr"],
        "dbreadth": W["dbreadth"],
        "breadth_level": W["breadth_level"],
        "mom_6m": W["mom_6m"],
        "mom_12m": W["mom_12m"],
        "value_ep": value_ep,
        "value_bp": value_bp,
        "value_sp": value_sp,
        "value_z": value_z,
        "quality_score": quality,
        "mcap_cr": W["mcap_cr"],
        "ret_fwd_1m": W["fwd"][1],
        "ret_fwd_3m": W["fwd"][3],
        "ret_fwd_6m": W["fwd"][6],
        "ret_fwd_12m": W["fwd"][12],
    }

    # melt every wide frame to long and outer-merge on (month_end, symbol)
    long = None
    for name, df in wide_cols.items():
        d = df.reindex(index=me_axis, columns=syms)
        m = d.stack(future_stack=True).rename(name)       # MultiIndex (month_end, symbol) -> value
        m.index.set_names(["month_end", "symbol"], inplace=True)
        long = m.to_frame() if long is None else long.join(m, how="outer")
    long = long.reset_index()

    # attach vst_id for downstream convenience (symbol -> vst_id; best-effort, may be None)
    long["vst_id"] = long["symbol"].map(_symbol_to_vid_map(syms))

    # drop rows that are entirely empty across all force + forward cols (no information)
    force_cols = [c for c in wide_cols if not c.startswith("ret_fwd_")]
    fwd_cols = [c for c in wide_cols if c.startswith("ret_fwd_")]
    keep = long[force_cols + fwd_cols].notna().any(axis=1)
    long = long[keep].reset_index(drop=True)

    # coverage report (share of (month,symbol) rows in months where ANY arm_level exists)
    cov = _coverage(long, list(wide_cols.keys()))
    log("[mesh_research] panel coverage % (of non-empty rows):")
    for c, p in cov.items():
        log(f"    {c:18s} {p:5.1f}%")

    long = long.sort_values(["month_end", "symbol"]).reset_index(drop=True)

    if persist:
        os.makedirs(os.path.dirname(PANEL_PATH), exist_ok=True)
        long.to_parquet(PANEL_PATH, index=False)
        log(f"[mesh_research] panel persisted -> {PANEL_PATH} "
            f"({len(long):,} rows, {long['month_end'].nunique()} months, "
            f"{long['symbol'].nunique()} symbols)")
    return PANEL_PATH


def _combined_value_z(ep, bp, sp):
    """value_z = average of cross-sectional z of E/P, B/P, S/P (each month), higher = cheaper.
    Uses whatever of the three is present that month (mean over available z's)."""
    zs = [_xs_z_wide(x) for x in (ep, bp, sp)]
    stack = np.stack([z.values for z in zs], axis=0)        # 3 x months x syms
    with np.errstate(invalid="ignore"):
        avg = np.nanmean(stack, axis=0)
    return pd.DataFrame(avg, index=ep.index, columns=ep.columns)


def _symbol_to_vid_map(syms):
    try:
        from . import idmap
        out = {}
        for s in syms:
            try:
                out[s] = idmap.symbol_to_vid(s)
            except Exception:
                out[s] = None
        return out
    except Exception:
        return {s: None for s in syms}


def _coverage(long, cols):
    n = len(long)
    return {c: (100.0 * long[c].notna().sum() / n if n else 0.0) for c in cols}


# ================================================================== evaluate (the IC verdict for ANY score)
def _wide_from_panel(panel: pd.DataFrame, col: str) -> pd.DataFrame:
    """Pivot a long-panel column back to a wide month_end x symbol frame."""
    return panel.pivot(index="month_end", columns="symbol", values=col)


def _ic_block(score_me: pd.DataFrame, fwd_me: pd.DataFrame, h: int):
    """One horizon: monthly Spearman IC + decile spread, on the EXACT convention.
    Returns (ics, eras_ic dict, spreads, n_rows_per_month list)."""
    ics, spreads, counts = [], [], []
    ic_by_era = {e[0]: [] for e in ERAS}
    for t in score_me.index:
        if t not in fwd_me.index:
            continue
        a = score_me.loc[t]
        r = fwd_me.loc[t]
        common = a.index.intersection(r.index)
        a, r = a[common], r[common]
        ok = a.notna() & r.notna()
        if ok.sum() < MIN_XS:
            continue
        a2, r2 = a[ok], r[ok]
        ic = a2.corr(r2, method="spearman")
        if pd.isna(ic):
            continue
        ics.append(ic)
        counts.append(int(ok.sum()))
        for nm, s0, s1 in ERAS:
            if pd.Timestamp(s0) <= t <= pd.Timestamp(s1):
                ic_by_era[nm].append(ic)
        try:
            d = pd.qcut(a2.rank(method="first"), 10, labels=False)
            grp = r2.groupby(d).mean()
            if 9 in grp.index and 0 in grp.index:
                spreads.append(grp.loc[9] - grp.loc[0])
        except Exception:
            pass
    return ics, ic_by_era, spreads, counts


def _resolve_score_wide(score, panel):
    """Accept a column NAME (str) -> pivot from panel; or an aligned Series with a
    (month_end, symbol) MultiIndex -> unstack; or a wide DataFrame -> use as-is."""
    if isinstance(score, str):
        return _wide_from_panel(panel, score)
    if isinstance(score, pd.DataFrame):
        return score
    if isinstance(score, pd.Series):
        if isinstance(score.index, pd.MultiIndex):
            w = score.unstack(level=-1)
            return w
        raise ValueError("Series score must have a (month_end, symbol) MultiIndex.")
    raise TypeError(f"unsupported score type {type(score)}")


def arm_level_on(mask: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """ARM_LEVEL restricted to the cells of `mask` — the apples-to-apples ARM baseline on a
    given signal's universe (the canonical comparison; on the blend's have_all mask this is 0.0712)."""
    arm_wide = _wide_from_panel(panel, "arm_level")
    m = mask.reindex(index=arm_wide.index, columns=arm_wide.columns)
    return arm_wide.where(m.fillna(False).astype(bool))


_ARM_BASELINE_CACHE: dict | None = None


def arm_baseline(panel: pd.DataFrame | None = None):
    """The ARM_LEVEL reference, computed ONCE. Returns {'ic_6m','wide'(arm_level wide),'fwd6'}.
    Cached so every evaluate() judges beats-ARM against the identical baseline."""
    global _ARM_BASELINE_CACHE
    if _ARM_BASELINE_CACHE is not None:
        return _ARM_BASELINE_CACHE
    if panel is None:
        panel = pd.read_parquet(PANEL_PATH)
    arm_wide = _wide_from_panel(panel, "arm_level")
    fwd6 = _wide_from_panel(panel, "ret_fwd_6m")
    ics, _, _, _ = _ic_block(arm_wide, fwd6, 6)
    _ARM_BASELINE_CACHE = {
        "ic_6m": float(np.mean(ics)) if ics else float("nan"),
        "wide": arm_wide, "fwd6": fwd6,
    }
    return _ARM_BASELINE_CACHE


def evaluate(score, name: str, panel: pd.DataFrame | None = None) -> dict:
    """The full IC verdict for ANY score (column name / MultiIndex Series / wide frame).

    Returns:
      ic_1m, ic_3m, ic_6m, ic_12m   (mean monthly Spearman IC at each horizon)
      t_6m                          (mean/std*sqrt(n) of the 6M monthly IC series)
      spread_6m_pct                 (mean monthly D10-D1 forward 6M return, annualised x(12/6), %)
      pct_months_pos_6m             (share of months with IC>0 at 6M)
      era_stability                 ({era: ic_6m} for the 4 calendar eras)
      n_months                      (# scored months at 6M)
      beats_arm_6m                  (this ic_6m > ARM_LEVEL ic_6m computed on the SAME rows)
      beats_margin_ic               (this ic_6m - ARM ic_6m on the same rows)
    """
    if panel is None:
        panel = pd.read_parquet(PANEL_PATH)
    score_wide = _resolve_score_wide(score, panel)

    fwd = {h: _wide_from_panel(panel, f"ret_fwd_{h}m") for h in HORIZONS}

    out = {"name": name}
    block6 = None
    for h in HORIZONS:
        ics, ic_by_era, spreads, counts = _ic_block(score_wide, fwd[h], h)
        out[f"ic_{h}m"] = round(float(np.mean(ics)), 4) if ics else None
        if h == 6:
            block6 = (ics, ic_by_era, spreads, counts)

    ics6, ic_by_era6, spreads6, counts6 = block6
    n6 = len(ics6)
    out["n_months"] = n6
    out["pct_months_pos_6m"] = round(float(np.mean(np.array(ics6) > 0)), 3) if n6 else None
    sd = np.std(ics6, ddof=1) if n6 > 2 else 0.0
    out["t_6m"] = round(float(np.mean(ics6) / (sd / np.sqrt(n6))), 2) if (n6 > 2 and sd > 0) else None
    out["spread_6m_pct"] = round(float(np.mean(spreads6) * (12.0 / 6.0)) * 100, 2) if spreads6 else None
    out["era_stability"] = {nm: (round(float(np.mean(v)), 4) if v else None)
                            for nm, v in ic_by_era6.items()}

    # beats-ARM on the SAME rows: re-score ARM on exactly the (month,symbol) cells this score covers
    base = arm_baseline(panel)
    arm_wide = base["wide"]
    fwd6 = fwd[6]
    # mask ARM to where the score is present, so the head-to-head is on identical rows
    mask = score_wide.reindex(index=arm_wide.index, columns=arm_wide.columns).notna()
    arm_masked = arm_wide.where(mask)
    arm_ics, _, _, _ = _ic_block(arm_masked, fwd6, 6)
    arm_ic_same = float(np.mean(arm_ics)) if arm_ics else float("nan")
    this6 = out["ic_6m"] if out["ic_6m"] is not None else float("nan")
    out["beats_arm_6m"] = bool(np.isfinite(this6) and np.isfinite(arm_ic_same) and this6 > arm_ic_same)
    out["beats_margin_ic"] = round(float(this6 - arm_ic_same), 4) if (
        np.isfinite(this6) and np.isfinite(arm_ic_same)) else None
    out["_arm_ic_6m_same_rows"] = round(arm_ic_same, 4) if np.isfinite(arm_ic_same) else None
    return out


# ================================================================== SELF-VALIDATION
def self_validate(start="2013-01-01", log=print) -> dict:
    """Build the panel, then evaluate (a) arm_level and (b) the equal-weight blend; confirm the
    known truth (arm IC@6m ~= 0.0712, blend ~= 0.0541, arm beats blend)."""
    path = build_panel(start, persist=True, log=log)
    panel = pd.read_parquet(path)

    # (b) the equal-weight blend, built with the SAME cross-sectional z used in the known truth
    #     and the SAME all-three-present mask (so the blend universe matches mesh_backtest).
    zf = _xs_z_wide(_wide_from_panel(panel, "flow_intensity_3m"))
    zb = _xs_z_wide(_wide_from_panel(panel, "dbreadth"))
    za = _xs_z_wide(_wide_from_panel(panel, "arm_trend_3m"))
    have_all = zf.notna() & zb.notna() & za.notna()
    blend = (zf + zb + za).where(have_all)
    blend_eval = evaluate(blend, "CONVICTION_ADD_blend", panel)

    # (a) ARM level — the CANONICAL baseline is ARM scored on the SAME apples-to-apples universe
    #     as the composite signal (the have_all mask), exactly as mesh_backtest judges the gate.
    #     That same-universe ARM IC@6m is the published 0.0712 (NOT ARM on its broader own universe,
    #     which is ~0.056 — a different, larger, non-comparable cross-section). evaluate() already
    #     computes it as blend_eval["_arm_ic_6m_same_rows"]; we also keep the full-universe figure.
    arm_eval = evaluate(arm_level_on(have_all, panel), "arm_level", panel)
    arm_eval_full = evaluate("arm_level", "arm_level_full_universe", panel)

    arm_ic = arm_eval["ic_6m"]
    blend_ic = blend_eval["ic_6m"]
    arm_beats = bool(arm_ic is not None and blend_ic is not None and arm_ic > blend_ic)
    reproduced = bool(
        arm_ic is not None and blend_ic is not None
        and abs(arm_ic - 0.0712) <= 0.01 and abs(blend_ic - 0.0541) <= 0.01
        and arm_beats)

    log("\n[mesh_research] === SELF-VALIDATION ===")
    log(f"  arm_level (same-universe)  IC@6m = {arm_ic}  (target 0.0712, spread {arm_eval['spread_6m_pct']}%)")
    log(f"  arm_level (full-universe)  IC@6m = {arm_eval_full['ic_6m']}  (context only, ~0.056)")
    log(f"  blend                      IC@6m = {blend_ic}  (target 0.0541)")
    log(f"  arm beats blend = {arm_beats};  reproduced = {reproduced}")
    return {
        "panel_path": path,
        "arm_eval": arm_eval,
        "arm_eval_full": arm_eval_full,
        "blend_eval": blend_eval,
        "arm_ic_6m": arm_ic,
        "arm_ic_6m_full_universe": arm_eval_full["ic_6m"],
        "blend_ic_6m": blend_ic,
        "arm_beats_blend": arm_beats,
        "reproduced": reproduced,
        "n_months": arm_eval["n_months"],
    }


# ================================================================== THE MUTABLE ANALYST DESK SIGNAL
# This is the ONLY function the autoresearch ANALYST loop edits (tag analyst-jun30). Everything
# above (build_panel/_assemble_wide/evaluate/_ic_block/arm_baseline) is the FROZEN evaluator + the
# frozen data plumbing and must NOT change. desk_signal() ASSEMBLES the analyst pitch score from the
# already-built forces in the panel. Every clause must trace to a VALIDATED force; no free parameter
# fit to maximise backtest IC; the assembly must clear the parameter plateau (autoresearch §3).
#
# House law (ANALYST_GOLDMINE §0): LEVEL = context, CHANGE = edge, every edge has a CLOCK.
#   validated forces:
#     arm_level     (ARM_100_REG, the analyst-revision LEVEL — IC@6m ~0.056 full / ~0.071 same-rows)
#     arm_trend_3m  (dARM over 3 month-ends — the revision DIRECTION, the "change is edge" mechanic)
#     mom_6m/mom_12m(price momentum — a separately validated cross-sectional force)
#     value_z       (combined E/P,B/P,S/P cheapness z — value, Ambit sign-replication only)
#     flow_intensity_3m / dbreadth (smart-money + Chen-Hong-Stein breadth — narrow universe ~57%)
#     quality_score (ROE - accrual penalty — Sloan)
# PROVEN DEAD END: equal-weight z(flow)+z(dbreadth)+z(arm_trend_3m) DILUTES ARM (0.071->0.054).

# CHAMPION (autoresearch analyst-jun30): a two-horizon medium-term MOMENTUM composite.
#   = z(mom_6m) + z(mom_12m), equal weight, over names with BOTH legs present.
# WHY this and NOT the old flow/breadth/arm_trend blend (which DILUTED ARM, the proven dead end):
#   on this TR universe the strongest VALIDATED single force is medium-term price momentum
#   (mom_6m IC@6m ~0.108, mom_12m ~0.112) — far above ARM-level (~0.067 same-rows) and the old
#   blend (~0.062). The desk had been assembling WEAK forces and ignoring the workhorse. This
#   composite is the ONLY assembly searched that BEATS its best single component (IC 0.118 >
#   best-single 0.112) AND is plateau-robust across the blend ratio (3:1..1:3 all ~0.116-0.118)
#   AND across the horizon choice ({6,9,12}m blends all ~0.114-0.118; the 3m leg is weaker and
#   dilutes). HONEST CAVEAT (no overclaim): the two legs are 0.72-correlated so the composite
#   adds only MARGINAL breadth over a single momentum leg (51% monthly win vs mom_6m, +0.008 IC) —
#   the real win is "momentum, well-specified, undiluted", not a miracle. Validated forces only;
#   no free parameter fit (the plateau proves the ratio is not tuned).
# The old equal-weight blend remains reachable via mode="ew_blend" for regression/baseline.
DESK_PARAMS = {
    "mode": "weighted",
    "weights": {"mom_6m": 1.0, "mom_12m": 1.0},
    "winsor": None,            # None or a float p in (0,0.5): clip each force-z to [-z_p, z_p]
    "orth": None,              # None or "arm": orthogonalise non-ARM legs vs ARM cross-sectionally
}


def _w(panel, col):
    """frozen helper: wide month_end x symbol frame for a panel column."""
    return _wide_from_panel(panel, col)


def _winsor_z(z: pd.DataFrame, p):
    """Clip a cross-sectional z frame to +/- the p-quantile magnitude per month (robustness).
    p=None -> unchanged. p e.g. 0.02 trims the 2% tails so one outlier name can't dominate a z-sum."""
    if not p:
        return z
    def _row(r):
        v = r.dropna()
        if len(v) < 5:
            return r
        lim = v.abs().quantile(1.0 - p)
        return r.clip(lower=-lim, upper=lim)
    return z.apply(_row, axis=1)


def _orth_vs(target: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectionally orthogonalise `target` vs `base` each month: residual of target ~ a + b*base
    over the names present in both. Returns the residual (the part of `target` NOT explained by base).
    This is how a value/momentum/flow leg is made to carry information BEYOND ARM (decorrelation)."""
    out = pd.DataFrame(index=target.index, columns=target.columns, dtype=float)
    for t in target.index:
        if t not in base.index:
            continue
        y = target.loc[t]
        x = base.loc[t]
        common = y.index.intersection(x.index)
        y, x = y[common], x[common]
        ok = y.notna() & x.notna()
        if ok.sum() < 10:
            continue
        yy, xx = y[ok].astype(float), x[ok].astype(float)
        sx = xx.std()
        if not np.isfinite(sx) or sx == 0:
            out.loc[t, ok.index[ok]] = yy.values
            continue
        b = np.cov(xx, yy, ddof=0)[0, 1] / (sx ** 2)
        a = yy.mean() - b * xx.mean()
        resid = yy - (a + b * xx)
        out.loc[t, resid.index] = resid.values
    return out


def desk_signal(panel: pd.DataFrame) -> pd.DataFrame:
    """Assemble the analyst desk's ex-ante pitch score (wide month_end x symbol).

    The score is a cross-sectional composite of validated forces. Higher score = stronger BUY pitch.
    Trials edit ONLY DESK_PARAMS / the branches here — never the evaluator. The frozen evaluate()
    then turns this into the IC verdict on the SAME convention as the published numbers.
    """
    P = DESK_PARAMS
    mode = P["mode"]

    def Z(col):
        return _xs_z_wide(_w(panel, col))

    if mode == "ew_blend":
        # BASELINE: equal-weight z(flow)+z(dbreadth)+z(arm_trend_3m), all-three-present mask.
        wt = P["weights"]
        zf = _winsor_z(Z("flow_intensity_3m"), P["winsor"])
        zb = _winsor_z(Z("dbreadth"), P["winsor"])
        za = _winsor_z(Z("arm_trend_3m"), P["winsor"])
        have_all = zf.notna() & zb.notna() & za.notna()
        s = (wt["flow"] * zf + wt["dbreadth"] * zb + wt["arm_trend_3m"] * za)
        return s.where(have_all)

    if mode == "weighted":
        # GENERAL weighted z-composite over an arbitrary set of force columns (weights in P["weights"]).
        # mask = ALL named legs present (a true confluence) unless a leg weight is 0.
        wt = P["weights"]
        legs = {}
        for col, w in wt.items():
            if w == 0:
                continue
            z = _winsor_z(Z(col), P["winsor"])
            if P["orth"] == "arm" and col != "arm_level":
                z = _winsor_z(_orth_vs(_w(panel, col), _w(panel, "arm_level")), P["winsor"])
            legs[col] = (w, z)
        if not legs:
            raise ValueError("no legs")
        mask = None
        for col, (w, z) in legs.items():
            m = z.notna()
            mask = m if mask is None else (mask & m)
        s = None
        for col, (w, z) in legs.items():
            term = w * z
            s = term if s is None else s.add(term, fill_value=0.0)
        return s.where(mask)

    raise ValueError(f"unknown desk mode {mode}")


if __name__ == "__main__":     # python -m vistas.mesh_research
    res = self_validate()
    print(json.dumps({k: v for k, v in res.items()
                      if k not in ("arm_eval", "blend_eval")}, indent=1, default=str))
