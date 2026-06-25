"""Build the consolidated historical holdings STORE (parquet) from the 13-year Cline concat,
joined through the verified Co_Code->vst_id master. Measures dedup, survivorship, %-sum sanity,
and value-resolved in the same pass. Out-of-core via DuckDB. Preserve raw + provenance; flag, never drop.
"""
import os, json, duckdb, pandas as pd
import pyarrow as pa, pyarrow.parquet as pq

ROOT = r"C:\Users\Administrator\Documents\Projects\Vistas"
OUTDIR = os.path.join(ROOT, "data", "funds", "history")
os.makedirs(OUTDIR, exist_ok=True)
CSV = r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Update December 2025\Portfolio Update\Cline Portfolio Data Jan'10 to Oct'25 - updated Dec'25.csv"
MAP_JSON = os.path.join(ROOT, "data", "funds", "_history_identity_map.json")
MAP_CSV  = os.path.join(OUTDIR, "cocode_vid_map.csv")
PARQUET  = os.path.join(OUTDIR, "holdings_history.parquet")

# 1) materialize the Co_Code->vst_id master as a joinable table
m = json.load(open(MAP_JSON, encoding="utf-8"))["master"]
map_df = pd.DataFrame([{"co_code": str(c), "vst_id": v["vst_id"], "id_conf": v["conf"],
                        "vid_name": v.get("name"), "nse_symbol": v.get("nse_symbol")}
                       for c, v in m.items()])
map_df.to_csv(MAP_CSV, index=False)
print(f"wrote {len(m)} Co_Code->vst_id rows -> {MAP_CSV}")

