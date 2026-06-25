"""
Price store for Vistas.

Loads the newest "Indices Data TR till *.csv" snapshot from ./data, applies the
SAME load-time quality filter the research engine uses (drop days where >=25% of
available indices are unchanged vs the prior day — vendor-stale / non-trading
days; `strategy/fft_strategy_v1.py` load_data:132-134), caches the result in
memory, and serves windowed daily price-level slices to the analytics layer.

Self-contained: this reads only files inside the Vistas folder; it does not
import the research project.
"""
from __future__ import annotations

import os
import glob
import re
import threading

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))

_LOCK = threading.Lock()
_CACHE: dict = {}     # measure -> {"path","df","asof","mtime"}

# Each measure's NATURE drives which analytics apply downstream:
#   level : a compounding price level — rebase / CAGR / vol / Sharpe / alpha / drawdown
#           are all valid (Total Return, Price Return, Net Total Return).
#   ratio : a valuation ratio, higher = dearer (P/E, P/B) — read as level / historical
#           percentile / z-score / bands; CAGR & Sharpe are MEANINGLESS on it.
#   yield : an income yield, higher = cheaper (Dividend Yield) — same family as ratio,
#           opposite polarity.
MEASURE_KIND = {"TR": "level", "PR": "level", "NTR": "level",
                "PE": "ratio", "PB": "ratio", "DY": "yield"}
DEFAULT_MEASURE = "TR"


def kind(measure: str = DEFAULT_MEASURE) -> str:
    return MEASURE_KIND.get(measure, "level")


def measures_present() -> list:
    """Measures that currently have a snapshot CSV on disk (TR is always expected)."""
    return [m for m in MEASURE_KIND if latest_csv(m) is not None]


def latest_csv(measure: str = DEFAULT_MEASURE, base: str = DATA_DIR):
    """Newest 'Indices Data <measure> till <date>.csv' by the date in the filename
    (mtime fallback) — mirrors the engine's _latest_tr_csv, per measure."""
    cands = glob.glob(os.path.join(base, f"Indices Data {measure} till *.csv"))
    if not cands:
        return None

    def _key(p):
        m = re.search(r"till (.+)\.csv$", os.path.basename(p))
        d = pd.to_datetime(m.group(1).strip(), errors="coerce") if m else None
        return d if pd.notna(d) else pd.Timestamp(os.path.getmtime(p), unit="s")

    return max(cands, key=_key)


def _stale_filter(idx: pd.DataFrame) -> pd.DataFrame:
    """Drop days where >=25% of available indices are unchanged vs the prior day.
    Verbatim port of the engine's vendor-stale-day filter (load_data:132-134)."""
    if len(idx) < 2:
        return idx
    rep = (((idx / idx.shift(1) - 1) == 0).sum(axis=1)
           / (len(idx.columns) - idx.isna().sum(axis=1)).replace(0, np.nan))
    return idx.loc[rep.fillna(0) < 0.25]


# Earliest history Vistas will surface. The research engine hard-cuts at
# 2005-04-01 (where its broad universe begins); Vistas is a general viewer, so it
# keeps everything from 2000 onward (the data was freshly fetched from 2000) — a
# few indices (NIFTY 50/500/BANK/FMCG) have history back to 2000-01.
HISTORY_FLOOR = pd.Timestamp("2000-01-01")


# Coverage gate: a publishable index must be a CONTINUOUS, regular history. A
# vendor export that only carries (say) one month per year would otherwise render a
# full, professional-looking but FABRICATED analytics page. Every real NSE index
# here has >=247 obs/yr and a max internal gap <=4 trading rows; the only offender
# (NIFTY100 ESG SECTOR LEADERS = May-only snapshots, ~23 obs/yr, 232-row gap) is
# excluded by a wide margin. Also protects the position-based rolling windows
# (shift of N observations), which only mean "1 year" for ~daily-dense series.
MIN_OBS_PER_YEAR = 150
MAX_INTERNAL_GAP_ROWS = 25


def _coverage_gate(idx: pd.DataFrame) -> pd.DataFrame:
    """Drop columns that aren't a continuous, regular daily history."""
    keep, dropped = [], []
    for c in idx.columns:
        s = idx[c].dropna()
        if len(s) < 2:
            dropped.append((c, "too few obs")); continue
        yrs = (s.index[-1] - s.index[0]).days / 365.25
        opy = len(s) / yrs if yrs > 0 else 0.0
        pos = idx.index.get_indexer(s.index)
        gap = int(np.diff(pos).max()) if len(pos) > 1 else 0
        if opy < MIN_OBS_PER_YEAR or gap > MAX_INTERNAL_GAP_ROWS:
            dropped.append((c, f"{opy:.0f} obs/yr, max gap {gap} rows"))
        else:
            keep.append(c)
    if dropped:
        print("[vistas] coverage gate excluded non-continuous series: "
              + "; ".join(f"{c} ({why})" for c, why in dropped))
    return idx[keep]


def _load(path: str, measure: str = DEFAULT_MEASURE) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", format="%Y-%m-%d")
    df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
    df = df[df.index >= HISTORY_FLOOR]
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.dropna(axis=1, how="all")
    if MEASURE_KIND.get(measure, "level") == "level":
        df = _stale_filter(df)        # the >=25%-unchanged filter is calibrated for PRICES;
                                      # a valuation ratio being flat day-to-day isn't "stale".
    df = _coverage_gate(df)
    return df


