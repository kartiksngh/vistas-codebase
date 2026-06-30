# AUTORESEARCH: Vistas (autonomous rule discovery for the Agentic AMC)

An adaptation of Karpathy's `autoresearch` loop to the Vistas persona stack. The idea is the same: an
autonomous agent edits one file, runs a fixed evaluation, keeps the change if it helps and reverts it if
it does not, and loops without supervision. The subject is different: instead of minimising `val_bpb` on a
language model, the agent searches for the **rule logic** behind the Analyst, the Fund Manager, and the CIO,
and is scored by the **Fundamental Law** (`IR = IC·√BR·TC`) under the house discipline (`CLAUDE.md`,
`AGENTIC_AMC.md`, `FUNDAMENTAL_LAW.md`).

Read this whole file before starting. The setup, the loop, and the ledger mirror Karpathy's doc closely so
the shape is familiar; sections 0, 3, 5, 8 and 10 are where the finance adaptation lives, and they are not
optional.

---

## 0. The one thing that makes this different from Karpathy (read first)

Karpathy's loop works because `val_bpb` on held-out text is **almost impossible to game**. You cannot lower
it without the model actually learning. A trading metric is the opposite: an autonomous searcher running
hundreds of trials against a backtest will **find rules that fit the noise** of our specific history, and the
equity curve will look beautiful for reasons that will not survive live markets. Pointed naively at "maximise
OOS Sharpe by editing the FM brain", this loop becomes a machine for manufacturing exactly the curve-fit
garbage the whole project forbids (`CLAUDE.md`: *no curve-fit, self-explaining, defensible*; the equal-weight
Mesh blend that **failed** in `mesh_backtest.py` is the standing proof that a combination of validated parts
is not automatically validated).

So we change three things versus Karpathy, and these three changes are the point of this document:

1. **The objective is not a raw metric. It is a gated, deflated score.** A change only "counts" if it passes
   the full discipline gauntlet (§3), not merely if it raises IC or IR. A change that lifts raw IC but fails
   the gauntlet is logged `discard` and reverted, exactly as Karpathy reverts a change that raises `val_bpb`.
   The gauntlet is the existing house discipline made into a hard gate: all-starts × ≥10k random, ≥5y
   windows, percentile-vs-random, walk-forward with no look-ahead, total return, **beats the single best
   component signal**, era-stable, **parameter-plateau-robust**, the tilt-matched luck bar, FDR across the
   trials, factor/sector deflation, net-of-fee, and the beta/size-tilt guard.

2. **Every trial is pre-registered before it runs (anti-hindsight).** Before editing, the agent writes the
   hypothesis, the first-principles mechanism, and the *expected* effect on the objective **and on the
   decomposition** (is the lift supposed to come from IC, from TC, or is it at risk of being a beta/size
   tilt?). A "keep" is only honest if the realised effect matches the pre-registered mechanism. Karpathy does
   not need this because `val_bpb` cannot be rationalised after the fact; a P&L number always can.

3. **A final holdout era is sealed from the search itself.** The gauntlet protects each trial, but running
   hundreds of trials still risks overfitting the *validation* periods. So one contiguous era (default: the
   most recent ~3 years, or a pre-chosen block) is **never touched during the loop**. The session ends with a
   single one-shot evaluation of the surviving champion on that sealed era. If the edge collapses there, the
   session's "winner" is declared overfit and is **not** promoted. This is `t = IR·√years` patience turned
   into an operational gate.

If you internalise nothing else: **the evaluator IS the discipline. The agent is only allowed to climb hills
the discipline has already certified are real.**

---

## 1. What autoresearch optimises here (and what it does NOT)

Autoresearch optimises the **deterministic rule modules** that the personas stand on. It is cheap, fast, and
reproducible. It does **not** run the expensive LLM agent rounds.

- **In scope (the loop edits these):** the signal construction the Analyst desk reads; the FM "brain" (the
  score function and the deployment logic in `amc_firm` / `amc_replay`); the CIO's doable/posture/allocation
  arithmetic. These are Python rules with parameters, scored by deterministic backtests.
