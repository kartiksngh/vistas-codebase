"""
shares.py — collect REAL issued-share counts from NSE (the exact way; NO fundamentals estimation).

WHY THIS EXISTS (first principles)
----------------------------------
Market cap = price x shares-outstanding. Our daily PRICE is already validated GREEN to the paise vs a
Bloomberg ground-truth (see vistas_gated/audit.py). So the ONLY missing piece for an accurate market cap is
the issued-share count — and we must COLLECT it from a reliable source, never DERIVE it from fundamentals
(PE x PAT / EPS reached only rank rho 0.969 because fundamentals carry a score-of-error that magnifies in
the mcap — KV directive 2026-06-22).

THE SOURCE (official exchange, raw integer — no derivation)
-----------------------------------------------------------
NSE's own quote API returns the exact issued-share count, point-in-time, ISIN-tagged:

    GET https://www.nseindia.com/api/quote-equity?symbol=<SYM>
      metadata.isin                 -> ISIN (joins to our panel at 100% via vistas_gated.idmap)
      securityInfo.issuedSize       -> TOTAL issued shares (raw integer)  <- THE FIELD WE WANT
      securityInfo.faceValue        -> face value (Rs)
      priceInfo.lastPrice           -> NSE last price (sanity only; we use OUR green close)

market cap is then computed downstream as  mcap = OUR green close x issuedSize  — reproducible to a fixed
close, never trusting NSE's intraday-floating live mcap field. issuedSize changes ONLY on a corporate
action, so a slow weekly/periodic refresh keeps the whole ~2000-name panel current.

FETCH DISCIPLINE
----------------
Per-symbol behind NSE's WAF: reuse vistas.bhav_prices.nse_session() (same host cookie handshake), warm the
per-symbol get-quotes page to set the Referer-matching cookies, send JSON/AJAX headers, throttle politely,
and RE-HANDSHAKE every ~10 calls or on the first 401/403. Graceful-degrade: never raises, persists a per-ISIN
cache after every symbol so a blocked/partial run just resumes; with no network it serves what is cached.

Provenance: a standalone collector in the spirit of fetch.py / bhav_prices.py; touches no analytics, no deck.
The recommended-source decision is logged in VISTAS_DATA_INTEGRATION_PLAN.md (workflow nse-mcap-source-hunt).
"""
from __future__ import annotations

import os
import io
import re
import json
import time
import random

import pandas as pd

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

from .bhav_prices import nse_session, UA, DATA_DIR

# Akamai on NSE inspects browser fingerprint headers on the /api/ path — a bare cookie jar 403s.
# These Sec-Fetch / sec-ch-ua client hints + a double page-warm get past the bot manager.
_CH_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

_SHARES_DIR = os.path.join(DATA_DIR, "_shares")
_CACHE_PATH = os.path.join(_SHARES_DIR, "issued_shares.json")

_QUOTE_URL = "https://www.nseindia.com/api/quote-equity?symbol={}"
_GETQUOTES = "https://www.nseindia.com/get-quotes/equity?symbol={}"


# --------------------------------------------------------------------------- cache
def _ensure_dir():
    os.makedirs(_SHARES_DIR, exist_ok=True)


def load() -> dict:
    """The ISIN-keyed issued-shares cache: {ISIN: {symbol, name, shares, face_value, last_price,
    asof, source}}. Empty dict if nothing collected yet."""
    if not os.path.exists(_CACHE_PATH):
        return {}
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(cache: dict):
    _ensure_dir()
    tmp = _CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=0, default=str)
    os.replace(tmp, _CACHE_PATH)


# --------------------------------------------------------------------------- fetch
def quote_session():
    """A requests session warmed for the NSE /api/ path: client-hint headers + a homepage landing so
    Akamai issues the bot-manager cookies. Returns None if requests is unavailable."""
    if requests is None:
        return None
    s = requests.Session()
    s.headers.update(_CH_HEADERS)
    try:
        s.get("https://www.nseindia.com", timeout=20,
              headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                       "Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate",
                       "Sec-Fetch-Dest": "document", "Upgrade-Insecure-Requests": "1"})
    except Exception:
        pass
    return s


def _quote_headers(sym: str) -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": _GETQUOTES.format(sym),
        "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
    }


