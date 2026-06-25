"""
NSE-bhavcopy single-stock price panel — the DECIMAL-ACCURATE replacement for the
yfinance stock source.

WHY THIS EXISTS
---------------
The live stock panel is yfinance auto-adjusted close. Yahoo's India *adjustment* is
imperfect to the decimal (e.g. TCS's first-year return came out ~+170% vs ~+60% from raw
prices — an internal-adjustment error no heuristic could fully cure). For a buy-side
terminal that must be "sure to the decimal," the only ground truth is the exchange's own
daily file: the **NSE bhavcopy**. This module reconstructs every stock's history from the
raw exchange close, so the number on screen is the number NSE published — to the paise.

WHAT IT DOES (pipeline, build-small-then-scale)
-----------------------------------------------
  1. fetch    — download one day's bhavcopy zip (UDiFF or legacy format), cache the
                VERBATIM exchange zip (fetch-once provenance). Resumable + polite.
  2. parse    — extract per-row {symbol, ISIN, series, close, name} (ISIN is the stable-ish
                key the legacy {symbol:close} cache threw away).
  3. assemble — pivot every cached day into a wide RAW-CLOSE panel keyed by **ISIN** (so a
                pure symbol rename is automatically one continuous column).
  4. lineage  — a security master: assign our own stable PERMID by union-find over the
                (symbol, ISIN) transitions the day-by-day timeline reveals. The hard
                both-flip case (merger/demerger) is FLAGGED, never silently merged.
  5. validate — reconcile vs the current panel to the paise on the latest dates (where
                adjusted == raw because no future corporate action has happened yet).

  [Phase 2, separate commit] corporate-action adjust (splits/bonuses both directions) +
  dividend reinvest -> full total-return level -> swap stocks.load() to read this panel.

The exchange archive is PUBLIC (no login) -> low account risk. Network is OPTIONAL and
graceful: every entry point returns a status dict / cached data and never raises, so an
offline machine just keeps serving whatever is already cached.

Provenance: the fetch URLs + zip parsing mirror the proven `clean_stocks.fetch_bhav`; this
module widens the capture to ISIN + security name and adds the bulk/resume/assemble layer.
Nothing here touches the TR *index* pipeline or the parity-checked analytics engine.
"""
from __future__ import annotations

import os
import io
import json
import time
import random
import zipfile
import datetime as dt

import numpy as np
import pandas as pd

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))
ZIP_CACHE = os.path.join(DATA_DIR, "_bhavzip")        # verbatim exchange zips (gitignored)
OUTPUT_DIR = os.path.abspath(os.path.join(HERE, "..", "output"))

# Equity board series we keep. EQ = the normal rolling-settlement board; BE/BZ/BT = the
# trade-to-trade / surveillance boards (still the same equity). We capture both and prefer
# EQ when a name appears on both on one day (it normally won't).
KEEP_SERIES = ("EQ", "BE", "BZ", "BT")

PAGE = "https://www.nseindia.com/all-reports"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ----------------------------------------------------------------------------- session
def nse_session():
    """A cookie-seeded NSE session (land on the site like a browser first), or None if
    requests is unavailable."""
    if requests is None:
        return None
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    try:
        s.get("https://www.nseindia.com", timeout=30)
        s.get(PAGE, timeout=30)
    except Exception:
        pass
    return s


def _bhav_urls(d: dt.date):
    """Both the new (UDiFF, 2024-01+) and legacy bhavcopy zip URLs for a date — try in order.
    The legacy archive reaches back to the early 2000s; the UDiFF covers the recent tail."""
    ymd = d.strftime("%Y%m%d")
    new = (f"https://nsearchives.nseindia.com/content/cm/"
           f"BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip")
    old = (f"https://nsearchives.nseindia.com/content/historical/EQUITIES/"
           f"{d.year}/{_MON[d.month - 1]}/cm{d.strftime('%d')}{_MON[d.month - 1]}{d.year}bhav.csv.zip")
    return [new, old]


# ----------------------------------------------------------------------------- fetch (cache zip)
def _zip_path(date_str: str) -> str:
    return os.path.join(ZIP_CACHE, f"{date_str}.zip")


def _none_path(date_str: str) -> str:
    return os.path.join(ZIP_CACHE, f"{date_str}.none")


def fetch_bhav_zip(date_str: str, session, log=lambda m: None, polite=True) -> str | None:
    """Ensure the bhavcopy zip for `date_str` is cached on disk; return its path (or None
    if NSE has no file for that date — holiday / pre-archive / unreachable). A `.none`
    marker is written for confirmed-empty dates so we never refetch a holiday. Resumable:
    a date already cached (zip or .none) returns immediately with no network call."""
    os.makedirs(ZIP_CACHE, exist_ok=True)
    zp, npth = _zip_path(date_str), _none_path(date_str)
    if os.path.exists(zp):
        return zp
    if os.path.exists(npth):
        return None
    if session is None:
        return None
    d = dt.date.fromisoformat(date_str)
    for url in _bhav_urls(d):
        try:
            r = session.get(url, timeout=45, headers={"Referer": PAGE})
            if r.status_code != 200 or r.content[:2] != b"PK":
                continue
            # validate it's a real zip we can open before committing it to cache
            zipfile.ZipFile(io.BytesIO(r.content)).namelist()
            with open(zp, "wb") as f:
                f.write(r.content)
            if polite:
                time.sleep(random.uniform(0.5, 1.2))
            return zp
        except Exception as e:
            log(f"    bhav {date_str} {url.split('/')[-1]}: {e}")
            continue
    # nothing available for this date -> mark so we don't try again
    try:
        open(npth, "w").close()
    except Exception:
        pass
    if polite:
        time.sleep(random.uniform(0.3, 0.7))
    return None


