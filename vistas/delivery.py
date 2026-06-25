"""
NSE DELIVERY-% layer for Vistas — the "smart-money conviction" plane.

WHY THIS EXISTS
---------------
Every trade on the exchange is a buy matched to a sell, but only a fraction of the day's
traded shares are actually *taken into demat* (delivered) — the rest are intraday round
trips that net to zero by the close. The delivered fraction,

    delivery%  =  100 * (shares actually delivered) / (total shares traded),

is the share of volume backed by someone willing to hold the stock overnight. A high and
rising delivery% on rising volume is the classic "smart-money accumulation" footprint;
a price spike on heavy volume but *low* delivery% is mostly speculative churn. This module
fetches the exchange's own daily delivery file so that footprint is available to the
terminal, decimal-exact, alongside price.

TWO OFFICIAL SOURCES (NSE public archives, no login -> low account risk)
------------------------------------------------------------------------
  * RECENT (2020-01+) — the "full" security-wise bhav file, which already carries delivery:
        GET https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_<DDMMYYYY>.csv
    A plain CSV. GOTCHA: every header AND every value has a LEADING SPACE
    (" SERIES", " DELIV_PER", " EQ", " 56.64") — we str.strip() the headers and every
    object column. Missing/illiquid cells print as '-' -> coerced to NaN.
    Columns (after strip):
        SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE,
        CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY,
        DELIV_PER
    (TURNOVER_LACS is rupees-in-lakh; 1 lakh = 1e5.)

  * DEEP (2011-02+) — the legacy "MTO" (Market-to-Order? "marked to delivery") fixed file:
        GET https://nsearchives.nseindia.com/archives/equities/mto/MTO_<DDMMYYYY>.DAT
    A comma file with a 4-line preamble to SKIP. After the preamble each data row is
        RecordType , SrNo , Symbol , Series , QtyTraded , DelivQty , Deliv%
    (record type 20 = a security row). It carries ONLY quantities + delivery% — no price,
    no turnover, no trade count — but it reaches back ~9 years before the full file does,
    so it is the historical backfill source.

WHAT THE LEAD GETS
------------------
  * fetch_sample()  -> a tidy LONG DataFrame (date, sym, series, deliv_pct, deliv_qty,
                       ttl_qty, avg_price, trades) proving both sources parse — a few recent
                       days via sec_bhavdata_full + one old day via MTO.
  * fetch_series()  -> the uniform Vistas contract: a market-level daily series, the MEDIAN
                       delivery% across liquid names each day (a breadth gauge of conviction),
                       plus per-name delivery% series for a few bellwether stocks, with meta.

GRACEFUL DEGRADE: network down / holiday / 404 never raises — we return whatever parsed.
Polite: a small jittered sleep between calls; we fetch only a handful of days here (the
lead runs the full, paced backfill later). Re-uses bhav_prices.nse_session() for the
cookie-seeded session.

Provenance: written for Vistas 2026-06-21. Standalone — imports only bhav_prices' session
helper; touches no shared/parity-checked file.
"""
from __future__ import annotations

import io
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

from vistas import bhav_prices as bp

# ----------------------------------------------------------------------------- constants
FULL_URL = ("https://nsearchives.nseindia.com/products/content/"
            "sec_bhavdata_full_{ddmmyyyy}.csv")          # recent (2020+)
MTO_URL = ("https://nsearchives.nseindia.com/archives/equities/mto/"
           "MTO_{ddmmyyyy}.DAT")                          # deep (2011+)
REFERER = "https://www.nseindia.com/all-reports"

# Bellwether large-caps we expose as standalone per-name delivery% series (sanity-auditable;
# liquid enough that delivery% is meaningful every day).
BELLWETHERS = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]

# A name is "liquid enough" for the market-level median if it traded a minimum number of
# shares that day — so the breadth gauge isn't dragged around by thinly-traded penny names
# whose delivery% is statistical noise.
MIN_QTY_FOR_MEDIAN = 50_000


