# Autoresearch CIO track — SESSION SUMMARY (tag: cio-jun30)

Branch: `autoresearch/cio-jun30`. Wall clock: start 03:58 IST, ~5h budget. Karpathy-style rule-discovery
loop for the Vistas Agentic-AMC CIO. Objective (minimise): `−IR_firm`, where the firm's information
ratio exceeds the average manager ONLY if the desks are DE-CORRELATED (team-IR = s·√M_eff). Graded on
breadth cultivated and crowding killed. Paper-only; no look-ahead; total return; FDR + luck deflated.

## What the CIO arithmetic IS here (re-derived, first principles)
The CIO does not pick stocks — it ALLOCATES firm risk across the desks (the FMs) to maximise the FIRM's
IR. By Grinold-Kahn, team-IR = s·√M_eff and M_eff = M/(1+(M−1)ρ̄) collapses toward 1 as the desks' active
bets crowd. So the CIO's lever is the firm desk-weight vector w_d: concentrate on the desks that ADD
BREADTH (de-correlated, validated skill), starve the crowded clones. This is the gated prescriptive
allocator (CIO_INTELLIGENCE §3d) where the only "views" are validated desk forces (each desk's own
gauntlet-graded IR/IC/TC) and the prior is the policy benchmark (equal weight = the naive firm).

## Files
- MUTABLE (the only edit target): `vistas/cio.py` — `firm_weights(meta, A, params)`. Modes:
  `equal` (baseline 1/N) · `skill` (w∝IR^γ) · `decrowd` (w∝max(0,IR_is)^γ / crowd_load^κ, then capped at
  w_max — THE CHAMPION). All weights provenance-traced to validated desk forces; no free param fit to
  maximise backtest IR; plateau-tested.
- FROZEN EVALUATOR (read-only in loop): `cio_firmtest.py` — builds the firm active-return panel from the
  pilot replay books, computes IR_firm + the s·√M_eff decomposition + the full gauntlet (rand≥10k Dirichlet
  · walk-forward · beats-single-best-desk · era-stability · parameter-plateau · block-bootstrap luck · FDR ·
  beta/tilt guard · net-of-fee GATED). Holdout sealed in `ERA_BOUNDS`.

