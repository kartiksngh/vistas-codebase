"""Monthly TOTAL-RETURN store for attribution: melt the BBG TR price matrix (cached monthly) -> long,
join to our vst_id, compute monthly TR returns per security. Historical TR = Bloomberg (approved,
publishable, verified vs our NSE to 0.15bp ex-div); delisted names keyed by ISIN/ticker so they stay
attributable. Forward TR will be computed from our own NSE (price + div reinvested) — separate build."""
import os, numpy as np, pandas as pd

H = r"C:\Users\Administrator\Documents\Projects\Vistas\data\funds\history"
CACHE = os.path.join(H, "_bbg_monthly_px.parquet")          # BBG monthly TR prices (date x bbg_ticker)
BRIDGE = os.path.join(H, "bbg_identity_bridge.csv")
OUT = os.path.join(H, "tr_returns_monthly.parquet")

pxm = pd.read_parquet(CACHE); pxm.index = pd.to_datetime(pxm.index)
br = pd.read_csv(BRIDGE)
v = {t: x for t, x in zip(br["bbg_ticker"], br["vst_id"]) if isinstance(x, str) and x}
fi = {t: x for t, x in zip(br["bbg_ticker"], br["final_isin"]) if isinstance(x, str) and x}
sy = {t: x for t, x in zip(br["bbg_ticker"], br["nse_symbol"]) if isinstance(x, str) and x}

long = (pxm.rename_axis("date").reset_index()
        .melt(id_vars="date", var_name="bbg_ticker", value_name="tr_price"))
long = long.dropna(subset=["tr_price"])
long["tr_price"] = pd.to_numeric(long["tr_price"], errors="coerce")
long = long.dropna(subset=["tr_price"])
long["vst_id"] = long["bbg_ticker"].map(v)
long["nse_symbol"] = long["bbg_ticker"].map(sy)
# stable join key: our vst_id if known, else ISIN, else the BBG ticker — so delisted names stay attributable
long["key"] = long["vst_id"].fillna(long["bbg_ticker"].map(fi).map(lambda z: f"ISIN:{z}" if isinstance(z,str) and z else None))
long["key"] = long["key"].fillna("BBG:" + long["bbg_ticker"].astype(str))
long = long.sort_values(["key", "date"])
long["ret_1m"] = long.groupby("key")["tr_price"].pct_change()

long.to_parquet(OUT, index=False)
n = len(long); nk = long["key"].nunique()
print(f"TR returns store -> {OUT}")
print(f"  rows={n:,}  series(keys)={nk:,}  span={long.date.min().date()}..{long.date.max().date()}")
print(f"  keyed by our vst_id: {long.vst_id.notna().sum():,} rows ({long[long.vst_id.notna()].key.nunique()} vst_ids)")
print(f"  keyed by ISIN (delisted, no vst_id): {long.key.str.startswith('ISIN:').sum():,} rows "
      f"({long[long.key.str.startswith('ISIN:')].key.nunique()} names)")
print(f"  keyed by BBG ticker only: {long.key.str.startswith('BBG:').sum():,} rows "
      f"({long[long.key.str.startswith('BBG:')].key.nunique()} names)")
# sanity: monthly TR return distribution (should be sane equity monthly returns)
r = long["ret_1m"].dropna()
print(f"\n  monthly TR return sanity: median={r.median():.4f}  mean={r.mean():.4f}  "
      f"p1={r.quantile(.01):.3f}  p99={r.quantile(.99):.3f}  |ret|>0.9 (suspect): {(r.abs()>0.9).sum():,}")
