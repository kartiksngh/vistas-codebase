"""
Active mutual-fund NAV layer for Vistas (AMFI + mfapi — no API key).

Adds Indian mutual-fund *scheme* NAVs (the per-unit price of an actively-managed fund)
as selectable price series next to NSE indices and stocks. NAV is already a TOTAL-RETURN
level (dividends reinvest into NAV in a Growth-option scheme), so it drops straight into the
existing performance engine and is directly comparable to a benchmark's TR index.

Sources (per VISTAS_ACTIVE_PLAN.md, "no score for error"):
  - MASTER + latest NAV : AMFI `NAVAll.txt` — one ~5 MB download gives EVERY scheme grouped
                          under SEBI category headers (code; ISIN; name; NAV; date). We keep
                          the OPEN-ENDED ACTIVE-EQUITY DIRECT-GROWTH subset (~535 schemes).
  - NAV history         : mfapi `api.mfapi.in/mf/{code}` (community mirror BUILT ON AMFI; 20+ yr
                          depth). AMFI is the authority; mfapi is just the transport.
  - cross-check         : for EVERY scheme, mfapi's latest NAV must equal AMFI's latest NAV
                          (NAVAll.txt) within tolerance — flag mismatches, prefer AMFI.
  - ISIN -> NSE symbol  : reused in-repo `data/stock_security_master.json` (ISIN lineage), so a
                          fund's equity holdings can later join our stock panel/fundamentals.

Snapshot: data/funds/MF NAV till <date>.csv  (wide: Date x schemeCode).
Per-scheme cache: data/funds/nav/<code>.json. Master: data/funds/scheme_master.json.
Same wide-CSV shape + graceful network degrade as world.py / stocks.py (never raises; offline
just keeps serving the snapshot). The display name (scheme name) is the canonical SERIES KEY
everywhere downstream (catalog, deck embed, analytics) — like world.py's friendly names.
"""
from __future__ import annotations

import os
import re
import glob
import json
import time
import random
import datetime as dt

import pandas as pd

try:
    import requests
except Exception:                                            # pragma: no cover
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))
FUNDS_DIR = os.path.join(DATA_DIR, "funds")
NAV_CACHE = os.path.join(FUNDS_DIR, "nav")
MASTER_FILE = os.path.join(FUNDS_DIR, "scheme_master.json")
ISIN_MAP_FILE = os.path.join(FUNDS_DIR, "isin_to_nse.json")
SECURITY_MASTER = os.path.join(DATA_DIR, "stock_security_master.json")

AMFI_NAVALL = "https://www.amfiindia.com/spages/NAVAll.txt"
AMFI_HIST = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"   # first-party, survivorship-free
MFAPI_BASE = "https://api.mfapi.in/mf"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
NAV_HISTORY_FLOOR = "2006-04-01"     # earliest NAV we keep (matches the legacy snapshot start)
_AMFI_MON = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
             "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

# Phase-1 universe: open-ended ACTIVE-EQUITY Direct-Growth (the canonical clean series — no
# distributor-fee drag; "active" = the equity scheme categories, which exclude index/ETF that
# live under "Other Scheme - Index Funds"). Wider categories come in later phases.
EQUITY_CAT_KEY = "Equity Scheme"
NAV_XCHECK_TOL = 0.001       # 0.1% — mfapi-latest vs AMFI-latest agreement tolerance


# ----------------------------------------------------------------------------- helpers
def _say(progress, msg):
    (progress or (lambda m: print(m, flush=True)))(msg)


def _is_direct_growth(name: str) -> bool:
    n = name.lower()
    return ("direct" in n) and ("growth" in n) and ("idcw" not in n) and ("dividend" not in n)


