import json
from vistas import funds_portfolio_viz as v
viz = v.build_viz()
print('schemes built:', len(viz))
r = viz['11524']
print('keys:', list(r.keys()))
print('months:', len(r['months']), r['months'][0], '->', r['months'][-1])
print('names:', len(r['names']), '| sample:', r['names'][0])
lm = r['months'][-1]
print('by_month count:', len(r['by_month']), '| latest', lm, 'rows:', len(r['by_month'][lm]))
print('latest row sample [nameIdx,pct,cr]:', r['by_month'][lm][0])
# sanity: latest by_month pct-sum vs the disclosed total
psum = sum(x[1] for x in r['by_month'][lm])
print('latest by_month pct-sum:', round(psum, 1))
# total inline size across all schemes
tot = sum(len(json.dumps(rv, separators=(',', ':'), default=str)) for rv in viz.values())
print(f'TOTAL portfolio-block bytes ~ {tot/1e6:.1f} MB across {len(viz)} schemes (avg {tot/len(viz)/1024:.0f} KB)')
# biggest scheme JSON
biggest = max(viz.items(), key=lambda kv: len(json.dumps(kv[1], separators=(',', ':'), default=str)))
print('biggest:', biggest[0], round(len(json.dumps(biggest[1], separators=(",", ":"), default=str)) / 1024), 'KB')
