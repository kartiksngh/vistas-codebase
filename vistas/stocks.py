"""
Single-stock price layer for Vistas (NSE equities) — separate from the NSE *index*
fetcher because the reliable free source for individual stocks is Yahoo Finance
(yfinance), NOT niftyindices.com.

  * PRICES are SPLIT/BONUS/DIVIDEND-ADJUSTED (yfinance auto_adjust) so long-term
    charts and CAGR are correct — a bonus issue must not look like a -50% return.
  * Verified (2026-06-19): yfinance adjusted close == NSE bhavcopy close to the paisa
    on a recent day; history reaches ~2000 for old names.
  * Symbol convention: NSE ticker + ".NS"  (RELIANCE -> RELIANCE.NS).
  * Stock adjusted price is a compounding LEVEL — the SAME kind as a TR/PR index — so
    these series drop straight into the existing performance analytics (NAV / CAGR /
    vol / alpha vs an index benchmark).

Snapshot: data/Stocks Data PX till <date>.csv  (wide: Date x SYMBOL, adjusted close).
Network is OPTIONAL / graceful: every entry point returns a status dict and never
raises, so a missing yfinance / offline machine just keeps serving the snapshot.
"""
from __future__ import annotations

import os
import re
import glob
import time
import random
import datetime as dt

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:                       # pragma: no cover
    yf = None

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))

# NIFTY-500 constituent list — STATIC file hosts (NOT the throttled Backpage endpoint).
# First URL VERIFIED 2026-06-19 to return 502 rows (header: Company Name, Industry,
# Symbol, Series, ISIN); it's a static blob, unaffected by the niftyindices Backpage
# rate-limit. The nsearchives/archives mirrors are fallbacks.
CONSTITUENTS_URLS = [
    "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
]
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

# A liquid large/mid-cap starter watchlist (clean Yahoo symbols, sans the .NS suffix),
# spread across sectors — enough to make the Stocks view useful immediately; the full
# NIFTY-500 is one call away via build_full_snapshot().
WATCHLIST = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR", "ITC", "SBIN",
    "BHARTIARTL", "KOTAKBANK", "LT", "BAJFINANCE", "AXISBANK", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO", "NESTLEIND", "ONGC", "NTPC",
    "POWERGRID", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "ADANIENT", "ADANIPORTS",
    "COALINDIA", "HCLTECH", "TECHM", "HDFCLIFE", "SBILIFE", "DRREDDY", "CIPLA",
    "GRASIM", "HINDALCO", "BRITANNIA", "EICHERMOT", "BAJAJFINSV",
]


# ----------------------------------------------------------------------------- fetch
def _clean_sym(s: str) -> str:
    return str(s).strip().upper().replace(".NS", "")


def fetch_stocks(symbols, start="2000-01-01", end=None, batch=8, progress=None) -> pd.DataFrame:
    """Wide DataFrame[Date x SYMBOL] of ADJUSTED daily close for the given NSE symbols.
    Polite: small batches + jittered pauses (yfinance 429s if hammered). Missing
    symbols are skipped. Returns an empty frame on total failure (never raises)."""
    log = progress or (lambda m: None)
    if yf is None:
        log("yfinance not installed")
        return pd.DataFrame()
    end = end or dt.date.today().isoformat()
    syms = [_clean_sym(s) for s in symbols]
    out = {}
    for i in range(0, len(syms), batch):
        chunk = syms[i:i + batch]
        tickers = [s + ".NS" for s in chunk]
        try:
            df = yf.download(tickers, start=start, end=end, auto_adjust=True,
                             progress=False, group_by="ticker", threads=False)
        except Exception as e:
            log(f"  batch {i}-{i+len(chunk)} failed: {e}")
            df = None
        if df is not None and len(df):
            for s in chunk:
                tk = s + ".NS"
                try:
                    col = df[tk]["Close"] if len(chunk) > 1 else df["Close"]
                    col = pd.to_numeric(col, errors="coerce").dropna()
                    if len(col):
                        out[s] = col
                except Exception:
                    pass
        log(f"  [{min(i+batch, len(syms))}/{len(syms)}] fetched {len([s for s in chunk if s in out])}/{len(chunk)}")
        time.sleep(random.uniform(0.8, 1.8))          # polite gap between batches
    if not out:
        return pd.DataFrame()
    wide = pd.DataFrame(out)
    wide.index = pd.DatetimeIndex(wide.index).normalize()
    wide = wide[~wide.index.duplicated(keep="last")].sort_index()
    wide.index.name = "Date"
    return wide


