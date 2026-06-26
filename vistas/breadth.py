"""Market-breadth engine (DISPLAY-PLANE, price-derived, licensing-clean).

Build contract: ASSET_ALLOCATOR_BREADTH_SPEC.md (read it). This module is the W7b
deliverable: per-date breadth time-series for the whole MARKET and for EACH SECTOR,
plus a current-snapshot "screen" so the Asset-Allocator UI can filter sectors by a
user m%-threshold.

WHAT BREADTH IS (plain words). A market index is a *weighted average* of its members:
a handful of mega-caps can drag the index up while most stocks quietly bleed. Breadth
throws away the index level and counts, directly, **how many individual stocks are
doing a given thing** — making a new multi-year high, sitting above their long trend
line, in an uptrend. It answers the literal question "what % of stocks are breaking
out to multi-year highs (or down to multi-year lows), and how broad is participation?"

The metrics computed here (all on the adjusted total-return CLOSE panel `P`, one
column per stock, no returns needed because corporate actions are already baked into
the TR level):

  * pct_new_high_{1,3,5}y  — % of eligible stocks whose close TODAY is the highest
                              close over the trailing N-year window (incl. today).
  * pct_new_low_{1,3,5}y   — mirror: % at their trailing N-year LOW.
  * pct_above_200dma       — % whose close >= their 200-day simple moving average
                              (the spec's recommended HEADLINE — smoothest, every-day
                              valid, highest coincident correlation with the tape).
  * pct_above_50dma        — same with the 50-DMA (faster trend).
  * nh_minus_nl_{1,3,5}y   — pct_new_high - pct_new_low (percentage points): the net
                              new-high line, a compact "tape expanding vs distributing".
  * pct_golden_cross       — % whose 50-DMA >= their 200-DMA (Stage-2 uptrend tell).

It MIRRORS `stock_intel._market_behaviour`'s exact conventions (52w high via the
trailing window max, 50/200-DMA `px >= mean(N)`, golden cross `dma50 >= dma200`) but
vectorised at the PANEL level (every stock at once) so the single-name cockpit and the
breadth cockpit can never disagree by convention.

★ HONEST VERDICT baked into the meta caveat (the single most important instruction in
the spec): on our own Indian TR data, breadth is a STRONG COINCIDENT / participation
gauge — it tells you cleanly how broad the move you are ALREADY in is — but it is NOT a
forward allocator signal (the apparent edge dies under honest overlapping-window
significance correction and is non-monotone). So everything here is DESCRIPTIVE
market-health, never a buy/sell trigger.

★ LICENSING: price-derived only — reads the public stock TR price panel + the NSE-500
industry map (+ optional AMC-disclosed crosswalk, also public). Reads NO ARM / LSEG
StarMine data. Outputs are safe to bake into the public deck. Does NOT import
analytics.py and adds NO JS-parity obligation (display-plane: computed once in Python,
the browser only reads the JSON).

RAM discipline: resolve the universe symbol list FIRST, then read the stock CSV with
`usecols` = those symbols + Date only — never materialise the full ~4,309-wide frame.

Run standalone:  python -m vistas.breadth         (build + write data/_breadth/breadth.json + validate)
"""
from __future__ import annotations

import os
import sys
import json
import math
import glob
import datetime as _dt

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DATA_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(DATA_DIR, "_breadth")
OUT_FILE = os.path.join(OUT_DIR, "breadth.json")

# ---------------------------------------------------------------- configuration
# New-high / new-low lookback windows, in TRADING days (~252/yr). The task asks for
# 1/3/5-year toggles; we key the emitted series by the YEAR label so the JS toggle is
# {1,3,5}y. (The spec's wider 2y/ATH set is a superset; we ship the three the task names
# plus carry 'all-time' is omitted to keep the file lean — 1/3/5y is the requested set.)
WINDOWS = {"1": 252, "3": 756, "5": 1260}          # year-label -> trailing trading days
DMA_SHORT = 50
DMA_LONG = 200