# ----------------------------------------------------------------------------- AMFI master
def fetch_amfi_master(equity_only: bool = True, progress=None) -> list:
    """Parse AMFI NAVAll.txt -> [{code, isin, name, category, fund_house, amfi_nav, amfi_date}]
    for open-ended Direct-Growth schemes (active-equity only by default). Network; returns []
    on failure (caller degrades to the cached snapshot)."""
    if requests is None:
        return []
    try:
        r = requests.get(AMFI_NAVALL, headers=_UA, timeout=45)
        r.encoding = "utf-8"                                 # AMFI is UTF-8; requests guesses latin-1
        text = r.text
    except Exception as e:                                   # pragma: no cover
        _say(progress, f"[funds_nav] AMFI master fetch failed: {e}")
        return []
    out = []
    amc, cat = None, None
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if ";" not in s:
            if "(" in s and ")" in s:
                cat = s                                      # category header, e.g. "Open Ended Schemes(Equity Scheme - Large Cap Fund)"
            elif s != "Scheme Code":
                amc = s                                      # AMC / fund-house line
            continue
        parts = s.split(";")
        if parts[0].strip() == "Scheme Code":
            continue
        if len(parts) < 6:
            continue
        code, isin1, isin2, name, nav, date = (p.strip() for p in parts[:6])
        if not (cat and "Open Ended" in cat):
            continue
        if equity_only and EQUITY_CAT_KEY not in cat:
            continue
        if not _is_direct_growth(name):
            continue
        try:
            navf = float(nav)
        except Exception:
            navf = None
        if navf is None or navf <= 0:
            continue
        m = re.search(r"\((.*)\)", cat)                      # "Equity Scheme - Large Cap Fund"
        out.append({"code": code, "isin": (isin1 or isin2 or None),
                    "name": name, "category": (m.group(1).strip() if m else cat),
                    "fund_house": amc, "amfi_nav": navf, "amfi_date": date})
    _say(progress, f"[funds_nav] AMFI master: {len(out)} open-ended {'equity ' if equity_only else ''}Direct-Growth schemes")
    return out


