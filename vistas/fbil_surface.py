"""
vistas/fbil_surface.py — FBIL money-market + FX surface (standalone fetcher)
===========================================================================

Pulls the Financial Benchmarks India Pvt Ltd (FBIL) daily benchmark surface that
sits on top of the existing FBIL T-bill / G-sec par-yield wiring in ``macro.py``.
This module adds four families that were not previously sourced:

    termmibor  — Term MIBOR (Mumbai Interbank Offer Rate), tenors 14D / 1M / 3M
    cd         — Certificate-of-Deposit benchmark curve, 7 tenors (14D … 12M)
    refrates   — Daily FX reference rates, 6 currency pairs (INR per unit foreign)
    fwdpremia  — USD/INR forward premia, per tenor: annualised % AND rupee paise

Everything comes from the public, no-auth FBIL inline-JSON endpoint:

    GET https://www.fbil.org.in/wasdm/<prod>/fetchfiltered
        ?fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD&authenticated=false

which returns a flat JSON *list* of records (one record per (date, sub-series)).
We split that list into one pandas Series per (product, tenor/pair), date-indexed.

Provenance / conventions: this re-implements the same FBIL request contract used by
``vistas/macro.py`` (``FBIL_BASE`` + ``fetchfiltered`` + ``authenticated=false`` +
desktop UA + Referer https://www.fbil.org.in/ + ``verify=False`` because FBIL serves
a self-signed-ish cert that trips urllib3). It imports NOTHING from macro.py so the
lead can wire it independently.

Public API (uniform across Vistas fetch modules)
------------------------------------------------
    fetch_series(start="2000-01-01", log=print)
        -> (series_by_name: dict[str, pandas.Series],
            meta_by_name:   dict[str, dict])

    Each Series: pandas.Timestamp index (daily, native), float values.
    meta[name] = {"unit", "freq", "source", "group"} with friendly human names as keys.

Graceful-degrade contract: on any network failure / 404 / holiday / malformed body
we log and skip that product, returning whatever else succeeded. Never raises.
"""

from __future__ import annotations

import datetime as _dt
import random as _random
import time as _time

import pandas as _pd

try:
    import requests as _requests
except Exception:  # pragma: no cover - requests is a hard dep but degrade anyway
    _requests = None

# Silence the urllib3 "InsecureRequestWarning" that verify=False triggers.
try:  # pragma: no cover
    import urllib3 as _urllib3

    _urllib3.disable_warnings(_urllib3.exceptions.InsecureRequestWarning)
except Exception:  # pragma: no cover
    pass


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
FBIL_BASE = "https://www.fbil.org.in/wasdm"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.fbil.org.in/",
    "Accept": "application/json, text/plain, */*",
}

# History floor for FBIL benchmarks. The wasdm endpoint only serves the modern
# benchmark era; 2014-01-01 is comfortably before any of these series begin and
# the server clips to whatever it actually has, so over-asking is harmless. We
# cap the requested start at this floor (a caller asking for "2000-01-01" just
# gets the full available FBIL history).
_FBIL_HISTORY_FLOOR = "2014-01-01"

# Friendly FX-pair labels keyed by the raw FBIL ``subProdName``. FBIL quotes
# rupees per unit of foreign currency (e.g. "INR / 1 USD", "INR / 100 JPY").
_FX_LABELS = {
    "INR / 1 USD": ("FBIL FX reference — USD/INR (INR per USD)", "INR per USD"),
    "INR / 1 EUR": ("FBIL FX reference — EUR/INR (INR per EUR)", "INR per EUR"),
    "INR / 1 GBP": ("FBIL FX reference — GBP/INR (INR per GBP)", "INR per GBP"),
    "INR / 1 AED": ("FBIL FX reference — AED/INR (INR per AED)", "INR per AED"),
    "INR / 100 JPY": ("FBIL FX reference — JPY/INR (INR per 100 JPY)", "INR per 100 JPY"),
    "INR / 10000 IDR": ("FBIL FX reference — IDR/INR (INR per 10000 IDR)", "INR per 10000 IDR"),
}


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def _to_float(x):
    """Coerce a raw JSON scalar to float; return None on anything unparseable.
    FBIL mixes plain numbers (rate=5.88) with quoted strings (rate="1.9292")."""
    if x is None:
        return None
    try:
        s = str(x).strip().replace(",", "")
        if s == "" or s.lower() in ("na", "n.a.", "-", "--", "null"):
            return None
        return float(s)
    except Exception:
        return None


