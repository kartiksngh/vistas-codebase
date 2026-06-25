"""Forward NSE-TR extension of tr_returns_monthly.parquet (Dec-2025 -> Jun-2026).

BBG history ends 2025-12. Living names trade on NSE -> compute their monthly TOTAL return
from our own NSE TR panel and append the post-BBG months, keyed by the SAME vst_id used in
holdings. Architecture (locked): adopt BBG TR for history, compute our own NSE TR going forward.

DISCIPLINE: validate BEFORE writing. Reconcile NSE-derived returns vs BBG on the 2018-2025
overlap (must hit corr>=0.99 like the prior price check). Append ONLY if validation passes
AND DO_WRITE is True. Back up the parquet once before any write.
"""
import pandas as pd, numpy as np, glob, os
H = 'data/funds/history/'
TRP = H + 'tr_returns_monthly.parquet'
DO_WRITE = True           # validated (corr_core 0.9995, cum-ratio 1.0000) -> append forward months

# --- authoritative vst_id <-> nse_symbol, from the store itself (resolved equity) ---
h = pd.read_parquet(H + 'holdings_history.parquet', columns=['vst_id', 'nse_symbol'])
vs = h.dropna(subset=['vst_id', 'nse_symbol']).copy()
vs['nse_symbol'] = vs.nse_symbol.astype(str).str.strip()
vs = vs.drop_duplicates('vst_id')[['vst_id', 'nse_symbol']]
sym2vid = dict(zip(vs.nse_symbol, vs.vst_id))
need = list(vs.nse_symbol.unique())

# --- load only the needed symbol columns from the NSE TR panel ---
panel = sorted(glob.glob('data/Stocks Data TR till*.csv'))[-1]
cols = list(pd.read_csv(panel, nrows=0).columns)
datecol = cols[0]
avail = [s for s in need if s in set(cols)]
print(f'panel={panel}  needed_syms={len(need)}  present_in_panel={len(avail)}')
px = pd.read_csv(panel, usecols=[datecol] + avail)
px[datecol] = pd.to_datetime(px[datecol], errors='coerce')
px = px.dropna(subset=[datecol]).set_index(datecol).sort_index()
print(f'panel loaded {px.shape}  span {px.index.min().date()} -> {px.index.max().date()}')

# --- month-end TR returns per symbol -> long, mapped to vst_id ---
me = px.resample('ME').last()
ret = me.pct_change(fill_method=None)   # do NOT pad stale prices (correctness)
ret.index = ret.index.strftime('%Y-%m')
ret.index.name = 'ym'
rl = (ret.reset_index().melt(id_vars='ym', var_name='nse_symbol', value_name='ret_nse')
        .dropna(subset=['ret_nse']))
rl['vst_id'] = rl.nse_symbol.map(sym2vid)
rl = rl.dropna(subset=['vst_id'])
# winsor view only for sanity print (store raw; attribution clips at load)
print(f'NSE monthly returns: rows={len(rl):,}  vst={rl.vst_id.nunique()}  ym {rl.ym.min()}..{rl.ym.max()}')

# --- OVERLAP RECONCILIATION vs BBG (the gate) ---
tr = pd.read_parquet(TRP)
tr['ym'] = pd.to_datetime(tr.date).dt.strftime('%Y-%m')
bbg = tr.dropna(subset=['vst_id', 'ret_1m'])[['vst_id', 'ym', 'ret_1m']].rename(columns={'ret_1m': 'ret_bbg'})
ov = bbg.merge(rl[['vst_id', 'ym', 'ret_nse']], on=['vst_id', 'ym'], how='inner')
ovw = ov[(ov.ym >= '2018-01') & (ov.ym <= '2025-12')].copy()
ovw['diff'] = ovw.ret_bbg - ovw.ret_nse
corr_all = ovw.ret_bbg.corr(ovw.ret_nse)
core = ovw[ovw['diff'].abs() <= 0.10]
corr_core = core.ret_bbg.corr(core.ret_nse)
mad = ovw['diff'].abs().median()
bias = (ovw.ret_nse - ovw.ret_bbg).mean()
ntail = int((ovw['diff'].abs() > 0.10).sum())
print(f'\n=== OVERLAP RECONCILE 2018-2025 ===  n={len(ovw):,}')
print(f'  corr(all)={corr_all:.4f}  corr(|diff|<=10pp)={corr_core:.4f}  medianAbsDiff={mad:.5f}  '
      f'meanBias(nse-bbg)={bias:+.5f} ({bias*1200:+.2f}%/yr)')
