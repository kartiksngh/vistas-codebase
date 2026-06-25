"""
Stock price data-quality cleaner + NSE-bhavcopy cross-check.

The stock panel is yfinance auto-adjusted close. Yahoo's India adjustment is imperfect, so
the panel carries three kinds of defect:
  * bad ticks      — a 1-day spike that reverts the next day (Yahoo glitch).
  * unadjusted CA  — a persistent jump on a round factor (½, ⅕, 1:1 bonus…) Yahoo failed to
                     back-adjust (split/bonus/consolidation).
  * real moves     — a genuine large move (news) or a merger/demerger value transfer.

This module DETECTS every suspicious 1-day jump, CROSS-CHECKS it against the authoritative
NSE bhavcopy (the exchange's own daily close), then REPAIRS only the high-confidence defects
and FLAGS the rest — it never silently rewrites an ambiguous move ("no score for error"). The
raw snapshot is backed up first; a full per-event report CSV makes every change auditable.

Repair rules (per event):
  * bad tick (reverts, or NSE shows a smooth series)      -> interpolate the single day out.
  * round-factor jump that NSE ALSO shows (real split/CA) -> back-adjust pre-event prices.
  * round-factor jump, NSE unavailable                    -> back-adjust (best effort).
  * anything NSE confirms as a real non-round move, or any persistent jump we can't explain
                                                          -> FLAG only, leave the data as-is.

Network (NSE) is OPTIONAL and graceful: if bhavcopy is unreachable we fall back to the offline
classification and say so in the report. Nothing here touches the TR index pipeline.
"""
from __future__ import annotations

import os
import io
import json
import time
import math
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
OUTPUT_DIR = os.path.abspath(os.path.join(HERE, "..", "output"))
RAW_BACKUP_DIR = os.path.join(DATA_DIR, "_raw")
BHAV_CACHE = os.path.join(DATA_DIR, "_bhavcache")
REPORT_CSV = os.path.join(OUTPUT_DIR, "stock_data_quality_report.csv")

# split/bonus/consolidation signatures (a 1-day ratio sitting near one of these)
ROUND = [0.5, 1/3, 0.25, 0.2, 0.1, 1/1.5, 2.0, 3.0, 4.0, 5.0, 10.0]
T_JUMP = math.log(1.5)        # |ln ratio| > this  => up >=50% or down >=33%  (a "jump")
GAP_MAX_DAYS = 5              # only treat near-consecutive obs as a 1-day event
ROUND_TOL = 0.04             # ratio within 4% of a round factor => looks like a split/bonus
REVERT_TOL = 0.10            # next-day ratio ~ 1/r  => a reverting bad tick
CONFIRM_TOL = 0.12           # NSE ratio ~ yf ratio  => exchange confirms the move
SMOOTH_TOL = 0.15            # NSE ratio ~ 1         => exchange shows no jump (yf artifact)


# Known true NSE listing dates for tickers where yfinance carries PHANTOM pre-listing data
# (a back-filled series dated BEFORE the security actually traded — it corrupts the earliest
# returns, rolling alpha and the NAV start). Seeded with confirmed cases; extend as found.
# The general method (see the data-cleaning skill): a stock's FIRST appearance in the NSE
# bhavcopy is its listing date — anything Yahoo shows before that is fabricated.
LISTING_DATES = {
    "TCS": "2004-08-25",     # IPO listed 2004-08-25; Yahoo carries bogus prices back to 2002-08
}


def trim_pre_listing(df: pd.DataFrame, log=lambda m: None) -> pd.DataFrame:
    """Null any data dated before a ticker's known true listing date (phantom pre-IPO series)."""
    n = 0
    for sym, d0 in LISTING_DATES.items():
        if sym in df.columns:
            mask = df.index < pd.Timestamp(d0)
            cnt = int(df.loc[mask, sym].notna().sum())
            if cnt:
                df.loc[mask, sym] = np.nan
                n += cnt
                log(f"  trimmed {cnt} phantom pre-listing rows for {sym} (before {d0})")
    return df