# ----------------------------------------------------------------------------- helpers
def _ddmmyyyy(d: dt.date) -> str:
    return d.strftime("%d%m%Y")


def _polite(lo: float = 0.5, hi: float = 1.2) -> None:
    time.sleep(random.uniform(lo, hi))


def _recent_trading_days(n: int, end: dt.date | None = None) -> list[dt.date]:
    """The last `n` weekday dates on/before `end` (today by default). Weekends are skipped;
    actual exchange holidays simply 404 and are dropped downstream — cheap and robust without
    needing a holiday calendar here."""
    end = end or dt.date.today()
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:                # Mon..Fri
            out.append(d)
        d -= dt.timedelta(days=1)
    return out


# ----------------------------------------------------------------------------- parse: full CSV
def parse_full_csv(text: str, date: dt.date) -> pd.DataFrame | None:
    """Parse one sec_bhavdata_full CSV (recent source) -> tidy long EQ rows. Strips the
    leading-space headers AND values, coerces '-' to NaN, keeps SERIES==EQ. Returns None on
    an unparseable/empty body."""
    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception:
        return None
    if df is None or not len(df):
        return None
    df.columns = [str(c).strip() for c in df.columns]
    if "SYMBOL" not in df.columns or "DELIV_PER" not in df.columns:
        return None
    for c in df.columns:                    # strip every string cell's leading space
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()
    # STALE-FILE GUARD (critical). For a non-trading date (weekend/holiday) NSE answers
    # HTTP 200 with the PREVIOUS trading day's file, so the requested date alone cannot be
    # trusted as the row date — without this check a holiday silently mislabels stale rows.
    # DATE1 (identical on every row) is the file's OWN trading date; require it == requested.
    if "DATE1" not in df.columns or not len(df):
        return None
    fdate = pd.to_datetime(str(df["DATE1"].iloc[0]), dayfirst=True, errors="coerce")
    if pd.isna(fdate) or fdate.date() != pd.Timestamp(date).date():
        return None
    df = df[df.get("SERIES", "").astype(str).str.upper() == "EQ"].copy()
    if not len(df):
        return None

    def num(col):
        # '-' / '' / non-numeric -> NaN
        return pd.to_numeric(df[col].replace({"-": np.nan, "": np.nan}), errors="coerce")

    out = pd.DataFrame({
        "date": pd.Timestamp(date),
        "sym": df["SYMBOL"].astype(str).str.strip().str.upper(),
        "series": "EQ",
        "deliv_pct": num("DELIV_PER"),
        "deliv_qty": num("DELIV_QTY"),
        "ttl_qty": num("TTL_TRD_QNTY"),
        "avg_price": num("AVG_PRICE"),
        "trades": num("NO_OF_TRADES"),
    })
    return out if len(out) else None


