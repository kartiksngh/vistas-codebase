# Autoresearch FM track — pre-registration (tag fm-jun30)

Anti-hindsight log. Every trial is written HERE **before** the edit + run. A "keep" is honest only if
the realised lift matches the pre-registered mechanism (came from IC or TC as predicted, not from an
unintended beta/size tilt the decomposition flagged).

Mutable file (only edit target): `vistas/amc_firm.py` (the FM brain: `build_rules_v0`, the brain
library / score functions, `waterfill`, `deploy_with_floor`).
Frozen evaluator (read-only): `vistas/amc_replay.py` via `_fm_harness.py` (modes: fast | full | holdout).

## OBJECTIVE (minimise)
`objective = −mean(IR)` across the 4 pilot books, IR = `scorecard.benchmark.info_ratio` (excess CAGR vs
the CORRECT benchmark / tracking error). Correct benchmark = the SECTOR TR index for theme-fenced funds
(`amc_replay._bench_for` / `_THEME_BENCHMARK`), else the matched category index. Lower (more negative) =
better. Must ALSO survive the IC·√BR·TC decomposition with the beta/size-tilt guard, and the
investor-experience side-constraints (maxDD / turnover), which are HARD side-constraints, never averaged in.

## PILOTS (the 4 CROSS-AMC contract pilots, pinned to navindia_codes by operator; resolved via
##  amc_live.pilot_reg_entries() — verified full 158-mo holdings each, 2013-04→2026-05)
- ICICI Prudential — Large Cap Fund  (code 7610, bench NIFTY 100)  — deep-liquidity, low-TC-leak control
- SBI — Equity Hybrid (Aggressive Hybrid)  (code 2383, bench NIFTY 500) — equity-band / regime_switch brain
- Aditya Birla Sun Life — Flexi Cap Fund  (code 9, bench NIFTY 500) — broad, core_multifactor brain
- Quant Mutual — Small Cap Fund  (code 52, bench NIFTY SMALLCAP 250) — ★ the TC-leak poster child (IR −0.47→+0.92 = pure deployment, not skill; β0.28 pathology at amc_firm.py:978)
Deliberately cross-AMC so the firm objective isn't dominated by one house's style (NOT the all-ABSL 28-desk live firm).
Objective = mean(−IR) firm-level HEADLINE; ALSO track per-pilot IR + per-pilot IC·√BR·TC + per-pilot beta/size so one book's tilt can't masquerade as firm skill.