- **Out of scope (the loop never touches these):** the live LLM round (`amc_round start/finish/publish` →
  `_amc_rebalance.js`, ~2M tokens, ~7 min, real money-shaped decisions); the data ingestion and identity
  spine; `analytics.py` formulas and the JS parity port (display plane).

The relationship to the firm: **autoresearch discovers robust rules in replay; the LLM agents then inherit
those rules through their charters; the live-forward paper-AMC running from today is the true out-of-sample
referee.** Autoresearch is rule discovery under the discipline. It is never the final word; the forward clock
is (`AGENTIC_AMC.md` §0, §10).

---

## 2. Setup (per-track, a fresh run)

Run **one track at a time** unless you have separate GPUs/workers. Each track gets its own branch and ledger.

1. **Agree a run tag** with the operator: a persona prefix + date, e.g. `analyst-jun30`, `fm-jun30`,
   `cio-jun30`. The branch `autoresearch/<tag>` must not already exist (a fresh run).
2. **Create the branch:** `git checkout -b autoresearch/<tag>` from current master.
3. **Read the in-scope files (full context):**
   - The track's charter doc: `ANALYST_GOLDMINE.md` / `FM_INTELLIGENCE.md` + `FUND_MANAGER_ANALYSER_DESIGN.md`
     / `CIO_INTELLIGENCE.md`.
   - The evaluation backbone `FUNDAMENTAL_LAW.md` and the house rules in `CLAUDE.md`.
   - The mutable rule file for the track (§4) and the frozen evaluator for the track (§3). Read them; do not
     guess their interfaces.
   - `MEMORY.md` RESUME block and `WORKPLAN.md` so you start on the real open problem, not a solved one.
4. **Verify the data exists** (point-in-time, total return, delisted retained): the holdings panel
   (`holdings_history.parquet`, ~158mo, keyed `vst_id`), the TR panel, the reconstructed benchmarks
   (`vistas/benchmarks.py`), and the licensed `arm_repo/` if the track uses ARM. If anything is missing,
   stop and tell the operator which build to run; do not fabricate inputs.
5. **Confirm the frozen evaluator runs on the baseline and SEAL the holdout** (§3, §8). The very first run is
   always the **baseline**: run the evaluator on the rule module exactly as it is, record the objective and
   the gate verdict, and **measure the wall-clock runtime** (Karpathy can assume a fixed 5-min budget; here
   the runtime depends on the evaluator, so you must measure it to size the session, §7).
6. **Initialise the ledger** `autoresearch_<tag>.tsv` with the header row only (§6). Leave it **untracked**
   by git.
7. **Confirm** setup looks good, then begin the loop and do not stop to ask permission again (§5, §7).

---

## 3. The frozen evaluator: the sacred harness (DO NOT MODIFY)

This is the analog of Karpathy's `prepare.py` / `evaluate_bpb`. It is **read-only inside the loop**. If you
find yourself wanting to edit the evaluator to make a result pass, that is the signal to reject the result,
not to weaken the test.

**Existing harness components (reuse, do not alter):**
- `arm_backtest.py`, the analyst signal IC backtest (ARM IC ≈ 0.03–0.045; all-starts × random, ≥5y windows,
  percentile-vs-random). This is the template for "is a signal real".
- `mesh_backtest.py`, the signal-combination backtest. It already records the **negative result** that the
  equal-weight blend dilutes ARM (IC 0.071 → 0.054). Negative knowledge is first-class; do not re-walk it.
- `amc_replay.py` (`replay()`, `construct_targets`) + `replay_pilots.py` / `make_absl_firm.py --replay-only`
 , the FM walk-forward replay that produces the scorecards (`IC·√BR·TC·IR` vs the benchmark). Note the
  benchmark logic already implemented: theme-fenced funds are scored vs their **sector** TR index, not NIFTY
  500, so sector beta does not masquerade as alpha (`_bench_for` / `_THEME_BENCHMARK`).
- `funds_attribution.py` (`load_panel`, groups by `navindia_code`) and `funds_flows.py`
  (`stock_active_flows`, the gross / price-adjusted / net-active decomposition), the attribution and flow
  inputs the FM/CIO scores rest on.