# ----------------------------------------------------------------------------- parse: MTO .DAT
def parse_mto_dat(text: str, date: dt.date) -> pd.DataFrame | None:
    """Parse one MTO .DAT file (deep source) -> tidy long EQ rows. Skips the 4-line preamble;
    each data row is RecordType,SrNo,Symbol,Series,QtyTraded,DelivQty,Deliv% (record type 20).
    Carries only quantities + delivery% (no price/turnover/trade-count -> those come back
    NaN). Returns None on an unparseable/empty body."""
    lines = text.splitlines()
    if len(lines) <= 4:
        return None
    # STALE-FILE GUARD (best-effort for MTO). The .DAT data rows carry no date, but the
    # 4-line preamble names the file's trading date. If a date token is confidently parsed
    # there and disagrees with the requested date, treat as no-data (a stale holiday file).
    # If none parses, fall through: MTO archive paths 404 on holidays (stale-serve unlikely),
    # and the richer full-CSV source carries the hard DATE1 guard above.
    head = " ".join(lines[:4])
    for _pat in (r"\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{4}", r"\d{1,2}[-/]\d{1,2}[-/]\d{4}"):
        _m = re.search(_pat, head)
        if _m:
            _ts = pd.to_datetime(_m.group(0), dayfirst=True, errors="coerce")
            if not pd.isna(_ts):
                if _ts.date() != pd.Timestamp(date).date():
                    return None
                break
    recs = []
    for ln in lines[4:]:                    # skip the 4-line header preamble
        ln = ln.strip()
        if not ln:
            continue
        parts = ln.split(",")
        if len(parts) != 7:                 # malformed / wrapped line -> skip
            continue
        rtype, _srno, sym, ser, qty, dqty, dpct = (p.strip() for p in parts)
        if rtype != "20" or ser.upper() != "EQ":
            continue

        def _f(x):
            try:
                return float(x)
            except Exception:
                return np.nan

        recs.append((sym.upper(), _f(qty), _f(dqty), _f(dpct)))
    if not recs:
        return None
    arr = pd.DataFrame(recs, columns=["sym", "ttl_qty", "deliv_qty", "deliv_pct"])
    out = pd.DataFrame({
        "date": pd.Timestamp(date),
        "sym": arr["sym"],
        "series": "EQ",
        "deliv_pct": arr["deliv_pct"],
        "deliv_qty": arr["deliv_qty"],
        "ttl_qty": arr["ttl_qty"],
        "avg_price": np.nan,                # MTO has no price
        "trades": np.nan,                   # nor trade count
    })
    return out if len(out) else None


# ----------------------------------------------------------------------------- fetch one day
def fetch_day(date: dt.date, session=None, source: str = "auto", log=print,
              polite: bool = True) -> pd.DataFrame | None:
    """Fetch + parse ONE trading day's delivery data, graceful on holiday/404/network error.

    source: "full" (recent CSV), "mto" (deep .DAT), or "auto" (full first — richer fields —
    then MTO as fallback). Returns a tidy long DataFrame or None.
    """
    if session is None:
        session = bp.nse_session()
    if session is None:
        return None
    ddmmyyyy = _ddmmyyyy(date)
    order = (["full", "mto"] if source == "auto"
             else ["full"] if source == "full" else ["mto"])
    for src in order:
        url = (FULL_URL if src == "full" else MTO_URL).format(ddmmyyyy=ddmmyyyy)
        try:
            r = session.get(url, timeout=45, headers={"Referer": REFERER})
        except Exception as e:
            log(f"    deliv {date.isoformat()} {src}: {e}")
            continue
        if r.status_code != 200 or len(r.content) < 200:
            continue
        df = (parse_full_csv(r.text, date) if src == "full"
              else parse_mto_dat(r.text, date))
        if polite:
            _polite()
        if df is not None and len(df):
            return df
    return None


# ----------------------------------------------------------------------------- market-level series
def _market_level(day: pd.DataFrame) -> float:
    """Market breadth-of-conviction = MEDIAN delivery% across liquid names that day
    (names with >= MIN_QTY_FOR_MEDIAN shares traded and a valid delivery% in [0,100])."""
    d = day[(day["ttl_qty"].fillna(0) >= MIN_QTY_FOR_MEDIAN)
            & day["deliv_pct"].between(0, 100)]
    if not len(d):
        d = day[day["deliv_pct"].between(0, 100)]
    return float(d["deliv_pct"].median()) if len(d) else np.nan


