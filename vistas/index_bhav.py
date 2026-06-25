"""
NSE index valuation panel for Vistas — per-index Closing value, P/E, P/B and
Dividend-Yield time-series, sourced from the exchange's own daily index report.

WHY THIS EXISTS
---------------
The TR index frame (data.py) carries index *levels* only. But the NSE publishes,
for every published index every trading day, the index's *valuation* — its
trailing price/earnings (P/E), price/book (P/B) and dividend yield — in one
plain-CSV daily report. Those three ratios are the bread-and-butter of "is the
market / this sector cheap or dear" analysis (e.g. NIFTY 50 P/E ~ 22-24 in
mid-2026 is a "fair-to-rich" reading; P/E in the low-30s in late 2021 was the
froth peak). This module captures that report so the terminal can chart index
valuation history alongside price — for ~160 NSE indices, free, no login.

ENDPOINT
--------
GET https://nsearchives.nseindia.com/content/indices/ind_close_all_<DDMMYYYY>.csv
A plain CSV (NOT zipped). Public archive -> low account risk. Needs an NSE
cookie session first (land on nseindia.com like a browser), so we reuse
`vistas.bhav_prices.nse_session()`.

CSV SHAPE (header verbatim, confirmed live 2026-06 and 2020-06)
---------------------------------------------------------------
    Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,
    Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),
    P/E,P/B,Div Yield
  * "Index Date" is DD-MM-YYYY.
  * Numbers can drop a leading zero: "-.64" means -0.64, ".33" means 0.33.
    pandas' float parser already handles ".64"/"-.64" correctly, so to_numeric
    needs no special pre-clean — but we coerce and verify anyway.
  * P/E / P/B / Div Yield are blank for some indices (debt / strategy indices
    with no equity earnings) -> parsed as NaN.
  * "Turnover (Rs. Cr.)" carries periods after Rs and Cr (NOT "Turnover (Rs Cr)").

PUBLIC API (uniform with the other Vistas data layers)
------------------------------------------------------
    fetch_series(start="2000-01-01", log=print)
        -> (series_by_name: {name: pandas.Series}, meta_by_name: {name: {...}})
    Each series is a daily pandas.Series indexed by Timestamp; keys are friendly
    "<Index Name> — <metric>" (e.g. "Nifty 50 — P/E"). meta carries unit/freq/
    source/group.

ACCOUNT SAFETY: the default fetch pulls only a handful of recent trading days
plus one old date (a *prove-it* sample, not a backfill). A full multi-year
backfill is `build_cache(...)` + `fetch_series(full=True)` and is left for the
lead to run politely later.

Graceful-degrade: every entry point returns what it has and never raises on a
network error / holiday / 404. Imports nothing from the research engine.
Written for Vistas 2026-06-21.
"""
from __future__ import annotations

import os
import io
import time
import random
import datetime as dt

import numpy as np
import pandas as pd

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))
CACHE_DIR = os.path.join(DATA_DIR, "_indexval")        # one verbatim CSV per date (gitignored)

BASE_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"
REFERER = "https://www.nseindia.com/"
SOURCE = "NSE (ind_close_all daily index report)"

# The four metrics we expose, each -> (CSV column, friendly suffix, unit, group).
METRICS = [
    ("Closing Index Value", "Close", "index level", "Index level"),
    ("P/E", "P/E", "ratio (×)", "Index valuation"),
    ("P/B", "P/B", "ratio (×)", "Index valuation"),
    ("Div Yield", "Div Yield", "% p.a.", "Index valuation"),
]


# ----------------------------------------------------------------------------- session
def _session():
    """Reuse bhav_prices' cookie-seeded NSE session; fall back to a bare session, or
    None if requests is unavailable."""
    if requests is None:
        return None
    try:
        from . import bhav_prices as bp
        s = bp.nse_session()
        if s is not None:
            return s
    except Exception:
        pass
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    try:
        s.get("https://www.nseindia.com", timeout=30)
    except Exception:
        pass
    return s


# ----------------------------------------------------------------------------- fetch one day
def _csv_path(date_str: str) -> str:
    return os.path.join(CACHE_DIR, f"{date_str}.csv")


def _none_path(date_str: str) -> str:
    return os.path.join(CACHE_DIR, f"{date_str}.none")


