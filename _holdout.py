import sys, time
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
import _fm_harness as h
from vistas import amc_live as al, amc_replay as ar, amc_firm as af
af._prices(); af._arm_raw(); ar._turn_med()
ents = al.pilot_reg_entries(min_aum_cr=500.0)
def runset(ldm, label):
    af.LIQ_DAYS_MAX = ldm
    irs=[]; betas=[]
    print(f"== HOLDOUT {label} (LIQ_DAYS_MAX={ldm}) seed {h.HOLD_START} -> end ==", flush=True)
    for re_ in ents:
        bid = af.brain_for_mandate(re_.get("category"), re_.get("mandate"))
        t1=time.time()
        nav,mon,sc,dg = ar.replay(re_, start=h.HOLD_START, end=None, brain_id=bid, log=lambda *_:None)
        b=sc.get("benchmark") or {}; fl=sc.get("fundamental_law") or {}
        import numpy as np
        eqm=np.mean([m.get("equity_pct") for m in mon if m.get("equity_pct") is not None])
        ir=b.get("info_ratio"); 
        if ir is not None: irs.append(ir)
        if b.get("beta") is not None: betas.append(b.get("beta"))
        print("  H %-32s IR=%5s beta=%4s eq%%=%.0f TC=%s excess=%s maxdd=%s [%.0fs]"%(
          re_["scheme"][:32], ir, b.get("beta"), eqm, fl.get("transfer_coefficient"),
          b.get("excess_cagr_pct"), sc.get("book",{}).get("maxdd_pct"), time.time()-t1), flush=True)
    mir = sum(irs)/len(irs) if irs else float('nan')
    mb = sum(betas)/len(betas) if betas else float('nan')
    print("  HOLDOUT_%s_OBJECTIVE\t%.6f\tmean_ir=%.4f\tmean_beta=%.3f"%(label, -mir, mir, mb), flush=True)
runset(60, "BASELINE")
runset(250, "CHAMPION")
print("DONE", flush=True)