# ----------------------------------------------------------------------------- public: sample
def fetch_sample(n_recent: int = 4, old_date: dt.date | None = None, log=print) -> pd.DataFrame:
    """Prove both sources parse with a LOW call count: ~`n_recent` recent days via the full
    CSV + ONE old day via MTO. Returns a tidy long DataFrame
    (date, sym, series, deliv_pct, deliv_qty, ttl_qty, avg_price, trades) for EQ names.
    Graceful: holidays/404s are silently skipped; returns an empty frame if nothing fetched."""
    session = bp.nse_session()
    frames = []
    # recent days (full CSV) — keep trying back-dated weekdays until n_recent succeed or we
    # exhaust a small budget (covers a run that lands on a holiday/long weekend).
    got = 0
    for d in _recent_trading_days(n_recent + 6):
        if got >= n_recent:
            break
        df = fetch_day(d, session, source="full", log=log)
        if df is not None:
            frames.append(df)
            got += 1
            log(f"  [full] {d.isoformat()}: {len(df)} EQ names "
                f"(median deliv% {_market_level(df):.1f})")
    # one old day (MTO) — proves the deep source
    old = old_date or dt.date(2016, 6, 1)
    dfo = fetch_day(old, session, source="mto", log=log)
    if dfo is not None:
        frames.append(dfo)
        log(f"  [mto ] {old.isoformat()}: {len(dfo)} EQ names "
            f"(median deliv% {_market_level(dfo):.1f})")
    if not frames:
        return pd.DataFrame(columns=["date", "sym", "series", "deliv_pct", "deliv_qty",
                                     "ttl_qty", "avg_price", "trades"])
    return pd.concat(frames, ignore_index=True).sort_values(["date", "sym"]).reset_index(drop=True)


# ----------------------------------------------------------------------------- public: series
def fetch_series(start: str = "2000-01-01", log=print):
    """Uniform Vistas contract -> (series_by_name, meta_by_name).

    Builds, from a SMALL set of recent trading days (proof, not a backfill — the lead runs the
    paced multi-year pull later), daily delivery% series:
      * "Market delivery% (median, liquid names)" — the breadth-of-conviction gauge.
      * one per bellwether large-cap, e.g. "Delivery% — RELIANCE".
    `start` is honored only as a lower bound; the deep MTO history reaches ~2011 and the full
    CSV ~2020, but here we sample only recent days to keep account risk near zero.

    Each value is a float delivery% in [0,100]; index is pandas Timestamp (daily, NSE
    trading days). Returns empty dicts (never raises) if nothing could be fetched.
    """
    session = bp.nse_session()
    # sample a short recent window (a couple of trading weeks is plenty to prove the series
    # and let the lead see real movement); the full backfill is the lead's paced job.
    days = _recent_trading_days(10)
    days = [d for d in days if d >= dt.date.fromisoformat(str(start)[:10])]
    market_pts, name_pts = {}, {name: {} for name in BELLWETHERS}
    n_days = 0
    for d in sorted(days):
        df = fetch_day(d, session, source="full", log=log)
        if df is None:
            continue
        n_days += 1
        ts = pd.Timestamp(d)
        market_pts[ts] = _market_level(df)
        by_sym = df.set_index("sym")["deliv_pct"]
        for name in BELLWETHERS:
            if name in by_sym.index:
                v = float(by_sym.loc[name])
                if 0 <= v <= 100:
                    name_pts[name][ts] = v
    log(f"[delivery] built {n_days} day(s) of delivery% series.")

    series_by_name: dict[str, pd.Series] = {}
    meta_by_name: dict[str, dict] = {}

    if market_pts:
        s = pd.Series(market_pts).sort_index().astype(float)
        nm = "Market delivery% (median, liquid names)"
        series_by_name[nm] = s
        meta_by_name[nm] = {"unit": "%", "freq": "daily", "source": "NSE sec_bhavdata_full",
                            "group": "Market internals — delivery"}
    for name in BELLWETHERS:
        pts = name_pts[name]
        if not pts:
            continue
        s = pd.Series(pts).sort_index().astype(float)
        nm = f"Delivery% — {name}"
        series_by_name[nm] = s
        meta_by_name[nm] = {"unit": "%", "freq": "daily",
                            "source": "NSE sec_bhavdata_full",
                            "group": "Market internals — delivery"}
    return series_by_name, meta_by_name


# ----------------------------------------------------------------------------- self-test
if __name__ == "__main__":
    print("=== fetch_sample ===")
    samp = fetch_sample()
    print(samp.head(12).to_string())
    print("rows:", len(samp), "dates:", sorted(samp["date"].dt.date.unique()))
    print("\n=== fetch_series ===")
    ser, meta = fetch_series()
    for k, v in ser.items():
        print(f"  {k}: {len(v)} pts, last={v.iloc[-1]:.2f}")
