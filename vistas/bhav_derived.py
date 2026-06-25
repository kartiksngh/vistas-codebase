"""
Market internals — BREADTH, RANGE-VOLATILITY and LIQUIDITY — from the already-cached
NSE bhavcopy OHLCV store. PURE COMPUTE: no network, no NSE calls; every series here is
reconstructed locally from `vistas/bhav_prices.load_ohlcv()` (the raw daily exchange
record: open/high/low/close, prev-close, volume, turnover, trade-count, vwap) and the
clean split-continuous total-return panel `vistas/stocks.load()`.

WHY THIS EXISTS (first principles)
----------------------------------
A single index level (NIFTY) hides *how many* stocks are actually participating. On a
day the index is flat, the market underneath can be quietly broadening (most names up)
or rotting (a few mega-caps holding up a sea of decliners). The classic cure is
"market breadth": count the advancers vs decliners, the advance/decline line, the share
of names above their long moving averages, the new-highs vs new-lows. Add the
cross-sectional spread of returns (dispersion) and range-based volatility (how wide the
daily candles are) and you can read fear/greed and stock-pickers'-vs-macro regimes
straight off the tape. Liquidity (total turnover, Amihud illiquidity, average trade
size) tells you whether moves are real or thin.

The honest way to build these is from the raw exchange file, one cross-section per day —
which we already have cached as parquet. So this module costs ZERO network and is fully
reproducible to the row.

CONVENTIONS (so every number is reproducible)
---------------------------------------------
  * "Liquid universe" per day = traded names with same-session close >= Rs 5 AND a
    finite previous close (so we drop penny noise and rows we can't return-compute).
    A LIQUID_MIN constant lets the lead retune it; the default 5 matches the spec.
  * DAILY RETURN for advance/decline = close / prevclose - 1, computed PER ROW from the
    bhavcopy's own published prev-close. On an ex-split / ex-bonus day NSE adjusts the
    prevclose, so this same-session ratio is already split-clean — no corporate-action
    engine needed for breadth. (For the 50/200-DMA and 52-week high/low breadth we use
    the split-CONTINUOUS adjusted panel `stocks.load()`, because a raw split jump would
    otherwise fake a moving-average cross or a new low.)
  * RANGE VOL is annualised with sqrt(252) (252 NSE trading days / year), cross-sectional
    MEDIAN across the liquid universe each day (median, not mean, so one blown-up penny
    candle can't dominate). Parkinson and Garman-Klass are the standard high/low(/open/
    close) volatility estimators; realized close-to-close is |ln(close/prevclose)| put on
    the same annual scale via *sqrt(252) (a 1-day proxy, median across names).
  * LIQUIDITY: total turnover summed across the liquid universe (Rs crore = Rs / 1e7);
    Amihud illiquidity = |daily return| / turnover_in_Rs_cr (median across names; the
    classic price-impact-per-rupee measure — higher = more illiquid); average trade size
    = turnover / trades (median; only meaningful 2011+ when the trade-count column exists).

Exposes the three frames the lead asked for — breadth(), rangevol(), liquidity() — plus
the uniform fetch_series(start, log) that flattens all of them into {name: Series} +
meta, all tagged group "Market internals".

Provenance: reads only this project's local parquet/CSV; touches no shared module and no
network. The breadth/vol/liquidity formulae are textbook (Parkinson 1980, Garman-Klass
1980, Amihud 2002) re-derived locally.
"""
from __future__ import annotations

import math
import datetime as dt

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- knobs
LIQUID_MIN = 5.0           # min same-session close (Rs) to count a name as non-penny
PPY = 252                  # NSE trading days per year (annualisation factor)
DMA_SHORT = 50             # short moving-average window (trading days)
DMA_LONG = 200             # long moving-average window
HL_WINDOW = 252            # ~52-week window for new highs / new lows
_SQRT_PPY = math.sqrt(PPY)
_PARK_K = 1.0 / (4.0 * math.log(2.0))     # Parkinson 1/(4 ln2) constant

_OHLCV_COLS = ["date", "sym", "series", "close", "high", "low",
               "prevclose", "volume", "turnover", "trades", "vwap"]

# in-process caches (the parquet read is ~9.4M rows; recomputing per call is wasteful)
_CACHE: dict = {}


