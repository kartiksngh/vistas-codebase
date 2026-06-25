"""Co_Code -> vst_id MASTER (prototype) via MULTI-SIGNAL VOTING, and audit of the 111 multi-vid
cases. For each equity Co_Code: resolve every reported/final ISIN it ever carried -> our vst_id,
then VOTE = the vid backed by the most ISIN-occurrences AND the best company-name match. The rare
wrong ISIN (the contaminant) loses the vote. Outputs the master + a review queue. Verify, don't trust.
"""
import sys, re, json
from collections import defaultdict, Counter
import pandas as pd
sys.path.insert(0, r"C:\Users\Administrator\Documents\Projects\Vistas")
from vistas import idmap

CSV = r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Update December 2025\Portfolio Update\Cline Portfolio Data Jan'10 to Oct'25 - updated Dec'25.csv"

def toks(s):
    s = re.sub(r"\b(LTD|LIMITED|THE|AND|CO|CORPORATION|INDIA|INDIAN)\b", " ", str(s or "").upper())
    return set(re.findall(r"[A-Z0-9]+", s))
def jacc(a, b):
    A, B = toks(a), toks(b);  return len(A & B) / len(A | B) if (A | B) else 0.0

co_isin_ct = defaultdict(Counter)   # co -> Counter(valid reported/final isin -> occurrences)
co_name_ct = defaultdict(Counter)   # co -> Counter(name)
co_his     = defaultdict(set)       # co -> {his Final ISIN}
co_equity  = defaultdict(bool); co_val = defaultdict(float)
for ch in pd.read_csv(CSV, usecols=["Co_Code","Reported ISIN","Final ISIN","Portfolio Company Name",
                                     "Investment Type","Market value"], chunksize=300000, dtype=str):
    eq = ch["Investment Type"].astype(str).str.lower().str.contains("equity", na=False)
    for co, ri, fi, nm, isq, mv in zip(ch["Co_Code"], ch["Reported ISIN"], ch["Final ISIN"],
                                       ch["Portfolio Company Name"], eq, ch["Market value"]):
        if pd.isna(co): continue
        if isq: co_equity[co] = True
        if isinstance(nm, str) and nm.strip(): co_name_ct[co][nm.strip()] += 1
        z = idmap.normalize_isin(ri)
        if idmap.is_valid_isin(z): co_isin_ct[co][z] += 1
        f = idmap.normalize_isin(fi)
        if idmap.is_valid_isin(f): co_his[co].add(f)
        if isq:
            try: co_val[co] += float(mv)
            except (TypeError, ValueError): pass

eq_cos = [c for c in co_name_ct if co_equity.get(c)]
val_tot = sum(co_val.get(c,0.0) for c in eq_cos) or 1.0
master = {}; review = []; multi_fixed = 0; multi_total = 0; agree_his = 0
resolved = 0; val_res = 0.0
for c in eq_cos:
    name = co_name_ct[c].most_common(1)[0][0]
    vid_score = defaultdict(float); vid_isins = defaultdict(list)
    for z, ct in co_isin_ct[c].items():
        v = idmap.resolve_to_vid(z)
        if v:
            vid_score[v] += ct; vid_isins[v].append((z, ct))
    if not vid_score:
        continue
    resolved += 1; val_res += co_val.get(c, 0.0)
    cand = list(vid_score)
    if len(cand) > 1: multi_total += 1
    # VOTE: combine ISIN-occurrence share with name-match to the vid's master name
    def score(v):
        nm = (idmap.vid_record(v) or {}).get("name", "")
        share = vid_score[v] / sum(vid_score.values())
        return share + 1.5 * jacc(name, nm)
    voted = max(cand, key=score)
    vr = idmap.vid_record(voted) or {}
    nmj = jacc(name, vr.get("name",""))
    # confidence: clean single-vid OR vote clearly backed by name + plurality
    if len(cand) == 1:
        conf = "high"
    elif nmj >= 0.34 and vid_score[voted] >= max(v for k,v in vid_score.items() if k != voted):
        conf = "high"; multi_fixed += 1
    else:
        conf = "review"; review.append((c, name, {idmap.vid_record(v).get("name","?") if idmap.vid_record(v) else "?":
                                                   round(vid_score[v]) for v in cand}))
    his = sorted(co_his[c]); his_vid = idmap.resolve_to_vid(his[0]) if his else None
    if his_vid and his_vid == voted: agree_his += 1
    master[c] = {"vst_id": voted, "name": name, "nse_symbol": vr.get("nse_symbol"),
                 "conf": conf, "n_isins": len(co_isin_ct[c]), "n_cand_vids": len(cand),
                 "his_final_vid": his_vid, "his_agrees": (his_vid == voted) if his_vid else None}

print(f"equity Co_Codes: {len(eq_cos)}   resolved: {resolved} ({100*val_res/val_tot:.1f}% of equity Rs)")
print(f"multi-vid cases: {multi_total}   FIXED by vote (name+plurality): {multi_fixed}   "
      f"-> review queue: {len(review)}")
print(f"his Final-ISIN vid agrees with our VOTE: {agree_his}/{sum(1 for c in master if master[c]['his_final_vid'])}")
print(f"\nreview queue (vote not confident — investigate):")
for c, nm, cands in review[:25]:
    print(f"   Co {c} '{nm[:30]}' -> {cands}")
json.dump({"master": master, "review": [{"co": c, "name": n, "cands": d} for c,n,d in review]},
          open(r"data\funds\_history_identity_map.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\nwrote data/funds/_history_identity_map.json  ({len(master)} Co_Code->vst_id, {len(review)} to review)")