# ----------------------------------------------------------------------------- parse
def parse_bhav(zip_path: str) -> pd.DataFrame | None:
    """Parse a cached bhavcopy zip -> DataFrame[sym, series, isin, name, open, high, low,
    close, prevclose, volume, turnover, trades] for the equity boards (KEEP_SERIES). Handles
    both the new UDiFF and the legacy CSV layouts (the legacy year-2000 layout has neither
    ISIN nor a trade-count column -> those come back blank/NaN). Returns None if the file
    can't be read or carries no equity rows.

    Beyond `close` (the only field the price panel needs), this captures the full daily
    record every quant use wants: true OHLC (-> range-based volatility, candlesticks),
    traded volume + turnover (-> liquidity, VWAP = turnover/volume) and the trade count."""
    try:
        with open(zip_path, "rb") as f:
            content = f.read()
        zf = zipfile.ZipFile(io.BytesIO(content))
        name = zf.namelist()[0]
        df = pd.read_csv(zf.open(name))
    except Exception:
        return None
    cols = {c.strip().upper(): c for c in df.columns}

    def C(*names):                       # first matching column name (or None)
        for n in names:
            if n in cols:
                return cols[n]
        return None

    if "TCKRSYMB" in cols:                                  # new UDiFF (2024-01+)
        sym_c, cls_c, ser_c = cols["TCKRSYMB"], cols["CLSPRIC"], cols.get("SCTYSRS")
        isin_c, nm_c = cols.get("ISIN"), cols.get("FININSTRMNM")
        o_c, h_c, l_c = C("OPNPRIC"), C("HGHPRIC"), C("LWPRIC")
        pc_c, vol_c, val_c, trd_c = (C("PRVSCLSGPRIC"), C("TTLTRADGVOL"),
                                     C("TTLTRFVAL"), C("TTLNBOFTXSEXCTD"))
    elif "SYMBOL" in cols:                                  # legacy
        sym_c, cls_c, ser_c = cols["SYMBOL"], cols["CLOSE"], cols.get("SERIES")
        isin_c, nm_c = cols.get("ISIN"), None
        o_c, h_c, l_c = C("OPEN"), C("HIGH"), C("LOW")
        pc_c, vol_c, val_c, trd_c = (C("PREVCLOSE"), C("TOTTRDQTY"),
                                     C("TOTTRDVAL"), C("TOTALTRADES"))
    else:
        return None

    def num(c):
        return pd.to_numeric(df[c], errors="coerce") if c else np.nan

    out = pd.DataFrame({
        "sym": df[sym_c].astype(str).str.strip().str.upper(),
        "series": (df[ser_c].astype(str).str.strip().str.upper() if ser_c else "EQ"),
        "isin": (df[isin_c].astype(str).str.strip().str.upper() if isin_c else ""),
        "name": (df[nm_c].astype(str).str.strip() if nm_c else ""),
        "open": num(o_c), "high": num(h_c), "low": num(l_c),
        "close": num(cls_c), "prevclose": num(pc_c),
        "volume": num(vol_c), "turnover": num(val_c), "trades": num(trd_c),
    })
    out = out[out["series"].isin(KEEP_SERIES)]
    out = out[out["close"].notna() & (out["close"] > 0)]
    # some legacy surveillance rows print OPEN=HIGH=LOW=0 with only a valid close -> null those
    # OHLC cells (0 is "no auction print", not a real price) so range/VWAP analytics aren't
    # polluted; the close (already > 0) is kept.
    for c in ("open", "high", "low"):
        out.loc[out[c] <= 0, c] = np.nan
    # blank/invalid ISINs -> empty string (handled as "no exchange ID" downstream)
    out.loc[~out["isin"].str.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$", na=False), "isin"] = ""
    return out if len(out) else None


# ----------------------------------------------------------------------------- calendar
def nse_calendar(start=None, end=None):
    """The authoritative NSE trading days = the index TR frame's dates (after the 2000
    floor + the vendor-stale filter). We only ever fetch bhavcopy for days NSE actually
    traded — so no wasted calls on weekends/holidays. Returns a sorted DatetimeIndex."""
    from . import data
    idx = pd.DatetimeIndex(data.load(data.DEFAULT_MEASURE).index).sort_values()
    if start:
        idx = idx[idx >= pd.Timestamp(start)]
    if end:
        idx = idx[idx <= pd.Timestamp(end)]
    return idx


# ----------------------------------------------------------------------------- bulk fetch
def build_cache(start="2000-01-01", end=None, session=None, progress=None,
                calendar=None) -> dict:
    """Download + cache every NSE trading day's bhavcopy zip in [start, end]. Resumable
    (skips dates already cached), polite (jittered pauses), graceful (None on no network).
    Returns counts. This is the slow, fetch-once step — run it in the background."""
    log = progress or (lambda m: print(m, flush=True))
    cal = calendar if calendar is not None else nse_calendar(start, end)
    if cal is None or not len(cal):
        return {"ok": False, "error": "no NSE calendar (index TR frame missing?)"}
    dates = [d.strftime("%Y-%m-%d") for d in cal]
    todo = [d for d in dates if not os.path.exists(_zip_path(d))
            and not os.path.exists(_none_path(d))]
    log(f"[bhav] {len(dates)} NSE trading days in window; {len(dates)-len(todo)} already "
        f"cached, {len(todo)} to fetch.")
    if not todo:
        return {"ok": True, "n_days": len(dates), "fetched": 0, "cached": len(dates),
                "found": sum(1 for d in dates if os.path.exists(_zip_path(d)))}
    if session is None:
        session = nse_session()
    if session is None:
        return {"ok": False, "error": "no network (requests unavailable)",
                "n_days": len(dates)}
    fetched = found = 0
    for i, ds in enumerate(todo):
        zp = fetch_bhav_zip(ds, session, log)
        fetched += 1
        if zp:
            found += 1
        if (i + 1) % 25 == 0:
            log(f"  [{i+1}/{len(todo)}] fetched; {found} have data "
                f"(latest {ds})")
        if (i + 1) % 400 == 0:           # periodically re-seed cookies on long runs
            session = nse_session()
    n_have = sum(1 for d in dates if os.path.exists(_zip_path(d)))
    log(f"[bhav] done: {fetched} fetched this run, {n_have}/{len(dates)} days have data.")
    return {"ok": True, "n_days": len(dates), "fetched": fetched, "found_this_run": found,
            "days_with_data": n_have}


# ----------------------------------------------------------------------------- assemble
def _cached_dates(start=None, end=None) -> list:
    """Dates that have a cached zip on disk, within [start, end], sorted."""
    if not os.path.isdir(ZIP_CACHE):
        return []
    out = []
    for f in os.listdir(ZIP_CACHE):
        if not f.endswith(".zip"):
            continue
        d = f[:-4]
        if (start and d < str(start)[:10]) or (end and d > str(end)[:10]):
            continue
        out.append(d)
    return sorted(out)


def assemble_raw(start=None, end=None, progress=None) -> dict:
    """Pivot every cached bhavcopy day into a wide RAW-CLOSE panel keyed by ISIN, plus the
    metadata the security master needs. Returns:
      {ok, close: DataFrame[Date x ISIN], isin_meta: {isin: {symbols, name, first, last,
       n_obs}}, sym_isins: {sym: [isin,...]}, n_days}
    Keying by ISIN means a pure symbol *rename* is already one continuous column; symbol
    history per ISIN is retained so the lineage step can detect ISIN flips too."""
    log = progress or (lambda m: None)
    dates = _cached_dates(start, end)
    if not dates:
        return {"ok": False, "error": "no cached bhavcopy zips (run build_cache first)"}
    rows_close = {}                       # date -> {key: close}
    isin_syms = {}                        # key -> {sym: [first_date, last_date, n]}
    isin_name = {}                        # key -> latest security name
    sym_isins = {}                        # sym -> {key: n}
    n_surrogate = 0                       # rows with no exchange ISIN (legacy pre-ISIN era)
    for i, ds in enumerate(dates):
        df = parse_bhav(_zip_path(ds))
        if df is None:
            continue
        # one row per key preferring EQ; fall back to first board seen
        df = df.sort_values("series", key=lambda s: s.map({"EQ": 0}).fillna(1))
        day_close = {}
        for sym, isin, close, name in df[["sym", "isin", "close", "name"]].itertuples(index=False):
            # KEY = the ISIN when present, else a SYMBOL surrogate so pre-ISIN (legacy ~2000-06)
            # prices are NOT dropped — the lineage's "same symbol, time-disjoint keys" rule then
            # stitches "SYM:RELIANCE" (pre-ISIN) onto RELIANCE's real ISIN automatically.
            if isin:
                key = isin
            else:
                key = "SYM:" + sym; n_surrogate += 1
            if key not in day_close:               # EQ wins (sorted first)
                day_close[key] = close
            rec = isin_syms.setdefault(key, {}).setdefault(sym, [ds, ds, 0])
            rec[1] = ds; rec[2] += 1
            if name:
                isin_name[key] = name
            sym_isins.setdefault(sym, {})[key] = sym_isins.get(sym, {}).get(key, 0) + 1
        rows_close[ds] = day_close
        if progress and (i + 1) % 250 == 0:
            log(f"  assembled {i+1}/{len(dates)} days "
                f"({len(isin_syms)} keys seen)")
    close = pd.DataFrame.from_dict(rows_close, orient="index")
    close.index = pd.DatetimeIndex(close.index)
    close = close.sort_index()
    isin_meta = {}
    for isin, syms in isin_syms.items():
        s = close[isin].dropna() if isin in close.columns else pd.Series(dtype=float)
        isin_meta[isin] = {
            "symbols": {k: {"first": v[0], "last": v[1], "n": v[2]} for k, v in syms.items()},
            "latest_symbol": max(syms.items(), key=lambda kv: kv[1][1])[0],
            "name": isin_name.get(isin, ""),
            "first": s.index[0].strftime("%Y-%m-%d") if len(s) else None,
            "last": s.index[-1].strftime("%Y-%m-%d") if len(s) else None,
            "n_obs": int(len(s)),
        }
    return {"ok": True, "close": close, "isin_meta": isin_meta, "sym_isins": sym_isins,
            "n_surrogate": n_surrogate, "n_days": len(dates),
            "first": dates[0], "last": dates[-1]}


# ----------------------------------------------------------------------------- lineage (easy cases)
class _UF:
    """Tiny union-find for stitching (symbol, ISIN) nodes into one economic entity."""
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def build_lineage(asm: dict) -> dict:
    """Security master from the bhavcopy TIMELINE alone (the two easy transitions):
      * same ISIN, symbol changes  (pure rename)         -> link
      * same symbol, ISIN changes  (reorg keeping ticker)-> link, IF the two ISIN lives
        are time-disjoint (old one ends, new one begins) — a continuity check, so we don't
        merge two genuinely different companies that merely reused a ticker.
    Each linked group gets a stable PERMID. The HARD case (a merger/demerger where BOTH the
    symbol and ISIN change at once) is NOT auto-linked here — it needs NSE corporate-action /
    scheme notices and is FLAGGED for review ("no score for error").
    Returns {permid: {isins, symbols, latest_symbol, name, links:[...], flagged:bool}}."""
    isin_meta = asm["isin_meta"]
    sym_isins = asm["sym_isins"]
    uf = _UF()
    links = []
    for isin in isin_meta:                 # every ISIN is at least its own node
        uf.find(("isin", isin))
    # (a) same ISIN, multiple symbols -> rename: link them all to the ISIN node
    for isin, m in isin_meta.items():
        syms = list(m["symbols"].keys())
        if len(syms) > 1:
            links.append({"type": "rename", "isin": isin, "symbols": syms})
        for s in syms:
            uf.union(("isin", isin), ("isin", isin))   # canonical node = the ISIN
    # (b) same symbol, multiple ISINs -> reorg keeping the ticker (link if time-disjoint)
    flagged = []
    for sym, isins in sym_isins.items():
        if len(isins) < 2:
            continue
        # order the ISINs this symbol used by their last-seen date under this symbol
        spans = []
        for isin in isins:
            sm = isin_meta[isin]["symbols"].get(sym)
            if sm:
                spans.append((isin, sm["first"], sm["last"]))   # isin, first, last under sym
        spans.sort(key=lambda t: t[1])
        for a, b in zip(spans, spans[1:]):
            (ia, fa, la), (ib, fb, lb) = a, b
            disjoint = la < fb                          # a ends strictly before b starts
            if disjoint:
                uf.union(("isin", ia), ("isin", ib))
                links.append({"type": "isin_change", "symbol": sym,
                              "from_isin": ia, "to_isin": ib, "switch": fb})
            else:
                flagged.append({"type": "symbol_reused_overlapping", "symbol": sym,
                                "isins": [ia, ib]})
    # collapse to PERMIDs
    groups = {}
    for isin in isin_meta:
        root = uf.find(("isin", isin))
        groups.setdefault(root, []).append(isin)
    master = {}
    for n, (_root, isins) in enumerate(sorted(groups.items(), key=lambda kv: kv[1][0]), 1):
        permid = f"VST{n:05d}"
        all_syms, name, last_date = {}, "", ""
        for isin in isins:
            m = isin_meta[isin]
            for s, sm in m["symbols"].items():
                cur = all_syms.get(s)
                all_syms[s] = sm["last"] if (cur is None or sm["last"] > cur) else cur
            if m["last"] and m["last"] > last_date:
                last_date = m["last"]; name = m["name"] or name
        latest_symbol = max(all_syms.items(), key=lambda kv: kv[1])[0] if all_syms else ""
        master[permid] = {
            "isins": sorted(isins),
            "symbols": sorted(all_syms.keys()),
            "latest_symbol": latest_symbol,
            "name": name,
            "n_isins": len(isins),
        }
    return {"master": master, "links": links, "flagged": flagged,
            "n_permids": len(master), "n_multi": sum(1 for v in master.values()
                                                     if v["n_isins"] > 1)}


# ----------------------------------------------------------------------------- corporate actions
CA_CACHE = os.path.join(DATA_DIR, "_corpactions")        # raw NSE CA responses by year
CA_URL = ("https://www.nseindia.com/api/corporates-corporateActions?index=equities"
          "&from_date={f}&to_date={t}")
CA_REF = "https://www.nseindia.com/companies-listing/corporate-filings-actions"

import re as _re

_BONUS_RE = _re.compile(r"bonus\s+(\d+)\s*:\s*(\d+)", _re.I)
_SPLIT_RE = _re.compile(r"from\s+(?:rs\.?|re\.?)\s*([0-9]+(?:\.[0-9]+)?)\s*/?-?\s*"
                        r"(?:per\s+(?:equity\s+)?share\s+)?to\s+(?:rs\.?|re\.?)\s*"
                        r"([0-9]+(?:\.[0-9]+)?)", _re.I)
# cash dividend "Rs/Re/INR <amt> [/-] per [equity] share|sh|unit". The amount may carry a
# stray leading '-'/space ("Rs - 26") or a trailing "/-" ("Rs 6/- Per Share"); "Per Sh" is
# the old abbreviation of "Per Share". `share` is listed before `sh\b` so it wins.
_DIV_RS_RE = _re.compile(
    r"(?:rs|re|inr)\.?\s*-?\s*([0-9]+(?:\.[0-9]+)?)\s*/?-?\s*"
    r"per\s+(?:equity\s+)?(?:shares?|sh\b|unit)", _re.I)
# reversed order "<amt> Rs Per Share" (used in some older AGM/Dividend records)
_DIV_RS_REV_RE = _re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*(?:rs|re|inr)\.?\s*/?-?\s*"
    r"per\s+(?:equity\s+)?(?:shares?|sh\b|unit)", _re.I)
