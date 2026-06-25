"""
MOSPI IIP — Use-Based classification + NIC manufacturing sub-divisions (Vistas).

Index of Industrial Production (IIP) breaks total factory output two ways. The
*sectoral* cut (Mining / Manufacturing / Electricity) already lives in macro.py.
This module adds the two cuts macro.py deliberately drops:

  1. The **use-based classification** — the 6 demand-side buckets MOSPI publishes
     alongside the sectoral cut. This is the *economically* interesting split,
     because it tells you WHAT KIND of goods the factories are making:
        • Primary goods            (mining + electricity + basic inputs)
        • Capital goods            (machinery, plant — the INVESTMENT cycle signal)
        • Intermediate goods       (parts/materials feeding further production)
        • Infrastructure/Construction goods (cement, steel structures, cables…)
        • Consumer durables        (cars, fridges, phones — discretionary demand)
        • Consumer non-durables    (FMCG, staples — defensive demand)
     Capital goods is the headline cyclical read: a sustained pickup there means
     firms are investing in new capacity (a leading sign of the next up-leg);
     a slump means the investment cycle has stalled.

  2. The **23 NIC manufacturing divisions** — the fine-grained manufacturing
     breakdown (Basic Metals, Chemicals, Motor Vehicles, Pharma, Textiles, …).
     macro.py keeps only the aggregate "Manufacturing" row and throws these away;
     here we surface each division so the terminal can show which industries are
     actually pulling the manufacturing index up or down.

ENDPOINT (one-shot, all rows; no paging needed — each type is well under the cap):
    GET https://api.mospi.gov.in/api/iip/getIipData
        ?base_year=2011-12 &frequency=Monthly &type=<type> &limit=5000

Discovered live (2026-06-21) — the `type` values the API actually accepts:
    'General'             -> category 'General'                       (the all-IIP headline; macro.py owns it)
    'Sectoral'            -> categories Mining / Manufacturing / Electricity
                            (Manufacturing rows ALSO carry the 23 NIC divisions in `sub_category`)
    'Use-based category'  -> the 6 use-based buckets above            (★ note the EXACT spelling)
NOTE the gotchas, burned in from probing:
  • `type='UseBased'` / 'Use Based' / 'Use-Based' all return an EMPTY data array
    (HTTP 200, {"data":[],"msg":"No Data Found"}). The ONLY accepted spelling is
    'Use-based category'.
  • 'Infrastructure/ Construction Goods' has a SPACE after the slash (raw category text).
  • Aggregate sectoral rows have `sub_category` == '' (empty); the 23 NIC divisions
    are the rows where `sub_category` is non-empty under category 'Manufacturing'.

Each row's fields: base_year, year, month, type, category, sub_category, index, growth_rate.
  • `index`        = the production index on the 2011-12 = 100 base (float).
  • `growth_rate`  = MOSPI's published YoY % growth for that month (float).
We surface BOTH for every series ("… (index)" and "… (YoY)").

Host serves a Govt self-signed cert chain certifi rejects -> verify=False (public,
read-only data; we suppress the urllib3 warning). Graceful-degrade: any network/parse
failure returns whatever was already collected, never raises. Polite jittered pacing
between the (few) calls.

Provenance: written for Vistas 2026-06-21. Standalone; imports nothing from the
research engine. Mirrors macro.py's MOSPI IIP conventions (same endpoint, headers,
month parsing, FIRST-of-month timestamp).
"""
from __future__ import annotations

import time
import random
import datetime as dt

import pandas as pd

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

try:                                    # api.mospi.gov.in serves a Govt self-signed cert
    import urllib3                       # the certifi chain rejects -> we verify=False that host
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:                       # pragma: no cover
    pass

# --------------------------------------------------------------------------- config
MOSPI_API = "https://api.mospi.gov.in/api"
IIP_ENDPOINT = f"{MOSPI_API}/iip/getIipData"
_VERIFY = False                          # Govt self-signed cert
_BASE_YEAR = "2011-12"                   # IIP base; indices are 2011-12 = 100
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://esankhyiki.mospi.gov.in/",
    "Accept": "application/json, text/plain, */*",
}