# ----------------------------------------------------------------------------- snapshot I/O
def _newest(pattern):
    cands = glob.glob(os.path.join(DATA_DIR, pattern))
    if not cands:
        return None

    def _key(p):
        m = re.search(r"till (.+)\.csv$", os.path.basename(p))
        d = pd.to_datetime(m.group(1).strip(), errors="coerce") if m else None
        return d if pd.notna(d) else pd.Timestamp(os.path.getmtime(p), unit="s")

    return max(cands, key=_key)


def latest_csv():
    """The stock-price snapshot the app serves. PREFERENCE: the bhavcopy-reconstructed
    total-return panel ("Stocks Data TR till *.csv") — decimal-accurate, sourced from the
    NSE exchange close + official corporate actions (see vistas/bhav_prices.py). It FALLS
    BACK to the legacy yfinance adjusted-close panel ("Stocks Data PX till *.csv") only when
    no bhavcopy panel is present, so the bhavcopy build switches the whole app over simply by
    writing its file, and reverts just as simply by removing it."""
    return _newest("Stocks Data TR till *.csv") or _newest("Stocks Data PX till *.csv")


def _write_dated(wide: pd.DataFrame) -> str:
    last = wide.index.max().date()
    out = os.path.join(DATA_DIR, f"Stocks Data PX till {last.strftime('%b %#d, %Y')}.csv")
    wide.reset_index().to_csv(out, index=False)
    return out


def build_snapshot(symbols=None, start="2000-01-01", end=None, progress=None) -> dict:
    """Fetch + merge into the stock snapshot CSV. Defaults to the WATCHLIST; pass the
    full constituent list for all of NIFTY 500. Merges with any existing snapshot."""
    log = progress or (lambda m: print(m, flush=True))
    symbols = symbols or WATCHLIST
    log(f"[stocks] fetching {len(symbols)} symbols {start}..{end or 'today'} (adjusted)…")
    wide = fetch_stocks(symbols, start=start, end=end, progress=log)
    if wide.empty:
        return {"ok": False, "error": "no stock data fetched (yfinance unreachable?)"}
    existing = latest_csv()
    if existing:
        try:
            old = pd.read_csv(existing)
            old["Date"] = pd.to_datetime(old["Date"], errors="coerce")
            old = old.dropna(subset=["Date"]).set_index("Date").sort_index()
            # prefer freshly-fetched values where present; fall back to the prior snapshot
            # for dates/symbols not re-fetched, so a partial pull never drops history.
            wide = wide.combine_first(old).sort_index()
        except Exception:
            pass
    out = _write_dated(wide)
    return {"ok": True, "file": os.path.basename(out), "n_symbols": wide.shape[1],
            "n_days": wide.shape[0], "asof": wide.index.max().date().isoformat(),
            "start": wide.index.min().date().isoformat()}


def build_full_snapshot(start="2000-01-01", end=None, progress=None) -> dict:
    """Fetch the FULL current NIFTY 500 (constituents from the static NSE archive)."""
    syms = nifty500_symbols()
    if not syms:
        return {"ok": False, "error": "could not load NIFTY 500 constituent list"}
    return build_snapshot([s for s in syms], start=start, end=end, progress=progress)


NSE_EQUITY_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


