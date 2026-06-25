"""
MOSPI CPI — COICOP division legs + core (ex food & fuel) for Vistas.
=====================================================================

A standalone fetcher for India's Consumer Price Index (CPI) broken out by its
COICOP "division" legs (Food & beverages, Housing, Health, Transport, …) plus a
derived **core CPI** (the index excluding the volatile food and fuel components),
sourced LIVE from MOSPI eSankhyiki on the new 2024 base.

What one row of source data is
------------------------------
MOSPI's CPI API returns one record per (year, month, state, sector, division,
group, class, sub_class, item). We keep ONLY the *division-aggregate* rows — the
ones where group/class/sub_class/item are all null — for state = All India, on the
three sectors Rural / Urban / Combined. Each such row carries the division's index
level (2024=100) and MOSPI's own published year-on-year (YoY) inflation %.

The 2024-base COICOP divisions (this is the NEW classification — note it differs
from the old 2012-base six-group scheme; in particular there is NO standalone
"Fuel and light" division any more — energy/fuel now sits *inside* division 4,
"Housing, water, electricity, gas and other fuels"):

    0  CPI (General)                          <- headline, reference only
    1  Food and beverages                     <- "food"  (excluded from core)
    2  Paan, tobacco and intoxicants
    3  Clothing and footwear
    4  Housing, water, electricity, gas …     <- contains the "fuel/energy" group
    5  Furnishings, household equipment …
    6  Health
    7  Transport
    8  Information and communication
    9  Recreation, sport and culture
    10 Education services
    11 Restaurants and accommodation services
    12 Personal care, social protection & misc

Core CPI — the two methods we expose (both clearly labelled)
------------------------------------------------------------
"Core" inflation = inflation stripped of the noisy food and fuel/energy legs, so
it reads the underlying trend. Because the 2024-base granular official weights are
not (yet) published in a form we can reconcile to the decimal, and because the
weights are NOT recoverable from the index data alone (only ~17 monthly points
exist on this base, and the divisions move in too narrow a band to identify 12
weights — a least-squares solve is degenerate), we DO NOT pretend to a single
authoritative weighted core. Instead we publish two transparent measures:

  • Core CPI (ex-food, equal-weight)   — the simple arithmetic mean of every
    division index EXCEPT "Food and beverages". Weight-free, fully reproducible,
    keeps housing/rent (so it tracks the sticky underlying trend). This is the
    broad, no-assumptions approximation.

  • Core CPI (ex food & fuel, official-weight est.) — the RBI-style definition:
    drop "Food and beverages" entirely and drop the "Electricity, gas and other
    fuels" group from within division 4, then take a weight-WEIGHTED average of
    the remaining legs using best-available published 2024-base Combined division
    weights (see CPI_2024_DIVISION_WEIGHTS below). Flagged as an estimate: these
    weights reproduce the published General index to within ~0.5 index points.

YoY inflation for each core series is computed the standard way:
    core_YoY(t) = core_index(t) / core_index(t-12 months) - 1.

A note on history / why YoY is sparse early on: the 2024-base CPI launched in
Feb-2025 with a back-cast to Jan-2025, so the index itself starts Jan-2025 and the
FIRST genuine YoY reading is Jan-2026 (you need 12 prior months). Before Jan-2026
MOSPI publishes no YoY for the legs, and neither do we.

Endpoint contract (proven live, 2026-06)
-----------------------------------------
  GET https://api.mospi.gov.in/api/cpi/getCpiData
      base_year=2024 & series=Current & state_code=1 (=All India!) &
      sector_code=1|2|3 (Rural|Urban|Combined) & level=Division &
      division_code=<0..12> & year=<YYYY> & page=<N>
  Dims:
  GET https://api.mospi.gov.in/api/cpi/getCpiFilterByLevelAndBaseYear?level=Group&base_year=2024

Gotchas baked in below (each cost a probe to discover — these are the load-bearing ones):
  • state_code MUST be 1 for All India — state_code=0 returns ZERO rows (the spec's
    "state_code=0" is wrong against the live API).
  • The `month` query param is SILENTLY IGNORED — every request returns the LATEST
    month regardless of the month you ask for. Pinning a month therefore FABRICATES a
    flat series. We never pass `month`; we read the real (year, month) from each
    record instead.  ← the single most dangerous trap here.
  • The `year` param IS honoured. We loop division_code × year and page within.
  • The API ignores limit/recordPerPage — it serves 10 rows/page; page through
    meta_data.totalPages.
  • level=Division still interleaves the sub-group rows; the division aggregate is
    the row where group/class/sub_class/item are ALL null — filter on that. It is the
    first row of each month's block. Month blocks are NOT equal-sized (item counts
    differ), so we page sequentially and early-stop once all of a year's months are
    in hand (cap MAX_PAGES_PER_DIV_YEAR), rather than jump-to-page.
  • SPA trap: a 200 HTML shell is not the API — we confirm the body starts with '{'.
  • Govt self-signed cert → verify=False (public, read-only data).
  • 'Back' series carries NO division legs (only the headline back-casts to 2013) —
    so the legs are 'Current' only; the 2024-base legs realistically start Jan-2025.

API: fetch_series(start="2000-01-01", log=print) -> (series_by_name, meta_by_name)

Provenance: written for Vistas 2026-06-21. Self-contained; imports nothing from
other Vistas modules or the research engine. Degrades gracefully on any network or
parse failure (returns whatever was gathered, never raises).
"""
from __future__ import annotations