# percentage dividend ("Dividend - 25%" = 25% of face value) — only meaningful with face value
_DIV_PCT_RE = _re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")


def parse_ca_subject(subject: str, face_val=None) -> dict:
    """Turn an NSE corporate-action `subject` string into the price-relevant adjustment:
      {'kind', 'split_mult', 'dividend', 'raw'} where
        split_mult = shares AFTER / shares BEFORE  (price multiplies by 1/split_mult):
                     1:1 bonus -> 2;  1:3 bonus -> 4/3;  split Rs10->Re1 -> 10;
                     consolidation Re1->Rs10 -> 0.1.
        dividend   = cash Rs per (old) share.
    split_mult and dividend are extracted INDEPENDENTLY so a combined event (e.g.
    "AGM/Dividend ... Per Share", or a bonus declared with a dividend) captures both. NSE's
    feed is messy — amounts appear as "Rs 6/- Per Share", "Rs.4.80 Per Sh", "Rs - 26 Per
    Share" or as a percentage of face value ("Dividend - 25%") — all handled here.
    Rights / buy-back carry no simple multiplicative price adjustment -> flagged, not applied
    (returned with kind set but split_mult=1, dividend=0)."""
    s = (subject or "").strip()
    sl = s.lower()
    out = {"kind": "other", "split_mult": 1.0, "dividend": 0.0, "raw": s}
    # rights / buy-back: not a clean multiplicative adjustment; flag and stop (don't let a
    # premium "Rs 148" leak into the dividend parser).
    if "rights" in sl:
        out["kind"] = "rights"; return out
    if "buy back" in sl or "buyback" in sl:
        out["kind"] = "buyback"; return out
    mult = 1.0
    mb = _BONUS_RE.search(sl)
    if mb:
        a, b = int(mb.group(1)), int(mb.group(2))       # a new shares for every b held
        if b:
            mult *= (a + b) / b
    ms = _SPLIT_RE.search(sl) if ("split" in sl or "sub-division" in sl
                                  or "subdivision" in sl or "consolidation" in sl) else None
    if ms:
        old_fv, new_fv = float(ms.group(1)), float(ms.group(2))
        if new_fv > 0:
            mult *= old_fv / new_fv
    div = sum(float(x) for x in _DIV_RS_RE.findall(sl))
    if div == 0.0:                                       # reversed "<amt> Rs Per Share"
        div = sum(float(x) for x in _DIV_RS_REV_RE.findall(sl))
    if div == 0.0 and "dividend" in sl and face_val:    # percentage of face value
        try:
            fv = float(face_val)
            div = sum(float(x) / 100.0 * fv for x in _DIV_PCT_RE.findall(sl))
        except Exception:
            pass
    if mult != 1.0 and div > 0:
        out["kind"] = "both"
    elif mult != 1.0:
        out["kind"] = "bonus" if mb else "split"
    elif div > 0:
        out["kind"] = "dividend"
    elif ("demerger" in sl or "de-merger" in sl or "spin off" in sl or "spin-off" in sl
          or "composite scheme" in sl or ("scheme" in sl and "arrangement" in sl)):
        # a demerger / spin-off transfers value to a NEW entity: the parent price drops on
        # the ex-date but the holder receives shares of the spun-off company -> NOT a loss.
        # No simple multiplicative factor (we'd need the spun-off entity's value); the TR
        # builder NEUTRALISES the structural ex-date drop and flags it.
        out["kind"] = "demerger"
    out["split_mult"] = mult
    out["dividend"] = div
    return out