def all_nse_symbols() -> list:
    """EVERY NSE-listed equity symbol (~2000) from the static NSE archive — the universe
    for 'all Indian stocks' (beyond NIFTY 500). Returns [] on failure."""
    if requests is None:
        return []
    for url in (NSE_EQUITY_LIST,
                "https://archives.nseindia.com/content/equities/EQUITY_L.csv"):
        try:
            r = requests.get(url, headers=_UA, timeout=40)
            if r.status_code == 200 and "SYMBOL" in r.text:
                df = pd.read_csv(pd.io.common.StringIO(r.text))
                col = next((c for c in df.columns if c.strip().upper() == "SYMBOL"), None)
                if col:
                    return [str(s).strip() for s in df[col].dropna()]
        except Exception:
            continue
    return []


def update_stocks(progress=None) -> dict:
    """Append last_date->today for the symbols already in the snapshot (small tail)."""
    csv = latest_csv()
    if csv is None:
        return {"ok": False, "error": "no stock snapshot yet — run build_snapshot first"}
    old = pd.read_csv(csv)
    old["Date"] = pd.to_datetime(old["Date"], errors="coerce")
    old = old.dropna(subset=["Date"]).set_index("Date").sort_index()
    start = (old.index.max() + pd.Timedelta(days=1)).date().isoformat()
    return build_snapshot([c for c in old.columns], start=start, progress=progress)


# ----------------------------------------------------------------------------- constituents
def nifty500_symbols() -> list:
    """Current NIFTY 500 trading symbols from the static NSE archive (not the throttled
    Backpage). Returns [] on failure."""
    if requests is None:
        return []
    for url in CONSTITUENTS_URLS:
        try:
            r = requests.get(url, headers=_UA, timeout=30)
            if r.status_code == 200 and "Symbol" in r.text:
                df = pd.read_csv(pd.io.common.StringIO(r.text))
                col = next((c for c in df.columns if c.strip().lower() == "symbol"), None)
                if col:
                    return [str(s).strip() for s in df[col].dropna()]
        except Exception:
            continue
    return []


# ----------------------------------------------------------------------------- load (serve)
_CACHE = {"path": None, "df": None, "mtime": None}


def load() -> pd.DataFrame:
    """Cached wide stock-price frame (adjusted close)."""
    path = latest_csv()
    if path is None:
        return pd.DataFrame()
    mtime = os.path.getmtime(path)
    if _CACHE["df"] is None or _CACHE["path"] != path or _CACHE["mtime"] != mtime:
        df = pd.read_csv(path)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
        df = df.apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")
        _CACHE.update({"path": path, "df": df, "mtime": mtime})
    return _CACHE["df"]


def available() -> list:
    df = load()
    return list(df.columns) if len(df) else []


def coverage() -> dict:
    df = load()
    out = {}
    for c in df.columns:
        s = df[c].dropna()
        if len(s):
            out[c] = {"start": s.index[0].strftime("%Y-%m-%d"),
                      "end": s.index[-1].strftime("%Y-%m-%d"), "n_obs": int(len(s))}
    return out


