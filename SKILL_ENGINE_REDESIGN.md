# Skill-Engine Redesign — design spec

> Breadth-maximized, Bayesian fund-manager skill engine: bet-level IC + trade alpha → empirical-Bayes posterior → factor/fee/FDR honesty rails → ranked, calibrated output. Replaces the NAV-IR·√years binary gate (which needs 15-25y of history) so skill is judgeable in ~1-2y and for new funds/NFOs. Drafted 2026-06-30 by a 4-agent grounded design panel (each read the real code). STATUS: spec for KV review — the BUILD is a separate, audited workstream (publish only after a pre-registered before/after audit). Pairs with FUNDAMENTAL_LAW.md, FUND_MANAGER_ANALYSER_DESIGN.md; disciplines: signal-backtest, flag-validation, first-principles-thinking.


## Why (the one-paragraph case)
The current verdict tests the NAV-level active return with `t = IR·√years`. The NAV collapses ~50 positions into one number/month, so its breadth is ~1-3 independent bets/yr → significance needs 15-25 years. That is useless for 3-7yr manager tenures and impossible for NFOs, and it makes 'skilled' too cheap (gross, un-factor-deflated, no multiple-testing correction). The Fundamental Law `t ≈ IC·√(BR·years)` says the cure is BREADTH: measure skill at the holdings cross-section and the trades (hundreds of bets/yr), express it as a calibrated Bayesian posterior, and gate honesty with factor/fee deflation + FDR. Then a real edge declares in ~1-2y, and an NFO gets an honest (wide-error-bar) early read.


---


# Component A — High-Breadth Skill Signals That Converge in Years, Not Decades

## 0. Why this component exists (the problem, in one paragraph)

The current skill verdict in `funds_attribution.scheme_metrics()` is built on the **NAV-level active return** `A(t) = R_p(t) − R_b(t)` — one number per fund per month. The significance test is `t = IR·√years` (line 212), and the Fundamental Law tells us exactly why that is slow: a fund's NAV is **one bet per month** (all its 50 positions move together inside a single number), so its *breadth* is roughly **1–3 independent bets per year**. With `t = IC·√(BR·years)`, a breadth of ~2/yr means you need 15–25 years of track record before luck and skill separate. That is useless for a 3–7 year manager tenure and impossible for a new fund.

The fix is to **measure skill at the level where the breadth lives** — the *cross-section of holdings* and the *trades between snapshots*. A fund holding 47 names (median, measured across 70,748 scheme-months) and trading ~180 positions/year is placing **hundreds of semi-independent bets per year**, not one. Move the measurement there and the same `t = IC·√(BR·years)` law that was a curse becomes a gift: with breadth in the hundreds, a real edge declares itself in **1–2 years**.

This section defines the three high-breadth signals, each with its exact formula, its data source down to the column, its **effective breadth** (how many independent bets/year it actually carries — not the naive count), its **convergence time**, and how to compute it over **any [start, end] window** so it works for a specific manager's tenure.

---

## The unifying identity (everything below is a slice of this)

A fund's edge over its benchmark is an **accounting identity** (FUND_MANAGER_ANALYSER_DESIGN.md §0), not a model:

$$A \;=\; R_p - R_b \;=\; \sum_i a_i\, r_i, \qquad a_i \equiv w_i - W_i$$

- `wᵢ` = the fund's start-of-month weight in stock `i` (we have it: `pct` renormalised over the equity sleeve, exactly as `load_panel()` does at lines 106–107).
- `Wᵢ` = the benchmark's weight in `i` (**we do NOT have point-in-time index constituent weights** — this is the single biggest honest gap, addressed below).
- `aᵢ` = the **active weight** = the manager's actual expressed opinion on `i`. `Σ aᵢ = 0`.
- `rᵢ` = stock `i`'s **total return** next month (`tr_returns_monthly.parquet`, column `ret_1m`).

Every signal below is a *correlation, a sign-test, or an event-study* applied to this one line. The breadth gain is simply that we stop collapsing the `Σ` into one number per month and instead score the **per-name terms** directly.

---

## Signal 1 — Holding-rank Information Coefficient (IC), formalised as a Fama-MacBeth t

### 1.1 What it is, in plain words
Each month, the manager has expressed an opinion on every stock they hold: a bigger weight = a stronger "I like this". The IC asks: **do the names the manager weighted more heavily actually go on to do better?** It is a rank correlation, per month, between *how much they hold* and *what happens next* — then averaged across all months in the window.

*Tiny worked example.* In a month the fund holds 5 names with weights ranked [1,2,3,4,5] (5 = biggest). Next month their returns rank [2,1,4,3,5]. Spearman rank-corr ≈ +0.7 → that month the manager's sizing tracked outcomes well. Average that monthly number over 60 months and you have a skill estimate built from 60 × ~47 = ~2,800 stock-month observations, not 60.

### 1.2 The exact formula (already half-built — `funds_attribution._ic`)

The current code (`load_panel()`, lines 122–127) computes, per scheme-month:
```
IC_t = Spearman( rank(w_i,t) , rank(r_i,t→t+1) )   over the covered held names
```
and `scheme_metrics()` (lines 215–217) reduces it to:
```
ic_mean = mean_t(IC_t)
ic_t    = mean_t(IC_t) / ( std_t(IC_t) / sqrt(T) )     # the Fama-MacBeth t-stat
```
This **is** the Fama-MacBeth procedure: estimate a cross-sectional statistic each period, then test the **time-series mean of those period statistics** against its own time-series standard error. Formalise it as:

$$\overline{IC} = \frac1T\sum_{t=1}^{T} IC_t, \qquad t_{IC} = \frac{\overline{IC}}{\,s_{IC}/\sqrt{T}\,}, \qquad s_{IC} = \text{std}_t(IC_t)$$

over the months `t ∈ [start, end]`. This is **directly windowable**: restrict the `IC_t` series (already stored per-fund in the `ts` array, key `"ic"`, line 298) to the manager's tenure and recompute `t_IC`. No re-fetch needed.

### 1.3 The contamination problem and how to clean it (this is the important part)

The IC as coded correlates **total weight `wᵢ`**, not **active weight `aᵢ = wᵢ − Wᵢ`**, with returns. That makes it a **cap-tilt-contaminated proxy**, and the code already flags this honestly (lines 254, 292–293: *"holding-rank IC is a cap-tilt-contaminated proxy, not pure active-weight selection — needs point-in-time benchmark weights"*). Here is *why* it is contaminated and the cleaning ladder:

**Why contaminated:** a large-cap fund mechanically holds Reliance/HDFC at big weights *because the index does*, not because the manager has a view. If big-cap names happen to outperform that month, the raw `Spearman(w, r)` lights up positive — crediting the manager for a passive cap tilt they had no choice about. The signal we *want* is whether their **deviations from the index** (`aᵢ`) predict returns.

**The cleaning ladder (cheapest → most correct):**

