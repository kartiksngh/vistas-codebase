"""Extend the consolidated holdings STORE (parquet) with the Nov'25 -> May'26 Cline monthlies.

Reuses the EXACT schema + Co_Code->vst_id master from _build_history_store.py. The store already
holds Jan'10..Oct'25 (source=cline_concat_dec25); these 7 monthlies are the natural continuation
from the same Capitaline backbone (Oct'25 reconcile = 100% scheme overlap, verified 2026-06-24).

Policy: preserve raw + provenance; flag unresolved co_codes (id_conf='unresolved'), never drop.
Build-small: run with --month Nov25 to inspect ONE month (no write); --all to append all 7 + write.
"""
import os, sys, json, argparse
import pandas as pd

ROOT = r"C:\Users\Administrator\Documents\Projects\Vistas"
SRC  = (r"C:\Users\Administrator\Documents\Consolidated reverse Dumps"
        r"\June 23, 2026 - Historical port, Moneyball, Top1000"
        r"\Historical Portfolio Dump  - June 23 2026\Cline portfolios July'25 to May'26")
MAP_JSON = os.path.join(ROOT, "data", "funds", "_history_identity_map.json")
PARQUET  = os.path.join(ROOT, "data", "funds", "history", "holdings_history.parquet")

# the 7 monthlies to APPEND (Jul25..Oct25 already in store). Chronological.
APPEND_MONTHS = ["Nov25", "Dec25", "Jan26", "Feb26", "Mar26", "Apr26", "May26"]

# new-file (whitespace-normalized) column -> store column
COLMAP = {
    "NAVIndia Code": "navindia_code",
    "Scheme Name": "scheme_name",
    "Name of the Mutual Fund Name": "amc",
    "Investment Type": "investment_type",
    "Co_Code": "co_code",
    "Reported ISIN": "reported_isin",
    "Portfolio Company Name": "company_name",
    "No of shares": "shares",
    "Market value": "market_value",
    "% of holding in scheme": "pct",
    "Sebi Category": "sebi_category",
    "Scheme Portfolio Date": "_date",
}
STORE_COLS = ["period_date","ym","navindia_code","scheme_name","amc","investment_type",
              "co_code","reported_isin","final_isin_his","company_name","shares","market_value",
              "pct","sebi_category","vst_id","id_conf","vid_name","nse_symbol","source"]
SOURCE_TAG = "cline_monthly_jun26"


def load_master():
    m = json.load(open(MAP_JSON, encoding="utf-8"))["master"]
    # keys are co_code strings; values carry vst_id/name/nse_symbol/conf
    return {str(c): v for c, v in m.items()}


def parse_month(mon, master):
    f = os.path.join(SRC, f"MF Data - {mon}.xlsx")
    df = pd.read_excel(f, dtype=str)
    # normalize column whitespace (handles 'Reported  ISIN' double-space across files)
    df.columns = [" ".join(str(c).split()) for c in df.columns]
    missing = [c for c in COLMAP if c not in df.columns]
    if missing:
        raise SystemExit(f"{mon}: MISSING expected columns {missing}\n got: {list(df.columns)}")
    df = df.rename(columns=COLMAP)
    out = pd.DataFrame(index=df.index)
    out["navindia_code"]   = df["navindia_code"].astype(str).str.strip()
    out["scheme_name"]     = df["scheme_name"]
    out["amc"]             = df["amc"]
    out["investment_type"] = df["investment_type"]
    out["co_code"]         = df["co_code"].astype(str).str.strip()
    out["reported_isin"]   = df["reported_isin"]
    out["final_isin_his"]  = ""                      # absent in monthly files
    out["company_name"]    = df["company_name"]
    out["shares"]          = pd.to_numeric(df["shares"], errors="coerce")
    out["market_value"]    = pd.to_numeric(df["market_value"], errors="coerce")
    out["pct"]             = pd.to_numeric(df["pct"], errors="coerce")
    out["sebi_category"]   = df["sebi_category"]
    # Scheme Portfolio Date arrives in TWO encodings WITHIN the same file: most rows as a
    # formatted datetime string ('2025-12-31 00:00:00'), but ~180-223 schemes/month in the
    # Dec25-Mar26 vendor files store it as a raw Excel SERIAL NUMBER ('46022' = days since
    # 1899-12-30). The string parse reads the former and yields NaT on the latter; without
    # recovery those whole schemes (full holdings, only the date cell mis-encoded) were
    # silently dropped as "junk", hollowing those months to ~520-565 schemes. Decode the
    # serials so the rows are KEPT — verified to decode EXACTLY to the file's month-end.
    d = pd.to_datetime(df["_date"].str.slice(0, 10), errors="coerce")
    serial = pd.to_numeric(df["_date"], errors="coerce")              # real datetime strings -> NaN
    need = d.isna() & serial.notna() & serial.between(20000, 80000)   # plausible Excel date serials (~1954..2089)
    if need.any():
        d = d.fillna(pd.to_datetime(serial.where(need), unit="D", origin="1899-12-30"))
        print(f"   [{mon}] recovered {int(need.sum())} rows from Excel-serial dates")
    out["period_date"]     = d.dt.date
    out["ym"]              = d.dt.strftime("%Y-%m")
    # DROP rows that cannot be placed in time or have no scheme key (footer/junk/disclaimer rows).
    bad = d.isna() | out["navindia_code"].isin(["", "nan", "None"]) | out["navindia_code"].isna()
    if bad.any():
        print(f"   [{mon}] dropped {int(bad.sum())} junk rows (no valid date / no scheme code)")
        out = out.loc[~bad].copy()
    # identity join
    mp = out["co_code"].map(master)
    out["vst_id"]     = mp.map(lambda v: v.get("vst_id") if isinstance(v, dict) else None)
    out["id_conf"]    = mp.map(lambda v: v.get("conf") if isinstance(v, dict) else None).fillna("unresolved")
    out["vid_name"]   = mp.map(lambda v: v.get("name") if isinstance(v, dict) else None)
    out["nse_symbol"] = mp.map(lambda v: v.get("nse_symbol") if isinstance(v, dict) else None)
    out["source"]     = SOURCE_TAG
    out = out[STORE_COLS]
    # dedup: one row per (period_date, navindia_code, reported_isin), keep max market_value
    out = (out.sort_values("market_value", ascending=False, na_position="last")
              .drop_duplicates(["period_date","navindia_code","reported_isin"], keep="first")
              .sort_values(["period_date","navindia_code"]).reset_index(drop=True))
    return out


