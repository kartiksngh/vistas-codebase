"""
Screener.in fundamentals connector for Vistas (the buy-side "Bloomberg" fundamentals
plane). Mirrors the fetch.py / stocks.py discipline: pull on refresh -> cache a local
JSON snapshot per company -> the deck/app reads the cache. Network + auth are OPTIONAL
and GRACEFUL — every entry point returns a status dict (or None) and never raises, so
a machine with no login / no internet just serves whatever is cached.

WHAT IT PULLS (robots-clean primary path — see robots note below):
  * company search          GET /api/company/search/?q=<name>      -> [{id,name,url}]
  * valuation + price history GET /api/company/<id>/chart/?q=...&days=10000&consolidated=true
                              -> {datasets:[{metric,label,values:[[date,val],...]}]}
                              (Price, PE, EPS, Median PE, DMA50/200, Volume — 2005->today)
  * financial statements      company page HTML <section>s -> pandas.read_html
                              (P&L, balance sheet, cash flow, ratios, quarters,
                               shareholding, peers)

AUTH: Screener serves a lot logged-out (often with limited history); full history +
some statements need a login. Credentials come from ENV ONLY and are never written to
disk or committed:  SCREENER_EMAIL / SCREENER_PASSWORD.  Only the resulting cookie jar
is cached (data/.screener_cookies.json) so we re-handshake rarely.

robots.txt: /user/* is DISALLOWED (that's the Excel export
/user/company/export/<warehouse_id>/). We DEFAULT to the robots-clean API+HTML path and
gate the export behind SCREENER_ALLOW_EXPORT=1 (opt-in).

NOTE: exact endpoint strings are centralised in the constants below so they can be
corrected from the screener-contract-research verification without touching logic.
read_html needs lxml (or bs4+html5lib) installed.
"""
from __future__ import annotations

import os
import re
import json
import time
import random
import datetime as dt

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

try:
    import pandas as pd
except Exception:                       # pragma: no cover
    pd = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))
CACHE_DIR = os.path.join(DATA_DIR, "screener")
COOKIE_CACHE = os.path.join(DATA_DIR, ".screener_cookies.json")

BASE = "https://www.screener.in"
LOGIN_URL = f"{BASE}/login/"
SEARCH_URL = f"{BASE}/api/company/search/"                       # ?q=<name>
CHART_URL = f"{BASE}/api/company/{{cid}}/chart/"                 # ?q=...&days=...&consolidated=
COMPANY_URL = f"{BASE}/company/{{sym}}/"                         # +'consolidated/' for consolidated
EXPORT_URL = f"{BASE}/user/company/export/{{wid}}/"             # opt-in, robots-disallowed

# Chart metric strings Screener accepts in the q= param (joined by '-').
# VERIFIED live 2026-06-19 (TCS id=3365, public — no login): returns datasets for each.
CHART_PRICE = "Price-DMA50-DMA200-Volume"
CHART_VALUATION = "Price to Earning-EPS-Median PE"

# Financial-statement <section id=...> on the company page -> friendly key. The page has
# MORE tables than statements (the 'profit-loss' section also holds 4 small growth boxes),
# so we map by Screener's own section id and take each section's FIRST table — robust to
# table order/count (VERIFIED live: ids quarters/profit-loss/balance-sheet/cash-flow/
# ratios/shareholding; 'peers' is JS-loaded, not in static HTML).
STATEMENT_SECTIONS = {
    "quarters": "quarters", "profit-loss": "profit_loss", "balance-sheet": "balance_sheet",
    "cash-flow": "cash_flow", "ratios": "ratios", "shareholding": "shareholding",
}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE + "/",
}

# Cache freshness: price/valuation move daily; statements update at most quarterly.
TTL_PRICE_DAYS = 1
TTL_STATEMENTS_DAYS = 7

# Polite pacing — one site, a personal account. Never burst.
_PACE_CHUNK = (3.0, 7.0)
_PACE_COMPANY = (8.0, 20.0)
# Per-run request cap (each company ≈ 4 requests). Default covers a full NIFTY 500 in one
# run (502 × 4 ≈ 2008). The counter resets per process, so RE-RUNNING simply resumes
# (cached companies are skipped). For the ~2000-company '--universe all' pull, raise it via
# the SCREENER_DAILY_CAP env (e.g. 12000) or just re-run a few times. The real account-safety
# lever is the polite per-company pacing below, which is unchanged regardless of this cap.
DAILY_CAP = int(os.environ.get("SCREENER_DAILY_CAP", "2500"))
_REQ = {"day": None, "count": 0, "blocks": 0}


