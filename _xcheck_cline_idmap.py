"""BEDROCK cross-check (independent method): does Cline's Co_Code/ISIN map to the RIGHT security,
per OUR own idmap identity layer? Stability (the Co_Code audit) proved Cline is self-consistent;
this proves CORRECTNESS against an independent source. Two tests:
  (1) name agreement: our master's name for each ISIN vs Cline's name (by count + by Rs held)
  (2) lineage corroboration: Co_Code split-ISIN cases -> do both ISINs map to ONE vst_id for us?
"""
import re, json, sys
from collections import defaultdict
import openpyxl
sys.path.insert(0, r"C:\Users\Administrator\Documents\Projects\Vistas")
from vistas import idmap

PATH = r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Cline portfolios July'25 to May'26\MF Data - May26.xlsx"

def toks(s):
    s = re.sub(r"\b(LTD|LIMITED|THE|AND|CO|CORPORATION|INDIA)\b", " ", str(s or "").upper())
    return set(re.findall(r"[A-Z0-9]+", s))
def jacc(a, b):
    A, B = toks(a), toks(b)
    return len(A & B) / len(A | B) if (A | B) else 0.0

# --- gather unique Cline securities (ISIN -> cline name, co_code, total Rs held) ---
wb = openpyxl.load_workbook(PATH, read_only=True, data_only=True)
ws = wb["Sheet1"]; it = ws.iter_rows(values_only=True)
hdr = list(next(it)); H = {h: i for i, h in enumerate(hdr)}
ci, zi, ni, vi = (H.get("Co_Code"), H.get("Reported  ISIN", H.get("Reported ISIN")),
                  H.get("Portfolio Company Name"), H.get("Market value"))
sec = {}      # isin -> {name, co, val}
co_isins = defaultdict(set)
for r in it:
    isin = idmap.normalize_isin(r[zi] if zi is not None else "")
    nm = r[ni] if ni is not None else ""
    co = r[ci] if ci is not None else None
    try: val = float(r[vi]) if vi is not None and r[vi] not in (None, "") else 0.0
    except (TypeError, ValueError): val = 0.0
    if isin:
        s = sec.setdefault(isin, {"name": nm, "co": co, "val": 0.0}); s["val"] += val
    if co not in (None, "") and idmap.is_valid_isin(isin):
        co_isins[co].add(isin)
wb.close()

# --- TEST 1: name agreement among ISINs our master KNOWS ---
n_total = len(sec); val_total = sum(s["val"] for s in sec.values())
resolved = invalid = foreign = unlisted = 0
val_resolved = 0.0
agree = high = low = 0; val_agree = 0.0
low_cases = []
for isin, s in sec.items():
    if not idmap.is_valid_isin(isin):
        invalid += 1; continue
    rec = idmap.resolve_record(isin)
    if not rec:
        if idmap.is_india_equity_isin(isin): unlisted += 1
        else: foreign += 1
        continue
    resolved += 1; val_resolved += s["val"]
    j = jacc(s["name"], rec["name"])
    if j >= 0.5: agree += 1; val_agree += s["val"]
    if j >= 0.6: high += 1
    else:
        low += 1
        low_cases.append((round(j,2), s["name"], rec["name"], rec["symbol"], isin))

print("=== TEST 1: name agreement vs OUR idmap master ===")
print(f"unique Cline securities: {n_total}  (total Rs held {val_total/1e5:.0f} cr)")
print(f"  invalid/dummy ISIN: {invalid}   valid-but-not-in-our-master: india_unlisted={unlisted} foreign={foreign}")
print(f"  RESOLVED to our master: {resolved}  ({100*resolved/n_total:.1f}% of names, "
      f"{100*val_resolved/val_total:.1f}% of Rs held)")
print(f"  of resolved -> name AGREE (Jaccard>=0.5): {agree}/{resolved} = {100*agree/max(1,resolved):.1f}% "
      f"(by Rs held {100*val_agree/max(1,val_resolved):.1f}%)   high(>=0.6)={high} low={low}")
print(f"\n  lowest-agreement cases (potential identity mismatch, eyeball):")
for j, cn, on, sym, isin in sorted(low_cases)[:25]:
    print(f"    j={j}  cline='{cn[:34]}'  ours='{on[:30]}' [{sym}] {isin}")

# --- TEST 2: Co_Code split-ISIN lineage corroboration ---
try:
    aud = json.load(open(r"data\funds\_cocode_audit.json", encoding="utf-8"))
    multi = aud.get("co_multiisin", {})
except Exception:
    multi = {c: sorted(v) for c, v in co_isins.items() if len(v) > 1}
print(f"\n=== TEST 2: Co_Code split-ISIN -> single vst_id for us? ({len(multi)} cases) ===")
same = diff = partial = 0
for co, isins in multi.items():
    vids = {idmap.resolve_to_vid(z) for z in isins}
    known = {v for v in vids if v}
    if not known: partial += 1; verdict = "neither in our master"
    elif len(known) == 1 and None not in vids: same += 1; verdict = f"SAME vst_id {list(known)[0]}"
    elif len(known) == 1: partial += 1; verdict = f"one maps ({list(known)[0]}), other unknown"
    else: diff += 1; verdict = f"DIFFERENT vids {known}  <-- conflict"
    if diff and verdict.endswith("conflict") or len(multi) <= 30:
        print(f"   Co {co}: {isins} -> {verdict}")
print(f"  summary: SAME-vid={same}  one-known={partial}  DIFFERENT-vid(conflict)={diff}")
print("\n(name disagreements are dumped above; TEST2 conflicts are the ones that would break a join.)")
