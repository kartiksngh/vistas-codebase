"""DEEP-HISTORY BEDROCK: independently re-derive Co_Code -> our vst_id across all 13 years of the
Cline history store, and cross-check vs (a) our idmap and (b) KV's hand-built 'Final ISIN'.
Tests: coverage, Co_Code->single-entity consistency, and whether his Final ISIN AGREES with our
independent resolution (disagreements = the stale/wrong mappings to investigate). Verify, don't trust.
"""
import sys
from collections import defaultdict, Counter
import pandas as pd
sys.path.insert(0, r"C:\Users\Administrator\Documents\Projects\Vistas")
from vistas import idmap

CSV = r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Update December 2025\Portfolio Update\Cline Portfolio Data Jan'10 to Oct'25 - updated Dec'25.csv"
USE = ["Co_Code", "Reported ISIN", "Final ISIN", "Portfolio Company Name", "Investment Type", "Market value"]

co_names = defaultdict(Counter); co_risin = defaultdict(set); co_fisin = defaultdict(set)
co_equity = defaultdict(bool); co_val = defaultdict(float)
rows = 0
for ch in pd.read_csv(CSV, usecols=USE, chunksize=300000, dtype=str):
    rows += len(ch)
    eq = ch["Investment Type"].astype(str).str.lower().str.contains("equity", na=False)
    for co, ri, fi, nm, isq, mv in zip(ch["Co_Code"], ch["Reported ISIN"], ch["Final ISIN"],
                                       ch["Portfolio Company Name"], eq, ch["Market value"]):
        if pd.isna(co): continue
        if isq: co_equity[co] = True
        if isinstance(nm, str) and nm.strip(): co_names[co][nm.strip()] += 1
        z = idmap.normalize_isin(ri);  co_risin[co].add(z) if idmap.is_valid_isin(z) else None
        f = idmap.normalize_isin(fi);  co_fisin[co].add(f) if idmap.is_valid_isin(f) else None
        if isq:
            try: co_val[co] += float(mv)
            except (TypeError, ValueError): pass
print(f"rows scanned={rows:,}   unique Co_Code={len(co_names):,}   equity Co_Code={sum(co_equity.values()):,}")

eq_cos = [c for c in co_names if co_equity.get(c)]
val_tot = sum(co_val.get(c, 0.0) for c in eq_cos) or 1.0
resolved = 0; val_res = 0.0; multi = []; agree = 0; val_agree = 0; his_present = 0; disagree = []
for c in eq_cos:
    our_vids = {v for z in (co_risin[c] | co_fisin[c]) if (v := idmap.resolve_to_vid(z))}
    if not our_vids:
        continue
    resolved += 1; val_res += co_val.get(c, 0.0)
    if len(our_vids) > 1:
        multi.append((c, our_vids, co_names[c].most_common(1)[0][0]))
        our_consensus = None
    else:
        our_consensus = next(iter(our_vids))
    his = sorted(co_fisin[c])
    if his:
        his_present += 1
        his_vid = idmap.resolve_to_vid(his[0])
        if our_consensus and his_vid == our_consensus:
            agree += 1; val_agree += co_val.get(c, 0.0)
        elif our_consensus and his_vid and his_vid != our_consensus:
            disagree.append((c, co_names[c].most_common(1)[0][0], his[0], his_vid, our_consensus))

print(f"\n=== COVERAGE (equity Co_Codes -> our vst_id) ===")
print(f"resolved: {resolved}/{len(eq_cos)} = {100*resolved/len(eq_cos):.1f}% of equity companies, "
      f"{100*val_res/val_tot:.1f}% of nominal equity Rs (summed across all periods)")
print(f"\n=== Co_Code -> SINGLE entity in our lineage? ===")
print(f"  clean (1 vst_id): {resolved-len(multi)}   MULTI vst_id (investigate): {len(multi)}")
for c, vids, nm in multi[:15]:
    print(f"    Co {c} '{nm[:34]}' -> {sorted(vids)}")
print(f"\n=== his Final ISIN vs OUR independent resolution ===")
print(f"  Co_Codes with his Final ISIN + our single vid: {agree+len(disagree)}")
print(f"  AGREE: {agree}  ({100*val_agree/val_tot:.1f}% of equity Rs)   DISAGREE: {len(disagree)}")
for c, nm, hf, hv, ov in disagree[:25]:
    hr = idmap.vid_record(hv); orr = idmap.vid_record(ov)
    print(f"    Co {c} '{nm[:26]}': his {hf}->{(hr or {}).get('name','?')[:20]} [{hv}]  "
          f"vs ours [{ov}]->{(orr or {}).get('name','?')[:20]}")
print(f"\n(MULTI = a Co_Code our lineage splits into >1 entity; DISAGREE = his hand map vs our derivation.)")