# ----------------------------------------------------------------------------- detect
def _snap_round(r):
    """Nearest round split factor to ratio r, or None if not close to one."""
    best, bd = None, 1e9
    for x in ROUND:
        d = abs(r / x - 1)
        if d < bd:
            best, bd = x, d
    return best if bd < ROUND_TOL else None


def detect_jumps(df: pd.DataFrame) -> list:
    """Every suspicious 1-day jump in the wide adjusted-price panel.
    One event = {sym, prev_date, date, prev, new, ratio, gap, reverts, round_factor}."""
    events = []
    for col in df.columns:
        s = df[col].dropna()
        if len(s) < 3:
            continue
        v = s.to_numpy(dtype="float64")
        idx = s.index
        gap = (idx[1:] - idx[:-1]).days
        ratio = v[1:] / v[:-1]
        with np.errstate(divide="ignore", invalid="ignore"):
            lr = np.log(ratio)
        hit = np.where((np.abs(lr) > T_JUMP) & (gap <= GAP_MAX_DAYS))[0]
        for i in hit:
            r = float(ratio[i])
            nxt = float(ratio[i + 1]) if i + 1 < len(ratio) else None
            reverts = (nxt is not None) and (abs(r * nxt - 1) < REVERT_TOL)
            events.append({
                "sym": col, "prev_date": idx[i].strftime("%Y-%m-%d"),
                "date": idx[i + 1].strftime("%Y-%m-%d"),
                "prev": round(float(v[i]), 4), "new": round(float(v[i + 1]), 4),
                "ratio": round(r, 4), "gap": int(gap[i]),
                "reverts": bool(reverts), "round_factor": _snap_round(r),
                "pos": int(i + 1),            # position of the NEW value in the dropna series
            })
    return events


def is_material(e) -> bool:
    """A modern, non-penny event worth acting on (strips deep-history sub-rupee noise)."""
    return e["prev"] >= 5 and e["new"] >= 5 and e["date"] >= "2010-01-01"


# ----------------------------------------------------------------------------- NSE bhavcopy
PAGE = "https://www.nseindia.com/all-reports"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _nse_session():
    if requests is None:
        return None
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    try:
        s.get("https://www.nseindia.com", timeout=30)
        s.get(PAGE, timeout=30)              # land like a browser -> seed cookies
    except Exception:
        pass
    return s


def _bhav_urls(d: dt.date):
    """Both the new (UDiFF, 2024-07+) and old bhavcopy URLs for a date — try in order."""
    ymd = d.strftime("%Y%m%d")
    new = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
    old = (f"https://nsearchives.nseindia.com/content/historical/EQUITIES/"
           f"{d.year}/{_MON[d.month - 1]}/cm{d.strftime('%d')}{_MON[d.month - 1]}{d.year}bhav.csv.zip")
    return [new, old]


def fetch_bhav(date_str: str, session, log=lambda m: None) -> dict | None:
    """{SYMBOL: close} for EQ-series rows on `date_str`, from NSE bhavcopy. Cached to disk.
    None if the date isn't available (weekend/holiday/archive gone) or NSE is unreachable."""
    os.makedirs(BHAV_CACHE, exist_ok=True)
    cache = os.path.join(BHAV_CACHE, f"{date_str}.json")
    if os.path.exists(cache):
        try:
            with open(cache) as f:
                j = json.load(f)
            return j or None
        except Exception:
            pass
    if session is None:
        return None
    d = dt.date.fromisoformat(date_str)
    out = None
    for url in _bhav_urls(d):
        try:
            r = session.get(url, timeout=40, headers={"Referer": PAGE})
            if r.status_code != 200 or not r.content[:2] == b"PK":
                continue
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            name = zf.namelist()[0]
            df = pd.read_csv(zf.open(name))
            cols = {c.strip().upper(): c for c in df.columns}
            if "TCKRSYMB" in cols:                       # new UDiFF format
                sym_c, cls_c, ser_c = cols["TCKRSYMB"], cols["CLSPRIC"], cols.get("SCTYSRS")
            elif "SYMBOL" in cols:                        # old format
                sym_c, cls_c, ser_c = cols["SYMBOL"], cols["CLOSE"], cols.get("SERIES")
            else:
                continue
            if ser_c:
                df = df[df[ser_c].astype(str).str.strip().str.upper() == "EQ"]
            out = {str(k).strip().upper(): float(v) for k, v in zip(df[sym_c], df[cls_c])
                   if pd.notna(v)}
            break
        except Exception as e:
            log(f"    bhav {date_str} {url.split('/')[-1]}: {e}")
            continue
    try:
        with open(cache, "w") as f:
            json.dump(out or {}, f)            # cache even an empty result (don't refetch holidays)
    except Exception:
        pass
    time.sleep(random.uniform(0.6, 1.4))       # polite
    return out or None


