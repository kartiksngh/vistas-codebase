import sys, time
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
from vistas import amc_live as al, amc_replay as ar, amc_firm as af
af._prices(); af._arm_raw(); ar._turn_med()
ents = al.pilot_reg_entries(min_aum_cr=500.0)
quant=[e for e in ents if e["code"]=="52"][0]
af.LIQ_DAYS_MAX = 250  # champion
print("Quant champion (LDM=250) validation 2013-04..2020-12, cost sensitivity:", flush=True)
for cb in [15.0, 30.0, 50.0]:
    t1=time.time(); nav,mon,sc,dg=ar.replay(quant,start="2013-04-01",end="2020-12-31",cost_bps=cb,log=lambda *_:None)
    b=sc.get("benchmark") or {}
    print("  cost=%2dbps IR=%s beta=%s excess=%s turn=%s%% [%.0fs]"%(
      int(cb),b.get("info_ratio"),b.get("beta"),b.get("excess_cagr_pct"),dg.get("avg_oneway_turnover_pct"),time.time()-t1), flush=True)
print("DONE", flush=True)
