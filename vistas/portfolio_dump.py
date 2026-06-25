"""Build a SELF-CONTAINED, self-explaining monthly dump of the full mutual-fund
portfolio history from the consolidated holdings store.

WHAT IT PRODUCES (one folder, auto-named from the data's coverage):

    All_funds_portfolio_<FirstMon><YY>_<LastMon><YY>/
        All_funds_portfolio_<...>.parquet   full holdings panel (every month, every
                                            scheme, every holding, all identifiers).
                                            Carries an embedded data dictionary in the
                                            parquet file-level metadata, so the file
                                            explains itself even with no other file.
        identity_history.parquet            per vst_id: every (co_code, ISIN, name)
                                            variant it ever appeared under + when
                                            (= the historical identifier changes:
                                            renames / ISIN changes / M&A bridging).
        scheme_history.parquet              per scheme: every (name, AMC, SEBI category)
                                            variant over time (scheme renames/recategorisations).
        _DATA_DICTIONARY.json               machine-readable column dictionary.
        README.md                           human-readable: grain, columns+units, the
                                            identifier model, how to load / convert, the
                                            monthly roll-forward convention, caveats.

MONTHLY ROLL-FORWARD: the folder/file name is derived from the data's first & last
month, so next month (once the store is extended) re-running this auto-produces
`All_funds_portfolio_Apr13_Jun26/` with the full cumulative history. No manual rename.

Run:  python vistas/portfolio_dump.py            (defaults: corrected store -> project root)
      python vistas/portfolio_dump.py --out-root D:/some/dir
"""
import os, sys, json, argparse, calendar, datetime
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DEFAULT_STORE = os.path.join(_ROOT, "data", "funds", "history", "holdings_history.parquet")

# ── column dictionary (verified: market_value=Rs crore, pct=% of TOTAL scheme NAV) ──
DATA_DICTIONARY = {
    "period_date":     {"meaning": "Portfolio snapshot date (the month-end the holding was reported as of).", "unit": "date (YYYY-MM-DD)", "example": "2026-05-31"},
    "ym":              {"meaning": "Year-month of the snapshot (a convenience key for grouping).", "unit": "string YYYY-MM", "example": "2026-05"},
    "navindia_code":   {"meaning": "Scheme identifier from the NAVIndia/Capitaline vendor universe. One mutual-fund scheme = one code.", "unit": "string id", "example": "44093"},
    "scheme_name":     {"meaning": "Scheme name AS REPORTED that month (can change over time -> see scheme_history.parquet).", "unit": "text", "example": "Nippon India Small Cap Fund - (G)"},
    "amc":             {"meaning": "Asset Management Company (fund house) running the scheme, as reported that month.", "unit": "text", "example": "Nippon India Mutual Fund"},
    "investment_type": {"meaning": "Asset class of the holding row (Equity / Debt / Cash & equivalents / etc.).", "unit": "category", "example": "Equity"},
    "co_code":         {"meaning": "Capitaline company code = the VENDOR's id for the held company. Stable for an entity but vendor-specific; a single real company can carry >1 over corporate actions -> use vst_id as the spine.", "unit": "string id", "example": "476"},
    "reported_isin":   {"meaning": "ISIN of the held security AS REPORTED that month (the market identifier; can change on re-issue/merger).", "unit": "ISIN", "example": "INE002A01018"},
    "final_isin_his":  {"meaning": "Resolved/canonical ISIN from the Bloomberg-history identity bridge where available; blank for monthly-only rows that were not in the BBG back-fill.", "unit": "ISIN or ''", "example": "INE002A01018"},
    "company_name":    {"meaning": "Held company's name AS REPORTED that month (vendor spelling; can vary -> see identity_history.parquet).", "unit": "text", "example": "Reliance Industries Ltd"},
    "shares":          {"meaning": "Number of shares of the security held by the scheme. RAW count, NOT adjusted for splits/bonuses, so a 1:1 bonus doubles this with no actual buying (relevant if you difference shares across months).", "unit": "share count", "example": "2078812"},
    "market_value":    {"meaning": "Market value of the holding (shares x market price). VERIFIED unit = Rupees CRORE (cross-checked: per-share prices reconcile to live prices, and the all-scheme equity total ~Rs 40 lakh crore matches India's active-equity MF AUM).", "unit": "Rs crore", "example": "326.4566"},
    "pct":             {"meaning": "Weight of this holding as a % of the scheme's TOTAL portfolio (all asset classes, not just equity). So the equity rows of a hybrid scheme sum to <100; a pure-equity scheme's equity rows sum to ~95-100.", "unit": "percent of scheme NAV", "example": "5.6068"},
    "sebi_category":   {"meaning": "SEBI scheme category as reported that month.", "unit": "category", "example": "Flexi Cap Fund"},
    "vst_id":          {"meaning": "VISTAS stable identity surrogate key for the held company = the SPINE. Unchanging across renames / ISIN changes / mergers/acquisitions, so you can track one real company through its whole history regardless of how the vendor labelled it. Blank/unresolved for a small tail (recent IPOs, unlisted/pre-IPO holdings) with no traded-returns identity yet.", "unit": "string id (VSTxxxxx)", "example": "VST03868"},
    "id_conf":         {"meaning": "Confidence of the company identity resolution (high/med/low/unresolved).", "unit": "category", "example": "high"},
    "vid_name":        {"meaning": "Canonical company name attached to the vst_id (Vistas' resolved, de-duplicated name).", "unit": "text", "example": "Reliance Industries Ltd"},
    "nse_symbol":      {"meaning": "NSE ticker for the company where listed & resolved (blank if unlisted / not yet resolved).", "unit": "NSE symbol", "example": "RELIANCE"},
    "source":          {"meaning": "Provenance tag for the row's origin: 'cline_concat_dec25' = the 2013-2025 Capitaline back-fill; 'cline_monthly_jun26' = the forward monthly Capitaline appends.", "unit": "category", "example": "cline_monthly_jun26"},
}