# Eligibility / coverage gate (mirrors data.py's continuity gate + §1.6 of the spec) so
# a 3-month-old listing can never "make a 5-year high" and a sparse series can't pollute:
MIN_OBS_PER_YEAR = 150         # density floor (same constant as data.py)
MAX_INTERNAL_GAP_ROWS = 25     # no internal gap bigger than this many rows (same as data.py)
MIN_BREADTH_N_MARKET = 100     # a market breadth point needs >= this many eligible stocks
MIN_BREADTH_N_SECTOR = 5       # sectors are thinner -> a lower cross-section floor
THIN_SECTOR_LABEL = "(thin)"   # sectors that never clear the floor are folded here, not dropped


# ---------------------------------------------------------------- universe + sector map
def resolve_universe(extended: bool = True, log=print):
    """Return ({SYMBOL: sector}, [symbols]) — the breadth universe and its sector map.

    Default `extended=True` uses the BROADEST available mapping (NSE-500 macro base +
    AMC-disclosed-industry crosswalk learned by majority vote), ~1,600 symbols. If the
    extended map's dependencies aren't on disk it degrades to the NSE-500 base (~750)."""
    secmap = {}
    if extended:
        try:
            from vistas.funds_portfolio_viz import _extended_secmap
            secmap = _extended_secmap(log=log) or {}
        except Exception as e:
            log(f"[breadth] extended secmap unavailable ({e}); falling back to NSE-500 base")
    if not secmap:
        try:
            from vistas.stock_intel import load_industry_map
            secmap = load_industry_map() or {}
        except Exception as e:
            log(f"[breadth] industry map unavailable: {e}")
            secmap = {}
    secmap = {str(k).strip().upper(): str(v).strip() for k, v in secmap.items() if k and v}
    return secmap, sorted(secmap.keys())


def _latest_stock_csv():
    """Newest 'Stocks Data TR till *.csv' (falls back to the legacy PX panel)."""
    def _newest(pat):
        cands = glob.glob(os.path.join(DATA_DIR, pat))
        if not cands:
            return None
        import re
        def _key(p):
            m = re.search(r"till (.+)\.csv$", os.path.basename(p))
            d = pd.to_datetime(m.group(1).strip(), errors="coerce") if m else None
            return d if pd.notna(d) else pd.Timestamp(os.path.getmtime(p), unit="s")
        return max(cands, key=_key)
    return _newest("Stocks Data TR till *.csv") or _newest("Stocks Data PX till *.csv")


def load_panel(symbols, log=print) -> pd.DataFrame:
    """RAM-bounded read: the newest stock TR CSV restricted via `usecols` to `symbols`
    (∩ what the file actually has) + Date. Never materialises the full 4,309-wide frame.
    Returns a DatetimeIndex × symbol float frame (sorted, deduped)."""
    path = _latest_stock_csv()
    if path is None:
        raise FileNotFoundError("no 'Stocks Data TR/PX till *.csv' panel in data/")
    header = pd.read_csv(path, nrows=0)
    have = set(header.columns)
    want = ["Date"] + [s for s in symbols if s in have]
    log(f"[breadth] panel {os.path.basename(path)} — reading {len(want)-1}/{len(symbols)} "
        f"universe symbols present (of {len(have)-1} in file)")
    df = pd.read_csv(path, usecols=want)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
    df = df.apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")
    df = df[~df.index.duplicated(keep="last")]
    return df


