"""vistas/cio.py — the MUTABLE CIO arithmetic (autoresearch tag: cio-jun30).

THE FILE THE AUTORESEARCH LOOP EDITS. The frozen evaluator cio_firmtest.py scores it.

WHAT THIS IS (re-derived from CIO_INTELLIGENCE.md §3-§4, first principles, not imported):
  The CIO does not pick stocks — it ALLOCATES across the firm's desks (the FMs) to maximise the FIRM's
  information ratio, which (Grinold-Kahn team-IR) exceeds the average manager ONLY if the desks are
  DE-CORRELATED:  team-IR = s·√M_eff,  where M_eff = M/(1+(M−1)ρ̄) collapses toward 1 as the desks'
  active bets crowd (ρ̄→1). So the CIO's lever is the firm DESK-WEIGHT vector w_d — concentrate the
  firm's risk on the desks that ADD BREADTH (de-correlated, validated skill), starve the crowded clones.

  This is the prescriptive allocation engine (CIO_INTELLIGENCE §3d): a constrained tilt where the
  "views" are ONLY validated desk forces (each desk's own IR/IC/TC from its replay scorecard — every one
  already gauntlet-graded) and the prior is the policy benchmark (equal weight = the naive firm). The
  posture map (§3a) and the doable priority/urgency arithmetic (§4) reduce, at FIRM-allocation scale, to
  the same thing: how hard to lean on a desk = conviction (validated skill) × de-correlation contribution,
  capped by a crowding/fragility limit. PROVENANCE is mandatory — every weight traces to desk forces.

THE CONTRACT (house discipline): no curve-fit, self-explaining, defensible; the prescriptive whole is
GATED (must beat the single best desk AND random allocation, era-stable, no beta/size tilt); a desk
weight without its source force is a defect. The combination of validated parts is NOT a validated whole
(the failed equal-weight Mesh blend is the proof) → the evaluator backtests the WHOLE allocator.

firm_weights(meta, A, params) -> {desk_name: weight≥0}  (the evaluator normalises to Σ=1)
  meta : {desk_name: {ir, ic, tc, bench, cat, brain}}   the validated desk forces (provenance)
  A    : the daily active-return panel (date × desk) for the IN-SAMPLE window the evaluator passes
         (walk-forward safe: the evaluator hands the PAST-ONLY slice when testing look-ahead)
  params : the free knobs the loop searches (all plateau-tested by the gauntlet)
"""
from __future__ import annotations
import numpy as np
import pandas as pd

# ── DEFAULT_PARAMS = the BASELINE: equal weight (the naive firm, CIO adds nothing). ───────────────
#   The loop mutates these. Every knob has a first-principles meaning and is plateau-tested.
DEFAULT_PARAMS = {
    "mode": "decrowd",        # 'equal' = naive 1/N baseline. The loop mutates this. 'equal'|'skill'|'decrowd'.
    "gamma": 1.0,             # skill-tilt exponent (skill / decrowd): w ∝ max(0, IR_d)^gamma.
    "kappa": 1.0,             # de-crowding exponent: w ∝ skill / crowd_load^kappa. 0 = no de-crowd.
    "floor": 0.05,            # crowd_load floor (avoid blow-up when a desk is ~uncorrelated).
    "w_max": 0.55,            # crowding/fragility CAP: no desk may exceed this firm weight (forces breadth,
                              #   stops the firm collapsing onto one book = the size-tilt trap). 1.0 = no cap.
}


def _apply_cap(w: dict, w_max: float) -> dict:
    """Cap each desk's NORMALISED weight at w_max and redistribute the excess to the uncapped desks,
    iterating to a fixed point (the standard 'cap-and-spill' / waterfilling). Forces firm breadth: it is
    the crowding/fragility cap — it prevents a single-book tilt masquerading as firm skill. If w_max is
    too small to fit (n·w_max < 1) it degenerates gracefully to equal weight over the active set."""
    names = [k for k, v in w.items() if v > 0]
    if not names:
        return w
    if w_max >= 1.0 or len(names) * w_max <= 1.0 + 1e-12:
        if len(names) * w_max <= 1.0 + 1e-12:        # cap can't be satisfied → equal over active set
            return {k: (1.0 / len(names) if k in names else 0.0) for k in w}
        return w
    x = {k: max(0.0, v) for k, v in w.items()}
    for _ in range(100):
        tot = sum(x.values())
        x = {k: (v / tot if tot > 0 else 0.0) for k, v in x.items()}
        over = {k: v for k, v in x.items() if v > w_max + 1e-12}
        if not over:
            break
        # pin the over-cap desks AT w_max, redistribute the rest proportionally among the uncapped
        pinned = {k: w_max for k in over}
        free = {k: v for k, v in x.items() if k not in over and v > 0}
        rem = 1.0 - w_max * len(pinned)
        fsum = sum(free.values())
        x = {**{k: 0.0 for k in w}, **pinned,
             **({k: (rem * v / fsum) for k, v in free.items()} if fsum > 0 else
                {k: rem / max(1, len(free)) for k in free})}
    return x


