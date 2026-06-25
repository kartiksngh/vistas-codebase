import pandas as pd, numpy as np, glob
H = 'data/funds/history/'
h = pd.read_parquet(H + 'holdings_history.parquet')
print('=== HOLDINGS STORE ===')
print('rows', len(h), 'months', h.ym.nunique(), 'span', h.ym.min(), '->', h.ym.max())
print('sources:', h.source.value_counts().to_dict())
new = ['2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05']
eqm = h.investment_type.astype(str).str.lower().str.contains('equity', na=False)
print('\n=== APPENDED MONTHS AUDIT (Nov25..May26) ===')
for m in new:
    g = h[h.ym == m]
    eq = g[g.investment_type.astype(str).str.lower().str.contains('equity', na=False)]
    vr = 100 * eq.loc[eq.vst_id.notna(), 'market_value'].sum() / max(eq.market_value.sum(), 1e-9)
    s = eq.groupby('navindia_code')['pct'].sum()
    inb = 100 * ((s >= 90) & (s <= 102)).mean() if len(s) else 0
    unres = int((g.id_conf == 'unresolved').sum())
    print(f'{m} rows={len(g):>6} sch={g.navindia_code.nunique():>4} cc={g.co_code.nunique():>4} '
          f'eqValRes={vr:5.1f}% unresolved={unres:>5} nullVst={int(g.vst_id.isna().sum()):>5} pctsumInBand={inb:4.0f}%')
u = h[(h.ym.isin(new)) & eqm & (h.id_conf == 'unresolved')]
print('\nUNRESOLVED equity co_codes in new months:', u.co_code.nunique(), 'rows', len(u), 'mv', round(u.market_value.sum(), 1))
if len(u):
    print(u.groupby('co_code').agg(name=('company_name', 'first'), mv=('market_value', 'sum'),
          sch=('navindia_code', 'nunique')).sort_values('mv', ascending=False).head(12).to_string())

print('\n=== FORWARD-TR FEASIBILITY ===')
tr = pd.read_parquet(H + 'tr_returns_monthly.parquet')
tr['ym'] = pd.to_datetime(tr.date).dt.strftime('%Y-%m')
print('tr_returns span', tr.ym.min(), '->', tr.ym.max(), 'vst', tr.vst_id.nunique(), 'rows', len(tr))
# store's own vst_id -> nse_symbol (most aligned source)
vid2sym = h.dropna(subset=['vst_id', 'nse_symbol']).drop_duplicates('vst_id').set_index('vst_id')['nse_symbol']
panel = sorted(glob.glob('data/Stocks Data TR till*.csv'))[-1]
hdr = pd.read_csv(panel, nrows=3)
print('NSE TR panel:', panel, '| cols', len(hdr.columns), '| col0', hdr.columns[0], '| sample syms', list(hdr.columns[1:5]))
syms = set(str(c).strip() for c in hdr.columns[1:])
may = h[(h.ym == '2026-05') & eqm & h.vst_id.notna()]
held = may.vst_id.unique()
mapped = [v for v in held if v in vid2sym.index and str(vid2sym[v]).strip() in syms]
print(f'May-2026 equity held vst_ids={len(held)} | priceable via NSE panel symbol={len(mapped)} ({100*len(mapped)/max(len(held),1):.1f}%)')
mvtot = may.groupby('vst_id').market_value.sum()
mv_ok = mvtot.reindex(mapped).sum()
print(f'May-2026 equity MV priceable via NSE panel: {100*mv_ok/max(mvtot.sum(),1e-9):.2f}%')
unmapped = [v for v in held if v not in mapped]
um = may[may.vst_id.isin(unmapped)].groupby('vst_id').agg(sym=('nse_symbol','first'), name=('vid_name','first'), mv=('market_value','sum')).sort_values('mv', ascending=False)
print('top unpriceable (no NSE-panel symbol):'); print(um.head(10).to_string())
