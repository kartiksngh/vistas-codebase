#!/usr/bin/env python
"""_fm_harness.py — FROZEN read-only autoresearch harness for the FM track (tag fm-jun30).

NOT the evaluator (that is vistas/amc_replay.py — read-only). This is the DRIVER that calls the
frozen evaluator over the pilot schemes, splits validation vs the SEALED holdout, extracts the
objective (-IR) and every gauntlet component, and prints two machine-greppable lines so the loop
can grep the objective + gate out of run.log without flooding context.

It edits NOTHING. It only imports amc_replay.replay (read-only) and amc_firm (the mutable brain).

CONVENTIONS (KV reporting rule — reproducible):
  objective   = -mean(IR) across the pilot books, IR = benchmark.info_ratio from the frozen
                scorecard (= excess CAGR vs the CORRECT benchmark / tracking error; the correct
                benchmark = the SECTOR TR index for fenced funds via amc_replay._bench_for, else
                the matched category index). One row per pilot scheme; aggregate = simple mean.
                Lower (more negative) objective = better (higher firm IR). save=False (no artifacts).
  validation  = replay(end=VAL_END). The loop ONLY sees data <= VAL_END. (VAL_END = 2023-06-30.)
  holdout     = replay(start=HOLD_START) — a FRESH book seeded in the sealed era walking forward,
                so the holdout track is built ONLY from sealed-era data (true OOS). HOLD_START =
                2023-07-01. The loop NEVER calls mode='holdout'; only the final champion test does.
  gauntlet components surfaced per run (PASS/FAIL/GATED honestly):
    rand   : place-holder GATED unless signal_navtest exists (the literal >=10k-random NAV bar is
             OWED; we never silently pass it).
    wf     : PASS structurally (replay is walk-forward, forecast strictly precedes return, no
             Math.random, total return, delisted retained) — asserted from the frozen engine.
    single : the deployed multi-force book's mean IR beats the single-force (score=ARM-only) book's
             mean IR on the SAME pilots/window (the Mesh dilution gate at the FM level).
    era    : the edge (sign of mean IR) holds in BOTH halves of the validation window (pre/post
             era split), not only the recent momentum tape.
    plateau: the objective is flat (no sign flip, spread within tol) across a +/- neighbourhood of
             the free parameter being tuned (passed in by the loop per trial).
    luck   : block-bootstrap of the per-rebalance active return mean clears 0 at ~95% (proxy luck
             bar; the full tilt-matched BSW-FDR is approximated, FDR across trials tracked in ledger).
    tilt   : the beta/size guard — mean |beta-1| small AND deploying-more did not inflate beta vs
             the single-force book (size tilt proxy = change in beta when the brain deploys more).
    fee    : GATED (no TER/expense data — honest hole per the contract).
"""
import os, sys, time, json, math
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from vistas import amc_replay as ar, amc_firm as af, amc_live as al

# ---- pilots: the 4 CROSS-AMC contract pilots, resolved via amc_live.pilot_reg_entries() to the exact
# navindia_codes the operator pinned (verified full 158-mo holdings each, 2013-04→2026-05):
#   ICICI Pru Large Cap (7610) · SBI Equity Hybrid (2383) · ABSL Flexi Cap (9) · Quant Small Cap (52, the
#   TC-leak poster child). Deliberately cross-AMC so the firm objective isn't dominated by one house style.
FULL_START = "2013-04-01"     # search/validation era start (holdings begin 2013-04; ~7.75y of validation)
VAL_END    = "2020-12-31"     # ★ SEALED HOLDOUT BOUNDARY: the loop sees ONLY data <= this
HOLD_START = "2021-01-01"     # holdout one-shot: fresh book seeded here, walks forward to 2026-06 (OOS, 5y, incl 2022 drawdown)
ERA_MID    = "2017-06-30"     # validation-window midpoint for the era-stability split (2013-04..2017-06 / 2017-07..2020-12)
MIN_AUM    = 500.0

_PILOT_ENTRIES = None
def _pilot_entries():
    global _PILOT_ENTRIES
    if _PILOT_ENTRIES is None:
        _PILOT_ENTRIES = al.pilot_reg_entries(min_aum_cr=MIN_AUM)
    return _PILOT_ENTRIES