print(f'  |diff|>10pp months: {ntail} ({100*ntail/max(len(ovw),1):.2f}%)')
# DECISIVE independent check: per-vst_id CUMULATIVE compound return — CA-timing diffs cancel over time
def cumret(df, col):
    return df.groupby('vst_id')[col].apply(lambda s: (1 + s.clip(-0.95, 5)).prod() - 1)
cum = pd.concat([cumret(ovw, 'ret_bbg').rename('bbg'), cumret(ovw, 'ret_nse').rename('nse')], axis=1).dropna()
cum = cum[cum.bbg > -0.99]
cum['ratio'] = (1 + cum.nse) / (1 + cum.bbg)
cum_med = cum.ratio.median()
print(f'  CUMULATIVE per-vst_id 2018-2025: n={len(cum)}  median ratio(nse/bbg)={cum_med:.4f}  '
      f'within +/-5%: {100*((cum.ratio >= 0.95) & (cum.ratio <= 1.05)).mean():.1f}%')
tailcnt = ovw[ovw['diff'].abs() > 0.10].vst_id.value_counts()
print(f'  tail concentration: {len(tailcnt)} of {ovw.vst_id.nunique()} vst_ids have a >10pp month '
      f'(top5 month-counts {list(tailcnt.head().values)})')
PASS = (corr_core >= 0.99) and (mad <= 0.005) and (0.98 <= cum_med <= 1.02)
print('VALIDATION:', 'PASS' if PASS else 'FAIL — investigate before writing')

# --- FORWARD months to append (strictly after BBG end) ---
bbg_max = tr.ym.max()
fwd = rl[rl.ym > bbg_max].copy()
print(f'\nBBG ends {bbg_max}; forward months to append: {sorted(fwd.ym.unique())}')
print(f'forward rows={len(fwd):,}  vst={fwd.vst_id.nunique()}')
# attach the level for schema parity
mlong = (me.reset_index().assign(ym=lambda d: d[datecol].dt.strftime('%Y-%m'))
           .melt(id_vars=['ym'], value_vars=avail, var_name='nse_symbol', value_name='tr_price'))
fwd = fwd.merge(mlong[['ym', 'nse_symbol', 'tr_price']], on=['ym', 'nse_symbol'], how='left')

if DO_WRITE and PASS and len(fwd):
    assert fwd.ym.min() > bbg_max, 'forward overlaps BBG — abort'
    bak = TRP + '.bak'
    if not os.path.exists(bak):
        tr.drop(columns=['ym']).to_parquet(bak, index=False); print(f'backup -> {bak}')
    out = pd.DataFrame({
        'date': pd.to_datetime(fwd.ym) + pd.offsets.MonthEnd(0),
        'bbg_ticker': None,
        'tr_price': fwd.tr_price.values,
        'vst_id': fwd.vst_id.values,
        'nse_symbol': fwd.nse_symbol.values,
        'key': fwd.vst_id.values,
        'ret_1m': fwd.ret_nse.values,
    })
    combined = pd.concat([tr.drop(columns=['ym']), out], ignore_index=True)
    tmp = TRP + '.tmp'; combined.to_parquet(tmp, index=False); os.replace(tmp, TRP)
    chk = pd.read_parquet(TRP); chk['ym'] = pd.to_datetime(chk.date).dt.strftime('%Y-%m')
    print(f'\nWROTE. tr_returns now {chk.ym.min()}..{chk.ym.max()}  rows={len(chk):,}  vst={chk.vst_id.nunique()}')
else:
    print('\n(no write — DO_WRITE False or validation failed)')