# --------------------------------------------------------------------------- loaders
def _load_ohlcv() -> pd.DataFrame:
    """Long-form RAW OHLCV (one row per symbol-day, EQ board preferred). Cached. Returns
    an empty frame if the parquet store is missing (graceful-degrade)."""
    if "ohlcv" in _CACHE:
        return _CACHE["ohlcv"]
    try:
        from . import bhav_prices as bp
        df = bp.load_ohlcv(columns=_OHLCV_COLS)
    except Exception:
        df = pd.DataFrame(columns=_OHLCV_COLS)
    if len(df):
        df["date"] = pd.to_datetime(df["date"])
        for c in ("close", "high", "low", "prevclose", "volume", "turnover",
                  "trades", "vwap"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
    _CACHE["ohlcv"] = df
    return df


def _load_tr_panel() -> pd.DataFrame:
    """Wide split-continuous adjusted close panel (Date x SYMBOL) from stocks.load().
    Cached. Empty frame if unavailable."""
    if "tr" in _CACHE:
        return _CACHE["tr"]
    try:
        from . import stocks
        tr = stocks.load()
    except Exception:
        tr = pd.DataFrame()
    if tr is None:
        tr = pd.DataFrame()
    _CACHE["tr"] = tr
    return tr


def _clip(df: pd.DataFrame, start) -> pd.DataFrame:
    if start is not None and len(df):
        df = df[df.index >= pd.Timestamp(start)]
    return df


# --------------------------------------------------------------------------- BREADTH
def breadth(start=None) -> pd.DataFrame:
    """Daily cross-sectional MARKET BREADTH (one row per NSE trading day):

      advances           # liquid names UP on the day (close/prevclose-1 > 0)
      declines           # liquid names DOWN on the day
      unchanged          # liquid names flat
      ad_ratio           advances / declines (advance-decline ratio; >1 = more up)
      ad_line            cumulative (advances - declines)  [the A/D line]
      pct_up             100 * advances / (advances+declines+unchanged)  [% up on the day]
      pct_above_50dma    100 * (names with adj close > own 50-day MA) / names with a 50-DMA
      pct_above_200dma   same vs the 200-day MA
      new_high_52w       names whose adj close == its trailing-252-day max (new 52w high)
      new_low_52w        names whose adj close == its trailing-252-day min (new 52w low)
      net_new_high_pct   100 * (new_high - new_low) / (names with a full 52w window)

    Advances/declines/unchanged + the A/D line use the bhavcopy's OWN prev-close ratio
    (split-clean per session). The moving-average and 52-week-extreme breadth use the
    split-CONTINUOUS adjusted panel (stocks.load()) so a raw split jump can't fake a
    cross or a new low. A name counts only if its same-session close >= LIQUID_MIN (Rs 5)
    and it has a finite prev-close.
    """
    if "breadth" in _CACHE:
        return _clip(_CACHE["breadth"], start)

    df = _load_ohlcv()
    out = pd.DataFrame()
    if len(df):
        liq = df[(df["close"] >= LIQUID_MIN) & df["prevclose"].notna()
                 & (df["prevclose"] > 0)].copy()
        liq["ret"] = liq["close"] / liq["prevclose"] - 1.0
        g = liq.groupby("date")
        adv = g["ret"].apply(lambda r: int((r > 0).sum()))
        dec = g["ret"].apply(lambda r: int((r < 0).sum()))
        unch = g["ret"].apply(lambda r: int((r == 0).sum()))
        out = pd.DataFrame({"advances": adv, "declines": dec, "unchanged": unch})
        out = out.sort_index()
        tot = out["advances"] + out["declines"] + out["unchanged"]
        out["ad_ratio"] = out["advances"] / out["declines"].replace(0, np.nan)
        out["ad_line"] = (out["advances"] - out["declines"]).cumsum()
        out["pct_up"] = 100.0 * out["advances"] / tot.replace(0, np.nan)

    # ---- moving-average + 52-week-extreme breadth from the split-continuous panel ----
    tr = _load_tr_panel()
    if len(tr):
        tr = tr.sort_index()
        ma50 = tr.rolling(DMA_SHORT, min_periods=DMA_SHORT).mean()
        ma200 = tr.rolling(DMA_LONG, min_periods=DMA_LONG).mean()
        above50 = (tr > ma50)
        above200 = (tr > ma200)
        # denominators = names that actually HAVE a 50/200-DMA + a price that day
        have50 = ma50.notna() & tr.notna()
        have200 = ma200.notna() & tr.notna()
        pct50 = 100.0 * above50.where(have50).sum(axis=1) / have50.sum(axis=1).replace(0, np.nan)
        pct200 = 100.0 * above200.where(have200).sum(axis=1) / have200.sum(axis=1).replace(0, np.nan)

        roll_max = tr.rolling(HL_WINDOW, min_periods=HL_WINDOW).max()
        roll_min = tr.rolling(HL_WINDOW, min_periods=HL_WINDOW).min()
        have_hl = roll_max.notna() & tr.notna()
        new_hi = (tr >= roll_max) & have_hl
        new_lo = (tr <= roll_min) & have_hl
        nh = new_hi.sum(axis=1)
        nl = new_lo.sum(axis=1)
        denom_hl = have_hl.sum(axis=1).replace(0, np.nan)
        ma_df = pd.DataFrame({
            "pct_above_50dma": pct50,
            "pct_above_200dma": pct200,
            "new_high_52w": nh.astype("float64"),
            "new_low_52w": nl.astype("float64"),
            "net_new_high_pct": 100.0 * (nh - nl) / denom_hl,
        })
        out = ma_df if not len(out) else out.join(ma_df, how="outer")

    out = out.sort_index() if len(out) else out
    _CACHE["breadth"] = out
    return _clip(out, start)


# --------------------------------------------------------------------------- RANGE VOL
def rangevol(start=None) -> pd.DataFrame:
    """Daily cross-sectional RANGE / DISPERSION volatility (one row per trading day):

      parkinson_vol     median across liquid names of the Parkinson high/low vol estimate,
                        annualised: sqrt( (1/(4 ln2)) * ln(H/L)^2 ) * sqrt(252)
      garman_klass_vol  median Garman-Klass estimate (uses O,H,L,C),
                        sqrt( 0.5 ln(H/L)^2 - (2 ln2 - 1) ln(C/O)^2 ) * sqrt(252)
      realized_cc_vol   median realized close-to-close: |ln(close/prevclose)| * sqrt(252)
      return_dispersion cross-sectional STDEV of the day's simple returns (close/prevclose-1)
                        across the liquid universe  [how far apart stock moves were]

    Median (not mean) is used across names so a single blown-up candle can't dominate the
    reading. Parkinson/Garman-Klass need the day's OPEN too; rows missing O/H/L are
    skipped for those estimators but still feed realized_cc_vol and dispersion.
    """
    if "rangevol" in _CACHE:
        return _clip(_CACHE["rangevol"], start)
    df = _load_ohlcv()
    if not len(df):
        _CACHE["rangevol"] = pd.DataFrame()
        return pd.DataFrame()

    liq = df[(df["close"] >= LIQUID_MIN) & df["prevclose"].notna()
             & (df["prevclose"] > 0)].copy()
    # bring in OPEN (was dropped from _OHLCV_COLS to save memory) only if available
    if "open" not in liq.columns:
        try:
            from . import bhav_prices as bp
            op = bp.load_ohlcv(columns=["date", "sym", "series", "open"])
            op["date"] = pd.to_datetime(op["date"])
            liq = liq.merge(op, on=["date", "sym", "series"], how="left")
        except Exception:
            liq["open"] = np.nan

    with np.errstate(divide="ignore", invalid="ignore"):
        hl = np.log(liq["high"] / liq["low"])
        liq["_park"] = np.sqrt(_PARK_K * hl ** 2) * _SQRT_PPY
        co = np.log(liq["close"] / liq["open"])
        gk_var = 0.5 * hl ** 2 - (2.0 * math.log(2.0) - 1.0) * co ** 2
        liq["_gk"] = np.sqrt(gk_var.clip(lower=0)) * _SQRT_PPY
        liq["_cc"] = np.abs(np.log(liq["close"] / liq["prevclose"])) * _SQRT_PPY
        liq["ret"] = liq["close"] / liq["prevclose"] - 1.0

    # invalid range rows (H<=0, L<=0, H<L) -> NaN so the median ignores them
    bad = (liq["high"] <= 0) | (liq["low"] <= 0) | (liq["high"] < liq["low"])
    liq.loc[bad, ["_park", "_gk"]] = np.nan
    liq.loc[(liq["open"] <= 0) | liq["open"].isna(), "_gk"] = np.nan

    g = liq.groupby("date")
    out = pd.DataFrame({
        "parkinson_vol": g["_park"].median(),
        "garman_klass_vol": g["_gk"].median(),
        "realized_cc_vol": g["_cc"].median(),
        "return_dispersion": g["ret"].std(),
    }).sort_index()
    _CACHE["rangevol"] = out
    return _clip(out, start)


# --------------------------------------------------------------------------- LIQUIDITY
def liquidity(start=None) -> pd.DataFrame:
    """Daily cross-sectional LIQUIDITY (one row per trading day):

      total_turnover_cr    sum of the liquid universe's turnover, in Rs crore (Rs / 1e7)
      vwap_close_gap_pct   median 100 * (close - vwap) / vwap across names  [where the close
                           sat vs the volume-weighted average price — buying vs selling
                           pressure into the close]
      amihud_illiq_median  median of |daily return| / turnover_in_Rs_cr  [Amihud 2002 price
                           impact per crore traded; higher = thinner / more illiquid]
      avg_trade_size_cr    median (turnover / trades) in Rs crore  [average rupee size of a
                           single trade; only finite 2011+ when the trade-count exists]

    Liquid universe = same-session close >= LIQUID_MIN and finite prev-close (as elsewhere).
    Amihud is in units of (return per Rs crore) so it's comparable day to day.
    """
    if "liquidity" in _CACHE:
        return _clip(_CACHE["liquidity"], start)
    df = _load_ohlcv()
    if not len(df):
        _CACHE["liquidity"] = pd.DataFrame()
        return pd.DataFrame()

    liq = df[(df["close"] >= LIQUID_MIN) & df["prevclose"].notna()
             & (df["prevclose"] > 0)].copy()
    liq["ret"] = liq["close"] / liq["prevclose"] - 1.0
    liq["turn_cr"] = liq["turnover"] / 1e7                      # Rs -> Rs crore
    with np.errstate(divide="ignore", invalid="ignore"):
        liq["amihud"] = np.abs(liq["ret"]) / liq["turn_cr"].replace(0, np.nan)
        liq["vwap_gap"] = 100.0 * (liq["close"] - liq["vwap"]) / liq["vwap"].replace(0, np.nan)
        ats = liq["turnover"] / liq["trades"].replace(0, np.nan)   # avg trade size, Rs
        liq["ats_cr"] = ats / 1e7
    liq.loc[~np.isfinite(liq["amihud"]), "amihud"] = np.nan
    liq.loc[~np.isfinite(liq["vwap_gap"]), "vwap_gap"] = np.nan
    liq.loc[~np.isfinite(liq["ats_cr"]), "ats_cr"] = np.nan

    g = liq.groupby("date")
    out = pd.DataFrame({
        "total_turnover_cr": g["turn_cr"].sum(),
        "vwap_close_gap_pct": g["vwap_gap"].median(),
        "amihud_illiq_median": g["amihud"].median(),
        "avg_trade_size_cr": g["ats_cr"].median(),
    }).sort_index()
    _CACHE["liquidity"] = out
    return _clip(out, start)


# --------------------------------------------------------------------------- McCLELLAN
def mcclellan(start=None) -> pd.DataFrame:
    """McClellan breadth-MOMENTUM / cycle (one row per trading day):

      mcclellan_osc        McClellan Oscillator = EMA19(RANA) - EMA39(RANA)
      mcclellan_summation  McClellan Summation Index = running cumulative sum of the Oscillator

    where RANA = "ratio-adjusted net advances" = 1000 * (advances - declines) /
    (advances + declines). The ratio adjustment is what makes a 25-year panel comparable
    era-to-era: our traded universe grows from a few hundred names to ~2000, so a RAW
    advances-minus-declines would balloon just because more stocks list. Dividing by the
    total participants normalises every day to [-1000, +1000]. The two exponential moving
    averages use the classic McClellan spans (19 and 39 trading days; smoothing 2/(n+1) =
    0.10 and 0.05), computed recursively (adjust=False) so the value at each date depends
    only on the past — no look-ahead.

    READ: Oscillator > 0 and rising = breadth is broadening to the upside (a thrust);
    < 0 = broad-based weakness; a DIVERGENCE (index makes a new high while the Oscillator
    makes a lower high) is the classic breadth-exhaustion warning. The Summation Index is
    the slow cumulative cycle gauge — its level and turning points frame where we sit in
    the breadth cycle. Both are derived purely from the advances/declines already counted
    in breadth() (bhavcopy own-prevclose ratio), so they cost zero extra network.
    """
    if "mcclellan" in _CACHE:
        return _clip(_CACHE["mcclellan"], start)
    b = breadth()                       # full history; `start` is applied at the very end
    out = pd.DataFrame()
    if len(b) and {"advances", "declines"}.issubset(b.columns):
        adv = pd.to_numeric(b["advances"], errors="coerce").astype("float64")
        dec = pd.to_numeric(b["declines"], errors="coerce").astype("float64")
        denom = (adv + dec).replace(0, np.nan)
        rana = (1000.0 * (adv - dec) / denom).dropna().sort_index()
        if len(rana):
            osc = rana.ewm(span=19, adjust=False).mean() - rana.ewm(span=39, adjust=False).mean()
            out = pd.DataFrame({
                "mcclellan_osc": osc,
                "mcclellan_summation": osc.cumsum(),    # anchored at inception, then clipped
            }).sort_index()
    _CACHE["mcclellan"] = out
    return _clip(out, start)


# --------------------------------------------------------------------------- uniform API
# friendly display name + (unit, freq) for every column the three frames emit
_SPEC = {
    # breadth
    "advances":           ("Advances (liquid names up on the day)",          "count"),
    "declines":           ("Declines (liquid names down on the day)",        "count"),
    "unchanged":          ("Unchanged (liquid names flat)",                  "count"),
    "ad_ratio":           ("Advance/Decline ratio",                          "ratio"),
    "ad_line":            ("Advance/Decline line (cumulative adv-dec)",      "index"),
    "pct_up":             ("% of names up on the day",                       "%"),
    "pct_above_50dma":    ("% of names above their 50-DMA",                  "%"),
    "pct_above_200dma":   ("% of names above their 200-DMA",                 "%"),
    "new_high_52w":       ("New 52-week highs",                              "count"),
    "new_low_52w":        ("New 52-week lows",                               "count"),
    "net_new_high_pct":   ("Net new-high index ((NH-NL)/total %)",          "%"),
    # range-vol
    "parkinson_vol":      ("Parkinson range vol (median, annualised)",       "ann. vol"),
    "garman_klass_vol":   ("Garman-Klass range vol (median, annualised)",    "ann. vol"),
    "realized_cc_vol":    ("Realized close-to-close vol (median, ann.)",     "ann. vol"),
    "return_dispersion":  ("Cross-sectional return dispersion (stdev)",      "stdev"),
    # liquidity
    "total_turnover_cr":  ("Total market turnover (Rs cr)",                  "Rs cr"),
    "vwap_close_gap_pct": ("Median VWAP-vs-close gap",                       "%"),
    "amihud_illiq_median":("Amihud illiquidity (median)",                    "ret / Rs cr"),
    "avg_trade_size_cr":  ("Median average trade size (Rs cr)",              "Rs cr"),
    # mcclellan (breadth momentum / cycle)
    "mcclellan_osc":      ("McClellan Oscillator (EMA19-EMA39 of RANA)",     "breadth osc"),
    "mcclellan_summation":("McClellan Summation Index (cum. oscillator)",     "index"),
}
_FREQ = "daily"
_SOURCE = "NSE bhavcopy (derived, local)"
_GROUP = "Market internals"


def fetch_series(start="2000-01-01", log=print):
    """Uniform entry point for the lead's wiring layer. Flattens breadth(), rangevol() and
    liquidity() into ({friendly_name: pandas.Series}, {friendly_name: meta}). Pure compute
    (no network): on a missing parquet store it logs and returns ({}, {}) — never raises.

    Returns
    -------
    (series_by_name, meta_by_name) where each Series is date-indexed float, and
    meta_by_name[name] = {"unit","freq":"daily","source","group":"Market internals"}.
    """
    series, meta = {}, {}
    try:
        frames = [breadth(start), rangevol(start), liquidity(start), mcclellan(start)]
    except Exception as e:                          # graceful-degrade
        log(f"[bhav_derived] compute failed: {e}")
        return {}, {}

    n_days = 0
    for fr in frames:
        if fr is None or not len(fr):
            continue
        n_days = max(n_days, len(fr))
        for col in fr.columns:
            disp, unit = _SPEC.get(col, (col, ""))
            s = pd.to_numeric(fr[col], errors="coerce").dropna()
            if not len(s):
                continue
            s.index = pd.DatetimeIndex(s.index)
            s.name = disp
            series[disp] = s.sort_index()
            meta[disp] = {"unit": unit, "freq": _FREQ, "source": _SOURCE, "group": _GROUP}

    log(f"[bhav_derived] {len(series)} market-internals series over {n_days} trading days "
        f"(pure compute, no network).")
    return series, meta


if __name__ == "__main__":
    s, m = fetch_series(log=lambda x: print(x, flush=True))
    for nm in list(s)[:30]:
        ser = s[nm]
        print(f"  {nm:48s} {ser.index[0].date()}..{ser.index[-1].date()}  n={len(ser)}  "
              f"last={ser.iloc[-1]:.4g}  [{m[nm]['unit']}]")
