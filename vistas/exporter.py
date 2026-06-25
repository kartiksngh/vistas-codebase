"""
Local data dump for Vistas — every dataset Vistas has collected, written to fast,
portable files you can open straight away.

Two output trees under ./export :
  parquet/   columnar binaries — load in one line with pandas, ~10x faster + far
             smaller than the raw CSVs. Also readable by R, DuckDB, Power BI,
             Excel's Power Query ("Get Data -> Parquet").
  excel/     multi-sheet .xlsx workbooks for hand analysis.

Python:
    import pandas as pd
    tr     = pd.read_parquet("export/parquet/indices_TR.parquet")     # Date index, one col / index
    stocks = pd.read_parquet("export/parquet/stocks.parquet")         # Date index, one col / stock
    macro  = pd.read_parquet("export/parquet/macro.parquet")
    pe     = pd.read_parquet("export/parquet/fundamentals_pe.parquet")# Date index, one col / company
    stmts  = pd.read_parquet("export/parquet/fundamentals_statements.parquet")  # tidy/long

Excel:  open anything in export/excel/.

Self-contained + graceful: a dataset that isn't present is skipped, never fatal.
"""
from __future__ import annotations

import os
import json
import math
import datetime as dt

import pandas as pd

from . import data

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.abspath(os.path.join(HERE, "..", "export"))
PARQUET_DIR = os.path.join(EXPORT_DIR, "parquet")
EXCEL_DIR = os.path.join(EXPORT_DIR, "excel")

# Excel hard limits (so we never silently truncate a wide dump).
XL_MAX_COLS = 16384
XL_MAX_ROWS = 1_048_576


# ----------------------------------------------------------------------------- helpers
def _num(v):
    """Coerce a Screener cell to float (handles '1,234', '12%', '', '-')."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "—", "nan", "NaN", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _series_from_points(pts):
    """[[date,val],...] (val may be '' / str) -> a clean float Series indexed by date."""
    idx, vals = [], []
    for pt in pts or []:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            idx.append(pt[0]); vals.append(_num(pt[1]))
    if not idx:
        return None
    s = pd.Series(vals, index=pd.to_datetime(idx, errors="coerce"), dtype="float64")
    s = s[~s.index.isna()]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s if len(s) else None


def _to_parquet(df: pd.DataFrame, name: str) -> dict:
    """Write a Date-indexed (or any) frame to parquet; return a small manifest entry."""
    os.makedirs(PARQUET_DIR, exist_ok=True)
    path = os.path.join(PARQUET_DIR, name)
    out = df.copy()
    if out.index.name == "Date" or isinstance(out.index, pd.DatetimeIndex):
        out = out.reset_index()
        if out.columns[0] != "Date":
            out = out.rename(columns={out.columns[0]: "Date"})
    out.to_parquet(path, index=False)
    return {"file": f"parquet/{name}", "rows": int(len(out)), "cols": int(out.shape[1]),
            "mb": round(os.path.getsize(path) / 1e6, 2)}


def _xl_sheet(name: str) -> str:
    for ch in '/\\?*[]:':
        name = name.replace(ch, "-")
    return name[:31]


def _wide_to_excel(frames: dict, path: str, about: pd.DataFrame | None = None):
    """Write {sheet_name: Date-indexed wide df} to one workbook (Date as first column).
    For small/medium frames — pandas' default writer (fast, correct)."""
    os.makedirs(EXCEL_DIR, exist_ok=True)
    with pd.ExcelWriter(path, engine="xlsxwriter", date_format="yyyy-mm-dd",
                        datetime_format="yyyy-mm-dd") as xw:
        for sheet, df in frames.items():
            d = df.copy()
            if isinstance(d.index, pd.DatetimeIndex) or d.index.name == "Date":
                d = d.reset_index()
                if d.columns[0] != "Date":
                    d = d.rename(columns={d.columns[0]: "Date"})
            if d.shape[1] > XL_MAX_COLS:
                d = d.iloc[:, :XL_MAX_COLS]          # never exceed Excel's grid
            if d.shape[0] > XL_MAX_ROWS:
                d = d.iloc[:XL_MAX_ROWS, :]
            d.to_excel(xw, sheet_name=_xl_sheet(sheet), index=False)
        if about is not None:
            about.to_excel(xw, sheet_name="About", index=False)


