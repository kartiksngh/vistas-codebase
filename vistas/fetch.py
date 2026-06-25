"""
Standalone NSE (niftyindices.com) total-return-index fetcher for Vistas.

Faithful port of the project's `data_update/update_nse_tr.py` so Vistas stays
self-contained (no import of the research project). Same mechanics that make it
work despite "NSE can't be scraped":

  * GET the historical-data page first to collect session COOKIES, then POST.
  * Browser User-Agent + AJAX headers.
  * Endpoint: POST .../Backpage.aspx/getTotalReturnIndexString
  * Payload: {"cinfo": "{'name':'IDX','startDate':'DD-Mon-YYYY',...}"}  -- the
    cinfo value is a SINGLE-quoted JS object literal (double quotes hang ~60s).
  * Server caps ~1 year per request -> chunk into <=350-day windows.
  * Display->API name map from IndexMapping.json (cached locally in ./data).

Everything is wrapped to DEGRADE GRACEFULLY: on any network failure the callers
return {"ok": False, "error": ...} instead of raising, so a hosted Vistas with
no outbound access simply keeps serving its bundled snapshot.
"""
from __future__ import annotations

import os
import json
import time
import random
import threading
import datetime as dt

import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

# ★ Prefer a REAL Chrome TLS/HTTP2 fingerprint (curl_cffi) over plain `requests`. NSE's anti-bot WAF
# weighs the TLS/JA3 fingerprint heavily and silently DROPS the easily-detected `requests` fingerprint
# (this is the #1 reason the pull gets soft-blocked from an otherwise-good residential IP). curl_cffi
# impersonates Chrome so the traffic looks like a person on a browser — far less likely to be flagged,
# and the durable cure for "NSE blocked me again". Falls back to `requests` if curl_cffi isn't installed.
try:
    from curl_cffi import requests as _cffi
except Exception:  # pragma: no cover
    _cffi = None
_IMPERSONATE = os.environ.get("VISTAS_IMPERSONATE", "chrome124")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))
MAP_CACHE = os.path.join(DATA_DIR, "IndexMapping.json")

PAGE = "https://www.niftyindices.com/reports/historical-data"
HOSTS = ["https://www.niftyindices.com", "https://niftyindices.com"]
MAP_URL = "https://iislliveblob.niftyindices.com/assets/json/IndexMapping.json"

# ----------------------------------------------------------------------------- endpoint registry
# Every niftyindices report uses the IDENTICAL Backpage.aspx contract (verified by
# inspecting the site's own historicalData.js / IISLComponet.js, 2026-06-19):
#   POST {"cinfo": "{'name':API,'startDate':DD-Mon-YYYY,'endDate':...,'indexName':DISPLAY}"}
#   -> {"d": "<json-row-array-as-string>"}.
# A "group" = one endpoint that yields one or more named MEASURES. Column names below
# are matched case-insensitively against the response keys (first hit wins).
#
# Per-group `timeout`/`prefer_bare`: cross-verified (2026-06-19) against the canonical
# nsepython reference, the PR/VAL request CONTRACT here is byte-for-byte correct — TR
# proves the machinery works. NSE simply rate-limits the price/PEPB endpoints harder
# than TR: a valid-but-throttled response can take ~60s (a 30s cap turns that into a
# ReadTimeout) and the WAF resets flagged connections (RemoteDisconnected). So PR/VAL
# get a patient 75s timeout and try the BARE host first (matching the reference client),
# while TR is left exactly as it was (30s, www-first) — its proven path is not touched.
ENDPOINTS = {
    # group : {method, date-column candidates, {measure: response-column candidates}, ...}
    "TR":  {"method": "getTotalReturnIndexString",
            "date": ["date"],
            "measures": {"TR": ["totalreturnsindex"], "NTR": ["ntr_value"]},
            "fallback": "TR",   # defensive: first numeric col if name drifts
            "timeout": 10,      # tight: a healthy TR response is ~0.3s, so 10s fails a stalled index
                                #        fast instead of burning 30s × retries on the long tail.
            "attempts": 2},     # incremental tail — a dead/renamed snapshot column shouldn't cost
                                #        3×2×10s; 2 attempts bounds it (next daily run retries anyway).
    "PR":  {"method": "getHistoricaldatatabletoString",
            "date": ["historicaldate", "date"],
            "measures": {"PR": ["close"], "PR_OPEN": ["open"],
                         "PR_HIGH": ["high"], "PR_LOW": ["low"]},
            "timeout": 75, "prefer_bare": True},
    "VAL": {"method": "getpepbHistoricaldataDBtoString",
            "date": ["date"],
            "measures": {"PE": ["pe"], "PB": ["pb"], "DY": ["divyield", "divyeild"]},
            "timeout": 75, "prefer_bare": True},
}


def _endpoint_urls(method: str, prefer_bare: bool = False) -> list[str]:
    """Backpage URLs for a method. prefer_bare puts https://niftyindices.com first
    (the bare host the reference client uses for the price/PEPB endpoints)."""
    hosts = list(reversed(HOSTS)) if prefer_bare else list(HOSTS)
    return [f"{h}/Backpage.aspx/{method}" for h in hosts]


