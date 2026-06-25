"""
MOSPI National Accounts (GDP / GVA) layer for Vistas — official, free source.

Pulls India's National Accounts Statistics (NAS) from MOSPI eSankhyiki: real
(constant-price) and nominal (current-price) GDP & GVA, real GDP growth, and the
expenditure split (PFCE, GFCF) — at BOTH Annual and Quarterly frequency, with the
deep Back-series history (Annual back to 1950-51). Same return contract as the rest
of Vistas' fetchers (macro.py / world.py): never raises on a network/parse failure;
a missing piece simply doesn't appear, and the caller keeps serving what it has.

WHAT IS NAS (plain words)
  • GDP (Gross Domestic Product) = the total value of everything the economy
    produced in a period. "Real"/constant-price strips out inflation (volume only,
    measured in a fixed base-year's prices); "nominal"/current-price includes
    inflation (today's prices). Real GDP growth ~6-8% YoY in recent years.
  • GVA (Gross Value Added) = GDP measured from the production side, before product
    taxes net of subsidies (GDP = GVA + net product taxes). Reported per industry.
  • PFCE (Private Final Consumption Expenditure) = household spending — the biggest
    GDP component (~60% of GDP). GFCF (Gross Fixed Capital Formation) = investment in
    fixed assets (~30% of GDP). Both are GDP expenditure-side components.

==================== HOW THE DATA IS FETCHED (reverse-engineered) ====================
The portal's Dash viz page (esankhyiki.mospi.gov.in/viz/nas?viz_req=true...) is a
DECOY for scraping: the `viz_req=true` GET just returns the Dash SPA HTML shell, and
the figure is built by a server-side callback that (at time of writing) is itself
BROKEN — it tries to select a DataFrame column named `value` that no longer exists
(the value columns are `current_price`/`constant_price`), so it throws
`KeyError: "['value'] not in index"` from /app/nas.py for the Current series.

The REAL data route is the prod JSON API, and `getNasData` is NOT dead — a plain GET
with the right params returns clean JSON (the HTTP-500 the spec saw came from
missing/insufficient params). The exact working call is:

  GET https://api.mospi.gov.in/api/nas/getNasData
      ?base_year=2011-12 &indicator_code=<N> &series=<Current|Back> &frequency=<1|2> &page=<p>
  headers: desktop User-Agent + Referer https://esankhyiki.mospi.gov.in/ ; verify=False
           (MOSPI presents a Govt self-signed cert chain certifi rejects).

Response shape (reverse-engineered on first pull):
  { "data": [ {record}, ... ], "meta_data": {"page","totalRecords","totalPages","recordPerPage"},
    "msg": "Data fetched successfully", "statusCode": true }
  record = { base_year, series, year, indicator, frequency, revision, quarter,
             current_price, constant_price, unit, [industry, subindustry, institutional_sector] }
  • `current_price` (nominal) AND `constant_price` (real) are BOTH in the SAME record,
    in ₹ Crore (1 crore = 1e7 rupees). For the growth indicators (21/22) both are %.
  • Paginated 10 records/page (`recordPerPage` is server-fixed; `page` walks pages).
  • The `frequency` param is IGNORED by the server — every page mixes Annual+Quarterly;
    we filter client-side on the `frequency` field.
  • `series=Current` = the modern 2011-12-base series (2011-12 → latest, A+Q).
    `series=Back`    = the back-cast Annual history (1950-51 → 2011-12).
  • `revision` carries the vintage (First/Second Advance, Provisional, First/Second/Third
    Revised, Final). Multiple per year — we keep the MOST-FINAL per (year,quarter).
  • GVA (indicator 1) is split by `industry`; the economy-wide total is the row with
    industry == "Total Gross Value Added" (subindustry/institutional_sector both null).
    GFCF (9) carries `institutional_sector`; the aggregate is institutional_sector null.

INDICATOR CODES (from getNasIndicatorList?viz_status=Active + probing):
  5=GDP, 1=GVA, 9=GFCF, 10=PFCE, 22=GDP growth%, 21=GVA growth% (others 11/14/15
  exist in the catalog history but only 1/5/21/22 are flagged "Active").

Provenance: written for Vistas 2026-06-21. Self-contained; imports nothing from the
research engine. analytics.py untouched (this is a data layer, no JS-parity port).
"""
from __future__ import annotations

import time
import random

import pandas as pd

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

