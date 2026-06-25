"""
World / cross-asset price layer for Vistas (Yahoo Finance — no API key).

Pulls a CURATED catalog of global instruments — major world equity indices,
commodities, FX, government-bond yields, credit/rate ETF proxies, volatility and
crypto — as a wide daily snapshot, so the terminal can chart NSE indices against
the S&P 500, gold, USD/INR, US 10Y, BTC, etc. Same wide-CSV shape and graceful
network degrade as stocks.py (never raises; a missing yfinance / offline machine
just keeps serving the snapshot).

Snapshot: data/World Data PX till <date>.csv  (wide: Date x YahooSymbol).
Symbols are RAW Yahoo tickers (^GSPC, GC=F, USDINR=X, ^TNX, BTC-USD) — NOT ".NS".
Each instrument's friendly name + asset group live in WORLD_CATALOG (static, in
code), so the picker can show "S&P 500" / "Gold" and search by name.

NOTE on kinds: equity indices, commodities, FX, bond-ETF proxies and crypto are
compounding LEVELS (drop straight into the performance analytics). Bond YIELDS
(^TNX etc.) are levels too but a "yield" reading — the Macro tab interprets them;
in the performance tab they chart as a level like everything else.
"""
from __future__ import annotations

import os
import re
import glob
import time
import random
import datetime as dt

import pandas as pd

try:
    import yfinance as yf
except Exception:                       # pragma: no cover
    yf = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))