def fetch_corp_actions(start_year=2000, end_year=None, session=None, progress=None) -> dict:
    """Fetch + cache NSE corporate actions year-by-year (resumable; one JSON per year).
    Returns {ok, n_records, years:{year:n}}. The current year is always refetched (new CAs
    keep being announced); past years are cached once."""
    log = progress or (lambda m: print(m, flush=True))
    end_year = end_year or dt.date.today().year
    os.makedirs(CA_CACHE, exist_ok=True)
    if session is None:
        session = nse_session()
    cur_year = dt.date.today().year
    total, years = 0, {}
    for y in range(start_year, end_year + 1):
        cache = os.path.join(CA_CACHE, f"{y}.json")
        if os.path.exists(cache) and y < cur_year:
            try:
                with open(cache, encoding="utf-8") as f:
                    j = json.load(f)
                years[y] = len(j); total += len(j)
                continue
            except Exception:
                pass
        if session is None:
            continue
        url = CA_URL.format(f=f"01-01-{y}", t=f"31-12-{y}")
        try:
            r = session.get(url, timeout=60, headers={"Referer": CA_REF})
            j = r.json() if r.status_code == 200 else []
            if not isinstance(j, list):
                j = []
        except Exception as e:
            log(f"  CA {y}: {e}"); j = []
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(j, f)
        years[y] = len(j); total += len(j)
        log(f"  CA {y}: {len(j)} records")
        time.sleep(random.uniform(0.6, 1.3))
    return {"ok": True, "n_records": total, "years": years}


def load_corp_actions(progress=None) -> dict:
    """Read all cached CA years -> parsed events keyed by ISIN and by symbol:
      {'by_isin': {isin: [ev,...]}, 'by_sym': {sym: [ev,...]}}
    where ev = {exdate(Timestamp), kind, split_mult, dividend, isin, sym, raw}. Same-day
    same-security events are NOT pre-merged here (the TR builder combines them)."""
    by_isin, by_sym = {}, {}
    if not os.path.isdir(CA_CACHE):
        return {"by_isin": by_isin, "by_sym": by_sym, "n": 0}
    n = 0
    for f in sorted(os.listdir(CA_CACHE)):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(CA_CACHE, f), encoding="utf-8") as fh:
                recs = json.load(fh)
        except Exception:
            continue
        for rec in recs:
            ex = rec.get("exDate") or rec.get("exdate")
            ts = pd.to_datetime(ex, errors="coerce", dayfirst=True)
            if pd.isna(ts):
                continue
            p = parse_ca_subject(rec.get("subject", ""), rec.get("faceVal"))
            if (p["split_mult"] == 1.0 and p["dividend"] <= 0      # no price impact -> skip
                    and p["kind"] != "demerger"):                 # (keep demergers as markers)
                continue
            isin = str(rec.get("isin", "")).strip().upper()
            sym = str(rec.get("symbol", "")).strip().upper()
            ev = {"exdate": ts.normalize(), "kind": p["kind"], "split_mult": p["split_mult"],
                  "dividend": p["dividend"], "isin": isin, "sym": sym, "raw": p["raw"]}
            if isin:
                by_isin.setdefault(isin, []).append(ev)
            if sym:
                by_sym.setdefault(sym, []).append(ev)
            n += 1
    return {"by_isin": by_isin, "by_sym": by_sym, "n": n}


# ----------------------------------------------------------------------------- TR reconstruction
import math as _math

CONFIRM_LN = _math.log(1.35)      # split confirmed if |ln(observed_ratio * mult)| < this
DIV_YIELD_FLAG = 0.25             # dividend > 25% of price -> flag for audit
DIV_YIELD_SKIP = 0.60            # dividend > 60% of price -> almost surely a parse error, skip
JUMP_LN = _math.log(1.5)          # a "jump" worth a fallback look: > +50% or < -33%
REVERT_LN = _math.log(1.10)       # day i+1 ~ 1/(day i)  => a reverting 1-day bad tick
ROUND_RATIOS = [0.5, 1/3, 0.25, 0.2, 0.1, 2.0, 3.0, 4.0, 5.0, 10.0]   # price ratio of a split
ROUND_TOL = 0.04


def _infer_split_mult(obs: float):
    """If a 1-day price ratio sits on a round split/bonus/consolidation factor, return the
    implied share multiplier (mult = 1/price_ratio); else None. Used ONLY as a deep-history
    fallback when NO official corporate action explains a big round jump."""
    for r in ROUND_RATIOS:
        if abs(obs / r - 1) < ROUND_TOL:
            return 1.0 / r
    return None


def _permid_series(close: pd.DataFrame, isins: list) -> pd.Series:
    """Coalesce a PERMID's (time-disjoint) ISIN columns into one raw-close series."""
    cols = [i for i in isins if i in close.columns]
    if not cols:
        return pd.Series(dtype=float)
    s = close[cols[0]].copy()
    for c in cols[1:]:
        s = s.combine_first(close[c])
    return s.dropna()


def _permid_events(ca: dict, isins: list, symbols: list, sym_spans: dict) -> list:
    """Collect this PERMID's CA events. Primary key = ISIN (carried since ~2010). Fall back
    to SYMBOL for older ISIN-less records, but only within the date-span this PERMID used
    that symbol (so a reused ticker can't leak another company's actions). Deduped."""
    seen, evs = set(), []

    def _add(ev):
        k = (ev["exdate"], round(ev["split_mult"], 5), round(ev["dividend"], 5))
        if k not in seen:
            seen.add(k); evs.append(ev)

    for isin in isins:
        for ev in ca["by_isin"].get(isin, []):
            _add(ev)
    for sym in symbols:
        span = sym_spans.get(sym)
        for ev in ca["by_sym"].get(sym, []):
            if span and not (pd.Timestamp(span[0]) <= ev["exdate"] <= pd.Timestamp(span[1])
                             + pd.Timedelta(days=7)):
                continue
            _add(ev)
    return evs


