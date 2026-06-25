"""BEDROCK AUDIT: is Capitaline Co_Code a stable, non-recycled entity key ACROSS TIME?
Loads all monthly Cline files, builds cross-month identifier maps, and reports drift/conflicts
with concrete examples so each can be adjudicated (rename/re-ISIN = OK & bridged; different
company = RECYCLING = bedrock failure). No assumption survives without evidence."""
import os, re, glob, json
from collections import defaultdict, Counter
import openpyxl

DIR = r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Cline portfolios July'25 to May'26"
MONTH_ORDER = ["Jul25","Aug25","Sep25","Oct25","Nov25","Dec25","Jan26","Feb26","Mar26","Apr26","May26"]
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

def norm_name(s):
    s = re.sub(r"\s+", " ", str(s or "").upper()).strip()
    s = re.sub(r"[.,]", "", s)
    s = re.sub(r"\b(LTD|LIMITED|LIMITE D)\b", "", s).strip()  # ignore Ltd/Limited variants
    s = re.sub(r"\s+", " ", s).strip()
    return s

def is_dummy_isin(z):
    z = str(z or "").strip().upper()
    return (not z) or z.startswith("DU") or "XXXX" in z or not ISIN_RE.match(z)

files = []
for m in MONTH_ORDER:
    p = os.path.join(DIR, f"MF Data - {m}.xlsx")
    if os.path.exists(p): files.append((m, p))
print(f"files found: {[m for m,_ in files]}\n")

co_months   = defaultdict(set)     # co -> {months}
co_names     = defaultdict(Counter) # co -> Counter(norm_name)
co_names_raw = defaultdict(set)     # co -> {raw names}
co_isins     = defaultdict(set)     # co -> {real isins}
isin_co      = defaultdict(set)     # real isin -> {co}
name_co      = defaultdict(set)     # norm_name -> {co}
co_first     = {}                    # co -> first month name seen
rows_total = 0

for m, p in files:
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb["Sheet1"]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    H = {h: i for i, h in enumerate(hdr)}
    ci = H.get("Co_Code")
    zi = H.get("Reported  ISIN", H.get("Reported ISIN"))
    ni = H.get("Portfolio Company Name")
    for r in it:
        rows_total += 1
        co = r[ci] if ci is not None and ci < len(r) else None
        if co in (None, ""): continue
        nm = r[ni] if ni is not None and ni < len(r) else ""
        zz = r[zi] if zi is not None and zi < len(r) else ""
        nn = norm_name(nm)
        co_months[co].add(m)
        if nn:
            co_names[co][nn] += 1; co_names_raw[co].add(str(nm).strip()); name_co[nn].add(co)
            co_first.setdefault(co, (m, nn))
        if not is_dummy_isin(zz):
            zz = str(zz).strip().upper(); co_isins[co].add(zz); isin_co[zz].add(co)
    wb.close()
    print(f"  loaded {m}")

print(f"\nrows_total={rows_total}  unique Co_Code={len(co_months)}")
multi = [c for c in co_months if len(co_months[c]) >= 2]
print(f"Co_Codes present in >=2 months: {len(multi)}  ({100*len(multi)/max(1,len(co_months)):.1f}%)")

# TEST 1+2: Co_Code -> multiple SUBSTANTIVE names  (rename vs recycling)
co_multiname = {c: co_names[c] for c in co_names if len(co_names[c]) > 1}
print(f"\n[TEST persistence/recycling] Co_Code with >1 distinct (normalized) name: {len(co_multiname)}")
for c, cnt in list(sorted(co_multiname.items(), key=lambda kv: -sum(kv[1].values())))[:25]:
    print(f"   Co {c}: {dict(cnt)}")

# TEST 3: Co_Code -> multiple real ISINs (re-ISIN bridged by Co_Code = GOOD; show to verify same co)
co_multiisin = {c: v for c, v in co_isins.items() if len(v) > 1}
print(f"\n[TEST bridging] Co_Code with >1 real ISIN over time: {len(co_multiisin)}")
for c, v in list(co_multiisin.items())[:15]:
    print(f"   Co {c}: ISINs={sorted(v)}  names={sorted(co_names_raw[c])[:2]}")

# REVERSE: same real ISIN -> multiple Co_Codes (vendor inconsistency / merger = concerning)
isin_multico = {z: v for z, v in isin_co.items() if len(v) > 1}
print(f"\n[REVERSE] real ISIN with >1 Co_Code: {len(isin_multico)}")
for z, v in list(isin_multico.items())[:15]:
    print(f"   ISIN {z}: Co_Codes={sorted(v)}  names={[sorted(co_names_raw[c])[:1] for c in v]}")

# REVERSE: same normalized name -> multiple Co_Codes (split identity = concerning)
name_multico = {z: v for z, v in name_co.items() if len(v) > 1}
print(f"\n[REVERSE] normalized NAME with >1 Co_Code: {len(name_multico)}")
for z, v in list(name_multico.items())[:15]:
    print(f"   '{z}': Co_Codes={sorted(v)}")

# positive evidence: of recurring Co_Codes, fraction perfectly stable (1 name, <=1 real isin)
stable = sum(1 for c in multi if len(co_names[c]) == 1 and len(co_isins[c]) <= 1)
print(f"\nBEDROCK VERDICT (recurring Co_Codes): {stable}/{len(multi)} "
      f"({100*stable/max(1,len(multi)):.2f}%) perfectly stable (1 name & <=1 real ISIN across all months)")

json.dump({"rows_total": rows_total, "unique_cocode": len(co_months),
           "recurring": len(multi), "stable": stable,
           "co_multiname": {str(c): dict(v) for c, v in co_multiname.items()},
           "co_multiisin": {str(c): sorted(v) for c, v in co_multiisin.items()},
           "isin_multico": {z: sorted(map(str, v)) for z, v in isin_multico.items()},
           "name_multico": {z: sorted(map(str, v)) for z, v in name_multico.items()}},
          open(r"data\funds\_cocode_audit.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("\nwrote data/funds/_cocode_audit.json")