def crosscheck(events, session, log=lambda m: None) -> None:
    """Annotate each event in place with nse_prev / nse_new / nse_ratio / nse_verdict by
    comparing the exchange's own raw close ratio to yfinance's. Bounded to material events."""
    todo = [e for e in events if is_material(e)]
    dates = sorted({e["prev_date"] for e in todo} | {e["date"] for e in todo})
    log(f"  cross-checking {len(todo)} material events over {len(dates)} bhavcopy dates…")
    bhav = {}
    for i, ds in enumerate(dates):
        bhav[ds] = fetch_bhav(ds, session, log)
        if (i + 1) % 25 == 0:
            log(f"    bhavcopy {i + 1}/{len(dates)} ({sum(1 for b in bhav.values() if b)} found)")
    for e in events:
        e["nse_prev"] = e["nse_new"] = e["nse_ratio"] = None
        e["nse_verdict"] = "not-checked"
        if not is_material(e):
            continue
        bp, bn = bhav.get(e["prev_date"]), bhav.get(e["date"])
        sym = e["sym"].upper()
        if not bp or not bn or sym not in bp or sym not in bn or bp[sym] == 0:
            e["nse_verdict"] = "no-bhavcopy"
            continue
        nr = bn[sym] / bp[sym]
        e["nse_prev"], e["nse_new"], e["nse_ratio"] = round(bp[sym], 2), round(bn[sym], 2), round(nr, 4)
        if abs(nr / e["ratio"] - 1) < CONFIRM_TOL:
            e["nse_verdict"] = "confirmed (exchange shows the same move)"
        elif abs(nr - 1) < SMOOTH_TOL:
            e["nse_verdict"] = "yfinance-artifact (exchange smooth)"
        else:
            e["nse_verdict"] = "diverges (ambiguous)"


# ----------------------------------------------------------------------------- decide + repair
def decide_action(e) -> str:
    """high-confidence repair, or flag-only. Uses the NSE verdict when present, else the
    offline signature."""
    v = e.get("nse_verdict", "not-checked")
    if v.startswith("yfinance-artifact"):
        return "interpolate" if e["reverts"] or e["gap"] <= 2 else "flag"
    if v.startswith("confirmed"):
        return "backadjust" if e["round_factor"] else "flag"   # confirmed + round = real split
    if v == "diverges (ambiguous)":
        return "flag"
    # no NSE info -> offline signature
    if e["reverts"]:
        return "interpolate"
    if e["round_factor"]:
        return "backadjust"
    return "flag"


