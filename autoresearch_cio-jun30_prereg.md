# Autoresearch CIO track — pre-registration log (tag: cio-jun30)

Branch: `autoresearch/cio-jun30`. Objective (minimise): `−IR_firm`, where the firm's information
ratio exceeds the average manager ONLY if the desks are DE-CORRELATED (team-IR `= s·√M_eff`). Graded on
breadth cultivated and crowding killed, with a drawdown-preemption credit from the fragility map.

## The substrate (what exists, no fabrication)
- 28 ABSL desks, each a deterministic rules-FM replay book (`amc_book/Aditya Birla Sun Life Mutual
  Fund/<scheme>/replay/`): daily `nav.csv` (2015-01-30 → 2026-06-25, 2824 days), daily
  `benchmark_nav.csv` (the desk's OWN NSE TR benchmark), and `scorecard.json` (per-desk IR/IC/TC, bench).
- Built by `make_absl_firm.py` (deterministic, no LLM, survivorship-clean, look-ahead-free, total return).
- The desk active-return stream `θ_d,t = r_nav,d,t − r_bench,d,t` is the firm's expressed opinion per desk.

## Baseline crowding finding (measured before any trial — the trap, exposed)
- Mean pairwise correlation of the 28 desks' DAILY active returns: **ρ̄ = 0.559**.
- Effective breadth `M_eff = M / (1+(M−1)ρ̄) = 28/(1+27·0.559) = 1.7` (NOT 28).
- Mean desk IR `s = 0.718`. Naive `s·√M = 3.80` (absurd); breadth-honest `s·√M_eff = 0.95`.
- 17 of 28 desks share NIFTY 500 as benchmark → mechanically correlated active bets. The firm runs
  ~2 independent bets, not 28. This IS the √BR effective-breadth collapse the contract names.

## The CIO arithmetic under test (MUTABLE = `vistas/cio.py`)
The CIO chooses firm desk-weights `w_d` (≥0, Σ=1) to assemble the firm book. Baseline = equal weight
(naive, 1/28). The objective is the firm's realised IR measured the breadth-honest way. Mutations search
the crowding cap / effective-breadth penalty / posture tilt strength / allocation tilt.

## FROZEN EVALUATOR = `cio_firmtest.py` (read-only inside the loop)
Computes, on the validation eras only (NEVER the sealed holdout):
- `IR_firm` = annualised(mean firm active return) / annualised(std firm active return), where the firm
  active return at t = `Σ_d w_d · θ_d,t` (firm = weighted desks vs each desk's own benchmark, so a desk's
  excess over ITS benchmark is the unit of skill; no sector beta masquerades as alpha).
- Decomposition: `s` = AUM/weight-weighted mean desk IR; `M_eff` = effective breadth from the
  weighted desk active-return correlation; report `s·√M_eff` and the realised IR side by side.
- GAUNTLET (every component must PASS, GATED where data missing):
  1. rand: firm IR beats ≥10k random desk-weightings of the same shape (Dirichlet on the simplex) — the
     CIO weighting must beat random allocation, on the percentile.
  2. wf: walk-forward — weights chosen on data strictly BEFORE the return window; no look-ahead.
  3. single: firm IR beats the SINGLE BEST desk's standalone IR (else the firm adds no breadth — the
     Mesh-blend lesson at firm scale).
  4. era: the lift holds across distinct calendar eras (2015-17 / 2018-20 / 2021-22), not one regime.
  5. plateau: objective flat across a neighbourhood of every free parameter (crowding cap, penalty).
  6. luck+fdr: block-bootstrap luck bar on the firm active-return mean; FDR across session trials.
  7. tilt: the lift is breadth (M_eff up / ρ̄ down), NOT a beta/size tilt vs the firm policy benchmark
     — beta of firm active return on policy-benchmark return must stay ~flat.
  8. fee: net-of-fee — GATED (no TER data); stamped, never silent pass.
- Provenance: every firm weight traces to validated desk forces (IR/IC/TC from the desk scorecards).

