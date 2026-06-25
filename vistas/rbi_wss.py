"""
RBI Weekly Statistical Supplement (WSS) layer for Vistas (free official source).

The Reserve Bank of India publishes, every Friday, a "Weekly Statistical
Supplement" — a small bundle of HTML tables that carry the freshest high-frequency
prints of India's monetary plumbing: the foreign-exchange reserves, money supply
(M3) and its components, and the Scheduled-Commercial-Bank balance sheet (bank
credit + aggregate deposits). These are *the* market-watched weekly macro numbers
(the "forex reserves rose/fell by $X bn this week" headline comes straight from
here), and there is no paid feed required — RBI serves them publicly.

ENDPOINT (ASP.NET WebForms, returns an HTML page of <table>s; there is NO stable
XLSX — the XLS links 404):

    GET https://www.rbi.org.in/Scripts/BS_viewWssExtract.aspx?SelectedDate=M/DD/YYYY

The SelectedDate is a US-style date with NO zero padding (e.g. 6/19/2026 for
19-Jun-2026), and must be a Friday on which an extract was published; weekend /
holiday dates 404 (or return an empty shell), so we iterate the most recent
Fridays and skip misses.

What we extract (each becomes a friendly-named pandas Series at NATIVE frequency):

  Foreign Exchange Reserves  (table titled "2. Foreign Exchange Reserves") — weekly,
    stamped at the table's "As on <date>" (typically the Friday a week prior):
      Total Reserves, Foreign Currency Assets, Gold, SDRs, Reserve Position in IMF.
    Published in BOTH Rs Crore and US$ Mn; we report the US$ figure converted to
    USD billion (the market convention), plus the Rs-crore Total for completeness.

  Money Supply M3  (table titled "Money Stock" / containing the row "M3") —
    fortnightly, stamped at the latest "Outstanding as on" column:
      M3, Currency with the Public, Demand Deposits, Time Deposits.

  Reserve Money (M0)  (table titled "Reserve Money" / containing the row "Reserve
    Money", when present that week) — weekly:
      Reserve Money (M0), Currency in Circulation.

  Scheduled Commercial Banks  (table "4. Scheduled Commercial Banks ...") —
    fortnightly, stamped at "Outstanding as on <date>":
      Bank Credit, Aggregate Deposits.

Frequencies differ by block (forex = weekly; SCB / M3 = fortnightly; reserve money
= weekly), so each Series carries its own native dates — the caller accumulates
Friday-by-Friday and de-duplicates by (series, date).

Conventions (so numbers are reproducible):
  * Forex levels are reported in USD bn = (US$ Mn value) / 1000. The Rs-crore total
    is reported as-is (unit "Rs crore").
  * M3 / Reserve Money / SCB levels are reported in Rs crore as published.
  * The observation DATE for each block is parsed from that table's header ("As on
    Jun. 12, 2026" / "Outstanding as on May 31, 2026"), NOT the URL's SelectedDate —
    so a given Friday's page is correctly attributed to the (slightly earlier)
    reference date the data actually pertains to.

Graceful degrade: NEVER raises on a network error / 404 / parse miss. A holiday
Friday is skipped; an unparseable table just contributes nothing; an offline run
returns whatever was gathered. Polite jittered pacing between Fridays.

API (uniform across Vistas data modules):
    fetch_series(start="2000-01-01", log=print)
        -> (series_by_name: dict[str, pd.Series], meta_by_name: dict[str, dict])

Provenance: written for Vistas 2026-06-21. Self-contained; imports nothing from the
research engine.
"""
from __future__ import annotations

import re
import time
import random
import datetime as dt

import numpy as np
import pandas as pd

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

try:                                    # RBI serves a chain certifi sometimes rejects;
    import urllib3                       # we verify=False defensively and silence the warning.
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:                       # pragma: no cover
    pass