1. **Cross-sectional demean within the fund (cheap, available TODAY).** Replace `wᵢ` with `wᵢ − w̄` (the fund's own mean weight that month) before ranking. This removes the level but not the index-shape tilt. Weak cleaner — use only as a fallback.

2. **Demean against the *peer consensus* (better, available TODAY).** Use the ex-self, AUM-weighted peer book as a *proxy benchmark weight* `Ŵᵢ` — this is **already computed** in `funds_flows.build_active_share()` (the `cons = exagg / extotal` line, ~line 699). Define `âᵢ = wᵢ − Ŵᵢ` and compute `Spearman(â, r)`. This is the cleanest IC we can build **without new data**, because it strips the part of the weight that every peer in the category also holds (most of the cap tilt). Honest caveat: peer consensus ≠ true index, and is itself active, so it slightly over-strips.

3. **True active-weight IC (most correct, NEEDS NEW DATA).** Use real point-in-time benchmark constituent weights `Wᵢ` (NIFTY/index weights by date) → `aᵢ = wᵢ − Wᵢ` → `Spearman(a, r)`. This is the textbook revealed-IC (FUND_MANAGER_ANALYSER_DESIGN §2.1). **Gap: we do not have point-in-time index weights** (flagged as `W-HIST` Phase 3 in the design doc). This is the single highest-value data acquisition for cleaning the IC.

**Recommendation:** ship route 2 (peer-consensus-demeaned IC) as the default cleaned IC now, keep the raw route-as-coded for backward-comparison, and stamp every IC with which route produced it.

### 1.4 Breadth & convergence
- **Naive observations:** `T months × N_held ≈ 60 × 47 ≈ 2,800` stock-months over 5 years.
- **Effective breadth (FUNDAMENTAL_LAW §5):** the held names are **not** independent — they share market beta and sector clusters. With an average pairwise residual correlation `ρ̄ ≈ 0.1–0.2`, `BR_eff ≈ N/(1+(N−1)ρ̄) ≈ 47/(1+46×0.15) ≈ 6` independent bets *per month* → **~70/year**. Still **20–35× the NAV-level breadth** of ~2/yr.
- **Convergence:** with `BR_eff ≈ 70/yr`, a true `IC ≈ 0.04` gives `t = IC·√(BR·years)` clearing 2.0 at roughly **1.5–2.5 years**. Versus 15–25 years at NAV level — a **~10× speedup**.

---

## Signal 2 — Trade-level alpha (the highest-breadth signal): do the ADDS beat the TRIMS?

### 2.1 The idea, in plain words
A holding-IC scores the *stock of opinion* (the weights). But the sharpest expression of skill is the *flow of opinion* — the **trades**: when the manager **increases** a stock's active weight (an "add") versus **decreases** it (a "trim"). If the names they're buying outperform the names they're selling over the next few months, that is selection skill caught at its purest, and there are **hundreds of these decisions per year** (I measured ~180/year for a representative large fund). This is the highest-breadth signal we can build.

**Critical honesty up front (the computable-TODAY boundary):** we only have **month-end snapshots**. We do **not** see intra-month trades. So a "trade" here is *operationally defined* as the **month-over-month change in active weight** — the net position the manager arrived at by month-end. Real fund-flow studies (Chen-Jegadeesh-Wermers 2000) use exactly this snapshot-delta definition; it is the standard, and it is the best the data permits.

### 2.2 The exact trade definition — already built as `net_active` in `funds_flows`

We must NOT use the raw weight change `Δwᵢ = wᵢ(t+1) − wᵢ(t)`, because that confuses the manager's *decision* with the market's *drift* (a stock that simply rose gains weight without any trade). The **drift-adjusted active trade** is exactly what `funds_flows._pair_flows_active()` computes (lines 224–229):

$$w^{\text{drift}}_i = w_i(t)\,\frac{1+r_i}{1+R_p}, \qquad \Delta w^{\text{active}}_i = w_i(t{+}1) - w^{\text{drift}}_i$$

where `R_p = Σ wᵢ(t)·rᵢ` is the do-nothing book drift. `Δw_active` is the genuine reweighting (the code's `dw_active`), and `Σ Δw_active ≈ 0` (the zero-sum audit, line 188). This is **inflow-immune by construction** (a pro-rata cash deployment leaves all weights unchanged → `Δw_active = 0`) and **corporate-action-immune** (total return absorbs splits/bonuses; the merger-bridge handles cross-identity events). **Entries** register as `wᵢ(t)=0 → Δw_active = wᵢ(t+1)` (full add); **exits** as `wᵢ(t+1)=0 → Δw_active = −w_drift` (full cut).

So we already have, per (fund, stock, month), a clean signed conviction-trade. We just have not yet scored it against forward returns.

### 2.3 The two ways to score it (event-study and IC-of-trades)

**(A) Add-vs-Trim spread (event study — the legible headline).**
Each month, split the fund's trades into ADDS (`Δw_active > +τ`) and TRIMS (`Δw_active < −τ`), with a noise floor `τ` (use the existing `_TOL`-style threshold, e.g. 0.2% of book). Over the next `k` months (k = 1, 3, 6, 12 — report the term structure), compute the forward total return of each leg, then the **conviction-weighted spread**:

$$\text{AvT}_t(k) = \frac{\sum_{i\in \text{adds}} |\Delta w^{\text{active}}_i|\, r_{i,t\to t+k}}{\sum_{i\in\text{adds}} |\Delta w^{\text{active}}_i|} \;-\; \frac{\sum_{i\in\text{trims}} |\Delta w^{\text{active}}_i|\, r_{i,t\to t+k}}{\sum_{i\in\text{trims}} |\Delta w^{\text{active}}_i|}$$

A persistently positive `AvT` over the window = the manager's *changes of mind* are informed. Test the time-series of `AvT_t(k)` with a Fama-MacBeth t: `mean(AvT)/(std(AvT)/√T)`. (FUND_MANAGER_ANALYSER_DESIGN §2.2 names this exactly: "trade-timing IC + Add-vs-Trim spread".)

**(B) IC-of-trades (the continuous version, highest breadth).**
Don't bucket — correlate the *signed magnitude* of every trade with its forward return, cross-sectionally, each month:

$$IC^{\text{trade}}_t(k) = \text{Spearman}\big(\,\Delta w^{\text{active}}_{i,t}\,,\; r_{i,\,t\to t+k}\,\big)$$

then the Fama-MacBeth `mean_t / (std_t/√T)` over the window. This uses **every trade** as an observation, so it carries the full breadth.

### 2.4 Why TRIMS need extra care (the long-only truncation — ties straight to the transfer coefficient)
The ADD leg is clean. The TRIM leg is **truncated by long-only**: a manager can only underweight a stock down to zero, so their *bearish* conviction on a name they don't hold is invisible, and a full exit (`Δw_active = −w_drift`) is the strongest negative signal we can ever see. This is precisely the **transfer-coefficient leak** (FUNDAMENTAL_LAW §6): `TC ≈ 0.3–0.6` for long-only books because the negative views are amputated. So the trade-alpha measured here is a **lower bound on the underlying forecasting skill** — a manager with great sell ideas they cannot fully express will look weaker on the trim leg than they truly are. Report this asymmetry; do not "fix" it (it is real economics, not a bug).

### 2.5 Breadth & convergence — the winner
- **Trades/year:** ~150–250 per fund (measured ~180/yr on a representative fund; smaller, more-concentrated funds fewer).
- **Effective breadth:** trades are *more* independent than holdings (a trade is a fresh decision, less anchored to the index than the static book), so `ρ̄` is lower — `BR_eff` per year is plausibly **80–150**.
- **Convergence:** with `BR_eff ≈ 100/yr` and `IC^trade ≈ 0.03–0.05`, `t = IC·√(BR·years)` clears 2.0 in roughly **1–2 years**. This is the **fastest-converging signal in the set** and the one most worth surfacing for short-tenure managers and NFOs.

---

## Signal 3 — Stock-level batting & slugging (per holding-month, partly built)

### 3.1 What they are (plain words)
Borrowed from baseball (KV's MoneyBall framing, already in the code at lines 142–168):
- **Batting average** = *how often* the manager's held stocks beat the benchmark — the **frequency** of being right.
- **Slugging** = *how big* the wins are when right vs the losses when wrong — the **magnitude** of being right.
A skilled manager can win on either; the pair separates a "many small wins" manager from a "few big wins" manager.

### 3.2 The exact formulas (already coded — `port_hit_*` / `port_slug_*`)

Per holding-month observation (one stock, one fund, one month), with `rᵢ` = forward total return and `r_b` = category-benchmark forward return (`load_panel()` lines 158–166):

**Batting (hit rate):**
$$\text{beat}_i = \mathbf 1[\,r_i - r_b \ge 0\,], \quad \text{port\_hit\_cnt} = \overline{\text{beat}_i}, \quad \text{port\_hit\_aum} = \sum_i w^c_i\,\text{beat}_i$$
(`w^c` = weight renormalised over covered names; AUM-version asks "did the PM *overweight* the winners?" — the gap `aum − cnt` is the allocation benefit).

**Slugging (universe-quartile lean):**
$$\text{port\_slug} = \big(\text{book share in top universe-quartile}\big) - \big(\text{book share in bottom quartile}\big)$$
using the month's universe return cut points `u25, u75` (line 151). Count- and AUM-weighted versions both exist.

### 3.3 The two flaws to fix (per the design doc §2.1)
1. **Wrong null.** The median stock *lags* a cap-weighted index, so the no-skill batting baseline is **~0.46–0.49, not 0.50** — the code currently presents the bare rate. Fix: report batting **relative to the empirical no-skill null** (bootstrap random same-size baskets from the eligible universe each month and read the percentile), not against 50%. This is the "luck bar" the design doc demands (§2.1, §3).
2. **Slug rate has look-ahead by construction** (the quartiles are cut from the very returns being scored — line 151 uses the contemporaneous `uq`). The design doc (§2.1) is explicit: **retire slug as a *signal*, keep it as a labelled ex-post *diagnostic*.** Do not let it enter the skill posterior; show it descriptively.

### 3.4 Breadth & convergence
- **Observations:** the full holding-month panel — ~`T × N ≈ 2,800` per fund over 5 years (same substrate as the IC, since both live on the held cross-section).
- **Effective breadth:** same `~70/yr` as Signal 1 (it reads the *same* held names; batting is a coarsened, binary version of the IC, so it carries *less* information per observation than the continuous IC).
- **Convergence:** batting converges **slightly slower than the IC** for the same breadth because binarising `r_i ≥ r_b` throws away magnitude (information loss ≈ the difference between a rank-corr and a sign-test). Practically **2–3 years** to a stable read. Best used as the **legible, communicable** companion to the IC (a "62% batting, slugging 1.4" line is intuitive to a non-quant), not as the primary statistical engine.

---

## 4. Ranking by breadth & convergence speed (the recommendation)

| Rank | Signal | Eff. breadth (bets/yr) | Convergence to t≥2 | Computable today? | Role in the posterior |
|---|---|---|---|---|---|
| **1** | **Trade-level alpha** (Add-vs-Trim spread + IC-of-trades on `Δw_active`) | **~80–150** | **~1–2 yr** | **Yes** — `net_active`/`dw_active` already built in `funds_flows._pair_flows_active`; only the forward-return scoring is new | **Primary fast signal** — the engine for short tenures & NFOs |
| **2** | **Holding-rank IC** (peer-consensus-cleaned `Spearman(â, r)`, Fama-MacBeth t) | **~60–70** | **~1.5–2.5 yr** | **Yes** (raw IC live in `_ic`; cleaning route 2 uses existing `build_active_share` consensus) | **Primary skill estimate** — the well-understood backbone |
| **3** | **Stock batting & slugging** (per holding-month, null-corrected) | **~60–70** (less info/obs) | **~2–3 yr** | **Yes** (`port_hit_*` live); needs empirical-null bar; slug = diagnostic only | **Legible companion** — communicates skill, not the statistical core |
| — | *(reference)* NAV-level IR·√years (current verdict) | **~1–3** | **~15–25 yr** | Yes (live) | The slow gate being replaced |

**Headline:** trade-level alpha is the breadth king and should anchor the fast posterior; the cleaned holding-IC is the trustworthy backbone; batting/slugging is the human-readable face. All three reduce to **per-name observations** scored against **forward total return** via a **Fama-MacBeth t** over **any [start, end] window**, so each is a tenure-windowable input to the calibrated Bayesian skill estimate Component B will build.

---

## 5. How to compute ANY [start, end] window (the windowing contract)

Every signal is a **time-series of monthly cross-sectional statistics** `X_t` (an `IC_t`, an `AvT_t(k)`, a batting rate). The window operation is identical for all three:

1. Slice the monthly series to `t ∈ [start, end]` (the per-fund `ts` array already carries `ic`, and the flow series carry `net_active` per month — so most of this is a filter, not a re-fetch).
2. Recompute the window statistic: `X̄ = mean_t(X_t)`, `s = std_t(X_t)`, `T = #months`.
3. Fama-MacBeth t: `t = X̄ / (s/√T)`; effective-breadth t: `t = IC·√(BR_eff · years)` (cross-check the two — they should agree to within the `ρ̄` estimate; a divergence is itself a diagnostic, FUNDAMENTAL_LAW §5).
4. **Newey-West HAC** the standard error (lag ≈ overlap of the `k`-month forward window) so overlapping-return autocorrelation doesn't inflate `t` ~4× (design doc §3).

This makes each signal a clean likelihood input keyed to a manager's actual tenure — exactly what Component B (the Bayesian posterior with factor/fee/FDR rails) needs.


**Key formulas (quick reference):**

- **Active-return identity (the spine)** — `A = R_p - R_b = Σ_i a_i·r_i,  a_i = w_i - W_i` — A fund's edge = the sum of its active bets (weight minus index weight) times what each stock did. An accounting identity, not a model. Every signal is a slice of this.
- **Holding-rank IC (as coded in _ic) + Fama-MacBeth t** — `IC_t = Spearman(rank(w_i,t), rank(r_i,t→t+1));  t_IC = mean_t(IC_t) / (std_t(IC_t)/√T)` — Each month, correlate how much they hold with what happens next; average those monthly correlations and t-test the average. Already in funds_attribution scheme_metrics as ic_mean/ic_t.
- **Cleaned active-weight IC (the de-contamination)** — `â_i = w_i - Ŵ_i (Ŵ = ex-self AUM-weighted peer consensus from build_active_share);  IC_t = Spearman(â_i, r_i,t→t+1)` — Subtract the weight every peer also holds (most of the passive cap tilt) before correlating, so you score the manager's deviations, not the index they're forced to hold. Best clean available without point-in-time index weights.
- **Drift-adjusted active trade (already = net_active in funds_flows)** — `w_drift_i = w_i(t)·(1+r_i)/(1+R_p);  Δw_active_i = w_i(t+1) - w_drift_i,  R_p = Σ w_i(t)·r_i` — A 'trade' = the weight change AFTER removing the part caused purely by the stock drifting up/down. Inflow- and corporate-action-immune by construction. Σ Δw_active ≈ 0.
- **Add-vs-Trim forward spread (trade event study)** — `AvT_t(k) = wmean_{adds}(r_{i,t→t+k}) - wmean_{trims}(r_{i,t→t+k}), weights = |Δw_active|;  test mean(AvT)/(std/√T)` — Do the names the manager added beat the names they trimmed over the next k months? The purest, highest-breadth read of selection skill.
- **IC-of-trades (continuous, full breadth)** — `IC_trade_t(k) = Spearman(Δw_active_i,t, r_i,t→t+k);  Fama-MacBeth t over the window` — Correlate the signed size of every trade with its forward return. Uses every one of the ~180 trades/yr as a bet — the fastest-converging signal.
- **Stock batting / slugging (port_hit_* / port_slug_*, as coded)** — `beat_i = 1[r_i - r_b ≥ 0]; port_hit_aum = Σ w_i·beat_i; port_slug = (book% in top universe-quartile) - (book% in bottom)` — How often (batting) and how big (slugging) the manager's stocks beat the benchmark. Batting needs the ~0.46-0.49 empirical null, not 0.50; slug has look-ahead so keep it as a diagnostic only.
- **Effective breadth (the real bets/year)** — `BR_eff ≈ N / (1 + (N-1)·ρ̄)  per period, × periods/yr` — Held names aren't independent — shared beta/sectors shrink breadth. 47 names at ρ̄≈0.15 → ~6 independent bets/month ≈ 70/yr, vs ~2/yr for the NAV. This is why holdings converge ~10× faster.
- **The convergence law (why this whole component works)** — `t_skill = IC·√(BR·years) = IR·√years` — Statistical confidence in skill grows with total independent bets = breadth × years. Lift breadth from ~2/yr (NAV) to ~100/yr (trades) and a real edge declares itself in 1-2 years instead of 15-25.

**Feasibility / data gaps:** COMPUTABLE TODAY (no new data): All three signals' core machinery already exists in the codebase. (1) Holding-rank IC is fully live in funds_attribution.load_panel._ic (lines 122-127) and reduced to a Fama-MacBeth t in scheme_metrics (ic_mean/ic_t, lines 215-217); the per-month IC_t series is already persisted per fund (ts array key 'ic', line 298), so windowing to any tenure is a filter, not a recompute. (2) The drift-adjusted active trade (net_active / dw_active) is fully built in funds_flows._pair_flows_active (lines 224-229) — inflow-immune, corporate-action-immune, with the merger-bridge and CA quarantine; what is NEW is only scoring those trades against forward returns (Add-vs-Trim spread and IC-of-trades), which is a thin layer over existing flow + tr_returns data. (3) Batting/slugging (port_hit_*, port_slug_*) is live in load_panel (lines 142-168). All forward returns come from tr_returns_monthly.parquet (ret_1m, TOTAL return, includes delisted tickers — verified, 1575 vst_id), so survivorship is handled. Empirically verified: 47.4 avg equity holdings/scheme-month across 70,748 scheme-months; ~180 drift-adjusted trades/yr on a representative large fund; ~19,600 holding-months of breadth substrate per long-lived fund.

GAPS / NEEDS NEW DATA: (1) TRUE active-weight IC needs point-in-time benchmark CONSTITUENT WEIGHTS W_i (NIFTY/index weights by date) — we do NOT have these (flagged W-HIST / Phase 3 in FUND_MANAGER_ANALYSER_DESIGN; the code itself flags the IC as 'cap-tilt-contaminated' at lines 254, 292-293). Interim clean = peer-consensus weights from build_active_share (route 2 above), which strips most but not all of the cap tilt. This is the single highest-value data acquisition. (2) INTRA-MONTH TRADES are unavailable — we have only month-end snapshots, so a 'trade' is operationally the month-over-month Δw_active. This is the standard fund-flow convention (Chen-Jegadeesh-Wermers) and the best the data permits, but it misses round-trips that open and close within a month. (3) The TRIM leg of trade-alpha is long-only-truncated (the transfer-coefficient leak, ~TC 0.3-0.6) so trade-alpha is a LOWER BOUND on true forecasting skill — report the asymmetry, don't 'fix' it. (4) Empirical NULLs for batting (the ~0.46-0.49 baseline) and the bootstrap luck bars + Newey-West HAC + book-level FDR are specified in the design doc §3 but must be wired in as part of Component B's honesty rails; the breadth ρ̄ for BR_eff needs a residual-covariance estimate (model-dependent, report a range). (5) Everything is SCHEME-level until a manager-tenure DB exists; the window machinery is built to accept tenure dates the moment that DB lands.


<details><summary>Grounded in (code/data the agent read)</summary>

- vistas/funds_attribution.py: load_panel() — builds the (scheme,month) panel; the _ic(d) nested function (lines 122-127) = monthly rank-corr of weight w vs forward return; the port_hit/port_slug block (lines 142-168); scheme_metrics() ic_mean/ic_t (lines 215-217)
- vistas/funds_flows.py: _pair_flows_active() (lines 172-232) — the net_active / dw_active conviction flow (weight-space, inflow-immune); stock_active_flows() (lines 235-305); build_stock_series() (lines 381-489); _prev_ym/_next_ym; the merger-bridge + CA quarantine
- vistas/amc_replay.py: scorecard() Fundamental-Law block (lines 738-758) — ic_mean, ic_t, BR_upper = avg_n*12, transfer_coefficient, implied_IR; _spearman()/_pearson() (lines 189-207); _tc_sample() (lines 463-472)
- FUNDAMENTAL_LAW.md §3.1 (t = IR·√years), §5 (BR_eff = N/(1+(N-1)ρ̄)), §6 (transfer coefficient)
- FUND_MANAGER_ANALYSER_DESIGN.md §2.1 (rank-IC + Fama-MacBeth t as the fix for hit-rate; Add-vs-Trim spread; the look-ahead trap in slug rate), §0 (A = Σ aᵢrᵢ active-weight identity)
- data/funds/history/holdings_history.parquet — 3,815,877 rows, 158 months (2013-04→2026-05), 817 schemes, 1628 vst_id; cols ym, navindia_code, vst_id, nse_symbol, pct, market_value, investment_type, sebi_category
- data/funds/history/tr_returns_monthly.parquet — 285,451 rows, cols date, vst_id, nse_symbol, ret_1m, tr_price (TOTAL return, includes delisted tickers)
- Empirical checks I ran: avg 47.4 equity holdings per scheme-month across 70,748 scheme-months; a representative large fund makes ~15 weight-changes/month (>0.2% move) ≈ 180 trades/yr and accumulates ~19,600 holding-months over its history
</details>


---


# Component B — The Bayesian Posterior Layer

## (turning noisy bet-level skill into a calibrated early read that works from ~1 year)

### 0. What this component is, in one breath

Component A gives us, for each fund, **a noisy point estimate of skill plus how noisy it is** — a number `x̂` (e.g. a trade-level information coefficient, or a holdings-IR) and its standard error `s` (how much that number would wobble if we re-ran history). Component B's only job is to answer the question an allocator actually asks about a 1- or 2-year-old fund:

> *"Given this fund's short, noisy record AND everything we know about how funds like it behave, what is our honest best guess of its true skill, how uncertain are we, and what is the probability it is genuinely skilled — not lucky?"*

The answer is a **posterior distribution**: a best-guess number (`posterior mean`) wrapped in an **error bar** (`credible interval`), from which we read **P(skilled) = P(true skill > threshold | data)**. The machinery is **shrinkage**: pull each fund's noisy estimate toward a **prior** built from its peers, by an amount set by *how trustworthy that fund's own number is*. A brand-new fund is mostly prior with wide bars; a fund with a long record is mostly its own record with tight bars. Nothing is invented — the wide bars on a young fund are the honest statement "we don't know yet," not a hidden bet.

---

### 1. The hierarchical / empirical-Bayes model

#### 1.1 The skill statistic we model (`x`)

We model skill on a **standardized skill axis** so one machine serves every Component-A signal. The natural choice, because the whole North Star is the Fundamental Law, is the **information ratio of the chosen skill signal** — but measured at the *highest available breadth* (holdings/trades), not just NAV:

- **NAV-IR** (today's `info_ratio` in `scheme_metrics`): breadth ≈ 1–3 bets/yr → needs 15–25 yrs. This is the *weak* input.
- **Holdings-IR / trade-IC** (Component A): breadth ≈ (names × rebalances), 100s of bets/yr → the *strong* input.

Component A hands B a tuple per fund **f**: `(x̂_f, s_f, T_f)` where
- `x̂_f` = the point estimate of skill on a common axis (we recommend a **standardized IC**, `IC/SE(IC)`, OR a holdings-IR; the layer is agnostic so long as A reports both the estimate and its SE),
- `s_f` = the **standard error** of `x̂_f` — from Component A's own block-bootstrap (the same machinery as `funds_attribution._block_bootstrap_mean`, which is autocorrelation-honest), NOT the naive `1/√T`,
- `T_f` = effective sample size (months, or breadth×years) — only used as a sanity cross-check on `s_f`.

> **Why the SE must come from A's bootstrap, not `1/√T`.** Monthly active returns and monthly ICs are autocorrelated and fat-tailed. The block bootstrap already in the code (`block=3`, circular) measures the *real* sampling wobble. Using `1/√T` would understate `s_f` and make us over-confident. (This is the same discipline the verdict tree already respects via `boot_p_positive`.)

#### 1.2 The prior hierarchy (universe → category → AMC/style)

A fund is never judged in a vacuum. Its prior is a **nested blend**, each level shrunk into the next so thin levels borrow strength from fat ones (a partial-pooling / James-Stein hierarchy):

```
                 grand universe  μ0, τ0²           (740 funds; the backstop)
                        │  (shrink category means toward μ0)
                 SEBI category    μ_cat, τ_cat²     (e.g. "Mid Cap Fund" — 31 funds)
                        │  (shrink AMC/style means toward the category)
                 AMC × style      μ_amc            (e.g. "HDFC, low-turnover" — handful of funds)
                        │
                 → the fund's PRIOR mean μ_f  and PRIOR variance τ_f²
```

Concretely, the prior mean for fund f is a **precision-weighted ladder** (each level only gets weight in proportion to how many independent peers it has and how tight they are):

```
μ_f = α_amc·m_amc + α_cat·m_cat + α_uni·μ0,   α's ∝ peer-count / spread,  Σα = 1
```

A **Mid Cap fund at a 2-fund boutique** gets ~all its prior from the *category* (the AMC level is too thin to trust); a **flagship at a 12-scheme AMC with a clear house style** gets a meaningful AMC tilt. The prior **variance** `τ_f²` is the *between-fund* variance of true skill *within the tightest level that has enough peers* — this is the number that controls how fast the fund is allowed to leave the prior.

#### 1.3 The shrinkage formula (normal–normal conjugate)

Treat true skill `θ_f` as drawn from the prior `N(μ_f, τ_f²)`, and the estimate as `x̂_f | θ_f ~ N(θ_f, s_f²)`. The posterior is **exactly** normal (conjugacy), with:

> **Posterior mean** = `w_f · x̂_f + (1 − w_f) · μ_f`
> **Shrinkage weight** `w_f = (1/s_f²) / (1/s_f² + 1/τ_f²) = τ_f² / (τ_f² + s_f²)`
> **Posterior variance** = `1 / (1/s_f² + 1/τ_f²) = (s_f²·τ_f²)/(s_f²+τ_f²)`  → **always smaller than both** `s_f²` and `τ_f²`.

`w_f` is the **fraction of the answer that is the manager's own record**: it is the signal precision divided by total precision. Read it in plain words: *"trust the fund's own number only to the extent it is precise relative to how much funds genuinely differ."*

- New fund: `s_f` huge → `w_f → 0` → posterior ≈ prior (μ_f), wide bars.
- Veteran with a real edge: `s_f` small → `w_f → 1` → posterior ≈ its own `x̂_f`, tight bars.

#### 1.4 How the credible interval narrows as months accrue

Since `s_f` shrinks roughly as `1/√(months)` (more precisely as A's bootstrap reports), the **95% credible interval** is

```
posterior mean  ±  1.96 · √(posterior variance)
```

and its half-width falls monotonically from ≈ `1.96·τ_f` (all prior, at month ~0) toward ≈ `1.96·s_f → 0` (as the record lengthens). The bars **start at the width of the peer group's skill spread and contract toward zero** — visibly, every month, which is the honest picture of "we are learning."

---

### 2. The early-read behaviour (the whole point)

Using our **measured universe shape** (IR mean 0.43, true-skill spread set to the category scale, and Component-A SEs that fall with tenure), the posterior for a fund whose *raw* estimate looks strong (IR≈0.80) evolves like this (reproducible — this is the literal output of the formula in §1.3):

| Tenure | signal SE `s` | weight `w` (own record) | posterior mean | posterior SD | 95% CI half-width | **P(true IR > 0.2)** |
|---|---|---|---|---|---|---|
| **12 mo** | 0.50 | **0.26** | 0.53 | 0.26 | ±0.50 | 0.90 |
| 24 mo | 0.35 | 0.42 | 0.59 | 0.23 | ±0.45 | 0.96 |
| **36 mo** | 0.29 | **0.52** | 0.62 | 0.21 | ±0.41 | 0.98 |
| 60 mo | 0.22 | 0.65 | 0.67 | 0.18 | ±0.35 | ~1.00 |
| 120 mo | 0.16 | 0.78 | 0.72 | 0.14 | ±0.28 | ~1.00 |

**Read it:** at **month 12 the answer is 74% prior, 26% the manager** — the posterior barely moved off the peer mean and the bars are *wide* (±0.50). By **year 3 it has crossed over** to be mostly the manager's own bet-level record (w=0.52) with bars a fifth narrower. A young fund therefore **cannot be declared a star on a lucky first year** — the machine won't let it, because `w` is small. But it *can* be flagged "early-promising, wide bars" honestly, which is exactly what an allocator can act on.

> **The output is a probability, not a label.** Instead of the current gated word "skilled," Component B emits, for any threshold the user picks (e.g. *meaningfully skilled* = true IR > 0.2, or *adds gross alpha* = true excess > 0):
> **P(skilled) = P(θ_f > θ\* | data) = Φ( (posterior_mean − θ\*) / posterior_SD )**
> (Φ = standard normal CDF). A 1-year fund might read **P(IR>0.2)=0.90 but with a posterior mean of only 0.53 ± 0.50** — the probability is high *because the prior is favourable and the threshold is low*, and the wide SD makes clear it is provisional. We recommend reporting **both** the probability **and** the posterior mean ± CI so a high P on a wide bar is never mistaken for proof.

---

### 3. Why this is honest and NOT curve-fitting

1. **The wide error bars ARE the feature.** Curve-fitting hides uncertainty to claim a sharp answer. Here uncertainty is the headline: a new fund's posterior is *deliberately* dominated by the prior and carries a *deliberately* wide CI. We never claim to know what we can't know in 12 months — we state a shrunk best-guess and an honest spread.

2. **Nothing is tuned to outcomes.** The prior is **empirical Bayes** — its parameters (`μ0, τ`, category means) are estimated **once, from the cross-section of all 740 funds, with no look-ahead** (the same panel `funds_attribution.load_panel` already builds). There is no free knob fit to make any particular fund look good. The shrinkage weight `w` is a *mechanical* function of the measured SE, not a choice.

3. **It is pre-registered and falsifiable.** Before scoring, we fix: the skill axis, the threshold θ\*, the prior hierarchy, and the SE source (A's bootstrap). Then we **calibrate** it: a posterior is only trustworthy if its probabilities are *calibrated* — of all funds we said "P(skilled)=0.7," ~70% should clear the bar out-of-sample. We test this with **out-of-sample split-half** (estimate the posterior on the first half of each fund's life, check the realized second-half skill lands inside the stated CI the right fraction of the time) — the **flag-validation** discipline applied to an early predictor of a tail outcome.

4. **It ties straight to flag-validation.** Component B *is* an early-warning flag: "P(this young fund is genuinely skilled)." We validate it as a flag, not a NAV backtest — score cross-sections of young funds by their month-12 posterior, then check whether high-posterior funds disproportionately become the future right-tail (top-decile realized skill) and low-posterior funds the left-tail. The honest metric is *predictive validity of the flag*, with an FDR correction across the 740 funds (we are testing many funds at once — the `amc_replay` scorecard already respects multiple-testing in spirit; here it becomes a Benjamini-Hochberg control on the "skilled" claims).

5. **Self-skeptical calibration of `τ` — the one place to be wrong.** Our live decomposition is sobering and we report it openly: at the **NAV-IR level**, observed cross-sectional var(IR)=0.16 is almost entirely estimation noise (E[1/years]=0.158), leaving true-skill var ≈ 0.002 (SD ≈ 0.05); and the **current holding-rank IC** (`_ic()`) has split-half persistence of only **0.097** and universe reliability **~0.7%** — i.e. *today's* bet-level proxy is barely real. **This is not a flaw in Component B; it is its justification.** It quantifies exactly why the NAV verdict is useless early, and it sets an *honest, defensible* prior: until Component A delivers a bet-level signal with measurably higher reliability, `τ` (the room a fund has to leave the prior) must be **small**, so the layer correctly refuses to crown young funds. As Component A's reliability rises (measured on the same split-half test), `τ` is re-estimated upward and the crossover to "the manager's own record" comes sooner — a *data-driven*, not hoped-for, improvement.

---

### 4. Parameter estimates from OUR actual universe (740 funds)

All measured live from `data/funds_attribution/*.json` (equity, non-hybrid, n_months ≥ 24, n = 494):

| Quantity | Value | Source / formula |
|---|---|---|
| Prior mean IR `μ0` | **+0.43** | mean of `info_ratio` across 494 equity funds |
| Observed cross-sec var(IR) | **0.160** | var(info_ratio), ddof=1 |
| Mean estimation var `E[1/years]` | **0.158** | mean of 1/`years` (autocorr-honest SE would be ≥ this) |
| Implied **true-skill var(IR)** | **≈0.002** → SD **≈0.05** | the difference — *very small at NAV level* |
| Category prior means (IR) | Multi Cap **0.76**, Value 0.57, Large Cap 0.49, Flexi 0.47, Small 0.45, ELSS 0.43, Focused 0.40, Mid 0.29 | per-category mean, n ≥ 8 |
| Between-category var of mean-IR | **0.015** | category explains a real slice → the category prior level matters |
| Per-fund mean holding-IC | mean **+0.009**, SD 0.023 | `ic_mean` |
| **Holding-IC split-half persistence** | **0.097** | corr(first-half mean IC, second-half mean IC), n=494 |
| **Active-return A split-half persistence** | **0.014** | same on the IR numerator — essentially zero |
| Monthly IC noise SD (within fund) | **0.186** | median per-fund sd of monthly `ic` |
| ⇒ SE(mean IC) at 12 / 36 / 60 mo | **0.054 / 0.031 / 0.024** | 0.186/√months |

**The honest reading:** the *category* layer of the prior is real and useful (between-cat var 0.015); the *fund-level true skill at the NAV/holding-IC level we have today is tiny* (true-skill SD ≈ 0.05; persistence < 0.10). So Component B, **on today's inputs**, will correctly keep nearly every fund close to its category prior with wide bars — which is the correct, non-curve-fit answer. The lift comes entirely from **Component A raising the bet-level reliability** (active-weight IC + trade IC at true breadth); Component B then automatically lets skill emerge faster, with `τ` re-estimated from the improved split-half reliability. The layer is built so that improvement is *plug-in*: better A ⇒ bigger `w` ⇒ earlier honest verdicts, no re-tuning.


**Key formulas (quick reference):**

- **Shrinkage weight (own-record fraction)** — `w_f = (1/s_f²) / (1/s_f² + 1/τ_f²) = τ_f² / (τ_f² + s_f²)` — How much to trust the fund's own noisy number vs its peers: its precision divided by total precision. New fund (huge s) → w→0 (all prior); long record (tiny s) → w→1 (all its own record).
- **Posterior mean (shrunk best-guess of skill)** — `θ̂_f = w_f · x̂_f + (1 − w_f) · μ_f` — A blend of the manager's measured skill x̂ and the peer-group prior μ. The headline estimate; mostly prior when young, mostly the manager when seasoned.
- **Posterior variance (the error bar)** — `Var(θ_f | data) = 1/(1/s_f² + 1/τ_f²) = (s_f²·τ_f²)/(s_f²+τ_f²); 95% CI = θ̂_f ± 1.96·√Var` — The uncertainty after combining record and prior — always tighter than either alone, and it narrows toward zero (~1/√months) as the track record grows. The wide young-fund bar is the honest 'we don't know yet'.
- **Hierarchical prior mean (partial pooling)** — `μ_f = α_amc·m_amc + α_cat·m_cat + α_uni·μ0,  Σα=1,  α_level ∝ (peers_level / spread_level²)` — Build the fund's prior by blending its AMC/style, its SEBI category, and the whole 740-fund universe — each level weighted by how many trustworthy peers it has. Thin AMCs borrow strength from the category; the category borrows from the universe.
- **P(skilled) — the output** — `P(θ_f > θ* | data) = Φ( (θ̂_f − θ*) / √Var(θ_f|data) )` — The probability the fund's TRUE skill beats a chosen bar θ* (e.g. IR>0.2). Φ is the normal CDF. Reported alongside the posterior mean ± CI so a high probability on a wide bar is never read as proof.
- **Empirical-Bayes prior variance (calibrated, self-skeptical)** — `τ² = Var_cross-section(x̂) − E[s²]  (method-of-moments; the between-fund TRUE-skill variance)` — How much funds genuinely differ in skill = the spread of their estimates MINUS the average estimation noise. We measured ≈0.002 (SD 0.05) at NAV-IR level — small, so the layer correctly keeps young funds near the prior until Component A raises reliability.

**Feasibility / data gaps:** COMPUTABLE TODAY, end-to-end, from existing artifacts — no new data: (1) The prior hierarchy μ0/μ_cat and τ are directly estimable from data/funds_attribution/*.json (I did it live: μ0=0.43 IR, category means span 0.29–0.76, between-cat var 0.015, true-skill var≈0.002). (2) Per-fund SE is already produced by funds_attribution._block_bootstrap_mean (boot_meanA_lo/hi) — convert the bootstrap spread to s_f. (3) The shrinkage, posterior, and P(skilled) are closed-form (normal-normal) — a ~40-line addition to funds_attribution.py that reads scheme_metrics output, no JS-parity port needed (display-plane, like the rest of funds_attribution). (4) Split-half calibration test runs on the existing ts[] blocks (I ran the persistence numbers live: holding-IC 0.097, active-return 0.014).\n\nHONEST GAPS / NEEDS COMPONENT A: (a) The KEY input — a bet-level skill estimate x̂ with HIGH reliability — does NOT exist yet. Today's holding-rank IC (_ic) has universe reliability ~0.7% and split-half persistence 0.097; the NAV-IR has true-skill SD only ~0.05. Component B will run on these but will (correctly) keep nearly all funds at their category prior — it cannot manufacture signal that isn't there. The early-read crossover (w reaching 0.5 by ~year 3) ASSUMES Component A delivers an active-weight/trade IC whose SE falls and whose reliability is materially above 0.7%; until then the SEs are large and w stays low even at long tenure. (b) AMC/style level of the prior needs a fund→manager/house-style tagging (the tenure DB FUNDAMENTAL_LAW.md flags as missing) to be more than a coarse AMC-name grouping; today it degrades gracefully to category-only. (c) Factor-deflation and fee-netting of x̂ (to make skill honest, not gross) are Component A / a factor model's job — B inherits whatever axis A hands it. (d) FDR/multiple-testing control across the 740 'P(skilled)' claims is specified (Benjamini-Hochberg) but must be wired in as a post-pass. None of these block building B now; B is the honest container that gets sharper automatically as A improves, with τ re-estimated from the same split-half reliability test.


<details><summary>Grounded in (code/data the agent read)</summary>

- vistas/funds_attribution.py:scheme_metrics() — produces info_ratio (IR=mean/sd*sqrt(ppy)), t_stat=IR*sqrt(years), ic_mean, ic_t, years (calendar span), tracking_error, and the per-month ts[] block (A, rp, rb, ic, herf, sz)
- vistas/funds_attribution.py:load_panel() and _ic() — the monthly holding-rank IC (rank-corr of weight vs forward return), which I measured to have universe reliability ~0.7% (the cap-tilt-contaminated proxy the code itself flags)
- vistas/funds_attribution.py:_block_bootstrap_mean() — circular block bootstrap giving boot_p_positive, the autocorrelation-honest SE source
- vistas/funds_flows.py:_pair_flows_active()/stock_active_flows()/build_stock_series() — the net-active (inflow-immune, CA-bridged) trade substrate that Component A turns into a trade-level IC
- vistas/amc_replay.py:scorecard() — the IC*sqrt(BR)*TC decomposition (BR_upper, transfer_coefficient, ic_tstat) showing the law-term conventions to mirror
- FUNDAMENTAL_LAW.md §3.1 — t = IR*sqrt(years) and t = IC*sqrt(BR*years); the breadth-times-years engine
- data/funds_attribution/_manifest.json — 740 schemes; verdict mix (152 skilled, 369 ahead-not-significant, 117 insufficient history); 494 equity funds with n_months>=24
- data/funds/history/holdings_history.parquet (158 monthly cross-sections) + data/funds/_amfi_nav_panel.parquet (survivorship-free NAVs) — the panels behind the ts[] series
- Empirical universe shape I measured live: IR mean=0.43, SD=0.40; implied true-skill IR SD~0.05 once 1/years estimation noise is removed; per-fund mean holding-IC split-half corr=0.097; mean active-return A split-half corr=0.014
</details>


---


## Component C — The Three Honesty Rails

The early/high-breadth detector (Components A/B) measures skill at the holdings + trades level, so it fires from ~1 year. That sensitivity is exactly what makes it dangerous: a fund can look "skilled" because it (1) rode a *factor or sector* the market rewarded, (2) charged fees that quietly ate the edge, or (3) was simply one of the *lucky* tails among 740 simultaneous tests. These three rails convert a raw, optimistic skill estimate into a defensible one. Each rail **lowers** the headline skill count, never raises it — that asymmetry is the point ("no score for error").

The current state we are correcting (verified from the live build, `data/funds_attribution/_manifest.json`): **740 funds scored, 152 verdict "skilled", and all 152 are exactly the funds with `t ≥ 2`** on the gross arithmetic active series. The verdict string itself flags the holes — `funds_attribution.py:scheme_metrics()` line ~291 literally says the basis is *"GROSS holdings-implied total-return excess … pre-cost, pre-cash-drag, pre-factor-deflation"*. The three rails close those three named holes, in order.

---

### RAIL 1 — Factor & Sector Deflation

**Plain idea.** A fund's monthly active return `A(t) = rp(t) − rb(t)` (its return minus the category benchmark, already computed in `load_panel()`) is not pure stock-picking. Some of it is a persistent *style tilt* — a small-cap lean, a value lean, a momentum lean, a quality lean — and for sector/thematic funds, a *sector* lean. The market pays (or punishes) those tilts regardless of any skill. We must not pay the manager a skill score for owning a factor. So we **regress active return on the factors the fund was exposed to, and read skill off the leftover (the residual) intercept**, not off raw `A`.

**The regression (per fund).**
```
A(t) = α + Σ_k β_k · F_k(t) + ε(t)
```
- `A(t)` = the fund's monthly active return (existing `panel["A"]`).
- `F_k(t)` = the month-`t` return of factor `k` — a long-short "factor leg" (defined below). Already excess-of-cash by construction (long minus short), so no separate rf needed.
- For a **sectoral/thematic** fund, we *add one more regressor*: `S(t)` = the active return of its **sector TR index vs the broad benchmark** (`sector_TR − NIFTY 500`), reusing `amc_replay._bench_for()` / `_THEME_BENCHMARK` which already maps "pharma"→NIFTY HEALTHCARE, "bank"→NIFTY FINANCIAL SERVICES, etc. This strips the sector beta that `funds_attribution` currently leaves in (it benchmarks thematic funds against NIFTY 500, so a pharma fund's whole sector run shows up as fake "alpha" — the verdict already half-admits this with the `_them` suffix *"largely a sector bet, not pure selection"*, but does not *remove* it).
- `α` = the **monthly factor-and-sector-residual alpha** = the part of active return *not* explained by tilts.

**Skill is then the t-stat on α**, computed Newey-West (lag 3, to handle the monthly autocorrelation the existing block-bootstrap already worries about):
```
skill_t = α_hat / SE_NW(α_hat)          (the rail's verdict statistic)
```
This *replaces* the raw `t = IR·√years` as the headline skill test. The raw IR/excess stay reported (for continuity and for the IC·√BR·TC lens), but the **verdict** keys off `skill_t`.

**Factor leg construction — from OUR data, honestly.** We build long-short legs cross-sectionally over the stock universe each month, exactly in the style of `arm_backtest.py:run()` (monthly cross-section → sort → top-minus-bottom). The substrate is the 4308-stock daily TR panel (`data/Stocks Data TR till *.csv`, resampled month-end) joined to `data/fundamentals_annual_consolidated.csv` (1986 syms, FY2000-2026, ~97% non-null networth/pat). Each leg = **mean forward TR of the top tercile − mean forward TR of the bottom tercile**, of the characteristic, rebalanced monthly, cap-/liquidity-screened (reuse `amc_replay` MIN_TURN_CR / MIN_PRICE gates so penny/illiquid noise can't define a leg):

| Factor | Characteristic (sort key) | Built from | Honesty note |
|---|---|---|---|
| **Size (SMB)** | market cap proxy = trailing-median traded value rank (`amc_replay._turn_med`) | stock TR panel + bhav turnover | We have NO clean point-in-time market-cap series; turnover-rank is the same size proxy `amc_replay` already uses for buckets. Adequate, not perfect — flag it. |
| **Value (HML)** | earnings yield = `pat / market_cap` and book yield = `networth / market_cap` | fundamentals CSV ⋈ price | `pat`/`networth` are annual, lagged 4 months (publish lag) to avoid look-ahead. Coverage ~1986 syms — good. |
| **Momentum (WML)** | trailing 12-1 month TR (skip the most recent month) | stock TR panel only | Cleanest leg — pure price, full universe, no fundamentals dependency. |
| **Quality (QMJ-lite)** | ROE = `pat/networth`, low leverage = `−total_debt/total_assets`, accruals-light | fundamentals CSV | Composite z-score of the three; same lag treatment as value. Honest proxy for Asness QMJ (no gross-profitability granularity). |

**Fund factor exposure — two routes, cross-checked.**
- *(a) Return-based* (the regression above): `β_k` = OLS slope of `A` on `F_k`. Needs ≥24 fund-months; works for any fund, including ones whose holdings we don't fully see.
- *(b) Holdings-based* (independent check): each month compute the AUM-weighted average characteristic z-score of the fund's actual book (`holdings_history.parquet` ⋈ characteristic), giving a *measured* tilt. Route (b) is look-through truth; route (a) is statistical inference. **The gap between (a) and (b) is itself a diagnostic** (same philosophy `FUNDAMENTAL_LAW.md §7` applies to the two TC routes). If a fund's return-based value-β disagrees with its holdings-based value-tilt, the factor model is mis-specified for it → flag, don't trust the deflated α.

**What it does to the numbers.** Raw excess for the typical Indian diversified equity fund 2013-2026 is roughly +1 to +3%/yr, much of it a small/mid-cap and momentum tilt during a multi-cap bull run. Factor-deflation typically **removes 40-70% of apparent excess** in tilt-heavy funds and **a much larger share for thematic funds** (the sector regressor alone can take a pharma/PSU fund from "skilled" to "sector-rider"). Expect the deflated-α t-stat to fall below 2 for a large fraction of the 152 — this rail alone is the single biggest cut. Funds whose α *survives* deflation are the ones doing genuine within-style selection.

**Honest gaps (stated, not hidden).** (i) Size uses turnover-rank not true float mcap. (ii) Value/quality use annual fundamentals lagged — no quarterly granularity, so the value signal is coarse. (iii) Our legs are *long-short academic legs*, but the funds are *long-only* — the regression still validly removes the part of `A` that co-moves with the leg, which is what we want; it does not claim the fund could have shorted. (iv) We do not (yet) orthogonalize the legs to each other; report the four βs with their correlation matrix so a collinear-leg artifact is visible.

---

### RAIL 2 — Net-of-Fee

**Plain idea.** The 152 "skilled" funds are scored **gross** (the holdings-implied return charges no expense). Indian regular-plan equity funds charge ~1.5-2.25%/yr; direct plans ~0.5-1.2%/yr. A fund that beats its benchmark by +1.5%/yr gross with a 1.8% expense ratio is, net to the investor, **behind**. We must report net skill alongside gross so a "skilled" verdict cannot be an artifact of pre-fee accounting.

**The method.**
```
A_net(t) = A(t) − (annual_TER / 12)        (monthly expense drag)
```
applied to the *same* active series before the IR / t-stat / bootstrap, and fed *into Rail 1's regression as the dependent variable* when a TER is available (so the net α is also factor-deflated — the two rails compose). Report **both** gross and net excess, gross and net `skill_t`, and the verdict keys off **net** where TER exists.

**THE DATA GAP — stated plainly.** **We do not currently have expense ratios.** I checked `data/funds/scheme_master.json` and `data/funds/_amfi_census.json`: their scheme records carry `name / isin / category / fund_house` but **no TER field**. So Rail 2 is *specified but not yet computable* until we ingest one of:
- **SEBI / AMFI monthly TER disclosure** — every AMC must publish scheme-level TER daily/monthly on its website and AMFI aggregates it. This is the authoritative source (the `data-cleaning` provenance hierarchy: official > vendor). It is a new feed (`vistas/funds_ter.py`, same graceful-degrade contract as `macro.py`).
- **Pragmatic interim proxy** (so the rail isn't a no-op meanwhile): apply a **category-median TER haircut** from the published SEBI ranges (e.g. direct-plan equity ≈ 0.8%/yr, regular ≈ 1.9%/yr; small-cap higher than large-cap), keyed by `sebi_category` + plan type parsed from the scheme name (`_norm_name` already strips DIRECT/REGULAR — we'd instead *detect* it). This is a **flagged approximation**, reported as `net_basis="category-median-proxy"` so no one mistakes it for the fund's real TER.

**What it does to the numbers.** A flat ~1.9%/yr (regular) haircut moves the entire excess distribution left by ~1.9%/yr; many "ahead but not yet significant" funds become "lagging," and a meaningful slice of the gross-"skilled" set drops below significance net-of-fee. Direct-plan funds (~0.8%) are hit less. **Crucially this also re-ranks: a high-gross-α / high-fee fund loses to a modest-gross-α / low-fee fund**, which is the economically correct ranking for an allocator. We report a `fee_drag_pct` per fund so the investor sees how much of the gross edge the fee consumed.

---

### RAIL 3 — FDR / Multiple-Testing

**Plain idea.** We run **740 simultaneous skill tests**. Even if *every* fund had zero true skill, sampling noise alone makes ~2.5% of them clear `t ≥ 2` on the *upside* by luck (one-tailed 5% → ~2.5% each tail). 2.5% of 740 ≈ **18-19 funds "skilled" by pure luck**, with *no* real edge. A naive "152 skilled" headline therefore contains an unknown number of false discoveries. Two complementary corrections fix this.

#### 3a. Barras–Scaillet–Wermers (BSW 2010) — decompose the whole population.
BSW models the 740 t-stats (or p-values) as a mixture of three populations and **estimates the proportions** rather than judging funds one at a time:
- `π0` = fraction of **zero-alpha** funds (no skill),
- `π+` = fraction **truly skilled** (positive α),
- `π−` = fraction **truly unskilled** (negative α).

**Mechanics on our data.** Take the per-fund **net, factor-deflated** α and its Newey-West p-value (the output of Rails 1+2). Estimate `π0` from the *centre* of the p-value histogram: under the null, p-values are Uniform[0,1], so the density of p-values near the uninformative middle/high region (say p > λ, λ≈0.5-0.6) estimates `π0` directly (Storey's `π0 = #{p_i > λ} / ((1−λ)·M)`). The funds in the significant tails beyond what `π0` predicts are the *true* discoveries; the rest of the tail count is **"lucky"**. This yields the exact reframing the brief asks for:

> **"152 gross-significant → of these, ~`π0·(tail rate)·740` are lucky-good; only the excess over the null is truly skilled."**

Concretely, if BSW estimates `π0 ≈ 0.80`, `π+ ≈ 0.06`, `π− ≈ 0.14` on the **net-deflated** αs (a plausible Indian-equity result, in line with the US literature where `π+` is low single digits), then of ~740 funds only **~0.06·740 ≈ 40-45 are truly skilled in population terms**, and among any *t≥2 shortlist* a large minority are lucky-good. BSW separately tells you the **lucky-bad** funds — unskilled funds that nonetheless cleared the bar — so you don't over-fire the bottom either.

#### 3b. Benjamini–Hochberg FDR — control luck in the *shortlist we publish*.
BSW describes the population; BH controls the **specific list of names we call "skilled."** Sort the 740 net-deflated one-tailed p-values ascending `p_(1) ≤ … ≤ p_(M)`; for a target false-discovery rate `q` (we set **q = 0.10** — at most 10% of the published list is luck), find the largest `k` with `p_(k) ≤ (k/M)·q`; call funds `1…k` skilled. This is *adaptive*: it's strict when few funds are significant, lenient when many are — and it guarantees the *expected* fraction of false names in the list is ≤ q. Optionally use the **BSW `π0`** to sharpen BH (Storey's q-value uses `π0·k/M·q`), recovering power lost to the conservative assumption that all nulls are true.

**What it does to the headline (the reframing).** The gross, un-deflated, un-corrected headline is **152 "skilled" / 740 = 20.5%**. After the rails compose:
1. Rail 1 (factor+sector deflation) knocks out the tilt-riders → the t≥2 set shrinks substantially (often to ~half).
2. Rail 2 (net-of-fee) shifts the survivors left → a further trim.
3. Rail 3 (BH at q=0.10 on the net-deflated p-values) removes the residual luck tail.

**Expected post-rail "skilled" rate: low single digits — on the order of ~3-7% of equity funds (~25-50 names), with a hard statement that ≤10% of *those* are still luck.** This is the honest number an allocator can act on, and it matches the literature's prior (`π+` is small everywhere; genuine persistent fund skill is rare). The verdict string is rewritten from *"+X%/yr gross, t=…"* to *"net +X%/yr after fees, factor-residual t=…, survives BH-FDR(q=0.10); BSW population π+≈…"* — every word of which is reproducible from the formulas above.

**Honesty rails on the honesty rail.** (i) BH/BSW assume the per-fund p-values are valid — so the Newey-West SE and the ≥24-month minimum from Rail 1 are prerequisites, not optional. (ii) Overlapping benchmarks make fund tests *correlated* (not the independence BH assumes); BH is robust to positive dependence (Benjamini-Yekutieli is the conservative fallback if we want to be strict) — flag this. (iii) FDR controls the *list*, not any single fund; a fund "in the list" still carries its own `t` and CI, never a bare label.

---

### How the three rails compose (one pipeline)

```
raw active A(t)  ──Rail2──►  A_net(t) = A − TER/12
                 ──Rail1──►  A_net = α + Σβ_k F_k + (β_S·S for thematic) + ε
                              skill_t = α_hat / SE_NW(α_hat)        [per fund]
all 740 skill p-values ──Rail3──► BSW(π0,π+,π−) population view
                              + BH(q=0.10) published "skilled" list
```
Order matters only in that fee and factor deflation both act on the *series* and can be applied together (fee is a constant shift, deflation a regression); FDR acts on the *cross-section of the resulting p-values* and must come last. The existing IC·√BR·TC scorecard (`amc_replay.scorecard`) is **untouched** and runs alongside as the breadth/transfer lens — the rails govern the *significance verdict*, the law governs the *interpretation* of where realized IR leaks.


**Key formulas (quick reference):**

- **Factor+sector deflation regression** — `A(t) = α + Σ_k β_k·F_k(t) + β_S·S(t) + ε(t);  skill_t = α̂ / SE_NW(α̂)` — Regress the fund's monthly active return on style-factor legs (and, for thematic funds, its sector-vs-broad index S). Skill is the t-stat on the leftover intercept α — the part NOT explained by tilts — using Newey-West standard errors for monthly autocorrelation.
- **Factor leg (long-short)** — `F_k(t) = mean_fwdTR(top tercile by characteristic_k) − mean_fwdTR(bottom tercile)` — Each month, sort the liquid stock universe by the factor's characteristic (size=turnover-rank, value=pat/mcap & networth/mcap, momentum=12-1m return, quality=ROE+low-leverage z), and take top-third return minus bottom-third return — the same monthly-cross-section recipe arm_backtest.py already uses.
- **Net-of-fee active return** — `A_net(t) = A(t) − annual_TER/12` — Subtract one-twelfth of the annual expense ratio each month before computing IR/t-stat, so skill is measured net of what the investor actually pays. (TER is not yet in our data — needs a SEBI/AMFI feed or a flagged category-median proxy.)
- **BSW zero-alpha proportion (Storey estimator)** — `π̂0 = #{ p_i > λ } / ( (1−λ)·M ),  λ≈0.5–0.6;  π+ , π− from the tails beyond π0` — Estimate the fraction of funds with NO skill from the flat middle of the p-value histogram (true nulls are uniform). What's left in the significant tails beyond that flat baseline is the genuinely skilled (π+) or genuinely unskilled (π−) population — so '152 significant' splits into truly-skilled vs lucky-good.
- **Benjamini-Hochberg FDR cutoff** — `skilled = { (i) : i ≤ k* },  k* = max{ k : p_(k) ≤ (k/M)·q },  q = 0.10  (M = 740)` — Sort all 740 one-tailed p-values ascending; keep the largest prefix whose k-th p-value stays under (k/740)·0.10. This guarantees at most ~10% of the published 'skilled' list is luck — adaptive: strict when few clear, lenient when many do.
- **Naive false-positive count (the problem)** — `E[false 'skilled'] ≈ α_1tail · M = 0.025 · 740 ≈ 18–19` — Running 740 tests at a one-tailed 2.5% bar, ~18-19 funds clear t≥2 by pure luck even if none has real skill — so the raw 152 'skilled' must be deflated and FDR-corrected before it means anything.

**Feasibility / data gaps:** COMPUTABLE TODAY from our data: (1) FACTOR DEFLATION is fully buildable now — the 4308-stock daily TR panel + data/fundamentals_annual_consolidated.csv (1986 syms, FY2000-2026, ~97% non-null pat/networth) give value/momentum/quality legs, and turnover-rank gives a size proxy; the monthly active series A(t) already exists in funds_attribution.load_panel(); the per-fund regression + Newey-West t is a ~1-day add. (2) SECTOR DEFLATION for thematic funds is essentially free — amc_replay._THEME_BENCHMARK / _bench_for() already map scheme-name keywords to NSE sector TR indices present in data.available(); we just add (sector_TR − NIFTY500) as a regressor. (3) FDR/BSW is pure post-processing on the 740 per-fund p-values — no new data, ~half a day (BH is trivial; BSW π0 is the Storey estimator). NEEDS NEW DATA — the one hard gap: (4) NET-OF-FEE — we have NO expense ratios. Confirmed: data/funds/scheme_master.json and _amfi_census.json carry name/isin/category/fund_house but no TER. So Rail 2 is specified but blocked until we ingest SEBI/AMFI scheme-level TER (a new graceful-degrade feed, vistas/funds_ter.py) OR run it on a flagged category-median-TER proxy in the interim. HONEST CAVEATS on the computable rails: size leg uses turnover-rank not true float market cap (no clean PIT mcap series); value/quality use annual (not quarterly) fundamentals, publish-lagged 4mo to avoid look-ahead — coarse but valid; factor legs are long-short academic legs regressed against long-only funds (removes co-movement, does not claim shortability); BH assumes valid independent-ish p-values (overlapping benchmarks induce positive dependence — BH is robust, Benjamini-Yekutieli is the strict fallback). Net effect, all rails composed: the gross 152/740 = 20.5% 'skilled' is expected to fall to low single digits (~3-7%, ~25-50 funds) with ≤10% of those still luck — the defensible, allocator-actionable number.


<details><summary>Grounded in (code/data the agent read)</summary>

- vistas/funds_attribution.py:scheme_metrics() — the verdict tree, mA/sA→IR, t=IR·√years, _ic() holding-rank IC, sizing edge, p_pos bootstrap, the 152/740 'skilled' headline
- vistas/funds_attribution.py:load_panel() — the (fund,month) active-return panel A=rp-rb, per-month equity weights w, _CAT_BENCH category benchmark map, fwd TR returns
- vistas/funds_attribution.py:build_all() — writes per-scheme JSON + _manifest.json (the 740-fund leaderboard whose verdicts the rails reframe)
- vistas/amc_replay.py:_THEME_BENCHMARK + _bench_for() + theme_sectors_for() — the EXISTING sector-TR-index map to reuse for sectoral/thematic deflation
- vistas/amc_replay.py:_tc_sample()/scorecard() — the existing TC and IC·√BR·TC decomposition convention
- vistas/funds_flows.py:_pair_flows_active()/stock_active_flows() — net-active trade substrate (the highest-breadth bet level)
- data/fundamentals_annual_consolidated.csv — sym,fy,sales,pat,networth,total_assets,total_debt,capex (1986 syms, FY2000-2026; ~97% non-null networth/pat) → the ONLY in-house source for value/quality factor legs
- data/Stocks Data TR till *.csv — 4308-stock daily TR panel → momentum leg + monthly stock returns for factor legs
- data/funds/history/holdings_history.parquet — 158 monthly cross-sections, 817 funds, vst_id-keyed → fund factor exposures via look-through
- data/funds/_amfi_census.json + scheme_master.json — scheme records carry name/isin/category but NO expense ratio (confirms the fee gap)
- FUNDAMENTAL_LAW.md §7/§9 — the law is an upper bound; IC is revealed-not-true; benchmark identity is load-bearing; control FDR across the 777/740
- vistas/arm_backtest.py:run() — the in-house cross-sectional Spearman-IC / decile-spread harness pattern to reuse for factor-leg construction
</details>


---


## Component D — The Output Schema, the Pre-Registered Validation, and the Before/After Audit & Rollout

This component turns the new high-breadth Bayesian skill estimate (Components A–C: the holdings-cross-section IC and the trade-alpha, expressed as a calibrated posterior with factor/fee/FDR rails) into (1) a concrete **per-fund output schema** that replaces the cheap binary verdict, (2) the **pre-registered out-of-sample test** that proves the early posterior actually predicts future active return (the only thing that makes the new metric "real, not curve-fit"), and (3) the **before/after audit and rollout plan** with honest risks.

Everything here is designed to be a *drop-in* on the existing plumbing: `funds_attribution.build_all()` already writes one JSON per fund + a `_manifest.json` + `_envelopes.json` that the deck lazy-fetches, and `static/vistas.js` already recomputes the same fields in `fsComputeWindow`. We ADD fields; we do not break the `ts` series or the existing keys (so the offline single-file deck and the digital-AMC consumer keep working during migration).

---

### D.1 The new per-fund output schema (`skill` block)

**The principle (Feynman first).** Today every fund gets ONE word — `verdict ∈ {skilled, ahead but not yet significant, lagging, ...}`. That word is a hard threshold on a noisy estimate: a fund at t=1.99 is "ahead but not yet significant", a fund at t=2.01 is "skilled", and yet their *true* skill is essentially identical. A threshold on a noisy number throws away the most important thing — *how uncertain we are*. The fix is to report skill the way a thermometer reports temperature: a **best estimate plus an error bar**, plus the *probability* the true value is above zero. That is a **posterior distribution**: a belief about the fund's true skill, after combining (a) what this fund's own bets showed and (b) what funds in general look like (the prior). A new NFO with 14 months of data gets a wide bar centred near the category prior; a 12-year fund with a strong holdings-IC gets a tight bar far from zero. Same scale, works from year one.

**Worked example.** Fund X, Mid Cap, 3.5 years live. Holdings-cross-section IC (Component A) over ~42 monthly cross-sections of ~55 names each ⇒ ~2,300 bet-observations ⇒ a noisy-but-real per-bet IC estimate of 0.018 with standard error 0.010. Shrink toward the Mid-Cap prior (mean ≈ 0.006, the category's pooled IC) ⇒ posterior IC ≈ 0.012 ± 0.008. Map IC→annual active-return via the Fundamental Law atom (`α ≈ TC·IC·√BR_eff·ω_active`, Component B's calibrated mapping): best estimate **+1.4%/yr net**, 90% credible interval **[−0.9%, +3.6%]**, **P(skilled = true net active >0) = 0.74**. Decile rank within Mid-Cap = **8th** (top-third). The slow NAV-IR corroborator over the same 3.5y is +0.4 (t=0.75 — uninformative, as expected at this tenure). The card shows: *"Likely skilled (74%), +1.4%/yr est., wide band — early read; bet-level evidence positive, NAV not yet confirming."* No false "skilled" stamp; no false "not significant" dismissal.

**The JSON schema.** Each `data/funds_attribution/<navindia_code>.json` gains a top-level `skill` object (the legacy flat keys `excess_cagr / info_ratio / t_stat / ic_t / verdict / verdict_why` are **retained** for back-compat, but `verdict` is re-derived from the posterior — see D.1.3):

```jsonc
{
  // ... existing keys (scheme_name, amc, sebi_category, benchmark, n_months, years, ts:[...], portfolio, crowd_flow) ...

  "skill": {
    "schema_version": 2,                  // bump so consumers can branch; v1 = legacy binary only

    // ---- 1) THE POSTERIOR (the headline; net-of-fee, factor-deflated annual active return) ----
    "posterior": {
      "metric": "net_active_cagr",        // what the estimate is OF: net-of-fee, factor-adjusted ann. active return
      "best": 0.014,                      // posterior MEAN (the point estimate), decimal/yr (1.4%)
      "lo90": -0.009, "hi90": 0.036,      // 90% credible interval (5th/95th posterior pctile)
      "lo50": 0.004, "hi50": 0.024,       // 50% interval (the "likely" band) for a tighter bar in the UI
      "sd": 0.014,                        // posterior std dev (the error bar half-width proxy)
      "p_skilled": 0.74,                  // P(true net active > 0)  ← the calibrated probability of skill
      "p_strong": 0.28,                   // P(true net active > +2%/yr) — "materially skilled", a higher bar
      "prior_mean": 0.006, "prior_sd": 0.015,  // the category prior the estimate was shrunk toward (auditable)
      "shrinkage": 0.41,                  // weight on the DATA vs the prior (0=all prior, 1=all data) = n_eff/(n_eff+n0)
      "basis": "net-of-fee (TER subtracted), factor-deflated (size/value/mom/quality), FDR-aware"
    },

    // ---- 2) THE TAG (replaces the binary; a 4-state honest label driven by the posterior) ----
    "tag": "likely_skilled",             // one of the 5 tags below (D.1.3)
    "tag_label": "Likely skilled",
    "tag_why": "P(skilled)=74%, +1.4%/yr est. (90% CI −0.9%…+3.6%); early read — NAV not yet confirming",

    // ---- 3) THE RANK (where it sits among its peers; what an allocator actually asks) ----
    "rank": {
      "basis": "p_skilled",              // ranked on P(skilled); ties broken by posterior.best
      "within": "Mid Cap Fund",          // the peer set (SEBI category)
      "n_peers": 28,
      "decile": 8,                       // 10 = best decile; computed cross-sectionally each build
      "pctile": 76                       // 0–100 percentile within category on the rank basis
    },

    // ---- 4) THE BET-LEVEL COMPONENT (Component A — the high-breadth skill source) ----
    "bet_level": {
      "ic": 0.012,                       // posterior holdings-cross-section IC (per-bet skill, shrunk)
      "ic_sd": 0.008,
      "ic_t": 1.6,                       // Fama-MacBeth t on the RAW (un-shrunk) monthly IC series
      "n_bets_eff": 1180,                // EFFECTIVE breadth: BR_eff = N/(1+(N-1)ρ̄) × months, NOT N×months
      "n_bets_naive": 2310,              // the upper-bound count (shown for honesty: how much breadth was lost to correlation)
      "rho_bar": 0.09,                   // avg pairwise active-bet correlation that deflated breadth
      "ic_source": "holdings-cross-section (active-weight vs fwd residual return)"
    },

    // ---- 5) THE TRADE-ALPHA COMPONENT (Component A/B — from the inferred trades = net-active flow) ----
    "trade_alpha": {
      "ic": 0.021,                       // IC of inferred trades (dw_active) vs fwd return — "are the trades good?"
      "ic_t": 1.9,
      "add_minus_trim": 0.018,           // ann. spread: names ADDED outperform names TRIMMED (the cleanest trade-skill read)
      "n_trades_eff": 640,
      "source": "funds_flows.net_active (dw_active), inflow-immune, corp-action-bridged"
    },

    // ---- 6) THE SLOW NAV-IR CORROBORATOR (kept clean, demoted from GATE to CHECK) ----
    "nav_corroborator": {
      "info_ratio": 0.41,                // the legacy NAV-level IR (slow, clean, breadth≈1-3/yr)
      "t_stat": 0.75,                    // IR·√years
      "years": 3.5,
      "years_needed": 23,                // (1.96/IR)^2 — why NAV alone can't decide at this tenure
      "agrees_with_posterior": true,     // sign(IR)==sign(posterior.best) — a consistency flag, not a gate
      "status": "uninformative_yet",     // {confirms | contradicts | uninformative_yet} vs the posterior
      "role": "Independent slow confirmation. Cannot DECIDE skill before ~years_needed; only corroborates."
    },

    // ---- 7) HONESTY RAILS (so the number is defensible, not curve-fit) ----
    "rails": {
      "fee_adjusted": true, "ter_annual": 0.0089,         // the TER subtracted to get net
      "factor_deflated": true, "factors": ["MKT","SMB","HML","WML","QMJ"],
      "factor_alpha_share": 0.55,                          // share of gross excess that SURVIVED factor deflation
      "fdr_q": 0.10, "passes_fdr": false,                  // Benjamini-Hochberg across the whole panel at q=0.10
      "fdr_note": "P(skilled) is the per-fund posterior; passes_fdr is the BOOK-level multiple-testing survivor flag",
      "benchmark_sensitivity": "low",                      // did the verdict flip under the alt benchmark? {low|med|high}
      "manager_tenure_contaminated": true,                 // still scheme-level until the tenure DB exists
      "caveats": ["scheme-level (may blend managers)", "month-end snapshots (intra-month trades invisible)"]
    },

    // ---- 8) PROVENANCE (reproducibility — KV's reporting rule) ----
    "as_of": "2026-05", "n_months": 42, "build_id": "2026-06-30",
    "definition": "posterior over net-of-fee factor-adjusted annual active return; estimated from the holdings cross-section IC and inferred-trade alpha (high breadth), shrunk to the SEBI-category prior, mapped to %/yr via the Fundamental Law (α≈TC·IC·√BR_eff·ω); NAV-IR kept as a slow independent corroborator."
  }
}
```

**`_manifest.json` (the leaderboard index)** gains the headline posterior fields so the scoreboard sorts on them without fetching every file:

```jsonc
"<code>": { "name":"...", "amc":"...", "category":"Mid Cap Fund",
  "tag":"likely_skilled", "p_skilled":0.74, "post_best":0.014, "post_lo90":-0.009, "post_hi90":0.036,
  "decile":8, "ic_t":1.6, "nav_ir":0.41, "n_months":42,
  "verdict":"likely_skilled" /* legacy key mirrors `tag` so old code doesn't crash */ }
```

**`_envelopes.json`** gains a per-category `posterior_rank` block (the cross-section of `p_skilled` and `post_best` each month) so the card can draw the fund's posterior bar *inside* its category's distribution — the existing vantage-envelope machinery (`build_envelopes`) already does exactly this shape for the batting/slug metrics.

#### D.1.3 The 5-state tag (replaces the binary verdict)

The single threshold (`t≥2 & boot≥95% ⇒ skilled`) becomes a **posterior-probability ladder** — same gate philosophy as the existing `fsVerdict`, but reading `p_skilled` and the credible interval instead of a hard t. Concretely, in `scheme_metrics` (Python) and mirrored in `fsVerdict` (JS):

| Tag | Rule (on the posterior) | Colour (reuse `_skillColor`) | Replaces |
|---|---|---|---|
| `skilled` | `p_skilled ≥ 0.90` **and** `lo90 > 0` **and** `passes_fdr` | green `#1a7f37` | `skilled` (but now FDR- & fee-gated → far fewer) |
| `likely_skilled` | `0.70 ≤ p_skilled < 0.90` | amber-green `#5a8f37` (new) | most of the old "ahead but not yet significant" |
| `unproven` | `0.40 ≤ p_skilled < 0.70` (CI straddles 0) | grey `#6e7781` | the genuine limbo — *explicitly labelled "statistically indistinguishable"* |
| `likely_unskilled` | `0.10 < p_skilled ≤ 0.40` | warm `#9a6700` | weak side of "ahead"/"lagging" |
| `lagging` | `p_skilled ≤ 0.10` **and** `hi90 < 0` | red `#b42318` | `lagging benchmark` |
| `insufficient_history` | `n_months < 12` (was 24 — the high-breadth estimate now works from ~1y) | grey | `insufficient history` |
| `index-like` | `tracking_error < 2%` | grey | unchanged |

The key change: **the old 50% limbo ("ahead but not yet significant") is no longer a dead bucket** — those funds now spread across `likely_skilled` / `unproven` / `likely_unskilled` *by their posterior*, each carrying a wide-but-quantified bar and a decile rank. The `unproven` tag is the *honest* "we cannot tell yet" — but even an unproven fund has a *rank* and a *best estimate*, which an allocator can use.

#### D.1.4 How the consumers change (Fund-Skill card + digital-AMC + parity)

**Fund-Skill card (`fsScorecardHTML`, `static/vistas.js`).** Today the header shows a single coloured `verdict` badge and a `q-stats` grid of point estimates. New top panel:
- **Skill posterior bar** — a horizontal credible-interval bar: a thick segment for `lo50…hi50`, thin whiskers to `lo90…hi90`, a tick at `best`, a zero line, and `P(skilled)=74%` as the headline number. Drawn with the existing Plotly machinery (reuse the `attachYAutoscale`/`viewPlotsResize` discipline from the chart-plotting skill; **never set a trace `marker:undefined`** — the burned-2026-06-22 lesson).
- **Decile chip** — "Top decile in Mid Cap (8/10)".
- The existing `qStat` grid stays but is re-labelled "Components": bet-level IC, trade-alpha add-minus-trim, NAV-IR (now tagged "slow corroborator: uninformative yet"). The MoneyBall batting/slugging panels are unchanged (they're descriptive).
- The `_windowed` tenure recompute (`fsComputeWindow`) must produce the posterior too (see parity below).

**Leaderboard (`fsLeaderboardHTML`).** Sort key default flips from `t_stat` to `p_skilled`; columns become `Tag | P(skilled) | Est/yr | 90% CI | Decile | NAV-IR | Mo.`. The CI column renders as a tiny inline bar so a user scans uncertainty at a glance.

**Digital-AMC manager scoring (`vistas/amc_context.py::packf`).** Today it ranks FM exemplars by `info_ratio` and reports `median_ir` per desk — i.e. on the SLOW, low-breadth NAV-IR (exactly the metric the problem statement says is useless at 3–7y tenure). Change: rank exemplars by `skill.posterior.best` (or `p_skilled`), and report `median_p_skilled` + `median_post_best` per desk alongside `median_ir`. The agentic-AMC's Fundamental-Law framing (FMs scored on IR decomposed into IC·√BR·TC) is *strengthened* — the FM's IC now comes from the high-breadth `bet_level.ic`, not the breadth≈1 NAV-IR. This is the direct line from this build to the North-Star: a per-scheme FM agent gets a *calibrated, early* skill read instead of a verdict that needs 16 years.

**★ Python↔JS parity (non-negotiable, the #1 repo rule).** The posterior must be computed identically in `vistas/funds_attribution.py` (baked into the full-history JSON) and in `static/vistas.js::fsComputeWindow` (recomputed for a tenure sub-window). The current parity surface already mirrors IR/t/IC/sizing/bootstrap *exactly*. The posterior adds: (a) the IC→%/yr mapping constants (TC, ω_active, the BR_eff correlation deflator), (b) the category prior `{prior_mean, prior_sd, n0}` table, and (c) the shrinkage + Normal-posterior arithmetic. **These must be shipped as DATA, not duplicated logic where avoidable**: bake the per-category prior table and the mapping constants into the deck shell (like `VISTAS_MACRO`), so JS reads the same numbers Python used. Then the *only* ported logic is the closed-form shrinkage and the Normal CDF for `p_skilled` (a 5-line `_normCdf`). After any change: `python _parity_dump.py && node _parity_check.js` (0 mismatches) and `node _deck_runtime_test.js` (PASS, 9/9 panels) — the posterior bar must render in the headless test. **And** add a parity assertion that `p_skilled`, `lo90`, `hi90`, `decile` agree Python↔JS to ≤1e-6 on the 12 dump configs. (Parity proves the two AGREE, not that the convention is RIGHT — the convention is audited separately by D.2/D.3.)

---

### D.2 Pre-registered out-of-sample validation — *does the early posterior predict future active return?*

This is the test that earns the metric its place. It follows the **flag-validation** skill (validate an early predictor of a future outcome as a *hypothesis test of predictive validity*, NOT a tradable NAV backtest) crossed with the **signal-backtest** discipline (judge the *distribution*, control for luck, pre-register the rule). It is **pre-registered**: the full spec below is committed to `prereg/skill_posterior_oos.md` with a SHA and date *before* the test is run; the analysis script reads only that frozen spec. No peeking, no post-hoc knob-turning.

**The one-sentence hypothesis.** *A fund's skill posterior, estimated using only data available at time t, ranks funds by their realized active return over the FOLLOWING window — so the top posterior bucket out-earns the bottom posterior bucket, out-of-sample, by a margin that beats a luck null.* If this fails, the posterior is curve-fit and must not ship as a forward read (it can still ship as a *descriptive* past-skill summary, clearly labelled).

#### D.2.1 The walk-forward protocol (concrete)

**Substrate.** The 158 monthly holdings cross-sections (`holdings_history.parquet`) ⋈ `tr_returns_monthly.parquet` ⋈ category benchmark fwd return — i.e. exactly the `load_panel()` output, plus the survivorship-free NAV panel (`_amfi_nav_panel.parquet`) for the *realized* forward active return (the outcome must be the REAL fund's NAV active return, not the holdings-implied one, to avoid any shared-estimation leakage between predictor and outcome).

**Loop (no look-ahead — the cardinal rule):**
1. For each **decision date** `t` stepping **every 6 months** from `2016-06` to `2024-06` (≈17 dates):
2. For every fund alive at `t` with `n_months(≤t) ≥ 12`: compute the **posterior using ONLY data ≤ t** (holdings cross-sections ≤ t, returns ≤ t, the category prior estimated from funds' data ≤ t — the prior itself must be walk-forward, not full-sample, or the prior leaks the future).
3. Rank funds **within SEBI category** by `p_skilled(t)` into deciles (or terciles where a category is thin; pool thin categories as in the live code's `MIN_PEERS` fallback).
4. **Hold for a forward window** `H ∈ {1y, 3y}` (pre-register BOTH; 3y is the headline — it matches the tenure horizon the metric is meant to serve). Measure each fund's **realized forward active return** = real-NAV CAGR(t→t+H) − category-benchmark CAGR(t→t+H), **net of fee** (TER already in NAV).
5. Record `(t, fund, category, decile, p_skilled, post_best, forward_active_H)`.

**The outcome statistics (pre-registered, in priority order):**
- **PRIMARY — top-minus-bottom decile spread.** Mean forward active of decile-10 minus decile-1, pooled across all `t`, with a **block-bootstrap CI** over decision dates (block = the forward horizon, to respect overlap autocorrelation — the same circular-block bootstrap already in `funds_attribution._block_bootstrap_mean`). **Pass = the spread is positive and its 95% CI excludes 0.** Pre-registered minimum effect to call it "useful": **≥ +2.0%/yr** top-minus-bottom at H=3y (below that it's real but not decision-grade).
- **SECONDARY — monotonicity & rank-IC.** Spearman rank-correlation between `p_skilled(t)` and `forward_active_H`, Fama-MacBeth-averaged across `t` with a t-stat; and a check that mean forward-active is *monotone increasing* across deciles (not just endpoints — a non-monotone "barbell" would signal an artifact).
- **TERTIARY — does the POSTERIOR beat the OLD verdict?** Re-run the identical loop ranking funds by the **legacy `t_stat`/`verdict`** and by the **NAV-IR alone**. The new posterior must show a *larger, earlier* spread — especially in the **short-tenure stratum** (funds with 1–4y of data at `t`), which is the entire reason the metric exists. Report the spread separately for tenure strata {1–4y, 4–8y, >8y}. **The headline result is: the posterior predicts at 1–4y tenure where the NAV-IR cannot.**

**The luck null (signal-backtest discipline).** The decile spread alone isn't enough — you must show it beats chance. Two nulls, both pre-registered:
- **Label-shuffle null:** at each `t`, randomly permute the `p_skilled` labels across funds *within category* (≥10,000 permutations), recompute the top-minus-bottom spread, and place the *actual* spread in that null distribution. Pass = actual ≥ 97.5th percentile of the shuffled null.
- **Persistence-vs-noise null:** the spread must also survive against a "past-return-chasing" straw man — rank funds by trailing raw active return (no skill model) and show the posterior's forward spread is *larger* (else the posterior is just momentum in disguise, the "trend-chasing mirage" FUNDAMENTAL_LAW §9.7 warns about).

**Multiple-testing honesty.** Two horizons × three tenure strata × (posterior, t_stat, NAV-IR) = a family of tests; pre-register the PRIMARY (3y, posterior, all-funds, top-minus-bottom) as the single confirmatory test, and treat the rest as **secondary/exploratory** with Benjamini-Hochberg FDR at q=0.10 across the family. Report all, but only the pre-registered primary can "confirm" the metric.

#### D.2.2 What a PASS / FAIL means (decided in advance)
- **PASS** (primary spread ≥ +2%/yr, CI excludes 0, beats both nulls, and the 1–4y stratum spread is positive): the posterior is a validated *forward* read. Ship it as the skill metric; the card may describe it as predictive (with the CI).
- **PARTIAL** (spread positive & beats nulls at all-funds, but the 1–4y stratum is null): ship the posterior but label the short-tenure read **"descriptive, not yet forward-validated"** — honest, and still better than the binary because of the rank+CI.
- **FAIL** (no spread, or doesn't beat nulls): **do not ship as forward**. Either keep it as a clearly-labelled past-skill descriptor, or send Component A/B back to the drawing board. "No score for error."

This entire protocol is implementable on today's data with no new fetch — it is exactly the `signal-backtest`/`flag-validation` harness pattern (`strategy/fft_strategy_v1.py::full_backtest`, `src/lib_harness.py`) re-pointed at the fund panel.

---

### D.3 The before/after audit + rollout

#### D.3.1 What the category verdict table plausibly looks like, after the rails

**Before (LIVE today, verified from `_manifest.json`, n=740 equity schemes):**

| Verdict | Count | Share |
|---|---|---|
| skilled | 152 | **21%** |
| ahead but not yet significant | 369 | **50%** |
| insufficient history | 117 | 16% |
| lagging benchmark | 97 | 13% |
| good selector, weak sizer | 4 | 1% |
| index-like | 1 | 0% |

Among the 249 *resolved* funds (skilled+lagging), **61% are "skilled"** — too cheap, because the excess is GROSS, un-factor-deflated, has no fee subtraction, and no multiple-testing correction (the problem statement's exact charge, now confirmed against data).

**After (plausible, directional — to be confirmed by the actual rebuild):** the three rails each shrink "skilled":
- **Fee subtraction** (median equity TER ≈ 0.9–1.8% net; the gross excess median is +2.5%/yr) wipes out roughly the bottom half of marginal "skilled" funds — a fund at +2%/yr gross is ~0–1%/yr net.
- **Factor deflation** (only ~55% of gross excess typically survives MKT/SMB/HML/WML/QMJ) removes the size/value/momentum tilts masquerading as selection — the FUNDAMENTAL_LAW §6 long-only finding.
- **Book-level FDR at q=0.10** across 740 funds: at p<0.05 you'd expect ~37 chance winners, so "skilled" must clear the *whole-right-tail-shifted* bar, not a per-fund threshold.

Plausible after-table (illustrative magnitudes, NOT a claim — the rebuild produces the real numbers):

| Tag | Plausible share | Note |
|---|---|---|
| skilled (net, factor-clean, FDR-survivor) | **~5–8%** | down from 21%; now genuinely defensible |
| likely_skilled (P 0.70–0.90, wide bar) | ~18–22% | the *informative* part of the old 50% limbo |
| unproven / statistically indistinguishable | ~30–38% | the HONEST limbo — but now each carries a posterior bar + decile rank |
| likely_unskilled | ~18–22% | |
| lagging (P≤0.10, hi90<0) | ~12–15% | |
| insufficient_history (<12mo) | ~6–8% | smaller than before — the high-breadth posterior now works from 1y, so fewer funds are "no verdict" |

**The headline migration story:** "skilled" drops from 1-in-5 to roughly 1-in-15 (defensible), the dead 50% limbo is *replaced by a ranked posterior with wide-but-quantified bars*, and **fewer** funds are stuck at "no verdict" because the metric now speaks from year one. The audit deliverable is a **side-by-side category table** (`output/SKILL_MIGRATION_AUDIT.md`) showing, per SEBI category: old-verdict counts, new-tag counts, the funds that *moved* (especially old-"skilled"→new-"unproven/likely_unskilled" — the ones the rails caught, each with WHY: fee, factor, or FDR), and the spread-test result from D.2.

#### D.3.2 Rollout plan (own workstream — audit before publish)

This is **not a UX-fix build**; it is a metric replacement, so it gets the full pre-registration → validation → audit → publish gate, run as its own workstream parallel to the live terminal (which keeps shipping the legacy verdict until the new one passes):

1. **Pre-register** (`prereg/skill_posterior_oos.md`, SHA-stamped) — D.2 frozen before any result is seen. (Aligns with the project's experiment-preregistration discipline from `project-setup`.)
2. **Build behind a flag** — `funds_attribution.build_all(posterior=True)` writes the new `skill` block alongside the legacy keys; `schema_version:2`. The deck reads `skill` when present, falls back to legacy. Nothing on the live site changes yet.
3. **Run the OOS validation** (D.2) → `output/SKILL_POSTERIOR_VALIDATION.md` (the decile-spread chart, the null distributions, the tenure-strata table, PASS/PARTIAL/FAIL).
4. **Run the before/after audit** (D.3.1) → `output/SKILL_MIGRATION_AUDIT.md`. **Human review gate:** KV reads both before anything goes live ("audit before publish").
5. **Parity + runtime** — `_parity_dump.py`/`_parity_check.js` 0 mismatches; `_deck_runtime_test.js` PASS with the posterior bar rendering; the new `_pup_fundskill.js` probe confirms the card renders the CI bar + decile chip under real Plotly.
6. **Flip the consumers** — only after PASS/PARTIAL + green audit: `fsScorecardHTML`/`fsLeaderboardHTML` default to the posterior; `amc_context.packf` ranks FMs on `p_skilled`. Legacy keys stay one release for rollback.
7. **Publish** via the normal one-click (`publish_terminal.py`), which also fires the off-machine code+docs backup (the standing crash-safety practice). The validation/audit `.md`s are committed to the codebase backup.
8. **Daily refresh** recomputes the posterior each build (`pipeline.py` already rebuilds `funds_attribution`); the cross-sectional decile/percentile and the FDR survivor flag are recomputed each run since they're panel-relative.

#### D.3.3 The honest risks (where this can be wrong — verify, don't import on authority)

1. **The IC→%/yr mapping is a model, not an identity.** `α ≈ TC·IC·√BR_eff·ω` assumes the Fundamental Law's five assumptions (independence, constant known IC, correct scaling, MV-optimal implementation, one-period/no-cost). The mapping constants (TC, ω_active, ρ̄) are *estimated* and category-dependent. **Mitigation:** the OOS test (D.2) validates the END-to-end mapping empirically — if the constants are wrong, the decile spread fails. Report the mapping constants in the JSON (`prior_mean`, `shrinkage`, etc.) so it's auditable, and run a sensitivity to ±50% on TC/ω.
2. **The posterior can be over-confident if the prior is wrong or the likelihood ignores estimation correlation.** A too-tight prior makes `p_skilled` cluster near 0.5 (everything "unproven"); a too-loose prior lets thin-history funds claim false confidence. **Mitigation:** calibration check — across the OOS folds, the realized hit-rate of "P(skilled)=X%" funds must track X% (a reliability diagram; pre-registered as a validation sub-check). If `p_skilled` is mis-calibrated, the whole headline is untrustworthy.
3. **Breadth deflation (ρ̄) is noisy and model-dependent** (FUNDAMENTAL_LAW §5/§9.3). Over-stating BR_eff inflates the posterior. **Mitigation:** report `n_bets_naive` AND `n_bets_eff` so the deflation is visible; bound BR_eff conservatively (use the *upper* ρ̄ estimate → lower breadth → wider, more honest bars).
4. **Holdings IC has the same cap-tilt contamination the live `_ic()` already flags** — active-weight-vs-fwd-return is not pure selection without point-in-time benchmark WEIGHTS (the W-HIST gap in the design doc). **Mitigation:** keep the existing inline caveat; factor deflation removes the size component; flag `manager_tenure_contaminated` and the W-HIST limitation in `rails.caveats`.
5. **Scheme-level, manager-blended** (no tenure DB yet). A scheme spanning 3 managers gives an uninterpretable posterior just as it gives an uninterpretable t. **Mitigation:** stamped in `rails.manager_tenure_contaminated`; the metric is honest about being scheme-level. The tenure DB remains the binding constraint for *person*-level claims.
6. **Survivorship in the OOS test.** Funds that died between `t` and `t+H` must keep their terminal active return (use the survivorship-free `_amfi_nav_panel.parquet`), or the forward-return outcome is upward-biased and the spread is fake. **Mitigation:** pre-register that dead funds are retained with their realized (often negative) terminal active return; verify the panel carries them before running.
7. **Overlap / pseudo-replication in the walk-forward.** 6-month steps with a 3y hold means each fund appears in ~6 overlapping windows → naive CIs are too tight. **Mitigation:** the block-bootstrap over decision dates (block=H) and the label-shuffle null both respect this; report effective-N.
8. **The metric could simply not predict** (the real risk). If D.2 FAILs, that is the finding — the posterior is a descriptive past-skill summary, not a forward signal, and we say so. No curve-fitting a pass.

**Bottom line:** the output schema gives every fund a calibrated skill posterior (estimate + bar + P(skilled)), a within-category decile rank, an explicit "unproven/statistically indistinguishable" tag for the limbo, the bet-level and trade-alpha components, and the slow NAV-IR demoted to corroborator — all parity-mirrored Python↔JS and consumed by both the Fund-Skill card and the digital-AMC FM scoring. The pre-registered walk-forward decile-spread test (with luck nulls, tenure strata, and a calibration check) is what proves it predicts forward active return — *especially at the 1–4y tenures the NAV-IR can't serve* — and the before/after audit + human gate ensures the much-lower, defensible "skilled" rate and the ranked-posterior replacement of the 50% limbo are validated before anything goes live.


**Key formulas (quick reference):**

- **Shrinkage posterior mean (Empirical-Bayes)** — `post_best = shrinkage·data_est + (1−shrinkage)·prior_mean,  where shrinkage = n_eff/(n_eff + n0),  n0 = prior_sd^{-2}-implied pseudo-count` — The best skill estimate is a weighted blend of what THIS fund's bets showed (data_est) and what funds in general look like (the category prior). A fund with lots of independent bets (big n_eff) leans on its own data; a new fund leans on the prior. n0 is how much the prior 'counts' as data.
- **Posterior variance & credible interval** — `post_var = 1/(1/prior_sd^2 + n_eff/sigma_bet^2);  lo90,hi90 = post_best ∓ 1.645·sqrt(post_var)` — The error bar. Combining the prior's spread with the data's spread (more effective bets → smaller post_var → tighter bar). The 90% credible interval is best ± 1.645 standard deviations — the band we're 90% sure the true skill lies in.
- **Probability of skill** — `p_skilled = P(true_active > 0) = 1 − Φ( (0 − post_best)/sqrt(post_var) ) = Φ( post_best / sqrt(post_var) )` — The single headline number: the area of the posterior bell-curve that sits above zero. Φ is the standard normal CDF. If the whole bar is above 0, p_skilled→1; if it straddles 0, p_skilled→0.5 (the honest 'unproven').
- **IC→annual-active mapping (Fundamental-Law atom, calibrated)** — `data_est (net %/yr) = TC · IC · sqrt(BR_eff) · omega_active − TER,  with BR_eff = (N/(1+(N−1)·rho_bar))·months` — Turns a per-bet skill correlation (IC) into an annual active return: skill per bet × how much survives long-only constraints (TC) × sqrt of the number of INDEPENDENT bets (breadth, deflated by bet correlation rho_bar) × the active-risk size (omega). Then subtract the fee to get NET. This is the law IR=TC·IC·√BR turned into a %/yr.
- **Effective breadth (Qian-Hua / Buckle)** — `BR_eff = N / (1 + (N−1)·rho_bar)  per cross-section, × number of independent rebalances` — You don't really get N independent bets if your bets move together. If 100 names share avg correlation 0.1, effective breadth ≈ 9, not 100. This is what stops a 200-stock thematic fund from claiming huge breadth — it's often one macro call in 200 coats.
- **OOS predictive test — top-minus-bottom decile spread** — `Spread_H = mean_t[ mean(fwd_active_H | decile=10) − mean(fwd_active_H | decile=1) ];  PASS if BlockBootstrapCI_95(Spread_H) excludes 0 AND Spread_H ≥ +2%/yr AND Spread_H ≥ 97.5th pctile of the within-category label-shuffle null` — Rank funds by the posterior using ONLY past data, then look forward H years: do the top-decile funds out-earn the bottom-decile funds, out-of-sample, by enough to matter and beyond luck? This is the test that proves the metric is real, not curve-fit.
- **Fama-MacBeth rank-IC of the posterior** — `rankIC = mean_t Spearman(p_skilled_t, fwd_active_{t→t+H});  t-stat = mean(rankIC)/(sd(rankIC)/sqrt(n_dates))` — A finer version of the decile test: across all decision dates, how well does the posterior's RANKING of funds line up with their actual forward outperformance ranking? Positive and t>2 means the ordering is informative, not noise.
- **Calibration (reliability) check** — `for each posterior-probability bucket b: realized_skill_rate(b) ≈ mean(p_skilled | bucket b);  well-calibrated ⇔ realized_rate(X%) ≈ X%` — If we say 30 funds are '80% likely skilled', about 24 of them (80%) should actually turn out skilled out-of-sample. If the realized rate doesn't track the stated probability, the posterior is over- or under-confident and the headline P(skilled) can't be trusted.

**Feasibility / data gaps:** COMPUTABLE TODAY (no new data): (1) The full per-fund `skill` JSON block — `funds_attribution.load_panel/scheme_metrics` already produces the scheme-month A/rp/rb/ic/herf/sizing series and the holding-rank IC; the trade-alpha comes from `funds_flows.net_active` (dw_active) which is already built and CA-bridged; `amc_replay.scorecard` already computes IC·√BR·TC and BR_upper, and `_tc_sample` gives a TC proxy — so the bet-level/trade-alpha/TC inputs all exist. (2) The walk-forward OOS validation — all 158 holdings cross-sections + tr_returns_monthly + the survivorship-free _amfi_nav_panel.parquet exist; the loop is the signal-backtest/flag-validation harness re-pointed (block-bootstrap + label-shuffle null already exist in _block_bootstrap_mean). (3) The before/after audit — the live _manifest.json gives the exact 'before' (verified: 21% skilled, 50% limbo, 61% skilled-among-resolved); the 'after' is a rebuild with the rails on. (4) Empirical-Bayes prior — pooled per-category IC from the panel itself (walk-forward). NEEDS-NEW-WORK but no new fetch: (a) the FACTOR LIBRARY (MKT/SMB/HML/WML/QMJ) for the factor-deflation rail — FUND_MANAGER_ANALYSER_DESIGN §6 Phase 2, buildable from our TR+mcap+fundamentals but not yet built; until it exists, ship the posterior on GROSS-but-fee-adjusted active and flag factor_deflated:false (the rail is staged). (b) TER history per scheme for exact fee subtraction — use AMFI/category-median TER as an interim, flagged. (c) Point-in-time benchmark constituent WEIGHTS (W-HIST) for a contamination-free active-weight IC — not available; the holding-rank IC stays a cap-tilt-contaminated proxy (already flagged in live code), partly mitigated by factor deflation. HONEST GAPS: the IC→%/yr mapping constants (TC, omega, rho_bar) are estimated and the mapping is a model — the OOS test is what validates it end-to-end; the metric stays SCHEME-level (manager-blended) until the tenure DB exists; and a genuine risk is that D.2 FAILs (the posterior doesn't predict forward), in which case it ships as a labelled descriptive past-skill summary, not a forward signal — 'no score for error'.


<details><summary>Grounded in (code/data the agent read)</summary>

- vistas/funds_attribution.py::scheme_metrics (the GATED verdict tree, IR=mean/std·√ppy, t=IR·√years, _ic() holding-rank IC, sizing edge, the `ts` per-month series baked into each JSON)
- vistas/funds_attribution.py::load_panel (scheme-month panel: A, rp, rb, ic, herf, sizing, port_hit/slug)
- vistas/funds_attribution.py::build_all / build_envelopes / _manifest.json (per-fund JSON + manifest + peer envelopes the deck lazy-loads)
- vistas/funds_flows.py::_pair_flows_active / stock_active_flows / build_stock_series (net-active trade substrate = dw_active, inflow-immune, CA-bridged)
- vistas/amc_replay.py::scorecard (the IC·√BR·TC decomposition: ic_mean, ic_t, transfer_coefficient, BR_upper, implied_IR_UPPER) and _tc_sample / _spearman
- static/vistas.js::fsComputeWindow (the JS parity port that recomputes every metric over a tenure window) + fsVerdict (the verdict-ladder port) + fsScorecardHTML + fsLeaderboardHTML + _skillColor
- vistas/amc_context.py::packf (the digital-AMC consumer: reads info_ratio/ic_mean/ic_t/excess_cagr/verdict per fund, ranks FMs by info_ratio, median_ir per desk)
- FUNDAMENTAL_LAW.md (IR=IC·√BR·TC; t≈IR·√years; BR_eff=N/(1+(N-1)ρ̄); long-only TC≈0.3-0.6)
- FUND_MANAGER_ANALYSER_DESIGN.md §2-3 (Fama-MacBeth IC-t, tilt-matched bootstrap null, t=IR·√years gate, years_needed, book-level FDR/Barras-Scaillet-Wermers, OOS persistence 2013-19→2020-25)
- DIGITAL_AMC.md (FMs scored on IR/TC, the agentic-AMC consumer)
- data/funds_attribution/_manifest.json LIVE: n=740, skilled 21%, 'ahead but not yet significant' 50% (369), lagging 13%, insufficient 16%; resolved=249, skilled-among-resolved 61%; median tenure 7.8y; holding-rank IC mean 0.009/mo sd 0.022; only 24% clear |t|≥2; excess>0 84%
- data/funds/history/holdings_history.parquet (158 monthly cross-sections, vst_id) + data/funds/_amfi_nav_panel.parquet (survivorship-free NAV)
- skills: signal-backtest (all-starts × random null, judge %tile distribution over ≥5y windows), flag-validation (predictive-validity test of an early flag, not a NAV backtest)
</details>


---
