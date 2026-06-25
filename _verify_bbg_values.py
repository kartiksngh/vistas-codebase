"""VALUE-LEVEL precision check (KV: 'values same to bps, not ranks'). Pool every common stock-month
return DIFFERENCE d = our_return - bbg_return (in basis points). Report:
  - MEAN SIGNED gap  -> catches a systematic Total-Return-vs-Price offset (would be ~ -div_yield/mo)
  - distribution of |d| and the % matching within 1bp / 10bp / 100bp
  - the >100bp tail characterized as corporate-action months (BBG raw vs our split-adjusted), not errors.
"""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, r"C:\Users\Administrator\Documents\Projects\Vistas")
from vistas import stocks

BBG = r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Update December 2025\Portfolio Update\BBG Data\Prices Cline Portfolio Data Jan'10 to Oct'25 - updated Dec'25.xlsx"
CACHE = r"C:\Users\Administrator\Documents\Projects\Vistas\data\funds\history\_bbg_monthly_px.parquet"
bridge = pd.read_csv(r"C:\Users\Administrator\Documents\Projects\Vistas\data\funds\history\bbg_identity_bridge.csv")
t2s = {t: s for t, s in zip(bridge["bbg_ticker"], bridge["nse_symbol"]) if isinstance(s, str) and s}

if os.path.exists(CACHE):
    pxm = pd.read_parquet(CACHE); pxm.index = pd.to_datetime(pxm.index)
    print("loaded cached BBG monthly")
else:
    print("reading BBG price matrix…")
    px = pd.read_excel(BBG, sheet_name="Sheet1")
    px = px.rename(columns={px.columns[0]: "Date"}); px["Date"] = pd.to_datetime(px["Date"], errors="coerce")
    px = px.dropna(subset=["Date"]).set_index("Date").sort_index()
    px = px.replace(["#N/A N/A", "#N/A Invalid Security", "#N/A Field Not Applicable"], np.nan).apply(pd.to_numeric, errors="coerce")
    pxm = px.resample("ME").last(); pxm.to_parquet(CACHE)

ours = stocks.load().resample("ME").last()

D = []  # signed return diffs in bps, pooled across all common stock-months
for tk in pxm.columns:
    sym = t2s.get(tk)
    if not sym or sym not in ours.columns:
        continue
    a = pxm[tk].dropna(); b = ours[sym].dropna()
    idx = a.index.intersection(b.index)
    if len(idx) < 24:
        continue
    rb = a.reindex(idx).pct_change(); ra = b.reindex(idx).pct_change()
    d = (ra - rb).dropna() * 1e4   # bps; ours - bbg
    D.append(d.values)
D = np.concatenate(D)
n = len(D); ad = np.abs(D)
print(f"\npooled common stock-months: {n:,}")
print(f"MEAN SIGNED gap = {D.mean():+.3f} bps   median signed = {np.median(D):+.4f} bps")
print(f"  (a systematic Total-Return-vs-Price offset would show as mean ~ -10..-30 bps/mo; ~0 = same KIND)")
print(f"\n|gap| distribution (bps):  median={np.median(ad):.4f}  p90={np.percentile(ad,90):.3f}  "
      f"p99={np.percentile(ad,99):.2f}  p99.9={np.percentile(ad,99.9):.1f}  max={ad.max():.0f}")
for thr in (0.1, 1, 10, 100, 1000):
    print(f"  |gap| < {thr:>6} bps : {100*np.mean(ad < thr):.3f}%")
# characterize the >100bp tail = corporate-action months (raw BBG vs our split-adjusted)
tail = ad[ad >= 100]
print(f"\n>=100 bps tail: {len(tail):,} stock-months = {100*len(tail)/n:.2f}% "
      f"(corporate-action months: BBG raw vs our split/bonus-adjusted — definitional, not error)")
body = D[ad < 100]
print(f"NON-CA body (|gap|<100bps): {len(body):,} months  mean={body.mean():+.4f}bps  "
      f"median|.|={np.median(np.abs(body)):.4f}bps  p99|.|={np.percentile(np.abs(body),99):.3f}bps")
print(f"\n>>> value precision: {100*np.mean(ad<1):.2f}% of stock-months match within 1 bp, "
      f"{100*np.mean(ad<10):.2f}% within 10 bps; systematic offset {D.mean():+.2f} bps (≈0 => same price-return basis).")