# --------------------------------------------------------------------------- config
WSS_URL = "https://www.rbi.org.in/Scripts/BS_viewWssExtract.aspx"
WSS_REFERER = "https://www.rbi.org.in/Scripts/BS_viewWss.aspx"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": _UA, "Referer": WSS_REFERER,
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}

# How many recent Fridays to try by default (each is one HTTP call). The lead's
# full backfill walks many more, politely; for a live proof a handful is plenty.
DEFAULT_FRIDAYS = 8
TIMEOUT = 45

_MONTHS = {m[:3].lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"]) if i}


# ============================================================ row -> series spec
# Each spec: (table-match keywords (any), row-label regex, friendly name, group,
#             value-column kind, unit).
#   value-column kind:
#     "usd"   -> pick the US$ Mn column from the forex table, divide by 1000 (USD bn)
#     "inr"   -> pick the Rs-crore "as on / outstanding" level column (last dated one)
# The row-label regex is matched against the WHITESPACE-NORMALISED first column.

FOREX_ROWS = [
    (r"^1\b.*total\s+reserves",                "Forex reserves — Total (USD bn)",                 "Total"),
    (r"^1\.1\b.*foreign\s+currency\s+assets",  "Forex reserves — Foreign Currency Assets (USD bn)", "FCA"),
    (r"^1\.2\b.*gold",                         "Forex reserves — Gold (USD bn)",                  "Gold"),
    (r"^1\.3\b.*sdr",                          "Forex reserves — SDRs (USD bn)",                  "SDR"),
    (r"^1\.4\b.*reserve\s+position\s+in",      "Forex reserves — Reserve Position in IMF (USD bn)", "RTP"),
]

M3_ROWS = [
    (r"^m3\b",                                 "Money supply M3 (Rs crore)",                      "M3"),
    (r"^1\.1\b.*currency\s+with\s+the\s+public", "M3 — Currency with the Public (Rs crore)",      "M3comp"),
    (r"^1\.2\b.*demand\s+deposits",            "M3 — Demand Deposits with Banks (Rs crore)",      "M3comp"),
    (r"^1\.3\b.*time\s+deposits",              "M3 — Time Deposits with Banks (Rs crore)",        "M3comp"),
]

M0_ROWS = [
    (r"^reserve\s+money\b",                    "Reserve money M0 (Rs crore)",                     "M0"),
    (r"currency\s+in\s+circulation",           "M0 — Currency in Circulation (Rs crore)",         "M0comp"),
]

SCB_ROWS = [
    (r"^7\b.*bank\s+credit$|^bank\s+credit$",  "SCB — Bank Credit (Rs crore)",                    "SCB"),
    (r"^2\.1\b.*aggregate\s+deposits",         "SCB — Aggregate Deposits (Rs crore)",             "SCB"),
]

# Friendly name -> meta (unit/freq/source/group). Built once below.
_GROUP = "External / Money & Banking"


def _meta_for(name: str) -> dict:
    if "USD bn" in name:
        unit, freq = "USD bn", "weekly"
    elif "Forex" in name:
        unit, freq = "Rs crore", "weekly"
    elif name.startswith("Reserve money") or name.startswith("M0"):
        unit, freq = "Rs crore", "weekly"
    elif name.startswith("Money supply M3") or name.startswith("M3"):
        unit, freq = "Rs crore", "fortnightly"
    else:                                                  # SCB
        unit, freq = "Rs crore", "fortnightly"
    return {"unit": unit, "freq": freq, "source": "RBI Weekly Statistical Supplement",
            "group": _GROUP}


# ============================================================ helpers
def _norm(s) -> str:
    """Whitespace-normalise a cell label for robust regex matching."""
    return re.sub(r"\s+", " ", str(s)).strip()


def _to_float(x):
    """Parse a WSS numeric cell -> float (handles thousands commas, '-', blanks, '#')."""
    if x is None:
        return np.nan
    s = str(x).strip().replace(",", "").replace("–", "-").replace("—", "-")
    s = s.replace("#", "").replace("@", "").replace("*", "").strip()
    if s in ("", "-", "--", "nan", "NaN", "None", "P", "PR"):
        return np.nan
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else np.nan


def _parse_asof_date(text: str, fallback: dt.date | None = None):
    """Pull an 'as on / outstanding as on' date out of a header string.

    Robust to the WSS habit of splitting a date across header ROWS, so by the time
    the blob is concatenated the parts may be NON-contiguous, e.g.:
        'Outstanding as on 2026 2026 May 31'   (year before month-day)
        'As on Jun. 12, 2026'                  (contiguous)
        '31-May-2026'                          (dd-mon-yyyy)
    Strategy: try contiguous 'Mon DD, YYYY' and 'DD-Mon-YYYY' first; then fall back
    to finding a 'Mon DD' anywhere and pairing it with the LAST 4-digit year in the
    blob. Returns a pandas Timestamp, or `fallback`, or None."""
    if text is None:
        return pd.Timestamp(fallback) if fallback else None
    t = _norm(text)
    # 1) contiguous 'Jun. 12, 2026' / 'May 31, 2026'
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(\d{4})", t)
    if m:
        mo = _MONTHS.get(m.group(1)[:3].lower())
        if mo:
            try:
                return pd.Timestamp(int(m.group(3)), mo, int(m.group(2)))
            except Exception:
                pass
    # 2) 'DD-Mon-YYYY' / 'DD-Mon-YY'
    m = re.search(r"(\d{1,2})[-/]([A-Za-z]{3,9})[-/](\d{2,4})", t)
    if m:
        mo = _MONTHS.get(m.group(2)[:3].lower())
        if mo:
            yr = int(m.group(3));  yr += 2000 if yr < 100 else 0
            try:
                return pd.Timestamp(yr, mo, int(m.group(1)))
            except Exception:
                pass
    # 3) split header: a 'Mon DD' token anywhere + the last standalone year in blob
    md = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})\b", t)
    yrs = re.findall(r"\b(19|20)(\d{2})\b", t)
    if md and yrs:
        mo = _MONTHS.get(md.group(1)[:3].lower())
        if mo:
            yr = int(yrs[-1][0] + yrs[-1][1])         # last year mentioned (the data year)
            try:
                return pd.Timestamp(yr, mo, int(md.group(2)))
            except Exception:
                pass
    return pd.Timestamp(fallback) if fallback else None