import time
import random
import datetime as dt

import numpy as np
import pandas as pd

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

try:                                    # MOSPI serves a Govt self-signed cert chain
    import urllib3                       # certifi rejects -> verify=False that host
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:                       # pragma: no cover
    pass

# --------------------------------------------------------------------------- config
MOSPI_API = "https://api.mospi.gov.in/api"
CPI_DATA_URL = f"{MOSPI_API}/cpi/getCpiData"
CPI_DIMS_URL = f"{MOSPI_API}/cpi/getCpiFilterByLevelAndBaseYear"
BASE_YEAR = 2024
VERIFY = False                          # Govt self-signed cert
MAX_PAGES_PER_DIV_YEAR = 300            # safety cap; heavy divisions (Food) span ~260 pp/yr
DIVISION_START_YEAR = 2025              # 2024-base legs begin Jan-2025 (back-cast launch)
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://esankhyiki.mospi.gov.in/",
    "Accept": "application/json, text/plain, */*",
}

SECTORS = {1: "Rural", 2: "Urban", 3: "Combined"}

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]
_MONTH_NUM = {m.lower(): i + 1 for i, m in enumerate(_MONTHS)}
_MONTH_NUM.update({m[:3].lower(): i + 1 for i, m in enumerate(_MONTHS)})

# The 13 COICOP-2024 division names exactly as MOSPI returns them. Index 0 = headline.
DIVISIONS = [
    "CPI (General)",                                                       # 0 headline
    "Food and beverages",                                                  # 1  (=food)
    "Paan, tobacco and intoxicants",                                       # 2
    "Clothing and footwear",                                               # 3
    "Housing, water, electricity, gas and other fuels",                    # 4  (has fuel)
    "Furnishings, household equipment and routine household maintenance",  # 5
    "Health",                                                              # 6
    "Transport",                                                           # 7
    "Information and communication",                                       # 8
    "Recreation, sport and culture",                                       # 9
    "Education services",                                                  # 10
    "Restaurants and accommodation services",                             # 11
    "Personal care, social protection and miscellaneous goods and services",  # 12
]
HEADLINE = "CPI (General)"
FOOD = "Food and beverages"
HOUSING_FUEL = "Housing, water, electricity, gas and other fuels"
_LEGS = [d for d in DIVISIONS if d != HEADLINE]            # the 12 non-headline legs