def build_tr(asm: dict, ca: dict, lineage: dict, progress=None) -> dict:
    """Reconstruct a decimal-accurate TOTAL-RETURN level per economic entity (PERMID) from
    raw bhavcopy closes + official corporate actions, via CRSP-style daily-return chaining
    with split/bonus VERIFICATION against the observed price move.

    Returns {ok, tr, pr, audit, flags, meta} where
      tr / pr = wide DataFrame[Date x latest_symbol] of the total-return / price-return level
                (back-adjusted so the LATEST value == the raw exchange close, like a
                conventional adjusted close), and
      audit   = one row per applied/[]skipped corporate action (fully reproducible),
      flags   = events that contradicted the price move or look mis-parsed (review these)."""
    log = progress or (lambda m: None)
    close = asm["close"]
    master = lineage["master"]
    tr_cols, pr_cols, cfac_cols, audit, flags = {}, {}, {}, [], []
    # per-symbol observed span (first/last date this symbol carried data) for the span guard
    sym_spans = {}
    for m in asm["isin_meta"].values():
        for s, sm in m["symbols"].items():
            cur = sym_spans.get(s)
            lo = sm["first"] if not cur else min(cur[0], sm["first"])
            hi = sm["last"] if not cur else max(cur[1], sm["last"])
            sym_spans[s] = (lo, hi)

    for permid, info in master.items():
        s = _permid_series(close, info["isins"])
        if len(s) < 2:
            continue
        col = info["latest_symbol"]
        dates = s.index
        vals = s.to_numpy(dtype="float64")
        # bad-tick pre-pass: a 1-day spike that reverts the next day (r * r_next ~ 1) is an
        # erroneous / single-trade print, not a real move -> interpolate it out (geometric
        # mean of neighbours) before anything else, and remember the position so the split
        # fallback below never mistakes the revert day for a real split (the spike-vs-split
        # interaction bug). The official close rarely has these, so this is mostly a deep-
        # history guard; every interpolation is flagged.
        spike = set()
        for i in range(1, len(vals) - 1):
            a, b, c = vals[i - 1], vals[i], vals[i + 1]
            if a > 0 and b > 0 and c > 0:
                lr = _math.log(b / a)
                if abs(lr) > JUMP_LN and abs(lr + _math.log(c / b)) < REVERT_LN:
                    spike.add(i)
        for i in spike:
            vals[i] = _math.sqrt(vals[i - 1] * vals[i + 1])
            flags.append({"permid": permid, "symbol": col,
                          "date": dates[i].strftime("%Y-%m-%d"), "kind": "bad-tick",
                          "verdict": "interpolated (1-day spike that reverts)"})
        # snap each event to the first observed date >= exdate; accumulate mult & div there
        evs = _permid_events(ca, info["isins"], info["symbols"], sym_spans)
        day_mult = {}; day_div = {}; day_src = {}; day_demerg = set()
        for ev in evs:
            pos = dates.searchsorted(ev["exdate"], side="left")
            if pos <= 0 or pos >= len(dates):     # ex-date before first / after last obs
                continue
            d = dates[pos]
            if ev["split_mult"] != 1.0:
                day_mult[d] = day_mult.get(d, 1.0) * ev["split_mult"]
            if ev["dividend"] > 0:
                day_div[d] = day_div.get(d, 0.0) + ev["dividend"]
            if ev.get("kind") == "demerger":
                day_demerg.add(d)
            day_src.setdefault(d, []).append(ev["raw"])
        # chain daily returns
        r_tr = np.zeros(len(vals)); r_pr = np.zeros(len(vals))
        for i in range(1, len(vals)):
            d = dates[i]; p0, p1 = vals[i - 1], vals[i]
            if p0 <= 0 or p1 <= 0:
                r_tr[i] = r_pr[i] = np.nan; continue
            mult = day_mult.get(d, 1.0); div = day_div.get(d, 0.0)
            obs = p1 / p0
            # demerger / spin-off: the parent price drops as value moves to a new entity, but
            # the holder receives the spun-off shares -> no economic loss. Neutralise the
            # structural ex-date drop (return = 0) and flag; we don't have the spun-off
            # entity's value to model the upside, so the post-spin appreciation isn't tracked.
            if d in day_demerg and mult == 1.0 and obs < 0.92:
                r_pr[i] = r_tr[i] = 0.0
                flags.append({"permid": permid, "symbol": col,
                              "date": d.strftime("%Y-%m-%d"), "kind": "demerger",
                              "obs_ratio": round(obs, 4), "note": day_src.get(d),
                              "verdict": "NEUTRALISED-structural-drop (spin-off not tracked)"})
                audit.append({"permid": permid, "symbol": col,
                              "date": d.strftime("%Y-%m-%d"), "kind": "demerger",
                              "obs_ratio": round(obs, 4), "applied": True,
                              "verdict": "neutralised", "subject": "; ".join(day_src.get(d, []))})
                continue
            # verify a split/bonus against the actual move; skip (don't fabricate) if it
            # strongly contradicts the price, and record why.
            applied_mult = mult
            if mult != 1.0:
                if abs(_math.log(obs * mult)) < CONFIRM_LN:
                    verdict = "confirmed"
                else:
                    applied_mult = 1.0; verdict = "SKIPPED-contradicts-price"
                    flags.append({"permid": permid, "symbol": col, "date": d.strftime("%Y-%m-%d"),
                                  "kind": "split/bonus", "mult": round(mult, 4),
                                  "obs_ratio": round(obs, 4),
                                  "note": day_src.get(d), "verdict": verdict})
                audit.append({"permid": permid, "symbol": col, "date": d.strftime("%Y-%m-%d"),
                              "kind": "split/bonus", "mult": round(mult, 4),
                              "obs_ratio": round(obs, 4), "applied": applied_mult != 1.0,
                              "verdict": verdict, "subject": "; ".join(day_src.get(d, []))})
            # deep-history fallback: a big jump sitting exactly on a round split factor with NO
            # official CA to explain it is likely a split/bonus the (sparse, pre-~2008) CA feed
            # missed. Inferring is DANGEROUS — penny stocks oscillate on round ratios (Re1<->Rs2
            # = "2.0"/"0.5") and a false inferred split rescales the whole history. So gate it
            # HARD: non-penny price (>= Rs 5), NOT a bad-tick revert day, and PERSISTENT (a real
            # split is permanent; penny noise reverts within days — checked against the median
            # of the next ~10 closes). Only then apply, and always flag.
            elif (i - 1) not in spike and abs(_math.log(obs)) > JUMP_LN and p0 >= 5 and p1 >= 2:
                inf = _infer_split_mult(obs)
                look = vals[i:i + 11]; look = look[look > 0]
                med = float(np.median(look)) if len(look) else p1
                persistent = abs(_math.log(med / p1)) < abs(_math.log(med / p0))
                if inf and persistent:
                    applied_mult = inf
                    flags.append({"permid": permid, "symbol": col,
                                  "date": d.strftime("%Y-%m-%d"), "kind": "split/bonus",
                                  "mult": round(inf, 4), "obs_ratio": round(obs, 4),
                                  "verdict": "INFERRED-no-CA (persistent round-factor jump, no CA)"})
                    audit.append({"permid": permid, "symbol": col,
                                  "date": d.strftime("%Y-%m-%d"), "kind": "split/bonus",
                                  "mult": round(inf, 4), "obs_ratio": round(obs, 4),
                                  "applied": True, "verdict": "inferred-no-CA", "subject": ""})
            # dividend sanity
            applied_div = div
            if div > 0:
                yld = div / p0
                if yld > DIV_YIELD_SKIP:
                    applied_div = 0.0
                    flags.append({"permid": permid, "symbol": col, "date": d.strftime("%Y-%m-%d"),
                                  "kind": "dividend", "div": round(div, 3), "price": round(p0, 2),
                                  "yield": round(yld, 3), "note": day_src.get(d),
                                  "verdict": "SKIPPED-implausible-yield"})
                elif yld > DIV_YIELD_FLAG:
                    flags.append({"permid": permid, "symbol": col, "date": d.strftime("%Y-%m-%d"),
                                  "kind": "dividend", "div": round(div, 3), "price": round(p0, 2),
                                  "yield": round(yld, 3), "note": day_src.get(d),
                                  "verdict": "applied-high-yield"})
                if applied_div > 0:
                    audit.append({"permid": permid, "symbol": col, "date": d.strftime("%Y-%m-%d"),
                                  "kind": "dividend", "div": round(applied_div, 4),
                                  "price": round(p0, 2), "yield": round(applied_div / p0, 4),
                                  "applied": True, "verdict": "applied",
                                  "subject": "; ".join(day_src.get(d, []))})
            r_pr[i] = (p1 * applied_mult) / p0 - 1.0
            r_tr[i] = (p1 * applied_mult + applied_div) / p0 - 1.0
        # chain to a level, then back-adjust so the LAST value == the raw close (adjusted-close
        # convention: today's price is exact; history scaled). Scale is irrelevant to analytics.
        lvl_tr = np.cumprod(1.0 + np.nan_to_num(r_tr, nan=0.0))
        lvl_pr = np.cumprod(1.0 + np.nan_to_num(r_pr, nan=0.0))
        last_raw = vals[-1]
        tr_series = pd.Series(lvl_tr * (last_raw / lvl_tr[-1]), index=dates)
        pr_series = pd.Series(lvl_pr * (last_raw / lvl_pr[-1]), index=dates)
        tr_cols[col] = tr_series
        pr_cols[col] = pr_series
        # cumulative price-adjustment factor = adjusted(PR) / raw close. Multiply RAW OHLC by
        # this to get split/bonus-continuous OHLC; divide RAW volume by it (turnover is
        # unaffected). Anchored so cfac == 1 on the latest day (today's prices are the raw ones).
        with np.errstate(divide="ignore", invalid="ignore"):
            cfac_cols[col] = pr_series / pd.Series(vals, index=dates)

    tr = pd.DataFrame(tr_cols).sort_index()
    pr = pd.DataFrame(pr_cols).sort_index()
    cfac = pd.DataFrame(cfac_cols).sort_index()
    n_div = sum(1 for a in audit if a["kind"] == "dividend")
    n_split = sum(1 for a in audit if a["kind"] == "split/bonus" and a["applied"])
    log(f"[TR] {tr.shape[1]} entities; applied {n_split} split/bonus + {n_div} dividends; "
        f"{len(flags)} flagged for review.")
    return {"ok": True, "tr": tr, "pr": pr, "cfac": cfac, "audit": audit, "flags": flags,
            "meta": {"n_entities": tr.shape[1], "n_split_applied": n_split,
                     "n_div_applied": n_div, "n_flags": len(flags)}}