def load(measure: str = DEFAULT_MEASURE, force: bool = False) -> pd.DataFrame:
    """Return the cached daily frame for `measure` (default TR), (re)loading if the
    newest CSV for that measure changed on disk or `force` is set. Each measure is
    cached independently."""
    with _LOCK:
        path = latest_csv(measure)
        if path is None:
            raise FileNotFoundError(
                f"No 'Indices Data {measure} till *.csv' found in {DATA_DIR}. "
                "Drop a snapshot there or run a data refresh / build_measures.")
        mtime = os.path.getmtime(path)
        c = _CACHE.get(measure)
        if force or c is None or c["path"] != path or c["mtime"] != mtime:
            df = _load(path, measure)
            m = re.search(r"till (.+)\.csv$", os.path.basename(path))
            asof = m.group(1).strip() if m else df.index[-1].strftime("%Y-%m-%d")
            _CACHE[measure] = {"path": path, "df": df, "asof": asof, "mtime": mtime}
        return _CACHE[measure]["df"]


def reload(measure: str = DEFAULT_MEASURE) -> pd.DataFrame:
    """Force a reload (e.g. after a data refresh wrote a newer CSV)."""
    return load(measure, force=True)


def asof(measure: str = DEFAULT_MEASURE) -> str:
    load(measure)
    return _CACHE[measure]["asof"]


def source_filename(measure: str = DEFAULT_MEASURE) -> str:
    load(measure)
    c = _CACHE.get(measure)
    return os.path.basename(c["path"]) if c and c["path"] else ""


def available(measure: str = DEFAULT_MEASURE) -> list:
    """Index columns that have history in the current snapshot for `measure`."""
    return list(load(measure).columns)


def coverage(measure: str = DEFAULT_MEASURE) -> dict:
    """{index: {start, end, n_obs}} for the picker."""
    df = load(measure)
    out = {}
    for c in df.columns:
        s = df[c].dropna()
        if len(s):
            out[c] = {"start": s.index[0].strftime("%Y-%m-%d"),
                      "end": s.index[-1].strftime("%Y-%m-%d"), "n_obs": int(len(s))}
    return out


def date_range(measure: str = DEFAULT_MEASURE) -> dict:
    df = load(measure)
    return {"start": df.index[0].strftime("%Y-%m-%d"),
            "end": df.index[-1].strftime("%Y-%m-%d")}


def get_series(tickers, measure: str = DEFAULT_MEASURE, start=None, end=None) -> pd.DataFrame:
    """Daily slice of `measure` for the requested columns over [start, end].

    Unknown columns are dropped silently; rows where every requested series is
    NaN are dropped. Each series keeps its own NaN pattern (different histories
    are not force-aligned) — the analytics layer handles pairwise alignment.
    """
    df = load(measure)
    cols = [c for c in tickers if c in df.columns]
    if not cols:
        return pd.DataFrame()
    sub = df[cols]
    if start:
        sub = sub[sub.index >= pd.Timestamp(start)]
    if end:
        sub = sub[sub.index <= pd.Timestamp(end)]
    return sub.dropna(how="all")


def get_prices(tickers, start=None, end=None) -> pd.DataFrame:
    """Back-compat: total-return price-level slice (== get_series(..., 'TR', ...))."""
    return get_series(tickers, DEFAULT_MEASURE, start, end)


def get_level_frame(names, measure: str = DEFAULT_MEASURE, start=None, end=None) -> pd.DataFrame:
    """A merged price-LEVEL frame for a mixed selection of INDICES and STOCKS, so the
    performance engine can chart e.g. RELIANCE vs NIFTY 50 like-for-like. Index columns
    come from the `measure` frame (TR/PR); stock columns from the yfinance snapshot
    (adjusted close ≈ a stock's total-return level). Unknown names are dropped."""
    from . import stocks as _stocks
    idx_avail = set(available(measure)) if measure in measures_present() else set()
    idx_cols = [n for n in names if n in idx_avail]
    parts = []
    if idx_cols:
        parts.append(get_series(idx_cols, measure, start, end))
    try:
        stk_avail = set(_stocks.available())
    except Exception:
        stk_avail = set()
    stk_cols = [n for n in names if n in stk_avail and n not in idx_cols]
    if stk_cols:
        sdf = _stocks.load()[stk_cols]
        if start:
            sdf = sdf[sdf.index >= pd.Timestamp(start)]
        if end:
            sdf = sdf[sdf.index <= pd.Timestamp(end)]
        parts.append(sdf.dropna(how="all"))
    # world / cross-asset instruments (friendly-named columns), e.g. S&P 500, Gold, USD/INR
    try:
        from . import world as _world
        w_avail = set(_world.available())
    except Exception:
        w_avail = set()
    w_cols = [n for n in names if n in w_avail and n not in idx_cols and n not in stk_cols]
    if w_cols:
        wdf = _world.load_named()[w_cols]
        if start:
            wdf = wdf[wdf.index >= pd.Timestamp(start)]
        if end:
            wdf = wdf[wdf.index <= pd.Timestamp(end)]
        parts.append(wdf.dropna(how="all"))
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, axis=1)
    return out.loc[:, ~out.columns.duplicated()].dropna(how="all")
