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
    "mode": "equal",          # BASELINE = naive 1/N firm. The loop mutates this. 'equal'|'skill'|'decrowd'.
    "gamma": 1.0,             # skill-tilt exponent (skill / decrowd): w ∝ max(0, IR_d)^gamma.
    "kappa": 1.0,             # de-crowding exponent: w ∝ skill / crowd_load^kappa. 0 = no de-crowd.
    "floor": 0.05,            # crowd_load floor (avoid blow-up when a desk is ~uncorrelated).
}


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
        # conviction tilt: lean on validated skill. w_d ∝ max(0, IR_d)^gamma. Pure skill, no breadth term.
        g = float(params.get("gamma", 1.0))
        return {nm: (max(0.0, float(meta.get(nm, {}).get("ir") or 0.0)) ** g) for nm in names}

    if mode == "decrowd":
        # THE breadth lever: w_d ∝ max(0, IR_d)^gamma / crowd_load_d^kappa. Reward validated skill,
        # DIVIDE by crowding → starve the correlated clones, ride the de-correlated breadth-adders.
        g = float(params.get("gamma", 1.0))
        k = float(params.get("kappa", 1.0))
        load = _crowd_load(A, float(params.get("floor", 0.05)))
        out = {}
        for nm in names:
            ir = max(0.0, float(meta.get(nm, {}).get("ir") or 0.0))
            out[nm] = (ir ** g) / (load[nm] ** k)
        return out

    raise ValueError(f"unknown CIO mode {mode!r}")