# ----------------------------------------------------------------------------- STEALTH: identity
# A small pool of realistic, current desktop identities. _session() picks one per
# session so our footprint isn't a single fixed signature repeatedly hitting the host.
UA_POOL = [
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
     'Chrome/124.0.0.0 Safari/537.36',
     '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
     'Chrome/125.0.0.0 Safari/537.36',
     '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"'),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
     'Chrome/123.0.0.0 Safari/537.36',
     '"Chromium";v="123", "Not:A-Brand";v="8", "Google Chrome";v="123"'),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
     'Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0',
     '"Microsoft Edge";v="124", "Chromium";v="124", "Not-A.Brand";v="99"'),
]
HEADERS = {  # base AJAX header set (kept as a name for back-compat references)
    "User-Agent": UA_POOL[0][0],
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",   # no 'br': brotli decoder not guaranteed installed
    "Content-Type": "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": PAGE,
    "Origin": "https://www.niftyindices.com",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
    # CLOSE, not keep-alive: NSE's WAF resets long-lived sockets, which poisons requests' keep-alive
    # connection pool so every later call hangs on the dead socket (the real cause of the "TR pull
    # hangs" — a 25-request burst with Connection:close is flawless; verified 2026-06-24). A fresh
    # socket per request is immune to that and costs only ~0.1s TLS overhead per index.
    "Connection": "close",
}
COOKIE_CACHE = os.path.join(DATA_DIR, ".nse_cookies.json")


def _pick_headers() -> dict:
    """A full, internally-consistent browser header set with a randomly chosen UA."""
    ua, sec = random.choice(UA_POOL)
    h = dict(HEADERS)
    h["User-Agent"] = ua
    h["sec-ch-ua"] = sec
    return h


def _host_of(url: str) -> str:
    """scheme://host from a full URL."""
    return "/".join(url.split("/")[:3])


def _headers_for_url(url: str) -> dict:
    """Per-request Referer/Origin matching the host being POSTed to (bare vs www), so
    the WAF sees a self-consistent same-origin request for the price/PEPB endpoints
    (the reference client uses the bare host's own Referer/Origin)."""
    host = _host_of(url)
    return {"Referer": host + "/reports/historical-data", "Origin": host}


_IDX_MAP: dict[str, str] = {}

# Major broad indices that define the real NSE EQUITY trading calendar. Every write
# is gated to days on which at least one of these has a value, so stray dates from
# an index on a different calendar can never inject NaN-filled rows (the bug that
# shredded a prior snapshot). Names are stored upper-case.
MAJORS = ["NIFTY 50", "NIFTY 500", "NIFTY 100", "NIFTY 200", "NIFTY BANK"]

# ---- STEALTH pacing: never burst NSE; wide jittered delays + periodic breathers + a
# hard daily request cap, so the traffic looks like one person idly browsing historical
# data, never a bulk scraper. Default profile = "slow" (under-the-radar); override with
# env VISTAS_FETCH_PROFILE=normal for the faster legacy pacing. -----------------------
_PROFILES = {
    "slow":   {"chunk": (1.5, 4.0), "index": (4.0, 9.0),
               "breather_every": 80,  "breather": (15.0, 40.0), "daily_cap": 6000},
    "normal": {"chunk": (0.15, 0.35), "index": (0.7, 1.4),
               "breather_every": 120, "breather": (5.0, 9.0),   "daily_cap": 25000},
}


def _profile() -> dict:
    # Default = "normal". The "slow" profile sleeps 4-9s PER INDEX → a full/incremental pull
    # spends ~50 min purely sleeping even though NSE answers in ~0.1s (verified 2026-06-24 from
    # this runtime: page/map/POST all 200 in <0.3s, no IP block). That dead-slow pacing is what
    # made the daily pipeline look "hung". "normal" (~0.7-1.4s/index, breathers) is still polite
    # (<1 req/s) and finishes a refresh in ~6-7 min. Set VISTAS_FETCH_PROFILE=slow for max stealth.
    return _PROFILES.get(os.environ.get("VISTAS_FETCH_PROFILE", "normal").lower(),
                         _PROFILES["normal"])


_REQ = {"n": 0, "day": None, "day_count": 0, "blocks": 0}


class FetchBlocked(RuntimeError):
    """Raised to ABORT a run cleanly when NSE signals we should back off (a streak of
    429/403, or the daily request cap) — quieter than hammering through it."""


def _count_request():
    """Per-request bookkeeping: daily cap (resets each calendar day) + abort-on-cap."""
    today = dt.date.today().isoformat()
    if _REQ["day"] != today:
        _REQ["day"], _REQ["day_count"] = today, 0
    _REQ["day_count"] += 1
    if _REQ["day_count"] > _profile()["daily_cap"]:
        raise FetchBlocked(f"daily request cap reached ({_profile()['daily_cap']}); backing off")


def _polite(kind: str = "chunk"):
    p = _profile()
    lo, hi = p.get(kind, p["chunk"])
    time.sleep(random.uniform(lo, hi))
    _REQ["n"] += 1
    if _REQ["n"] % p["breather_every"] == 0:    # a longer breather periodically
        time.sleep(random.uniform(*p["breather"]))