class ScreenerBlocked(RuntimeError):
    """Raised to abort cleanly on a block streak / daily cap (don't hammer a personal acct)."""


def have_credentials() -> bool:
    return bool(os.environ.get("SCREENER_EMAIL") and os.environ.get("SCREENER_PASSWORD"))


# ----------------------------------------------------------------------------- pacing
def _count_request():
    today = dt.date.today().isoformat()
    if _REQ["day"] != today:
        _REQ["day"], _REQ["count"] = today, 0
    _REQ["count"] += 1
    if _REQ["count"] > DAILY_CAP:
        raise ScreenerBlocked(
            f"per-run request cap reached ({DAILY_CAP} requests ≈ {DAILY_CAP // 4} companies); "
            f"re-run to resume, or raise SCREENER_DAILY_CAP env for a bigger single run")


def _polite(kind="chunk"):
    lo, hi = _PACE_COMPANY if kind == "company" else _PACE_CHUNK
    time.sleep(random.uniform(lo, hi))


# ----------------------------------------------------------------------------- session / auth
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
    if requests is None:
        raise RuntimeError("the 'requests' package is not installed")
    s = requests.Session()
    s.headers.update(HEADERS)
    _load_cookies(s)
    return s


def _logged_in(s) -> bool:
    """Cheap check: the cached sessionid still authenticates."""
    try:
        r = s.get(BASE + "/dash/", timeout=20, allow_redirects=False)
        return r.status_code == 200
    except Exception:
        return False


def login(s=None) -> dict:
    """Django login: GET /login/ for csrf, POST credentials, persist cookies. Creds from
    env only. Returns a status dict; never raises."""
    if requests is None:
        return {"ok": False, "error": "'requests' not installed"}
    if not have_credentials():
        return {"ok": False, "error": "set SCREENER_EMAIL and SCREENER_PASSWORD in the environment"}
    s = s or _session()
    try:
        g = s.get(LOGIN_URL, timeout=30)
        token = s.cookies.get("csrftoken")
        m = re.search(r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)', g.text)
        midtoken = m.group(1) if m else token
        payload = {
            "username": os.environ["SCREENER_EMAIL"],
            "password": os.environ["SCREENER_PASSWORD"],
            "csrfmiddlewaretoken": midtoken,
        }
        r = s.post(LOGIN_URL, data=payload, timeout=30,
                   headers={"Referer": LOGIN_URL, "Content-Type": "application/x-www-form-urlencoded"})
        ok = ("sessionid" in s.cookies.get_dict()) and r.status_code in (200, 302)
        if ok:
            _save_cookies(s)
            return {"ok": True, "message": "logged in"}
        return {"ok": False, "error": f"login failed (HTTP {r.status_code}); check creds / 2FA"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _ensure_session():
    """A session that's logged in if creds exist (re-login only when needed). Works
    logged-OUT too — many endpoints are public, just with shorter history."""
    s = _session()
    if have_credentials() and not _logged_in(s):
        login(s)
    return s


# ----------------------------------------------------------------------------- API calls
def _get_json(s, url, params=None):
    _count_request()
    r = s.get(url, params=params, timeout=30)
    if r.status_code in (429, 403):
        _REQ["blocks"] += 1
        if _REQ["blocks"] >= 5:
            raise ScreenerBlocked(f"{r.status_code} streak from Screener; backing off")
    r.raise_for_status()
    _REQ["blocks"] = 0
    return r.json()


def search(s, query: str) -> list:
    """Company search -> [{id,name,url}]. Empty on failure."""
    try:
        j = _get_json(s, SEARCH_URL, {"q": query})
        return j if isinstance(j, list) else j.get("results", [])
    except ScreenerBlocked:
        raise
    except Exception:
        return []


def resolve_company(s, symbol: str) -> dict | None:
    """Best-effort symbol -> {id, name, url, warehouse_id?}. Picks the hit whose url
    ends with /<symbol>/ when possible."""
    sym = symbol.strip().upper()
    hits = search(s, sym)
    if not hits:
        return None
    exact = [h for h in hits if str(h.get("url", "")).rstrip("/").upper().endswith("/" + sym)]
    pick = (exact or hits)[0]
    return {"id": pick.get("id"), "name": pick.get("name"), "url": pick.get("url")}


def chart(s, company_id, metrics: str, days: int = 10000, consolidated: bool = True) -> dict:
    """Raw chart datasets for a company_id. Returns {} on failure."""
    try:
        params = {"q": metrics, "days": days, "consolidated": "true" if consolidated else "false"}
        return _get_json(s, CHART_URL.format(cid=company_id), params)
    except ScreenerBlocked:
        raise
    except Exception:
        return {}


def _chart_series(payload: dict) -> dict:
    """Flatten {datasets:[{metric/label, values:[[date,val]...]}]} -> {label: [[date,float]...]}.
    Screener returns values as STRINGS (e.g. Median PE) -> coerce to float (None if not numeric)."""
    out = {}
    for ds in (payload or {}).get("datasets", []) or []:
        label = ds.get("metric") or ds.get("label")
        vals = ds.get("values") or []
        if not (label and vals):
            continue
        clean = []
        for pt in vals:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                try:
                    clean.append([pt[0], float(pt[1])])
                except (TypeError, ValueError):
                    clean.append([pt[0], None])
        out[str(label)] = clean
    return out


def statements(symbol: str, s=None, consolidated: bool = True) -> dict:
    """Financial-statement tables from the company page, labeled by Screener's own
    <section id=...> (robust to table order/count) via bs4 + pandas.read_html. Returns
    {key: list-of-row-dicts}. Needs bs4 + lxml; returns {} if unavailable / on failure."""
    if pd is None:
        return {}
    try:
        from io import StringIO
        from bs4 import BeautifulSoup
    except Exception:
        return {}
    s = s or _ensure_session()
    url = COMPANY_URL.format(sym=symbol.strip().upper())
    if consolidated:
        url = url + "consolidated/"
    try:
        _count_request()
        r = s.get(url, timeout=30)
        if r.status_code != 200:
            return {}
        soup = BeautifulSoup(r.text, "lxml")
        out = {}
        for sid, key in STATEMENT_SECTIONS.items():
            sec = soup.find("section", id=sid)
            tbl = sec.find("table") if sec else None
            if tbl is None:
                continue
            try:
                dfs = pd.read_html(StringIO(str(tbl)))
                if dfs:
                    out[key] = dfs[0].to_dict(orient="records")
            except Exception:
                pass
        return out
    except Exception:
        return {}


# ----------------------------------------------------------------------------- cache + orchestrate
def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol.strip().upper()}.json")