# ---------------------------------------------------------------- vectorised builders
def _eligible_mask(panel: pd.DataFrame, min_span: int) -> pd.DataFrame:
    """Bool frame: True where stock `s` is eligible to be SCORED on date `t` for a rule
    needing `min_span` history. Eligibility = (a) at least `min_span` real (non-NaN)
    observations have accrued up to and including `t`, AND (b) the stock has cleared the
    CONTINUITY gate as of `t` — density >= MIN_OBS_PER_YEAR obs/yr and no internal gap
    > MAX_INTERNAL_GAP_ROWS rows over its realised history. A name failing either is
    simply EXCLUDED from that rule's denominator (never counted as a non-high)."""
    notna = panel.notna()
    # (a) running count of real observations up to t >= min_span
    obs_to_date = notna.cumsum()
    enough_span = obs_to_date >= min_span

    # (b) continuity gate, evaluated per-column on its realised span, as a single
    #     per-column verdict applied from the row it first becomes eligible onward.
    cont_ok = {}
    pos_index = np.arange(len(panel.index))
    for c in panel.columns:
        s = panel[c]
        present = np.where(s.notna().to_numpy())[0]
        if len(present) < 2:
            cont_ok[c] = False
            continue
        first, last = present[0], present[-1]
        days = (panel.index[last] - panel.index[first]).days / 365.25
        opy = len(present) / days if days > 0 else 0.0
        gap = int(np.diff(present).max())
        cont_ok[c] = (opy >= MIN_OBS_PER_YEAR and gap <= MAX_INTERNAL_GAP_ROWS)
    cont_series = pd.Series(cont_ok)
    # broadcast the per-column continuity verdict across all rows
    cont_frame = pd.DataFrame(np.broadcast_to(cont_series.values, panel.shape),
                              index=panel.index, columns=panel.columns)
    return enough_span & cont_frame & notna


def rolling_new_high(panel, W):
    """Bool frame: today's close is the highest close over the trailing W-day window
    INCLUDING today (>= the rolling max, min_periods=W). NaN where the window isn't full."""
    rmax = panel.rolling(W, min_periods=W).max()
    return panel >= rmax


def rolling_new_low(panel, W):
    rmin = panel.rolling(W, min_periods=W).min()
    return panel <= rmin


def above_dma(panel, N):
    """Bool frame: close >= its own N-day simple moving average (min_periods=N)."""
    ma = panel.rolling(N, min_periods=N).mean()
    return panel >= ma


def golden_cross(panel):
    """Bool frame: 50-DMA >= 200-DMA (both with min_periods)."""
    dma50 = panel.rolling(DMA_SHORT, min_periods=DMA_SHORT).mean()
    dma200 = panel.rolling(DMA_LONG, min_periods=DMA_LONG).mean()
    return dma50 >= dma200


# ---------------------------------------------------------------- aggregation
def _pct_series(true_frame: pd.DataFrame, elig_frame: pd.DataFrame, cols, min_n: int):
    """% = 100 * (# eligible-and-True) / (# eligible), per date, over `cols`. Returns
    (pct_series, eligible_n_series). pct is NaN on dates where eligible_n < min_n
    (the 'no score for error' floor — rendered as a gap, never 0%)."""
    tf = true_frame[cols]
    ef = elig_frame[cols]
    true_and_elig = (tf & ef).sum(axis=1).astype(float)
    elig_n = ef.sum(axis=1).astype(float)
    pct = 100.0 * true_and_elig / elig_n.replace(0, np.nan)
    pct = pct.where(elig_n >= min_n)
    return pct, elig_n


def _downsample_month_end(df_index: pd.DatetimeIndex) -> np.ndarray:
    """Positional indices of the LAST trading row of each calendar month — the breadth
    history is sampled month-end (breadth is a slow regime gauge; daily adds noise, not
    signal, and keeps the JSON small). The current snapshot stays DAILY (latest row)."""
    s = pd.Series(np.arange(len(df_index)), index=df_index)
    return s.resample("ME").last().dropna().astype(int).to_numpy()


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return round(float(v), 2)