def _wide_excel_streamed(df: pd.DataFrame, path: str, sheet: str):
    """Write a VERY wide Date-indexed frame (e.g. 2000+ stock columns) ROW-BY-ROW in
    xlsxwriter constant-memory mode. Row-major streaming is both memory-safe (only the
    current row is held) AND correct — pandas' own writer streams column-major, which
    silently drops all but the last row under constant_memory. Empty (NaN) cells are skipped."""
    import xlsxwriter
    os.makedirs(EXCEL_DIR, exist_ok=True)
    cols = list(df.columns)[:XL_MAX_COLS - 1]
    n = min(len(df), XL_MAX_ROWS - 1)
    wb = xlsxwriter.Workbook(path, {"constant_memory": True})
    ws = wb.add_worksheet(_xl_sheet(sheet))
    dfmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws.write(0, 0, "Date")
    for j, c in enumerate(cols):
        ws.write(0, j + 1, str(c))
    idx = df.index
    vals = df[cols].to_numpy()
    for i in range(n):
        d = idx[i]
        try:
            ws.write_datetime(i + 1, 0, d.to_pydatetime() if hasattr(d, "to_pydatetime") else d, dfmt)
        except Exception:
            ws.write(i + 1, 0, str(d))
        rowv = vals[i]
        for j in range(len(cols)):
            v = rowv[j]
            if v == v and v is not None:             # skip NaN/None
                ws.write_number(i + 1, j + 1, float(v))
    wb.close()


# ----------------------------------------------------------------------------- datasets
def _export_indices(manifest, log):
    measures = data.measures_present()
    frames = {}
    for m in measures:
        try:
            df = data.load(m).copy()
            df.index.name = "Date"
            frames[m] = df
            manifest["datasets"][f"indices_{m}"] = _to_parquet(df, f"indices_{m}.parquet")
            log(f"  indices {m}: {df.shape[1]} series x {df.shape[0]} dates")
        except Exception as e:
            log(f"  indices {m}: skipped ({e})")
    if frames:
        about = pd.DataFrame({
            "Field": ["Generated", "Measures", "Source"],
            "Value": [dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                      ", ".join(frames), "NSE total/price-return index levels (Vistas)"]})
        _wide_to_excel(frames, os.path.join(EXCEL_DIR, "Vistas Indices.xlsx"), about)
    return frames


def _export_one_plane(loader, name, xlsx, log):
    """Generic wide-plane export (stocks / world / macro)."""
    try:
        df = loader().copy()
    except Exception as e:
        log(f"  {name}: skipped ({e})"); return None
    if df is None or not len(df):
        log(f"  {name}: empty, skipped"); return None
    df.index.name = "Date"
    log(f"  {name}: {df.shape[1]} series x {df.shape[0]} dates")
    return df


def _export_fundamentals(manifest, log):
    from . import screener
    syms = screener.available()
    if not syms:
        log("  fundamentals: none cached, skipped"); return
    val_cols = {"Price to Earning": {}, "EPS": {}, "Median PE": {}}
    st_rows = []
    summ = []
    for i, sym in enumerate(syms):
        b = screener.load(sym)
        if not b or not b.get("ok", True):
            # cached error bundles have ok=False; still skip cleanly
            if not b:
                continue
        name = (b.get("name") or sym)
        val = b.get("valuation") or {}
        for label in val_cols:
            s = _series_from_points(val.get(label))
            if s is not None:
                val_cols[label][sym] = s
        # statements -> tidy/long rows
        for stmt, rows in (b.get("statements") or {}).items():
            for row in rows or []:
                keys = list(row.keys())
                if not keys:
                    continue
                line_item = str(row.get(keys[0])).strip()
                for period in keys[1:]:
                    st_rows.append((sym, name, stmt, line_item, str(period), _num(row.get(period))))
        # one-row summary (latest non-null of each headline series)
        def _last(group, label):
            s = _series_from_points((b.get(group) or {}).get(label))
            if s is None:
                return (None, None)
            s = s.dropna()
            return (float(s.iloc[-1]), s.index[-1].strftime("%Y-%m-%d")) if len(s) else (None, None)
        last_px, px_date = _last("price", "Price")
        last_pe, _ = _last("valuation", "Price to Earning")
        last_eps, _ = _last("valuation", "EPS")
        med_pe, _ = _last("valuation", "Median PE")
        summ.append({"symbol": sym, "name": name,
                     "consolidated": b.get("consolidated"), "fetched": b.get("fetched"),
                     "last_price": last_px, "last_price_date": px_date,
                     "last_PE": last_pe, "last_EPS": last_eps, "median_PE": med_pe})
        if (i + 1) % 300 == 0:
            log(f"    …parsed {i + 1}/{len(syms)} companies")

    # wide valuation frames (Date x company), like the stocks frame
    for label, key in [("Price to Earning", "pe"), ("EPS", "eps"), ("Median PE", "median_pe")]:
        cols = val_cols[label]
        if not cols:
            continue
        wide = pd.concat(cols, axis=1).sort_index()
        wide.index.name = "Date"
        manifest["datasets"][f"fundamentals_{key}"] = _to_parquet(wide, f"fundamentals_{key}.parquet")
        log(f"  fundamentals {label}: {wide.shape[1]} companies x {wide.shape[0]} dates")

    if st_rows:
        st_df = pd.DataFrame(st_rows, columns=["symbol", "name", "statement", "line_item", "period", "value"])
        manifest["datasets"]["fundamentals_statements"] = _to_parquet(st_df, "fundamentals_statements.parquet")
        log(f"  fundamentals statements (tidy): {len(st_df):,} rows")

    if summ:
        summ_df = pd.DataFrame(summ).sort_values("symbol").reset_index(drop=True)
        manifest["datasets"]["fundamentals_summary"] = _to_parquet(summ_df, "fundamentals_summary.parquet")
        _wide_to_excel({"Summary": summ_df.set_index("symbol")},
                       os.path.join(EXCEL_DIR, "Vistas Fundamentals (summary).xlsx"))
        log(f"  fundamentals summary: {len(summ_df)} companies")