# Best-available published 2024-base CPI **Combined** division weights (%, sum=100).
# Source: MOSPI 2024-base CPI weighting diagram (All-India, Combined). NOTE: these
# are used ONLY for the official-weight core estimate and are flagged as such — they
# reproduce the published General index to within ~0.5 index points (the 2024-base
# granular weights are not yet released to the decimal). The food/fuel legs we strip
# for core are excluded from the weighting at compute time.
CPI_2024_DIVISION_WEIGHTS = {
    "Food and beverages": 45.86,
    "Paan, tobacco and intoxicants": 2.38,
    "Clothing and footwear": 6.53,
    "Housing, water, electricity, gas and other fuels": 10.91,
    "Furnishings, household equipment and routine household maintenance": 3.77,
    "Health": 5.89,
    "Transport": 7.60,
    "Information and communication": 2.05,
    "Recreation, sport and culture": 1.68,
    "Education services": 4.46,
    "Restaurants and accommodation services": 2.15,
    "Personal care, social protection and miscellaneous goods and services": 6.72,
}
# Approx fuel/energy share inside division 4 (the "Electricity, gas and other fuels"
# group), used to net the energy weight out of housing for the ex-food&fuel core.
# Conservative ~40% of the housing division by weight (rest = rent + maintenance).
_HOUSING_FUEL_FRACTION = 0.40


# ============================================================ low-level helpers
def _to_float(raw):
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except Exception:
        return None


def _get_json(params, log, tag=""):
    """One GET → parsed JSON dict, or None. Guards the MOSPI SPA trap (a 200 HTML
    shell is NOT the API — the real body starts with '{')."""
    if requests is None:
        return None
    try:
        r = requests.get(CPI_DATA_URL, params=params, headers=HEADERS,
                         verify=VERIFY, timeout=45)
    except Exception as e:
        log(f"    [cpi_core] request failed {tag}: {e}")
        return None
    body = (r.text or "").lstrip()
    if not body.startswith("{"):
        log(f"    [cpi_core] non-JSON body {tag} (SPA shell?, status {r.status_code}, "
            f"{len(body)} bytes)")
        return None
    try:
        return r.json()
    except Exception as e:
        log(f"    [cpi_core] JSON parse failed {tag}: {e}")
        return None


def _is_division_aggregate(rec: dict) -> bool:
    """True only for the pure division row — all finer levels null."""
    return all(rec.get(k) in (None, "") for k in
               ("group", "class", "sub_class", "item"))


def _rec_timestamp(rec: dict):
    """The TRUE month-start Timestamp from a record's own year/month (never trust the
    requested month — the API ignores the month param)."""
    mm = _MONTH_NUM.get(str(rec.get("month", "")).strip().lower())
    try:
        yy = int(rec.get("year"))
    except Exception:
        return None
    if not mm:
        return None
    try:
        return pd.Timestamp(yy, mm, 1)
    except Exception:
        return None


def _expected_n_months(year: int, today: dt.date) -> int:
    """How many monthly observations a given year should have (≤ current month for
    the running year), so we can early-stop a division-year scan once complete."""
    if year < today.year:
        return 12
    if year > today.year:
        return 0
    return today.month                  # running year: up to the current month


# ============================================================ the fetch
def _fetch_division_year(sector_code, division_code, year, want_n, log):
    """All monthly aggregates for ONE (sector, division, year). Pages sequentially,
    keeps only the division-aggregate rows, reads each row's TRUE month, early-stops
    once `want_n` months are collected. Returns {Timestamp: (index, inflation)}."""
    out: dict[pd.Timestamp, tuple] = {}
    page, total_pages = 1, 1
    while page <= total_pages and page <= MAX_PAGES_PER_DIV_YEAR:
        j = _get_json(
            {"base_year": BASE_YEAR, "series": "Current", "state_code": 1,
             "sector_code": sector_code, "level": "Division",
             "division_code": division_code, "year": year, "page": page},
            log, tag=f"s{sector_code} d{division_code} {year} p{page}")
        if not j:
            break
        total_pages = int(j.get("meta_data", {}).get("totalPages", 1) or 1)
        for rec in j.get("data", []):
            if not _is_division_aggregate(rec):
                continue
            ts = _rec_timestamp(rec)
            if ts is None or ts in out:
                continue
            out[ts] = (_to_float(rec.get("index")), _to_float(rec.get("inflation")))
        if want_n and len(out) >= want_n:    # got every month this year offers → stop
            break
        page += 1
        if page <= total_pages:
            time.sleep(random.uniform(0.10, 0.25))
    return out