def clean_panel(df: pd.DataFrame, events: list) -> tuple:
    """Return (clean_df, n_interp, n_split). Applies interpolation (bad ticks) + split
    back-adjustment (continuous-level) per column; leaves flagged events untouched."""
    out = df.copy()
    by_sym = {}
    for e in events:
        e["action"] = decide_action(e)
        by_sym.setdefault(e["sym"], []).append(e)
    n_interp = n_split = n_revert_skip = 0
    for sym, evs in by_sym.items():
        s = out[sym].dropna()
        if len(s) < 3:
            continue
        v = s.to_numpy(dtype="float64")
        idx = s.index
        # 1) interpolate single-day bad ticks (geometric mean of neighbours)
        interp_pos = set()
        for e in evs:
            if e["action"] == "interpolate":
                p = e["pos"]
                if 0 < p < len(v) - 1 and v[p - 1] > 0 and v[p + 1] > 0:
                    v[p] = math.sqrt(v[p - 1] * v[p + 1]); n_interp += 1; interp_pos.add(p)
        # 2) back-adjust splits: multiply all PRE-event prices by the round factor (left->right
        #    *= accumulates correctly when a column has multiple splits). CRITICAL: a one-day bad
        #    tick (spike up at pos p, then back down at p+1) shows up as TWO events — the spike
        #    (interpolated above) AND a spurious round-factor "split" on the revert day (p+1).
        #    Once the spike is interpolated out, that revert is NOT a real split; back-adjusting it
        #    would wrongly rescale ALL prior history (this halved TCS's whole pre-2005 series).
        for e in sorted([e for e in evs if e["action"] == "backadjust"], key=lambda x: x["pos"]):
            p = e["pos"]
            if (p - 1) in interp_pos:                 # the bad-tick revert, already handled
                e["action"] = "skip-revert"; n_revert_skip += 1; continue
            f = e["round_factor"]
            if f and 0 < p <= len(v):
                v[:p] = v[:p] * f; n_split += 1
        out.loc[idx, sym] = v
    return out, n_interp, n_split, n_revert_skip


# ----------------------------------------------------------------------------- orchestrate
def _latest_stock_csv():
    import glob
    c = glob.glob(os.path.join(DATA_DIR, "Stocks Data PX till *.csv"))
    return max(c, key=os.path.getmtime) if c else None


def _nse_calendar():
    """The authoritative NSE trading calendar = the dates of the loaded TR index frame
    (after the 2000 floor + the vendor-stale-day filter). yfinance stock data sits on its
    own (imperfect) calendar; reindexing every stock onto THIS makes all series share NSE's
    trading days, with NSE deciding which days count — the root-cause cure for the calendar
    mismatch that fragmented rolling beta/vol/Sharpe/correlation."""
    from . import data
    try:
        idx = pd.DatetimeIndex(data.load(data.DEFAULT_MEASURE).index).sort_values()
        return idx if len(idx) else None
    except Exception:
        return None


def align_to_nse(df: pd.DataFrame, cal, ffill_limit: int = 5, log=lambda m: None) -> pd.DataFrame:
    """Reindex the stock panel onto the NSE trading calendar `cal`: drop any stock-only dates
    (days NSE did not trade) and carry the last print across SHORT gaps (<= ffill_limit days
    NSE traded but Yahoo missed a print). Pre-listing leading NaNs are never back-filled, so no
    price is fabricated before a stock's first real print."""
    if cal is None or not len(cal):
        log("  NSE calendar unavailable — skipping alignment")
        return df
    before = df.shape[0]
    out = df.sort_index().reindex(cal)
    out = out.ffill(limit=ffill_limit)        # leading NaNs have nothing to carry -> stay NaN
    log(f"  NSE-aligned: {before} stock rows -> {out.shape[0]} NSE trading days "
        f"(ffill <= {ffill_limit}d for missed prints; pre-listing left blank)")
    return out


