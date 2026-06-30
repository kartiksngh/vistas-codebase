"""Scratch smoke-test for skill_engine.compute_skill on ~5 real funds (NO build_all, NO lock, NO publish).
Run: python -m vistas._skill_engine_selfcheck
"""
from __future__ import annotations
import json, os, pickle, time, sys

import numpy as np
import pandas as pd

from vistas import funds_attribution as fa
from vistas import skill_signals as ssig
from vistas import skill_factors as sf
from vistas import skill_engine as se

_SC = os.environ.get("SE_CACHE", "")   # if set, load legs/fwd/uni/flows from this dir (fast reruns)


def main():
    t0 = time.time()
    log = lambda *a: print(*a, flush=True)

    # 1) legacy scheme records + per-fund panel slices (the substrate compute_skill READS)
    panel = fa.load_panel()
    recs = fa.scheme_metrics(panel)
    by_code = {str(r["navindia_code"]): r for r in recs}
    log(f"[selfcheck] panel + {len(recs)} scheme records in {time.time()-t0:.0f}s")

    # 2) prior table (built ONCE) + factor legs (built ONCE)
    prior_table = se.build_prior_table(recs)
    log(f"[selfcheck] prior_table cats={[k for k in prior_table if k!='_universe']}")
    log(f"[selfcheck] _universe={prior_table['_universe']}")
    if _SC and os.path.exists(os.path.join(_SC, "legs.pkl")):
        legs = pd.read_pickle(os.path.join(_SC, "legs.pkl"))
        log(f"[selfcheck] legs LOADED from cache: {legs.shape[0]} months in {time.time()-t0:.0f}s")
    else:
        legs = sf.get_factor_legs(log=lambda *a: None)
        log(f"[selfcheck] legs built: {legs.shape[0]} months in {time.time()-t0:.0f}s")

    # 3) pick 5 real funds with long history across a few categories
    cnt = panel.groupby("navindia_code")["ym"].count().sort_values(ascending=False)
    picks = []
    seen_cat = set()
    for code in cnt.index:
        code = str(code)
        r = by_code.get(code)
        if not r:
            continue
        cat = r.get("sebi_category")
        if cat in seen_cat and len(picks) >= 3:
            continue
        seen_cat.add(cat)
        picks.append(code)
        if len(picks) >= 5:
            break
    log(f"[selfcheck] picks={picks}")

    # 4) shared Component-A substrate (built once, reused across the picks)
    from vistas import funds_flows as ff
    h_hold = None
    if _SC and os.path.exists(os.path.join(_SC, "fwd.pkl")):
        with open(os.path.join(_SC, "fwd.pkl"), "rb") as f:
            fwd_by_k = pickle.load(f)
        with open(os.path.join(_SC, "uni.pkl"), "rb") as f:
            universe = pickle.load(f)
        h_flow_df = pd.read_pickle(os.path.join(_SC, "hflow.pkl"))
        ret_ser = pd.read_pickle(os.path.join(_SC, "ret.pkl"))
        log(f"[selfcheck] substrate LOADED from cache in {time.time()-t0:.0f}s")
    else:
        fwd_by_k = ssig.build_fwd_returns()
        universe = ssig.build_universe_fwd(fwd_by_k.get(1))
        h_flow_df, ret_ser = ff._load()
        log(f"[selfcheck] shared substrate in {time.time()-t0:.0f}s")

    import traceback
    n_pick = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    picks = picks[:n_pick]
    cons_cache, bench_cache = {}, {}
    skill_by_fund = {}
    for code in picks:
        r = by_code[code]
        cat = r.get("sebi_category")
        d = panel[panel["navindia_code"].astype(str) == code]
        try:
            if cat not in cons_cache:
                cons_cache[cat] = ssig.build_consensus_by_ym(cat)
                bench_cache[cat] = ssig.build_bench_fwd(cat)
            shared = {"fwd_by_k": fwd_by_k, "ret": ret_ser, "h_flow": h_flow_df, "h_hold": h_hold,
                      "consensus": cons_cache[cat], "universe": universe, "bench_fwd": bench_cache[cat]}
            sk = se.compute_skill(r, d, None, legs, prior_table, build_id="2026-06-30", shared=shared)
            sk["_category"] = cat
            skill_by_fund[code] = sk
            log(f"  computed {code} {r.get('scheme_name')} ({cat}) in {time.time()-t0:.0f}s")
        except Exception as e:
            log(f"  ERROR on {code} ({cat}): {e}")
            traceback.print_exc()
    picks = [c for c in picks if c in skill_by_fund]
    if not picks:
        log("[selfcheck] NO funds computed — abort")
        return 1

    # 5) book-level FDR + rank over the 5 picks
    se.fdr_and_rank(skill_by_fund)

    # 6) print one full block + sanity asserts on all
    code0 = picks[0]
    log("\n================ FULL skill BLOCK for one fund ================")
    log(f"{by_code[code0].get('scheme_name')} ({by_code[code0].get('sebi_category')}) code={code0}")
    log(json.dumps(skill_by_fund[code0], indent=2, default=str))

    log("\n================ SANITY across the 5 picks ================")
    ok = True
    for code in picks:
        sk = skill_by_fund[code]
        post = sk["posterior"]; rails = sk["rails"]
        p = post["p_skilled"]; lo, best, hi = post["lo90"], post["best"], post["hi90"]
        r = by_code[code]
        gross = r.get("excess_cagr")
        checks = []
        checks.append(("p_skilled in [0,1]", p is not None and 0.0 <= p <= 1.0))
        checks.append(("lo90<=best<=hi90", lo <= best <= hi))
        # rails only LOWER vs gross: residual factor alpha <= gross excess (within tol), net<=gross
        net_ex = rails.get("net_excess_ann")
        if gross is not None and net_ex is not None:
            checks.append(("net_excess<=gross", net_ex <= gross + 1e-9))
        ra = rails.get("residual_alpha_ann")
        if gross is not None and ra is not None and gross > 0:
            checks.append(("residual_alpha<=gross (factor only lowers a + edge)", ra <= gross + 1e-6))
        checks.append(("tag is a known state", sk["tag"] in {
            "skilled", "likely_skilled", "unproven", "likely_unskilled", "lagging",
            "insufficient_history", "index-like"}))
        checks.append(("rank decile 1..10", 1 <= (sk["rank"].get("decile") or 0) <= 10))
        line = f"{code:>6} {sk['tag']:<20} p={p:.2f} best={best:+.4f} CI=[{lo:+.4f},{hi:+.4f}] " \
               f"fdr={rails.get('passes_fdr')} dec={sk['rank'].get('decile')} " \
               f"ter={rails.get('ter_annual')} resid_a={ra}"
        log(line)
        for nm, c in checks:
            if not c:
                ok = False
                log(f"      FAIL: {nm}")
    log(f"\n[selfcheck] ALL SANE: {ok}   total {time.time()-t0:.0f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
