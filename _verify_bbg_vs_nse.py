"""Verify our Vistas NSE prices vs Bloomberg (KV ask). Compare MONTHLY-return correlation per stock
on the overlap (returns, not levels, since our panel is split/adjusted and BBG is raw). High rho =
our prices agree with BBG; low rho = a mapping error or corporate-action-timing diff to investigate."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, r"C:\Users\Administrator\Documents\Projects\Vistas")
from vistas import stocks

BBG = r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Update December 2025\Portfolio Update\BBG Data\Prices Cline Portfolio Data Jan'10 to Oct'25 - updated Dec'25.xlsx"
bridge = pd.read_csv(r"C:\Users\Administrator\Documents\Projects\Vistas\data\funds\history\bbg_identity_bridge.csv")
t2s = {t: s for t, s in zip(bridge["bbg_ticker"], bridge["nse_symbol"]) if isinstance(s, str) and s}
print(f"bridge: {len(t2s)} BBG tickers -> our NSE symbol")

print("reading BBG price matrix…")
px = pd.read_excel(BBG, sheet_name="Sheet1")
px = px.rename(columns={px.columns[0]: "Date"})
px["Date"] = pd.to_datetime(px["Date"], errors="coerce")
px = px.dropna(subset=["Date"]).set_index("Date").sort_index()
px = px.replace(["#N/A N/A", "#N/A Invalid Security", "#N/A Field Not Applicable"], np.nan)
px = px.apply(pd.to_numeric, errors="coerce")
pxm = px.resample("ME").last()
print(f"  BBG monthly: {pxm.shape[0]} months {pxm.index.min().date()}..{pxm.index.max().date()}, {pxm.shape[1]} tickers")

ours = stocks.load().resample("ME").last()
print(f"  our monthly: {ours.shape[0]} months, {ours.shape[1]} symbols")

rows = []
for tk in pxm.columns:
    sym = t2s.get(tk)
    if not sym or sym not in ours.columns:
        continue
    a = pxm[tk].dropna(); b = ours[sym].dropna()
    idx = a.index.intersection(b.index)
    if len(idx) < 24:
        continue
    ra = a.reindex(idx).pct_change(); rb = b.reindex(idx).pct_change()
    j = ra.dropna().index.intersection(rb.dropna().index)
    if len(j) < 20:
        continue
    rho = ra.reindex(j).corr(rb.reindex(j))
    # median abs monthly-return gap (a second, scale-free agreement measure)
    gap = float((ra.reindex(j) - rb.reindex(j)).abs().median())
    rows.append((tk, sym, len(j), rho, round(gap, 4)))

res = pd.DataFrame(rows, columns=["bbg_ticker", "nse_symbol", "n_months", "rho", "med_abs_gap"]).dropna(subset=["rho"])
print(f"\ncompared {len(res)} stocks (>=20 common monthly returns)")
print(f"median rho = {res.rho.median():.4f}   mean = {res.rho.mean():.4f}")
print(f"  rho>=0.99: {100*(res.rho>=0.99).mean():.1f}%   rho>=0.95: {100*(res.rho>=0.95).mean():.1f}%   "
      f"rho<0.8: {100*(res.rho<0.8).mean():.1f}%")
print(f"median of per-stock median abs monthly-return gap = {res.med_abs_gap.median():.4f}")
print("\nlowest-rho (investigate: mapping error vs corporate-action timing):")
print(res.sort_values("rho").head(15).to_string(index=False))
res.to_csv(r"C:\Users\Administrator\Documents\Projects\Vistas\data\funds\history\_bbg_vs_nse_price_check.csv", index=False)
print("\nwrote data/funds/history/_bbg_vs_nse_price_check.csv")