## SEALED HOLDOUT (chosen BEFORE any trial; the loop NEVER evaluates on it)
**Holdout era = 2023-07-01 → 2026-06-25 (the most recent ~3 years).**
Validation eras (what the loop sees) = everything STRICTLY BEFORE 2023-07-01, i.e. 2015-01-30 → 2023-06-30.
The champion is one-shot tested on the holdout at the end. Collapse → not promoted, logged as overfit.

## Expectations (honest, calibrated — signals are weak)
The firm-IR ceiling is governed by ρ̄: you cannot manufacture breadth, only stop diluting it. Expect a
SMALL, plateau-robust lift from concentrating weight on the de-correlated desks (the non-NIFTY-500 sector
desks + the genuinely distinct brains), capped by the crowding penalty. A 3.8→0.95 honesty correction is
the headline; any "improvement" must come from ρ̄ DOWN (real breadth), never from levering a high-IR
correlated cluster (that is the tilt trap the gauntlet must catch).

---

## Trials

### P0 — BASELINE (equal-weight firm, 1/28 each)
- Hypothesis: the naive firm (CIO adds nothing, equal-weights all desks) has a breadth-honest IR far
  below the naive `s·√M` because ρ̄≈0.56 collapses effective breadth.
- Mechanism: pure measurement, no edge claim. Establishes the number the loop must beat.
- Expected: IR_firm modest (~0.7–1.0 realised); M_eff ≈ 2; single-best-desk IR likely HIGHER than the
  equal-weight firm (so the naive firm FAILS the `single` gate — proving the CIO must actually decorrelate).

### P2 — de-crowding firm weighting (THE breadth lever)
- Hypothesis: w_d ∝ max(0, IR_d) / (crowd_load_d)^kappa, where crowd_load_d = mean correlation of desk d's
  active return with all other desks (its contribution to ρ̄). Reward validated skill, DIVIDE by crowding.
