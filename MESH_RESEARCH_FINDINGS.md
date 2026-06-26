# MESH RESEARCH FINDINGS — does any multi-force combination beat ARM-alone?

*Vistas Market-Forces Mesh, Phase-3 validation pass. Research only — no live build, no publish.*
*All numbers are paper information-coefficients on a frozen monthly panel; raw per-stock ARM never written here (methods/aggregates/verdicts only). Convention reproduced and validated: ARM_100_REG level IC@6m = 0.0712, the failed S1 equal-weight blend = 0.0541, ARM beats the blend — all confirmed (`reproduced: true`).*

---

## 0. How to read this doc (every metric defined, in plain words)

Before any verdict, here is exactly what each number means and how it is computed, so you can reproduce it without me.

- **The universe / one observation.** The data is a frozen monthly panel, `mesh_panel.parquet`: one row = one (month-end, stock) cell. Roughly 535 stocks per month over ~157 months (May-2013 to May-2026). Dead/delisted names are **retained** (no survivorship bias — a stock that later got delisted still sits in the months it was alive). Each cell carries the seven "forces" (defined below) plus the stock's **forward total return** over the next 1, 3, 6 and 12 months (`tr_returns_monthly.parquet`).

- **A "force"** = one predictive input, expressed each month as a cross-sectional **z-score** (subtract that month's mean across all stocks, divide by that month's standard deviation), oriented so higher = more bullish. The seven forces:
  - **arm_level** — LSEG StarMine **Analyst Revision Model** score (`ARM_100_REG`, 0–100), forward-filled to month-ends. Plain words: a 0–100 reading of how strongly sell-side analysts are *upgrading* this stock's estimates right now. This is the **incumbent signal we must beat.**
  - **arm_trend_3m** — the *change* in that ARM score over the last 3 months (revision momentum).
  - **flow_intensity_3m** — trailing-3-month net *active* mutual-fund flow into the stock, divided by its end fund-held market value. Plain words: are funds, as a group, putting fresh conviction money in (over and above just riding the price)?
  - **dbreadth** — change in the *number* of fund schemes holding the stock vs last month (more owners = breadth rising).
  - **mom_6m** — trailing 6-month price total return, skipping the most recent month (classic price momentum).
  - **value_z** — a combined cheapness z-score of E/P (earnings yield), B/P (book yield) and S/P (sales yield); higher = cheaper.
  - **quality_z** — return-on-equity minus a Sloan accrual penalty (high-ROE, low-accrual = quality).