def _page_headers() -> dict:
    return {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document", "Referer": "https://www.nseindia.com/"}


def fetch_one(sym: str, session) -> dict | None:
    """Fetch one symbol's issued shares from NSE quote-equity. Returns a record dict or None on
    failure (404 / block / parse error). Warms the get-quotes page (twice, so Akamai's _abck sensor
    settles to a valid state) before the /api/ call so cookies + Referer line up."""
    if session is None or requests is None:
        return None
    try:
        # warm the per-symbol page twice (the second load updates the Akamai _abck to a passing state)
        for _ in range(2):
            session.get(_GETQUOTES.format(sym), timeout=20, headers=_page_headers())
        r = session.get(_QUOTE_URL.format(sym), headers=_quote_headers(sym), timeout=20)
        if r.status_code != 200:
            return {"_status": r.status_code}
        j = r.json()
    except Exception as e:
        return {"_error": type(e).__name__}
    try:
        sec = j.get("securityInfo") or {}
        meta = j.get("metadata") or {}
        info = j.get("info") or {}
        price = j.get("priceInfo") or {}
        issued = sec.get("issuedSize")
        isin = meta.get("isin") or info.get("isin")
        if issued in (None, "", 0, "0"):
            return {"_status": "no_issuedSize"}
        shares = float(str(issued).replace(",", ""))
        if not (shares > 0):
            return {"_status": "bad_issuedSize"}
        return {
            "isin": (isin or "").strip().upper() or None,
            "symbol": sym,
            "name": (info.get("companyName") or meta.get("symbol") or sym),
            "shares": shares,
            "face_value": sec.get("faceValue"),
            "last_price": price.get("lastPrice"),
            "source": "nse_issuedSize",
        }
    except Exception as e:
        return {"_error": f"parse_{type(e).__name__}"}


def build(symbols, pace: float = 0.6, rehandshake_every: int = 10,
          progress=None, asof: str | None = None) -> dict:
    """Collect issued shares for `symbols` (list of NSE symbols). Throttled + resumable: persists after
    every symbol, re-handshakes the NSE session every `rehandshake_every` calls and on the first block.
    `asof` stamps the records (pass a fixed date string for reproducibility; default left blank). Returns
    a status summary. Names already in the cache (same ISIN/symbol) are RE-FETCHED (issuedSize may have
    changed on a corporate action) — to skip-existing, filter `symbols` before calling."""
    log = progress or (lambda m: print(m, flush=True))
    cache = load()
    # index existing by symbol so we can update in place
    by_symbol = {v.get("symbol"): k for k, v in cache.items() if isinstance(v, dict)}
    s = quote_session()
    ok = blocked = failed = 0
    n = len(symbols)
    for i, sym in enumerate(symbols, 1):
        if rehandshake_every and i > 1 and (i % rehandshake_every == 1):
            s = quote_session()                     # periodic fresh cookies
        rec = fetch_one(sym, s)
        if rec and "shares" in rec:
            rec["asof"] = asof or ""
            key = rec.get("isin") or by_symbol.get(sym) or f"SYM:{sym}"
            # if we previously stored this symbol under a different (synthetic) key, drop the old one
            old = by_symbol.get(sym)
            if old and old != key and old in cache:
                cache.pop(old, None)
            cache[key] = rec
            by_symbol[sym] = key
            ok += 1
        elif rec and rec.get("_status") in (401, 403):
            blocked += 1
            s = quote_session()                     # recover the session after a block
            time.sleep(pace * 4)
        else:
            failed += 1
        if i % 10 == 0 or i == n:
            _save(cache)
            log(f"[shares] {i}/{n}  ok={ok} blocked={blocked} failed={failed}  last={sym}")
        time.sleep(pace + random.uniform(0, pace))   # jittered politeness
    _save(cache)
    return {"ok": True, "n": n, "collected": ok, "blocked": blocked, "failed": failed,
            "cache": _CACHE_PATH, "total_in_cache": len([v for v in cache.values() if isinstance(v, dict)])}


# --------------------------------------------------------------------------- accessors
def shares_by_symbol() -> dict:
    """{NSE symbol -> issued shares} from the cache."""
    out = {}
    for v in load().values():
        if isinstance(v, dict) and v.get("symbol") and v.get("shares"):
            out[v["symbol"]] = float(v["shares"])
    return out


def shares_by_isin() -> dict:
    """{ISIN -> issued shares} from the cache (keys that are real ISINs)."""
    return {k: float(v["shares"]) for k, v in load().items()
            if isinstance(v, dict) and v.get("shares") and not str(k).startswith("SYM:")}


def mcap_snapshot(close_by_symbol: dict) -> dict:
    """{symbol -> market cap (Rs)} = close x issued shares, for symbols we have both. close in Rs."""
    sh = shares_by_symbol()
    return {sym: float(close_by_symbol[sym]) * sh[sym]
            for sym in sh if sym in close_by_symbol and close_by_symbol[sym] is not None}


def mcap_resolved() -> dict:
    """COLLECTED (never estimated) market cap per NSE symbol, in Rs crore, with provenance.

    Prefers the EXACT NSE issuedSize x its own NSE last price (self-consistent, per-symbol)
    when the issued-shares cache is populated; otherwise falls back to AMFI's published FULL
    market cap. Also carries the AMFI SEBI size cohort. Both inputs are COLLECTED from reliable
    official sources (NSE quote / AMFI bulk) — neither is a fundamentals-ratio estimate, per
    KV's directive (do not estimate mcap from earnings ratios; the error magnifies).

    Returns {sym: {"mcap_cr": float, "source": str, "cohort": str|None}}; {} if no source.
    """
    am = amfi_mcap_by_symbol()        # {our symbol: full mcap, Rs cr}  (AMFI published, ISIN-anchored)
    co = amfi_cohort_by_symbol()      # {our symbol: 'Large/Mid/Small Cap'}  (ISIN-anchored)
    out: dict = {}
    # exact NSE issuedSize x its own last price (Rs -> Rs cr); inert while the cache is empty.
    # ISIN-ANCHORED: key by our canonical symbol (resolve the row's ISIN), so a rename can't orphan it.
    for isin_key, v in load().items():
        if not isinstance(v, dict):
            continue
        sh, px = v.get("shares"), v.get("last_price")
        sym = _resolve_isin_to_symbol(v.get("isin") or isin_key, v.get("symbol"))
        try:
            if sym and sh and px and float(sh) > 0 and float(px) > 0:
                out[sym] = {"mcap_cr": float(px) * float(sh) / 1e7,
                            "source": "NSE issuedSize × NSE last price",
                            "cohort": co.get(sym)}
        except (TypeError, ValueError):
            continue
    # AMFI published fallback for every symbol not covered by the exact NSE figure
    for sym, mc in am.items():
        if sym not in out and mc and float(mc) > 0:
            out[sym] = {"mcap_cr": float(mc),
                        "source": "AMFI published (6-mo avg full mcap)",
                        "cohort": co.get(sym)}
    return out


# --------------------------------------------------------------------------- AMFI bulk (no-WAF; mcap + cohort)
# AMFI's half-yearly "Average Market Capitalisation of listed companies" is the ONE bulk official file: it
# carries published FULL market cap + the SEBI Large/Mid/Small label per company, ISIN + NSE-symbol keyed, as
# a single plain-GET XLSX (no Akamai). It is REAL collected mcap (official-but-derived; a 6-month average),
# NOT a fundamentals estimate. Validated: its mcap rank-matches Bloomberg cap at rho 0.9947 when time-aligned
# (2026-06-22). Use it for cohort labels + a bulk cross-check/fallback to the exact NSE issuedSize.
_AMFI_INDEX = "https://www.amfiindia.com/otherdata/categorisation-of-stocks"
_AMFI_CACHE = os.path.join(_SHARES_DIR, "amfi_mcap.json")
_MON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _amfi_headers() -> dict:
    return {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Referer": _AMFI_INDEX}


def amfi_latest_url() -> str | None:
    """Discover the NEWEST AverageMarketCapitalization*.xlsx link from AMFI's index page (the filename
    date-suffix drifts each half-year, so we never hard-code it)."""
    if requests is None:
        return None
    try:
        r = requests.get(_AMFI_INDEX, headers=_amfi_headers(), timeout=30)
        links = re.findall(r'(/[^"\']*?AverageMarketCapitalization[^"\']*?\.xlsx)', r.text, re.I)
    except Exception:
        return None
    if not links:
        return None

    def datekey(l):
        m = re.search(r"(\d{1,2})([A-Za-z]{3})(\d{4})", l)
        return (int(m.group(3)), _MON.get(m.group(2).lower(), 0), int(m.group(1))) if m else (0, 0, 0)

    best = max(links, key=datekey)
    return "https:" + best if best.startswith("//") else best


def build_from_amfi(url: str | None = None, progress=None) -> dict:
    """Fetch + parse the AMFI bulk XLSX -> persist {ISIN: {symbol, name, amfi_mcap_cr, category, period}}.
    Plain GET (no cookie handshake). Caches the raw XLSX verbatim. Returns a status dict (never raises)."""
    log = progress or (lambda m: print(m, flush=True))
    if requests is None:
        return {"ok": False, "error": "requests unavailable"}
    url = url or amfi_latest_url()
    if not url:
        return {"ok": False, "error": "could not discover AMFI xlsx url"}
    try:
        r = requests.get(url, headers=_amfi_headers(), timeout=60)
        if r.status_code != 200:
            return {"ok": False, "error": f"http {r.status_code}", "url": url}
        _ensure_dir()
        open(os.path.join(_SHARES_DIR, os.path.basename(url.split("?")[0])), "wb").write(r.content)
        df = pd.read_excel(io.BytesIO(r.content), header=1)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "url": url}
    df = df.rename(columns={c: str(c).strip() for c in df.columns})
    isin_c = next((c for c in df.columns if c.lower() == "isin"), None)
    sym_c = next((c for c in df.columns if "nse symbol" in c.lower()), None)
    mc_c = next((c for c in df.columns if "nse" in c.lower() and "market cap" in c.lower()), None)
    cat_c = next((c for c in df.columns if "categor" in c.lower()), None)
    name_c = next((c for c in df.columns if "company" in c.lower()), None)
    if not (isin_c and mc_c):
        return {"ok": False, "error": "AMFI ISIN/mcap columns not found", "cols": list(df.columns)}
    m = re.search(r"(\d{1,2}[A-Za-z]{3}\d{4})", url)
    period = m.group(1) if m else ""
    out = {}
    for _, row in df.iterrows():
        isin = str(row.get(isin_c) or "").strip().upper()
        if len(isin) != 12:
            continue
        mc = pd.to_numeric(pd.Series([row.get(mc_c)]), errors="coerce").iloc[0]
        if not (mc and mc > 0):
            continue
        out[isin] = {"isin": isin,
                     "symbol": (str(row.get(sym_c)).strip() if sym_c and pd.notna(row.get(sym_c)) else None),
                     "name": (str(row.get(name_c)).strip() if name_c else ""),
                     "amfi_mcap_cr": float(mc),
                     "category": (str(row.get(cat_c)).strip() if cat_c and pd.notna(row.get(cat_c)) else None),
                     "period": period}
    with open(_AMFI_CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=0)
    log(f"[shares] AMFI {period}: {len(out)} NSE mcap rows -> {_AMFI_CACHE}")
    return {"ok": True, "period": period, "n": len(out), "url": url, "cache": _AMFI_CACHE}


def load_amfi() -> dict:
    if not os.path.exists(_AMFI_CACHE):
        return {}
    try:
        with open(_AMFI_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _resolve_isin_to_symbol(isin, fallback_symbol=None):
    """ISIN -> OUR canonical NSE symbol via idmap (the rename-proof join anchor); fall back to the
    vendor's own symbol (uppercased) only if the ISIN is invalid/unresolved or the master is
    unavailable. This is why a vendor rename can't drop a row: a renamed name (e.g. AMFI listing
    'ETERNAL' where our panel still calls it 'ZOMATO', or vice-versa) keeps the SAME ISIN, which
    resolves to whatever symbol our master currently holds live — so the join always lands."""
    try:
        from . import idmap
        if idmap.is_valid_isin(isin):
            sym = idmap.resolve(isin)
            if sym:
                return sym
    except Exception:
        pass
    return str(fallback_symbol).upper() if fallback_symbol else None


def amfi_mcap_by_symbol() -> dict:
    """{OUR canonical NSE symbol -> AMFI full mcap (Rs crore)} — ISIN-ANCHORED via idmap (rename-proof),
    falling back to AMFI's own NSE-symbol column only when the ISIN doesn't resolve to our master."""
    out = {}
    for isin, rec in load_amfi().items():
        mc = rec.get("amfi_mcap_cr")
        if not (mc and float(mc) > 0):
            continue
        sym = _resolve_isin_to_symbol(isin, rec.get("symbol"))
        if sym:
            out[sym] = float(mc)
    return out


def amfi_cohort_by_symbol() -> dict:
    """{OUR canonical NSE symbol -> 'Large Cap'/'Mid Cap'/'Small Cap'} from AMFI's SEBI categorisation,
    ISIN-ANCHORED via idmap (rename-proof), falling back to AMFI's own symbol when unresolved."""
    out = {}
    for isin, rec in load_amfi().items():
        cat = rec.get("category")
        if not cat:
            continue
        sym = _resolve_isin_to_symbol(isin, rec.get("symbol"))
        if sym:
            out[sym] = cat
    return out


def target_symbols(limit: int | None = None, new_only: bool = False) -> list:
    """Our priceable NSE symbols (the stock-panel columns), ordered BIGGEST-FIRST by AMFI mcap — so a
    partial / blocked issuedSize run still covers the names that matter most. `new_only` drops symbols
    already in the issued-shares cache (for incremental resumes). Falls back to panel order if AMFI is
    not yet collected."""
    try:
        from . import stocks
        syms = list(stocks.load().columns)
    except Exception:
        syms = []
    mc = amfi_mcap_by_symbol()
    syms.sort(key=lambda s: mc.get(s, 0.0), reverse=True)
    if new_only:
        have = set(shares_by_symbol().keys())
        syms = [s for s in syms if s not in have]
    return syms[:limit] if limit else syms