def _tag(ym):
    """'2013-04' -> 'Apr13'."""
    y, m = ym.split("-")
    return f"{calendar.month_abbr[int(m)]}{y[2:]}"


def build_dump(store_path=DEFAULT_STORE, out_root=_ROOT, build_date=None):
    build_date = build_date or datetime.date.today().isoformat()
    df = pd.read_parquet(store_path)
    df = df.sort_values(["ym", "navindia_code", "pct"], ascending=[True, True, False]).reset_index(drop=True)

    ymin, ymax = df["ym"].min(), df["ym"].max()
    name = f"All_funds_portfolio_{_tag(ymin)}_{_tag(ymax)}"
    outdir = os.path.join(out_root, name)
    os.makedirs(outdir, exist_ok=True)

    # ── summary stats (for README + embedded about) ──
    eq = df[df["investment_type"].astype(str).str.strip().str.lower() == "equity"]
    stats = {
        "coverage_first_month": ymin,
        "coverage_last_month": ymax,
        "n_months": int(df["ym"].nunique()),
        "n_rows": int(len(df)),
        "n_schemes": int(df["navindia_code"].nunique()),
        "n_amcs": int(df["amc"].nunique()),
        "n_companies_vst_id": int(df["vst_id"].nunique(dropna=True)),
        "n_equity_rows": int(len(eq)),
        "equity_value_resolved_pct_latest": round(
            100.0 * eq.loc[(eq["ym"] == ymax) & eq["vst_id"].notna(), "market_value"].sum()
            / max(eq.loc[eq["ym"] == ymax, "market_value"].sum(), 1e-9), 2),
        "asset_types": {str(k): int(v) for k, v in df["investment_type"].value_counts().items()},
        "sources": {str(k): int(v) for k, v in df["source"].value_counts().items()},
        "build_date": build_date,
        "store_source_file": os.path.basename(store_path),
    }

    # ── 1) main panel with embedded self-describing metadata ──
    main_path = os.path.join(outdir, f"{name}.parquet")
    table = pa.Table.from_pandas(df, preserve_index=False)
    meta = dict(table.schema.metadata or {})
    meta[b"vistas_about"] = (
        "Full mutual-fund portfolio history (all schemes, all holdings, all months). "
        "One row = one scheme's holding of one security on one month-end snapshot. "
        "Self-describing: see the vistas_data_dictionary key for column meanings/units. "
        "Companion files identity_history.parquet & scheme_history.parquet record how "
        "identifiers changed over time. Regenerated monthly with the next month appended."
    ).encode()
    meta[b"vistas_data_dictionary"] = json.dumps(DATA_DICTIONARY, indent=2).encode()
    meta[b"vistas_stats"] = json.dumps(stats, indent=2).encode()
    table = table.replace_schema_metadata(meta)
    pq.write_table(table, main_path, compression="zstd")

    # ── 2) identity_history: every identifier variant per company, with when ──
    ih = (df.groupby(["vst_id", "co_code", "reported_isin", "company_name"], dropna=False)
            .agg(first_ym=("ym", "min"), last_ym=("ym", "max"),
                 n_months=("ym", "nunique"), n_rows=("ym", "size"))
            .reset_index())
    # attach canonical name/symbol + flag entities whose identifiers changed over time
    canon = (df.sort_values("ym").groupby("vst_id")
               .agg(vid_name=("vid_name", "last"), nse_symbol=("nse_symbol", "last"),
                    id_conf=("id_conf", "last")).reset_index())
    ih = ih.merge(canon, on="vst_id", how="left")
    chg = (ih.groupby("vst_id")
             .agg(n_isin=("reported_isin", "nunique"), n_coco=("co_code", "nunique"),
                  n_name=("company_name", "nunique")).reset_index())
    chg["identity_changed"] = (chg[["n_isin", "n_coco", "n_name"]].max(axis=1) > 1)
    ih = ih.merge(chg[["vst_id", "identity_changed"]], on="vst_id", how="left")
    ih = ih.sort_values(["vst_id", "first_ym"]).reset_index(drop=True)
    ih.to_parquet(os.path.join(outdir, "identity_history.parquet"), compression="zstd", index=False)

    # ── 3) scheme_history: name/AMC/category variants per scheme over time ──
    sh = (df.groupby(["navindia_code", "scheme_name", "amc", "sebi_category"], dropna=False)
            .agg(first_ym=("ym", "min"), last_ym=("ym", "max"), n_months=("ym", "nunique"))
            .reset_index().sort_values(["navindia_code", "first_ym"]).reset_index(drop=True))
    sh.to_parquet(os.path.join(outdir, "scheme_history.parquet"), compression="zstd", index=False)

    # ── 4) machine-readable data dictionary ──
    with open(os.path.join(outdir, "_DATA_DICTIONARY.json"), "w", encoding="utf-8") as f:
        json.dump({"columns": DATA_DICTIONARY, "stats": stats}, f, indent=2)

    # ── 5) README ──
    _write_readme(outdir, name, stats, ih, sh)
    return outdir, stats