try:                                    # api.mospi.gov.in serves a Govt self-signed cert
    import urllib3                       # chain certifi rejects -> we verify=False that host
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:                       # pragma: no cover
    pass

# -------------------------------------------------------------------- endpoints
NAS_API = "https://api.mospi.gov.in/api/nas/getNasData"
NAS_CATALOG = "https://api.mospi.gov.in/api/nas/getNasIndicatorList"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://esankhyiki.mospi.gov.in/"}
_VERIFY = False
_BASE_YEAR = "2011-12"

# Revision finality ranking: higher = more final/authoritative. We keep the highest
# rank per (year, quarter) so each period contributes its latest official estimate.
_REVISION_RANK = {
    "Final Estimates": 9,
    "Third Revised Estimates": 8,
    "Second Revised Estimates": 7,
    "First Revised Estimates": 6,
    "Provisional Estimates": 5,
    "Additional Revision": 4,
    "Second Advance Estimates": 3,
    "First Advance Estimates": 2,
    "None": 1, None: 1, "": 1,
}

# Friendly group + the quart/annual indicators we build.
_GROUP = "National Accounts (GDP)"


def _to_float(raw):
    try:
        if raw is None:
            return None
        return float(str(raw).replace(",", "").strip())
    except Exception:
        return None


def _fy_end(year_str: str) -> "pd.Timestamp | None":
    """Indian fiscal year 'YYYY-YY' -> fiscal year-END date = 31-Mar of the 2nd
    calendar year. e.g. '2023-24' -> 2024-03-31."""
    try:
        a = int(str(year_str).split("-")[0])
        return pd.Timestamp(a + 1, 3, 31)          # Apr(yr)..Mar(yr+1) -> stamp Mar 31
    except Exception:
        return None


def _quarter_end(year_str: str, quarter: str) -> "pd.Timestamp | None":
    """Fiscal quarter -> calendar quarter-END date. India FY: Q1=Apr-Jun(Jun30),
    Q2=Jul-Sep(Sep30), Q3=Oct-Dec(Dec31), Q4=Jan-Mar(Mar31 of next cal yr)."""
    try:
        a = int(str(year_str).split("-")[0])       # FY start calendar year
    except Exception:
        return None
    q = str(quarter or "").strip().upper().replace("QUARTER", "Q").replace(" ", "")
    table = {"Q1": (a, 6, 30), "Q2": (a, 9, 30), "Q3": (a, 12, 31), "Q4": (a + 1, 3, 31)}
    if q not in table:
        return None
    y, m, d = table[q]
    return pd.Timestamp(y, m, d)


def _pull_indicator(indicator_code: int, series: str, log=lambda m: None,
                    page_cap: int = 250) -> list[dict]:
    """All getNasData records for one indicator+series (Current|Back), paginated.
    Returns [] on any failure (never raises). Polite jittered pacing between pages."""
    if requests is None:
        return []
    out, page, pages = [], 1, 1
    while page <= pages and page <= page_cap:
        try:
            r = requests.get(NAS_API, timeout=60, verify=_VERIFY, headers=_HEADERS,
                             params={"base_year": _BASE_YEAR, "indicator_code": indicator_code,
                                     "series": series, "page": page})
            # Guard the MOSPI SPA trap: a real endpoint returns JSON starting with '{'.
            body = r.text.lstrip()
            if not body.startswith("{"):
                log(f"    nas ind{indicator_code} {series} p{page}: non-JSON ({r.status_code})")
                break
            j = r.json()
        except Exception as e:
            log(f"    nas ind{indicator_code} {series} p{page} failed: {e}")
            break
        recs = j.get("data") or []
        out.extend(recs)
        try:
            pages = int(j.get("meta_data", {}).get("totalPages", 1) or 1)
        except Exception:
            pages = 1
        page += 1
        time.sleep(random.uniform(0.15, 0.40))
    return out


def _is_aggregate(rec: dict) -> bool:
    """Keep only the economy-wide aggregate rows (drop industry / sub-industry /
    institutional-sector breakdown rows). The GVA total carries
    industry == 'Total Gross Value Added'; all other indicators have no breakdown
    keys or carry them as null on the aggregate row."""
    ind = (rec.get("industry") or "").strip()
    if ind and ind != "Total Gross Value Added":
        return False
    if (rec.get("subindustry") or "").strip():
        return False
    if (rec.get("institutional_sector") or "").strip():
        return False
    return True