- **IC (Information Coefficient), the headline.** *Definition:* the **Spearman rank correlation** between a signal's score this month and each stock's *forward* total return. *Method:* compute it cross-sectionally **each month** (need ≥30 stocks that month, `MIN_XS=30`), then **average** those monthly IC values across all months. *Why:* it answers "when this signal says a stock ranks higher, does it actually tend to earn more over the next h months?" An IC of ~0.05 is a genuinely useful equity signal; ~0.10 is strong. We report IC at horizons h = 1, 3, 6, 12 months; **the repo bar is IC@6m** (ARM's best horizon).

- **t-statistic.** *Definition:* `mean_IC / std_IC × √(n_months)` — how many standard errors the average monthly IC sits above zero. *Why:* `t ≥ 2` means the signal is reliably positive, not a fluke of a few good months. **Caveat stated up front:** forward 6-month returns overlap month to month, so the monthly ICs are serially correlated; a plain t **overstates** significance. Treat large t's as *direction*, not a calibrated p-value (the adversarial pass uses Newey-West to correct this).

- **Decile spread.** *Definition:* sort stocks into 10 buckets by the score each month; take mean(top decile return) − mean(bottom decile return), average over months, then **annualize** by ×(12/h). *Why:* it is the *tradable* long-short version of IC — IC lives in mid-rank ordering, spread lives in the tails. A signal can lift IC without widening the spread (and then a long-short book would *not* capture the gain).

- **"Beats ARM" — the fair test.** A combination usually covers a *different* set of cells than ARM-alone (e.g. only where all 7 forces exist). So "beats ARM" is always judged by **re-scoring ARM on the identical (month, stock) cells the combination covers.** This is why you'll see ARM quoted at different baselines (0.0712 on the 3-force universe, 0.0797 on the 7-force universe, 0.0596 on the value-intersection, etc.) — each is ARM measured on *that combo's own rows*, so the head-to-head is apples-to-apples.

- **Era stability.** Split into four calendar eras — 2013-16, 2017-20, 2021-23, 2024-26 — and require the edge to be **positive in all four**. *Why:* a signal that only works in one regime is a curve-fit, not an edge.

---

## 1. THE VERDICT

**Yes — a genuine, robust, era-stable edge over ARM-alone exists. But it is NOT what the "mesh" name promises, and the honest description of it is mundane: *ARM + price momentum* (optionally + value), simply combined. The "correlation-aware optimal weighting" and "regime-switching" machinery that produced the flashiest headline numbers is, on adversarial scrutiny, decoration that adds essentially nothing.**

### 1.1 What survived every adversarial test (the confirmed edge)

Two constructions were stress-tested to destruction (all-starts rolling-window beat-rate, walk-forward with no look-ahead, OOS halves, jackknife, MIN_XS sweep, sign across horizons and eras, Newey-West t):

| Construction | IC@6m | ARM same-rows | margin | t@6m | pos. all eras | all-starts beat-rate | verdict |
|---|---|---|---|---|---|---|---|
| Orthogonalized stack `z(ARM)+z(resid_mom)+z(resid_flow)+z(resid_breadth)` | **0.1113** | 0.0713 | **+0.040** | 14.3 (NW t=5.2) | yes (.111/.117/.136/.063) | 100% (85/85 rolling 5y, 85/85 expanding) | **REAL — ship as "ARM+momentum"** |
| Correlation-aware optimal `Σ⁻¹·IC` over 7 forces | 0.1205 | 0.0797 | +0.041 | 10.7 | yes (.139/.118/.143/.062) | 100% (82/82); walk-fwd OOS +0.037 | **REAL win, decorative method** |

Both clear the repo bar comfortably: **IC@6m well above ARM, t ≫ 2 even after Newey-West, positive in all four eras, and 100% all-starts beat-rate** (the combination beats ARM-on-the-same-cells in every single rolling-5-year and expanding-start window, with the walk-forward / no-look-ahead version still +0.037 out of sample). This is a real edge, not a period artifact.

### 1.2 The crucial honest decomposition — *where the edge actually comes from*

This is the part KV's "no score for error" standard demands I state loudly:

- **The entire advantage over ARM is the addition of near-orthogonal 6-month price momentum.** Momentum *alone* scores IC@6m ≈ 0.098–0.105, which **already beats ARM (0.080) by itself.** ARM + momentum captures essentially the whole win; flow and breadth residuals add only ~+0.005–0.016 each; value adds a regime-dependent sliver.
- **The clever machinery is not load-bearing.** Orthogonalizing momentum against ARM adds +0.0014 over a naive equal-weight sum (0.1099 → 0.1113). The `Σ⁻¹` "optimal combination" adds only ~+0.02 over a naive equal-weight 7-force blend (0.099 → 0.120), and its specific weights are **unstable** (value/quality flip sign between subsamples) — i.e. the weight vector is partly noise-fit. **Medium overfit risk on the weights; low on the win.**
- **Regime-switching is pure decoration.** A matched *static* 5-force blend (0.1142) actually **beats** the regime-conditional version (0.1112); a random-regime placebo sits at z≈1.97 (borderline inside the noise band); inverting the two recipes barely moves IC. The ~10 hand-set weights + split-point + regime-variable choice buy nothing. **Drop it.**

So the durable, defensible statement is: **two of the most-replicated cross-sectional factors in all of finance — analyst-revision momentum (ARM) and price momentum — are imperfectly correlated, and combining them lifts IC from ~0.08 to ~0.11 (≈21%/yr decile spread). That is exactly what factor theory predicts; the magnitude is plausible, not anomalous.** Everything fancier is narrative.

### 1.3 What did NOT beat ARM as an IC-additive blend (legitimate, important negatives)

- **The original S1 "conviction" blend** `z(flow)+z(dbreadth)+z(dARM_3m)` — IC@6m **0.0541, LOSES to ARM (0.0712).** Reproduced. *Why it failed:* the three forces are each individually weak (IC 0.02–0.05) and barely mutually correlated, so there was no redundancy to exploit and no strong orthogonal factor added — diluting full-strength ARM into a one-third share of an equal-weight sum just weakened it. **Dilution was never the real problem; the forces were simply weaker than ARM.**
- **The ARM × momentum *interaction* (multiplicative)** — IC@6m 0.0574, margin +0.0014, a coin-flip (beats ARM in 50.3% of months, paired t=1.01, p=0.32). **No multiplicative synergy.** Momentum deserves its own additive slot, not an interaction term.
- **The ARM × value *multiplicative corner*** (the "Ambit cheap-AND-upgrading" interaction) — the pure cross-term has a **negative** IC@6m (−0.0069). The value+ARM gain is a plain additive *diversification* (a rank-sum captures it just as well), **not** an interaction.
- **Hard-gated subsets** (keep ARM only where flow>0 AND breadth>0; or only among up-momentum names) — all score *below* ARM on the full universe, because gating halves the monthly cross-section, making IC noisier and compressing the deciles.

### 1.4 The genuinely useful smaller findings (CONFIRMATION-FILTER role, not IC-additive)

These do not beat ARM by a large IC-additive margin, but they are **real, era-stable, and structurally instructive** — and they are exactly the material for differentiated FM brains (Section 2):

- **Soft confirmation tilt** (`ARM_rank + 0.20·confirm_sign`, where confirm = flow>0 & breadth>0): IC@6m 0.0805 vs ARM 0.0709 same-rows, **margin +0.0096**, t=13.1, positive all eras. Thin but real: confirmation nudges ARM's marginal ranks the right way.
- **Graded force-agreement vote-count** (count how many of {ARM-top-tercile, flow>0, breadth>0, value-not-bottom-tercile} agree, 0–4): IC@6m 0.0819 vs ARM 0.0726, **margin +0.0093**, t=16.2, positive 93% of months, all eras. **Key structural lesson:** the *graded count* beats ARM, but the same forces as a *linear z-sum* lose (the S1 failure) — binarising each force into a vote stops one big z from dominating. And the high-conviction basket (≥3 votes) earns +2.66%/6m over the pool and +0.81%/6m over the top-ARM basket (paired t 9.8 / 2.9). **Agreement is a real, modest *confirmation* premium, not a blockbuster IC source.**
- **Quality as an orthogonal nudge** (`z(ARM)+0.5·quality_rank`): IC@6m 0.0631 vs 0.0596, margin +0.0035, smooth concave plateau peaking near w=0.5 (an interior optimum, not a knife-edge), concentrated in the *weakest* era (2024-26, where ARM has decayed). But the **decile spread slips** (13.4%→12.8%) — the gain is rank-IC only, *not tradable as a long-short spread.* Use as a quality-aware modulation, never standalone (quality alone is a weak, sign-flipping 6m signal).
- **Value diversifies ARM** (rank-sum of cheapness + revision): IC@6m 0.0788 vs 0.0598, margin +0.019 — but **regime-dependent**: value *hurts* in 2017-20 (its own IC was −0.04 then). Real but rides the value-strong 2013-16 / 2021-23 regimes.

---

## 2. THE FM LENSES — six genuinely different fund-manager brains

This is the payoff of the negative result. **Because no single linear blend dominates and the forces carry distinct, partly-orthogonal information at distinct clocks, the right use of the mesh is not one "best score" — it is several *different FMs*, each with a real multi-force brain that overweights a different edge and bites the long-only transfer leak in a different place.** That directly answers the critique that every virtual FM is an ARM clone: below, each lens has a different IC engine, a different breadth, and a different transfer leak.

Throughout I tie each to the **Fundamental Law: `IR = IC · √BR · TC`** —
- **IC** = per-bet skill (where the lens's forecast power comes from),
- **BR** = breadth = number of *independent* bets per year (correlated bets shrink effective breadth),
- **TC** = transfer coefficient = the fraction of the paper edge that survives real-world long-only + constraints (≈0.3–0.6 for a long-only book; this is the usual hidden reason a skilled manager looks mediocre).

### Lens 1 — "Revisions-Momentum Core" (the validated workhorse)
- **Rule:** `score = z(ARM_level) + z(mom_6m)`, equal weight, fixed. Optionally + 0.3·z(value). Monthly rebalance, long the top ranks.
- **IC source:** the one confirmed-real edge — two replicated, imperfectly-correlated factors (analyst-revision momentum + price momentum). IC@6m ≈ 0.11.
- **BR:** high — ~500 names × 12 months, but the two forces are ~0.24 correlated, so *effective* breadth is below the raw count; still the broadest of the six lenses.
- **TC leak:** **momentum is high-turnover** → the long-only + transaction-cost leak is the binding constraint here. The +0.04 paper-IC margin will shrink most for this lens once turnover and the long-only cap are imposed. **This is the lens whose deployable IR must be turnover-validated before trusting.**

### Lens 2 — "Conviction-Confirmation FM" (ARM as signal, flow+breadth as a gate)
- **Rule:** base rank on ARM; apply the **soft tilt** `ARM_rank + 0.20·sign(flow_3m>0 & dbreadth>0)`; or trade the **high-agreement basket** (≥3 of 4 votes). Do NOT hard-gate (hard cuts halve breadth and lose).
- **IC source:** ARM, *confirmed* by independent smart-money behaviour — the +0.0093 marginal IC and the +0.81%/6m basket premium over top-ARM. The brain is "I trust analyst upgrades **more** when funds are actually buying and the owner-base is widening."
- **BR:** moderate — confirmation concentrates into fewer high-conviction names (~128/month at ≥3 votes), so breadth is lower than Lens 1 but the bets are higher-quality.
- **TC leak:** flow/breadth are slower-moving than price momentum → **lower turnover, better transfer.** This FM gives up some raw IC for a higher TC — a genuinely different point on the IR frontier.

### Lens 3 — "Value-Revision Contrarian" (cheap AND upgrading)
- **Rule:** `rank-sum(cheapness, ARM)` — long names that are both cheap (high value_z) and being upgraded. (Use the *additive* form; the multiplicative "corner" interaction is dead.)
- **IC source:** value *diversifying* ARM. IC@6m ≈ 0.079, margin +0.019 — but **explicitly regime-conditional** (strong 2013-16 / 2021-23, *negative-contribution* 2017-20).
- **BR:** moderate; value and revision are near-orthogonal, so the two legs add real independent breadth.
- **TC leak:** value is **low-turnover** (best TC of the lenses) but **deep-value names carry liquidity/illiquidity and "value-trap" risk** → the long-only book can't always size the cheapest names, and the leak bites via *capacity*, not turnover. A different transfer problem again.
- **Honest caveat baked into the FM:** this FM should *expect* to underperform in growth-led, low-dispersion regimes (2017-20-type) — that is its identity, not a bug.

### Lens 4 — "Quality-Modulated Revisions" (analyst upgrades, quality-screened)
- **Rule:** `z(ARM) + 0.5·quality_rank` (ROE − accruals). A light additive nudge, not a gate.
- **IC source:** quality as an **orthogonal mid-rank nudge** (corr to ARM ≈ −0.01), concentrated in the *weakest, most recent* era where ARM has decayed — a decay-insurance brain.
- **BR:** similar to Lens 1's universe but the quality nudge re-orders the middle, not the tails.
- **TC leak:** **the gain is rank-IC only — the decile spread shrinks** — so a *long-short* implementation transfers almost none of it (TC≈0 in spread space). This FM only makes sense as a **long-only, full-cross-section weight tilt**, where mid-rank ordering matters. A sharp illustration that *where* IC lives (mid-rank vs tails) decides which implementation can harvest it.

### Lens 5 — "Breadth-Expansion Early-Mover" (the change-not-level FM)
- **Rule:** rank on **rising breadth + accelerating flow** confirmed by a non-falling ARM trend; explicitly trade the *change* (dbreadth>0, flow accelerating) not the *level* of ownership. Fast clock (1q–6M).
- **IC source:** the mesh's organizing principle — **LEVEL is context, CHANGE is the edge, every edge has a clock.** Ownership *level* is a spent/crowding signal; ownership *change* is the forward signal (Chen-Hong-Stein breadth, Yan-Zhang short-horizon institutions). Standalone these are weak (IC 0.04–0.05), so this FM is the *lowest-IC* brain — but the most *differentiated* (it fires on names the others miss, early).
- **BR:** potentially high breadth of *independent* timing bets, but each bet is low-IC → by the Law, low IR unless breadth is genuinely large and de-correlated.
- **TC leak:** earliest-mover names are often **smaller/less liquid** → the transfer leak is **liquidity/impact**, and the fast clock means **turnover** too. The hardest TC of all six — but its de-correlation from Lenses 1–4 is exactly what raises *firm-level* breadth (the CIO benefit).

### Lens 6 — "Multi-Force Agreement / Risk-Off CIO Filter" (the meta-lens)
- **Rule:** the graded **vote-count (0–4)** across ARM, flow, breadth, value — *not* as the primary alpha but as a **conviction sizing and risk overlay**: full size where ≥3 forces agree, half size where 2, avoid where ≤1 even if ARM is high.
- **IC source:** non-linear agreement (vote-count beats the linear z-sum because binarising stops one z dominating). The premium is thin (+0.0093 IC) but **its real value is risk control**: the ≥3-agreement basket has a higher hit-rate (62.8% vs 60.7%) — fewer single-force false positives.
- **BR / TC:** this lens *modifies* breadth and transfer for the *other* lenses rather than generating its own — it is the FM-of-FMs / CIO sizing rule. By trimming low-agreement bets it raises the *effective* IC per held bet (better TC of conviction), at the cost of some breadth.

> **The point, in Fundamental-Law terms:** Lenses 1–6 are *deliberately de-correlated brains* — different IC engines (price-mom / smart-money-confirmation / value / quality / breadth-change / agreement), different clocks (fast flow vs slow value), and different transfer leaks (turnover / capacity / liquidity / spread-vs-mid-rank). A firm running all six gets **firm-level breadth** from running de-correlated desks, which is precisely the `√BR` lever the Law says is where a multi-manager firm's edge compounds — *even though no single lens dominates ARM by a large IC margin.* That is the legitimate answer to "every FM is an ARM clone": they are not, by construction.

---

## 3. CAVEATS, WHAT STAYS UN-SHIPPABLE, AND WHAT'S NEXT

### 3.1 Caveats (state them, don't bury them)
1. **Paper-IC, not tradable IR.** Every number here is forecast IC on a frozen panel. The **transfer coefficient is untested** — long-only + turnover + costs will shrink the +0.04 margin most for the momentum-heavy Lens 1. *Do not quote the paper margin as a deployable edge.*
2. **Overlapping forward returns inflate t-stats.** The Newey-West correction (t≈5.2 for the orthogonal stack) is the honest one; plain t's (14.3) are direction only.
3. **The win is "ARM + momentum," not a novel mesh edge.** Ship it described plainly, or a reviewer mistakes narrative complexity for alpha. The `Σ⁻¹` weights and the regime switch are **fragile/decorative** — use fixed equal weights, not the in-sample optimal vector.
4. **General factor decay.** Both ARM and the combinations weaken in 2024-26 (era IC ~0.06 vs ~0.14 early). The advantage stays positive but smaller — watch live.
5. **Coverage / proxy limits.** Forward-return panel ~70% coverage; mom_6m ~78%; value is best-effort (E/P point-in-time, B/P & S/P annual-lagged); quality has fat tails (rank-transformed for that reason).
6. **Regime-dependence is a feature to disclose, not hide** (esp. Lens 3 value in 2017-20). Each FM's identity *includes* the regime it is built to lose in.

### 3.2 Un-shippable / licensing
- **No raw per-stock ARM table may ever be written to a findings file, the terminal, or any published artifact.** Raw `ARM_100_REG` may be used **in memory** for research only. Everything in this doc is **methods, aggregates, and derived verdicts** — sector/universe aggregates and IC statistics only, never a per-name ARM column. ARM-stale (>90 days) names must be flagged "not recommending," never silently scored.
- This research pass ran **no terminal build, no publish, no `save_terminal_site`** — research only, per the licensing contract.

### 3.3 What's next
1. **Validate transfer for Lens 1** — turnover, long-only cap, and cost drag on `ARM + momentum`; report the *deployable* IR, not the paper IC. This is the gating step before any FM goes live.
2. **Wire the six lenses as six distinct rules-FMs** in the agentic-AMC paper-trading book (each a different `build_rules_v0` recipe), scored honestly by `IR = IC·√BR·TC` and by *de-correlation against each other* (the firm-breadth metric).
3. **Ship the confirmation/agreement layer as a SIZING + RISK overlay** (Lens 6), not as a primary alpha — its proven value is the higher hit-rate / fewer false positives, which is a transfer/conviction benefit.
4. **Re-run live each month** to watch the 2024-26 decay and the value-regime flag; pre-register the expectations so the learning loop is anti-hindsight.

**Bottom line for KV:** ARM is a strong stand-alone signal. *One* multi-force combination genuinely and robustly beats it — but only because it adds price momentum, a separate known factor; the "mesh-specific" weighting and regime tricks are noise. The real product of this research is **not a single better score** — it is **six differentiated FM brains** that each harvest a different force, at a different clock, with a different transfer leak, whose *de-correlation* is the firm-level edge the Fundamental Law tells us to build. No curve-fit; no score for error.