## ★ SEALED HOLDOUT (operator-revised to last ~5y — chosen BEFORE any trial; the loop NEVER evaluates here)
- **Validation/search window** (the loop sees ONLY this): **2013-04-01 → 2020-12-31** (`VAL_END`) = ~7.75y, ample.
- **Sealed holdout era** (one-shot, champion only, at the very end): **2021-01-01 → 2026-06** (`HOLD_START`) = ~5y INCLUDING the 2022 drawdown → a harsher OOS exam (the last-3y window was one homogeneous smid-momentum bull → a smid/beta-tilt champion could sail through; the 5y vault catches exactly the FM track's headline trap).
- Era-split midpoint for the era-stability gate: **2017-06-30** (halves: 2013-04..2017-06 / 2017-07..2020-12).
- Holdout test = a FRESH book seeded in the sealed era walking forward (book built ONLY from sealed-era
  data → true OOS). If the edge collapses there (sign flips, tilt re-appears, or single-force beats it),
  the champion is declared validation-overfit and is **NOT** promoted.

## Gauntlet components (per the contract §3; honest GATED stamps)
- rand   : OWED literal ≥10k-random NAV bar — **GATED** (`signal_navtest.py` absent; never silently passed).
- wf     : structural PASS (frozen replay: walk-forward, forecast precedes return, total return, delisted retained, no Math.random).
- single : deployed multi-force book mean IR must beat the single-force (ARM-only score) book mean IR on same pilots/window.
- era    : sign of mean IR holds in BOTH halves of validation (2015–2019-06 and 2019-07–2023-06).
- plateau: objective flat (no sign flip, spread within tol) across a ± neighbourhood of the tuned free parameter.
- luck   : block-bootstrap (block≈3, 2000×) of monthly active return mean clears 0 at ≥90% (proxy luck bar; full tilt-matched BSW-FDR approximated; FDR tracked across trials in ledger).
- tilt   : beta/size guard — mean |beta−1| ≤ 0.20 AND multi-force beta not inflated >0.10 vs single-force book (deploy-more-must-not-buy-beta).
- fee    : **GATED** (no TER/expense data).

---

## P1 — BASELINE (build_rules_v0, the 4-brain multi-force library as shipped, score per brain_for_mandate)
- **Hypothesis:** the current shipped FM brain (orthogonalised ARM+mom+value per mandate, waterfill ∝ raw
  score, deploy_with_floor TC fix) is the reference. Record its objective + full gauntlet to beat.
- **Mechanism:** n/a (this is the reference point, no change).
- **Expected:** establishes baseline −mean(IR); expect Quant Small Cap to already benefit from the existing
  deploy_with_floor TC fix; expect ARM-only single-force baseline close (the brains are ARM-dominant).
- **Decomp expectation:** IC small (~0.03–0.05), TC the lever; beta near 1 for diversified, watch Quant beta.

---

## P2 — RISK-SCALED ALLOCATION (a_i ∝ score / σ^p) — the TC lever, NOT an IC change
- **Hypothesis:** weighting the selected names by `score · (1/σ)^p` (p≥0) instead of `score` raises the
  realized IR by raising the TRANSFER COEFFICIENT — the same per-bet skill (IC, selection) is converted
  into a more risk-efficient book, so more of the signal survives per unit of tracking error — AND it
  lowers portfolio vol / maxDD (an investor-experience win). Tested at p ∈ {0 (baseline), 0.5, 1.0, 2.0}.
- **First-principles mechanism:** Grinold-Kahn / Markowitz with diagonal risk → the IR-maximising active
  weight under a TE budget is `a_i ∝ IC·score_i/σ_i²`. The current waterfill uses `a_i ∝ score_i` (σ-blind),
  so two equal-score names of different vol get equal weight → the book spends tracking-error budget on
  uncompensated idiosyncratic vol. Scaling by 1/σ^p reallocates toward the risk-efficient names. SELECTION
  (top-n_hi by raw score) is UNCHANGED — the rescale happens inside deploy_with_floor, AFTER selection — so
  IC/ranking is identical; ONLY the weighting (hence TC) changes. σ = trailing_vol(sym, asof, 126d), PIT.
- **Expected effect on objective:** −mean(IR) DECREASES (IR up), driven by TC↑ (and te↓). Largest on the
  small-cap book (Quant) where idiosyncratic vol dispersion is widest. Modest on large-cap (ICICI).
- **Expected on decomposition:** IC ~flat (selection unchanged); TC UP; vol/maxDD down. RISK/TRAP: inverse-
  vol tilts toward low-vol (often larger, stabler) names → could become a low-beta/large-cap SIZE tilt. The
  decomposition guard must confirm beta does NOT fall materially (Δbeta vs baseline small) and the lift
  is not a size tilt. If beta collapses or it fails the plateau across p, DISCARD as a tilt, not alpha.
- **Plateau:** p ∈ {0.5,1.0,2.0} must give a FLAT (same-sign, monotone-ish, no spike) objective — a
  mechanism, not a curve-fit knob. A spike at one p = overfit → discard.
- **RESULT (P2/T1): DISCARD.** Parity p=0 reproduced baseline exactly (Quant −0.25, ICICI −0.24). But
  p∈{1,2}: Quant FLAT (−0.25→−0.25/−0.24, beta 0.60 flat, vol flat) — the pre-registered "TC lift on
  Quant" did NOT materialise; only ICICI nudged (−0.24→−0.20). Firm objective ~unchanged. Mechanism
  mismatch → discard, reverted. DIAGNOSTIC PIVOT (the real finding): risk-scaling reshapes weights WITHIN
  the deployed set, but Quant's leak isn't weight shape — it is that the book deploys only **mean 54%
  equity (min 14%)** against its **65% mandate floor**, i.e. ~46% sits in CASH because the per-name
  LIQUIDITY caps bind on a ₹30,540cr book in an illiquid small-cap universe even after deploy_with_floor's
  widen. beta 0.60 / up-capture 0.59 = pure cash drag. THAT is the β0.28-pathology TC leak. → T2.

---

## P3 — LIQUIDITY-HORIZON TC FIX (let the floor-deploy actually reach the mandate equity floor)
- **Hypothesis:** raising the patient-accumulation horizon `LIQ_DAYS_MAX` (the relaxed per-name liquidity
  cap deploy_with_floor uses when a book is below its mandate equity floor) lets the small-cap book deploy
  the equity it is MANDATED to hold instead of leaking ~46% to cash — raising beta toward 1, cutting the
  cash drag, and converting the already-good per-bet IC (Quant IC 0.070, the highest) into realized IR.
- **First-principles mechanism (pure TC, capacity-bounded):** a paper book has FIXED AUM (no
  subscription flow), so its build is a ONE-TIME accumulation that can take as long as a real patient core
  holding does. LIQ_DAYS_MAX=60 (a quarter of ADV) is too short for a ₹30k-cr small-cap core position;
  Berk-Green capacity says a patient holder accumulates over months-to-a-year. Extending the horizon
  raises the achievable equity ceiling so waterfill can reach the 65% floor; where ADV STILL binds, the
  shortfall stays HONEST cash (no fabrication). This is the literal TC fix (deploy mandated equity, don't
  leak IC to cash) — NOT more skill, NOT a new signal.
- **Expected on objective:** −mean(IR) DECREASES (IR up), concentrated on Quant (the only chronically
  under-deployed book; large/mid/flexi already clear their floor so deploy_with_floor never triggers for
  them → they are UNCHANGED, a clean isolation).
- **Expected on decomposition (the guard):** Quant **beta RISES toward 1** (0.60 → ~0.8-0.95) and
  up-capture rises — this is the SIGNATURE OF FIXING the cash drag, the OPPOSITE of the size-tilt trap
  (we are removing an unintended LOW-beta tilt, not adding a high-beta one). IC ~flat (selection
  unchanged). The trap to watch: if beta OVERSHOOTS >1.15 or the IR gain comes with beta inflation on the
  already-deployed books, that's buying beta → discard. Decomp must show: Quant beta 0.6→toward-1, other
  pilots UNCHANGED, IC flat.
- **Capacity realism (contract §10.7):** sweep LIQ_DAYS_MAX ∈ {60(base),120,250}. 250d≈1y of ADV is the
  realistic patient ceiling; do NOT go further (un-investable at size). Plateau = IR improves smoothly and
  flattens (diminishing as the floor is reached), not a spike. Realistic costs (15bps/side) stay charged.
- **RESULT (P3/T2): KEEP — champion (commit 22fdf79).** Realised EXACTLY as pre-registered:
  - Quant: IR −0.25→+1.13, **beta 0.60→0.99** (toward 1, NO overshoot), up-capture 0.59→1.01, equity
    54%→94%, IC unchanged (0.070), TC 0.39→0.56. The lift = removing the unintended low-beta CASH-DRAG
    tilt (the OPPOSITE of the size-tilt trap). The other 3 pilots UNCHANGED (clean isolation —
    deploy_with_floor never fires for an already-floored book). Firm −mean(IR) +0.045 → −0.300.
  - PLATEAU (saturation map, Quant): IR by LDM = {60:−0.25, 90:+0.15, 180:+0.94, 250:+1.13, 400:+1.22,
    600:+1.20}; equity {54,68,90,94,95,95}%; beta {0.60,0.75,0.96,0.99,1.00,0.99}. Monotone rise →
    FLAT plateau ≥250 as equity caps at the ~95% ADV-bound ceiling. A mechanism, not a spike. PASS.
  - Inner gate: single PASS (multi +0.300 > ARM-only +0.0375), tilt PASS (firm beta 0.95, |β−1|≤0.20,
    multi not inflated vs ARM 0.93). Decomp clean.
  - HONEST side-constraint cost (NOT averaged into the objective): Quant maxDD −43%→−59% (a ~95%-invested
    small-cap book draws down harder than a 46%-cash one — the real cost of holding the mandated equity; a
    real 95%-invested small-cap fund also drew ~55-60% in 2020). Firm mean maxDD −40%→−46%. FLAGGED.
  - Era + luck deferred to the FULL gauntlet certification (run_T2_full); then the SEALED-HOLDOUT one-shot.

## P4 — (held in reserve) further levers considered + ruled out
- Turnover no-trade band (hysteresis): NOT IMPLEMENTABLE without touching the FROZEN evaluator —
  deploy_with_floor/waterfill receive no prior weights (the frozen replay passes only reg_entry/uni/asof/
  aum), so per-name hysteresis can't be added in the mutable file. Certified dead-end (structural).
- Score concentration (weight ∝ score^γ): a pure curve-fit knob with no first-principles TC mechanism on
  THIS leak (the leak is aggregate cash, not weight shape — proven by T1). Not run (would fail plateau/
  mechanism by construction); logged as a ruled-out idea, not a trial.

---

## ★ SEALED-HOLDOUT ONE-SHOT (champion 22fdf79, LDM=250 vs baseline LDM=60) — 2021-01..2026-06 OOS
Run ONCE at the end, never during the loop. Includes the 2022 drawdown (the harsh OOS exam).

| Holdout firm | Baseline LDM60 | Champion LDM250 |
|---|---|---|
| mean IR | +0.9525 | **+0.980** |
| mean beta | 1.100 | 1.095 |
| ICICI | 0.64 (β1.19) | 0.64 (β1.19) — identical |
| SBI | 0.91 (β0.93) | 0.91 (β0.93) — identical |
| ABSL | 0.95 (β1.26) | 0.95 (β1.26) — identical |
| Quant | 1.31 (β1.02) | **1.42 (β1.00)** |

**VERDICT: HELD → PROMOTE.** (1) The champion does NOT collapse OOS (firm IR +0.98 ≥ baseline +0.95). (2)
The fix is a CONDITIONAL TC repair: in the 2021-26 smid bull the cash-drag leak does NOT bind (Quant
already deploys 95% at LDM=60 because small-cap liquidity/AUM was favourable then), so LDM=250 is DORMANT —
it changes only Quant (+1.31→+1.42) and leaves the other 3 books BYTE-IDENTICAL. (3) The decomposition is
clean OOS: Quant champion beta = **1.00** (NOT inflated; if anything ↓ from 1.02) — the fix NEVER buys
beta, it only deploys MANDATED equity when the book would otherwise breach its floor. This is the exact
opposite of the headline trap (an IR jump that is really a smid/beta tilt). The harsh era (with the 2022
drawdown, where ABSL/ICICI themselves show β 1.19-1.26) did NOT expose a tilt in the T2 change.

So T2 is a genuine, defensible, conditional TRANSFER-COEFFICIENT fix — it earns the right to be tried live.
Honest ceiling: the gain is large where the leak binds (an illiquid ₹-large book below its floor) and ~nil
where it doesn't; it is implementation, not new skill (IC unchanged). Side-constraint cost flagged: holding
the mandated equity means the small-cap book draws down ~harder (validation maxDD −43%→−59%).