# ----------------------------------------------------------------------------- validate
def validate_recent(asm: dict = None, n_dates: int = 5, tol_paise: float = 0.10,
                    progress=None) -> dict:
    """Decimal reconciliation vs the CURRENT (yfinance) panel. On the LATEST trading dates
    there has been no *future* corporate action, so an adjusted close == the raw exchange
    close: the bhavcopy raw close must equal the current panel value to the paise. We check
    that for every overlapping symbol on the last `n_dates` days and report the match rate
    + the worst mismatches. (Older dates legitimately differ because the live panel was
    back-adjusted for CAs that postdate them — so we anchor on the most recent dates only.)"""
    log = progress or (lambda m: print(m, flush=True))
    if asm is None:
        asm = assemble_raw(progress=log)
    if not asm.get("ok"):
        return asm
    from . import stocks
    cur = stocks.load()
    if cur is None or not len(cur):
        return {"ok": False, "error": "current stock panel not loaded"}
    close, isin_meta = asm["close"], asm["isin_meta"]
    # build {latest_symbol: ISIN} for symbols that exist in BOTH panels
    sym2isin = {}
    for isin, m in isin_meta.items():
        sym2isin.setdefault(m["latest_symbol"], isin)
    dates = close.index.sort_values()[-n_dates:]
    rows, matched, checked, missing = [], 0, 0, []
    for sym in cur.columns:
        isin = sym2isin.get(sym)
        if isin is None or isin not in close.columns:
            missing.append(sym)
            continue
        for d in dates:
            if d not in close.index or d not in cur.index:
                continue
            bv = close.at[d, isin]; cv = cur.at[d, sym]
            if pd.isna(bv) or pd.isna(cv):
                continue
            checked += 1
            diff = abs(float(bv) - float(cv))
            ok = diff <= max(tol_paise, abs(float(cv)) * 1e-4)   # paise OR 1bp tolerance
            if ok:
                matched += 1
            else:
                rows.append({"sym": sym, "date": d.strftime("%Y-%m-%d"),
                             "bhav": round(float(bv), 2), "current": round(float(cv), 2),
                             "diff": round(diff, 2),
                             "pct": round(100 * diff / float(cv), 3) if cv else None})
    rows.sort(key=lambda r: -(r["pct"] or 0))
    rate = (matched / checked) if checked else 0.0
    log(f"[validate] {checked} (symbol,date) checks on the last {n_dates} days: "
        f"{matched} match to the paise ({rate:.1%}); {len(rows)} mismatch; "
        f"{len(missing)} current symbols not yet in the bhavcopy panel.")
    return {"ok": True, "checked": checked, "matched": matched, "match_rate": rate,
            "n_mismatch": len(rows), "worst": rows[:30], "n_missing": len(missing),
            "missing_sample": sorted(missing)[:30],
            "n_isins": len(isin_meta), "dates": [d.strftime("%Y-%m-%d") for d in dates]}


# ----------------------------------------------------------------------------- OHLCV capture
EXPORT_DIR = os.path.abspath(os.path.join(HERE, "..", "export", "parquet"))
OHLCV_DIR = os.path.join(EXPORT_DIR, "stocks_ohlcv")        # per-year raw OHLCV parquet parts


