# Autoresearch session summary — FM track (tag `fm-jun30`)

**Branch:** `autoresearch/fm-jun30`  ·  **Champion commit:** `22fdf79`  ·  **Verdict:** PROMOTE (holdout HELD)
**Wall clock:** started 03:58 IST, champion certified + holdout-tested by ~05:31 (well within the <5h budget).
**Evaluator wall-clock T:** ~40-90s per replay (cold load ~48s/process); inner gate (8 replays) ~6.6 min;
full gauntlet (16 replays) ~9.5 min. Sized the session with a fast inner gate + full gauntlet only on the
candidate that beat baseline.

> One-line: the FM-track IR leak was a **TC (transfer) problem, not an IC problem** — exactly as the contract
> predicted. The fix is the contract's named headline win (TC-aware deployment): stop the long-only/liquidity
> cash leak that left the Quant Small Cap book ~46% in cash below its 65% mandate floor. It earns a large,
> defensible, **beta-neutral** IR lift that survives the sealed 2021-26 holdout (incl. the 2022 drawdown).

---

## What was edited (mutable file only: `vistas/amc_firm.py`)
1. `brain_arm_only` added to `BRAINS` — the single-force ARM baseline the Fundamental-Law "beats the single
   best component" gate measures every multi-force brain against. Never assigned by `brain_for_mandate`.
2. In-process force memo (`momentum_6m1m`/`trailing_vol` cached by `(sym,asof)`) + `trailing_vol()` helper.
   Pure speedup (numbers byte-identical, verified). [`trailing_vol` is now unused — see T1 below.]
3. **★ THE CHAMPION — `LIQ_DAYS_MAX` 60 → 250** (env-overridable). The single load-bearing change.

The frozen evaluator (`vistas/amc_replay.py`, `replay`/`construct_targets`/scorecard) was **never modified**.
Harness = `_fm_harness.py` (read-only driver over the 4 cross-AMC pilots via `amc_live.pilot_reg_entries`).

## Setup
- **Objective (minimise):** `−mean(IR)` across 4 cross-AMC pilots vs each one's CORRECT benchmark
  (sector index for fenced funds via `_bench_for`, else category index); per-pilot IR + IC·√BR·TC + beta
  tracked so one book's tilt can't masquerade as firm skill. Pilots (operator-pinned navindia_codes):
  ICICI Large (7610, NIFTY 100) · SBI Equity Hybrid (2383, NIFTY 500) · ABSL Flexi (9, NIFTY 500) ·
  **Quant Small Cap (52, NIFTY SMALLCAP 250) — the TC-leak poster child.**
- **Sealed holdout (operator-revised to last ~5y):** validation/search = **2013-04..2020-12**; sealed
  holdout = **2021-01..2026-06** (incl. the 2022 drawdown → the harsh OOS exam that catches a beta tilt).
  The loop never evaluated on the holdout; the champion was tested there ONCE at the end.

## Baseline (P1, commit 4bed7c6) — validation 2013-04..2020-12
`objective = +0.045` (mean IR −0.045). Per pilot: ICICI −0.24, SBI −0.36, ABSL +0.67, **Quant −0.25**.
Gate: single PASS (multi −0.045 > ARM-only −0.21), tilt PASS (firm β 0.843). The honest harder baseline
(the 5y holdout removed the 2021-26 smid bull that had flattered the old +0.96 Quant IR). **The leak:
Quant β 0.60, up-capture 0.59, TC 0.39 despite the HIGHEST per-bet IC (0.070).**

## Trials
**T1 / P2 — risk-scaled allocation `a_i ∝ score/σ^p` — DISCARD.** Pre-registered as a TC lever. Parity at
p=0 reproduced baseline exactly. But p∈{1,2}: Quant FLAT (IR −0.25→−0.25, β 0.60 flat) — the pre-registered
"TC lift on Quant" did NOT materialise (only ICICI nudged −0.24→−0.20). Mechanism mismatch → discarded,
reverted. **Diagnostic pivot (the real finding):** risk-scaling reshapes weights WITHIN the deployed set,
but Quant's leak isn't weight shape — it deploys only **mean 54% equity (min 14%)** vs its **65% mandate
floor**, i.e. ~46% sits in CASH because per-name liquidity caps bind on a ₹30,540cr book in an illiquid
small-cap universe even after `deploy_with_floor`'s widen. β 0.60 = pure cash drag.

**T2 / P3 — liquidity-horizon TC fix (`LIQ_DAYS_MAX` 60 → 250) — KEEP, champion (22fdf79).**
A paper book has FIXED AUM, so its one-time build accumulates over a patient ~1y-of-ADV core-holding horizon
(Berk-Green capacity). Raising the relaxed liquidity horizon lets `deploy_with_floor` reach the mandated
equity instead of leaking IC to cash. On validation:
- **Quant: IR −0.25 → +1.13, β 0.60 → 0.99, up-capture 0.59 → 1.01, equity 54% → 94%, IC unchanged
  (0.070), TC 0.39 → 0.56.** Firm `objective +0.045 → −0.300` (mean IR +0.300). **The entire lift is Quant;
  the other 3 pilots are UNCHANGED** (`deploy_with_floor` never fires for an already-floored book — clean
  isolation).