def _build_series(recs: list[dict], frequency: str, price_field: str) -> "pd.Series":
    """Reduce raw records -> one clean Timestamp-indexed Series for the requested
    frequency ('Annual'|'Quarterly') and price field ('current_price'|'constant_price').
    Keeps the MOST-FINAL revision per period (year[,quarter])."""
    best: dict[pd.Timestamp, tuple[int, float]] = {}
    for x in recs:
        if (x.get("frequency") or "") != frequency:
            continue
        if not _is_aggregate(x):
            continue
        if frequency == "Annual":
            ts = _fy_end(x.get("year"))
        else:
            ts = _quarter_end(x.get("year"), x.get("quarter"))
        if ts is None:
            continue
        val = _to_float(x.get(price_field))
        if val is None:
            continue
        rank = _REVISION_RANK.get(x.get("revision"), 1)
        prev = best.get(ts)
        if prev is None or rank >= prev[0]:
            best[ts] = (rank, val)
    if not best:
        return pd.Series(dtype="float64")
    s = pd.Series({ts: v for ts, (_, v) in best.items()}, dtype="float64").sort_index()
    return s


def _splice_back(current: "pd.Series", back: "pd.Series") -> "pd.Series":
    """Extend a Current-series Annual series backward with the Back series for the
    pre-overlap history. We PREFER Current wherever it exists (modern 2011-12 base);
    Back only fills earlier years. NOTE: Back is on the old base and may have a small
    level kink at the 2011-12 join — fine for long-run shape, but for decimal-exact
    modern levels use the Current portion (2011-12 onward)."""
    if back is None or back.empty:
        return current
    if current is None or current.empty:
        return back
    earliest_cur = current.index.min()
    add = back[back.index < earliest_cur]
    return pd.concat([add, current]).sort_index()


# ============================================================ PUBLIC API

# (name, indicator_code, frequency, price_field, unit, freq_label) for the simple
# level/growth series. Expenditure SHARES are derived afterwards.
# IMPORTANT: every NAME must be unique across the whole module — series_by_name is
# keyed by name, so an Annual and a Quarterly series of the same measure MUST carry
# distinct names (we suffix the frequency) or one silently overwrites the other.
_SPECS = [
    # Real (constant-price) GDP & GVA — volume, inflation stripped out
    ("GDP — real (constant prices, Rs cr, annual)", 5, "Annual", "constant_price", "Rs crore (2011-12 base)", "annual"),
    ("GDP — real (constant prices, Rs cr, quarterly)", 5, "Quarterly", "constant_price", "Rs crore (2011-12 base)", "quarterly"),
    ("GVA — real (constant prices, Rs cr, annual)", 1, "Annual", "constant_price", "Rs crore (2011-12 base)", "annual"),
    ("GVA — real (constant prices, Rs cr, quarterly)", 1, "Quarterly", "constant_price", "Rs crore (2011-12 base)", "quarterly"),
    # Nominal (current-price) GDP & GVA — at today's prices
    ("GDP — nominal (current prices, Rs cr, annual)", 5, "Annual", "current_price", "Rs crore", "annual"),
    ("GDP — nominal (current prices, Rs cr, quarterly)", 5, "Quarterly", "current_price", "Rs crore", "quarterly"),
    ("GVA — nominal (current prices, Rs cr, annual)", 1, "Annual", "current_price", "Rs crore", "annual"),
    ("GVA — nominal (current prices, Rs cr, quarterly)", 1, "Quarterly", "current_price", "Rs crore", "quarterly"),
    # PFCE / GFCF nominal levels (for the expenditure split + shares)
    ("PFCE — nominal (current prices, Rs cr, annual)", 10, "Annual", "current_price", "Rs crore", "annual"),
    ("PFCE — nominal (current prices, Rs cr, quarterly)", 10, "Quarterly", "current_price", "Rs crore", "quarterly"),
    ("GFCF — nominal (current prices, Rs cr, annual)", 9, "Annual", "current_price", "Rs crore", "annual"),
    ("GFCF — nominal (current prices, Rs cr, quarterly)", 9, "Quarterly", "current_price", "Rs crore", "quarterly"),
]

# Official growth-rate indicators (server-published % YoY): 22=GDP, 21=GVA.
# current_price field = nominal growth %, constant_price field = real growth %.
_GROWTH_SPECS = [
    ("GDP growth — real (% YoY, official, annual)", 22, "Annual", "constant_price", "% YoY", "annual"),
    ("GDP growth — real (% YoY, official, quarterly)", 22, "Quarterly", "constant_price", "% YoY", "quarterly"),
    ("GDP growth — nominal (% YoY, official, annual)", 22, "Annual", "current_price", "% YoY", "annual"),
    ("GVA growth — real (% YoY, official, annual)", 21, "Annual", "constant_price", "% YoY", "annual"),
]