def _insample_ir(A: "pd.DataFrame"):
    """Each desk's IR measured ON THE WINDOW the evaluator passed (annualised daily active-return IR).
    Walk-forward safe (the wf gate hands the past-only slice). Correctly shows NEGATIVE skill for a desk
    that didn't beat its bench in-sample, so max(0,·) starves it — the CIO stops funding losers."""
    out = {}
    for nm in A.columns:
        t = A[nm].dropna()
        if len(t) < 30 or t.std(ddof=1) == 0:
            out[nm] = 0.0
        else:
            out[nm] = float((t.mean() * 252) / (t.std(ddof=1) * (252 ** 0.5)))
    return out


def _crowd_load(A: "pd.DataFrame", floor: float = 0.05):
    """Each desk's CROWDING LOAD = its mean active-return correlation with all OTHER desks (its
    contribution to the firm's ρ̄). High load = a crowded clone; low load = a breadth-adder. Computed
    on the in-sample window the evaluator passes (walk-forward safe). Floored so a near-uncorrelated
    desk does not blow the weight up. Negative-correlation desks (rare, pure breadth) get the floor."""
    C = A.corr()
    n = len(C)
    if n <= 1:
        return {nm: 1.0 for nm in A.columns}
    load = (C.sum(axis=1) - 1.0) / (n - 1.0)        # exclude self (diag=1)
    return {nm: max(float(load[nm]) if pd.notna(load[nm]) else 1.0, floor) for nm in A.columns}


def firm_weights(meta: dict, A: pd.DataFrame, params: dict) -> dict:
    """Return the firm desk-weight map. BASELINE (mode='equal') = 1/N over the desks present in A.

    The loop replaces / extends this body with breadth-aware allocation modes. The evaluator
    normalises Σw=1 and clamps w≥0, so this returns RELATIVE leans."""
    names = list(A.columns)
    mode = params.get("mode", "equal")

    if mode == "equal":
        # the policy prior: the naive firm rides every desk equally. CIO adds no breadth here.
        return {nm: 1.0 for nm in names}

    if mode == "skill":
        # conviction tilt: lean on in-sample skill. w_d ∝ max(0, IR_d)^gamma. Pure skill, no breadth term.
        g = float(params.get("gamma", 1.0))
        ir_is = _insample_ir(A)
        out = {nm: (max(0.0, ir_is[nm]) ** g) for nm in names}
        return _apply_cap(out, float(params.get("w_max", 1.0)))

    if mode == "decrowd":
        # THE breadth lever: w_d ∝ max(0, IR_d)^gamma / crowd_load_d^kappa, IR & crowd measured IN-SAMPLE
        # (walk-forward safe), THEN capped at w_max so the firm stays diversified. max(0,·) drops desks
        # that didn't beat their own benchmark in-sample; /crowd_load up-weights the de-correlated
        # breadth-adders; the cap stops the firm collapsing onto one book (the size-tilt trap). The
        # √BR breadth effect: two uncorrelated desks, each insignificant alone, jointly significant.
        g = float(params.get("gamma", 1.0))
        k = float(params.get("kappa", 1.0))
        load = _crowd_load(A, float(params.get("floor", 0.05)))
        ir_is = _insample_ir(A)
        out = {}
        for nm in names:
            ir = max(0.0, ir_is[nm])
            out[nm] = (ir ** g) / (load[nm] ** k)
        return _apply_cap(out, float(params.get("w_max", 0.55)))

    raise ValueError(f"unknown CIO mode {mode!r}")