# 2) pandas (proven on this file) -> raw parquet, so DuckDB's strict CSV parser never sees the
#    non-RFC4180 rows (embedded commas in company names). One typed parquet, fast to re-query.
RAW_PARQUET = os.path.join(OUTDIR, "_raw_concat.parquet")
if not os.path.exists(RAW_PARQUET):
    writer = None; nraw = 0
    for ch in pd.read_csv(CSV, dtype=str, chunksize=300000):
        ch = ch.fillna("")
        t = pa.Table.from_pandas(ch, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(RAW_PARQUET, t.schema)
        writer.write_table(t); nraw += len(ch)
    writer.close()
    print(f"raw -> parquet: {nraw:,} rows -> {RAW_PARQUET}")
else:
    print(f"raw parquet exists -> {RAW_PARQUET} (skip rebuild)")

def q(p):
    return p.replace(chr(92), "/").replace("'", "''")

con = duckdb.connect()
con.execute("PRAGMA threads=4")
con.register("cmap", map_df)
con.execute(f"""CREATE TABLE raw AS SELECT * FROM read_parquet('{q(RAW_PARQUET)}')""")
print("raw rows:", con.execute("SELECT count(*) FROM raw").fetchone()[0])

con.execute(f"""
CREATE TABLE holdings AS
SELECT
  TRY_CAST(substr("Scheme Portfolio Date",1,10) AS DATE)                       AS period_date,
  strftime(TRY_CAST(substr("Scheme Portfolio Date",1,10) AS DATE), '%Y-%m')    AS ym,
  CAST("NAVIndia Code" AS VARCHAR)        AS navindia_code,
  "Scheme Name"                            AS scheme_name,
  "Name of the Mutual Fund Name"           AS amc,
  "Investment Type"                        AS investment_type,
  CAST(r."Co_Code" AS VARCHAR)             AS co_code,
  "Reported ISIN"                          AS reported_isin,
  "Final ISIN"                             AS final_isin_his,
  "Portfolio Company Name"                 AS company_name,
  TRY_CAST("No of shares" AS DOUBLE)       AS shares,
  TRY_CAST("Market value" AS DOUBLE)       AS market_value,
  TRY_CAST("% of holding in scheme" AS DOUBLE) AS pct,
  "Sebi Category"                          AS sebi_category,
  m.vst_id, COALESCE(m.id_conf,'unresolved') AS id_conf, m.vid_name, m.nse_symbol,
  'cline_concat_dec25'                     AS source
FROM raw r LEFT JOIN cmap m ON CAST(r."Co_Code" AS VARCHAR) = m.co_code
""")

tot = con.execute("SELECT count(*) FROM holdings").fetchone()[0]
dist = con.execute("SELECT count(*) FROM (SELECT DISTINCT period_date,navindia_code,reported_isin FROM holdings)").fetchone()[0]
print(f"\nrows={tot:,}  distinct(date,scheme,isin)={dist:,}  exact-key dups={tot-dist:,}")

# DEDUPE: keep one row per (date, scheme, reported_isin) — the max market_value (defensive; dups~0 expected)
con.execute("""CREATE TABLE holdings_dd AS
  SELECT * EXCLUDE rn FROM (
    SELECT *, row_number() OVER (PARTITION BY period_date,navindia_code,reported_isin
                                 ORDER BY market_value DESC NULLS LAST) rn FROM holdings) WHERE rn=1""")
ddn = con.execute("SELECT count(*) FROM holdings_dd").fetchone()[0]
print(f"after dedupe: {ddn:,} rows")

con.execute(f"""COPY (SELECT * FROM holdings_dd ORDER BY period_date, navindia_code)
               TO '{PARQUET.replace(chr(92),'/')}' (FORMAT parquet, COMPRESSION zstd)""")
sz = os.path.getsize(PARQUET)/1e6
print(f"wrote {PARQUET}  ({sz:.0f} MB)")

print("\n=== STORE SUMMARY ===")
for q, lab in [
  ("SELECT min(period_date),max(period_date),count(DISTINCT ym) FROM holdings_dd", "span (min,max,n_months)"),
  ("SELECT count(DISTINCT navindia_code), count(DISTINCT amc) FROM holdings_dd", "n_schemes, n_amc"),
  ("SELECT investment_type, count(*) c FROM holdings_dd GROUP BY 1 ORDER BY c DESC LIMIT 6", "investment_type"),
  ("SELECT id_conf, count(*) c FROM holdings_dd GROUP BY 1 ORDER BY c DESC", "id_conf dist"),
]:
    print(f"\n[{lab}]"); print(con.execute(q).df().to_string(index=False))

# value resolved (equity)
vr = con.execute("""SELECT
  100.0*sum(CASE WHEN vst_id IS NOT NULL THEN market_value ELSE 0 END)/NULLIF(sum(market_value),0) AS pct_val_resolved
  FROM holdings_dd WHERE lower(investment_type) LIKE '%equity%'""").fetchone()[0]
print(f"\n[equity value resolved to vst_id]: {vr:.2f}%")

# SURVIVORSHIP: schemes whose LAST month is before the data end (= closed/merged, present = good)
con.execute("CREATE TABLE sch AS SELECT navindia_code, any_value(scheme_name) nm, min(ym) first_m, max(ym) last_m FROM holdings_dd GROUP BY 1")
end = con.execute("SELECT max(ym) FROM holdings_dd").fetchone()[0]
dead = con.execute(f"SELECT count(*) FROM sch WHERE last_m < '{end}'").fetchone()[0]
tot_s = con.execute("SELECT count(*) FROM sch").fetchone()[0]
print(f"\n[survivorship] schemes total={tot_s}  ENDED before {end} (dead/merged, present=good): {dead} "
      f"({100*dead/tot_s:.0f}%)")
print(con.execute(f"SELECT nm, first_m, last_m FROM sch WHERE last_m < '{end}' ORDER BY last_m LIMIT 8").df().to_string(index=False))

# %-sum sanity per (scheme, month) for EQUITY sleeve
print("\n[equity %-sum per scheme-month: distribution]")
print(con.execute("""SELECT
  round(median(s),1) med, round(min(s),1) lo, round(max(s),1) hi,
  100.0*sum(CASE WHEN s BETWEEN 90 AND 102 THEN 1 ELSE 0 END)/count(*) pct_in_90_102
  FROM (SELECT navindia_code,ym,sum(pct) s FROM holdings_dd WHERE lower(investment_type) LIKE '%equity%' GROUP BY 1,2)
""").df().to_string(index=False))
con.close()