def _write_readme(outdir, name, s, ih, sh):
    n_changed = int(ih.drop_duplicates("vst_id")["identity_changed"].sum())
    at = "\n".join(f"  - {k}: {v:,} rows" for k, v in s["asset_types"].items())
    src = "\n".join(f"  - {k}: {v:,} rows" for k, v in s["sources"].items())
    readme = f"""# {name}

**Full mutual-fund portfolio history — self-contained, self-explaining.**

Coverage **{s['coverage_first_month']} -> {s['coverage_last_month']}** ({s['n_months']} monthly
snapshots). Built {s['build_date']} from `{s['store_source_file']}`.

| | |
|---|---|
| Rows (holdings) | {s['n_rows']:,} |
| Schemes | {s['n_schemes']:,} |
| AMCs (fund houses) | {s['n_amcs']:,} |
| Distinct companies (vst_id) | {s['n_companies_vst_id']:,} |
| Equity rows | {s['n_equity_rows']:,} |
| Equity value resolved to a company identity (latest month) | {s['equity_value_resolved_pct_latest']}% |

## The grain (what one row is)
One row = **one scheme's holding of one security, on one month-end snapshot**.
So `(ym, navindia_code, reported_isin)` is the natural key.

## Files in this folder
- **`{name}.parquet`** — the full panel. Self-describing: a data dictionary is embedded
  in the parquet file-level metadata (keys `vistas_data_dictionary`, `vistas_about`, `vistas_stats`).
- **`identity_history.parquet`** — *the historical changes companies went through.* Per `vst_id`,
  every `(co_code, reported_isin, company_name)` variant it ever appeared under, with `first_ym`/
  `last_ym`/`n_months`, and an `identity_changed` flag. {n_changed:,} companies show >1 variant
  (renames, ISIN re-issues, mergers/acquisitions bridged onto one stable identity).
- **`scheme_history.parquet`** — per scheme, every `(scheme_name, amc, sebi_category)` variant over
  time (scheme renames / re-categorisations / AMC changes).
- **`_DATA_DICTIONARY.json`** — the same column dictionary + stats, machine-readable.

## The identifier model (3 layers)
1. **Vendor ids** — `navindia_code` (scheme), `co_code` (company, Capitaline). Vendor-specific.
2. **Market ids** — `reported_isin` / `final_isin_his` (security), `nse_symbol` (ticker).
3. **`vst_id` = the SPINE** — Vistas' own stable surrogate key for a company, **unchanging across
   renames / ISIN changes / M&A**. Use it to follow one real company through its whole history no
   matter how the vendor labelled it month to month. (A small tail is unresolved: recent IPOs and
   genuinely-unlisted/pre-IPO holdings that have no traded-returns identity yet.)

## Asset types
{at}

## Provenance (`source`)
{src}

## Load it in Python
```python
import pandas as pd
df = pd.read_parquet("{name}.parquet")            # the full panel

# read the embedded, self-describing data dictionary:
import pyarrow.parquet as pq, json
md = pq.read_metadata("{name}.parquet").metadata
print(json.loads(md[b"vistas_data_dictionary"]))

ident = pd.read_parquet("identity_history.parquet")   # how company identifiers changed
schemes = pd.read_parquet("scheme_history.parquet")    # how scheme labels changed
```

## Convert to CSV / Excel
```python
df.to_csv("{name}.csv", index=False)               # fine (no row limit)
```
**Excel caveat:** the full panel has {s['n_rows']:,} rows — far beyond Excel's ~1,048,576-row
sheet limit, so a single `.xlsx` of the whole panel is **not possible**. Use CSV, or split by
year/AMC into multiple sheets if you need `.xlsx`.

## Key conventions (so numbers are reproducible)
- **`market_value` is in Rupees CRORE** (verified two ways: per-share prices reconcile to live
  prices, and the all-scheme equity total ~Rs 40 lakh crore matches India's active-equity MF AUM).
- **`pct` is the holding's weight in the scheme's TOTAL portfolio** (all asset classes) — so the
  equity rows of a hybrid scheme sum to <100; a pure-equity scheme sums to ~95-100.
- **`shares` is a RAW count, not split/bonus-adjusted** — differencing shares across months mixes
  real buying/selling with corporate actions (a 1:1 bonus doubles shares with zero trading).

## Monthly roll-forward
The folder/file name is derived from the data's first & last month. Next month, once the holdings
store is extended with the new month, re-running `python vistas/portfolio_dump.py` auto-produces a
fresh folder with the same start month and the new end month (this dump ends `{s['coverage_last_month']}`;
next becomes `_Jun26`, and so on) — a full cumulative dump each month. No manual rename needed.
"""
    with open(os.path.join(outdir, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--out-root", default=_ROOT)
    a = ap.parse_args()
    outdir, stats = build_dump(a.store, a.out_root)
    print(f"wrote dump -> {outdir}")
    print(json.dumps(stats, indent=2))
