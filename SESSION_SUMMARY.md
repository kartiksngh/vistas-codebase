# Autoresearch SESSION SUMMARY — ANALYST track (tag: analyst-jun30)

Branch: `autoresearch/analyst-jun30` (isolated worktree). Date: 2026-06-30. Budget: <5h, used ~well inside it.
Mutable file: `vistas/mesh_research.py` — a new `desk_signal()` / `DESK_PARAMS` assembly ONLY.
Frozen (never edited): `evaluate`/`_ic_block`/`build_panel`/`_assemble_wide`/`arm_baseline` (the IC convention
+ data plumbing), `vistas/arm_backtest.py`, `vistas/mesh_backtest.py`. Driver harness: `_gauntlet.py`.

## Objective
Minimise `−IC_deflated` of the analyst desk's pitch ranking. IC = monthly cross-sectional Spearman rank
correlation between the ex-ante pitch score(t) and realised forward 6-month total return(t→t+6), averaged
across scored months (≥30 names/month) — the frozen `mesh_research.evaluate` convention. Fama-MacBeth
t = mean_IC/(std_IC/√n). Secondary: decorrelation from other desks (breadth).

## Sealed holdout (operator-updated mid-session, applied before any champion sealed)
HOLDOUT = **2021-01-01 .. 2026-06-30** (last ~5y, incl. the 2022 rate-shock drawdown). VALIDATION = all
scoring months ≤ 2020-12-31. The loop evaluated ONLY on validation; the champion was one-shot tested on the
holdout once, at the end.

## What the data said (the real finding)
On this total-return universe the **strongest validated single force is medium-term price momentum**, NOT
the forces the desk had been blending. Single-force IC@6m (validation, same rows): mom_6m 0.108, mom_12m
0.112, ARM-level 0.067, dbreadth 0.053, flow 0.045, value_z 0.041, arm_trend_3m 0.030, quality 0.029.
The old desk signal (equal-weight `z(flow)+z(dbreadth)+z(arm_trend_3m)`) scored IC 0.062 and **LOST to ARM
and to momentum** — it was diluting the workhorse, the proven dead-end live again.

## Baseline
`ew_blend` (the old desk signal): validation IC@6m **0.062**, obj −0.062, gate `single:FAIL` (does not beat
its best component), era:PASS luck:PASS tilt:PASS. (Commit 90f22d4.)

## Champion (commit 8812812)
`desk_signal` = **two-horizon momentum composite `z(mom_6m) + z(mom_12m)`**, equal weight, over names with
both legs present. The only searched assembly that BEATS its single best component.

| metric | VALIDATION (≤2020, n=80) | SEALED HOLDOUT (2021-01..2026-06, n=59) |
|---|---|---|
| IC@6m | **0.118** | **0.111** (held — same sign, ~same magnitude) |
| Fama-MacBeth t | 7.4 | 8.94 |
| era stability | 3/3 pos (0.107 / 0.139 / 0.112) | 3/3 pos (0.116 / 0.111 / 0.099, incl. 2022) |
| beats best single | yes (0.118 > 0.112) | yes (0.111 > 0.107) |
| size-neutralised IC | 0.102 (86% retained) | 0.107 (96% retained) |
| block-bootstrap luck | frac_pos 1.0, t 4.62 | frac_pos 1.0, t 6.18 |

**Full gauntlet verdict:** `rand:GATED  wf:PASS(structural)  single:PASS  era:PASS  plateau:PASS  luck:PASS
fdr:PASS  tilt:PASS  fee:GATED`.
- **plateau:** flat across blend ratio 3:1..1:3 (all IC 0.116-0.118) AND across horizon {6,9,12}m blends
  (all 0.114-0.118; the 3m leg is weaker, 0.085, and dilutes) — a mechanism, not a tuned spike.
- **tilt:** size-tercile IC small 0.114 / mid 0.104 / **large 0.091** — survives in large caps, so NOT a
  pure size/illiquidity tilt; size-neutralised IC 0.102.
- **fdr:** Benjamini-Hochberg q=0.10 over 13 trials — champion p=1.3e-10 ≪ threshold; a real discovery.
- **decorrelation (secondary):** the momentum desk's ranking correlates only **0.25** with an ARM desk →
  largely independent bets → adds genuine breadth (√M team-IR), not a crowded echo.

## Sealed-holdout verdict: HELD → CANDIDATE TO PROMOTE
The champion's edge held on the harsher 5-year vault it never saw (IC 0.118→0.111, all sub-eras positive
incl. the 2022 rate-shock, decomposition still clean). It is a candidate to promote into the analyst
charter. For contrast the old ew_blend collapsed on the same holdout to IC 0.043 (single:FAIL).

## Honest caveats (GATED, never silent passes)
- **NAV bar GATED:** `signal_navtest.py` (the all-starts × ≥10k random NAV bar) was OWED and NOT built this
  session — the champion is validated on the IC-beat metric + the block-bootstrap luck bar, **not** the
  literal NAV bar. Do not claim it passed the NAV bar.
- **net-of-fee GATED:** no TER/cost data; a 6-month-momentum tilt implies turnover, real costs unmodelled.
- **Marginal composite breadth:** the two legs are 0.72-correlated; the composite beats a single momentum
  leg by only +0.008 IC (51% of months) — it is variance-reduction, not independent breadth. The real win
  is "momentum, well-specified and undiluted", not a miracle. (Consistent with the house ceiling: signals
  are weak; holding-rank split-half persistence ≈0.097.)
- Holdings-based legs (flow/breadth) only cover ~57% of rows and only from 2013-04 — momentum/ARM/price legs
  span the fuller history.

## Top 3 certified dead-ends (negative knowledge, so the next agent doesn't re-walk them)
1. **Equal-weight blending a WEAK force into momentum DILUTES it** — re-proven live: mom+arm (ew) 0.104 <
   mom alone 0.108; +value 0.088; +breadth 0.086; the old flow/breadth/arm_trend blend 0.062. (The Mesh
   dilution lesson generalises: never EW a weak leg into a strong one.)
2. **Orthogonalising momentum vs ARM-level REMOVES IC** (mom-orth+arm 0.083 < mom 0.108): momentum's IC
   lives partly in its ARM-correlated component, so residualising it throws away signal. And mom-orth-vs-ARM
   THEN adding the weak arm_trend_3m drives IC **negative** (−0.009) — a hard dead-end (P7).
3. **No exploitable momentum term-structure beyond ~6-12m**: short momentum (mom_3m 0.085) is weaker and
   dilutes; {6,9,12}m blends all land at ~0.114-0.118 — adding horizons buys nothing. (And ARM-revision-
   direction / herding / flow do not rescue a momentum-led desk on this data.)

## Reproduce
`python _gauntlet.py '{}'` (validation champion) · `python _gauntlet.py holdout '{}'` (sealed-holdout test).
Requires `VISTAS_ARM_DIR` pointing at the licensed `arm_repo/` and the panel parquet (built once via
`mesh_research.build_panel`, ~195s; cached thereafter, per-eval ~2.5s). Validation/holdout boundary lives in
`_gauntlet.py` (`VAL_END=2020-12-31`).
