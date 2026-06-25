# Vistas Fund-Manager Intelligence — Design Blueprint
### "Reflect the real managers, then synthesise the expert one"

The **Fund-Manager (FM) persona** of the Vistas CIO stack. It answers two questions that the other
personas do **not**:

1. **REFLECT** — *"How good is each real manager, at what, and is it skill or luck?"* (descriptive,
   holdings-based attribution + skill-vs-luck). This half is already designed in depth in
   **[[FUND_MANAGER_ANALYSER_DESIGN]]** and largely shipped (the Fund-Skill cockpit, Active Share,
   attribution to May-2026). **This doc does not re-derive it — it references it.**
2. **CONSTRUCT** — *"Given a category mandate and its constraints, what portfolio would an **expert**
   manager hold right now, by rule?"* (prescriptive — KV's engine **(c): "produce expert fund
   managers"**). **This is the gap KV flagged** and the new content here.

Why the FM persona is **distinct** from its neighbours, in one line each:
- The **Analyst** ([[ANALYST_GOLDMINE]]) supplies a *signal* (revisions turning) — it does not build a
  portfolio or respect a mandate.
- The **CIO** ([[CIO_INTELLIGENCE]]) allocates *across* mandates and flags book/AMC risk — it sits one
  level up.
- The **FM does selection-and-weighting WITHIN a single category mandate**, under that mandate's
  benchmark, active-share, tracking-error, liquidity and turnover constraints. That is a different job
  with different math (Grinold-Kahn's *transfer coefficient* lives here), and it deserves its own engine.

Status: **design blueprint.** The REFLECT half is built/shipping; the CONSTRUCT half is **gated
research** (engine (c)) — nothing prescriptive ships until it clears the §4 gate. Stamped 2026-06-26.
House discipline: historical-data-based, rule-based, **no curve-fit**; every score self-explaining;
unproven ⇒ diagnostic, never an actionable.

---

## 0. The spine — the same identity, run forwards instead of backwards

The analyser ([[FUND_MANAGER_ANALYSER_DESIGN]] §0) rests on the **accounting identity**
`A = R_p − R_b = Σ_i a_i r_i` (active return = active weights · realized returns) — run *backwards* on
history to score a real manager. The **expert-FM synthesiser runs the same identity *forwards*:** choose
active weights `a_i` today to maximise *expected* `Σ_i a_i · E[r_i]`, where `E[r_i]` is built **only** from
the validated, defensible forces, subject to the mandate's constraints. Same equation; one looks back to
*judge*, the other looks forward to *build*.

> **Grinold-Kahn fundamental law:** `IR ≈ IC · √breadth · TC`. An expert manager maximises the
> *information ratio* by (a) having real **IC** (a signal that predicts cross-sectional returns — ours is
> ARM-revision + smart-money-flow confluence, each separately validated), (b) **breadth** (applying it
> across many independent names), and (c) a high **transfer coefficient TC** — converting the signal into
> the portfolio *despite* long-only and constraint frictions. The CONSTRUCT engine is, precisely, a
> constrained maximiser of transferable IC. **TC is where mandates bite** and why the FM persona cannot
> be replaced by the Analyst's raw signal.

---

## 1. REFLECT — the real-manager engine (built; pointer + the persona's use of it)

Full design: **[[FUND_MANAGER_ANALYSER_DESIGN]]**. The headline objects the FM persona consumes:

- **Brinson 3-way attribution** (allocation / selection / interaction, Brinson-Fachler, multi-period
  Cariño-linked, closure < 1 bp/yr) — *where* the active return came from.
- **Skill-vs-luck gate** `t = IR·√years` + tilt-matched bootstrap + OOS persistence + factor survival +
  book-level FDR — *is it real or noise* (with `years_needed = (1.96/IR)²` stamped so "no skill" ≠ "too
  little data").
- **Active Share** (`½Σ|w_i − W_i|`, closet-indexer < 0.6) vs the **reconstructed category benchmark**
  ([[BENCHMARK_PORTFOLIO_PLAN]], `vistas/benchmarks.py`) — guarded (`build_active_share`).
- **Sizing skill** (sign vs magnitude), **trade-timing IC**, **turnover/cost-drag/return-gap**,
  **herding/crowd-alignment** — the manager's fingerprint.

**★ Two honesty fixes already applied to the REFLECT surfaces** (from the scoring-defensibility audit,
[[vistas-scoring-and-cio]] — keep them, do not regress):
- **Crowd-alignment / herding is a persistent STYLE TRAIT, not a forward signal.** Per-fund split-half
  rank-corr ≈ +0.32 (it's a real, stable trait), but forward category-excess IC ≈ **0** (t < 1 at
  3/6/12M). The earlier live claim ("lower herding → +2–3%/yr, Verardo, ρ = −0.10") was a **contemporaneous
  artifact** that reproduced at **+0.12 opposite sign** and was never forward-tested → **removed**.
  Rendered **neutrally** ("trades WITH or AGAINST the consensus" — positioning/diagnostic, not a
  prediction). "Leader" is **refuted** (contrarians' lead score 0.062 < followers' 0.120 — they fade, not
  lead). **The FM CONSTRUCT engine must NOT use anti-consensus as an alpha source** — only as a
  conviction-scaling/diagnostic context (Asness: factor-timing is deceptively hard).
- **Turnover is a STYLE descriptor, not "churn = bad."** On our 18-month panel
  Spearman(turnover, excess) = **+0.24** (higher turnover *coincided* with higher return — opposite of
  the naive view), but it's contemporaneous + regime-specific → shipped as a process descriptor, never an
  edge.

**The FM persona's REFLECT actionables** (#38/#39/#45) — what we add on top of the analyser:
- **#45 (done) — skill reflection:** each manager's quadrant bets vs the **category-one** (the cohort's
  consensus portfolio) and *did they pay off*, time-windowed to the manager's tenure where known.
- **#38 — Analyst×FM quadrant + time-windowed manager isolation:** classify each holding by its Mesh
  (analyst, flow) quadrant; compute **% of the fund's book** in each, and the manager's **flow-adjusted
  additions** per window (reusing `_pair_flows`, corp-action-immune) so an incoming manager is judged on
  *their* moves, not inherited positions ([[MESH_DESIGN]] §4.4).
- **#39 — actionable aggregation:** roll the per-fund posture into "which managers are positioned for the
  turning sectors" — the bridge into the CONSTRUCT engine and the CIO.

---

## 2. CONSTRUCT — the "expert FM" (engine c) — the new content

A **rules-based, mandate-constrained portfolio constructor**: a transparent function that, for a chosen
**category mandate** (e.g. Flexicap, Large-cap, Mid-cap) and its constraints, outputs the active weights an
expert would hold today — **and its full reasoning**, so it is defensible to an investment committee.
It is **not** a learned/black-box optimiser and **not** a return forecaster of NAVs; it is a *deterministic
tilt of the mandate's benchmark toward validated signals under explicit constraints.*

### 2.1 The four ingredients (each independently defensible)

1. **The investable universe & benchmark** = the mandate's reconstructed category benchmark
   (`vistas/benchmarks.py`, EW + FF-mcap weights) — defines `W_i` and the eligibility set (cap band,
   liquidity). The expert FM never strays outside its mandate (that is the CIO's job).
2. **The signal / expected-return proxy** `E[r_i]` = a **z-composite of ONLY validated forces**, each of
   which passed its own signal-backtest:
   - ARM **revision change** (`ΔARM_3M` direction) — the analyst force ([[ANALYST_GOLDMINE]]).
   - **Smart-money flow** confluence (cross-AMC net active flow, size-neutral intensity + breadth change)
     — the `CONVICTION_ADD` confluence ([[MESH_DESIGN]] S1), **only where the flow leads return** (Granger
     gate).
   - **Value × revision** tilt (cheap × improving) — *only if* `VALUE_x_REVISION` (S4) passes on our panel;
     it is **Ambit's number, not ours**, until then.
   - **Quality** as a *defensive screen / risk dampener* (QMJ), not a return source.
   - **★ The composite must beat its strongest single component** in the gate, or it is repackaging — and
     it carries the burned caveat that the equal-weight Mesh blend (S1) **failed** that gate
     (`mesh_backtest.py`): "equal-weight blend < ARM-alone." So the composite's *weights* are themselves
     a research output (likely signal-dominant, not equal), pre-registered and validated, never
     hand-tuned to fit.
3. **The constraints (the mandate)** — the **TC** machinery, all explicit and adjustable:
   - long-only (`a_i ≥ −W_i`), full-investment (`Σw_i = 1`);
   - **tracking-error budget** (so the product stays a *category* fund, not a closet hedge fund);
   - **active-share floor** (don't be a closet indexer) and **per-name / per-sector caps** (concentration,
     SEBI single-issuer limits);
   - **liquidity / capacity caps** (Berk-Green) — position ≤ X days' ADV, so the build respects the ~₹1–2.5k
     cr capacity ceiling noted for momentum-type signals;
   - **turnover budget** (a hold-band / no-trade region so the expert FM doesn't churn — the same `dms`
     spectral-momentum lesson from the ABQ work: smoother is better at equal CAGR).
4. **The construction rule** — `a_i ∝ E[r_i]/σ_i²` (Kelly/Markowitz tilt) **clipped to the constraints**,
   solved as a simple constrained tilt (no exotic optimiser — defensibility favours a transparent
   rule a human can audit over an opaque QP). Output: target weights + the per-name **reason string**
   ("overweight because ARM Δ3M top-quintile AND funds accumulating AND cheap vs sector").

### 2.2 What the expert FM produces

- A **model portfolio** per mandate (target `w_i`, active `a_i`, expected TE, active share, sector
  exposures), refreshed monthly (signals) / weekly (ARM).
- A **reason ledger** per name — the defensible "why" (which validated force, what level/change, what
  horizon).
- A **benchmark-relative posture** ("this mandate's expert is overweight Financials / underweight Staples,
  driven by revision + flow") — the input the CIO aggregates.
- A **counterfactual vs the real managers** (REFLECT): "the expert FM would hold X; manager M holds Y;
  here's where they differ and whether M's deviation has historically paid" — the most useful CIO/investor
  artifact (it turns the abstract model into "is my fund manager doing the expert thing").

### 2.3 Why this is defensible and not curve-fit (the IC test it must pass)

- **Every input is a separately validated force**; the composite is a pre-registered weighting (not
  hand-tuned); the construction rule is a textbook Kelly/Markowitz tilt; the constraints are the mandate's
  real rules. There are **no free parameters fit to maximise backtest return** — the param-plateau lesson
  from ABQ applies (a signal that only works at one knob setting is overfit).
- **The whole engine is backtested as a product** (§4): the model portfolio, run forward on history under
  realistic costs/constraints, must beat its **category benchmark net of fees** AND beat the **strongest
  single signal applied the same way** (else the "expert" adds nothing over buying ARM). It must also be
  **parameter-plateau robust** and **era-stable** (works pre- and post-2018, not just in the momentum
  market). Until it clears this, it ships as a **"reference portfolio — research, unproven"** diagnostic,
  never as advice.

---

## 3. How the FM persona feeds the CIO (the portfolio-of-managers)

The CIO ([[CIO_INTELLIGENCE]]) does **manager selection & combination** and **allocation across
mandates**. The FM persona hands it:

- **Per-manager skill verdicts** (REFLECT, factor-residual α with its luck bar) — the CIO never combines
  raw scorecards; it treats each manager's α-stream as an asset and runs the Markowitz/Grinold team-build
  ([[FUND_MANAGER_ANALYSER_DESIGN]] §5): up-weight **uncorrelated** alpha, down-weight a brilliant *clone*
  of someone already on the team. **Crowding (α-correlation → 1) kills the √M diversification** — this is
  the bridge to the CIO's AMC-systemic-risk lens.
- **Per-mandate expert model portfolios** (CONSTRUCT) — the CIO uses them as the *neutral expert
  benchmark* against which to judge real managers and to assemble a multi-manager book.
- **The manager-vs-expert deviation map** — feeds the CIO's "doable" layer (e.g. "this fund is fighting
  the revision tide in a sector the expert is overweight — flag for review").

**★ Binding gap (carried from the analyser):** person-level skill needs the **manager↔scheme↔dates
tenure DB**, which does **not** exist yet. Everything REFLECT computes is **scheme-level**, so all
manager-level statements are stamped *"scheme-level proxy, manager-change-contaminated."* The tenure
scrape is on the critical path and should run in parallel.

---

## 4. The gate (shared discipline) + logs + resumability

**Gate (nothing prescriptive ships until it passes, on our TR panel):**
1. **Product backtest:** the expert-FM model portfolio run forward on history under realistic
   cost/turnover/liquidity constraints; judged like a fund — CAGR, vol, IR, MaxDD, up/down capture — vs the
   category benchmark **net of fees**, **all-starts × ≥10k random / null comparators**, fixed ≥5y windows,
   **survivorship-free** (dead names + closed schemes retained in research).
2. **Beats the single-signal baseline** (must add over "tilt by ARM alone applied identically") and is
   **parameter-plateau robust** + **era-stable** (the ABQ holdability lesson: a static lever is often just
   a beta dial; verify the edge is *selection*, not market beta).
3. **Factor survival** — the expert FM's edge must survive MKT+SMB+HML+WML+QMJ+BAB (else it's a cheap-ETF
   style the investor shouldn't pay an active fee for).
4. **TC realism** — the long-only/constraint transfer coefficient must be honestly applied; a paper edge
   that needs shorting or 200% turnover is not an "expert *fund* manager."

**Logs & resumability (KV standing requirement):** every phase appends to **`FM_INTELLIGENCE_LOG.md`**
(what was built, the exact gate numbers, the verdict, next step); backtest artifacts to
`data/_fm/_backtests/`; the in-repo `MEMORY.md` resume point updated. Reversible by construction — new
`data/_fm/*` files, reads existing JSONs, no mutation of `analytics.py`/`funds_flows.py` formulas, no
JS-parity port (display-plane).

**Build order:**
- **Phase 1 (now, no new data):** finish REFLECT actionables #38/#39 on the baked Mesh substrate
  (quadrant %-book, flow-adjusted additions, manager-vs-category-one, deviation map). Descriptive — ships
  after probe.
- **Phase 2 (gated):** the CONSTRUCT engine `vistas/fm_construct.py` — universe/benchmark → validated
  composite → constrained tilt → model portfolio + reason ledger → the §4 product backtest. Ship only on
  pass, else as labelled reference-research.
- **Phase 3 (new data):** manager-tenure DB → person-level REFLECT + tenure-isolated CONSTRUCT
  counterfactuals.
- **Phase 4:** hand the per-manager α-streams + per-mandate experts to the CIO team-builder.

---

## 5. FLAGGED — what must not become a silent assumption

1. **CONSTRUCT is engine (c) and is GATED** — no prescriptive "expert portfolio" ships as advice until the
   §4 product backtest passes net of fees and beats the single-signal baseline. Until then: "reference
   portfolio — research, unproven."
2. **Herding / anti-consensus is NOT an alpha input** (forward IC ≈ 0) — only conviction-scaling /
   diagnostic context. Do not let the CONSTRUCT composite quietly load on it.
3. **Turnover is a style descriptor, not edge** — keep the honest label.
4. **Value × revision (~9%) is Ambit's BSE200 number, not ours** — backtest `VALUE_x_ARM` on our panel
   before it enters the composite; ARM ≠ Ambit's revision factor.
5. **The equal-weight Mesh blend FAILED its gate** (`mesh_backtest.py`: blend < ARM-alone) — the composite
   weighting is itself a validated research output, never an equal-weight default and never hand-tuned.
6. **Manager-level = scheme-level proxy until the tenure DB exists** — the binding gap for *person*
   questions; stamp every person-level number.
7. **Returns input must be TOTAL return** (BBG TR / computed NSE TR, delisted names retained) — never the
   price-return panel (it under-credits dividend-payers and re-introduces survivorship bias). Carried from
   the analyser's lead-review correction.
8. **Capacity is real** — momentum-type tilts cap ≈ ₹1–2.5k cr; the constraint set must encode liquidity,
   or the "expert" is un-investable at size.
9. **Benchmark identity is load-bearing** — `A = R_p − R_b` is only as right as `R_b`; use the scheme's
   stated SID benchmark where known, category default otherwise, and run a benchmark-sensitivity check.

*Canonical sources:* Grinold (1989) & Grinold-Kahn (1999, the fundamental law + transfer coefficient);
Clarke-de Silva-Thorley (2002, long-only TC); Markowitz (1952), Kelly (1956); Berk-Green (2004, capacity);
Cremers-Petajisto (2009, Active Share); Asness-Frazzini-Pedersen (2019, QMJ; factor-timing difficulty);
Carhart (1997) & Fama-French (2010) for the factor-survival/FDR gate; plus the project's own
`arm_backtest.py`, `mesh_backtest.py`, the ABQ holdability findings, and [[FUND_MANAGER_ANALYSER_DESIGN]].