def build_breadth_panel(extended: bool = True, log=print) -> dict:
    """The whole engine. Returns the JSON-able dict written to data/_breadth/breadth.json."""
    secmap, syms = resolve_universe(extended=extended, log=log)
    if not syms:
        raise RuntimeError("empty breadth universe (no sector map on disk)")
    panel = load_panel(syms, log=log)
    cols = list(panel.columns)
    log(f"[breadth] computing on {len(cols)} symbols x {len(panel)} rows "
        f"({panel.index[0].date()} -> {panel.index[-1].date()})")

    # --- precompute the boolean rule frames + eligibility masks (vectorised) -------
    nh = {y: rolling_new_high(panel, W) for y, W in WINDOWS.items()}
    nl = {y: rolling_new_low(panel, W) for y, W in WINDOWS.items()}
    a200 = above_dma(panel, DMA_LONG)
    a50 = above_dma(panel, DMA_SHORT)
    gc = golden_cross(panel)

    elig_W = {y: _eligible_mask(panel, W) for y, W in WINDOWS.items()}   # for new-high/low at W
    elig_200 = _eligible_mask(panel, DMA_LONG)
    elig_50 = _eligible_mask(panel, DMA_SHORT)
    # golden cross needs the longer (200) history to be meaningful
    elig_gc = elig_200

    # month-end sampling positions for the time series
    me_pos = _downsample_month_end(panel.index)
    me_dates = [panel.index[i].strftime("%Y-%m-%d") for i in me_pos]

    def _group_cols(group_syms):
        return [c for c in cols if c in group_syms]

    # sector -> [symbols] ; market = all
    groups = {"__market__": set(cols)}
    sec_of = {}
    for s in cols:
        sec = secmap.get(s, "Unclassified")
        sec_of[s] = sec
        groups.setdefault(sec, set()).add(s)

    def _build_group(gcols, min_n):
        """All breadth series for one column group, month-end sampled."""
        out = {}
        # new high / low / nh-nl per window
        pct_nh, pct_nl, nhnl = {}, {}, {}
        elig_n_any = None
        for y in WINDOWS:
            ph, en = _pct_series(nh[y], elig_W[y], gcols, min_n)
            pl, _ = _pct_series(nl[y], elig_W[y], gcols, min_n)
            pct_nh[y] = [_clean(ph.iloc[i]) for i in me_pos]
            pct_nl[y] = [_clean(pl.iloc[i]) for i in me_pos]
            net = (ph - pl)
            nhnl[y] = [_clean(net.iloc[i]) for i in me_pos]
            if y == "1":
                elig_n_any = en
        p200, en200 = _pct_series(a200, elig_200, gcols, min_n)
        p50, _ = _pct_series(a50, elig_50, gcols, min_n)
        pgc, _ = _pct_series(gc, elig_gc, gcols, min_n)
        out["pct_new_high"] = pct_nh
        out["pct_new_low"] = pct_nl
        out["nh_minus_nl"] = nhnl
        out["pct_above_200dma"] = [_clean(p200.iloc[i]) for i in me_pos]
        out["pct_above_50dma"] = [_clean(p50.iloc[i]) for i in me_pos]
        out["pct_golden_cross"] = [_clean(pgc.iloc[i]) for i in me_pos]
        # eligible_n: use the 200-DMA eligibility as the headline denominator (every-day valid)
        out["eligible_n"] = [int(en200.iloc[i]) if en200.iloc[i] == en200.iloc[i] else 0
                             for i in me_pos]
        return out

    market = _build_group(_group_cols(groups["__market__"]), MIN_BREADTH_N_MARKET)

    sectors = {}
    thin = []
    for sec, members in groups.items():
        if sec == "__market__":
            continue
        gcols = _group_cols(members)
        if not gcols:
            continue
        # decide thin: never clears the per-sector floor on the LAST row
        last_elig = int(elig_200[gcols].iloc[-1].sum())
        if last_elig < MIN_BREADTH_N_SECTOR:
            thin.append(sec)
            continue
        sectors[sec] = _build_group(gcols, MIN_BREADTH_N_SECTOR)

    # --- CURRENT-DAY snapshot (daily resolution) for the m%-threshold screen --------
    t = len(panel) - 1
    asof = panel.index[t].strftime("%Y-%m-%d")

    def _names_true(frame, elig, gcols):
        row_true = frame[gcols].iloc[t]
        row_el = elig[gcols].iloc[t]
        return sorted([c for c in gcols if bool(row_true.get(c)) and bool(row_el.get(c))])

    def _pct_now(frame, elig, gcols):
        row_el = elig[gcols].iloc[t]
        n = int(row_el.sum())
        if n == 0:
            return None, 0
        tr = int((frame[gcols].iloc[t] & row_el).sum())
        return _clean(100.0 * tr / n), n

    # market snapshot
    m_a200, m_n200 = _pct_now(a200, elig_200, _group_cols(groups["__market__"]))
    m_nh1, _ = _pct_now(nh["1"], elig_W["1"], _group_cols(groups["__market__"]))
    m_gc, _ = _pct_now(gc, elig_gc, _group_cols(groups["__market__"]))
    snapshot_market = {"pct_new_high_1y": m_nh1, "pct_above_200dma": m_a200,
                       "pct_golden_cross": m_gc, "eligible_n": m_n200}

    # per-sector snapshot rows (drives the screen) — INCLUDING thin sectors flagged
    screen_current = {}
    snapshot_sectors = []
    for sec, members in groups.items():
        if sec == "__market__":
            continue
        gcols = _group_cols(members)
        if not gcols:
            continue
        p_a200, n_a200 = _pct_now(a200, elig_200, gcols)
        p_nh1, _ = _pct_now(nh["1"], elig_W["1"], gcols)
        p_nh3, _ = _pct_now(nh["3"], elig_W["3"], gcols)
        p_nh5, _ = _pct_now(nh["5"], elig_W["5"], gcols)
        p_gc, _ = _pct_now(gc, elig_gc, gcols)
        is_thin = sec in thin
        # screen_current: the compact per-sector current values the JS filters by m%.
        # "pct_breakout" = % at a NEW 1-YEAR HIGH today (the default breakout rule).
        screen_current[sec] = {
            "n": n_a200, "thin": is_thin,
            "pct_breakout": p_nh1,        # default breakout = new 1y high
            "pct_breakout_3y": p_nh3, "pct_breakout_5y": p_nh5,
            "pct_golden_cross": p_gc, "pct_above_200dma": p_a200,
        }
        snapshot_sectors.append({
            "sector": sec, "n": n_a200, "thin": is_thin,
            "pct_new_high_1y": p_nh1, "pct_new_high_3y": p_nh3, "pct_new_high_5y": p_nh5,
            "pct_above_200dma": p_a200, "pct_golden_cross": p_gc,
            "names_new_high_1y": _names_true(nh["1"], elig_W["1"], gcols),
            "names_golden_cross": _names_true(gc, elig_gc, gcols),
        })
    snapshot_sectors.sort(key=lambda r: (-(r["pct_new_high_1y"] or -1), r["sector"]))

    payload = {
        "meta": {
            "asof": asof,
            "universe": "NSE-500 + AMC-disclosed crosswalk" if extended else "NSE-500",
            "universe_n": len(cols),
            "n_sectors": len(sectors),
            "thin_sectors": sorted(thin),
            "n_years_options": [1, 3, 5],
            "dmas": [DMA_SHORT, DMA_LONG],
            "cadence": "month-end sampled history; daily current snapshot",
            "gate": {
                "min_obs_per_year": MIN_OBS_PER_YEAR,
                "max_internal_gap_rows": MAX_INTERNAL_GAP_ROWS,
                "min_breadth_n_market": MIN_BREADTH_N_MARKET,
                "min_breadth_n_sector": MIN_BREADTH_N_SECTOR,
                "note": ("A stock is eligible for a rule only if it has >= the rule's required "
                         "history (W days for new-high/low, N days for the DMA) AND clears the "
                         "continuity gate (>=150 obs/yr, no internal gap >25 rows). A breadth "
                         "point is emitted only with >= the cross-section floor of eligible names; "
                         "below it the value is null (a gap), never 0%."),
            },
            "caveat": ("Descriptive / COINCIDENT participation gauge, validated on our own Indian "
                       "TR data — NOT a forward signal (the apparent edge dies under honest "
                       "overlapping-window correction and is non-monotone). India stocks only. "
                       "'New N-year high' = today's adjusted close is the highest close over the "
                       "trailing N years. '% above 200-DMA' = % of stocks whose close is at/above "
                       "their 200-day average."),
            "source": ("adjusted total-return price panel (bhavcopy-reconstructed) + NSE-500 industry "
                       "map (+ AMC-disclosed crosswalk). Price-derived only — NO ARM / licensed data."),
        },
        "dates": me_dates,
        "market": market,
        "sectors": sectors,
        "screen_current": screen_current,
        "snapshot": {"asof": asof, "market": snapshot_market, "sectors": snapshot_sectors},
    }
    return payload