def _gate_calendar(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only rows on the equity trading calendar (a major broad index present),
    with a normalized date-only, de-duplicated index."""
    if df.empty:
        return df
    df = df.copy()
    df.index = pd.DatetimeIndex(df.index).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    majors = [c for c in MAJORS if c in df.columns]
    if majors:
        df = df.loc[df[majors].notna().any(axis=1)]
    return df


# ----------------------------------------------------------------------------- index mapping
def _parse_map(raw_bytes) -> dict:
    j = json.loads(raw_bytes.decode("utf-8-sig"))
    m = {}
    for e in j:
        tn = e.get("Trading_Index_Name")
        ln = e.get("Index_long_name")
        if tn:
            m[tn.strip().upper()] = tn
            if ln:
                m[ln.strip().upper()] = tn
    return m, j


def load_index_map(refresh: bool = False) -> dict:
    """Load the display->API name map: from the local cache, refreshing from the
    upstream blob when asked (and re-caching). Returns {} on total failure."""
    global _IDX_MAP
    if _IDX_MAP and not refresh:
        return _IDX_MAP
    # local cache first
    if not refresh and os.path.exists(MAP_CACHE):
        try:
            with open(MAP_CACHE, "rb") as f:
                _IDX_MAP, _ = _parse_map(f.read())
            return _IDX_MAP
        except Exception:
            pass
    # remote
    if requests is not None:
        try:
            content = requests.get(MAP_URL, headers={"User-Agent": HEADERS["User-Agent"]},
                                   timeout=30).content
            _IDX_MAP, _ = _parse_map(content)
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(MAP_CACHE, "wb") as f:
                f.write(content)
            return _IDX_MAP
        except Exception:
            pass
    return _IDX_MAP


def catalog_names() -> list[str]:
    """All fetchable index display names known from IndexMapping (cached/remote).
    Returns the unique long/display names; empty if the map is unavailable."""
    if not os.path.exists(MAP_CACHE) and requests is not None:
        load_index_map(refresh=True)
    elif not _IDX_MAP:
        load_index_map()
    if not os.path.exists(MAP_CACHE):
        return []
    try:
        with open(MAP_CACHE, "rb") as f:
            _, j = _parse_map(f.read())
        names = []
        for e in j:
            nm = e.get("Index_long_name") or e.get("Trading_Index_Name")
            if nm:
                names.append(nm.strip())
        return sorted(set(names))
    except Exception:
        return []


def api_name(col: str) -> str:
    return load_index_map().get(str(col).strip().upper(), col)


# ----------------------------------------------------------------------------- low-level fetch
def _save_cookies(s):
    try:
        with open(COOKIE_CACHE, "w") as f:
            json.dump(requests.utils.dict_from_cookiejar(s.cookies), f)
    except Exception:
        pass


def _load_cookies(s):
    try:
        if os.path.exists(COOKIE_CACHE):
            with open(COOKIE_CACHE) as f:
                s.cookies.update(requests.utils.cookiejar_from_dict(json.load(f)))
    except Exception:
        pass


def _session():
    if _cffi is not None:
        # real Chrome TLS+HTTP2 fingerprint; impersonate owns the UA / sec-ch-ua / TLS, so we add only
        # the AJAX headers the Backpage endpoint needs (Content-Type / X-Requested-With / Accept), never
        # a hand-rolled UA (a mismatched UA-vs-TLS pair is itself a bot tell).
        s = _cffi.Session(impersonate=_IMPERSONATE)
        ajax = {k: v for k, v in HEADERS.items() if k not in ("User-Agent", "sec-ch-ua", "Connection")}
        try:
            s.headers.update(ajax)
        except Exception:
            pass
    elif requests is not None:
        s = requests.Session()
        s.headers.update(_pick_headers())   # rotate identity per session (stealth)
    else:
        raise RuntimeError("neither curl_cffi nor requests is installed")
    _load_cookies(s)                    # reuse a prior cookie jar so we re-handshake less
    try:
        # SHORT timeout: the HTML-page handshake is flaky (intermittent ReadTimeout) AND unnecessary —
        # the Backpage data endpoints serve fine with zero cookies (verified 2026-06-24). So we try it
        # briefly for politeness but never let it stall the pull (it used to eat the full 30s).
        s.get(PAGE, timeout=8)
    except Exception:
        pass                            # cookies aren't actually required for the data endpoints
    _save_cookies(s)
    load_index_map(refresh=True)        # refresh + cache the name map while we have a session
    return s


def _rehandshake(s):
    """After a connection-level drop (WAF reset / RemoteDisconnected) the keep-alive
    socket is poisoned. Rotate identity + re-seed cookies on the SAME session so the
    next attempt looks like a fresh browser landing on the page. Best-effort."""
    try:
        if _cffi is None and requests is not None:
            s.headers.update(_pick_headers())     # only rotate UA on the `requests` path; curl_cffi
        s.get(PAGE, timeout=20)                   # keeps its Chrome-consistent identity fixed
        _save_cookies(s)
    except Exception:
        pass
    return s


def _fmt(d) -> str:
    return d.strftime("%d-%b-%Y")


def _parse(j) -> pd.DataFrame:
    raw = j["d"] if isinstance(j, dict) and "d" in j else j
    if isinstance(raw, str):
        try:
            rows = json.loads(raw)
        except Exception:
            rows = json.loads(raw.replace("'", '"'))
    else:
        rows = raw
    return pd.DataFrame(rows)


def _match_col(cols, candidates) -> str | None:
    """First response column whose lower-cased name matches a candidate (exact, then
    substring). Case/space-insensitive — NSE casing is inconsistent across reports."""
    low = {str(c).lower().strip(): c for c in cols}
    for cand in candidates:                 # exact match wins
        if cand in low:
            return low[cand]
    for cand in candidates:                 # then substring
        for lc, orig in low.items():
            if cand in lc:
                return orig
    return None


def fetch_frame(s, index, start, end, group: str = "TR") -> pd.DataFrame:
    """Date-indexed frame of the MEASURES for one report `group` (TR/PR/VAL) for one
    index over [start, end]; chunks NSE's ~1-year cap into <=350-day windows. Same
    polite, retry/backoff, abort-on-block machinery for every report — only the
    endpoint + the response column mapping differ by group. Columns returned are the
    measure keys that appear in the response (e.g. PE/PB/DY for VAL, PR(+OHLC) for PR).
    """
    spec = ENDPOINTS[group]
    timeout = spec.get("timeout", 30)                  # PR/VAL get a patient 75s (throttled-
                                                       # but-valid responses run ~60s); TR=30s.
    urls = _endpoint_urls(spec["method"], prefer_bare=spec.get("prefer_bare", False))
    nm = api_name(index)
    out, cur = [], start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=350), end)
        cinfo = "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}" % (
            nm, _fmt(cur), _fmt(chunk_end), nm)
        body = json.dumps({"cinfo": cinfo})
        df, last_err, dropped = None, None, False
        for attempt in range(spec.get("attempts", 3)):  # retry with backoff (TR=2: dead snapshot
            for url in urls:
                try:
                    _count_request()                   # daily cap (raises FetchBlocked)
                    r = s.post(url, data=body, timeout=timeout, headers=_headers_for_url(url))
                    if r.status_code == 200 and r.content:
                        df = _parse(r.json())
                        _REQ["blocks"] = 0
                        break
                    last_err = f"HTTP {r.status_code}"
                    if r.status_code in (429, 403, 503):   # rate-limited / blocked
                        _REQ["blocks"] += 1
                        if _REQ["blocks"] >= 6:            # a streak -> stop, don't hammer
                            raise FetchBlocked(f"{r.status_code} streak from NSE; backing off")
                        break
                except FetchBlocked:
                    raise
                except Exception as e:
                    last_err = repr(e)
                    # connection-level drop (WAF reset / RemoteDisconnected) poisons the
                    # keep-alive socket -> re-handshake a fresh identity before retrying.
                    if requests is not None and isinstance(
                            e, (requests.exceptions.ConnectionError,
                                requests.exceptions.ChunkedEncodingError)):
                        dropped = True
            if df is not None:
                break
            backoff = 1.5 * (attempt + 1) ** 2 + random.uniform(0, 1.0)
            if dropped:
                _rehandshake(s)                        # recover the session after a drop
                backoff += 2.0 * (attempt + 1)         # give the WAF a moment longer
                dropped = False
            time.sleep(backoff)
        if df is None:
            raise RuntimeError(f"{index}[{group}]: fetch failed -> {last_err}")
        if len(df):
            out.append(df)
        cur = chunk_end + dt.timedelta(days=1)
        _polite("chunk")                               # gentle, jittered, de-bursting
    if not out:
        return pd.DataFrame()
    raw = pd.concat(out, ignore_index=True)
    dcol = _match_col(raw.columns, spec["date"]) or \
        next((c for c in raw.columns if "date" in str(c).lower()), raw.columns[0])
    res = {"Date": pd.to_datetime(raw[dcol], errors="coerce", dayfirst=True)}
    for measure, cands in spec["measures"].items():
        col = _match_col(raw.columns, cands)
        if col is not None:
            res[measure] = pd.to_numeric(raw[col], errors="coerce")
    fb = spec.get("fallback")
    if fb and fb not in res:                           # name drift -> first numeric col
        for c in raw.columns:
            if c == dcol:
                continue
            v = pd.to_numeric(raw[c], errors="coerce")
            if v.notna().any():
                res[fb] = v
                break
    frame = pd.DataFrame(res).dropna(subset=["Date"]).set_index("Date").sort_index()
    frame.index = frame.index.normalize()              # date-only, no time component
    return frame[~frame.index.duplicated(keep="last")]


def fetch_index(s, index, start, end) -> pd.DataFrame:
    """DataFrame[Date -> value] of the TOTAL-RETURN level for one index, column named
    by the index display name. Thin back-compat wrapper over fetch_frame('TR') — the
    exact contract every existing caller (update / add_index / fetch_all / build_fresh)
    already depends on."""
    f = fetch_frame(s, index, start, end, "TR")
    if f.empty or "TR" not in f.columns:
        return pd.DataFrame()
    return f[["TR"]].rename(columns={"TR": index})


# ----------------------------------------------------------------------------- high-level ops
def _latest_csv(measure: str = "TR"):
    import glob
    import re
    cands = glob.glob(os.path.join(DATA_DIR, f"Indices Data {measure} till *.csv"))
    if not cands:
        return None

    def _key(p):
        m = re.search(r"till (.+)\.csv$", os.path.basename(p))
        d = pd.to_datetime(m.group(1).strip(), errors="coerce") if m else None
        return d if pd.notna(d) else pd.Timestamp(os.path.getmtime(p), unit="s")

    return max(cands, key=_key)


def _write_dated(merged: pd.DataFrame, measure: str = "TR") -> str:
    merged = _gate_calendar(merged)              # normalize, de-dup, equity-calendar only
    new_last = merged.index.max().date()
    merged.index.name = "Date"
    out = os.path.join(DATA_DIR,
                       f"Indices Data {measure} till {new_last.strftime('%b %#d, %Y')}.csv")
    merged.reset_index().to_csv(out, index=False)
    return out


_DEAD_STALE_DAYS = 30        # a column whose own data ended >30d before the snapshot's last day = dead/renamed

def _reachable(s, name, start, end, timeout=8, tries=2) -> bool:
    """Lightweight 'is NSE answering us?' canary — one short-timeout POST (no internal retry storm),
    a couple of tries. Returns True on the first 200-with-body. Used to fail the whole pull FAST and
    clearly when NSE's WAF is silently dropping this machine, instead of grinding every index to timeout."""
    spec = ENDPOINTS["TR"]
    urls = _endpoint_urls(spec["method"], prefer_bare=spec.get("prefer_bare", False))
    nm = api_name(name)
    cinfo = "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}" % (nm, _fmt(start), _fmt(end), nm)
    body = json.dumps({"cinfo": cinfo})
    for _ in range(tries):
        for url in urls:
            try:
                r = s.post(url, data=body, timeout=timeout, headers=_headers_for_url(url))
                if r.status_code == 200 and r.content:
                    return True
            except Exception:
                pass
        time.sleep(1.0)
    return False

def update(dry: bool = False) -> dict:
    """Append last_date->today for every LIVE column in the newest snapshot; write a new dated CSV.
    Robust + self-diagnosing (never raises):
      • CANARY early-abort — probe one liquid index first; if NSE's data host is WAF-blocking/timing-out
        this machine, return FAST with `unreachable:True` and a plain-English reason, instead of grinding
        ~130 indices × per-request timeouts (~87 min) and writing nothing. This is the #1 cause of "I ran
        the update 10× and prices never changed": the pull silently fails and the publish ships the old CSV.
      • DEAD-COLUMN PRUNE — indices whose own data stopped >30d before the snapshot's last day (renamed /
        discontinued) are SKIPPED (their history is kept); they otherwise cost a timeout each, every run.
      • LOUD status — distinguishes 'NSE unreachable', 'no new trading days', and 'updated through <date>'."""
    try:
        csv = _latest_csv()
        if csv is None:
            return {"ok": False, "error": f"No snapshot CSV in {DATA_DIR}."}
        data = pd.read_csv(csv)
        data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
        data = data.dropna(subset=["Date"]).set_index("Date").sort_index()
        last = data.index.max().date()
        today = dt.date.today()
        start = last + dt.timedelta(days=1)
        if start > today:
            return {"ok": True, "updated": False, "message": "Already up to date.",
                    "asof": last.strftime("%Y-%m-%d"), "rows_added": 0}
        cols = list(data.columns)
        # --- dead-column prune: only fetch indices that are still live (data within 30d of the snapshot end)
        snap_last = data.index.max()
        last_valid = {c: data[c].last_valid_index() for c in cols}
        dead = [c for c in cols if last_valid[c] is None or (snap_last - last_valid[c]).days > _DEAD_STALE_DAYS]
        live = [c for c in cols if c not in dead]
        if not live:
            return {"ok": False, "error": "No live columns to update (every column looks stale)."}
        s = _session()
        # --- CANARY: is NSE's data endpoint actually answering us right now? (fast, no retry storm) ---
        canary = "NIFTY 50" if "NIFTY 50" in live else live[0]
        if not _reachable(s, canary, start, today):
            return {"ok": False, "unreachable": True,
                    "message": (f"NSE's data host (www.niftyindices.com) is not responding to this machine "
                                f"(canary '{canary}' timed out twice). This is almost always NSE's anti-bot "
                                f"WAF temporarily IP-blocking us — it self-clears in a few hours, and repeated "
                                f"retries make it worse. Prices left UNCHANGED at {last:%d-%b-%Y}. To update "
                                f"now: run from a different network (mobile hotspot / VPN); otherwise the "
                                f"scheduled job will catch up once the block lifts."),
                    "asof": last.strftime("%Y-%m-%d"), "rows_added": 0,
                    "n_live": len(live), "n_dead_skipped": len(dead)}
        # --- live pull ---
        new = {}
        failed = []
        for idx in live:
            try:
                d = fetch_index(s, idx, start, today)
                new[idx] = d[idx] if idx in d.columns else pd.Series(dtype=float)
            except Exception as e:
                new[idx] = pd.Series(dtype=float)
                failed.append(f"{idx}: {type(e).__name__}")
            time.sleep(0.3)
        add = pd.DataFrame(new)
        if add.dropna(how="all").empty:
            # endpoint answered the canary but no rows came back → genuinely no new trading days, OR the
            # block kicked in mid-pull. Surface both possibilities honestly.
            return {"ok": True, "updated": False,
                    "message": (f"No new rows after {last:%d-%b-%Y} (likely no trading days yet, or NSE "
                                f"throttled mid-pull: {len(failed)}/{len(live)} indices failed)."),
                    "failed": failed[:10], "asof": last.strftime("%Y-%m-%d"), "rows_added": 0,
                    "n_live": len(live), "n_dead_skipped": len(dead)}
        merged = pd.concat([data, add]).groupby(level=0).last().sort_index()
        if dry:
            return {"ok": True, "updated": False, "dry": True,
                    "rows_added": len(merged) - len(data), "n_live": len(live), "n_dead_skipped": len(dead),
                    "n_failed": len(failed), "new_asof": merged.index.max().date().strftime("%Y-%m-%d")}
        out = _write_dated(merged)
        return {"ok": True, "updated": True, "file": os.path.basename(out),
                "rows_added": int(len(merged) - len(data)),
                "new_asof": merged.index.max().date().strftime("%Y-%m-%d"),
                "n_live": len(live), "n_dead_skipped": len(dead), "n_failed": len(failed),
                "failed": failed[:10]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------------- batch "fetch all"
# A single global background job so one click can pull every not-yet-local index
# without the user clicking each. Front-end starts it then polls /api/fetch_status.
_JOB = {"running": False, "finished": False, "done": 0, "total": 0,
        "current": "", "added": [], "failed": [], "error": None, "phase": ""}
_JOB_LOCK = threading.Lock()


def fetch_status() -> dict:
    with _JOB_LOCK:
        return dict(_JOB)


def _missing_indices() -> list[str]:
    """Catalog (display) names not present as a populated column in the snapshot."""
    csv = _latest_csv()
    have = set()
    if csv is not None:
        d = pd.read_csv(csv, nrows=1)
        have = {c for c in d.columns if c != "Date"}
    names = catalog_names()
    return [n for n in names if n not in have]


def _fetch_all_worker(names):
    csv = _latest_csv()
    if csv is None:
        with _JOB_LOCK:
            _JOB.update(running=False, finished=True, error=f"No snapshot CSV in {DATA_DIR}.")
        return
    data = pd.read_csv(csv)
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"]).set_index("Date").sort_index()
    try:
        s = _session()
    except Exception as e:
        with _JOB_LOCK:
            _JOB.update(running=False, finished=True, error=f"NSE session failed: {e}")
        return
    today = dt.date.today()
    last_written = None
    for i, nm in enumerate(names):
        with _JOB_LOCK:
            if not _JOB["running"]:        # cancelled
                break
            _JOB["current"] = nm
        try:
            d = fetch_index(s, nm, dt.date(2005, 4, 1), today)
            if not d.empty and nm in d.columns and d[nm].notna().sum() > 0:
                data = data.join(d[[nm]], how="outer").sort_index()
                with _JOB_LOCK:
                    _JOB["added"].append(nm)
            else:
                with _JOB_LOCK:
                    _JOB["failed"].append(nm)
        except Exception:
            with _JOB_LOCK:
                _JOB["failed"].append(nm)
        with _JOB_LOCK:
            _JOB["done"] = i + 1
        # checkpoint every 20 successful merges so a long run isn't lost
        if (i + 1) % 20 == 0:
            last_written = _write_dated(data)
        time.sleep(0.25)
    out = _write_dated(data)
    with _JOB_LOCK:
        _JOB.update(running=False, finished=True, current="", file=os.path.basename(out),
                    new_asof=data.index.max().date().strftime("%Y-%m-%d"))


def fetch_all_start() -> dict:
    """Kick off a background fetch of every not-yet-local catalog index. Returns
    immediately; poll fetch_status()."""
    with _JOB_LOCK:
        if _JOB["running"]:
            return {"ok": True, "already_running": True, **_JOB}
    if requests is None:
        return {"ok": False, "error": "the 'requests' package is not installed"}
    try:
        names = _missing_indices()
    except Exception as e:
        return {"ok": False, "error": f"catalog unavailable: {e}"}
    if not names:
        return {"ok": True, "nothing_to_fetch": True, "message": "All catalog indices already local."}
    with _JOB_LOCK:
        _JOB.update(running=True, finished=False, done=0, total=len(names),
                    current="", added=[], failed=[], error=None, phase="fetching full history")
    threading.Thread(target=_fetch_all_worker, args=(names,), daemon=True).start()
    return {"ok": True, "started": True, "total": len(names)}


def fetch_all_cancel() -> dict:
    with _JOB_LOCK:
        _JOB["running"] = False
    return {"ok": True, "cancelled": True}


# ----------------------------------------------------------------------------- fresh full rebuild
def _equity_universe() -> list[str]:
    """Upper-cased equity index names (broad / factor / sector-thematic) from the NSE
    catalog, de-duplicated, with the major broad indices guaranteed present."""
    from . import catalog as _cat
    names = catalog_names()
    out, seen = [], set()
    for n in names:
        col = n.strip().upper()
        if col in seen:
            continue
        seen.add(col)
        if _cat._group(col) in ("Fixed income / debt", "REIT / InvIT"):
            continue
        out.append(col)
    for k in MAJORS:
        if k not in seen:
            out.append(k); seen.add(k)
    return out


def build_fresh(start="2000-01-01", end=None, universe=None, progress=None):
    """Refetch the full EQUITY universe fresh from `start` (default 2000) into a
    clean, calendar-gated snapshot — replacing stale/partial data. Polite to NSE:
    jittered pacing, periodic breathers, retry/backoff, and a session refresh every
    20 indices so cookies don't expire mid-run. Checkpoints as it goes."""
    log = progress or (lambda m: print(m, flush=True))
    end = end or dt.date.today()
    if isinstance(end, str):
        end = dt.date.fromisoformat(end)
    start_d = dt.date.fromisoformat(start) if isinstance(start, str) else start
    uni = universe or _equity_universe()
    log(f"[build_fresh] {len(uni)} equity indices, {start_d}..{end} (<=350d chunks, polite)")
    s = _session()
    series, ok, fail = {}, [], []
    for i, col in enumerate(uni):
        try:
            d = fetch_index(s, col, start_d, end)
            n = int(d[col].notna().sum()) if col in d.columns else 0
            if n > 0:
                series[col] = d[col]; ok.append(col)
                log(f"  [{i+1}/{len(uni)}] {col}: {n} obs ({d.index.min().date()}..{d.index.max().date()})")
            else:
                fail.append(col); log(f"  [{i+1}/{len(uni)}] {col}: EMPTY")
        except Exception as e:
            fail.append(col); log(f"  [{i+1}/{len(uni)}] {col}: FAIL {e}")
        _polite("index")
        if (i + 1) % 20 == 0:
            if series:
                _write_dated(pd.DataFrame(series)); log(f"  …checkpoint ({len(series)} cols)")
            try:
                s = _session()                      # refresh cookies mid-run
            except Exception:
                pass
    out = _write_dated(pd.DataFrame(series)) if series else None
    log(f"[build_fresh] DONE: {len(ok)} fetched, {len(fail)} empty/failed -> {os.path.basename(out) if out else 'NOTHING'}")
    if fail:
        log("  empty/failed: " + ", ".join(fail[:40]))
    return {"ok": ok, "fail": fail, "file": out}


def add_index(name: str) -> dict:
    """Fetch full history (2005-04-01..today) for a NEW index and merge it as a
    column into the newest snapshot. Returns a status dict (never raises)."""
    try:
        csv = _latest_csv()
        if csv is None:
            return {"ok": False, "error": f"No snapshot CSV in {DATA_DIR}."}
        data = pd.read_csv(csv)
        data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
        data = data.dropna(subset=["Date"]).set_index("Date").sort_index()
        if name in data.columns and data[name].notna().any():
            return {"ok": True, "added": False, "message": f"'{name}' already in the snapshot."}
        s = _session()
        d = fetch_index(s, name, dt.date(2005, 4, 1), dt.date.today())
        if d.empty or name not in d.columns or d[name].notna().sum() == 0:
            return {"ok": False, "error": f"No data returned for '{name}' "
                    f"(check the exact NSE index name)."}
        merged = data.join(d[[name]], how="outer").sort_index()
        out = _write_dated(merged)
        return {"ok": True, "added": True, "index": name,
                "n_obs": int(d[name].notna().sum()), "file": os.path.basename(out)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------------- multi-measure pulls
# Which measures get persisted to a snapshot CSV. NTR (net total return) and the PR
# OHLC extras are fetched but not snapshotted yet (kept for a future view) so we don't
# multiply files before they're used.
PERSIST_MEASURES = {"TR", "PR", "PE", "PB", "DY"}


def _tr_snapshot_columns() -> list[str]:
    """Index column names in the current TR snapshot — the canonical identifier set,
    so the new per-measure CSVs use the SAME column names and align with TR."""
    csv = _latest_csv("TR")
    if csv is None:
        return []
    return [c for c in pd.read_csv(csv, nrows=1).columns if c != "Date"]


def _universe_for(group: str, base: list[str]) -> list[str]:
    """The index list to pull for a report group. PR = `base` PLUS every other catalog
    index (so new index types — multi-asset / fixed-income — come in). TR / VAL = `base`
    (equity; there is no P/E on a bond)."""
    if group != "PR":
        return list(base)
    uni, seen = list(base), {b.strip().upper() for b in base}
    for n in catalog_names():
        u = n.strip().upper()
        if u not in seen:
            seen.add(u)
            uni.append(n)
    return uni


def build_measures(groups=("PR", "VAL"), start="2000-01-01", end=None,
                   universe=None, progress=None) -> dict:
    """Bulk-pull one or more report GROUPS (PR / VAL / TR) into per-measure wide
    snapshot CSVs ('Indices Data <MEASURE> till <date>.csv'). Stealthy by default:
    rotating identity, slow jittered pacing, RANDOMIZED index order, a session refresh
    + checkpoint every 20 indices, and a CLEAN ABORT on a block streak / daily cap
    (FetchBlocked) rather than hammering. Never raises — returns a status dict and keeps
    whatever it has checkpointed. Network is optional.

    Column names come from the current TR snapshot (so measures align with TR); for PR
    the rest of the catalog is appended. Intended to be run by hand, off-hours."""
    log = progress or (lambda m: print(m, flush=True))
    if requests is None:
        return {"ok": False, "error": "the 'requests' package is not installed"}
    end = end or dt.date.today()
    if isinstance(end, str):
        end = dt.date.fromisoformat(end)
    start_d = dt.date.fromisoformat(start) if isinstance(start, str) else start
    explicit = universe is not None
    base = universe or _tr_snapshot_columns() or _equity_universe()
    # per-index inception from the TR snapshot (PR/VAL share each index's history), so a
    # pull STARTS at the index's first real date — never a pre-inception range, which the
    # historical endpoint stalls on (read-timeouts) — and this also cuts requests sharply.
    incept = {}
    try:
        _tr = pd.read_csv(_latest_csv("TR"))
        _tr["Date"] = pd.to_datetime(_tr["Date"], errors="coerce")
        _tr = _tr.dropna(subset=["Date"]).set_index("Date").sort_index()
        for _c in _tr.columns:
            _s = _tr[_c].dropna()
            if len(_s):
                incept[_c.strip().upper()] = _s.index[0].date()
    except Exception:
        pass
    try:
        s = _session()
    except Exception as e:
        return {"ok": False, "error": f"NSE session failed: {e}"}

    out = {}
    for group in groups:
        if group not in ENDPOINTS:
            out[group] = {"ok": False, "error": f"unknown group '{group}'"}
            continue
        # explicit universe -> use it verbatim for every group; auto -> PR sweeps the catalog
        order = list(base) if explicit else _universe_for(group, base)
        random.shuffle(order)                                   # non-fixed access pattern
        keep = [m for m in ENDPOINTS[group]["measures"] if m in PERSIST_MEASURES]
        cols = {m: {} for m in keep}
        ok, fail, aborted, consec = [], [], None, 0
        log(f"[build_measures:{group}] {len(order)} indices, {start_d}..{end}, measures={keep}")
        for i, name in enumerate(order):
            try:
                idx_start = start_d
                inc = incept.get(name.strip().upper())
                if inc is not None and inc > idx_start:
                    idx_start = inc - dt.timedelta(days=7)   # start at the index's inception
                f = fetch_frame(s, name, idx_start, end, group)
                consec = 0                              # a real response (even if empty) = healthy
                got, obs = False, {}
                for m in keep:
                    if m in f.columns and f[m].notna().sum() > 0:
                        cols[m][name] = f[m]
                        obs[m] = int(f[m].notna().sum())
                        got = True
                (ok if got else fail).append(name)
                log(f"  [{i + 1}/{len(order)}] {name}: " + (str(obs) if got else "EMPTY"))
            except FetchBlocked as e:
                aborted = str(e)
                log(f"  ABORT (NSE asked us to back off): {e}")
                break
            except Exception as e:
                fail.append(name); consec += 1
                log(f"  [{i + 1}/{len(order)}] {name}: FAIL {e}")
                if consec >= 5:                         # streak of timeouts = silent re-throttle
                    aborted = f"{consec} consecutive fetch failures (likely re-throttled); stopping"
                    log("  ABORT: " + aborted); break
            _polite("index")
            if (i + 1) % 20 == 0:                               # checkpoint + refresh cookies
                for m in keep:
                    if cols[m]:
                        _write_dated(pd.DataFrame(cols[m]), m)
                try:
                    s = _session()
                except Exception:
                    pass
                log(f"  …checkpoint @ {i + 1}/{len(order)} (ok={len(ok)} fail={len(fail)})")
        files = {}
        for m in keep:
            if cols[m]:
                files[m] = os.path.basename(_write_dated(pd.DataFrame(cols[m]), m))
        out[group] = {"ok": aborted is None, "aborted": aborted, "n_ok": len(ok),
                      "n_fail": len(fail), "files": files}
        log(f"[build_measures:{group}] DONE ok={len(ok)} fail={len(fail)} -> {files}")
        if aborted:
            break                                              # don't start the next group blocked
    return {"ok": True, "groups": out}


def update_measures(groups=("TR", "PR", "VAL"), dry: bool = False) -> dict:
    """Append last_date->today for each EXISTING per-measure snapshot (only groups that
    already have a CSV are touched). Stealthy + clean-abort like build_measures; never
    raises. This is the routine, low-footprint refresh — it only ever pulls the small
    new tail, never re-fetches history."""
    if requests is None:
        return {"ok": False, "error": "the 'requests' package is not installed"}
    today = dt.date.today()
    try:
        s = _session()
    except Exception as e:
        return {"ok": False, "error": f"NSE session failed: {e}"}
    out = {}
    for group in groups:
        keep = [m for m in ENDPOINTS.get(group, {}).get("measures", {}) if m in PERSIST_MEASURES]
        # find the per-measure CSVs that exist for this group
        present = [(m, _latest_csv(m)) for m in keep]
        present = [(m, p) for m, p in present if p is not None]
        if not present:
            continue
        for m, csv in present:
            try:
                data = pd.read_csv(csv)
                data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
                data = data.dropna(subset=["Date"]).set_index("Date").sort_index()
                last = data.index.max().date()
                start = last + dt.timedelta(days=1)
                if start > today:
                    out[m] = {"ok": True, "updated": False, "message": "up to date"}
                    continue
                add = {}
                for name in [c for c in data.columns]:
                    try:
                        f = fetch_frame(s, name, start, today, group)
                        if m in f.columns:
                            add[name] = f[m]
                    except FetchBlocked as e:
                        out[m] = {"ok": False, "aborted": str(e)}
                        return {"ok": True, "groups": out, "aborted": str(e)}
                    except Exception:
                        pass
                    _polite("index")
                addf = pd.DataFrame(add)
                if addf.dropna(how="all").empty:
                    out[m] = {"ok": True, "updated": False, "message": "no new rows"}
                    continue
                merged = pd.concat([data, addf]).groupby(level=0).last().sort_index()
                if dry:
                    out[m] = {"ok": True, "dry": True, "rows_added": len(merged) - len(data)}
                else:
                    fn = _write_dated(merged, m)
                    out[m] = {"ok": True, "updated": True, "file": os.path.basename(fn),
                              "rows_added": int(len(merged) - len(data))}
            except Exception as e:
                out[m] = {"ok": False, "error": str(e)}
    return {"ok": True, "groups": out}
