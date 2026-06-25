"""REALITY CHECK (KV): does the holdings-implied 'paper' return track the fund's ACTUAL NAV return?
Compares R_p (engine's holdings-implied) vs the real Direct-Growth NAV return for Nippon Small Cap.
high monthly correlation = the engine's portfolio return is real, not fabricated; the CAGR gap
(NAV − holdings-implied) = the return gap = -(fees+cash drag)+trading (Kacperczyk-Sialm-Zheng)."""
import json, sys, numpy as np, pandas as pd
sys.path.insert(0, ".")
from vistas import funds_nav

TARGET = "nippon india small cap"
man = json.load(open("data/funds_attribution/_manifest.json", encoding="utf-8"))
key = [k for k, v in man.items() if TARGET in (v.get("name") or "").lower()][0]
rec = json.load(open(f"data/funds_attribution/{key}.json", encoding="utf-8"))
ts = pd.DataFrame(rec["ts"])[["ym", "rp", "rb"]].dropna()        # rp = t->t+1 holdings-implied; rb = t->t+1 bench
print(f"scheme: {rec['scheme_name']} (code {key})  holdings months={len(ts)}")

nav = funds_nav.load_named()
cols = [c for c in nav.columns if "nippon" in c.lower() and "small cap" in c.lower()]
print("NAV column(s) matched:", cols[:3])
ns = pd.to_numeric(nav[cols[0]], errors="coerce").dropna()
ns.index = pd.to_datetime(ns.index)
nav_me = ns.resample("ME").last()
nav_ret = nav_me.pct_change().dropna()
nav_ret.index = nav_ret.index.strftime("%Y-%m")                 # return ENDING at month m
months = list(nav_ret.index)
nxt = {m: months[i + 1] for i, m in enumerate(months[:-1])}
navfwd = pd.DataFrame({"ym": list(nxt.keys()), "nav": [float(nav_ret[nxt[m]]) for m in nxt]})  # forward: t->t+1

m = ts.merge(navfwd, on="ym", how="inner").dropna()
yrs = len(m) / 12
cagr = lambda s: (np.prod(1 + s.values)) ** (1 / yrs) - 1
print(f"\naligned months: {len(m)}  {m.ym.min()}..{m.ym.max()}  ({yrs:.1f}y)")
print(f"corr(holdings-implied R_p , actual NAV return) = {m.rp.corr(m['nav']):.4f}   <- the validation")
print(f"corr(benchmark R_b       , actual NAV return) = {m.rb.corr(m['nav']):.4f}")
print(f"median |R_p - NAV| monthly gap = {float((m.rp - m['nav']).abs().median())*100:.2f}%")
print()
print(f"holdings-implied (GROSS) CAGR : {cagr(m.rp)*100:6.2f}%/yr")
print(f"ACTUAL NAV (Direct-G) CAGR    : {cagr(m['nav'])*100:6.2f}%/yr")
print(f"benchmark CAGR                : {cagr(m.rb)*100:6.2f}%/yr")
print(f"RETURN GAP (NAV − holdings)   : {(cagr(m['nav'])-cagr(m.rp))*100:+6.2f}%/yr  = -(fees+cash drag)+trading")
print(f"NAV excess vs benchmark       : {(cagr(m['nav'])-cagr(m.rb))*100:+6.2f}%/yr  (what investors ACTUALLY beat the index by)")
print(f"engine's gross excess vs bench: {(cagr(m.rp)-cagr(m.rb))*100:+6.2f}%/yr  (the Fund-Skill-tab number)")