def _fresh(path: str, ttl_days: int) -> bool:
    return os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl_days * 86400


def fundamentals(symbol: str, s=None, consolidated: bool = True, force: bool = False) -> dict:
    """Full per-company fundamentals bundle: meta + valuation/price history + statements.
    Caches to data/screener/<SYM>.json. Serves cache when fresh. Never raises."""
    sym = symbol.strip().upper()
    path = _cache_path(sym)
    if not force and _fresh(path, TTL_PRICE_DAYS):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    s = s or _ensure_session()
    try:
        comp = resolve_company(s, sym)
        if not comp or comp.get("id") is None:
            return {"ok": False, "symbol": sym, "error": "company not found on Screener"}
        cid = comp["id"]
        _polite("chunk")
        val = _chart_series(chart(s, cid, CHART_VALUATION, consolidated=consolidated))
        _polite("chunk")
        px = _chart_series(chart(s, cid, CHART_PRICE, consolidated=consolidated))
        _polite("chunk")
        stmts = statements(sym, s, consolidated=consolidated)
        bundle = {"ok": True, "symbol": sym, "name": comp.get("name"), "company_id": cid,
                  "consolidated": consolidated, "valuation": val, "price": px,
                  "statements": stmts, "fetched": dt.date.today().isoformat()}
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(bundle, f)
        return bundle
    except ScreenerBlocked as e:
        return {"ok": False, "symbol": sym, "aborted": str(e)}
    except Exception as e:
        return {"ok": False, "symbol": sym, "error": str(e)}