# ----------------------------------------------------------------------------- company names
# A {SYMBOL: "Company Name"} map so the picker can be searched by part of the company
# name (or an acronym), not only the bare NSE ticker. Cached to data/stock_names.json;
# rebuilt when missing/stale by merging the NIFTY-500 constituents CSV (broad coverage)
# with any locally cached Screener bundle names (authoritative). Never raises.
NAMES_CACHE = os.path.join(DATA_DIR, "stock_names.json")
_SCREENER_DIR = os.path.join(DATA_DIR, "screener")
_NAME_RE = re.compile(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _constituent_names() -> dict:
    """{SYMBOL: Company Name} from the NIFTY-500 constituents CSV (network, best-effort)."""
    if requests is None:
        return {}
    for url in CONSTITUENTS_URLS:
        try:
            r = requests.get(url, headers=_UA, timeout=30)
            if r.status_code == 200 and "Symbol" in r.text:
                df = pd.read_csv(pd.io.common.StringIO(r.text))
                scol = next((c for c in df.columns if c.strip().lower() == "symbol"), None)
                ncol = next((c for c in df.columns if "company" in c.strip().lower()), None)
                if scol and ncol:
                    return {str(s).strip().upper(): str(n).strip()
                            for s, n in zip(df[scol], df[ncol]) if pd.notna(s) and pd.notna(n)}
        except Exception:
            continue
    return {}


def _screener_names() -> dict:
    """{SYMBOL: company name} from locally cached Screener bundles — read cheaply (just
    the leading 'name' field, no full JSON parse). Authoritative when present."""
    out = {}
    if not os.path.isdir(_SCREENER_DIR):
        return out
    for f in os.listdir(_SCREENER_DIR):
        if not f.endswith(".json"):
            continue
        sym = os.path.splitext(f)[0].upper()
        try:
            with open(os.path.join(_SCREENER_DIR, f), encoding="utf-8") as fh:
                head = fh.read(600)            # name sits at the top of the bundle
            m = _NAME_RE.search(head)
            if m and m.group(1):
                out[sym] = m.group(1)
        except Exception:
            continue
    return out


# ----------------------------------------------------------------------------- security master / aliases
# The bhavcopy build writes the PERMID lineage to output/stock_security_master.json
# {permid: {isins, symbols, latest_symbol, name, n_isins}}. We read it (output/ preferred, data/
# fallback) to (a) give the long-tail stocks a company NAME and (b) let the picker resolve an OLD
# ticker to the live column (TATAMOTORS -> TMPV).
OUTPUT_DIR = os.path.abspath(os.path.join(HERE, "..", "output"))
_SECMASTER_FILES = (os.path.join(DATA_DIR, "stock_security_master.json"),
                    os.path.join(OUTPUT_DIR, "stock_security_master.json"))
_SECM = {"path": None, "mtime": None, "master": None}
_ALIAS = {"key": None, "map": None}

def security_master() -> dict:
    """{permid: {isins, symbols, latest_symbol, name, n_isins}} from the bhavcopy build, or {}."""
    import json
    path = next((p for p in _SECMASTER_FILES if os.path.exists(p)), None)
    if path is None:
        return {}
    mt = os.path.getmtime(path)
    if _SECM["master"] is None or _SECM["path"] != path or _SECM["mtime"] != mt:
        try:
            with open(path, encoding="utf-8") as fh:
                sm = json.load(fh)
            _SECM.update({"path": path, "mtime": mt, "master": sm.get("master", sm)})
        except Exception:
            _SECM.update({"path": path, "mtime": mt, "master": {}})
    return _SECM["master"] or {}


def aliases() -> dict:
    """{current_panel_symbol: [former NSE symbols]} so the picker resolves an old ticker (e.g.
    TATAMOTORS -> TMPV, AMIORG -> ACUTAAS) to the live column. Guards against error: a former symbol
    that is ITSELF a current panel column is dropped (it belongs to that live stock), and a former
    symbol that would map to MORE THAN ONE current stock is dropped as ambiguous (never fabricate a
    mapping). Cached on the (security-master, panel) identity."""
    cols = set(available())
    key = (_SECM.get("mtime"), len(cols))
    if _ALIAS["map"] is not None and _ALIAS["key"] == key:
        return _ALIAS["map"]
    cand = {}                                  # former symbol -> set of current targets
    for rec in security_master().values():
        latest = rec.get("latest_symbol")
        if latest not in cols:                 # only alias to a symbol the panel actually carries
            continue
        for s in rec.get("symbols", []):
            if not s or s == latest or s in cols:
                continue
            cand.setdefault(s, set()).add(latest)
    out = {}
    for s, targets in cand.items():
        if len(targets) == 1:                  # unambiguous only
            out.setdefault(next(iter(targets)), []).append(s)
    out = {k: sorted(set(v)) for k, v in out.items()}
    _ALIAS.update({"key": key, "map": out})
    return out


_NAME_LOWER = {"and", "of", "the", "for", "&"}
_NAME_KEEP_UPPER = {
    "ITC", "HDFC", "ICICI", "IDFC", "IDBI", "SBI", "LIC", "GAIL", "ONGC", "NTPC", "NMDC", "BHEL",
    "BPCL", "HPCL", "IOC", "IOCL", "NHPC", "PFC", "REC", "IRCTC", "IRFC", "RVNL", "BEL", "HAL",
    "MRF", "TVS", "DLF", "JSW", "UPL", "ABB", "ACC", "MOIL", "SAIL", "CESC", "PNB", "RBL", "TCS",
    "KEC", "NCC", "HEG", "BSE", "NSE", "MCX", "CAMS", "KFIN", "CDSL", "NSDL", "GMR", "GVK", "DCM",
    "LTI", "LTTS", "HUL", "BASF", "SJVN", "MTNL", "BSNL", "ZEE", "PVR", "HFCL", "MMTC", "STC",
    "FACT", "PI", "AU", "CSB", "DCB", "MRPL", "IGL", "MGL", "HUDCO", "EIH", "DCW", "GHCL", "JBM",
}

def _smart_title(name: str) -> str:
    """Title-case an ALL-CAPS exchange name for display without mangling acronyms: keep known/short
    consonant-only acronyms upper (HDFC, ITC, NMDC), lower the joiners (and/of/the), normalise the
    legal suffix (LIMITED -> Ltd). Imperfect on rare names but only used for the long tail (Screener /
    constituent names, which are already nicely cased, take precedence)."""
    if not name:
        return ""
    out = []
    for i, w in enumerate(re.split(r"\s+", name.strip())):
        u = w.upper().strip(".")
        if u in ("LIMITED", "LTD"):
            out.append("Ltd")
        elif u in ("PRIVATE", "PVT"):
            out.append("Pvt")
        elif i > 0 and w.lower() in _NAME_LOWER:
            out.append(w.lower())
        elif u in _NAME_KEEP_UPPER:
            out.append(u)
        elif any(ch.isdigit() for ch in w) or "&" in w:
            out.append(w.upper())
        elif w.isupper() and len(w) <= 4 and not re.search(r"[AEIOU]", w):
            out.append(w)                      # short consonant-only acronym (JSW, NMDC, TVS)
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def _secmaster_names() -> dict:
    """{SYMBOL: smart-cased company name} for the WHOLE bhavcopy universe (the long tail)."""
    out = {}
    for rec in security_master().values():
        latest, nm = rec.get("latest_symbol"), rec.get("name")
        if latest and nm:
            out[str(latest).upper()] = _smart_title(nm)
    return out


def company_names(refresh: bool = False) -> dict:
    """{SYMBOL: company name} for the stock universe, for the picker's name/acronym
    search. Cached; rebuilt when missing/stale (or refresh=True). Always a dict."""
    import json
    cache_ok = os.path.exists(NAMES_CACHE)
    if cache_ok and not refresh:
        try:                                   # serve cache unless Screener OR the security master is newer
            sdir_m = os.path.getmtime(_SCREENER_DIR) if os.path.isdir(_SCREENER_DIR) else 0
            smf = next((p for p in _SECMASTER_FILES if os.path.exists(p)), None)
            sm_m = os.path.getmtime(smf) if smf else 0
            if os.path.getmtime(NAMES_CACHE) >= max(sdir_m, sm_m):
                with open(NAMES_CACHE, encoding="utf-8") as fh:
                    return json.load(fh)
        except Exception:
            pass
    names = {}
    names.update(_secmaster_names())           # broad base: the WHOLE bhavcopy universe (long tail)
    names.update(_constituent_names())         # NIFTY-500, nicely cased, overrides
    names.update(_screener_names())            # authoritative, overrides
    if names:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(NAMES_CACHE, "w", encoding="utf-8") as fh:
                json.dump(names, fh)
        except Exception:
            pass
    elif cache_ok:                             # nothing built but a cache exists -> use it
        try:
            with open(NAMES_CACHE, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return names


if __name__ == "__main__":
    import json
    print(json.dumps(build_snapshot(progress=lambda m: print(m, flush=True)), indent=2))
