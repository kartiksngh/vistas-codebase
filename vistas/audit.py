"""
Vistas DATA AUDIT — read-only, every data plane.

Finds, counts and classifies data-quality defects so we can feed model-grade data downstream.
Writes a human log (output/DATA_AUDIT.md) + a machine CSV per plane (output/audit_<plane>.csv).
Touches nothing — auditing is separate from fixing (the cleaners apply repairs).

Planes:
  stocks        wide adjusted-price panel (yfinance)            -> audit_price_panel
  world         wide cross-asset panel (Yahoo)                  -> audit_price_panel
  indices       TR (and any PR/PE/PB/DY) index panels           -> audit_price_panel
  macro         India + global macro series                     -> audit_macro
  fundamentals  per-company Screener bundles                    -> audit_fundamentals

Issue record: {plane, scope, entity, issue, severity, count, detail, fix}.
Severities: high (corrupts returns/levels — must fix), med (distorts some metrics),
low (cosmetic / informational).

Practitioner references folded into the checks: CRSP-style split/dividend back-adjustment;
Brownlees–Gallo / Hampel outlier (bad-tick) detection; winsorization of extreme returns;
survivorship & delisting awareness; point-in-time / look-ahead hygiene; unit/range sanity.
"""
from __future__ import annotations

import os
import math
import datetime as dt

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.abspath(os.path.join(HERE, "..", "output"))

# thresholds
T_JUMP = math.log(1.5)        # 1-day |ln ratio| > this => up>=50% / down>=33%
GAP_MAX = 5                   # consecutive-obs calendar gap to call a move "1-day"
X100_LO, X100_HI = 50.0, 200.0  # 100x decimal/currency glitch band
FLAT_RUN = 15                # >= N identical consecutive non-null prices => stale/suspended
LONG_GAP = 45                # calendar-day gap between consecutive obs => suspension/missing
SPARSE = 60                  # < N non-null obs => too short to model reliably
STALE_TAIL = 30              # last obs older than panel_end - N days => series went stale


def _sev_rank(s):
    return {"high": 0, "med": 1, "low": 2}.get(s, 3)


# ----------------------------------------------------------------------------- price panels
def _is_level(col: str, plane: str) -> bool:
    """True for compounding PRICE levels; False for yields/rates/spreads/vol where 0/negative
    values and large 1-day moves are LEGITIMATE (US T-bill→0 in 2020, WTI<0, VIX spikes)."""
    if plane in ("stocks",) or plane.startswith("indices"):
        return True
    u = col.lower()
    return not any(k in u for k in ("yield", "rate", "t-bill", "treasury", "vix", "volatility",
                                    "spread", "dxy", "index (vix"))