def fetch_day(date_str: str, session=None, log=lambda m: None, polite=True) -> str | None:
    """Ensure the index-valuation CSV for `date_str` (YYYY-MM-DD) is cached on disk; return
    its path, or None when NSE has no file for that date (holiday / pre-archive / unreachable).
    Resumable: a date already cached (CSV or a `.none` holiday marker) returns immediately
    with no network call. Polite jittered pause after a real fetch."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp, npth = _csv_path(date_str), _none_path(date_str)
    if os.path.exists(cp):
        return cp
    if os.path.exists(npth):
        return None
    if session is None:
        session = _session()
    if session is None:
        return None
    try:
        d = dt.date.fromisoformat(date_str)
    except Exception:
        return None
    url = BASE_URL.format(ddmmyyyy=d.strftime("%d%m%Y"))
    try:
        r = session.get(url, timeout=45, headers={"Referer": REFERER})
        # A real file starts with the "Index Name" header. Holidays / bad dates return a
        # short error page or 404 -> mark .none so we never refetch that date.
        if r.status_code == 200 and r.text[:10].lstrip().lower().startswith("index name"):
            with open(cp, "w", encoding="utf-8", newline="") as f:
                f.write(r.text)
            if polite:
                time.sleep(random.uniform(0.5, 1.2))
            return cp
        log(f"    indexval {date_str}: no file (status {r.status_code})")
    except Exception as e:
        log(f"    indexval {date_str}: {e}")
        if polite:
            time.sleep(random.uniform(0.3, 0.7))
        return None                          # transient network error: do NOT poison-mark
    # confirmed no file (holiday / not yet published) -> mark so we skip it next time
    try:
        open(npth, "w").close()
    except Exception:
        pass
    if polite:
        time.sleep(random.uniform(0.3, 0.7))
    return None


# ----------------------------------------------------------------------------- parse one day
def parse_day(csv_path: str) -> pd.DataFrame | None:
    """Parse one cached index-valuation CSV -> tidy DataFrame with columns
    [Index Name, date(Timestamp), Closing Index Value, P/E, P/B, Div Yield].
    The numeric columns are coerced (blank -> NaN; the dropped-leading-zero forms
    like '-.64'/'.33' parse correctly as floats). Returns None on an unreadable file."""
    try:
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, skipinitialspace=True)
    except Exception:
        return None
    # header may carry stray spaces -> normalize to a lookup
    cols = {c.strip(): c for c in df.columns}

    def col(name):
        return cols.get(name)

    name_c = col("Index Name")
    date_c = col("Index Date")
    if not name_c or not date_c:
        return None

    out = pd.DataFrame()
    out["Index Name"] = df[name_c].astype(str).str.strip()
    # Index Date is DD-MM-YYYY
    out["date"] = pd.to_datetime(df[date_c].astype(str).str.strip(),
                                 format="%d-%m-%Y", errors="coerce")

    def num(name):
        c = col(name)
        if c is None:
            return np.nan
        # blank cells -> NaN; "-.64"/".33" float-parse fine via to_numeric
        s = df[c].astype(str).str.strip().replace({"": None, "-": None, "NA": None,
                                                   "nan": None, "NaN": None})
        return pd.to_numeric(s, errors="coerce")

    out["Closing Index Value"] = num("Closing Index Value")
    out["P/E"] = num("P/E")
    out["P/B"] = num("P/B")
    out["Div Yield"] = num("Div Yield")

    out = out[out["Index Name"].astype(bool) & out["date"].notna()]
    return out if len(out) else None


# ----------------------------------------------------------------------------- cache helpers
def _cached_dates(start=None, end=None) -> list:
    """Dates (YYYY-MM-DD) that already have a cached CSV on disk, within [start, end]."""
    if not os.path.isdir(CACHE_DIR):
        return []
    out = []
    for f in os.listdir(CACHE_DIR):
        if not f.endswith(".csv"):
            continue
        d = f[:-4]
        if (start and d < str(start)[:10]) or (end and d > str(end)[:10]):
            continue
        out.append(d)
    return sorted(out)


def _nse_trading_days(start, end):
    """The NSE trading-day calendar = the TR index frame's dates (post stale-day filter).
    Falls back to None if data.py / the snapshot is unavailable (then we use a plain
    weekday range as a coarse calendar). Sorted list of YYYY-MM-DD strings."""
    try:
        from . import data
        idx = pd.DatetimeIndex(data.load(data.DEFAULT_MEASURE).index).sort_values()
        idx = idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]
        if len(idx):
            return [d.strftime("%Y-%m-%d") for d in idx]
    except Exception:
        pass
    # coarse fallback: business days (will hit .none on holidays, then skip thereafter)
    rng = pd.bdate_range(start, end)
    return [d.strftime("%Y-%m-%d") for d in rng]


def build_cache(start="2000-01-01", end=None, session=None, log=None,
                max_days=None) -> dict:
    """Download + cache every NSE trading day's index-valuation CSV in [start, end].
    Resumable, polite, graceful. `max_days` caps how many NEW dates to fetch this run
    (account safety for big backfills). Returns counts. The lead runs the full backfill;
    fetch_series() below only needs a tiny prove-it sample."""
    log = log or (lambda m: print(m, flush=True))
    end = end or dt.date.today().isoformat()
    cal = _nse_trading_days(start, end)
    todo = [d for d in cal if not os.path.exists(_csv_path(d))
            and not os.path.exists(_none_path(d))]
    if max_days:
        todo = todo[-int(max_days):]
    log(f"[indexval] {len(cal)} trading days in window; {len(todo)} to fetch.")
    if session is None and todo:
        session = _session()
    fetched = found = 0
    for i, ds in enumerate(todo):
        cp = fetch_day(ds, session, log)
        fetched += 1
        if cp:
            found += 1
        if (i + 1) % 25 == 0:
            log(f"  [{i+1}/{len(todo)}] {found} have data (latest {ds})")
    n_have = sum(1 for d in cal if os.path.exists(_csv_path(d)))
    return {"ok": True, "n_days": len(cal), "fetched": fetched, "found_this_run": found,
            "days_with_data": n_have}


# ----------------------------------------------------------------------------- assemble
def _assemble(dates: list, log=lambda m: None):
    """Pivot a list of cached dates into per-(index, metric) daily Series. Returns
    (series_by_name, meta_by_name)."""
    # long table: date x Index Name for each metric
    frames = {m[0]: {} for m in METRICS}   # csv-col -> {date: {index: value}}
    for ds in dates:
        df = parse_day(_csv_path(ds))
        if df is None:
            continue
        df = df.drop_duplicates("Index Name", keep="last")
        d = df["date"].iloc[0]
        for csv_col, _suffix, _unit, _grp in METRICS:
            vals = df.set_index("Index Name")[csv_col]
            frames[csv_col][d] = vals
    series_by_name, meta_by_name = {}, {}
    for csv_col, suffix, unit, grp in METRICS:
        by_date = frames[csv_col]
        if not by_date:
            continue
        wide = pd.DataFrame(by_date).T.sort_index()      # rows=date, cols=index name
        wide.index = pd.DatetimeIndex(wide.index)
        for idx_name in wide.columns:
            s = wide[idx_name].dropna()
            if not len(s):
                continue
            key = f"{idx_name} — {suffix}"
            series_by_name[key] = s.astype(float)
            meta_by_name[key] = {"unit": unit, "freq": "daily", "source": SOURCE,
                                 "group": grp, "index": idx_name, "metric": suffix}
    return series_by_name, meta_by_name


# ----------------------------------------------------------------------------- public API
def fetch_series(start="2000-01-01", log=print, full=False,
                 sample_recent=4, sample_old=("2020-06-01",)):
    """Vistas-uniform entry point.

    Returns (series_by_name, meta_by_name):
      * series_by_name[name] = pandas.Series indexed by Timestamp (daily), float values.
      * meta_by_name[name]   = {"unit","freq":"daily","source","group","index","metric"}.
      * Keys are friendly "<Index Name> — <metric>", e.g. "Nifty 50 — P/E".

    Two modes:
      * full=False (default, ACCOUNT-SAFE): pull a few recent trading days + one old date
        (a prove-it sample) and assemble those. Plus ANY dates already cached on disk.
      * full=True: assemble EVERY cached date (run build_cache(...) first for a real
        backfill — this function never bulk-fetches in full mode, it only reads cache).
    Never raises: on a network failure it returns whatever is cached (possibly empty).
    """
    log = log or (lambda m: None)
    if full:
        dates = _cached_dates(start)
        log(f"[indexval] full mode: assembling {len(dates)} cached dates.")
        return _assemble(dates, log)

    # account-safe prove-it sample: a few recent NSE trading days + one old date.
    session = _session()
    cal = _nse_trading_days((dt.date.today() - dt.timedelta(days=20)).isoformat(),
                            dt.date.today().isoformat())
    want = list(cal[-int(sample_recent):]) + list(sample_old)
    want = [d for d in want if d >= str(start)[:10]]
    got = []
    for ds in want:
        cp = fetch_day(ds, session, log)
        if cp:
            got.append(ds)
    # also fold in anything already cached, so repeated runs accumulate
    cached = _cached_dates(start)
    dates = sorted(set(got) | set(cached))
    log(f"[indexval] sample mode: {len(got)} fetched/confirmed, "
        f"{len(dates)} total cached dates assembled.")
    return _assemble(dates, log)


# ----------------------------------------------------------------------------- self-test
if __name__ == "__main__":
    sb, mb = fetch_series()
    print(f"\n{len(sb)} series across {len({m['index'] for m in mb.values()})} indices")
    pe = sb.get("Nifty 50 — P/E")
    if pe is not None and len(pe):
        print("Nifty 50 P/E latest:", pe.index[-1].date(), round(float(pe.iloc[-1]), 2))