def update(symbols, consolidated: bool = True, progress=None) -> dict:
    """Refresh the per-company cache for a list of symbols. Polite + abort-fast."""
    log = progress or (lambda m: print(m, flush=True))
    if requests is None:
        return {"ok": False, "error": "'requests' not installed"}
    s = _ensure_session()
    ok, fail, aborted = [], [], None
    for i, sym in enumerate(symbols):
        try:
            r = fundamentals(sym, s, consolidated=consolidated, force=True)
            (ok if r.get("ok") else fail).append(sym)
            log(f"  [{i+1}/{len(symbols)}] {sym}: {'ok' if r.get('ok') else r.get('error') or r.get('aborted')}")
            if r.get("aborted"):
                aborted = r["aborted"]
                break
        except ScreenerBlocked as e:
            aborted = str(e)
            log(f"  ABORT: {e}")
            break
        except Exception as e:
            fail.append(sym)
            log(f"  [{i+1}/{len(symbols)}] {sym}: FAIL {e}")
        _polite("company")
    return {"ok": aborted is None, "n_ok": len(ok), "n_fail": len(fail),
            "aborted": aborted, "cached": ok}


# ----------------------------------------------------------------------------- incremental refresh + universe
NSE_EQUITY_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


def all_nse_symbols() -> list:
    """EVERY NSE-listed equity symbol (~2000) from the static NSE archive — the universe
    for 'all companies' (beyond the NIFTY 500). [] on failure."""
    if requests is None:
        return []
    try:
        import io
        import pandas as _pd
        r = requests.get(NSE_EQUITY_LIST, headers={"User-Agent": _UA}, timeout=40)
        if r.status_code == 200 and "SYMBOL" in r.text:
            df = _pd.read_csv(io.StringIO(r.text))
            col = next((c for c in df.columns if c.strip().upper() == "SYMBOL"), None)
            if col:
                return [str(s).strip() for s in df[col].dropna()]
    except Exception:
        pass
    return []


def refresh(symbols, full: bool = False, consolidated: bool = True, progress=None) -> dict:
    """INCREMENTAL fundamentals refresh — the routine, low-footprint path.

    For each symbol: if it is already cached AND fresh (within the statements TTL) it is
    SKIPPED unless `full=True`; otherwise it is (re)pulled. So a refresh only fetches NEW
    companies + STALE ones, never re-downloading everything. `full=True` re-pulls the whole
    universe (use after results-season corrections). Polite + abort-fast; never raises."""
    log = progress or (lambda m: print(m, flush=True))
    if requests is None:
        return {"ok": False, "error": "'requests' not installed"}
    syms = [str(s).strip().upper() for s in symbols if str(s).strip()]
    s = _ensure_session()
    added, refreshed, skipped, failed, aborted = [], [], 0, 0, None
    for i, sym in enumerate(syms):
        path = _cache_path(sym)
        exists = os.path.exists(path)
        if exists and _fresh(path, TTL_STATEMENTS_DAYS) and not full:
            skipped += 1
            continue
        try:
            r = fundamentals(sym, s, consolidated=consolidated, force=True)
            if r.get("ok"):
                (refreshed if exists else added).append(sym)
            else:
                failed += 1
            log(f"  [{i + 1}/{len(syms)}] {sym}: {'ok' if r.get('ok') else (r.get('error') or r.get('aborted'))}")
            if r.get("aborted"):
                aborted = r["aborted"]
                break
        except ScreenerBlocked as e:
            aborted = str(e); log(f"  ABORT: {e}"); break
        except Exception as e:
            failed += 1; log(f"  [{i + 1}/{len(syms)}] {sym}: FAIL {e}")
        _polite("company")
    out = {"ok": aborted is None, "added": added, "refreshed": refreshed,
           "n_added": len(added), "n_refreshed": len(refreshed), "skipped": skipped,
           "failed": failed, "aborted": aborted, "total_cached": len(available())}
    log(f"refresh: +{len(added)} new, {len(refreshed)} updated, {skipped} fresh-skipped, "
        f"{failed} failed; {out['total_cached']} cached total" + (f" (ABORTED: {aborted})" if aborted else ""))
    return out


# ----------------------------------------------------------------------------- serve (offline)
def available() -> list:
    """Symbols with a cached fundamentals bundle."""
    if not os.path.isdir(CACHE_DIR):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(CACHE_DIR) if f.endswith(".json"))


def load(symbol: str) -> dict | None:
    path = _cache_path(symbol)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def status() -> dict:
    """Quick health/status for the app/UI."""
    return {"have_credentials": have_credentials(),
            "cached_symbols": len(available()),
            "requests_available": requests is not None,
            "read_html_available": pd is not None}


if __name__ == "__main__":
    import sys
    syms = sys.argv[1:] or ["TCS"]
    print(json.dumps(status(), indent=2))
    print(json.dumps(update(syms, progress=lambda m: print(m, flush=True)), indent=2))