def audit_price_panel(df: pd.DataFrame, plane: str) -> list:
    """Battery of checks for any wide Date×series price/level panel."""
    out = []
    # stocks/indices are pure price levels (jumps = defects to fix); world is a mixed bag of
    # diverse instruments (commodities/FX/vol) whose big moves are often REAL -> report as med.
    jump_sev = "high" if (plane == "stocks" or plane.startswith("indices")) else "med"

    def add(scope, entity, issue, sev, count, detail, fix):
        out.append({"plane": plane, "scope": scope, "entity": entity, "issue": issue,
                    "severity": sev, "count": count, "detail": detail, "fix": fix})

    if df is None or not len(df):
        add("global", plane, "empty panel", "high", 0, "no data loaded", "check the snapshot CSV")
        return out

    idx = df.index
    # ---- index-level (global) checks
    dup = int(idx.duplicated().sum())
    if dup:
        add("global", plane, "duplicate dates", "high", dup,
            f"{dup} duplicated rows in the date index", "drop duplicate dates (keep last)")
    if not idx.is_monotonic_increasing:
        add("global", plane, "non-monotonic dates", "high", 1,
            "date index not sorted ascending", "sort_index()")
    # weekend stamps (often a vendor calendar artifact)
    wknd = int(((idx.dayofweek >= 5)).sum())
    if wknd:
        add("global", plane, "weekend-dated rows", "low", wknd,
            f"{wknd} rows fall on Sat/Sun", "usually harmless; verify the trading calendar")

    panel_end = idx.max()
    zero_neg = zero_neg_series = sparse = flat = longgap = stale = jumps = x100 = 0
    ex_zero, ex_jump, ex_x100, ex_flat, ex_gap, ex_stale = [], [], [], [], [], []

    for col in df.columns:
        s = df[col]
        nn = s.dropna()
        n = len(nn)
        if n == 0:
            add("series", col, "all-NaN series", "med", 0, "no observations", "drop column or refetch")
            continue
        v = nn.to_numpy(dtype="float64")
        d = nn.index
        lvl = _is_level(col, plane)        # price level vs yield/rate/vol (0/neg/spikes legit there)

        # zero / negative prices (only meaningful for price levels)
        zn = int((v <= 0).sum()) if lvl else 0
        if zn:
            zero_neg += zn; zero_neg_series += 1
            if len(ex_zero) < 6:
                ex_zero.append(f"{col}:{zn}")

        # sparse
        if n < SPARSE:
            sparse += 1

        # stale tail
        if (panel_end - d[-1]).days > STALE_TAIL:
            stale += 1
            if len(ex_stale) < 6:
                ex_stale.append(f"{col}@{d[-1].date()}")

        if n >= 2:
            ratio = v[1:] / np.where(v[:-1] == 0, np.nan, v[:-1])
            gap = (d[1:] - d[:-1]).days
            with np.errstate(divide="ignore", invalid="ignore"):
                lr = np.log(ratio)
            # 100x glitches (very high severity — Yahoo decimal/currency); price levels only
            x = np.where(lvl & (((ratio >= X100_LO) & (ratio <= X100_HI)) |
                         ((ratio <= 1 / X100_LO) & (ratio >= 1 / X100_HI))) & (gap <= GAP_MAX))[0]
            if len(x):
                x100 += len(x)
                if len(ex_x100) < 6:
                    ex_x100.append(f"{col}@{d[x[0]+1].date()}={round(float(ratio[x[0]]),1)}x")
            # suspicious 1-day jumps (excl the 100x ones)
            jm = np.where((np.abs(lr) > T_JUMP) & (gap <= GAP_MAX) &
                          ~(((ratio >= X100_LO) | (ratio <= 1 / X100_LO))))[0]
            if len(jm):
                jumps += len(jm)
                if len(ex_jump) < 6:
                    ex_jump.append(f"{col}@{d[jm[0]+1].date()}={round(float(ratio[jm[0]]),3)}")
            # long internal gaps
            lg = int((gap > LONG_GAP).sum())
            if lg:
                longgap += lg
                if len(ex_gap) < 6:
                    ex_gap.append(f"{col}:{lg}")

        # flat runs (>=FLAT_RUN identical consecutive values)
        if n >= FLAT_RUN:
            same = (np.diff(v) == 0).astype(int)
            run = mx = 0
            for z in same:
                run = run + 1 if z else 0
                mx = max(mx, run)
            if mx >= FLAT_RUN - 1:
                flat += 1
                if len(ex_flat) < 6:
                    ex_flat.append(f"{col}:{mx+1}d")

    if zero_neg:
        add("series", f"{zero_neg_series} series", "zero/negative prices", jump_sev, zero_neg,
            "e.g. " + ", ".join(ex_zero), "set <=0 to NaN (real for WTI<0); refetch")
    if x100:
        add("series", "various", "100x decimal/currency glitch", jump_sev, x100,
            "e.g. " + ", ".join(ex_x100), "interpolate (1-day) or back-adjust by 100")
    if jumps:
        add("series", "various", "extreme 1-day jump (split/bad-tick/merger)", jump_sev, jumps,
            "e.g. " + ", ".join(ex_jump), "clean_stocks.py: interpolate bad ticks / back-adjust splits / flag")
    if flat:
        add("series", f"{flat} series", "long flat run (>=15d identical)", "med", flat,
            "e.g. " + ", ".join(ex_flat), "suspension/illiquid/vendor-freeze; mask flat stretch for vol")
    if longgap:
        add("series", "various", "long internal gap (>45d)", "med", longgap,
            "e.g. " + ", ".join(ex_gap), "suspension/missing; treat as gap, don't interpolate across")
    if sparse:
        add("series", f"{sparse} series", "sparse history (<60 obs)", "low", sparse,
            "too short for stable stats", "exclude from models needing long history")
    if stale:
        add("series", f"{stale} series", "stale tail (no recent obs)", "med", stale,
            "e.g. " + ", ".join(ex_stale), "delisted/suspended; mark inactive (survivorship)")
    return out