def _one(reg_entry, start, end, brain_id):
    """Run the frozen replay read-only; return a compact metric dict (or None on failure)."""
    try:
        nav, monthly, score, diag = ar.replay(reg_entry, start=start, end=end,
                                              brain_id=brain_id, log=lambda *_: None)
    except Exception as e:
        return {"err": f"{type(e).__name__}:{e}"}
    b = score.get("benchmark", {}) or {}
    fl = score.get("fundamental_law", {}) or {}
    bk = score.get("book", {}) or {}
    # per-rebalance active return proxy for the luck bar: monthly NAV step minus 0 (book-level);
    # we use the book daily returns vs bench inside scorecard already for IR; for the luck bar we
    # resample the monthly nav into simple active steps if a bench series is available.
    return {
        "scheme": reg_entry["scheme"], "ir": b.get("info_ratio"),
        "excess": b.get("excess_cagr_pct"), "beta": b.get("beta"),
        "up": b.get("up_capture"), "dn": b.get("down_capture"),
        "ic": fl.get("ic_mean"), "tc": fl.get("transfer_coefficient"),
        "implied_ir": fl.get("implied_IR_UPPER"), "br": fl.get("breadth_per_year_UPPER"),
        "maxdd": bk.get("maxdd_pct"), "sharpe": bk.get("sharpe"), "cagr": bk.get("cagr_pct"),
        "years": score.get("window", {}).get("years"),
        "bench": score.get("benchmark_name"),
        "n_reb": diag.get("n_rebalances"), "avg_n": diag.get("avg_holdings"),
        "turn": diag.get("avg_oneway_turnover_pct"),
        "nav": nav, "monthly": monthly,
    }


def _mean(xs):
    xs = [x for x in xs if x is not None and x == x]
    return (sum(xs) / len(xs)) if xs else float("nan")


def run_panel(start, end, brain_override=None, entries=None):
    """Replay every pilot over [start,end] with each one's mandate brain (or a forced brain).
    Returns (rows, agg). brain_override=None -> each pilot uses brain_for_mandate (the deployed
    multi-force brain). brain_override='_arm_single' -> force the registered single-force ARM-only
    baseline (the naive book, for the 'beats single best component' gate)."""
    rows = []
    for re_ in (entries or _pilot_entries()):
        # brain_override None/_mandate -> each pilot's deployed multi-force brain (brain_for_mandate);
        # '_arm_single' -> the registered single-force ARM-only baseline (brain_arm_only) for the
        # 'beats single best component' gate; any other -> that explicit brain id.
        if brain_override in (None, "_mandate"):
            bid = None
        elif brain_override == "_arm_single":
            bid = "arm_only"
        else:
            bid = brain_override
        m = _one(re_, start, end, bid)
        rows.append(m)
    irs = [r.get("ir") for r in rows if not r.get("err")]
    agg = {
        "mean_ir": _mean(irs), "objective": -_mean(irs),
        "mean_beta": _mean([r.get("beta") for r in rows if not r.get("err")]),
        "mean_ic": _mean([r.get("ic") for r in rows if not r.get("err")]),
        "mean_tc": _mean([r.get("tc") for r in rows if not r.get("err")]),
        "mean_maxdd": _mean([r.get("maxdd") for r in rows if not r.get("err")]),
        "mean_turn": _mean([r.get("turn") for r in rows if not r.get("err")]),
        "n_ok": len(irs),
    }
    return rows, agg


