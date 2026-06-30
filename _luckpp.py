import sys
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
import _fm_harness as h
from vistas import amc_live as al, amc_replay as ar, amc_firm as af
af._prices(); af._arm_raw(); ar._turn_med()
ents = al.pilot_reg_entries(min_aum_cr=500.0)
for re_ in ents:
    bid = af.brain_for_mandate(re_.get("category"), re_.get("mandate"))
    nav,mon,sc,dg = ar.replay(re_, start=h.FULL_START, end=h.VAL_END, brain_id=bid, log=lambda *_:None)
    bnav = ar.benchmark_nav_series(nav, re_)
    p = h._block_bootstrap_luck(nav, bnav)
    b = sc.get("benchmark") or {}
    print("LUCK %-34s IR=%5s  p(active>0)=%s"%(re_["scheme"][:34], b.get("info_ratio"), round(p,3) if p==p else None))
print("DONE")