# ----------------------------------------------------------------------------- macro
def audit_macro() -> list:
    out = []

    def add(scope, entity, issue, sev, count, detail, fix):
        out.append({"plane": "macro", "scope": scope, "entity": entity, "issue": issue,
                    "severity": sev, "count": count, "detail": detail, "fix": fix})
    try:
        from . import macro
        df = macro.load()
    except Exception as e:
        add("global", "macro", "load failed", "high", 0, str(e), "check macro snapshot")
        return out
    if df is None or not len(df):
        add("global", "macro", "empty", "high", 0, "no macro data", "run Pull India Macro.bat")
        return out
    idx = df.index
    if int(idx.duplicated().sum()):
        add("global", "macro", "duplicate dates", "high", int(idx.duplicated().sum()), "", "dedupe")
    if not idx.is_monotonic_increasing:
        add("global", "macro", "non-monotonic dates", "high", 1, "", "sort_index()")

    # plausible-range sanity by series-name keyword
    def expect(name):
        u = name.lower()
        if "inflation" in u or "yoy" in u or "growth" in u:
            return (-15.0, 40.0, "%")
        if any(k in u for k in ("repo", "yield", "t-bill", "rate", "wacr")):
            return (0.0, 25.0, "%")
        if any(k in u for k in ("cpi", "wpi", "iip")) and "inflation" not in u:
            return (20.0, 500.0, "index")
        return None
    end = idx.max()
    for col in df.columns:
        s = df[col].dropna()
        if not len(s):
            add("series", col, "all-NaN macro series", "med", 0, "", "wire the source or drop")
            continue
        rng = expect(col)
        if rng:
            lo, hi, _ = rng
            bad = int(((s < lo) | (s > hi)).sum())
            if bad:
                add("series", col, "out-of-range values", "med", bad,
                    f"{bad} obs outside [{lo},{hi}]; min={s.min():.2f} max={s.max():.2f}",
                    "unit error / outlier; verify source")
        if (end - s.index[-1]).days > 70:    # monthly series ~1 stamp/mo
            add("series", col, "stale macro series", "med", 1,
                f"last obs {s.index[-1].date()}", "refresh source or note the lag")

    # YoY recompute consistency — EXPLICIT, correct pairings (Rural↔Rural, etc.). A naive 12m
    # recompute diverges around a base-year rebase (official YoY links across it), so flag only
    # large gaps and label the caveat.
    cols = set(df.columns)
    pairs = [("CPI inflation — Combined (YoY)", "CPI — Combined (index)"),
             ("CPI inflation — Rural (YoY)", "CPI — Rural (index)"),
             ("CPI inflation — Urban (YoY)", "CPI — Urban (index)"),
             ("WPI inflation (YoY)", "WPI — All commodities (index)")]
    for ycol, bcol in pairs:
        if ycol in cols and bcol in cols:
            a = df[[bcol, ycol]].dropna()
            if len(a) > 13:
                recomp = (a[bcol] / a[bcol].shift(12) - 1) * 100
                cmp = pd.concat([recomp, a[ycol]], axis=1).dropna().tail(12)
                if len(cmp):
                    err = float((cmp.iloc[:, 0] - cmp.iloc[:, 1]).abs().mean())
                    if err > 3.0:
                        add("series", ycol, "YoY vs index gap (>3pp)", "med", 1,
                            f"mean |recomputed-published| = {err:.2f}pp vs {bcol} (may be a base rebase)",
                            "verify base year / linking; published YoY is authoritative")
    return out