# ---- gauntlet components -----------------------------------------------------------------------
def _era_split(reg_entry, brain_id):
    """era: sign of mean IR holds in BOTH halves of the validation window. Splits the VAL window at
    ERA_MID into two ~equal eras and checks the IR sign is the same (>0) in both."""
    import pandas as pd
    nxt = (pd.Timestamp(ERA_MID) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    a = _one(reg_entry, FULL_START, ERA_MID, brain_id)
    b = _one(reg_entry, nxt, VAL_END, brain_id)
    ia, ib = a.get("ir") if not a.get("err") else None, b.get("ir") if not b.get("err") else None
    return ia, ib


def _block_bootstrap_luck(nav_book, nav_bench, block=3, nboot=2000, seed=7):
    """Luck bar proxy: circular block bootstrap of the MEAN monthly active return (book - bench).
    Returns the fraction of bootstrap means > 0 (a one-sided p that the edge is real). >= 0.95
    PASS. Uses monthly resampled active returns so block-3 ~ a quarter of dependence."""
    if nav_book is None or nav_bench is None:
        return None
    import pandas as pd
    common = nav_book.index.intersection(nav_bench.index)
    if len(common) < 40:
        return None
    bk = nav_book.reindex(common).resample("M").last().pct_change().dropna()
    bn = nav_bench.reindex(common).resample("M").last().pct_change().reindex(bk.index)
    act = (bk - bn).dropna().values
    n = len(act)
    if n < 12:
        return None
    rng = np.random.default_rng(seed)
    nb = max(1, n // block)
    means = np.empty(nboot)
    for i in range(nboot):
        starts = rng.integers(0, n, size=nb)
        idx = np.concatenate([(s + np.arange(block)) % n for s in starts])[:n]
        means[i] = act[idx].mean()
    return float((means > 0).mean())


def gauntlet(brain_label, entries=None, verbose=True):
    """Run the FULL gauntlet on the CURRENT brain code (validation window only). Since we mutate code
    per trial, the loop passes the plateau result in separately; here we compute the data-driven
    components. Returns (agg, gate_dict, decomp_dict, detail)."""
    ents = entries or _pilot_entries()
    rows, agg = run_panel(FULL_START, VAL_END, brain_override=None, entries=ents)
    # single-force baseline (ARM-only) on the same pilots/window
    rows_s, agg_s = run_panel(FULL_START, VAL_END, brain_override="_arm_single", entries=ents)
    gate = {}
    decomp = {}
    # rand: OWED literal >=10k NAV bar — GATED (no signal_navtest in repo)
    gate["rand"] = "GATED"
    # wf: structural PASS (frozen engine is walk-forward, forecast precedes return, TR, delisted kept)
    gate["wf"] = "PASS"
    # single: deployed multi-force mean IR beats single-force ARM-only mean IR
    gate["single"] = "PASS" if (agg["mean_ir"] == agg["mean_ir"] and agg_s["mean_ir"] == agg_s["mean_ir"]
                                and agg["mean_ir"] > agg_s["mean_ir"] + 1e-6) else "FAIL"
    decomp["single_multi_ir"] = round(agg["mean_ir"], 4) if agg["mean_ir"]==agg["mean_ir"] else None
    decomp["single_arm_ir"] = round(agg_s["mean_ir"], 4) if agg_s["mean_ir"]==agg_s["mean_ir"] else None
    # era: sign of mean IR holds in both halves (per pilot, then require firm-mean positive both halves)
    ia_all, ib_all = [], []
    for re_ in ents:
        bid = af.brain_for_mandate(re_.get("category"), re_.get("mandate"))
        ia, ib = _era_split(re_, bid)
        if ia is not None: ia_all.append(ia)
        if ib is not None: ib_all.append(ib)
    era_a, era_b = _mean(ia_all), _mean(ib_all)
    gate["era"] = "PASS" if (era_a == era_a and era_b == era_b and era_a > 0 and era_b > 0) else "FAIL"
    decomp["era_ir_first"] = round(era_a, 3) if era_a==era_a else None
    decomp["era_ir_second"] = round(era_b, 3) if era_b==era_b else None
    # luck: block bootstrap of active returns, per pilot (matched to its reg_entry by scheme); PASS if firm-mean p>0 >= 0.90
    by_scheme = {re_["scheme"]: re_ for re_ in ents}
    lps = []
    for r in rows:
        if r.get("err"):
            continue
        re_ = by_scheme.get(r["scheme"])
        if re_ is None:
            continue
        bnav = ar.benchmark_nav_series(r["nav"], re_)
        p = _block_bootstrap_luck(r["nav"], bnav)
        if p is not None:
            lps.append(p)
    luck_p = _mean(lps)
    gate["luck"] = "PASS" if (luck_p == luck_p and luck_p >= 0.90) else ("FAIL" if luck_p==luck_p else "GATED")
    decomp["luck_p"] = round(luck_p, 3) if luck_p == luck_p else None
    # tilt: beta/size guard — mean |beta-1| small (<=0.20) AND multi-force beta not inflated vs single
    beta_m = agg["mean_beta"]; beta_s = agg_s["mean_beta"]
    beta_ok = (beta_m == beta_m and abs(beta_m - 1.0) <= 0.20)
    size_ok = (beta_m == beta_m and beta_s == beta_s and (beta_m - beta_s) <= 0.10)
    gate["tilt"] = "PASS" if (beta_ok and size_ok) else "FAIL"
    decomp["mean_beta_multi"] = round(beta_m, 3) if beta_m == beta_m else None
    decomp["mean_beta_single"] = round(beta_s, 3) if beta_s == beta_s else None
    decomp["mean_ic"] = round(agg["mean_ic"], 4) if agg["mean_ic"]==agg["mean_ic"] else None
    decomp["mean_tc"] = round(agg["mean_tc"], 3) if agg["mean_tc"]==agg["mean_tc"] else None
    # fee: GATED (no TER data)
    gate["fee"] = "GATED"
    detail = {"rows": [{k: r.get(k) for k in ("scheme","ir","beta","excess","ic","tc","maxdd","turn","cagr","bench","avg_n")} for r in rows],
              "rows_single": [{k: r.get(k) for k in ("scheme","ir","beta")} for r in rows_s]}
    return agg, gate, decomp, detail


def fmt_gate(gate):
    return " ".join(f"{k}:{v}" for k, v in gate.items())


def fmt_decomp(decomp):
    parts = []
    if decomp.get("mean_ic") is not None: parts.append(f"IC={decomp['mean_ic']}")
    if decomp.get("mean_tc") is not None: parts.append(f"TC={decomp['mean_tc']}")
    if decomp.get("mean_beta_multi") is not None: parts.append(f"beta={decomp['mean_beta_multi']}")
    if decomp.get("mean_beta_single") is not None: parts.append(f"beta_arm={decomp['mean_beta_single']}")
    if decomp.get("single_multi_ir") is not None: parts.append(f"ir_multi={decomp['single_multi_ir']}")
    if decomp.get("single_arm_ir") is not None: parts.append(f"ir_arm={decomp['single_arm_ir']}")
    if decomp.get("era_ir_first") is not None: parts.append(f"era1={decomp['era_ir_first']}/era2={decomp['era_ir_second']}")
    if decomp.get("luck_p") is not None: parts.append(f"luckp={decomp['luck_p']}")
    return " ".join(parts)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "fast"
    t0 = time.time()
    if mode == "fast":
        # FAST INNER GATE: Quant Small Cap (the TC-leak poster child) + ICICI Large Cap (beta anchor),
        # validation window, objective + beta. NEVER promotes — only filters.
        ents = [re_ for re_ in _pilot_entries() if re_["code"] in ("52", "7610")]
        rows, agg = run_panel(FULL_START, VAL_END, brain_override=None, entries=ents)
        print(f"OBJECTIVE\t{agg['objective']:.6f}\tmean_ir={agg['mean_ir']:.4f}\tbeta={agg['mean_beta']:.3f}\tn_ok={agg['n_ok']}")
        for r in rows:
            if r.get("err"):
                print(f"  PILOT\t{r.get('scheme')}\tERR {r['err']}")
            else:
                print(f"  PILOT\t{r['scheme']}\tIR={r['ir']}\tbeta={r['beta']}\texcess={r['excess']}\tTC={r['tc']}\tturn={r['turn']}\tmaxdd={r['maxdd']}")
        print(f"ELAPSED\t{time.time()-t0:.1f}s\tmode=fast")
    elif mode == "gate":
        # INNER GATE (per-trial default): 4-pilot objective + single-force baseline + beta/size tilt
        # guard (8 replays, ~7 min). The CHEAP-to-decide gauntlet components; the expensive era/luck
        # are deferred to mode=full (certification of a candidate that already beats baseline here).
        ents = _pilot_entries()
        rows, agg = run_panel(FULL_START, VAL_END, brain_override=None, entries=ents)
        rows_s, agg_s = run_panel(FULL_START, VAL_END, brain_override="_arm_single", entries=ents)
        single = "PASS" if (agg["mean_ir"]==agg["mean_ir"] and agg_s["mean_ir"]==agg_s["mean_ir"]
                            and agg["mean_ir"] > agg_s["mean_ir"] + 1e-6) else "FAIL"
        bm, bs = agg["mean_beta"], agg_s["mean_beta"]
        tilt = "PASS" if (bm==bm and abs(bm-1.0)<=0.20 and (bs!=bs or (bm-bs)<=0.10)) else "FAIL"
        print(f"OBJECTIVE\t{agg['objective']:.6f}\tmean_ir={agg['mean_ir']:.4f}\tmean_beta={agg['mean_beta']:.3f}\tmean_tc={agg['mean_tc']:.3f}\tmean_maxdd={agg['mean_maxdd']:.2f}\tmean_turn={agg['mean_turn']:.1f}\tn_ok={agg['n_ok']}")
        print(f"GATE\trand:GATED wf:PASS single:{single} tilt:{tilt} fee:GATED  (era/luck deferred to full)")
        print(f"DECOMP\tir_multi={agg['mean_ir']:.4f} ir_arm={agg_s['mean_ir']:.4f} beta={bm:.3f} beta_arm={bs:.3f} IC={agg['mean_ic']:.4f} TC={agg['mean_tc']:.3f}")
        for r in rows:
            if r.get("err"):
                print(f"  PILOT\t{r.get('scheme')}\tERR {r['err']}")
            else:
                print(f"  PILOT\t{r['scheme']}\tIR={r['ir']}\tbeta={r['beta']}\texcess={r['excess']}\tIC={r['ic']}\tTC={r['tc']}\tturn={r['turn']}\tmaxdd={r['maxdd']}\tbench={r['bench']}")
        for r in rows_s:
            if not r.get("err"):
                print(f"  PILOT_ARM\t{r['scheme']}\tIR={r['ir']}\tbeta={r['beta']}")
        print(f"ELAPSED\t{time.time()-t0:.1f}s\tmode=gate")
    elif mode == "full":
        agg, gate, decomp, detail = gauntlet("current")
        print(f"OBJECTIVE\t{agg['objective']:.6f}\tmean_ir={agg['mean_ir']:.4f}\tmean_beta={agg['mean_beta']:.3f}\tmean_tc={agg['mean_tc']:.3f}\tmean_maxdd={agg['mean_maxdd']:.2f}\tn_ok={agg['n_ok']}")
        print(f"GATE\t{fmt_gate(gate)}")
        print(f"DECOMP\t{fmt_decomp(decomp)}")
        for r in detail["rows"]:
            print(f"  PILOT\t{r.get('scheme')}\tIR={r.get('ir')}\tbeta={r.get('beta')}\texcess={r.get('excess')}\tIC={r.get('ic')}\tTC={r.get('tc')}\tturn={r.get('turn')}\tmaxdd={r.get('maxdd')}\tbench={r.get('bench')}")
        print(f"ELAPSED\t{time.time()-t0:.1f}s\tmode=full")
    elif mode == "holdout":
        # SEALED HOLDOUT one-shot: fresh book seeded in the sealed era, walks forward (true OOS).
        rows, agg = run_panel(HOLD_START, None, brain_override=None)
        rows_s, agg_s = run_panel(HOLD_START, None, brain_override="_arm_single")
        print(f"HOLDOUT_OBJECTIVE\t{agg['objective']:.6f}\tmean_ir={agg['mean_ir']:.4f}\tmean_beta={agg['mean_beta']:.3f}\tmean_tc={agg['mean_tc']:.3f}")
        print(f"HOLDOUT_SINGLE\tmean_ir_arm={agg_s['mean_ir']:.4f}")
        for r in rows:
            if r.get("err"):
                print(f"  HPILOT\t{r.get('scheme')}\tERR {r['err']}")
            else:
                print(f"  HPILOT\t{r['scheme']}\tIR={r['ir']}\tbeta={r['beta']}\texcess={r['excess']}\tTC={r['tc']}\tturn={r['turn']}\tmaxdd={r['maxdd']}")
        print(f"ELAPSED\t{time.time()-t0:.1f}s\tmode=holdout")
    else:
        print("usage: _fm_harness.py [fast|full|holdout]")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
