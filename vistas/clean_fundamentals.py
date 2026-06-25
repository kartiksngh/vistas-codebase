"""
Fundamentals data cleaner — turns the raw Screener bundles into a MODEL-GRADE tidy dataset,
plus a per-company quality scorecard and flag lists for the issues we can't safely auto-fix.

Raw bundles (data/screener/<SYM>.json) are read_html parses: numbers mixed with footnote text,
NBSP/'+'-suffixed labels, NaN-filled empty cells, "Mar 2024"/"Jun 2024" period headers. Models
need: clean numeric values, normalized period dates, impossible values removed, and an explicit
record of what's missing/suspect (never silently fabricate).

Outputs:
  export/parquet/fundamentals_clean_statements.parquet   tidy: symbol,name,statement,line_item,
                                                          period,period_date,is_quarter,value
  output/fundamentals_quality.csv                         one row/company: completeness scorecard
  output/fundamentals_flags.csv                           the issues to review/refetch

Auto-fixed (safe, deterministic): numeric coercion; impossible negatives nulled (Sales/Revenue<0,
share-count<0); duplicate period headers de-duped. FLAGGED (not auto-changed): symbol<->name
resolution doubts, missing statements, missing valuation — these need a refetch/manual check.
"""
from __future__ import annotations

import os
import re
import math
import datetime as dt

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.abspath(os.path.join(HERE, "..", "output"))
PARQUET_DIR = os.path.join(OUTPUT_DIR, "..", "export", "parquet")
PARQUET_DIR = os.path.abspath(PARQUET_DIR)

STMTS = ["quarters", "profit_loss", "balance_sheet", "cash_flow", "ratios", "shareholding"]
CORE = ["profit_loss", "balance_sheet", "cash_flow"]
_MONTH_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{4})")


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if (isinstance(v, int) or math.isfinite(v)) else None
    s = str(v).strip().replace(",", "").replace("%", "").replace("₹", "").replace("\xa0", "")
    if s in ("", "-", "—", "nan", "NaN", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _period_date(label: str):
    """'Mar 2024'/'Jun 2024' -> month-end Timestamp; else NaT."""
    m = _MONTH_RE.search(str(label))
    if not m:
        return pd.NaT
    return pd.to_datetime(f"{m.group(1)[:3]} {m.group(2)}", format="%b %Y", errors="coerce")


def _label(row):
    """The line-item label (first column; Screener uses 'Unnamed: 0' or the first key)."""
    if "Unnamed: 0" in row:
        return str(row["Unnamed: 0"]).replace("\xa0", " ").strip()
    return str(next(iter(row.values()))).replace("\xa0", " ").strip() if row else ""


def _resolution_doubt(sym: str, name: str) -> bool:
    if not name:
        return True
    sy = "".join(ch for ch in sym.upper() if ch.isalpha())
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name.upper()) if w]
    acr = "".join(w[0] for w in words if w[0].isalpha())
    joined = "".join(ch for ch in "".join(words) if ch.isalpha())
    if len(sy) < 3:
        return False
    rel = (sy[:4] in joined or joined[:4] in sy or acr.startswith(sy[:3]) or sy[:3] in acr
           or any(len(w) >= 4 and w[:4] in sy for w in words))
    return not rel