- Mechanism (first principles, the CIO's actual job): team-IR = s·√M_eff and M_eff = N/(1+(N−1)ρ̄). To LIFT
  M_eff you must LOWER ρ̄ — i.e. starve the crowded clones (the 17 NIFTY-500 generalist desks that are one
  large-cap active bet in 17 coats) and ride the de-correlated sector/thematic desks (Pharma/SmallCap/
  Digital/Transport/Banking/Infra/Mfg — each on its OWN NIFTY sub-index, a genuinely distinct bet). This is
  pure breadth cultivation + crowding killing — exactly what the objective grades. Provenance: every weight
  = (desk's gauntlet-graded IR) ÷ (its measured crowding load); the de-correlation is the benchmark-identity
  structure, not a fit.
- Expected on decomposition: ρ̄ DOWN, M_eff UP (well above the baseline 1.76), n_active lower (concentrated
  on the breadth-adders), s roughly flat-or-up. The lift should come from M_eff (breadth), NOT from a tilt
  (beta must stay flat, |beta|≤0.25). TARGET: beat the single best desk IR 1.762 BECAUSE diversification of
  independent active bets pushes the firm IR above any one desk — the whole point of √BR. If it beats single
  ONLY via a beta/size tilt (tilt:FAIL) → discard. Must clear the parameter plateau in kappa.
- crowd_load is computed on the SAME in-sample window the evaluator hands firm_weights (walk-forward safe).

---

## ★ OPERATOR PIVOT (2026-06-30) — firm redefined + holdout extended. Prior 28-desk trials (P0-P2)
## are RETAINED as historical context but SUPERSEDED. The firm and holdout below now govern.

### NEW FIRM = the 4 cross-AMC contract pilots (M=4)
- ICICI Prudential — Large Cap Fund (NIFTY 100)
- SBI — Equity Hybrid / Aggressive Hybrid (NIFTY 500)
- Aditya Birla Sun Life — Flexi Cap Fund (NIFTY 500)
- Quant Mutual — Small Cap Fund (NIFTY SMALLCAP 250)
Objective unchanged: minimise −IR_firm via BREADTH (team-IR = s·√M_eff), graded on de-correlation
cultivated + crowding killed. Per-pilot IR + firm weight tracked so one book's tilt can't masquerade as
firm skill. ★ HONEST BOUND (operator): with only M=4 desks the √BR headroom is SMALL → the de-correlation
lift demonstrable is bounded; report the bound, never over-claim.

### NEW SEALED HOLDOUT = last ~5y: 2021-01-01 → 2026-06-25 (harsher; includes the 2022 drawdown).
Validation era (all the loop sees) = 2015-01-30 → 2020-12-31. The loop evaluates era='val' ONLY.

### Baseline crowding on the 4 pilots (VALIDATION 2015-2020, measured before trials)
- Per-pilot active-IR: ICICI −0.141, SBI −0.061, ABSL +0.596, Quant +0.571 (two pilots NEGATIVE-skill
  in this era — the rules-FM brain didn't beat their bench 2015-2020).
- Corr: large-cap trio ICICI/SBI/ABSL crowded (ρ 0.55-0.76); Quant SmallCap ~uncorrelated (ρ≈0, even
  −0.16 vs ICICI) = the genuine diversifier.
- ρ̄ = 0.323, M_eff(equal) = 2.03. s = 0.241. s·√M_eff = 0.344 ≈ realised equal-weight firm IR 0.367.
  Naive s·√4 = 0.48 overstates. Best single pilot = 0.596 (ABSL).
- THE CIO LEVER: starve the redundant/negative-skill large-caps (ICICI, SBI), lean on the de-correlated
  + skilled books (Quant for breadth, ABSL for large-cap skill). This is skill AND breadth, defensible
  (provenance = each desk's validated IR + its measured crowding load). Bounded by M=4.

### Q0 — NEW BASELINE (equal-weight 4-pilot firm)
- Hypothesis/mechanism: naive firm rides all 4 equally; IR≈0.37, M_eff≈2.0; below best single pilot 0.60
  → the naive firm FAILS `single` (must actually decorrelate to justify itself).

### Q2 — CAPPED de-crowding (the breadth-honest champion candidate)
- Hypothesis: w_d ∝ max(0, IR_d^val)^gamma / crowd_load_d^kappa, THEN apply a per-desk weight CAP w_max
  and renormalise (iteratively) so NO desk exceeds w_max. The cap is the crowding/fragility cap in the
  search space — it FORCES the firm to stay diversified instead of collapsing onto one book.
- Mechanism (first principles, the √BR breadth effect made real): dropping the two negative-skill large-
  caps (ICICI, SBI) leaves ABSL (large-cap skill) + Quant (de-correlated small-cap). The cap keeps the
  firm ~50/50 across them rather than 89% Quant. ABSL alone (IR 0.60, p_luck 0.082) and Quant alone
  (0.57, p 0.109) are EACH individually NOT luck-significant; combined ~50/50 (ρ̄≈0.003) the firm IR rises
  to ~0.80 and p_luck≈0.043 — two uncorrelated bets, each insignificant alone, JOINTLY significant. That
  is genuine breadth (M_eff≈2.0, NOT a single-book tilt), the exact thing the objective grades.
- Provenance: each weight = (in-sample IR) ÷ (crowding load), capped. The retained set + the cap are the
  fragility/crowding control. Beta must stay flat (it's not the Quant size tilt because the cap stops the
  firm BECOMING Quant).
- Expected on decomposition: ρ̄ DOWN to ~0, M_eff ~2.0 (HELD, not collapsed), both ABSL & Quant present
  (~40-60% each), s up (losers dropped). TARGET: pass single AND luck AND rand AND tilt, plateau-robust
  in (gamma, kappa, w_max). HONEST BOUND: M=4 with only 2 positive-skill desks → M_eff caps at ~2, so the
  lift is bounded; this is the ceiling, reported as such, not over-claimed.
- The DISCARD condition: if it only passes by w_max→1 (collapsing to Quant) or by a beta/size tilt, or
  fails luck off the 50/50 point (not plateau-robust), discard.
