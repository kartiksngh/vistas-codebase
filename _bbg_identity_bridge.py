"""BBG ISIN Map -> unified identity bridge: Final ISIN <-> BBG ticker <-> name <-> our vst_id.
The 3rd witness. Measures how much it extends identity coverage (esp. the delisted tail) and
cross-checks names where we resolve. Publishable (KV: all BBG data here is public)."""
import os, re, sys, json
import pandas as pd
sys.path.insert(0, r"C:\Users\Administrator\Documents\Projects\Vistas")
from vistas import idmap

MAP = r"C:\Users\Administrator\Documents\Projects\MoneyBall\Cline Data on Portfolio, NAV\Update December 2025\Portfolio Update\BBG Data\ISIN Map Cline Portfolio Data Jan'10 to Oct'25 - updated Dec'25.xlsx"
OUT = r"C:\Users\Administrator\Documents\Projects\Vistas\data\funds\history\bbg_identity_bridge.csv"

def toks(s):
    s = re.sub(r"\b(LTD|LIMITED|THE|AND|CO|CORP|CORPORATION|INDIA|INDIAN)\b"," ",str(s or "").upper())
    return set(re.findall(r"[A-Z0-9]+", s))
def jacc(a,b):
    A,B=toks(a),toks(b); return len(A&B)/len(A|B) if (A|B) else 0.0

df = pd.read_excel(MAP, dtype=str)
# columns: [idx, 'Final ISIN', 'Bloomberg Ticker', 'Company name from BBG']
cols = {c.lower().strip(): c for c in df.columns}
cf = next(c for k,c in cols.items() if "final isin" in k)
ct = next(c for k,c in cols.items() if "ticker" in k)
cn = next(c for k,c in cols.items() if "company" in k and "unnamed" not in k)  # not 'Unnamed: 0'!
print(f"ISIN map rows={len(df)}  cols={list(df.columns)}")

rows=[]; res=unlisted=valid_not_master=agree=0; aglist=[]
for _,r in df.iterrows():
    fisin = idmap.normalize_isin(r[cf]); tk=str(r[ct] or "").strip(); nm=str(r[cn] or "").strip()
    if not idmap.is_valid_isin(fisin):
        unlisted += 1; status="unlisted_or_invalid"; vid=our_nm=sym=None
    else:
        vid = idmap.resolve_to_vid(fisin)
        if vid:
            rec = idmap.vid_record(vid) or {}; our_nm=rec.get("name"); sym=rec.get("nse_symbol")
            res += 1; status="resolved_vst"
            j=jacc(nm, our_nm or "")
            if j>=0.5: agree+=1
            else: aglist.append((round(j,2), nm[:28], (our_nm or "")[:24], sym, fisin))
        else:
            valid_not_master += 1; status="valid_isin_not_in_master"; our_nm=sym=None
    rows.append({"final_isin":fisin if idmap.is_valid_isin(fisin) else str(r[cf]),
                 "bbg_ticker":tk, "bbg_name":nm, "vst_id":vid, "nse_symbol":sym,
                 "our_name":our_nm, "status":status})
out=pd.DataFrame(rows); out.to_csv(OUT, index=False)
N=len(df)
print(f"\nresolved to our vst_id: {res}/{N} = {100*res/N:.1f}%")
print(f"valid ISIN but NOT in our NSE master (delisted/unlisted real): {valid_not_master} "
      f"= {100*valid_not_master/N:.1f}%  (BBG ticker gives them price history)")
print(f"'Unlisted'/invalid Final ISIN: {unlisted} = {100*unlisted/N:.1f}%")
print(f"name agreement (resolved, Jaccard>=0.5): {agree}/{res} = {100*agree/max(1,res):.1f}%")
print(f"\nlow-name-agreement (verify these aren't mis-maps):")
for j,bn,on,sym,iz in sorted(aglist)[:18]:
    print(f"   j={j}  bbg='{bn}'  ours='{on}' [{sym}] {iz}")
print(f"\nwrote {OUT}  ({N} rows: final_isin/bbg_ticker/bbg_name/vst_id/nse_symbol/status)")
# how many distinct BBG tickers in the price matrix can now map to a vst_id?
print(f"\nbridge: {res} BBG tickers -> our vst_id directly; +{valid_not_master} priced delisted names "
      f"identified by ISIN+ticker (attributable via BBG prices, no vst_id needed yet).")