def fetch_series(start="2000-01-01", log=print):
    """Vistas-standard fetcher for MOSPI National Accounts.

    Returns (series_by_name, meta_by_name):
      • series_by_name[name] -> pandas Series indexed by Timestamp (fiscal year-end
        for Annual, calendar quarter-end for Quarterly), float values.
      • meta_by_name[name]   -> {"unit","freq","source","group"}.

    Builds: real & nominal GDP and GVA (Annual + Quarterly), official real & nominal
    GDP growth and real GVA growth %, PFCE & GFCF nominal levels (Annual + Quarterly),
    and PFCE-as-%-of-GDP and GFCF-as-%-of-GDP shares (derived from the nominal levels,
    same frequency). Annual GDP/GVA levels are spliced with the Back series so history
    runs from 1950-51; the modern (Current, 2011-12-base) portion is authoritative.

    Graceful-degrade: if the API is unreachable, returns ({}, {}) — never raises.
    """
    if requests is None:
        log("mospi_nas: 'requests' unavailable — skipping.")
        return {}, {}

    try:
        start_ts = pd.Timestamp(start)
    except Exception:
        start_ts = None

    # Cache raw record pulls so we hit each (indicator, series) at most once.
    raw: dict[tuple[int, str], list[dict]] = {}

    def _get(ind, ser):
        key = (ind, ser)
        if key not in raw:
            raw[key] = _pull_indicator(ind, ser, log=log)
            time.sleep(random.uniform(0.2, 0.5))
        return raw[key]

    series_by_name: dict[str, pd.Series] = {}
    meta_by_name: dict[str, dict] = {}

    def _emit(name, s, unit, freq_label):
        if s is None or s.empty:
            return
        if start_ts is not None:
            s = s[s.index >= start_ts]
        if s.empty:
            return
        series_by_name[name] = s
        meta_by_name[name] = {"unit": unit, "freq": freq_label,
                              "source": "MOSPI eSankhyiki (National Accounts)", "group": _GROUP}

    # ---- level + growth series (splice Back into Annual GDP/GVA levels) ----
    for name, ind, freq, field, unit, freq_label in _SPECS:
        cur = _build_series(_get(ind, "Current"), freq, field)
        if freq == "Annual":
            back = _build_series(_get(ind, "Back"), "Annual", field)
            cur = _splice_back(cur, back)
        _emit(name, cur, unit, freq_label)

    for name, ind, freq, field, unit, freq_label in _GROWTH_SPECS:
        s = _build_series(_get(ind, "Current"), freq, field)
        _emit(name, s, unit, freq_label)

    # ---- derived expenditure SHARES (% of nominal GDP), same frequency ----
    # share = component_nominal / GDP_nominal * 100, aligned on common dates.
    for comp_label, share_name, freq_label, freq in [
        ("PFCE", "PFCE — share of GDP (%, annual)", "annual", "Annual"),
        ("PFCE", "PFCE — share of GDP (%, quarterly)", "quarterly", "Quarterly"),
        ("GFCF", "GFCF — share of GDP (%, annual)", "annual", "Annual"),
        ("GFCF", "GFCF — share of GDP (%, quarterly)", "quarterly", "Quarterly"),
    ]:
        comp_ind = 10 if comp_label == "PFCE" else 9
        gdp = _build_series(_get(5, "Current"), freq, "current_price")
        comp = _build_series(_get(comp_ind, "Current"), freq, "current_price")
        if gdp.empty or comp.empty:
            continue
        idx = comp.index.intersection(gdp.index)
        if len(idx) == 0:
            continue
        share = (comp.reindex(idx) / gdp.reindex(idx) * 100.0).dropna().sort_index()
        _emit(share_name, share, "% of nominal GDP", freq_label)

    log(f"mospi_nas: built {len(series_by_name)} series.")
    return series_by_name, meta_by_name


if __name__ == "__main__":                          # pragma: no cover
    s, m = fetch_series()
    for name in sorted(s):
        ser = s[name]
        print(f"{name:48s} n={len(ser):4d} {ser.index.min().date()}..{ser.index.max().date()} "
              f"last={ser.iloc[-1]:.4g}  [{m[name]['freq']}, {m[name]['unit']}]")