# Month name -> number (the API stamps full English month names: "April", "March", …).
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
# Also accept 3-letter abbreviations and numeric, defensively.
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
for _i in range(1, 13):
    _MONTHS[str(_i)] = _i
    _MONTHS[f"{_i:02d}"] = _i

# The 6 use-based buckets, in MOSPI's publication order. Keys are the RAW category
# strings the API returns (note the space in "Infrastructure/ Construction Goods");
# values are the friendly display stem.
_USEBASED = {
    "Primary Goods":                       "Primary goods",
    "Capital Goods":                       "Capital goods",
    "Intermediate Goods":                  "Intermediate goods",
    "Infrastructure/ Construction Goods":  "Infrastructure/Construction goods",
    "Consumer Durables":                   "Consumer durables",
    "Consumer Non-durables":               "Consumer non-durables",
}


# --------------------------------------------------------------------------- helpers
def _to_float(raw):
    """Parse a published number ('173.3', '14.6', '-', '') to float or None."""
    if raw is None:
        return None
    try:
        s = str(raw).replace(",", "").strip()
        if s == "" or s == "-":
            return None
        return float(s)
    except Exception:
        return None


def _ts(year, month) -> "pd.Timestamp | None":
    """First-of-month Timestamp from a year + English month name, matching macro.py."""
    mm = _MONTHS.get(str(month).strip().lower())
    if not mm:
        return None
    try:
        return pd.Timestamp(int(year), mm, 1)
    except Exception:
        return None


def _fetch_type(type_value: str, log=lambda m: None) -> list:
    """One polite GET for a given IIP `type`. Returns the raw record list (possibly
    empty). Never raises — on any failure logs and returns []."""
    if requests is None:
        log("[iip-ub] requests unavailable; skipping")
        return []
    try:
        r = requests.get(
            IIP_ENDPOINT,
            params={"base_year": _BASE_YEAR, "frequency": "Monthly",
                    "type": type_value, "limit": 5000},
            headers=_HEADERS, verify=_VERIFY, timeout=90,
        )
    except Exception as e:
        log(f"[iip-ub] GET type={type_value!r} failed: {e}")
        return []
    # MOSPI SPA trap: a 200 HTML shell is NOT real JSON. Confirm the body is JSON.
    body = (r.text or "").lstrip()
    if not body.startswith("{") and not body.startswith("["):
        log(f"[iip-ub] type={type_value!r} returned non-JSON (status {r.status_code}, "
            f"head {body[:40]!r})")
        return []
    try:
        recs = r.json().get("data", [])
    except Exception as e:
        log(f"[iip-ub] type={type_value!r} JSON parse failed: {e}")
        return []
    log(f"[iip-ub] type={type_value!r}: {len(recs)} rows")
    return recs


def _series_from_rows(rows: list, field: str) -> "pd.Series":
    """Build a monthly Series (first-of-month index) from a list of records, taking
    record[field] as the value. Drops unparseable rows, sorts, de-dups (keep last)."""
    data = {}
    for x in rows:
        ts = _ts(x.get("year"), x.get("month"))
        if ts is None:
            continue
        v = _to_float(x.get(field))
        if v is None:
            continue
        data[ts] = v                     # de-dup: last write wins
    if not data:
        return pd.Series(dtype="float64")
    return pd.Series(data, dtype="float64").sort_index()