def build_ohlcv(start=None, end=None, progress=None) -> dict:
    """Stream every cached bhavcopy day into a long-form RAW OHLCV store (one parquet per
    year under export/parquet/stocks_ohlcv/), capturing the full daily record the price panel
    discards: open/high/low/close, prev-close, volume, turnover, trade-count and a daily VWAP
    (= turnover/volume). Keyed by date+symbol+ISIN (one row per symbol/day, EQ board preferred).
    Memory-safe (flushes per calendar year). RAW values — multiply by the adjustment factor
    (stock_adj_factor.parquet) for split/bonus-continuous series. This is the granular base
    for liquidity, breadth, range-volatility and candlestick analytics."""
    log = progress or (lambda m: print(m, flush=True))
    dates = _cached_dates(start, end)
    if not dates:
        return {"ok": False, "error": "no cached bhavcopy zips"}
    os.makedirs(OHLCV_DIR, exist_ok=True)
    keep = ["date", "sym", "series", "isin", "open", "high", "low", "close",
            "prevclose", "volume", "turnover", "trades", "vwap"]
    cur_year, buf, n_rows, n_parts = None, [], 0, 0

    def _flush(year):
        nonlocal n_rows, n_parts
        if not buf:
            return
        part = pd.concat(buf, ignore_index=True)
        part.to_parquet(os.path.join(OHLCV_DIR, f"{year}.parquet"), index=False)
        n_rows += len(part); n_parts += 1
        log(f"  wrote {year}.parquet ({len(part):,} rows)")
        buf.clear()

    for ds in dates:
        yr = ds[:4]
        if cur_year is None:
            cur_year = yr
        if yr != cur_year:
            _flush(cur_year); cur_year = yr
        df = parse_bhav(_zip_path(ds))
        if df is None:
            continue
        df = df.sort_values("series", key=lambda s: s.map({"EQ": 0}).fillna(1))
        df = df.drop_duplicates("sym", keep="first").copy()      # one row/symbol (EQ wins)
        df["date"] = pd.Timestamp(ds)
        with np.errstate(divide="ignore", invalid="ignore"):
            df["vwap"] = df["turnover"] / df["volume"].replace(0, np.nan)
        buf.append(df[keep])
    _flush(cur_year)
    log(f"[ohlcv] wrote {n_parts} year-parts, {n_rows:,} symbol-days -> "
        f"{os.path.relpath(OHLCV_DIR, os.path.dirname(HERE))}")
    return {"ok": True, "n_rows": n_rows, "n_parts": n_parts, "dir": OHLCV_DIR}


def load_ohlcv(adjusted: bool = False, columns=None) -> pd.DataFrame:
    """Load the long-form OHLCV store (all year-parts). With adjusted=True, multiply OHLC by
    the saved adjustment factor (and divide volume) for split/bonus-continuous series; turnover
    is left raw (value is unaffected by splits)."""
    import glob as _glob
    parts = sorted(_glob.glob(os.path.join(OHLCV_DIR, "*.parquet")))
    if not parts:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(p, columns=columns) for p in parts], ignore_index=True)
    if adjusted:
        fac_path = os.path.join(EXPORT_DIR, "stock_adj_factor.parquet")
        if os.path.exists(fac_path):
            fac = pd.read_parquet(fac_path)              # long: date, sym, cfac
            df = df.merge(fac, on=["date", "sym"], how="left")
            df["cfac"] = df["cfac"].fillna(1.0)
            for c in ("open", "high", "low", "close", "prevclose", "vwap"):
                if c in df.columns:
                    df[c] = df[c] * df["cfac"]
            if "volume" in df.columns:
                df["volume"] = df["volume"] / df["cfac"]
    return df


# ----------------------------------------------------------------------------- reconcile
def reconcile_vs_current(tr: pd.DataFrame, progress=None) -> dict:
    """Compare the reconstructed TR panel against the CURRENT (yfinance) panel over their
    overlap: latest-date level match to the paise, and daily-return tracking per symbol.
    Names that track tightly confirm the rebuild; large divergences are where the two
    sources disagree (often a yfinance adjustment error this rebuild fixes) — surfaced for
    review, never silently trusted either way."""
    log = progress or (lambda m: print(m, flush=True))
    from . import stocks
    cur = stocks.load()
    if cur is None or not len(cur):
        return {"ok": False, "error": "current panel unavailable"}
    common = [c for c in tr.columns if c in cur.columns]
    rows = []
    for sym in common:
        a = tr[sym].dropna(); b = cur[sym].dropna()
        idx = a.index.intersection(b.index)
        if len(idx) < 60:
            continue
        ra = a.reindex(idx).pct_change().dropna()
        rb = b.reindex(idx).pct_change().dropna()
        j = ra.index.intersection(rb.index)
        if len(j) < 60:
            continue
        corr = float(np.corrcoef(ra.reindex(j), rb.reindex(j))[0, 1])
        # cumulative-return gap over the common window (annualized)
        yrs = (j[-1] - j[0]).days / 365.25
        ta = float((1 + ra.reindex(j)).prod()); tb = float((1 + rb.reindex(j)).prod())
        cagr_a = ta ** (1 / yrs) - 1 if yrs > 0 else np.nan
        cagr_b = tb ** (1 / yrs) - 1 if yrs > 0 else np.nan
        rows.append({"sym": sym, "n": len(j), "corr": round(corr, 4),
                     "cagr_bhav": round(100 * cagr_a, 2), "cagr_yf": round(100 * cagr_b, 2),
                     "cagr_gap_pp": round(100 * (cagr_a - cagr_b), 2)})
    df = pd.DataFrame(rows)
    if not len(df):
        return {"ok": True, "n": 0, "note": "no overlap"}
    tight = int((df["corr"] >= 0.99).sum())
    df = df.sort_values("corr")
    log(f"[reconcile] {len(df)} symbols compared vs yfinance: {tight} track tightly "
        f"(return corr >= 0.99); median corr {df['corr'].median():.4f}.")
    return {"ok": True, "n": len(df),
            "tight_corr_ge_0_99": tight, "median_corr": float(df["corr"].median()),
            "worst_corr": df.head(20).to_dict("records"),
            "biggest_cagr_gap": df.reindex(df["cagr_gap_pp"].abs().sort_values(
                ascending=False).index).head(20).to_dict("records")}


# ----------------------------------------------------------------------------- orchestrate
def _align_nse(tr: pd.DataFrame, ffill_limit=5) -> pd.DataFrame:
    """Reindex onto the NSE trading calendar within the panel's span and carry the last
    print across short (<= ffill_limit) gaps; leading pre-listing NaNs stay blank."""
    if not len(tr):
        return tr
    cal = nse_calendar(tr.index.min(), tr.index.max())
    if cal is None or not len(cal):
        return tr
    return tr.reindex(cal).ffill(limit=ffill_limit)


# Conservative, first-principles panel hygiene (audited 2026-06-21). The reconstruction itself is
# decimal-correct (last value == raw exchange close to the paise; splits/bonuses produce SMOOTH
# total-return series on liquid names). The only contamination is in the long tail, so we remove
# exactly two clearly-wrong things and nothing else:
_TRANSIENT_RE = _re.compile(r"-(RE\d?|PP|RT|W|E1|E2)$")  # rights-entitlement / partly-paid / warrant lines
FRAGMENT_GAP = 250    # a NaN-run longer than this (~1 trading year) splits a series into disconnected blocks
FRAGMENT_BLIP = 60    # a disconnected block smaller than this, BESIDE a larger block, is a negligible fragment