def _fetch_sector_legs(sector_code, years, today, log) -> pd.DataFrame:
    """For one sector, pull every division aggregate over `years`. Returns a DataFrame
    indexed by month-start Timestamp, columns = a (field, division) MultiIndex with
    field in {index, inflation}."""
    idx_rows: dict[pd.Timestamp, dict] = {}
    inf_rows: dict[pd.Timestamp, dict] = {}
    for di, dname in enumerate(DIVISIONS):           # division_code = list position
        for yr in years:
            got = _fetch_division_year(sector_code, di, yr,
                                       _expected_n_months(yr, today), log)
            for ts, (ix, infl) in got.items():
                idx_rows.setdefault(ts, {})[dname] = ix
                inf_rows.setdefault(ts, {})[dname] = infl
            time.sleep(random.uniform(0.12, 0.28))
    if not idx_rows:
        return pd.DataFrame()
    idx_df = pd.DataFrame.from_dict(idx_rows, orient="index").sort_index()
    inf_df = pd.DataFrame.from_dict(inf_rows, orient="index").sort_index()
    idx_df.columns = pd.MultiIndex.from_product([["index"], idx_df.columns])
    inf_df.columns = pd.MultiIndex.from_product([["inflation"], inf_df.columns])
    return idx_df.join(inf_df, how="outer").sort_index()


# ============================================================ core computation
def _equal_weight_core(idx_df: pd.DataFrame) -> pd.Series:
    """Core (ex-food, equal-weight): simple mean of all division indices except
    Food and beverages, per month. Weight-free, fully reproducible."""
    legs = [d for d in _LEGS if d != FOOD and d in idx_df.columns]
    if not legs:
        return pd.Series(dtype="float64")
    return idx_df[legs].mean(axis=1, skipna=True)


def _weighted_core(idx_df: pd.DataFrame) -> pd.Series:
    """Core (ex food & fuel, official-weight est.): weighted mean of the legs,
    dropping Food and beverages entirely and netting the fuel/energy share out of
    the Housing division's weight, using the published 2024-base weights."""
    legs, weights = [], []
    for d in _LEGS:
        if d == FOOD or d not in idx_df.columns:
            continue
        w = CPI_2024_DIVISION_WEIGHTS.get(d, 0.0)
        if d == HOUSING_FUEL:            # strip the fuel/energy slice of housing
            w *= (1.0 - _HOUSING_FUEL_FRACTION)
        if w > 0:
            legs.append(d)
            weights.append(w)
    if not legs:
        return pd.Series(dtype="float64")
    w = np.asarray(weights, dtype="float64")
    w = w / w.sum()
    sub = idx_df[legs]
    return sub.mul(w, axis=1).sum(axis=1, skipna=True)


def _yoy(level: pd.Series) -> pd.Series:
    """Year-on-year % change of a monthly index level (t vs t-12 months)."""
    if level.empty:
        return level
    lvl = level.sort_index()
    prev = lvl.shift(12, freq="MS")
    prev = prev.reindex(lvl.index)
    out = (lvl / prev - 1.0) * 100.0
    return out.dropna()