def diag(mon, df, master):
    n_sch = df["navindia_code"].nunique()
    n_cc  = df["co_code"].nunique()
    n_new_cc = sum(1 for c in df["co_code"].unique() if c not in master)
    eq = df[df["investment_type"].str.lower().str.contains("equity", na=False)]
    vr = 100.0 * eq.loc[eq["vst_id"].notna(), "market_value"].sum() / max(eq["market_value"].sum(), 1e-9)
    # %-sum per scheme-month (equity sleeve)
    s = eq.groupby("navindia_code")["pct"].sum()
    in_band = 100.0 * ((s >= 90) & (s <= 102)).mean() if len(s) else 0.0
    ymrng = f"{df['ym'].min()}..{df['ym'].max()}"
    print(f"[{mon}] rows={len(df):,}  schemes={n_sch}  co_codes={n_cc}  "
          f"new(unmapped)_co_codes={n_new_cc}  equity_val_resolved={vr:.1f}%  "
          f"eq %-sum in[90,102]={in_band:.0f}%  ym={ymrng}")
    return n_sch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="inspect one month (no write), e.g. Nov25")
    ap.add_argument("--all", action="store_true", help="append all 7 months + write store")
    args = ap.parse_args()
    master = load_master()
    print(f"master co_codes: {len(master)}")

    if args.month:
        df = parse_month(args.month, master)
        diag(args.month, df, master)
        print("\n--- sample rows ---")
        print(df.head(3).to_string())
        print("\n(build-small: NO write)")
        return

    if args.all:
        store = pd.read_parquet(PARQUET)
        print(f"STORE before: rows={len(store):,}  ym {store.ym.min()}..{store.ym.max()}  "
              f"months={store.ym.nunique()}  schemes={store.navindia_code.nunique()}")
        existing_ym = set(store["ym"].unique())
        parts = []
        for mon in APPEND_MONTHS:
            df = parse_month(mon, master)
            diag(mon, df, master)
            overlap = set(df["ym"].unique()) & existing_ym
            if overlap:
                raise SystemExit(f"ABORT: {mon} ym {overlap} already in store (would double-count)")
            parts.append(df)
        new = pd.concat(parts, ignore_index=True)
        # align dtypes to store
        new["period_date"] = pd.to_datetime(new["period_date"]).dt.date if new["period_date"].dtype == object else new["period_date"]
        combined = pd.concat([store, new], ignore_index=True)
        # backup once
        bak = PARQUET + ".bak"
        if not os.path.exists(bak):
            store.to_parquet(bak, compression="zstd", index=False)
            print(f"backup written -> {bak}")
        tmp = PARQUET + ".tmp"
        combined.to_parquet(tmp, compression="zstd", index=False)
        os.replace(tmp, PARQUET)
        print(f"\nSTORE after:  rows={len(combined):,}  ym {combined.ym.min()}..{combined.ym.max()}  "
              f"months={combined.ym.nunique()}  schemes={combined.navindia_code.nunique()}")
        print(f"appended {len(new):,} rows across {len(APPEND_MONTHS)} months -> {PARQUET}")
        return

    print("nothing to do; pass --month <Mon> or --all")


if __name__ == "__main__":
    main()