def _ts(x):
    """Parse an FBIL 'processRunDate' ('2026-06-12 00:00:00') -> normalized Timestamp."""
    t = _pd.to_datetime(str(x), errors="coerce")
    if t is None or _pd.isna(t):
        return None
    return _pd.Timestamp(t).normalize()


def _pace():
    """Polite jittered pause between FBIL calls (we make only 4)."""
    _time.sleep(0.6 + _random.random() * 0.7)


def _fetch_product(prod: str, start: str, end: str, log) -> list:
    """GET one FBIL product's inline-JSON list. [] on any failure (graceful)."""
    if _requests is None:
        log("    (requests unavailable — FBIL skipped)")
        return []
    url = f"{FBIL_BASE}/{prod}/fetchfiltered"
    try:
        r = _requests.get(
            url,
            params={"fromDate": start, "toDate": end, "authenticated": "false"},
            timeout=60,
            headers=_HEADERS,
            verify=False,
        )
    except Exception as e:
        log(f"    fbil {prod} request failed: {e}")
        return []
    if r.status_code != 200:
        log(f"    fbil {prod} HTTP {r.status_code} — skipped")
        return []
    # SPA / error-shell guard: a real endpoint returns a JSON array starting '['.
    body = (r.text or "").lstrip()
    if not body.startswith("[") and not body.startswith("{"):
        log(f"    fbil {prod} non-JSON body (len {len(r.text)}) — skipped")
        return []
    try:
        data = r.json()
    except Exception as e:
        log(f"    fbil {prod} JSON parse failed: {e}")
        return []
    if not isinstance(data, list):
        log(f"    fbil {prod} unexpected JSON type {type(data).__name__} — skipped")
        return []
    return data


def _series_from_records(records, key_field, value_field, key_filter=None):
    """Split a flat record list into {sub_key: Series}. ``key_field`` is the column
    that names the sub-series (tenor / pair); ``value_field`` is the numeric column.
    Optional ``key_filter`` (set/iterable) restricts which sub-keys we keep."""
    buckets: dict[str, dict] = {}
    keep = set(key_filter) if key_filter is not None else None
    for rec in records:
        k = rec.get(key_field)
        if k is None:
            continue
        k = str(k).strip()
        if keep is not None and k not in keep:
            continue
        ts = _ts(rec.get("processRunDate"))
        v = _to_float(rec.get(value_field))
        if ts is None or v is None:
            continue
        buckets.setdefault(k, {})[ts] = v
    out = {}
    for k, rows in buckets.items():
        s = _pd.Series(rows, dtype="float64").sort_index()
        s = s[~s.index.duplicated(keep="last")]
        if len(s):
            out[k] = s
    return out


# --------------------------------------------------------------------------- #
# Per-product builders
# --------------------------------------------------------------------------- #
def _build_termmibor(records, series, meta):
    """Term MIBOR: field 'tenor' (e.g. '14 DAYS','1 MONTH','3 MONTHS'), value 'rate' (%)."""
    sub = _series_from_records(records, "tenor", "rate")
    # Normalise tenor spelling to a clean label.
    label_map = {
        "14 DAYS": "14D", "1 MONTH": "1M", "3 MONTHS": "3M",
        "1 MONTHS": "1M", "3 MONTH": "3M",
    }
    for raw, s in sub.items():
        tag = label_map.get(raw.upper(), raw.title())
        name = f"FBIL Term MIBOR — {tag} (%)"
        series[name] = s
        meta[name] = {"unit": "%", "freq": "daily",
                      "source": "FBIL (wasdm/termmibor)", "group": "Money market"}


def _build_cd(records, series, meta):
    """CD benchmark curve: field 'tenorName' (7 tenors), value 'rate' (%)."""
    sub = _series_from_records(records, "tenorName", "rate")
    for raw, s in sub.items():
        name = f"FBIL CD rate — {raw} (%)"
        series[name] = s
        meta[name] = {"unit": "%", "freq": "daily",
                      "source": "FBIL (wasdm/cd)", "group": "Money market"}