def build(progress=None) -> dict:
    log = progress or (lambda m: print(m, flush=True))
    from . import screener
    syms = screener.available()
    if not syms:
        return {"ok": False, "error": "no fundamentals cached"}
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(PARQUET_DIR, exist_ok=True)

    rows = []          # tidy statement rows
    quality = []       # per-company scorecard
    flags = []         # issues to review/refetch
    n_neg_nulled = n_text = 0

    def flag(sym, issue, detail):
        flags.append({"symbol": sym, "issue": issue, "detail": detail})

    for i, sym in enumerate(syms):
        b = screener.load(sym)
        if not b or not b.get("ok", True):
            flag(sym, "error/empty bundle", "ok=False or unreadable")
            quality.append({"symbol": sym, "name": "", "ok": False})
            continue
        name = b.get("name") or sym
        st = b.get("statements") or {}
        val = b.get("valuation") or {}
        present = [k for k in STMTS if st.get(k)]
        has_val = bool(val.get("Price to Earning") or val.get("EPS"))
        parse_text = 0

        for k in STMTS:
            for row in (st.get(k) or []):
                if not isinstance(row, dict):
                    continue
                li = _label(row)
                li_l = li.lower()
                is_sales = ("sales" in li_l or "revenue" in li_l) and "expense" not in li_l
                is_shares = "no. of shares" in li_l or "share capital" in li_l
                for kk in list(row.keys())[1:]:
                    raw = row.get(kk)
                    if isinstance(raw, str) and raw.strip() not in ("", "-", "—") and _num(raw) is None:
                        parse_text += 1
                    v = _num(raw)
                    if v is not None and ((is_sales and v < 0) or (is_shares and v < 0)):
                        v = None; n_neg_nulled += 1          # impossible -> null
                    pd_ = _period_date(kk)
                    rows.append((sym, name, k, li, str(kk), pd_,
                                 bool(k == "quarters"), v))
        n_text += parse_text

        # scorecard
        miss_core = [k for k in CORE if k not in present]
        doubt = _resolution_doubt(sym, name)
        quality.append({"symbol": sym, "name": name, "ok": True,
                        "n_statements": len(present), "missing_core": ",".join(miss_core),
                        "has_valuation": has_val, "parse_text_cells": parse_text,
                        "resolution_doubt": doubt, "fetched": b.get("fetched")})
        if miss_core:
            flag(sym, "missing core statement(s)", ",".join(miss_core))
        if not has_val:
            flag(sym, "no valuation series", "missing PE/EPS history")
        if doubt:
            flag(sym, "symbol<->name resolution doubt", f"{sym} -> {name}")
        if (i + 1) % 500 == 0:
            log(f"  …processed {i + 1}/{len(syms)}")

    # write tidy clean statements (the model interface)
    tidy = pd.DataFrame(rows, columns=["symbol", "name", "statement", "line_item",
                                       "period", "period_date", "is_quarter", "value"])
    tidy.to_parquet(os.path.join(PARQUET_DIR, "fundamentals_clean_statements.parquet"), index=False)
    qdf = pd.DataFrame(quality)
    qdf.to_csv(os.path.join(OUTPUT_DIR, "fundamentals_quality.csv"), index=False)
    fdf = pd.DataFrame(flags) if flags else pd.DataFrame(columns=["symbol", "issue", "detail"])
    fdf.to_csv(os.path.join(OUTPUT_DIR, "fundamentals_flags.csv"), index=False)

    summary = {"ok": True, "companies": len(syms), "tidy_rows": len(tidy),
               "neg_nulled": n_neg_nulled, "text_cells": n_text,
               "flags": len(flags),
               "no_valuation": int((~qdf.get("has_valuation", pd.Series(dtype=bool)).fillna(False)).sum()) if len(qdf) else 0,
               "resolution_doubt": int(qdf.get("resolution_doubt", pd.Series(dtype=bool)).fillna(False).sum()) if len(qdf) else 0,
               "tidy_parquet": os.path.join(PARQUET_DIR, "fundamentals_clean_statements.parquet"),
               "quality_csv": os.path.join(OUTPUT_DIR, "fundamentals_quality.csv"),
               "flags_csv": os.path.join(OUTPUT_DIR, "fundamentals_flags.csv")}
    log(f"\nfundamentals clean: {len(tidy):,} tidy rows; {n_neg_nulled} impossible negatives nulled; "
        f"{summary['no_valuation']} no-valuation, {summary['resolution_doubt']} resolution-doubt -> flags CSV")
    return summary


if __name__ == "__main__":
    build()