def _table_title(t: pd.DataFrame) -> str:
    """The (possibly mangled, repeated) column header carries the table title."""
    try:
        return _norm(" ".join(str(c) for c in t.columns))
    except Exception:
        return ""


def _header_blob(t: pd.DataFrame, ncols: int) -> list[str]:
    """Concatenate the first few rows of each value column into a per-column header
    string, so we can read each column's 'as on <date>' label. Returns a list of
    length ncols (index 0 = the label column, ignored)."""
    blobs = [""] * ncols
    head_rows = t.head(6)
    for r in range(len(head_rows)):
        for c in range(ncols):
            try:
                v = head_rows.iat[r, c]
            except Exception:
                continue
            if pd.isna(v):
                continue
            sv = _norm(v)
            if sv and sv.lower() not in ("item", "date", "1", "2", "3", "4", "5", "6", "7", "8"):
                blobs[c] = (blobs[c] + " " + sv).strip()
    return blobs


def _last_dated_inr_col(t: pd.DataFrame, header_blobs: list[str]) -> tuple[int | None, object]:
    """For Rs-crore 'outstanding as on' tables, the level we want is the LATEST
    dated 'outstanding'/'as on' value column (NOT a 'variation over' column).
    Returns (col_index, parsed_date). Falls back to col 1 if none carry a date."""
    best_c, best_d = None, None
    for c in range(1, t.shape[1]):
        h = (header_blobs[c] if c < len(header_blobs) else "").lower()
        if "variation" in h or "growth" in h:        # skip change columns
            continue
        if "outstanding" not in h and "as on" not in h:
            continue
        d = _parse_asof_date(header_blobs[c])
        if d is not None and (best_d is None or d > best_d):
            best_d, best_c = d, c
    if best_c is not None:
        return best_c, best_d
    # fallback: first value column is typically the headline outstanding level
    fb = 1 if t.shape[1] > 1 else None
    return fb, (_parse_asof_date(header_blobs[fb]) if fb is not None else None)


