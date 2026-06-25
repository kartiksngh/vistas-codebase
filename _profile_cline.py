"""Profile the Cline May26 file: identifier consistency, blanks, asset types, plan dup, %-sum sanity.
This is the 'understand before you clean' pass — find where the data can bite us."""
import re, statistics
from collections import Counter, defaultdict
import openpyxl

PATH = r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Cline portfolios July'25 to May'26\MF Data - May26.xlsx"
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

wb = openpyxl.load_workbook(PATH, read_only=True, data_only=True)
ws = wb["Sheet1"]
rows = ws.iter_rows(values_only=True)
hdr = next(rows)
H = {h: i for i, h in enumerate(hdr)}
def g(r, name):
    i = H.get(name)
    return r[i] if i is not None and i < len(r) else None

navc = Counter(); scheme = set(); mf = set(); cocode = set(); isin = set(); coname = set()
invtype = Counter(); sebicat = Counter(); dates = Counter()
blank_isin = blank_cocode = blank_pct = blank_coname = bad_isin = 0
co2isin = defaultdict(set); isin2co = defaultdict(set); co2name = defaultdict(set)
pct_by_scheme = defaultdict(float); rows_by_scheme = Counter()
n = 0
for r in rows:
    n += 1
    nc = g(r, "NAVIndia Code"); sc = g(r, "Scheme Name"); cc = g(r, "Co_Code")
    iz = g(r, "Reported  ISIN") or g(r, "Reported ISIN"); cn = g(r, "Portfolio Company Name")
    pc = g(r, "% of holding in scheme"); it = g(r, "Investment Type"); cat = g(r, "Sebi Category")
    d = g(r, "Scheme Portfolio Date")
    navc[nc] += 1; scheme.add(sc); mf.add(g(r, "Name of the Mutual Fund Name"))
    if cc not in (None, ""): cocode.add(cc)
    else: blank_cocode += 1
    iz_s = str(iz).strip() if iz is not None else ""
    if iz_s: isin.add(iz_s)
    else: blank_isin += 1
    if iz_s and not ISIN_RE.match(iz_s): bad_isin += 1
    if cn not in (None, ""): coname.add(str(cn).strip())
    else: blank_coname += 1
    if pc in (None, ""): blank_pct += 1
    invtype[str(it)] += 1; sebicat[str(cat)] += 1; dates[str(d)[:10]] += 1
    if cc not in (None, "") and iz_s: co2isin[cc].add(iz_s); isin2co[iz_s].add(cc)
    if cc not in (None, "") and cn: co2name[cc].add(str(cn).strip())
    try: pct_by_scheme[(nc, sc)] += float(pc)
    except (TypeError, ValueError): pass
    rows_by_scheme[(nc, sc)] += 1
wb.close()

print(f"ROWS={n}")
print(f"unique: NAVIndia={len(navc)}  Scheme={len(scheme)}  MF/AMC={len(mf)}  Co_Code={len(cocode)}  ISIN={len(isin)}  CoName={len(coname)}")
print(f"blanks: ISIN={blank_isin}  Co_Code={blank_cocode}  pct={blank_pct}  CoName={blank_coname}  bad_ISIN_fmt={bad_isin}")
print()
print("Investment Type:", dict(invtype.most_common()))
print()
print("Scheme Portfolio Date(s):", dict(dates))
print()
print("Top SEBI Categories:", dict(sebicat.most_common(15)))
print()
# identifier drift WITHIN the month (should mostly be 1:1)
co_multi_isin = {k: v for k, v in co2isin.items() if len(v) > 1}
isin_multi_co = {k: v for k, v in isin2co.items() if len(v) > 1}
co_multi_name = {k: v for k, v in co2name.items() if len(v) > 1}
print(f"Co_Code -> >1 ISIN: {len(co_multi_isin)} cases")
for k, v in list(co_multi_isin.items())[:8]: print("   Co", k, "->", v)
print(f"ISIN -> >1 Co_Code: {len(isin_multi_co)} cases")
for k, v in list(isin_multi_co.items())[:8]: print("   ISIN", k, "->", v)
print(f"Co_Code -> >1 Name: {len(co_multi_name)} cases")
for k, v in list(co_multi_name.items())[:6]: print("   Co", k, "->", list(v)[:3])
print()
# plan duplication: same scheme base name with Regular & Direct?
import re as _re
base = defaultdict(set)
for nc, sc in rows_by_scheme:
    b = _re.sub(r"\s*-\s*(regular|direct).*$", "", str(sc), flags=_re.I).strip().lower()
    base[b].add(str(sc))
dup_plans = {k: v for k, v in base.items() if len(v) > 1}
print(f"scheme base-names with >1 plan variant: {len(dup_plans)} (of {len(base)} bases)")
for k, v in list(dup_plans.items())[:5]: print("   ", k, "->", list(v)[:3])
print()
# %-sum sanity across schemes
sums = list(pct_by_scheme.values())
print(f"per-scheme %-sum: n={len(sums)}  median={statistics.median(sums):.1f}  "
      f"min={min(sums):.1f}  max={max(sums):.1f}")
off = [(k, round(v,1)) for k, v in pct_by_scheme.items() if not (95 <= v <= 105)]
print(f"  schemes with %-sum outside [95,105]: {len(off)}")
for k, v in sorted(off, key=lambda x: x[1])[:5]: print("   LOW ", k[1][:40], v)
for k, v in sorted(off, key=lambda x: -x[1])[:5]: print("   HIGH", k[1][:40], v)