# --------------------------------------------------------------------------- public API
def fetch_series(start: str = "2000-01-01", log=print):
    """Fetch IIP use-based buckets + NIC manufacturing divisions from MOSPI eSankhyiki.

    Returns (series_by_name, meta_by_name):
      • series_by_name[name] -> monthly pandas.Series (DatetimeIndex, first-of-month, float)
      • meta_by_name[name]   -> {"unit","freq","source","group"}

    For EACH use-based bucket and EACH manufacturing division we expose TWO series:
        "<name> (index)"  -> production index, 2011-12 = 100
        "<name> (YoY)"    -> MOSPI's published year-on-year % growth

    `start` filters the output to observations on/after that date (IIP starts Apr-2012
    on this base regardless). Graceful-degrade: a failed call simply omits its series.
    """
    series_by_name: dict[str, pd.Series] = {}
    meta_by_name: dict[str, dict] = {}

    try:
        start_ts = pd.Timestamp(start)
    except Exception:
        start_ts = pd.Timestamp("2000-01-01")

    def _add(name, s, unit, group):
        if start_ts is not None and len(s):
            s = s[s.index >= start_ts]
        if s is None or not len(s):
            return
        series_by_name[name] = s
        meta_by_name[name] = {
            "unit": unit, "freq": "monthly",
            "source": "MOSPI eSankhyiki (IIP, base 2011-12)", "group": group,
        }

    # --- (1) Use-based classification (the 6 demand-side buckets) -----------------
    ub_rows = _fetch_type("Use-based category", log=log)
    by_cat: dict[str, list] = {}
    for x in ub_rows:
        cat = x.get("category")
        if cat in _USEBASED:
            by_cat.setdefault(cat, []).append(x)
    # Emit in MOSPI's canonical order.
    for raw_cat, stem in _USEBASED.items():
        rows = by_cat.get(raw_cat, [])
        if not rows:
            continue
        _add(f"IIP {stem} (index)", _series_from_rows(rows, "index"),
             "index (2011-12=100)", "IIP — Use-based")
        _add(f"IIP {stem} (YoY)", _series_from_rows(rows, "growth_rate"),
             "% YoY", "IIP — Use-based")

    time.sleep(random.uniform(0.3, 0.7))

    # --- (2) NIC manufacturing sub-divisions -------------------------------------
    # Pulled from the 'Sectoral' type: rows with category 'Manufacturing' AND a
    # non-empty sub_category are the 23 NIC divisions (the empty-sub_category row is
    # the aggregate Manufacturing index, which macro.py already owns).
    sec_rows = _fetch_type("Sectoral", log=log)
    by_div: dict[str, list] = {}
    for x in sec_rows:
        if x.get("category") != "Manufacturing":
            continue
        sub = str(x.get("sub_category") or "").strip()
        if not sub:                      # skip the aggregate Manufacturing row
            continue
        by_div.setdefault(sub, []).append(x)
    for sub in sorted(by_div):
        rows = by_div[sub]
        disp = _shorten_division(sub)
        _add(f"IIP Mfg — {disp} (index)", _series_from_rows(rows, "index"),
             "index (2011-12=100)", "IIP — Manufacturing (NIC)")
        _add(f"IIP Mfg — {disp} (YoY)", _series_from_rows(rows, "growth_rate"),
             "% YoY", "IIP — Manufacturing (NIC)")

    n_idx = sum(1 for k in series_by_name if k.endswith("(index)"))
    log(f"[iip-ub] done: {len(series_by_name)} series "
        f"({n_idx} index + {len(series_by_name) - n_idx} YoY)")
    return series_by_name, meta_by_name


def _shorten_division(name: str) -> str:
    """Trim MOSPI's verbose NIC division labels for display, keeping the industry.
    'Manufacture of Basic Metals' -> 'Basic Metals'; falls back to the raw name."""
    n = name.strip()
    for pre in ("Manufacture of ", "Manufacturing of "):
        if n.lower().startswith(pre.lower()):
            n = n[len(pre):]
            break
    # Title-case the first letter so 'basic pharmaceutical…' reads cleanly.
    if n and n[0].islower():
        n = n[0].upper() + n[1:]
    return n


# --------------------------------------------------------------------------- self-test
if __name__ == "__main__":
    s, m = fetch_series(start="2012-01-01")
    print(f"\n=== {len(s)} series ===")
    for name in list(s)[:60]:
        ser = s[name]
        print(f"  {name:<48} {ser.index.min().date()}..{ser.index.max().date()} "
              f"n={len(ser)} last={ser.iloc[-1]}")