def _usd_col(t: pd.DataFrame, header_blobs: list[str]) -> int | None:
    """In the forex table, find the FIRST 'US$ Mn' value column (the as-on level,
    not a variation). Columns alternate Rs Cr / US$ Mn; the first US$ Mn is col 2."""
    for c in range(1, t.shape[1]):
        h = (header_blobs[c] if c < len(header_blobs) else "").lower()
        if "us$" in h or "us $" in h or "usd" in h:
            return c
    return None


def _extract_rows(t: pd.DataFrame, row_specs, value_col: int, asof, scale: float = 1.0):
    """Walk the table's first column, match each row spec, read value_col, build
    {friendly_name: (asof, value)}."""
    out = {}
    labels = t.iloc[:, 0].map(_norm)
    for rx, name, _grp in row_specs:
        pat = re.compile(rx, re.I)
        for i, lab in labels.items():
            if pat.search(lab):
                val = _to_float(t.iat[i, value_col]) if value_col < t.shape[1] else np.nan
                if not pd.isna(val):
                    out[name] = (asof, val * scale)
                break
    return out


# ============================================================ single-Friday parse
def parse_extract(html: str, url_date: dt.date | None = None, log=print) -> dict:
    """Parse one WSS page's HTML -> {friendly_name: (Timestamp, value)} for every
    series we recognise. Robust to table reordering: we identify tables by title /
    content, not by index. Returns {} on a non-data shell."""
    if not html or "<table" not in html.lower():
        return {}
    try:
        from io import StringIO
        tables = pd.read_html(StringIO(html))
    except Exception as e:
        log(f"  parse: read_html failed ({e})")
        return {}

    found = {}
    for t in tables:
        if t.shape[1] < 2 or t.shape[0] < 3:
            continue
        title = _table_title(t).lower()
        labels_join = " ".join(t.iloc[:, 0].map(lambda x: _norm(x).lower()))
        ncols = t.shape[1]
        blobs = _header_blob(t, ncols)

        # ---- Foreign Exchange Reserves (US$ Mn -> USD bn) ----
        if "foreign exchange reserves" in title or "total reserves" in labels_join:
            asof = _parse_asof_date(" ".join(blobs), url_date)
            uc = _usd_col(t, blobs)
            if uc is not None:
                found.update(_extract_rows(t, FOREX_ROWS, uc, asof, scale=1.0 / 1000.0))
            continue

        # ---- Money Supply M3 ----
        if re.search(r"\bm3\b", labels_join) and "currency with the public" in labels_join:
            vc, asof = _last_dated_inr_col(t, blobs)
            if asof is None:
                asof = _parse_asof_date(" ".join(blobs), url_date)
            if vc is not None:
                found.update(_extract_rows(t, M3_ROWS, vc, asof))
            continue

        # ---- Reserve Money (M0) — present only some weeks ----
        if "reserve money" in labels_join:
            vc, asof = _last_dated_inr_col(t, blobs)
            if asof is None:
                asof = _parse_asof_date(" ".join(blobs), url_date)
            if vc is not None:
                found.update(_extract_rows(t, M0_ROWS, vc, asof))
            continue

        # ---- Scheduled Commercial Banks ----
        if "scheduled commercial banks" in title or ("bank credit" in labels_join
                                                     and "aggregate deposits" in labels_join):
            # outstanding column is the FIRST value column on this table
            vc = 1
            asof = _parse_asof_date(blobs[1] if len(blobs) > 1 else " ".join(blobs), url_date)
            found.update(_extract_rows(t, SCB_ROWS, vc, asof))
            continue

    return found