## THE FIRM (operator-fixed 2026-06-30) = the 4 cross-AMC contract pilots (M=4)
ICICI Pru Large Cap (NIFTY 100) · SBI Equity Hybrid/Aggressive (NIFTY 500) · ABSL Flexi Cap (NIFTY 500) ·
Quant Small Cap (NIFTY SMALLCAP 250). Each = a deterministic rules-FM replay book (daily nav.csv +
benchmark_nav.csv + scorecard.json, 2015→2026, survivorship-clean, look-ahead-free). Desk active return
θ_d,t = r_nav − r_bench (excess over the desk's OWN benchmark → no benchmark beta masquerades as alpha).
★ HONEST BOUND (operator): M=4 → small √BR headroom; the de-correlation lift is BOUNDED. Reported as such.

## SEALED HOLDOUT (operator-set, harsher) = last ~5y 2021-01-01 → 2026-06-25 (includes the 2022 drawdown).
Validation (all the loop saw) = 2015-01-30 → 2020-12-31. The loop evaluated `era='val'` only; the holdout
was touched ONCE, at the end, on the champion.

## Baseline (Q0) — the honest starting number
Equal-weight 4-pilot firm on validation: **IR_firm = 0.367**. Decomposition: s=0.241, ρ̄=0.323, M_eff=2.03,
s·√M_eff=0.344 ≈ realised (self-consistent). GATE: `rand:FAIL single:FAIL wf:PASS era:PASS plateau:NA
luck:FAIL tilt:PASS fee:GATED`. The naive firm is weak and NOT luck-significant (p=0.20) in 2015-20, and
loses to the best single pilot (0.596). Two of the four pilots (ICICI −0.14, SBI −0.06) have NEGATIVE
validation skill; the large-cap trio ICICI/SBI/ABSL is crowded (ρ 0.55-0.76); Quant SmallCap is the
de-correlated diversifier (ρ≈0). T (evaluator wall-clock) ≈ 0.5s → full gauntlet every trial, no fast gate.

## CHAMPION = Q2 (commit 5bcdffe): capped de-crowding, w_max=0.55, γ=1, κ=1
Rule: w_d ∝ max(0, IR_d^in-sample)^γ / crowd_load_d^κ, then cap each desk at w_max and spill the excess
(forces breadth, stops a single-book tilt). On validation it drops the two negative-skill large-caps and
holds **ABSL 45% / Quant 55%** — two uncorrelated positive-skill desks.

VALIDATION: **IR_firm = 0.812** (objective −0.812078). Decomposition: s=0.582, **ρ̄=0.003, M_eff=1.97
(HELD near 2 — breadth preserved, NOT a single-book collapse)**, beta −0.014.
GATE (validation): `rand:PASS single:PASS wf:PASS era:PASS plateau:PASS luck:PASS tilt:PASS fee:GATED` —
plus `fdr:FAIL` (see below). Plateau is broad: IR≈0.80-0.82 across w_max∈[0.45,0.65], flat in γ∈[0.5,2],
κ∈[0,2]; luck only breaks at w_max≥0.70 (where the cap stops enforcing breadth) — the cap working exactly
as designed.

The breadth mechanism, made real: ABSL alone (IR 0.60, p_luck 0.082) and Quant alone (0.57, p 0.109) are
EACH individually NOT luck-significant; combined ~50/50 (ρ̄≈0) the firm IR rises to 0.81 and p_luck=0.046 —
two uncorrelated bets, insignificant alone, JOINTLY significant. That is the √BR breadth effect, not a tilt.

## SEALED-HOLDOUT VERDICT (2021-2026, one-shot): PARTIAL HOLD
| | Validation 2015-20 | Holdout 2021-26 |
|---|---|---|
| Champion IR | 0.812 (p=0.046) | **0.988 (p=0.015)** |
| Naive equal-wt IR | 0.367 (p=0.20) | 0.792 (p=0.043) |
| **Champion − Naive** | **+0.445** | **+0.196** |
| Best single desk | 0.596 | 1.044 (Quant) |

- The champion's lift OVER THE NAIVE FIRM **HELD out-of-sample**: +0.45 in-sample → **+0.20 IR OOS**, same
  sign, luck-significant (p=0.015, clears even BH). Mechanism generalised: s 0.72→0.84, M_eff held
  1.57→1.66, ρ̄ down 0.518→0.392, tilt:PASS (beta 0.112). The capped de-crowding allocator genuinely beats
  the naive equal-weight firm via breadth.
- BUT it did **NOT beat the single best desk OOS** (0.988 < Quant 1.044): `single:FAIL`. With M=4 and one
  dominant de-correlated specialist, the firm cannot out-IR that specialist — the √BR breadth bound the
  operator flagged, confirmed empirically. (`era:FAIL` on the holdout is a HARNESS ARTIFACT — the 3 sub-eras
  are all pre-2021 so empty on the holdout; discount it.)

## PROMOTE / NOT decision (honest, bounded)
PROMOTE the allocator as: **"capped de-crowding beats the naive equal-weight firm OOS by ~0.2 IR via
genuine breadth (cap + de-correlation), at M=4"** — a real, defensible, OOS-robust CIO construction edge.
Do **NOT** promote it as "beats the single best desk" — it does not, OOS. CARRY these caveats with any
charter update: (1) validation FDR-fragile (p barely < 0.05, fails BH across the ~3 families) — the holdout
luck p=0.015 partly redeems it but it remains borderline; (2) the specific "drop ICICI/SBI" desk SELECTION
is a full-validation-era artifact (all 4 were positive in 2015-18 and in 2021-26 — the drop is NOT stable
across sub-splits); the MECHANISM (cap + de-correlate) is what's robust, not the selection; (3) M=4 → the
breadth ceiling is ~M_eff 2, so the firm cannot beat its best de-correlated specialist; (4) net-of-fee/TER
is GATED (no expense data) — the +0.2 OOS lift is gross of switching cost. This is the contract's calibrated
outcome (small, plateau-robust, defensible) — not a 1-year miracle, and not over-claimed.

## CERTIFIED DEAD-ENDS (negative knowledge — do not re-walk)
1. **Skill-tilt alone (w∝IR^γ)** (P1, 28-desk; and the γ-lever generally): lifts s but leaves M_eff FLAT —
   it concentrates on the high-IR cluster, it is NOT breadth, and it cannot beat the single best desk
   without becoming an extreme conviction tilt. The CIO's lever is de-correlation, not conviction.
2. **Uncapped de-crowding** (Q1): beats the single best desk ONLY by collapsing to 89% Quant SmallCap — a
   smid/size single-book TILT (M_eff falls to 1.24), and it FAILS the luck bar at every (γ,κ). The cap is
   what makes de-crowding a breadth move instead of a tilt.
3. **Unconstrained max-Sharpe Σ⁻¹μ allocator** (Q3): fits validation noise into an illegal long/short book
   (ICICI −3.9, ABSL +4.2) or, long-only, UNDERperforms the disciplined capped rule both in-sample (0.731
   vs 0.812) and OOS (0.680 vs 0.988). The transparent, auditable capped rule beats the black-box optimizer —
   the project's simplicity-and-defensibility prize, vindicated.

## The headline the CIO must internalise (the trap, exposed and measured)
The √BR effective-breadth collapse is REAL and quantified. On the original 28-desk ABSL firm the desks'
daily active returns had ρ̄≈0.56 → M_eff≈1.8 (NOT 28); the naive s·√M=3.8 was a mirage, the honest number
s·√M_eff≈0.95. On the 4 pilots ρ̄≈0.32 → M_eff≈2.0; naive s·√4 overstates. A CIO graded on a headcount of
desks is graded on a fiction; graded on M_eff, the only lever that helps is killing crowding — which here
means starving redundant/negative-skill books and holding the de-correlated ones at a CAP. That lift is
real and survives OOS, but it is BOUNDED by how many genuinely independent desks the firm has.
