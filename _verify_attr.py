import json, glob, pandas as pd
D = 'data/funds_attribution/'
man = json.load(open(D + '_manifest.json'))
df = pd.DataFrame(man).T
df['n_months'] = pd.to_numeric(df.n_months, errors='coerce')
print('schemes', len(df))
print('verdict dist:'); print(df.verdict.value_counts().to_string())
print('n_months: min', int(df.n_months.min()), 'max', int(df.n_months.max()), 'median', int(df.n_months.median()))

# inspect one scheme JSON structure + its time coverage
ns = df[df.name.str.contains('Nippon', case=False, na=False) & df.name.str.contains('Small', case=False, na=False)]
print('\nNippon Small Cap:'); print(ns[['name', 'category', 'verdict', 'excess_cagr', 't_stat', 'n_months']].to_string())
code = ns.index[0]
j = json.load(open(D + f'{code}.json'))
print('\nscheme JSON top-level keys:', list(j.keys()))
ts = j.get('ts')
if isinstance(ts, dict):
    print('ts sub-keys:', list(ts.keys()))
    # find the date/month axis
    for k in ts:
        v = ts[k]
        if isinstance(v, list) and v and isinstance(v[0], str):
            print(f'  ts["{k}"] first..last: {v[0]} .. {v[-1]}  (len {len(v)})')

# cross-section: how many schemes now have data through 2026-05?
def last_month(code):
    try:
        jj = json.load(open(D + f'{code}.json'))
        t = jj.get('ts', {})
        for k in ('ym', 'months', 'dates', 'date'):
            if isinstance(t.get(k), list) and t[k]:
                return t[k][-1]
    except Exception:
        return None
    return None
lasts = pd.Series({c: last_month(c) for c in df.index}).dropna()
print('\nlast-month distribution across schemes (top):')
print(lasts.value_counts().head(8).to_string())