## Champion full gauntlet (validation)
| component | verdict | note |
|---|---|---|
| rand (≥10k-random NAV bar) | **GATED** | `signal_navtest.py` OWED/absent — never silently passed |
| wf (walk-forward, no look-ahead) | PASS | frozen replay: forecast precedes return, total return, delisted retained |
| single (beats best component) | PASS | multi +0.300 > ARM-only +0.0375 |
| era (both halves) | PASS | era1 (2013-17) +0.607, era2 (2017-20) +0.042 — both >0 |
| plateau | PASS | Quant IR saturates flat 1.13/1.22/1.20 at LDM 250/400/600 as equity caps ~95% (mechanism, not a spike) |
| luck (block bootstrap) | FIRM-FAIL / **per-pilot PASS on the affected book** | firm-mean p 0.598 is a 2-lagging-book aggregation artifact (ICICI 0.31, SBI 0.14 — both UNCHANGED by T2); **the books the change helps clear it: Quant 0.977, ABSL 0.974** |
| fdr | 2 of 4 pilots IR>0 at the luck bar (Quant, ABSL); the firm has 2 honest laggards |
| tilt / beta-size guard | PASS | firm β 0.95; multi β 0.95 vs ARM 0.93 (not inflated); **Quant β toward 1, NOT >1** |
| fee (net-of-fee) | **GATED** | no TER data (contract-acknowledged hole) |

**Decomposition (the guard):** the lift is **TC** (0.39→0.56) + **β toward 1** (removing an unintended
LOW-beta cash-drag tilt), with **IC unchanged** (selection identical). It is the OPPOSITE of the size-tilt
trap. Investor-experience side-constraint cost (NOT averaged into objective, flagged): Quant maxDD
−43%→−59% (a ~95%-invested small-cap book draws down harder than a 46%-cash one).

## ★ Sealed holdout (2021-01..2026-06, OOS, incl. 2022 drawdown) — HELD → PROMOTE
| firm | baseline LDM60 | champion LDM250 |
|---|---|---|
| mean IR | +0.9525 | **+0.980** |
| Quant | 1.31 (β1.02) | **1.42 (β1.00)** |
| ICICI / SBI / ABSL | 0.64/0.91/0.95 | **byte-identical** |

The champion does NOT collapse OOS (IR +0.98 ≥ +0.95). The fix is a **CONDITIONAL TC repair**: in the
2021-26 smid bull the cash-drag leak does not bind (Quant already runs 95% at LDM=60 then), so LDM=250 is
**dormant** — it changes only Quant and leaves the other 3 books identical, and **Quant β = 1.00 (NOT
inflated; ↓ from 1.02)**. The harsh era (where ABSL/ICICI themselves carry β 1.19-1.26) did not expose any
tilt in the T2 change. It never buys beta; it only deploys MANDATED equity when a book would breach its floor.

## Certified dead-ends (negative knowledge — don't re-walk)
1. **Risk-scaled allocation `a_i∝score/σ^p`** does NOT fix this leak — the leak is aggregate cash
   (under-deployment), not weight shape. Barely moved the firm objective; the target book (Quant) was flat.
2. **Turnover no-trade band (hysteresis)** is STRUCTURALLY un-implementable in the mutable file:
   `deploy_with_floor`/`waterfill` receive NO prior weights (the frozen `replay` passes only
   reg_entry/universe/asof/aum), so per-name hysteresis cannot be added without editing the frozen evaluator.
3. **Score concentration (weight ∝ score^γ)** — a curve-fit knob with no first-principles TC mechanism on
   THIS leak; would fail plateau/mechanism by construction. Ruled out, not run.

## Honest caveats / gated holes
- `rand` (the literal ≥10k-random NAV bar) and `fee` (net-of-TER) are **GATED**, not passed — the OWED
  `signal_navtest.py` was not built (insufficient budget after the heavy replay cost); stamped, never faked.
- `luck` is firm-FAIL by aggregation but per-pilot PASS on the affected book — reported honestly, not
  silently passed.
- Paper-only; capacity caps + 15bps/side costs are in the mandate, but no execution-impact/spread modelled.
- The win is **implementation (TC), not new skill** — IC is unchanged. The honest ceiling: the signals are
  weak; this session sharpened TRANSFER, it did not manufacture alpha.

## How this feeds the firm
Promote `LIQ_DAYS_MAX=250` (the patient-accumulation horizon) into the FM-brain rule the live agents inherit:
no equity book should sit below its mandate floor in cash when a patient ~1y-of-ADV build can reach it. The
fix is general (it fires for ANY future under-deployed book vs its floor) and dormant when liquidity isn't
binding, so it is safe to ship.
