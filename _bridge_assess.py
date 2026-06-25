"""Assess live-AMC (name-slug) -> store (navindia_code) bridge by HOLDINGS FINGERPRINT (symbol-set
overlap), not fuzzy names. Quantify: how many live funds map to a store scheme, how many are genuinely
new (latest-month-only, no history). Decides the restore strategy."""
import json, glob, os, sys
import pandas as pd
sys.path.insert(0, '.')
from vistas import scheme_identity as sid

H = 'data/funds/history/holdings_history.parquet'
h = pd.read_parquet(H, columns=['navindia_code', 'ym', 'scheme_name', 'amc', 'nse_symbol', 'pct'])
h['navindia_code'] = h['navindia_code'].map(sid.canonical_code)
h = h[h.pct.notna() & (h.pct > 0)]
store = {}
for code, d in h.groupby('navindia_code'):
    lm = d.ym.max()
    dl = d[d.ym == lm]
    syms = set(s for s in dl.nse_symbol.dropna().astype(str) if s and s != 'nan')
    store[code] = {'name': str(dl.scheme_name.iloc[0]), 'amc': str(dl.amc.iloc[0]), 'syms': syms}

P = 'output/terminal_site/data/funds_portfolio/'
live = {}
for f in glob.glob(P + '*.json'):
    b = os.path.basename(f)[:-5]
    if b.startswith('_'):
        continue
    j = json.load(open(f, encoding='utf-8'))
    syms = set(str(x.get('symbol')) for x in (j.get('holdings') or []) if x.get('symbol'))
    live[b] = {'name': j.get('name'), 'amc': j.get('amc'), 'syms': syms}
print('store schemes', len(store), '| live-AMC funds', len(live))

def jac(a, b):
    return len(a & b) / len(a | b) if (a and b) else 0.0

store_items = [(c, st['syms']) for c, st in store.items() if st['syms']]
matched, newonly, no_syms = 0, 0, 0
unmatched = []
for slug, lv in live.items():
    if not lv['syms']:
        no_syms += 1
        continue
    bj, best = 0.0, None
    for code, ssy in store_items:
        j = jac(lv['syms'], ssy)
        if j > bj:
            bj, best = j, code
    if bj >= 0.5:
        matched += 1
    else:
        newonly += 1
        unmatched.append((lv['name'], lv['amc'], round(bj, 2), len(lv['syms'])))
print(f'matched to store (symJaccard>=0.5): {matched} | new/unmatched: {newonly} | live w/o symbols(debt/liquid): {no_syms}')
print('\ntop unmatched (new funds, would be latest-month-only), by #holdings:')
for r in sorted(unmatched, key=lambda x: -x[3])[:20]:
    print('  ', r)
# classify unmatched: passive/index/ETF/debt vs genuinely ACTIVE (the only ones that'd matter to the skill model)
import re
passive_re = re.compile(r'index|etf|\bnifty\b|sensex|\bbse\b|midcap ?1?\d\d|smallcap ?250|microcap|total market|gold|silver|liquid|overnight|\bdebt\b|bond|gilt|g-?sec|money market|arbitrage|target maturity|fof|fund of fund|\b20\d\d\b', re.I)
active_unm = [u for u in unmatched if not passive_re.search(str(u[0]))]
# 476 no-symbol funds are debt/liquid by definition (no equity book)
print(f'\nUNMATCHED breakdown: passive/index/ETF/debt-ish {len(unmatched) - len(active_unm)} | ACTIVE-looking {len(active_unm)} (+ {no_syms} no-symbol debt/liquid)')
print('ACTIVE-looking unmatched (genuine active funds missing from the skill universe?):')
for r in sorted(active_unm, key=lambda x: -x[3])[:25]:
    print('  ', r)
from collections import Counter
amc_ct = Counter(u[1] for u in unmatched)
print('\nunmatched by AMC (top 12):')
for a, c in amc_ct.most_common(12):
    print(f'  {c:>3}  {a}')