# ----------------------------------------------------------------------------- ISIN -> NSE symbol
def build_isin_map(progress=None) -> dict:
    """{ISIN: NSE symbol} from the in-repo security master (ISIN lineage). Cached to
    data/funds/isin_to_nse.json. Lets a fund's equity holdings join our stock panel later."""
    m = {}
    try:
        with open(SECURITY_MASTER, encoding="utf-8") as f:
            sm = json.load(f)
        master = sm.get("master", sm) if isinstance(sm, dict) else {}
        for _permid, rec in master.items():
            if not isinstance(rec, dict):
                continue
            sym = rec.get("latest_symbol") or (rec.get("symbols") or [None])[-1]
            if not sym:
                continue
            for isin in (rec.get("isins") or []):
                if isin:
                    m[str(isin).strip().upper()] = sym
    except Exception as e:                                   # pragma: no cover
        _say(progress, f"[funds_nav] ISIN map build failed: {e}")
        return {}
    try:
        os.makedirs(FUNDS_DIR, exist_ok=True)
        with open(ISIN_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(m, f)
    except Exception:
        pass
    _say(progress, f"[funds_nav] ISIN->symbol map: {len(m)} ISINs cached")
    return m


def load_isin_map() -> dict:
    try:
        with open(ISIN_MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ----------------------------------------------------------------------------- NAV history (mfapi)
def fetch_nav_history(code, session=None) -> pd.Series:
    """mfapi /mf/{code} -> Series(Date -> NAV), cleaned (NAV>0, dayfirst dates, dedup keep-last,
    sorted). Empty Series on any failure. Never raises."""
    if requests is None:
        return pd.Series(dtype=float)
    try:
        sess = session or requests
        r = sess.get(f"{MFAPI_BASE}/{code}", headers=_UA, timeout=30)
        d = r.json()
        rows = d.get("data") or []
        if not rows:
            return pd.Series(dtype=float)
        dates = pd.to_datetime([x["date"] for x in rows], format="%d-%m-%Y", errors="coerce")
        navs = pd.to_numeric([x["nav"] for x in rows], errors="coerce")
        s = pd.Series(navs, index=dates).dropna()
        s = s[s > 0]
        s = s[~s.index.duplicated(keep="first")].sort_index()   # mfapi is newest-first; keep-first=latest dup
        return s
    except Exception:
        return pd.Series(dtype=float)


# --------------------------------------------------- NAV history (AMFI hist-report, PRIMARY tier)
def fetch_nav_history_amfi_bulk(codes_wanted=None, start=NAV_HISTORY_FLOOR, end=None,
                                progress=None, pace=(0.3, 0.7)) -> dict:
    """AMFI's own historical-NAV report (`DownloadNAVHistoryReport_Po`) — FIRST-PARTY and
    SURVIVORSHIP-FREE — pulled in <=90-day windows over [start, end]. ONE window returns EVERY
    scheme (incl. merged/wound-up), so this is a bulk transport (far fewer requests than per-scheme
    mfapi and no third-party dependency). Format gotcha: the date is the LAST ';'-field (older rows
    carry extra Repurchase/Sale columns -> 8 fields, newer -> 6). Returns {code: pd.Series(Date->NAV)}
    restricted to `codes_wanted` (None = keep all). Never raises; returns {} if the portal is down."""
    log = progress or (lambda m: None)
    if requests is None:
        return {}
    want = set(map(str, codes_wanted)) if codes_wanted is not None else None
    start_d = pd.Timestamp(start).normalize()
    end_d = (pd.Timestamp(end) if end else pd.Timestamp.today()).normalize()
    sess = requests.Session()
    sess.headers.update(_UA)
    sess.verify = False                                       # portal serves a self-signed-ish chain
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    acc, win, n_win = {}, start_d, 0
    while win <= end_d:
        w_end = min(win + pd.Timedelta(days=89), end_d)
        txt = None
        for attempt in range(3):
            try:
                r = sess.get(AMFI_HIST, params={"tp": "1", "frmdt": win.strftime("%d-%b-%Y"),
                                                "todt": w_end.strftime("%d-%b-%Y")}, timeout=90)
                if r.status_code == 200 and len(r.text) > 200:
                    txt = r.text
                    break
            except Exception:
                pass
            time.sleep(2 + 3 * attempt)
        if txt:
            for ln in txt.splitlines():
                if ";" not in ln:
                    continue
                p = ln.split(";")
                if len(p) < 6 or p[0].strip() == "Scheme Code":
                    continue
                code = p[0].strip()
                if not code or (want is not None and code not in want):
                    continue
                ds = p[-1].strip().split("-")
                if len(ds) != 3 or ds[1] not in _AMFI_MON:
                    continue
                try:
                    nav = float(p[4])
                except Exception:
                    continue
                if nav <= 0:
                    continue
                acc.setdefault(code, {})[pd.Timestamp(int(ds[2]), _AMFI_MON[ds[1]], int(ds[0]))] = nav
        n_win += 1
        if n_win % 8 == 0:
            log(f"[funds_nav]   AMFI hist window {n_win} ({win.date()}..{w_end.date()}): {len(acc)} schemes so far…")
        win = w_end + pd.Timedelta(days=1)
        if pace:
            time.sleep(random.uniform(*pace))
    out = {}
    for code, dd in acc.items():
        s = pd.Series(dd).sort_index()
        out[code] = s[~s.index.duplicated(keep="last")]
    return out


# ----------------------------------------------------------------------------- snapshot build
def latest_csv():
    cands = glob.glob(os.path.join(FUNDS_DIR, "MF NAV till *.csv"))
    if not cands:
        return None

    def _key(p):
        m = re.search(r"till (.+)\.csv$", os.path.basename(p))
        d = pd.to_datetime(m.group(1).strip(), errors="coerce") if m else None
        return d if pd.notna(d) else pd.Timestamp(os.path.getmtime(p), unit="s")

    return max(cands, key=_key)


def _write_dated(wide: pd.DataFrame) -> str:
    last = wide.index.max().date()
    out = os.path.join(FUNDS_DIR, f"MF NAV till {last.strftime('%b %#d, %Y')}.csv")
    wide.reset_index().rename(columns={"index": "Date"}).to_csv(out, index=False)
    return out


def build_snapshot(limit=None, equity_only=True, pace=(0.05, 0.18), progress=None,
                   full=False, refresh_tail_days=35) -> dict:
    """Build/refresh the MF-NAV snapshot. Identity + latest NAV from AMFI `NAVAll.txt`; NAV HISTORY
    from AMFI's own hist-report (PRIMARY — first-party, survivorship-free, bulk windowed) with mfapi
    as a per-scheme FALLBACK for any live code the portal didn't return. Writes per-scheme cache
    (rebuilt from the MERGED full series, so an incremental tail-pull never thins it) + the wide
    snapshot CSV (fresh-wins merge) + scheme_master.json, and cross-checks each series' latest NAV
    vs AMFI `NAVAll`. Never raises; returns a status dict.

    Incremental by default: with an existing snapshot it re-pulls only a short tail (catches NAV
    revisions) and fresh-wins-merges; `full=True` (or a cold start) does the full backfill from the
    floor. mfapi is the resilience tier — if the AMFI portal is down, every code falls back to it."""
    log = progress or (lambda m: print(m, flush=True))
    os.makedirs(NAV_CACHE, exist_ok=True)
    build_isin_map(progress=log)
    master = fetch_amfi_master(equity_only=equity_only, progress=log)
    if not master:
        return {"ok": False, "error": "AMFI master unavailable (offline?) — kept cached snapshot"}
    if limit:
        master = master[:limit]
    want_codes = [sc["code"] for sc in master]

    # existing snapshot — fresh-wins merge base + incremental-window anchor
    old = None
    existing = latest_csv()
    if existing:
        try:
            old = pd.read_csv(existing)
            old["Date"] = pd.to_datetime(old["Date"], errors="coerce")
            old = old.dropna(subset=["Date"]).set_index("Date").sort_index()
            old.columns = [str(c) for c in old.columns]
        except Exception:
            old = None

    # PRIMARY tier: AMFI hist-report (first-party, survivorship-free)
    if full or old is None or not len(old):
        start = NAV_HISTORY_FLOOR
        log(f"[funds_nav] AMFI hist-report: FULL backfill from {start}…")
    else:
        start = (old.index.max() - pd.Timedelta(days=refresh_tail_days)).strftime("%Y-%m-%d")
        log(f"[funds_nav] AMFI hist-report: incremental from {start} (tail {refresh_tail_days}d)…")
    amfi_hist = fetch_nav_history_amfi_bulk(want_codes, start=start, progress=log)
    log(f"[funds_nav] AMFI hist-report returned {len(amfi_hist)} of {len(want_codes)} live schemes")

    sess = requests.Session() if requests else None
    if sess:
        sess.headers.update(_UA)
    cols, scheme_master, src_tag, n_fallback = {}, {}, {}, 0
    for sc in master:
        code = sc["code"]
        s = amfi_hist.get(code)
        src = "amfi_hist"
        if s is None or len(s) == 0:                          # FALLBACK tier: mfapi per-scheme (full history)
            s = fetch_nav_history(code, session=sess)
            src = "mfapi"
            n_fallback += 1
            if pace:
                time.sleep(random.uniform(*pace))
        # inject AMFI NAVAll's latest (today) — the hist-report lags NAVAll ~1 day, so this keeps the
        # snapshot current AND makes the latest-NAV cross-check apples-to-apples
        an, ad = sc.get("amfi_nav"), sc.get("amfi_date")
        if an and ad:
            try:
                dts = pd.to_datetime(ad, dayfirst=True, errors="coerce")
                if pd.notna(dts):
                    if s is None or not len(s):
                        s = pd.Series(dtype=float)
                    s.loc[dts.normalize()] = float(an)
                    s = s[~s.index.duplicated(keep="last")].sort_index()
            except Exception:
                pass
        if s is not None and len(s):
            cols[code] = s
            src_tag[code] = src
        scheme_master[code] = {"name": sc["name"], "category": sc["category"],
                               "fund_house": sc["fund_house"], "isin": sc["isin"]}

    if not cols and (old is None or not len(old)):
        return {"ok": False, "error": "no NAV history fetched (AMFI + mfapi both unreachable) — kept cached snapshot"}

    wide = pd.DataFrame(cols).sort_index() if cols else pd.DataFrame()
    wide.index.name = "Date"
    if old is not None and len(old):
        wide = wide.combine_first(old).sort_index() if len(wide) else old.sort_index()   # fresh-wins
    keep = [c for c in wide.columns if wide[c].notna().sum() >= 30]   # drop too-short series
    wide = wide[keep]

    # per-scheme cache rebuilt from the MERGED full series + latest-NAV cross-check vs AMFI NAVAll
    meta = {sc["code"]: sc for sc in master}
    amfi_latest = {sc["code"]: sc.get("amfi_nav") for sc in master}
    xcheck = {"checked": 0, "mismatch": 0, "examples": []}
    for code in wide.columns:
        s = wide[code].dropna()
        if not len(s):
            continue
        m = meta.get(code, {})
        try:
            with open(os.path.join(NAV_CACHE, f"{code}.json"), "w", encoding="utf-8") as f:
                json.dump({"code": code, "name": m.get("name"), "category": m.get("category"),
                           "fund_house": m.get("fund_house"), "isin": m.get("isin"),
                           "source": src_tag.get(code, "cache"),
                           "dates": [d.strftime("%Y-%m-%d") for d in s.index],
                           "nav": [round(float(v), 4) for v in s.to_numpy()]},
                          f, separators=(",", ":"))
        except Exception:
            pass
        try:
            al = amfi_latest.get(code)
            if al:
                xcheck["checked"] += 1
                if abs(float(s.iloc[-1]) - al) / al > NAV_XCHECK_TOL:
                    xcheck["mismatch"] += 1
                    if len(xcheck["examples"]) < 8:
                        xcheck["examples"].append({"code": code, "snapshot": round(float(s.iloc[-1]), 4),
                                                   "amfi": al, "name": m.get("name"), "src": src_tag.get(code)})
        except Exception:
            pass

    out = _write_dated(wide)
    try:
        with open(MASTER_FILE, "w", encoding="utf-8") as f:
            json.dump(scheme_master, f, ensure_ascii=False)
    except Exception:
        pass
    n_amfi = sum(1 for v in src_tag.values() if v == "amfi_hist")
    n_mf = sum(1 for v in src_tag.values() if v == "mfapi")
    res = {"ok": True, "file": os.path.basename(out), "n_schemes": wide.shape[1],
           "n_days": wide.shape[0], "asof": wide.index.max().date().isoformat(),
           "start": wide.index.min().date().isoformat(),
           "sources": {"amfi_hist": n_amfi, "mfapi": n_mf}, "n_fallback": n_fallback, "xcheck": xcheck}
    log(f"[funds_nav] snapshot: {res['n_schemes']} schemes x {res['n_days']} days, {res['start']}..{res['asof']}; "
        f"sources amfi_hist={n_amfi} mfapi_fallback={n_mf}; cross-check {xcheck['checked']} checked, "
        f"{xcheck['mismatch']} mismatch")
    return res


# ----------------------------------------------------------------------------- serve
_CACHE = {"path": None, "df": None, "mtime": None}


def scheme_master() -> dict:
    try:
        with open(MASTER_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load() -> pd.DataFrame:
    """Wide snapshot, columns = scheme codes (strings)."""
    path = latest_csv()
    if path is None:
        return pd.DataFrame()
    mtime = os.path.getmtime(path)
    if _CACHE["df"] is None or _CACHE["path"] != path or _CACHE["mtime"] != mtime:
        df = pd.read_csv(path)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
        df.columns = [str(c) for c in df.columns]
        df = df.apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")
        _CACHE.update({"path": path, "df": df, "mtime": mtime})
    return _CACHE["df"]


def load_named() -> pd.DataFrame:
    """Snapshot with columns renamed scheme-code -> scheme NAME (the canonical series key
    everywhere downstream). Drops codes missing from the master."""
    df = load()
    if not len(df):
        return df
    sm = scheme_master()
    ren = {c: (sm.get(c, {}).get("name") or c) for c in df.columns}
    df = df.rename(columns=ren)
    return df.loc[:, ~df.columns.duplicated()]


def available() -> list:
    df = load_named()
    return list(df.columns) if len(df) else []


def coverage() -> dict:
    """{scheme name: {start,end,n_obs}} for the picker."""
    df = load_named()
    out = {}
    for c in df.columns:
        s = df[c].dropna()
        if len(s):
            out[c] = {"start": s.index[0].strftime("%Y-%m-%d"),
                      "end": s.index[-1].strftime("%Y-%m-%d"), "n_obs": int(len(s))}
    return out


def names() -> dict:
    """{scheme name: scheme code} — picker secondary search key (type the code too)."""
    sm = scheme_master()
    return {v.get("name", k): k for k, v in sm.items()}


def categories() -> dict:
    """{scheme name: SEBI category} — for grouping in the picker."""
    sm = scheme_master()
    return {v.get("name", k): v.get("category", "Equity") for k, v in sm.items()}


# ----------------------------------------------------------------------------- self-test
def _selftest(limit=12):
    print("building a small MF-NAV snapshot (limit=%d) to verify the pipeline…" % limit)
    r = build_snapshot(limit=limit, progress=print)
    print("RESULT:", json.dumps({k: v for k, v in r.items() if k != "xcheck"}, default=str))
    print("xcheck:", r.get("xcheck"))
    df = load_named()
    print("load_named:", df.shape, "| first cols:", list(df.columns)[:4])
    cov = coverage()
    for nm in list(cov)[:4]:
        print("  ", nm[:55], "->", cov[nm])


if __name__ == "__main__":
    _selftest()
