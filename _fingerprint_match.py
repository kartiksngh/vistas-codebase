"""
_fingerprint_match.py — PROOF-OF-CONCEPT for the return-fingerprint scheme-identity crack.

Claim: a scheme's monthly RETURN SERIES is a near-unique, name-invariant fingerprint. Match each
holdings-keyed navindia_code to its AMFI scheme by MAX correlation of (holdings-implied return) vs
(actual NAV return). True match ~0.99; the separation from the 2nd-best tells us how decisive it is.

This validates the approach before we build the full spine. Diagnostics only.
"""
import os, json, glob
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
ATTR = os.path.join(ROOT, "data", "funds_attribution")
NAVDIR = os.path.join(ROOT, "data", "funds", "nav")

# 1) AMFI NAV -> monthly return matrix (ym -> ret), per amfi_code
def nav_monthly_returns():
    out = {}
    meta = {}
    for f in glob.glob(os.path.join(NAVDIR, "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        dates = d.get("dates") or []; nav = d.get("nav") or []
        if len(dates) < 60:
            continue
        s = pd.Series(nav, index=pd.to_datetime(dates)).sort_index()
        me = s.resample("ME").last()
        r = me.pct_change().dropna()
        r.index = r.index.strftime("%Y-%m")
        if len(r) >= 24:
            out[str(d.get("code"))] = r
            meta[str(d.get("code"))] = {"name": d.get("name", ""), "amc": d.get("fund_house", ""), "isin": d.get("isin", "")}
    return out, meta

# 2) holdings-implied monthly return per navindia_code, from attribution ts[].rp.
#    rp at ym=t is the t->t+1 return (forward). Relabel to month t+1 so it aligns with a NAV return
#    "ending month M".
def holdings_returns():
    out = {}; meta = {}
    for f in glob.glob(os.path.join(ATTR, "*.json")):
        if f.endswith("_manifest.json"):
            continue
        d = json.load(open(f, encoding="utf-8"))
        ts = d.get("ts") or []
        if len(ts) < 24:
            continue
        yms = [p["ym"] for p in ts]; rp = [p.get("rp") for p in ts]
        # relabel ym=t -> t+1
        def nextm(ym):
            y, m = int(ym[:4]), int(ym[5:7]); m += 1
            if m > 12: y, m = y + 1, 1
            return f"{y:04d}-{m:02d}"
        s = pd.Series(rp, index=[nextm(y) for y in yms]).dropna()
        if len(s) >= 24:
            out[str(d.get("navindia_code"))] = s
            meta[str(d.get("navindia_code"))] = {"name": d.get("scheme_name", ""), "amc": d.get("amc", ""), "cat": d.get("sebi_category", "")}
    return out, meta


def _norm(s):
    import re
    s = str(s).lower()
    s = re.sub(r"\(g\)|\(idcw\)|growth|idcw|dividend|direct|regular|plan|option|fund|scheme|-", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def main():
    nav, navmeta = nav_monthly_returns()
    hold, holdmeta = holdings_returns()
    print(f"AMFI NAV return series: {len(nav)} | holdings-implied series: {len(hold)}")
    navmat = pd.DataFrame(nav)   # index=ym, cols=amfi_code

    rows = []
    for code, hr in hold.items():
        common = navmat.index.intersection(hr.index)
        if len(common) < 24:
            rows.append(dict(code=code, best=None, corr=np.nan, n=len(common), gap=np.nan)); continue
        H = hr.reindex(common)
        sub = navmat.reindex(common)
        # IDIOSYNCRATIC fingerprint: rank by TRACKING ERROR = std(holdings_implied - nav). A true match
        # differs only by a small, low-variance fee/cash-drag/trading gap; a wrong match (even if highly
        # market-correlated) has a big idiosyncratic difference. te in monthly return units.
        tes = {}
        for amfi in sub.columns:
            col = sub[amfi]; ok = col.notna() & H.notna()
            if ok.sum() < 24:
                continue
            d = (H[ok] - col[ok])
            tes[amfi] = float(d.std())
        if not tes:
            rows.append(dict(code=code, best=None, te=np.nan, n=len(common), ratio=np.nan)); continue
        ranked = sorted(tes.items(), key=lambda kv: kv[1])   # MIN tracking error first
        best, bte = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else np.nan
        ratio = (second / bte) if (bte and bte > 0) else np.nan   # decisiveness: 2nd-best TE / best TE
        hn = _norm(holdmeta[code]["name"]); nn = _norm(navmeta[best]["name"])
        name_agree = (hn == nn) or (hn in nn) or (nn in hn) or (len(set(hn.split()) & set(nn.split())) >= 2)
        rows.append(dict(code=code, hold_name=holdmeta[code]["name"], best=best, best_name=navmeta[best]["name"],
                         te=round(bte, 5), ratio=round(ratio, 2), n=len(common), name_agree=bool(name_agree)))
    R = pd.DataFrame(rows)
    matched = R[R["te"].notna()]
    te = matched["te"]
    print(f"\nholdings series with >=24mo NAV overlap: {len(matched)} / {len(R)}")
    print(f"best tracking-error (monthly std of hold-nav): median={te.median()*100:.2f}%  p25={te.quantile(.25)*100:.2f}%  p75={te.quantile(.75)*100:.2f}%")
    # a CONFIDENT match: low absolute TE AND decisively better than the 2nd-best
    conf = matched[(matched["te"] <= 0.010) & (matched["ratio"] >= 1.5)]
    print(f"CONFIDENT (TE<=1.0%/mo AND 2nd-best TE >= 1.5x best): {len(conf)} ({100*len(conf)/len(matched):.0f}%)")
    print(f"  of those, NAME independently agrees: {100*conf['name_agree'].mean():.0f}%  (cross-validation)")
    print(f"  CONFIDENT but name DISAGREES (real renames the name-match misses): {int((~conf['name_agree']).sum())}")
    loose = matched[matched["te"] <= 0.010]
    print(f"low-TE (<=1.0%/mo) regardless of decisiveness: {100*len(loose)/len(matched):.0f}%")
    dis = conf[~conf["name_agree"]].head(14)
    if len(dis):
        print("\n--- CONFIDENT fingerprint match where NAME disagreed (true renames the name-match would miss) ---")
        print(dis[["hold_name", "best_name", "te", "ratio", "n"]].to_string(index=False))
    amb = matched[(matched["te"] <= 0.010) & (matched["ratio"] < 1.5)].head(8)
    if len(amb):
        print(f"\n--- low-TE but AMBIGUOUS (2nd-best close: clones/share-classes) — {len(matched[(matched['te']<=0.010)&(matched['ratio']<1.5)])} cases ---")
        print(amb[["hold_name", "best_name", "te", "ratio"]].to_string(index=False))
    R.to_csv(os.path.join(ROOT, "data", "funds", "_fingerprint_match.csv"), index=False)
    print("\nwrote data/funds/_fingerprint_match.csv")


if __name__ == "__main__":
    main()
