import pandas as pd, json, sys
sys.path.insert(0, '.')
from vistas import scheme_identity as sid
H = 'data/funds/history/holdings_history.parquet'
h = pd.read_parquet(H, columns=['navindia_code', 'ym', 'investment_type', 'company_name', 'nse_symbol', 'pct', 'market_value'])
h['navindia_code'] = h['navindia_code'].map(sid.canonical_code)
h['pct'] = pd.to_numeric(h['pct'], errors='coerce')
h['market_value'] = pd.to_numeric(h['market_value'], errors='coerce')
h = h[h['pct'].notna() & (h['pct'] > 0)]

def build(code):
    d = h[h.navindia_code == code].copy()
    months = sorted(d.ym.dropna().unique())
    d['k'] = d.company_name.astype(str) + '|' + d.nse_symbol.astype(str)
    uniq = d.drop_duplicates('k')[['company_name', 'nse_symbol']].reset_index(drop=True)
    idx = {(str(r.company_name) + '|' + str(r.nse_symbol)): i for i, r in uniq.iterrows()}
    def cr(mv):
        try:
            return round(float(mv) / 100, 1)
        except Exception:
            return None
    bym = {}
    for m, md in d.groupby('ym'):
        bym[m] = [[idx[str(r.company_name) + '|' + str(r.nse_symbol)], round(float(r.pct), 2), cr(r.market_value)]
                  for r in md.sort_values('pct', ascending=False).itertuples()]
    names = [[str(r.company_name), (None if pd.isna(r.nse_symbol) else str(r.nse_symbol))] for r in uniq.itertuples()]
    art = {'months': months, 'names': names, 'by_month': bym}
    s = json.dumps(art, separators=(',', ':'), default=str)
    return len(months), len(uniq), sum(len(v) for v in bym.values()), len(s)

# largest-history + a couple of typical schemes
for code in ['11524', '696', '1223']:
    try:
        nm, nu, nr, nb = build(code)
        print(f'scheme {code}: months={nm} uniq_names={nu} total_rows={nr} JSON={nb} bytes (~{round(nb/1024)} KB)')
    except Exception as e:
        print(code, 'ERR', e)
# extrapolate: avg rows per scheme across the store
import numpy as np
sizes = h.groupby('navindia_code').size()
print(f'\nstore: {len(sizes)} schemes, total holding-rows {int(sizes.sum()):,}, '
      f'avg rows/scheme {sizes.mean():.0f}, p95 {np.percentile(sizes,95):.0f}, max {sizes.max()}')
print('rough total if ~ (bytes/row) * total_rows:', round(sizes.sum() * 22 / 1e6), 'MB across all schemes (at ~22 B/row)')