# ---------------------------------------------------------------- write + validate
def write(payload: dict, path: str = OUT_FILE) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    return path


def _validate(payload: dict, path: str):
    """Print the three checks the task asks for + a sanity read."""
    m = payload["market"]
    meta = payload["meta"]
    print("\n========== VALIDATION ==========")
    print(f"asof={meta['asof']}  universe_n={meta['universe_n']}  "
          f"sectors={meta['n_sectors']}  thin={meta['thin_sectors']}")
    print(f"history: {len(payload['dates'])} month-end points "
          f"{payload['dates'][0]} -> {payload['dates'][-1]}")

    print("\n(a) LATEST MARKET BREADTH (last month-end of the history series):")
    print(f"    pct_above_200dma   = {m['pct_above_200dma'][-1]}")
    print(f"    pct_above_50dma    = {m['pct_above_50dma'][-1]}")
    print(f"    pct_golden_cross   = {m['pct_golden_cross'][-1]}")
    for y in WINDOWS:
        print(f"    new {y}y-high       = {m['pct_new_high'][y][-1]}   "
              f"new {y}y-low = {m['pct_new_low'][y][-1]}   "
              f"nh-nl = {m['nh_minus_nl'][y][-1]}")
    print(f"    eligible_n         = {m['eligible_n'][-1]}")
    sm = payload["snapshot"]["market"]
    print(f"    [daily snapshot]   a200={sm['pct_above_200dma']} "
          f"nh1y={sm['pct_new_high_1y']} gc={sm['pct_golden_cross']} n={sm['eligible_n']}")

    print("\n(b) screen_current — 3-sector sample:")
    sample = list(payload["screen_current"].items())[:3]
    for sec, v in sample:
        print(f"    {sec:28s} n={v['n']:4d} thin={v['thin']}  "
              f"breakout(1yNH)={v['pct_breakout']}  gc={v['pct_golden_cross']}  "
              f"a200={v['pct_above_200dma']}")

    sz = os.path.getsize(path)
    print(f"\n(c) output: {path}")
    print(f"    size = {sz/1024:.1f} KB ({sz:,} bytes)   "
          f"date range {payload['dates'][0]} -> {payload['dates'][-1]}")

    # sanity: %>200DMA should be high near tops, low after crashes. Pull a known crash
    # trough (2020-03 covid, 2008-end GFC) and a top (late 2017 / late 2021) if present.
    print("\n    SANITY — %>200DMA at known regimes (high near tops, low after crashes):")
    didx = {d[:7]: i for i, d in enumerate(payload["dates"])}
    for ym, label in [("2008-10", "GFC crash"), ("2020-03", "COVID crash"),
                      ("2017-12", "2017 melt-up top"), ("2021-10", "2021 top"),
                      ("2022-06", "2022 drawdown")]:
        i = didx.get(ym)
        if i is not None:
            print(f"      {ym} ({label:18s}): %>200DMA = {m['pct_above_200dma'][i]}")
    print("================================\n")


def build_and_write(extended: bool = True, log=print) -> dict:
    payload = build_breadth_panel(extended=extended, log=log)
    path = write(payload)
    _validate(payload, path)
    return payload


if __name__ == "__main__":
    try:                                  # Windows console is cp1252; the secmap log uses arrows
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = _dt.datetime.now()
    build_and_write(log=lambda m: print(m, flush=True))
    print(f"[breadth] done in {(_dt.datetime.now()-t0).total_seconds():.1f}s", flush=True)