# ----------------------------------------------------------------------------- fundamentals
def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if (isinstance(v, int) or math.isfinite(v)) else None
    s = str(v).strip().replace(",", "").replace("%", "").replace("₹", "")
    if s in ("", "-", "—", "nan", "NaN", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def audit_fundamentals() -> list:
    out = []

    def add(scope, entity, issue, sev, count, detail, fix):
        out.append({"plane": "fundamentals", "scope": scope, "entity": entity, "issue": issue,
                    "severity": sev, "count": count, "detail": detail, "fix": fix})
    try:
        from . import screener
        syms = screener.available()
    except Exception as e:
        add("global", "fundamentals", "load failed", "high", 0, str(e), "")
        return out
    if not syms:
        add("global", "fundamentals", "none cached", "high", 0, "", "run Pull Screener Fundamentals.bat")
        return out

    STMTS = ["quarters", "profit_loss", "balance_sheet", "cash_flow", "ratios", "shareholding"]
    n = len(syms)
    missing_stmt = {k: 0 for k in STMTS}
    parse_fail = parse_cells = 0
    name_mismatch = stale = neg_sales = no_val = dup_period = err_bundle = 0
    ex_name, ex_parse, ex_neg, ex_stale = [], [], [], []
    today = dt.date.today()

    for sym in syms:
        b = screener.load(sym)
        if not b:
            err_bundle += 1
            continue
        if not b.get("ok", True):
            err_bundle += 1
            continue
        name = (b.get("name") or "")
        # symbol vs name sanity: the LT->"LTM Ltd" class. Flag when neither the symbol's
        # leading alpha chunk nor the name's acronym/first word relate to the other.
        if name:
            sy = "".join(ch for ch in sym.upper() if ch.isalpha())     # digit-insensitive
            words = [w for w in "".join(c if c.isalnum() else " " for c in name.upper()).split() if w]
            acr = "".join(w[0] for w in words if w[0].isalpha())
            joined = "".join(ch for ch in "".join(words) if ch.isalpha())
            rel = bool(sy) and (sy[:4] in joined or joined[:4] in sy or acr.startswith(sy[:3])
                                or sy[:3] in acr or (words and words[0][:4] in sy)
                                or any(len(w) >= 4 and w[:4] in sy for w in words))
            if not rel and len(sy) >= 3:
                name_mismatch += 1
                if len(ex_name) < 12:
                    ex_name.append(f"{sym}->{name}")
        # statements present
        st = b.get("statements") or {}
        for k in STMTS:
            if not st.get(k):
                missing_stmt[k] += 1
        # numeric parse failures + duplicate period headers
        for k, rows in st.items():
            if not isinstance(rows, list) or not rows:
                continue
            hdrs = list(rows[0].keys()) if isinstance(rows[0], dict) else []
            if len(hdrs) != len(set(hdrs)):
                dup_period += 1
            for row in rows:
                if not isinstance(row, dict):
                    continue
                keys = list(row.keys())
                for kk in keys[1:]:
                    val = row.get(kk)
                    if not isinstance(val, str):       # numbers / NaN floats / None aren't text errors
                        continue
                    sv = val.strip()
                    if sv in ("", "-", "—") or _num(sv) is not None:
                        continue                        # empty or parseable -> fine
                    parse_cells += 1
                    if len(ex_parse) < 8:
                        ex_parse.append(f"{sym}/{k}:{sv[:18]}")
        if parse_cells and parse_fail == 0:
            parse_fail = 1
        # negative sales (impossible) in P&L
        pl = st.get("profit_loss") or []
        for row in pl:
            lab = str(list(row.values())[0] if row else "").lower()
            if "sales" in lab or "revenue" in lab:
                for kk in list(row.keys())[1:]:
                    nv = _num(row.get(kk))
                    if nv is not None and nv < 0:
                        neg_sales += 1
                        if len(ex_neg) < 6:
                            ex_neg.append(f"{sym}/{kk}")
                break
        # valuation present?
        val = b.get("valuation") or {}
        if not any(val.get(x) for x in ("Price to Earning", "EPS")):
            no_val += 1
        # stale fetched
        f = b.get("fetched")
        try:
            if f and (today - dt.date.fromisoformat(f)).days > 30:
                stale += 1
                if len(ex_stale) < 6:
                    ex_stale.append(f"{sym}@{f}")
        except Exception:
            pass

    add("global", "fundamentals", "companies cached", "low", n, "universe size", "")
    if err_bundle:
        add("series", f"{err_bundle} cos", "error/empty bundle", "high", err_bundle,
            "ok=False or unreadable", "refetch these symbols")
    if parse_cells:
        add("series", "various", "non-numeric statement cells", "med", parse_cells,
            "e.g. " + ", ".join(ex_parse), "coerce via _num at load; many are footnote text")
    if name_mismatch:
        add("series", f"{name_mismatch} cos", "symbol<->name possible-mismatch (review)", "med", name_mismatch,
            "e.g. " + ", ".join(ex_name), "heuristic; verify the genuine ones (e.g. LT->LTM) by exact Screener URL")
    if neg_sales:
        add("series", f"{neg_sales} rows", "negative sales (impossible)", "high", neg_sales,
            "e.g. " + ", ".join(ex_neg), "parse/sign error; refetch or null")
    if dup_period:
        add("series", f"{dup_period} tables", "duplicate period headers", "med", dup_period,
            "read_html collided columns", "de-dup headers on parse")
    if no_val:
        add("series", f"{no_val} cos", "no valuation series", "med", no_val,
            "missing PE/EPS history", "refetch valuation chart")
    for k, c in missing_stmt.items():
        if c:
            sev = "med" if k in ("profit_loss", "balance_sheet", "cash_flow") else "low"
            add("series", f"{c} cos", f"missing statement: {k}", sev, c, "", "refetch / banks lack some")
    if stale:
        add("series", f"{stale} cos", "stale fundamentals (>30d)", "low", stale,
            "e.g. " + ", ".join(ex_stale), "refresh incrementally")
    return out


# ----------------------------------------------------------------------------- orchestrate
def run(planes=("stocks", "fundamentals", "world", "indices", "macro"), progress=None) -> dict:
    log = progress or (lambda m: print(m, flush=True))
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_issues = []
    summary = {}

    for plane in planes:
        log(f"[audit] {plane}…")
        try:
            if plane == "stocks":
                from . import stocks
                iss = audit_price_panel(stocks.load(), "stocks")
            elif plane == "world":
                from . import world
                iss = audit_price_panel(world.load_named(), "world")
            elif plane == "indices":
                from . import data
                iss = []
                for m in data.measures_present():
                    iss += audit_price_panel(data.load(m), f"indices:{m}")
            elif plane == "macro":
                iss = audit_macro()
            elif plane == "fundamentals":
                iss = audit_fundamentals()
            else:
                iss = []
        except Exception as e:
            iss = [{"plane": plane, "scope": "global", "entity": plane, "issue": "audit crashed",
                    "severity": "high", "count": 0, "detail": str(e), "fix": "debug audit"}]
        all_issues += iss
        hi = sum(1 for x in iss if x["severity"] == "high")
        summary[plane] = {"issues": len(iss), "high": hi}
        log(f"  {len(iss)} issue-types ({hi} high)")
        # per-plane CSV
        if iss:
            pd.DataFrame(iss).to_csv(os.path.join(OUTPUT_DIR, f"audit_{plane.replace(':','_')}.csv"), index=False)

    # master markdown log
    md = ["# Vistas DATA AUDIT", "",
          f"Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
          "", "Severity: **high** = corrupts levels/returns (must fix) · med = distorts some metrics · low = informational.",
          ""]
    for plane in planes:
        pl_iss = [x for x in all_issues if x["plane"].split(":")[0] == plane or x["plane"] == plane]
        md.append(f"## {plane}  —  {len(pl_iss)} issue-types, {sum(1 for x in pl_iss if x['severity']=='high')} high")
        if not pl_iss:
            md.append("\n_no issues detected._\n"); continue
        md.append("\n| sev | issue | count | entity | detail | fix |")
        md.append("|---|---|---|---|---|---|")
        for x in sorted(pl_iss, key=lambda z: _sev_rank(z["severity"])):
            det = str(x["detail"]).replace("|", "/")[:90]
            md.append(f"| {x['severity']} | {x['issue']} | {x['count']} | {x['entity']} | {det} | {x['fix']} |")
        md.append("")
    with open(os.path.join(OUTPUT_DIR, "DATA_AUDIT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    log(f"\n[audit] {len(all_issues)} issue-types total -> output/DATA_AUDIT.md")
    return {"ok": True, "summary": summary, "n_issues": len(all_issues),
            "report": os.path.join(OUTPUT_DIR, "DATA_AUDIT.md")}


if __name__ == "__main__":
    import sys
    pl = tuple(a for a in sys.argv[1:] if not a.startswith("-")) or \
        ("stocks", "fundamentals", "world", "indices", "macro")
    run(planes=pl)