def _build_refrates(records, series, meta):
    """FX reference rates: field 'subProdName' (6 pairs), value 'rate' (INR per unit fgn)."""
    sub = _series_from_records(records, "subProdName", "rate")
    for raw, s in sub.items():
        friendly, unit = _FX_LABELS.get(raw, (f"FBIL FX reference — {raw}", "INR"))
        series[friendly] = s
        meta[friendly] = {"unit": unit, "freq": "daily",
                          "source": "FBIL (wasdm/refrates)", "group": "FX"}


def _build_fwdpremia(records, series, meta):
    """USD/INR forward premia: field 'tenorName', TWO numeric columns:
        rate            — annualised forward premium, %
        usdInrPremiaRs  — outright forward points, rupees (paise) per USD
    We skip the calendar-anchored junk tenors ('FBD January'/'Spot'/'O/N' kept)."""
    # Keep the standard month tenors + O/N; drop the 'FBD <month>' calendar entries
    # which are sparse/irregular and not a clean monthly ladder.
    raw_tenors = {str(r.get("tenorName")).strip() for r in records}
    keep = {t for t in raw_tenors if not t.upper().startswith("FBD")}

    pct = _series_from_records(records, "tenorName", "rate", key_filter=keep)
    rup = _series_from_records(records, "tenorName", "usdInrPremiaRs", key_filter=keep)

    def _order_key(t):
        u = t.upper()
        if u == "SPOT":
            return -1.0
        if u in ("O/N", "ON"):
            return 0.0
        # '1M'..'12M'
        digits = "".join(c for c in t if c.isdigit())
        return float(digits) if digits else 99.0

    for raw in sorted(pct, key=_order_key):
        name = f"FBIL USD/INR fwd premium — {raw} (% p.a.)"
        series[name] = pct[raw]
        meta[name] = {"unit": "% p.a.", "freq": "daily",
                      "source": "FBIL (wasdm/fwdpremia)", "group": "FX forwards"}
    for raw in sorted(rup, key=_order_key):
        name = f"FBIL USD/INR fwd points — {raw} (INR/USD)"
        series[name] = rup[raw]
        meta[name] = {"unit": "INR per USD", "freq": "daily",
                      "source": "FBIL (wasdm/fwdpremia)", "group": "FX forwards"}


_BUILDERS = [
    ("termmibor", _build_termmibor),
    ("cd", _build_cd),
    ("refrates", _build_refrates),
    ("fwdpremia", _build_fwdpremia),
]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def fetch_series(start: str = "2000-01-01", log=print):
    """Fetch the full FBIL money-market + FX surface.

    Parameters
    ----------
    start : str
        Earliest date to request (ISO 'YYYY-MM-DD'). Clamped up to the FBIL
        history floor (2014-01-01) since the wasdm benchmark feed begins there.
    log : callable
        Progress sink; defaults to ``print``.

    Returns
    -------
    (series_by_name, meta_by_name) : tuple[dict[str, pandas.Series], dict[str, dict]]
    """
    try:
        req_start = max(str(start), _FBIL_HISTORY_FLOOR)
    except Exception:
        req_start = _FBIL_HISTORY_FLOOR
    end = _dt.date.today().isoformat()

    series: dict[str, _pd.Series] = {}
    meta: dict[str, dict] = {}

    if _requests is None:
        log("[fbil_surface] requests unavailable — returning empty surface")
        return series, meta

    log(f"[fbil_surface] FBIL surface {req_start} -> {end}")
    for i, (prod, builder) in enumerate(_BUILDERS):
        records = _fetch_product(prod, req_start, end, log)
        if records:
            before = len(series)
            try:
                builder(records, series, meta)
            except Exception as e:
                log(f"    fbil {prod} build failed: {e}")
            log(f"    {prod}: {len(records)} records -> {len(series) - before} series")
        if i < len(_BUILDERS) - 1:
            _pace()

    log(f"[fbil_surface] done — {len(series)} series")
    return series, meta


# --------------------------------------------------------------------------- #
# CLI: quick live proof / audit
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import sys

    # Prove with only ~45 recent days so we keep call volume low.
    recent = (_dt.date.today() - _dt.timedelta(days=45)).isoformat()
    s, m = fetch_series(start=recent, log=print)
    print(f"\n=== {len(s)} series ===")
    for name in sorted(s):
        ser = s[name]
        last_d = ser.index[-1].date()
        print(f"  {name:48s} n={len(ser):4d}  last={last_d} {ser.iloc[-1]:.4f}  [{m[name]['unit']}]")
    sys.exit(0)