def run(crosscheck_nse: bool = True, promote: bool = False, align_nse: bool = True,
        progress=None) -> dict:
    """Detect -> cross-check (NSE) -> repair -> write report (+ optionally promote the cleaned
    snapshot to the canonical file, backing up the raw original first)."""
    log = progress or (lambda m: print(m, flush=True))
    from . import stocks
    src = _latest_stock_csv()
    if not src:
        return {"ok": False, "error": "no stock snapshot found"}
    df = stocks.load().copy()
    # FIX 0: zero/negative adjusted prices -> NaN. These are deep-history (2002-04) prices
    # back-adjusted to sub-paisa that rounded to 0; a 0 price makes returns infinite. Safe.
    zmask = df <= 0
    n_zero = int(zmask.to_numpy().sum())
    if n_zero:
        df = df.mask(zmask)
        log(f"[0/4] nulled {n_zero} zero/negative adjusted prices (deep-history rounding)")
    df = trim_pre_listing(df, log)        # drop phantom pre-IPO segments (known listing dates)
    log(f"[1/4] scanning {df.shape[1]} stocks x {df.shape[0]} dates for suspicious jumps…")
    events = detect_jumps(df)
    mat = [e for e in events if is_material(e)]
    log(f"  {len(events)} jumps (>50%/-33%, gap<=5d); {len(mat)} material (>=Rs.5, >=2010)")

    if crosscheck_nse:
        log("[2/4] cross-checking material events against NSE bhavcopy…")
        s = _nse_session()
        crosscheck(events, s, log)
        verdicts = {}
        for e in events:
            if is_material(e):
                verdicts[e["nse_verdict"]] = verdicts.get(e["nse_verdict"], 0) + 1
        log("  NSE verdicts (material): " + ", ".join(f"{k}={v}" for k, v in sorted(verdicts.items())))
    else:
        log("[2/4] skipping NSE cross-check (offline mode)")
        for e in events:
            e["nse_prev"] = e["nse_new"] = e["nse_ratio"] = None
            e["nse_verdict"] = "not-checked"

    log("[3/4] repairing high-confidence defects (interpolate bad ticks, back-adjust splits)…")
    clean, n_interp, n_split, n_revert_skip = clean_panel(df, events)
    n_flag = sum(1 for e in events if e.get("action") == "flag")
    log(f"  repaired: {n_interp} bad ticks interpolated, {n_split} splits back-adjusted; "
        f"{n_revert_skip} bad-tick reverts NOT mistaken for splits; {n_flag} flagged (left as-is)")

    n_aligned = 0
    if align_nse:
        log("[3b/4] aligning every stock onto the NSE trading calendar (NSE decides the days)…")
        cal = _nse_calendar()
        clean = align_to_nse(clean, cal, log=log)
        n_aligned = len(cal) if cal is not None else 0

    # report CSV — every event, sorted material-first then by symbol/date
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    rep = pd.DataFrame(events)
    if len(rep):
        rep["material"] = rep.apply(is_material, axis=1)
        cols = ["material", "sym", "prev_date", "date", "prev", "new", "ratio", "gap", "reverts",
                "round_factor", "nse_prev", "nse_new", "nse_ratio", "nse_verdict", "action"]
        rep = rep[[c for c in cols if c in rep.columns]].sort_values(
            ["material", "sym", "date"], ascending=[False, True, True])
        rep.to_csv(REPORT_CSV, index=False)
    log(f"  wrote report -> {REPORT_CSV} ({len(rep)} rows)")

    # write cleaned snapshot
    last = clean.index.max().date()
    clean_name = f"Stocks Data PX Clean till {last.strftime('%b %#d, %Y')}.csv"
    clean_path = os.path.join(DATA_DIR, clean_name)
    clean.reset_index().rename(columns={"index": "Date"}).to_csv(clean_path, index=False)
    log(f"[4/4] wrote cleaned snapshot -> {clean_name}")

    promoted = False
    if promote:
        os.makedirs(RAW_BACKUP_DIR, exist_ok=True)
        backup = os.path.join(RAW_BACKUP_DIR, os.path.basename(src))
        if not os.path.exists(backup):
            import shutil
            shutil.copyfile(src, backup)                 # preserve the raw original (once)
        clean.reset_index().rename(columns={"index": "Date"}).to_csv(src, index=False)
        promoted = True
        log(f"  PROMOTED: raw backed up -> _raw/{os.path.basename(src)}; canonical now cleaned")

    return {"ok": True, "n_events": len(events), "n_material": len(mat), "n_zeroed": n_zero,
            "n_interpolated": n_interp, "n_backadjusted": n_split, "n_flagged": n_flag,
            "n_nse_days": n_aligned, "report": REPORT_CSV, "clean_csv": clean_path,
            "promoted": promoted}


if __name__ == "__main__":
    import sys
    run(crosscheck_nse=("--no-nse" not in sys.argv), promote=("--promote" in sys.argv),
        align_nse=("--no-align" not in sys.argv))