def finalize_panel(tr: pd.DataFrame, progress=None):
    """Hygiene the reconstructed TR panel from first principles. The reconstruction is decimal-correct
    (last value == raw exchange close to the paise; splits/bonuses produce SMOOTH total-return series on
    liquid names), so we remove ONLY what is provably not a real continuous-equity series, and we never
    risk dropping live/real data:

      DROP transient NON-EQUITY instruments — rights entitlements (``-RE``/``-RE1..3``), partly-paid
      shares (``-PP``), warrants (``-W``), rights (``-RT``) and special series (``-E1``/``-E2``). These
      trade for a few days during a corporate action; they are not common-equity total return and only
      pollute the picker / breadth / distribution stats.

    Disconnected-history names (a symbol whose trades split into >1 block more than ``FRAGMENT_GAP`` rows
    apart — e.g. NIRLON: actively traded 2000-02 as a sub-Re1 penny, dormant ~23y, relisted ~2025 near
    Rs600) are NOT trimmed. Trimming them is unsafe: "keep the largest block" would keep the dead early
    penny fragment and DROP the live block (breaking the last==raw-close anchor), while "keep the latest"
    would discard a long real early history for a tiny recent blip. There is no generic rule that is
    always right, so we leave every real block intact and let the render-time COVERAGE GATE in ``data.py``
    (``MAX_INTERNAL_GAP_ROWS``) do its job: it drops a name on any window that spans the gap (no fabricated
    cross-gap analytics) and renders it correctly on any window that falls inside one block. We only REPORT
    these names so they are visible for audit.

    Returns ``(clean_tr, report)``."""
    log = progress or (lambda m: None)
    cols = list(tr.columns)
    transient = [c for c in cols if _TRANSIENT_RE.search(c)]
    keep = [c for c in cols if c not in set(transient)]
    out = tr[keep].copy()
    fragmented = []                                       # informational only — NOT modified
    for c in keep:
        m = out[c].notna().values
        idx = np.flatnonzero(m)
        if idx.size == 0:
            continue
        fi, li = idx[0], idx[-1]
        blocks, cc, grun = [], 0, 0                       # block sizes split at NaN-run > FRAGMENT_GAP
        for j in range(fi, li + 1):
            if m[j]:
                cc += 1; grun = 0
            else:
                grun += 1
                if grun > FRAGMENT_GAP and cc > 0:
                    blocks.append(cc); cc = 0
        if cc > 0:
            blocks.append(cc)
        if len(blocks) > 1:
            fragmented.append((c, int(m.sum()), len(blocks), int(max(blocks))))
    log(f"[finalize] dropped {len(transient)} transient non-equity cols; "
        f"flagged {len(fragmented)} disconnected-history names (kept intact; coverage gate protects); "
        f"universe {len(cols)} -> {out.shape[1]}")
    report = {"transient_dropped": transient, "fragmented_flagged": fragmented,
              "n_in": len(cols), "n_out": int(out.shape[1])}
    return out, report


def build_panel(start="2000-01-01", end=None, promote=False, align_nse=True,
                progress=None) -> dict:
    """Full reconstruction: assemble raw closes -> security master (PERMID) -> apply official
    corporate actions (verified) -> total-return level -> NSE-align -> write the panel CSV +
    the security master + per-event audit + flags + a reconciliation report. With promote=True
    the panel is written under the name `stocks.load()` prefers, so the whole app switches to
    the bhavcopy source on the next load (the yfinance CSV is kept as a fallback)."""
    log = progress or (lambda m: print(m, flush=True))
    log("[build] assembling raw closes by ISIN…")
    asm = assemble_raw(start, end, progress=log)
    if not asm.get("ok"):
        return asm
    log(f"  {len(asm['isin_meta'])} ISINs over {asm['n_days']} days "
        f"({asm['first']}..{asm['last']})")
    log("[build] building the security master (PERMID lineage)…")
    lin = build_lineage(asm)
    log(f"  {lin['n_permids']} entities; {lin['n_multi']} span multiple ISINs; "
        f"{len(lin['flagged'])} lineage transitions flagged for review")
    log("[build] loading corporate actions…")
    ca = load_corp_actions(progress=log)
    log(f"  {ca['n']} price-relevant CA events")
    log("[build] reconstructing total-return levels…")
    res = build_tr(asm, ca, lin, progress=log)
    tr = res["tr"]
    if align_nse:
        tr = _align_nse(tr)
        log(f"  NSE-aligned -> {tr.shape[0]} trading days x {tr.shape[1]} stocks")
    # first-principles hygiene: drop non-equity transients + trim negligible disconnected fragments
    tr, clean_rep = finalize_panel(tr, progress=log)
    # write artifacts
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    last = tr.index.max().date()
    panel_name = f"Stocks Data TR till {last.strftime('%b %#d, %Y')}.csv"
    panel_path = os.path.join(DATA_DIR, panel_name)
    out_path = panel_path if promote else os.path.join(OUTPUT_DIR, panel_name)
    tr.reset_index().rename(columns={"index": "Date"}).to_csv(out_path, index=False)
    log(f"[build] wrote panel -> {os.path.relpath(out_path, os.path.dirname(HERE))}")
    # security master
    with open(os.path.join(OUTPUT_DIR, "stock_security_master.json"), "w", encoding="utf-8") as f:
        json.dump({"master": lin["master"], "links": lin["links"],
                   "flagged": lin["flagged"]}, f, indent=1)
    # audit + flags
    pd.DataFrame(res["audit"]).to_csv(
        os.path.join(OUTPUT_DIR, "bhav_corp_action_audit.csv"), index=False)
    pd.DataFrame(res["flags"]).to_csv(
        os.path.join(OUTPUT_DIR, "bhav_ca_flags.csv"), index=False)
    # cleaning report: what finalize_panel dropped (transient) + flagged (disconnected history, kept intact)
    pd.DataFrame([{"sym": s, "action": "drop_transient"} for s in clean_rep["transient_dropped"]]
                 + [{"sym": s, "action": "flag_disconnected", "obs": o, "n_blocks": n, "largest_block": lb}
                    for (s, o, n, lb) in clean_rep["fragmented_flagged"]]
                 ).to_csv(os.path.join(OUTPUT_DIR, "bhav_clean_report.csv"), index=False)
    # OHLCV granular capture + the adjustment factor (so OHLC/volume can be made
    # split/bonus-continuous on demand). Same single processing run — no re-fetch.
    os.makedirs(EXPORT_DIR, exist_ok=True)
    fac = res.get("cfac")
    if fac is not None and len(fac):
        fac_long = (fac.reset_index().rename(columns={"index": "date"})
                    .melt(id_vars="date", var_name="sym", value_name="cfac").dropna())
        fac_long.to_parquet(os.path.join(EXPORT_DIR, "stock_adj_factor.parquet"), index=False)
        log(f"[build] wrote adjustment factor ({len(fac_long):,} rows)")
    log("[build] capturing granular OHLCV (open/high/low/close, volume, turnover, trades)…")
    ohlcv = build_ohlcv(start, end, progress=log)
    log("[build] reconciling vs the current panel…")
    rec = reconcile_vs_current(tr, progress=log)
    return {"ok": True, "panel": out_path, "promoted": promote,
            "n_stocks": tr.shape[1], "n_days": tr.shape[0],
            "first": tr.index.min().strftime("%Y-%m-%d"),
            "last": tr.index.max().strftime("%Y-%m-%d"),
            "lineage": {"n_permids": lin["n_permids"], "n_multi": lin["n_multi"],
                        "n_flagged": len(lin["flagged"])},
            "ca": res["meta"], "ohlcv": ohlcv, "reconcile": rec}


# ----------------------------------------------------------------------------- CLI
if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    P = lambda m: print(m, flush=True)
    start = next((a for a in args if a[:4].isdigit()), "2000-01-01")
    if "--probe" in args:
        s = nse_session()
        cal = nse_calendar()
        recent = [d.strftime("%Y-%m-%d") for d in cal[-5:]]
        print("probing", recent)
        for ds in recent:
            zp = fetch_bhav_zip(ds, s)
            df = parse_bhav(zp) if zp else None
            print(f"  {ds}: {'OK' if zp else 'no-file'}; "
                  f"{0 if df is None else len(df)} equity rows; "
                  f"sample ISIN present={bool(df is not None and (df['isin']!='').any())}")
    elif "--ca" in args:
        print(json.dumps(fetch_corp_actions(start_year=int(start[:4]), progress=P)["years"]))
    elif "--build" in args:
        r = build_panel(start=start, promote=("--promote" in args), progress=P)
        print(json.dumps({k: r[k] for k in ("ok", "panel", "promoted", "n_stocks",
              "n_days", "first", "last", "lineage", "ca") if k in r}, indent=2, default=str))
    else:                                    # default: fetch the price zips
        build_cache(start=start, progress=P)
