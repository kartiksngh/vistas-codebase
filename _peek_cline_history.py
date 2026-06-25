"""Verify (don't trust) the big Cline history CSVs: schema, date span, row count, identifier coverage.
Memory-safe chunked scan. Treats his files as WITNESSES to audit, not inputs to adopt."""
import sys, pandas as pd
from collections import Counter

FILES = {
  "raw_CLine_dump": r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Update July 2025\Portfolio Data\CLine Portfolios of MF holdings.csv",
  "processed_Dec25": r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Update December 2025\Portfolio Update\Cline Portfolio Data Jan'10 to Oct'25 - updated Dec'25.csv",
}

def find_col(cols, *needles):
    low = {c.lower().strip(): c for c in cols}
    for n in needles:
        for lc, orig in low.items():
            if n in lc:
                return orig
    return None

for tag, path in FILES.items():
    print(f"\n{'='*70}\n{tag}\n{path}\n{'='*70}")
    try:
        head = pd.read_csv(path, nrows=3)
    except Exception as e:
        print("  read failed:", e); continue
    cols = list(head.columns)
    print("COLUMNS:", cols)
    print("FIRST ROWS:")
    for _, r in head.iterrows():
        print("   ", " | ".join(str(r[c])[:22] for c in cols))
    cdate = find_col(cols, "portfolio date", "scheme portfolio", "nav date", "date")
    cco   = find_col(cols, "co_code", "cocode")
    crisin= find_col(cols, "reported  isin", "reported isin")
    cfisin= find_col(cols, "final isin")
    cnav  = find_col(cols, "navindia", "scheme code")
    camc  = find_col(cols, "mutual fund", "amc")
    use = [c for c in {cdate, cco, crisin, cfisin, cnav, camc} if c]
    n = 0; months = Counter(); navs = set(); cos = set(); risin = set(); fisin = set()
    dmin = dmax = None
    for chunk in pd.read_csv(path, usecols=use, chunksize=300000, dtype=str):
        n += len(chunk)
        if cdate:
            d = chunk[cdate].dropna().astype(str)
            for v in d: months[v[:7]] += 1
            lo, hi = d.min(), d.max()
            dmin = lo if dmin is None else min(dmin, lo); dmax = hi if dmax is None else max(dmax, hi)
        if cnav: navs |= set(chunk[cnav].dropna().astype(str))
        if cco:  cos  |= set(chunk[cco].dropna().astype(str))
        if crisin: risin |= set(chunk[crisin].dropna().astype(str))
        if cfisin: fisin |= set(chunk[cfisin].dropna().astype(str))
    print(f"\nROWS={n:,}")
    print(f"date col '{cdate}': min={dmin} max={dmax}  n_months={len(months)}")
    ms = sorted(months)
    print(f"  month span: {ms[0] if ms else '?'} .. {ms[-1] if ms else '?'}  (first5={ms[:5]} last5={ms[-5:]})")
    if cnav: print(f"unique scheme code '{cnav}': {len(navs):,}")
    if cco:  print(f"unique Co_Code '{cco}': {len(cos):,}")
    if crisin: print(f"unique Reported ISIN '{crisin}': {len(risin):,}")
    if cfisin: print(f"unique Final ISIN '{cfisin}': {len(fisin):,}")