# ============================================================ fetch one Friday
def _fetch_one(session, d: dt.date, log=print) -> str | None:
    """GET one Friday's extract. Returns HTML, or None on 404/holiday/error."""
    sd = f"{d.month}/{d.day}/{d.year}"           # US-style, NO zero pad
    try:
        r = session.get(WSS_URL, params={"SelectedDate": sd},
                        headers=HEADERS, verify=False, timeout=TIMEOUT)
    except Exception as e:
        log(f"  {d}: request error ({e})")
        return None
    if r.status_code != 200:
        log(f"  {d}: HTTP {r.status_code}")
        return None
    body = r.text or ""
    # An empty / SPA-shell / no-data page has no real tables.
    if "<table" not in body.lower() or "total reserves" not in body.lower():
        log(f"  {d}: no WSS tables (holiday / not published)")
        return None
    return body


def _recent_fridays(n: int, anchor: dt.date | None = None):
    """The n most recent Fridays on/before anchor (default today), newest first."""
    d = anchor or dt.date.today()
    while d.weekday() != 4:                       # 4 = Friday
        d -= dt.timedelta(days=1)
    out = []
    for _ in range(n):
        out.append(d)
        d -= dt.timedelta(days=7)
    return out


# ============================================================ public API
def fetch_series(start="2000-01-01", log=print, n_fridays: int = DEFAULT_FRIDAYS,
                 anchor: dt.date | None = None):
    """Pull the most recent `n_fridays` RBI WSS extracts, accumulate every series
    Friday-by-Friday, and return:
        (series_by_name: {name: pd.Series indexed by Timestamp, float values},
         meta_by_name:   {name: {"unit","freq","source","group"}}).

    Never raises. Skips holiday / unpublished Fridays. Polite jittered pacing.
    `start` clips the returned series (the lead backfills deeper separately)."""
    if requests is None:
        log("rbi_wss: 'requests' not available")
        return {}, {}

    start_ts = pd.Timestamp(start)
    fridays = _recent_fridays(n_fridays, anchor=anchor)
    log(f"[rbi_wss] fetching {len(fridays)} Fridays "
        f"{fridays[-1]}..{fridays[0]} from RBI WSS …")

    # accumulate {name: {Timestamp: value}}
    acc: dict[str, dict] = {}
    n_ok = 0
    with requests.Session() as ses:
        ses.headers.update(HEADERS)
        for i, d in enumerate(fridays):
            html = _fetch_one(ses, d, log=log)
            if html:
                got = parse_extract(html, url_date=d, log=log)
                if got:
                    n_ok += 1
                    for name, (ts, val) in got.items():
                        if ts is None:
                            continue
                        acc.setdefault(name, {})[pd.Timestamp(ts)] = float(val)
                    log(f"  {d}: parsed {len(got)} series")
            if i < len(fridays) - 1:
                time.sleep(random.uniform(1.2, 2.6))   # polite

    series_by_name, meta_by_name = {}, {}
    for name, dd in acc.items():
        s = pd.Series(dd, dtype="float64").sort_index()
        s = s[~s.index.duplicated(keep="last")]
        if start_ts is not None:
            s = s[s.index >= start_ts]
        if len(s):
            s.name = name
            series_by_name[name] = s
            meta_by_name[name] = _meta_for(name)

    log(f"[rbi_wss] done — {len(series_by_name)} series from {n_ok}/{len(fridays)} Fridays")
    return series_by_name, meta_by_name


if __name__ == "__main__":
    import json
    s, m = fetch_series(start="2024-01-01", log=lambda x: print(x, flush=True), n_fridays=8)
    print(f"\n{len(s)} series:")
    for name in sorted(s):
        ser = s[name]
        print(f"  {name:55s} n={len(ser):2d}  "
              f"latest {ser.index[-1].date()} = {ser.iloc[-1]:,.2f}  [{m[name]['freq']}]")