# ----------------------------------------------------------------------------- orchestrate
def build_all(progress=None) -> dict:
    """Export every dataset to export/parquet (fast Python) + export/excel. Returns a manifest."""
    log = progress or (lambda m: print(m, flush=True))
    os.makedirs(PARQUET_DIR, exist_ok=True)
    os.makedirs(EXCEL_DIR, exist_ok=True)
    manifest = {"generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "datasets": {}}

    log("[1/5] indices (TR / PR / valuation, per measure)…")
    _export_indices(manifest, log)

    log("[2/5] stocks (adjusted price)…")
    from . import stocks
    sdf = _export_one_plane(stocks.load, "stocks", None, log)
    if sdf is not None:
        manifest["datasets"]["stocks"] = _to_parquet(sdf, "stocks.parquet")
        # wide stock workbook (2k+ cols) -> row-major streamed write (correct + memory-safe)
        _wide_excel_streamed(sdf, os.path.join(EXCEL_DIR, "Vistas Stocks.xlsx"), "Stocks (adj price)")

    log("[3/5] world / cross-asset…")
    from . import world
    wdf = _export_one_plane(world.load_named, "world", None, log)
    if wdf is not None:
        manifest["datasets"]["world"] = _to_parquet(wdf, "world.parquet")
        _wide_to_excel({"World": wdf}, os.path.join(EXCEL_DIR, "Vistas World.xlsx"))

    log("[4/5] India macro…")
    from . import macro
    mdf = _export_one_plane(macro.load, "macro", None, log)
    if mdf is not None:
        manifest["datasets"]["macro"] = _to_parquet(mdf, "macro.parquet")
        meta_rows = None
        try:
            mm = macro.meta() or {}
            if mm:
                meta_rows = pd.DataFrame([{"series": k, **(v if isinstance(v, dict) else {"value": v})}
                                          for k, v in mm.items()])
        except Exception:
            pass
        _wide_to_excel({"Macro": mdf}, os.path.join(EXCEL_DIR, "Vistas Macro.xlsx"), about=meta_rows)

    log("[5/5] fundamentals (Screener)…")
    _export_fundamentals(manifest, log)

    # manifest + README
    with open(os.path.join(EXPORT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    _write_readme(manifest)
    total = round(sum(d.get("mb", 0) for d in manifest["datasets"].values()), 1)
    log(f"\nDONE. {len(manifest['datasets'])} datasets -> {EXPORT_DIR}  (parquet total {total} MB)")
    return manifest


def _write_readme(manifest):
    lines = [
        "VISTAS LOCAL DATA DUMP",
        "=" * 60,
        f"Generated: {manifest['generated']}",
        "",
        "parquet/  fast columnar binaries — load in one line with pandas:",
        "            import pandas as pd",
        "            df = pd.read_parquet('export/parquet/indices_TR.parquet')",
        "          (Date is a normal column; set_index('Date') if you want it indexed.)",
        "          Also readable by R/arrow, DuckDB, Power BI, and Excel Power Query.",
        "",
        "excel/    multi-sheet .xlsx workbooks for hand analysis.",
        "          (For the 2000+ stock columns, prefer the parquet — Excel is slower.)",
        "",
        "DATASETS:",
    ]
    for k, d in manifest["datasets"].items():
        lines.append(f"  {k:28s} {d['file']:42s} {d['rows']:>9,} rows x {d['cols']:>5} cols  ({d['mb']} MB)")
    lines += [
        "",
        "NOTES",
        "  * indices_<M>     wide, Date x index — M = TR (total return) / PR / PE / PB / DY (when pulled).",
        "  * stocks          wide, Date x stock — adjusted close (≈ a stock's total-return level).",
        "  * world           wide, Date x instrument — global equity/commodity/FX/rates/crypto.",
        "  * macro           wide, Date x series — India CPI/WPI/IIP/rates/yields/FII-DII + global.",
        "  * fundamentals_pe/eps/median_pe  wide, Date x company (Screener valuation history).",
        "  * fundamentals_statements        tidy/long: symbol,name,statement,line_item,period,value.",
        "  * fundamentals_summary           one row per company: latest price/PE/EPS/median PE.",
    ]
    with open(os.path.join(EXPORT_DIR, "README.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    build_all()