**Build this FIRST if it does not yet exist (it is currently OWED, per `MEMORY.md`):**
- `vistas/signal_navtest.py`, productionise the scratchpad signal→NAV harness: the validated
  all-starts × ≥10k NAV bar on a signal's actual treatment (the literal NAV test, not just the IC-beat
  metric). Until this exists, **no FM or analyst champion may claim it passed the NAV bar** ("don't trust
  'ARM helps' on the IC-beat metric alone", `MEMORY.md` KEY RISK). This file is part of the frozen evaluator;
  build it, validate it, freeze it, then start the loop.

**The gauntlet (the gate every champion must clear):**
1. **Random baseline:** beats ≥10k random portfolios/signals of the same shape on percentile, over **fixed
   ≥5y windows, all-starts** (not one lucky window).
2. **Walk-forward, no look-ahead:** the forecast strictly predates the return (`a_t` before `r_{t+1}`); no
   `Math.random`, no full-window smoothing leak. Total return, delisted names retained.
3. **Beats the single best component:** the combination must beat its best individual signal, or it is
   dilution (the Mesh-blend lesson). For the FM, the deployed book must beat the naive single-force book.
4. **Era stability:** the edge holds across distinct regimes, not only the 2025–26 momentum tape (the
   turnover×alpha finding is contemporaneous and regime-specific; it is descriptive, not forward edge).
5. **Parameter-plateau robustness:** the objective is **flat across a neighbourhood** of every free
   parameter, not a sharp spike. A spike is a curve-fit; a plateau is a mechanism. This single test does most
   of the anti-overfit work.
6. **Luck bar + FDR:** the tilt-matched block bootstrap (circular block-resample with replacement of the mean
   active return, block≈3, ≥2000×) clears its bar; and across all trials in the session, control the false
   discovery rate (Barras-Scaillet-Wermers) so the champion is not just the luckiest of many.
7. **Decomposition / tilt guard (the finance-specific trap):** the lift must be attributable to **IC** or
   **TC**, not to an **unintended beta or size tilt** vs the benchmark. Excess that is really an
   equal-weight/smid tilt vs a cap-weighted index is **not alpha** (this trap is flagged repeatedly in the
   replay notes: Quant SmallCap, ABSL β 1.04→1.15). Beta-control and size-control before crediting a result.
8. **Net-of-fee** where data allows.

**Honest holes in the gauntlet right now (label, do not pretend):** net-of-fee is **data-gated** (no TER/
expense data yet), point-in-time index constituent **weights** are lacking (W-HIST), the factor library is
buildable but not built, and there is no manager-tenure DB (so everything is **scheme-level**). A champion
that could not be run through a gauntlet component because the data is missing must be stamped exactly that:
"component X not run, data-gated", never silently counted as passed. This is the project's own
"label unproven ones diagnostic" rule.

---

## 4. The three tracks

Each track names its **mutable file** (Karpathy's `train.py`), its **search space** (what is fair game), its
**objective**, and its **overfit trap** (the specific landmine the gauntlet must catch).

### 4.1 Analyst track
- **Objective (minimise):** `−IC_deflated` of the desk's pitch ranking, where IC is the rank correlation
  between the ex-ante pitch direction and the realised forward residual return (Fama-MacBeth mean, `t =
  IC·√(BR·years)`), after the gauntlet and FDR. Secondary: decorrelation from other desks (a desk that echoes
  another adds no breadth).
- **Mutable file:** the signal-construction module the desk reads (the Goldmine forces assembly: which forces,
  how combined, orthogonalisation, z-scoring/winsorisation, the ARM treatment, the value/momentum blend,
  lead-lag, EW vs FF sector rollup, the conviction bar, horizon/decay).
- **Search space (fair game):** force weights and combinations, transforms, lookback windows, rebalance
  cadence, the orthogonalisation scheme. **Constraint:** every clause traces to a validated force; no free
  parameter fit to maximise backtest IC; must clear the parameter plateau.
- **Overfit trap:** the equal-weight blend **dilutes** ARM (proven). Herding does **not** predict forward
  returns (refuted live). The honest ceiling: bet-level signals are **weak** (holding-rank IC split-half
  persistence ≈ 0.097); the loop can sharpen ranking and cut false positives, it **cannot manufacture
  signal**. Expect small, plateau-robust gains (the validated win is ARM+momentum+value orthogonalised,
  +~0.037 OOS IC@6m, not a miracle).

### 4.2 Fund Manager track
- **Objective (minimise):** `−IR_realized` vs the **correct** benchmark (sector index for fenced funds, else
  the reconstructed category benchmark), holdings-based and NAV-based, with the skill-vs-luck gate; **and the
  result must survive the decomposition** into `IC·√BR·TC` with the beta/size-tilt guard. Plus the
  investor-experience constraints (rolling-1y/3y win-rate, max drawdown, Ulcer) as hard side-constraints, not
  averaged into the objective.
- **Mutable file:** the FM brain in `amc_firm` (`build_rules_v0`, the score function currently `score=arm`)
  and the deployment logic (`deploy_with_floor`, the TC-aware implementation, sizing, turnover control,
  liquidity caps, active-share/breadth `n_hi`). Each FM should carry a **distinct** multi-force brain, not an
  ARM clone (the standing critique).
- **Search space (fair game):** the score combination, the **TC-aware deployment** (the biggest known win:
  stop the long-only/liquidity IR leak that crushed Quant SmallCap from IR −0.47 to +0.92 on pure
  implementation), turnover/constraint control, breadth, the equity-floor logic. **Constraint:**
  mandate-compliant, capacity caps (~₹1–2.5k cr for momentum tilts), realistic costs, no look-ahead.
- **Overfit trap:** the IR leak is a **TC** problem (transfer), not an IC problem; fix the implementation, do
  not chase more "skill". And the headline trap: an IR jump that is really a **smid/beta tilt vs a
  cap-weighted index is not alpha** (Quant SmallCap excess is partly an EW/size tilt; ABSL gained beta when
  it deployed more). The decomposition guard exists precisely to catch this; honour it.

### 4.3 CIO track
- **Objective (minimise):** `−IR_firm` where the firm's information ratio exceeds the average manager **only
  if the desks are de-correlated** (team-IR `= s·√M`); so the CIO is graded on **breadth cultivated and
  crowding killed**, with a drawdown-preemption credit from the fragility map.
- **Mutable file:** the CIO arithmetic: the doable-engine priority/urgency formula (priority = conviction ×
  materiality × 1/urgency-window; urgency ≠ timeline), the posture-map recommender (the rule combination of
  validated Mesh signals → aggressive/neutral/defensive per sector), and the allocation engine (constrained
  Markowitz / Black-Litterman where the views are **only** validated forces and the prior is the policy
  benchmark).
- **Search space (fair game):** the priority/urgency arithmetic, posture thresholds, allocation tilt
  strength, the crowding/fragility caps and the effective-breadth penalty. **Constraint:** the **descriptive**
  pieces (3-lens pulse, fragility map) ship freely; the **prescriptive** pieces (recommendation, allocation)
  are **gated** and must be backtested **as a whole**, beat the single best signal and the policy benchmark
  net-of-fee OOS, era-stable. Provenance is mandatory: a doable without its source forces is a defect.
- **Overfit trap:** the **√BR effective-breadth collapse** (the live CIO already caught ONGC as the top
  target in 14/27 books → the firm ran ~5–6 independent bets, not 27). FII flow is **positive-feedback** in
  India (an effect of returns); never route a contemporaneous FII–NIFTY correlation as an action without the
  lead-lag/Granger gate. And again: the combination of validated parts is **not** a validated whole.

---

## 5. The experiment loop (with pre-registration)

This is Karpathy's loop with a pre-registration step inserted at the front and a gate (not just a comparison)
at the back. Run it on the track's branch.

**LOOP (until the operator stops you, or the §7 budget is spent):**

1. **Look at git state:** the branch/commit you are on, and the current champion's objective + gate verdict.
2. **Pre-register the trial** (append to `autoresearch_<tag>_prereg.md`, untracked): the hypothesis, the
   first-principles mechanism (why it should work), and the **expected** effect on the objective **and on the
   decomposition** (lift from IC? from TC? any beta/size-tilt risk?). One paragraph. This is written
   **before** you edit, and it is the thing a later "keep" is checked against.
3. **Edit the mutable rule file** with the experimental idea.
4. **git commit** (the rule change).
5. **Run the frozen evaluator** for the track: redirect everything to a log, do not flood context, e.g.
   `python -m vistas.<evaluator> > run.log 2>&1` (use the track's actual entry point: `arm_backtest` /
   `mesh_backtest` / `signal_navtest` for analyst; `replay_pilots` / `make_absl_firm --replay-only` for FM;
   the CIO firm-replay for CIO). **The evaluator runs on the validation eras only; it must not touch the
   sealed holdout (§8).**
6. **Read the result:** grep the objective and the gate lines out of the log (do not read the whole log into
   context). If the grep is empty the run crashed; `tail -n 50 run.log`, fix if it is something dumb (typo,
   missing import), else skip it (§ crashes).
7. **GATE (the decision):**
   - **PASS** = the change clears the **whole gauntlet** (§3), **and** the realised effect matches the
     **pre-registered mechanism** (the lift came from where you said it would, not from an unintended tilt the
     decomposition flagged), **and** the objective improved. → **keep**, advance the branch (keep the commit).
   - **Improved the number but FAILS any gauntlet component** (overfit; or it is a beta/size tilt; or it fails
     the parameter plateau; or it fails the luck bar / FDR; or the mechanism does not match the prereg) →
     **discard**, `git reset` back, and **log why** (this is negative knowledge; it stops the next agent
     re-walking the dead end).
   - **Equal or worse objective** → **discard**, `git reset` back. (Equal objective with **simpler** code is a
     **keep**: the simplicity criterion, which this project already prizes.)
8. **Log to the ledger** (§6; untracked, do not commit it).
9. **Crashes:** Karpathy's rule. Dumb and easy (typo/import) → fix and re-run. Idea fundamentally broken →
   log `crash` and move on.
10. **Never stop** to ask "should I keep going?" within the budget. If you run out of ideas: re-read the
    charter doc and `FUNDAMENTAL_LAW.md` for a new angle, combine previous plateau-robust near-misses, read
    the canonical sources cited in the docs (Grinold-Kahn, Clarke-de Silva-Thorley, Cremers-Petajisto,
    Coval-Stafford), or try a more radical but still first-principles brain. Rewind across the branch only
    very sparingly, if ever.

**The reset discipline matters more here than in Karpathy.** Because the gate can reject a numerically better
result (as overfit), "advance" means "passed the gauntlet **and** the prereg", not "lower number". Do not let
a pretty backtest tempt you past the gate.

---

## 6. The ledger format (`autoresearch_<tag>.tsv`)

Tab-separated (commas break in descriptions). Header row, then one row per trial. **Untracked by git.**

```
commit	persona	obj_value	gate	decomp	status	prereg_ref	description
```

1. `commit`, short hash (7 chars).
2. `persona`, analyst | fm | cio.
3. `obj_value`, the deflated objective on the validation eras (e.g. IC 0.118, or IR 0.50); use 0.000000 for
   crashes.
4. `gate`, compact per-component verdict, e.g. `rand:PASS wf:PASS single:PASS era:PASS plateau:FAIL luck:PASS
   fdr:NA tilt:PASS fee:GATED`. A champion needs every applicable component PASS; `GATED`/`NA` are honest
   "not run" stamps, never silent passes.
5. `decomp`, the attribution that proves it is not a tilt, e.g. `IC+0.01 TC+0.12 beta:flat size:flat` (FM),
   or `breadth:6→9 crowding:down` (CIO), or `na` (analyst single-signal).
6. `status`, keep | discard | crash.
7. `prereg_ref`, the line/anchor in `autoresearch_<tag>_prereg.md` so the kept change is auditable against
   what you predicted.
8. `description`, short text of what the trial tried (no commas).

Example:

```
commit	persona	obj_value	gate	decomp	status	prereg_ref	description
a1b2c3d	fm	0.240000	rand:PASS wf:PASS single:PASS era:PASS plateau:PASS luck:PASS fdr:NA tilt:PASS fee:GATED	IC+0.00 TC+0.00 beta:flat	keep	P1	baseline (build_rules_v0 score=arm)
b2c3d4e	fm	0.920000	rand:PASS wf:PASS single:PASS era:PASS plateau:PASS luck:PASS fdr:PASS tilt:PASS fee:GATED	IC+0.00 TC+0.46 beta:flat size:flat	keep	P2	TC-aware deploy_with_floor on Quant book
c3d4e5f	fm	0.310000	rand:PASS wf:PASS single:FAIL era:FAIL plateau:FAIL luck:NA fdr:NA tilt:FAIL	beta:0.28→0.92	discard	P3	uncapped smid tilt, IR up but it is a size tilt not alpha
d4e5f6g	analyst	0.054000	single:FAIL	na	discard	P4	equal-weight mesh blend, dilutes ARM (known dead end)
```

---

## 7. The <5hr budget (this differs from Karpathy)

Karpathy fixes 5 minutes per run and does ~12/hour. Here the per-run time is **not** fixed; it depends on the
evaluator, so you size the session from the baseline measurement.

- On the **baseline run** (§2.5), record the wall-clock evaluator time `T`. The session budget is ~5 hours of
  wall clock; reserve ~20–30 min for the baseline + the sealed-holdout champion test (§8). That leaves roughly
  `(270 / (T_minutes + edit_overhead))` trials. If `T` is large (a full ≥10k-random gauntlet over 158 months
  can be minutes, not seconds), prefer a **fast inner gate** for screening (a cheaper subset of the gauntlet)
  and run the **full gauntlet only on a candidate that clears the fast gate**. Never let the cheap inner gate
  promote a champion; it only filters.
- **Per-run timeout:** if a single evaluator run exceeds ~3× the baseline `T` (or any run exceeds a hard wall,
  e.g. 15 min), kill it, treat it as a crash, revert.
- **Stop at the budget**, run the §8 champion test, and write the summary (§ end). Do not silently overrun.

---

## 8. The final holdout: the champion's one-shot OOS test

This is the deepest protection and it is **not** in Karpathy.

- **Seal one era before the loop starts** (§2.5): default the most recent ~3 years, or a pre-chosen
  contiguous block, chosen **before** any trial. Record which era in the prereg file. The evaluator's
  validation eras (§5.5) **exclude** this block. The loop never sees it.
- **At the end of the budget**, take the surviving champion (the kept commit with the best gauntlet-passing
  objective) and run it **once** on the sealed era.
- **Verdict:** if the edge holds (same sign, plateau-robust, decomposition still clean), the champion is a
  candidate to promote into the live-forward charters (§9). If it **collapses** on the sealed era, the
  session has produced an overfit winner: **do not promote it**; log it as "validation-overfit, holdout-fail"
  and keep it as negative knowledge. A null session that correctly refuses to promote a mirage is a
  **successful** session, not a failed one.

---

## 9. How this feeds the digital fund (the live-forward firm)

Autoresearch does not run the fund. It **upgrades the rules the fund's agents inherit.**

1. A promoted champion (gauntlet-passing **and** holdout-surviving) updates the relevant rule module
   (`amc_firm` brain, the analyst signal assembly, or the CIO arithmetic), under the normal
   parity/validation/publish discipline (`CLAUDE.md`).
2. The LLM agents' charters reference the rule modules and the skills/memories, so on their **next run** they
   inherit the improved logic instantly ("the terminal grows → the agents benefit immediately", extended from
   data to rules; `AGENTIC_AMC.md` §8). The knowledge-sync that folds a new finding into the charters follows
   the **same pre-registration** discipline, so evolution stays genuine learning and never becomes hindsight
   curve-fit.
3. The live-forward cadence (`amc_round start --amc … / finish / publish` → `_amc_rebalance.js`, monthly LLM
   round + daily python mark) then runs the upgraded firm **forward from today**. That forward clock is the
   real out-of-sample test; replay and the sealed holdout only earn a rule the right to be tried live, they do
   not certify it as live alpha.

Operationally, "how to run a digital fund" here is the closed loop: **autoresearch finds defensible rules →
the agents inherit them → the firm paper-trades forward → the attribution/scorecard engine grades it honestly
(IC·√BR·TC, luck bar, FDR) → confirmed lessons flow back into the rules, pre-registered.** The agents
synthesise validated signals; they never manufacture alpha. An "insight" the signals do not support is a
hallucination, not edge.

---

## 10. FLAGGED: what must not become a silent assumption

1. **This loop is a curve-fit machine unless the evaluator restrains it.** The entire value is §3 + §5 + §8.
   A version of this that optimises raw IC/IR is actively harmful and violates the house discipline.
2. **A combination of validated signals is NOT automatically validated**, the failed Mesh blend is the
   proof. Backtest the whole brain / recommender / allocator, not its parts.
3. **The signals are weak.** Holding-rank IC split-half persistence ≈ 0.097. The honest output of a good
   session is small, plateau-robust, defensible gains plus a pile of certified dead ends, **not** a 1-year
   miracle. Calibrate expectations and say so.
4. **An IR jump can be a beta/size tilt, not alpha.** Beta-control, size-control, and use the **right**
   benchmark (sector index for fenced funds). The decomposition guard is mandatory.
5. **Gauntlet holes are data-gated, not passed.** Net-of-fee (no TER data), point-in-time index weights
   (W-HIST), the factor library (not built), manager-tenure (scheme-level only). Stamp every champion with
   which components could not be run.
6. **No look-ahead, ever.** Point-in-time data, delisted names retained, total return, forecast strictly
   before the return, no `Math.random`. A contemporaneous correlation is the trend-chasing mirage, not skill.
7. **Paper ≠ live.** No execution/impact/liquidity friction is fully modelled; capacity caps and realistic
   costs must be in the mandate or the paper IR is flattered. This is a research harness, not advice, and it
   trades no real money.
8. **Pre-registration is non-negotiable.** A "keep" that was not pre-registered, or whose realised mechanism
   does not match the prereg, is hindsight, not a finding.

---

## Appendix: the Karpathy → Vistas mapping

| Karpathy `autoresearch` | Vistas autoresearch |
|---|---|
| `train.py` (mutable) | the persona's rule module: analyst signal assembly · `amc_firm` FM brain · CIO arithmetic |
| `prepare.py` / `evaluate_bpb` (frozen) | `arm_backtest.py` · `mesh_backtest.py` · `amc_replay`/`replay_pilots` · the OWED `signal_navtest.py`, all read-only |
| `val_bpb` (minimise) | `−IC_deflated` (analyst) · `−IR_realized` decomposed (FM) · `−IR_firm` via breadth (CIO), each **gated** |
| fixed 5-min budget | measured evaluator time `T`; session sized to ~5 hr; fast inner gate + full gauntlet on candidates |
| keep if `val_bpb` lower | keep if it **passes the gauntlet AND matches the prereg AND** improves the objective |
| `results.tsv` | `autoresearch_<tag>.tsv` + `autoresearch_<tag>_prereg.md` (pre-registration, anti-hindsight) |
| simplicity tiebreaker | same (the project already prizes simple-and-defensible over fragile) |
| held-out val set | walk-forward OOS **plus** a sealed holdout era the search itself never sees (§8) |
| never stop | never stop within budget; then run the sealed-holdout champion test and summarise |

*Sources in-repo:* `CLAUDE.md`, `AGENTIC_AMC.md`, `FUNDAMENTAL_LAW.md`, `CIO_INTELLIGENCE.md`,
`DIGITAL_AMC.md`, `FUNDS_INTELLIGENCE_RESEARCH.md`, `WORKPLAN.md`, `MEMORY.md`, and the existing
`arm_backtest.py` / `mesh_backtest.py` / `amc_replay.py` harness.