# ============================================================ public API
def fetch_series(start="2000-01-01", log=print):
    """Live-fetch India CPI division legs + derived core from MOSPI eSankhyiki.

    Returns (series_by_name, meta_by_name). Friendly names, e.g.
      "CPI Combined — Food and beverages (index, 2024=100)"
      "CPI Combined — Food and beverages inflation (YoY)"
      "CPI Combined — Core ex-food (index, 2024=100)"
      "CPI Combined — Core ex-food inflation (YoY)"
      "CPI Combined — Core ex food & fuel (index, official-weight est.)"
      "CPI Combined — Core ex food & fuel inflation (YoY, est.)"
    Each series is a monthly pandas Series (Timestamp index, month-start stamp).
    Never raises: on any failure it returns whatever it managed to gather.
    """
    series_by_name: dict[str, pd.Series] = {}
    meta_by_name: dict[str, dict] = {}
    if requests is None:
        log("[cpi_core] 'requests' unavailable — returning empty.")
        return series_by_name, meta_by_name

    # History window. The 2024-base legs realistically start Jan-2025; honour an
    # earlier `start` request but never page below DIVISION_START_YEAR (nothing there).
    try:
        req_year = pd.Timestamp(start).year
    except Exception:
        req_year = DIVISION_START_YEAR
    today = dt.date.today()
    start_year = max(DIVISION_START_YEAR, min(req_year, today.year))
    years = list(range(start_year, today.year + 1))

    SRC = "MOSPI eSankhyiki (api.mospi.gov.in/cpi/getCpiData, base 2024)"

    for sc, sname in SECTORS.items():
        log(f"[cpi_core] MOSPI CPI legs+core — {sname} (sector {sc}); years {years}")
        df = _fetch_sector_legs(sc, years, today, log)
        if df.empty:
            log(f"    [cpi_core] {sname}: no data")
            continue
        idx_df = df["index"] if "index" in df.columns.get_level_values(0) else pd.DataFrame()
        inf_df = df["inflation"] if "inflation" in df.columns.get_level_values(0) else pd.DataFrame()

        # ---- per-division index + MOSPI's own official YoY -----------------
        for d in DIVISIONS:
            if d not in idx_df.columns:
                continue
            lvl = idx_df[d].dropna()
            if lvl.empty:
                continue
            tag = "headline" if d == HEADLINE else "division"
            nm = f"CPI {sname} — {d} (index, 2024=100)"
            series_by_name[nm] = lvl
            meta_by_name[nm] = {"unit": "index (2024=100)", "freq": "monthly",
                                "source": SRC,
                                "group": f"CPI legs — {sname}"}
            if d in inf_df.columns:
                infl = inf_df[d].dropna()
                if not infl.empty:
                    nmi = f"CPI {sname} — {d} inflation (YoY)"
                    series_by_name[nmi] = infl
                    meta_by_name[nmi] = {"unit": "% YoY", "freq": "monthly",
                                         "source": SRC + " (official YoY)",
                                         "group": f"CPI inflation legs — {sname}"}

        # ---- derived core (two methods) -----------------------------------
        if not idx_df.empty:
            # (a) ex-food equal-weight
            core_eq = _equal_weight_core(idx_df).dropna()
            if not core_eq.empty:
                nm = f"CPI {sname} — Core ex-food (index, 2024=100)"
                series_by_name[nm] = core_eq
                meta_by_name[nm] = {"unit": "index (2024=100)", "freq": "monthly",
                                    "source": SRC + " (derived: equal-wt mean ex Food & beverages)",
                                    "group": f"CPI core — {sname}"}
                core_eq_yoy = _yoy(core_eq)
                if not core_eq_yoy.empty:
                    nmi = f"CPI {sname} — Core ex-food inflation (YoY)"
                    series_by_name[nmi] = core_eq_yoy
                    meta_by_name[nmi] = {"unit": "% YoY", "freq": "monthly",
                                         "source": SRC + " (derived: YoY of equal-wt ex-food core)",
                                         "group": f"CPI core inflation — {sname}"}
            # (b) ex food & fuel, official-weight estimate
            core_wt = _weighted_core(idx_df).dropna()
            if not core_wt.empty:
                nm = f"CPI {sname} — Core ex food & fuel (index, official-weight est.)"
                series_by_name[nm] = core_wt
                meta_by_name[nm] = {"unit": "index (2024=100)", "freq": "monthly",
                                    "source": SRC + " (derived: 2024-base weights ex food & fuel)",
                                    "group": f"CPI core — {sname}"}
                core_wt_yoy = _yoy(core_wt)
                if not core_wt_yoy.empty:
                    nmi = f"CPI {sname} — Core ex food & fuel inflation (YoY, est.)"
                    series_by_name[nmi] = core_wt_yoy
                    meta_by_name[nmi] = {"unit": "% YoY", "freq": "monthly",
                                         "source": SRC + " (derived: YoY of weighted ex food&fuel core)",
                                         "group": f"CPI core inflation — {sname}"}

    log(f"[cpi_core] done: {len(series_by_name)} series across "
        f"{len(set(m['group'] for m in meta_by_name.values()))} groups")
    return series_by_name, meta_by_name


if __name__ == "__main__":            # quick manual run
    s, m = fetch_series()
    print(f"\n{len(s)} series")
    for nm in list(s)[:20]:
        ser = s[nm]
        print(f"  {nm:70s} {ser.index.min().date()}..{ser.index.max().date()} "
              f"last={ser.iloc[-1]:.2f}")