# (Yahoo symbol, display name, asset group) — curated "top by relevance" set.
# A few tickers may not exist on Yahoo at pull-time; fetch_world skips misses, so
# over-including is safe. Groups drive the picker's grouping.
WORLD_CATALOG = [
    # --- Global equity indices: US ---
    ("^GSPC", "S&P 500 (US)", "Global equity"),
    ("^DJI", "Dow Jones Industrial (US)", "Global equity"),
    ("^IXIC", "Nasdaq Composite (US)", "Global equity"),
    ("^NDX", "Nasdaq 100 (US)", "Global equity"),
    ("^RUT", "Russell 2000 (US small-cap)", "Global equity"),
    ("^SP400", "S&P MidCap 400 (US)", "Global equity"),
    # --- Europe ---
    ("^FTSE", "FTSE 100 (UK)", "Global equity"),
    ("^GDAXI", "DAX (Germany)", "Global equity"),
    ("^FCHI", "CAC 40 (France)", "Global equity"),
    ("^STOXX50E", "Euro Stoxx 50", "Global equity"),
    ("^STOXX", "STOXX Europe 600", "Global equity"),
    ("^IBEX", "IBEX 35 (Spain)", "Global equity"),
    ("FTSEMIB.MI", "FTSE MIB (Italy)", "Global equity"),
    ("^AEX", "AEX (Netherlands)", "Global equity"),
    ("^SSMI", "SMI (Switzerland)", "Global equity"),
    ("^OMXS30", "OMX Stockholm 30 (Sweden)", "Global equity"),
    # --- Asia-Pacific ---
    ("^N225", "Nikkei 225 (Japan)", "Global equity"),
    ("^HSI", "Hang Seng (Hong Kong)", "Global equity"),
    ("000001.SS", "Shanghai Composite (China)", "Global equity"),
    ("399001.SZ", "Shenzhen Component (China)", "Global equity"),
    ("^STI", "Straits Times (Singapore)", "Global equity"),
    ("^KS11", "KOSPI (South Korea)", "Global equity"),
    ("^TWII", "TWSE (Taiwan)", "Global equity"),
    ("^AXJO", "ASX 200 (Australia)", "Global equity"),
    ("^JKSE", "Jakarta Composite (Indonesia)", "Global equity"),
    ("^KLSE", "FTSE Bursa Malaysia KLCI", "Global equity"),
    ("^NZ50", "NZX 50 (New Zealand)", "Global equity"),
    # --- Americas / EM / MEA ---
    ("^GSPTSE", "S&P/TSX (Canada)", "Global equity"),
    ("^BVSP", "Bovespa (Brazil)", "Global equity"),
    ("^MXX", "IPC (Mexico)", "Global equity"),
    ("^MERV", "Merval (Argentina)", "Global equity"),
    ("^TA125.TA", "TA-125 (Israel)", "Global equity"),
    # --- India market refs (Yahoo) — complement the NSE TR indices ---
    ("^NSEI", "Nifty 50 (price, Yahoo)", "Global equity"),
    ("^BSESN", "BSE Sensex (India)", "Global equity"),
    ("^INDIAVIX", "India VIX", "Volatility"),
    # --- Commodities (front-month futures) ---
    ("GC=F", "Gold", "Commodities"),
    ("SI=F", "Silver", "Commodities"),
    ("PL=F", "Platinum", "Commodities"),
    ("PA=F", "Palladium", "Commodities"),
    ("HG=F", "Copper", "Commodities"),
    ("CL=F", "Crude Oil (WTI)", "Commodities"),
    ("BZ=F", "Crude Oil (Brent)", "Commodities"),
    ("NG=F", "Natural Gas", "Commodities"),
    ("RB=F", "Gasoline (RBOB)", "Commodities"),
    ("HO=F", "Heating Oil", "Commodities"),
    ("ZC=F", "Corn", "Commodities"),
    ("ZW=F", "Wheat", "Commodities"),
    ("ZS=F", "Soybeans", "Commodities"),
    ("KC=F", "Coffee", "Commodities"),
    ("SB=F", "Sugar", "Commodities"),
    ("CC=F", "Cocoa", "Commodities"),
    ("CT=F", "Cotton", "Commodities"),
    ("LE=F", "Live Cattle", "Commodities"),
    # --- FX ---
    ("DX-Y.NYB", "US Dollar Index (DXY)", "FX"),
    ("USDINR=X", "USD / INR", "FX"),
    ("EURUSD=X", "EUR / USD", "FX"),
    ("GBPUSD=X", "GBP / USD", "FX"),
    ("USDJPY=X", "USD / JPY", "FX"),
    ("USDCNY=X", "USD / CNY", "FX"),
    ("AUDUSD=X", "AUD / USD", "FX"),
    ("USDCAD=X", "USD / CAD", "FX"),
    ("EURINR=X", "EUR / INR", "FX"),
    ("GBPINR=X", "GBP / INR", "FX"),
    ("JPYINR=X", "JPY / INR", "FX"),
    # --- Rates & bonds (yields + bond-ETF total-return proxies) ---
    ("^TNX", "US 10Y Treasury yield", "Rates & bonds"),
    ("^TYX", "US 30Y Treasury yield", "Rates & bonds"),
    ("^FVX", "US 5Y Treasury yield", "Rates & bonds"),
    ("^IRX", "US 13-week T-bill yield", "Rates & bonds"),
    ("TLT", "US 20+Y Treasury (TLT)", "Rates & bonds"),
    ("IEF", "US 7-10Y Treasury (IEF)", "Rates & bonds"),
    ("SHY", "US 1-3Y Treasury (SHY)", "Rates & bonds"),
    ("TIP", "US TIPS / inflation-linked (TIP)", "Rates & bonds"),
    ("AGG", "US Aggregate Bond (AGG)", "Rates & bonds"),
    ("LQD", "US Investment-Grade Credit (LQD)", "Credit"),
    ("HYG", "US High-Yield Credit (HYG)", "Credit"),
    ("EMB", "EM USD Sovereign Bond (EMB)", "Credit"),
    # --- Volatility ---
    ("^VIX", "CBOE Volatility Index (VIX)", "Volatility"),
    ("^VVIX", "VIX of VIX (VVIX)", "Volatility"),
    ("^OVX", "Crude Oil VIX (OVX)", "Volatility"),
    ("^GVZ", "Gold VIX (GVZ)", "Volatility"),
    # --- Crypto ---
    ("BTC-USD", "Bitcoin", "Crypto"),
    ("ETH-USD", "Ethereum", "Crypto"),
    ("BNB-USD", "BNB", "Crypto"),
    ("SOL-USD", "Solana", "Crypto"),
    ("XRP-USD", "XRP", "Crypto"),
]
WORLD_SYMS = [c[0] for c in WORLD_CATALOG]
WORLD_NAME = {c[0]: c[1] for c in WORLD_CATALOG}            # symbol -> friendly name
WORLD_GROUP = {c[0]: c[2] for c in WORLD_CATALOG}           # symbol -> asset group
SYM_BY_NAME = {c[1]: c[0] for c in WORLD_CATALOG}           # friendly name -> symbol
GROUP_BY_NAME = {c[1]: c[2] for c in WORLD_CATALOG}         # friendly name -> asset group


# ----------------------------------------------------------------------------- fetch
def fetch_world(symbols=None, start="2000-01-01", end=None, batch=12, progress=None) -> pd.DataFrame:
    """Wide DataFrame[Date x YahooSymbol] of adjusted daily close. Polite batches +
    jittered pauses. Missing symbols skipped. Empty frame on total failure (never raises)."""
    log = progress or (lambda m: None)
    if yf is None:
        log("yfinance not installed")
        return pd.DataFrame()
    symbols = symbols or WORLD_SYMS
    end = end or dt.date.today().isoformat()
    out = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        try:
            df = yf.download(chunk, start=start, end=end, auto_adjust=True,
                             progress=False, group_by="ticker", threads=False)
        except Exception as e:
            log(f"  batch {i}-{i+len(chunk)} failed: {e}")
            df = None
        if df is not None and len(df):
            for s in chunk:
                try:
                    col = df[s]["Close"] if len(chunk) > 1 else df["Close"]
                    col = pd.to_numeric(col, errors="coerce").dropna()
                    if len(col):
                        out[s] = col
                except Exception:
                    pass
        log(f"  [{min(i+batch, len(symbols))}/{len(symbols)}] fetched {len([s for s in chunk if s in out])}/{len(chunk)}")
        time.sleep(random.uniform(0.8, 1.8))
    if not out:
        return pd.DataFrame()
    wide = pd.DataFrame(out)
    wide.index = pd.DatetimeIndex(wide.index).normalize()
    wide = wide[~wide.index.duplicated(keep="last")].sort_index()
    wide.index.name = "Date"
    return wide


# ----------------------------------------------------------------------------- snapshot I/O
def latest_csv():
    cands = glob.glob(os.path.join(DATA_DIR, "World Data PX till *.csv"))
    if not cands:
        return None

    def _key(p):
        m = re.search(r"till (.+)\.csv$", os.path.basename(p))
        d = pd.to_datetime(m.group(1).strip(), errors="coerce") if m else None
        return d if pd.notna(d) else pd.Timestamp(os.path.getmtime(p), unit="s")

    return max(cands, key=_key)


def _write_dated(wide: pd.DataFrame) -> str:
    last = wide.index.max().date()
    out = os.path.join(DATA_DIR, f"World Data PX till {last.strftime('%b %#d, %Y')}.csv")
    wide.reset_index().to_csv(out, index=False)
    return out


def build_snapshot(symbols=None, start="2000-01-01", end=None, progress=None) -> dict:
    """Fetch the cross-asset catalog + merge into the World snapshot CSV (fresh-wins,
    so a partial pull never drops history). Never raises."""
    log = progress or (lambda m: print(m, flush=True))
    symbols = symbols or WORLD_SYMS
    log(f"[world] fetching {len(symbols)} cross-asset instruments {start}..{end or 'today'} (adjusted)…")
    wide = fetch_world(symbols, start=start, end=end, progress=log)
    if wide.empty:
        return {"ok": False, "error": "no world data fetched (yfinance unreachable?)"}
    existing = latest_csv()
    if existing:
        try:
            old = pd.read_csv(existing)
            old["Date"] = pd.to_datetime(old["Date"], errors="coerce")
            old = old.dropna(subset=["Date"]).set_index("Date").sort_index()
            wide = wide.combine_first(old).sort_index()
        except Exception:
            pass
    out = _write_dated(wide)
    return {"ok": True, "file": os.path.basename(out), "n_symbols": wide.shape[1],
            "n_days": wide.shape[0], "asof": wide.index.max().date().isoformat(),
            "start": wide.index.min().date().isoformat()}


def update_world(progress=None) -> dict:
    """Append last_date->today for the instruments already in the snapshot (small tail)."""
    csv = latest_csv()
    if csv is None:
        return {"ok": False, "error": "no world snapshot yet — run build_snapshot first"}
    old = pd.read_csv(csv)
    old["Date"] = pd.to_datetime(old["Date"], errors="coerce")
    old = old.dropna(subset=["Date"]).set_index("Date").sort_index()
    start = (old.index.max() + pd.Timedelta(days=1)).date().isoformat()
    return build_snapshot([c for c in old.columns], start=start, progress=progress)


# ----------------------------------------------------------------------------- serve
_CACHE = {"path": None, "df": None, "mtime": None}


def load() -> pd.DataFrame:
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


def load_named() -> pd.DataFrame:
    """The snapshot with columns renamed Yahoo-symbol -> friendly name (e.g. ^GSPC ->
    'S&P 500 (US)'), so charts/legends/picker all read cleanly. The friendly name is the
    canonical SERIES KEY everywhere downstream (catalog, deck embed, analytics)."""
    df = load()
    if not len(df):
        return df
    df = df.rename(columns={c: WORLD_NAME.get(c, c) for c in df.columns})
    return df.loc[:, ~df.columns.duplicated()]


def available() -> list:
    """Friendly names present in the snapshot."""
    df = load_named()
    return list(df.columns) if len(df) else []


def coverage() -> dict:
    """{friendly name: {start,end,n_obs}} for the picker."""
    df = load_named()
    out = {}
    for c in df.columns:
        s = df[c].dropna()
        if len(s):
            out[c] = {"start": s.index[0].strftime("%Y-%m-%d"),
                      "end": s.index[-1].strftime("%Y-%m-%d"), "n_obs": int(len(s))}
    return out


def names() -> dict:
    """{friendly name: Yahoo symbol} — used as the picker's secondary search key so you
    can also type the raw ticker (e.g. 'GSPC')."""
    return dict(SYM_BY_NAME)


if __name__ == "__main__":
    import json
    print(json.dumps(build_snapshot(progress=lambda m: print(m, flush=True)), indent=2))
