# The Fundamental Law of Active Management — a bottom-up derivation
### From "what is a forecast worth?" to `IR = IC · √BR · TC`, and how to measure it on real funds

A first-principles report. We build every block from scratch — define each term in plain words with a
worked example, derive the law rather than quote it, stress-test the assumptions, then show exactly what
is and isn't measurable on our fund panel. Written for the Vistas CIO/FM stack
([[FM_INTELLIGENCE]], [[FUND_MANAGER_ANALYSER_DESIGN]]); stamped 2026-06-26.

> **The one sentence (Feynman version).** *How good an active manager looks = how good each bet is
> (skill) × how many independent bets they take (breadth) × how much of that skill actually survives the
> real-world constraints they trade under (transfer).* In symbols: **`IR = IC · √BR · TC`.** The first two
> are the dream (Grinold 1989); the third is the tax reality charges on the dream (Clarke-de Silva-Thorley
> 2002). Most of the gap between "this manager is skilled" and "this fund beat its index" lives in the
> third term.

---

## 1. The primitives — build the vocabulary from zero

We separate a portfolio's return into the part the market gave everyone and the part the manager *chose*.

- **Benchmark `B`** — the index the fund is measured against (for us: the reconstructed category benchmark,
  `vistas/benchmarks.py`). Its return `R_B`.
- **Active (residual) return `θ`** = fund return − benchmark return, `θ = R_p − R_B`. The part that is the
  manager's *doing*, not the market's. (Strictly, "residual" means after also removing beta; for a fund
  run at beta ≈ 1 to its benchmark, active ≈ residual. We keep the distinction where it matters.)
- **Active risk / tracking error `ω`** = the standard deviation of `θ`, annualized. How *bumpy* the active
  return is. A fund hugging its index has tiny `ω`; a high-conviction fund has large `ω`.
- **Active weights `a_i = w_i − W_i`** — the manager's bet on stock `i`: how much they hold (`w_i`) minus
  what the index holds (`W_i`). By construction `Σ_i a_i = 0` (overweights funded by underweights). **The
  active weights ARE the portfolio's expressed opinion.** Active return is exactly `θ = Σ_i a_i r_i`
  (the accounting identity the attribution engine rests on — [[FUND_MANAGER_ANALYSER_DESIGN]] §0).

Now the three quantities the law is built from:

### 1.1 Information Ratio (IR) — the scorecard
> **Definition.** `IR = (annualized active return) / (annualized active risk) = E[θ]/ω`.

It is the **Sharpe ratio of skill**: reward per unit of *active* risk taken, with the market return netted
out. Scale-free (doubling all bets doubles both numerator and denominator → IR unchanged), so it compares
a cautious and an aggressive manager fairly. *Worked example:* a fund beats its index by **+2%/yr** with a
tracking error of **4%/yr** → `IR = 0.50`. That's a *good* long-only equity manager. `IR = 1.0` is
exceptional and rare; `IR = 0` is no skill.

**Why IR is the right target (not raw alpha):** Grinold-Kahn show the *value added* a manager can deliver
is `VA = IR²/(4·λ)`, maximised at an optimal active risk `ω* = IR/(2λ)`, where `λ` is the investor's
aversion to active risk. **Value added depends on IR *squared* and on nothing else about skill.** So IR is
the sufficient statistic for "how much good can this manager do" — everything else is about *how to use*
that IR (how aggressive to run).

### 1.2 Information Coefficient (IC) — the skill of one bet
> **Definition.** `IC = correlation( forecast , realized outcome )` across bets — how well the manager's
> *predicted* ranking of stocks matches what actually happens.

It is the **per-bet skill**, a correlation in `[−1, +1]`. *Calibration:* IC = 0 is a coin-flip; **IC ≈
0.05 is a genuinely good equity forecaster**; IC ≈ 0.10 is world-class; IC = 1 is omniscience. These look
tiny because predicting a single stock's residual return is *hard* — most of it is noise. (For context,
our ARM signal's IC is ≈ 0.03–0.045 — `arm_backtest.py` — a real but modest edge.) IC is usually measured
as a **rank** correlation (Spearman) to be robust to outliers.

### 1.3 Breadth (BR) — how many independent bets per year
> **Definition.** `BR = number of independent active decisions made per year.`

The word that does all the work — and the one most often abused. **"Independent" is the load-bearing
word.** A manager who bets on 50 stocks but on *one theme* (e.g. "rates will fall," expressed 50 ways) has
**breadth ≈ 1**, not 50 — the bets rise and fall together. A manager covering 200 stocks with 200 genuinely
distinct theses and re-deciding quarterly has `BR ≈ 800`. *Worked example:* 100 stocks × 4 independent
rebalances/yr = `BR = 400` *only if* the 100 picks and the 4 rebalances are mutually independent; correlated
bets cut it sharply (§5).

---

## 2. The first building block — what is a single forecast worth?

Before combining bets, value **one** forecast. This is **Grinold's forecasting rule**, and it is the atom
of the whole theory.

**Setup.** A stock has an unknown residual return `θ` with mean 0 and volatility `ω`. You observe a signal,
standardized into a **score** `z` (mean 0, std 1) — e.g. a z-scored ARM, or a valuation rank. Assume `θ`
and `z` are jointly normal with correlation `IC`.

**The projection theorem** (conditional expectation under joint normality) gives, exactly:
$$\boxed{\;\alpha \;\equiv\; \mathbb{E}[\theta \mid z] \;=\; \underbrace{\omega}_{\text{volatility}} \cdot \underbrace{IC}_{\text{skill}} \cdot \underbrace{z}_{\text{score}}\;}$$

**Read it in words:** your best estimate of a stock's edge = *its volatility* (the size of the opportunity)
× *your skill at reading it* (IC) × *how strong today's signal is* (the score). This is the famous
**"alpha = volatility × IC × score."**

*Worked example.* A stock with `ω = 30%/yr`. Your signal fires at `z = +2` (two standard deviations
bullish). Your skill `IC = 0.05`. Then `α = 0.30 × 0.05 × 2 = +3%`. A strong signal, a real (if modest)
skill, on a volatile stock → a 3% expected edge. Halve the skill or the conviction and it halves. **This is
why IC ≈ 0.05 is "good": multiplied by volatility and conviction it still produces tradeable alphas — but
only just, which is why you need many bets.**

The amount of *information* one such optimal bet carries (its contribution to IR²) is, exactly,
`IR_{1\,bet}^2 = IC^2/(1-IC^2) \approx IC^2` for the small IC of real life. **Hold onto this:** one bet is
worth `IC²` of squared-information. The law is just this, summed.

---

## 3. The law itself — combine independent bets

Now take **BR independent bets**, each an optimal forecast of quality `IC`. Two facts do all the work:

1. **Information adds across independent bets.** Value added is proportional to `IR²`, and for
   *independent* return streams variances add, so the squared information ratios add:
   $$IR_{\text{total}}^2 \;=\; \sum_{k=1}^{BR} IR_{k}^2 \;=\; BR \cdot IC^2.$$
   (This is the "additivity of information": two uncorrelated alpha sources contribute `IR_1²` and `IR_2²`,
   and the combined `IR² = IR_1² + IR_2²` — exactly like combining independent Sharpe ratios.)

2. **Take the square root.**
   $$\boxed{\;IR \;=\; IC \cdot \sqrt{BR}\;}\qquad\text{(Grinold's Fundamental Law, 1989)}$$

**Why `√BR` and not `BR` — the intuition that makes it click.** Each extra independent bet adds *value*
linearly (more good bets → more alpha) but also adds *risk*, and independent risks add as a square root
(diversification). Value over risk therefore grows as `linear / √ = √`. It is the **exact same √N law that
makes a diversified portfolio's Sharpe ratio rise with the number of independent holdings** — here applied
to *bets* instead of *assets*. Skill sets the *quality* of each bet; breadth lets diversification turn
modest per-bet skill into a high aggregate ratio.

**The headline consequence — skill is cheap, breadth is dear, and they trade off as a square.** To double
your IR you can either **quadruple your skill** (IC, almost impossible) or **quadruple your breadth** (BR,
merely hard). *Worked example:*

| Manager | IC (skill/bet) | BR (independent bets/yr) | `IR = IC·√BR` |
|---|---|---|---|
| Concentrated guru | 0.10 (rare) | 10 | `0.10·√10 = 0.32` |
| Diversified quant | 0.04 (modest) | 400 | `0.04·√400 = 0.80` |

The "lesser" forecaster with a *quarter* the skill wins decisively **because breadth is inside a square
root and they have 40× more of it.** This single table is why systematic, broad strategies can beat star
stock-pickers — and why a concentrated manager must have *genuinely exceptional* IC to justify the
structure.

### 3.1 The t-statistic bridge (skill vs luck)
The IC is estimated from `N = BR × years` cross-sectional observations; a correlation's t-stat is
`t ≈ IC·√N`. Combined with `IR = IC·√BR`:
$$\boxed{\;t_{\text{skill}} \;=\; IR \cdot \sqrt{\text{years}}\;}$$
So an `IR = 0.5` manager needs **`(1.96/0.5)² ≈ 16 years`** to clear `p < 0.05`; an `IR = 1.0` needs 4.
**Quadrupling the track record only doubles the confidence.** This is the minimum-data rule stamped on
every skill verdict in [[FUND_MANAGER_ANALYSER_DESIGN]] §3 — and it falls straight out of the law.

---

## 4. The assumptions hiding in the law (read before trusting it)

The clean `IR = IC·√BR` quietly assumed **five** things. Each is a place to be wrong:

1. **Bets are independent** — needed for "information adds." Almost never true; correlated bets shrink
   *effective* breadth (§5).
2. **IC is constant and known** — real IC varies through time and is *estimated* with error (and is often
   overstated by look-ahead/ex-post selection).
3. **Signals are unbiased and properly scaled** — the forecasting rule assumes you scale alphas correctly;
   over-confident scaling (treating IC = 0.10 when it's 0.03) destroys value.
4. **Mean-variance optimal, UNCONSTRAINED implementation** — you can hold the exact optimal active weights,
   including shorts. **This is the assumption real funds violate hardest** → the transfer coefficient (§6).
5. **One period, no costs** — turnover and trading costs are absent; more "breadth" via faster trading is
   not free.

Grinold-Kahn knew the law was a *guideline for thinking*, not a measurement device. The next two sections
are the two most important corrections.

---

## 5. Correction 1 — effective breadth (Qian-Hua, Buckle)

"Number of stocks × rebalances" massively overstates BR when bets are correlated. If `N` bets share an
average pairwise correlation `ρ̄`, the **effective breadth** collapses toward:
$$BR_{\text{eff}} \;\approx\; \frac{N}{1 + (N-1)\,\bar\rho}.$$
*Worked example:* 100 bets with a modest `ρ̄ = 0.1` → `BR_eff ≈ 100/(1+9.9) ≈ 9.2`, **not 100.** A little
correlation annihilates breadth. (This is why a "200-stock" thematic fund can have the breadth of a handful
of bets — it's really one macro call wearing 200 coats.)

**Qian-Hua's "strategy risk":** realized IR has *extra* variance because IC itself wobbles year to year, so
the *realized* IR is systematically **below** `IC·√BR`. The law gives an *upper bound* on what's
achievable, not a promise. Lesson for fund analysis: never read a shortfall from `IC·√BR` as "no skill" —
some of it is correlated-breadth and strategy-risk leakage.

---

## 6. Correction 2 — the Transfer Coefficient (Clarke-de Silva-Thorley, 2002)

This is the piece KV asked about, and the most practically important. The law assumed you implement the
**optimal** active weights `a^* \propto \Sigma^{-1}\alpha` (mean-variance: bets proportional to
risk-adjusted alphas, `Σ` = residual covariance). **Real portfolios cannot.** They face:

- **long-only** (`w_i ≥ 0`, i.e. `a_i ≥ −W_i` — you cannot underweight a stock by more than its index
  weight, and cannot short at all);
- full-investment (`Σ w_i = 1`), sector/single-issuer caps (SEBI), position-size limits;
- a **tracking-error budget**, turnover/liquidity/capacity limits.

So the **implemented** active weights `a` differ from the **ideal** `a^*`. Clarke-de Silva-Thorley define:

> **Transfer Coefficient `TC` = the (risk-adjusted) correlation between the active weights you actually
> hold and the active weights you *would* hold if unconstrained.** `TC ∈ [0, 1]`. 1 = perfect transfer
> (you held exactly the optimal bets); 0 = your portfolio is uncorrelated with your own best ideas.

**The generalized law:**
$$\boxed{\;IR \;=\; TC \cdot IC \cdot \sqrt{BR}\;}$$

**Why this is exactly right — the geometry (the clean intuition).** Think of every possible active-weight
vector as a point in a space where "distance" is measured in active risk (the `Σ`-metric). The maximal IR
points in the direction of `a^*`. Your constraints confine you to a feasible region; the best you can do is
the *projection* of `a^*` onto that region. The realized IR is the full IR multiplied by the **cosine of the
angle** between where you wanted to point (`a^*`) and where you were *allowed* to point (`a`). **That cosine
is the transfer coefficient.** Constraints don't lower your *skill* (IC) or your *breadth* — they rotate
your portfolio away from your ideas, and you keep only `cos(angle) = TC` of the dream.

**The killer empirical fact — long-only alone is brutal.** The long-only constraint bites hardest on the
*negative* views: for a stock the index holds at `W_i = 0.1%`, the most you can underweight is 0.1% — your
bearish conviction is **truncated**. Since a cap-weighted index is mostly *small* weights, *most* of a
manager's would-be short/underweight bets are amputated. Clarke-de Silva-Thorley's result: a long-only,
benchmark-relative equity portfolio typically has **`TC ≈ 0.3 – 0.6`** — i.e. **a skilled manager captures
only a third to a half of their theoretical information ratio**, purely from being long-only. Add tight
tracking-error, sector caps and turnover limits and TC falls further.

*Worked example (the whole point in one line).* A manager with real skill `IC = 0.05` over `BR = 200`
independent bets has a *theoretical* `IR = 0.05·√200 = 0.71` — excellent. Run long-only with `TC = 0.4`:
**realized `IR = 0.4 × 0.71 = 0.28`** — merely "okay." **Their skill didn't fall; 60% of it was confiscated
at the door by the long-only constraint.** If you judged them on realized IR alone you'd under-rate a
genuinely good forecaster. *This is the central insight for manager evaluation:* **decompose the shortfall —
is a mediocre IR a skill (IC) problem or a transfer (TC) problem?** They have opposite remedies (fire the
manager vs. loosen the mandate / let them run higher active share).

### 6.1 The bridge to Active Share (why these two ideas are the same coin)
Cremers-Petajisto's **Active Share** (`½Σ|w_i − W_i|`, [[FUND_MANAGER_ANALYSER_DESIGN]] §2.2) measures *how
different* the portfolio is from the index. **TC measures how well that difference is *aimed* at the
manager's best ideas.** A closet indexer (Active Share < 0.6) has *both* low active share and low TC — it
can't transfer skill because it barely deviates. A high-active-share fund *can* have high TC (it took real
active risk) — but only if those deviations point at genuine alpha, not noise. **Empirically TC should rise
with Active Share** — a cross-check we can run on our data (§7).

---

## 7. Measuring this on OUR funds — what's possible, the proxies, the honest caveats

We have the ingredients: the **holdings panel** (`vst_id`-keyed, monthly 2013→2025-10, ~777 schemes), the
**reconstructed category benchmarks** (`vistas/benchmarks.py`, EW + FF-mcap), the **TR return panel**, the
**attribution engine** (`funds_attribution.py`), and **Active Share** (`build_active_share`). Each law-term
maps to a computable (sometimes proxied) quantity:

| Term | How we estimate it | Data source / granularity | The honest caveat |
|---|---|---|---|
| **Realized IR** | annualized active return ÷ tracking error, holdings-based (`θ_t = Σ a_i,t r_i,t`) and NAV-based, multi-period Cariño-linked | holdings ⋈ TR ⋈ benchmark; row = (fund, month) | needs the right benchmark (`R_b`); run a benchmark-sensitivity check |
| **Realized IC** (revealed-forecast) | Fama-MacBeth mean of monthly cross-sectional Spearman( `a_i,t` , residual `r_i,t+1` ) — **the active weight is the revealed forecast** | per (fund, month) cross-section | conflates forecast quality with sizing; it's a *revealed* IC, a proxy, not the manager's true private forecast |
| **Effective breadth `BR_eff`** | `N_active / (1 + (N−1)ρ̄)` × rebalances/yr, `ρ̄` from the residual-return covariance of held names | holdings + residual cov | model-dependent; correlation estimate is noisy → report a range |
| **Transfer coefficient `TC`** | **(a) direct:** build a residual risk model `Σ` (start: single-factor/sample-shrunk), infer the implied forecast from `a`, compute `corr(a, a^*)` in the `Σ`-metric. **(b) residual:** `TC = IR_realized /(IC·√BR_eff)` | holdings + `Σ` | route (a) needs a risk model (a real choice); route (b) inherits all prior errors — **the gap between (a) and (b) is itself a diagnostic** |

**The defensible framing (so this is holdable, not curve-fit):**
- We **cannot observe true forecasts**, so IC here is "skill *revealed by the bets placed*" — a *lower
  bound* flavour (a manager with great ideas they couldn't implement shows low revealed-IC but it's really
  a TC problem; that's exactly what we want to surface).
- Every fund-level number carries the **`t = IR·√years` minimum-data stamp** and a **tilt-matched bootstrap
  luck bar** ([[FUND_MANAGER_ANALYSER_DESIGN]] §3); book-level we control **FDR** across the 777.
- **Scheme-level, not manager-level** until the tenure DB exists — stamped.
- **The cross-checks that make it credible (not just plausible):** (1) **TC should rise with Active Share**
  (§6.1) — if our data shows it, the TC estimate is behaving; (2) the **direct and residual TC routes
  should roughly agree**; (3) decile-sorted, higher-IC funds should show higher realized IR *only after*
  TC adjustment.

**What we'd be testing — the findings worth an article:** is the binding constraint on Indian long-only
equity funds' information ratios their **skill (IC)** or their **implementation/long-only leak (TC)**? The
literature predicts TC ≈ 0.3–0.6 dominates — *if our panel shows the same, that's a clean, defensible,
locally-novel finding* with a direct implication: **manager selection should reward high revealed-IC even
where realized IR is modest, and mandates that allow higher active share / relaxed constraints convert
existing skill into realized return without needing *more* skill.** That last sentence is also the design
rationale for the **expert-FM constructor** ([[FM_INTELLIGENCE]] §2): maximise *transferable* IC under the
mandate — i.e. push TC up as far as the constraints allow.

---

## 8. The article — proposed structure (rooted in the academics, carrying our numbers)

1. **The puzzle** — "why do skilled-looking managers so often fail to beat their index?" (hook).
2. **The law, bottom-up** — §§1–3 of this report, compressed to the forecasting-rule atom → `IC·√BR`.
3. **The two cracks** — effective breadth (§5) and, the star, the transfer coefficient (§6) with the
   long-only-leak worked example.
4. **Our evidence** — the funds analysis (§7): the cross-section of realized IR, revealed-IC, BR_eff and TC
   across ~777 schemes; the TC↔Active-Share cross-check; the IC-vs-TC binding-constraint verdict; the luck
   bar + FDR so it's honest.
5. **Implications** — for investors (don't confuse a transfer problem with a skill problem), for manager
   selection (the CIO team-build — reward uncorrelated transferable skill), and for product design (the
   expert-FM mandate that maximises TC).
6. **Caveats** — §9, in full, never buried.

---

## 9. FLAGGED — what must not be overclaimed

1. **The law is an *upper bound / guideline*, not a measurement** — realized IR sits below `IC·√BR` because
   of correlated breadth and strategy-risk (Qian-Hua). Never read a shortfall as "no skill."
2. **IC here is *revealed* from active weights**, not the manager's true forecast — a proxy that conflates
   forecasting and sizing; a low revealed-IC may be a *TC* problem in disguise (which is the interesting case).
3. **BR_eff and TC are model-dependent** (need a residual covariance / risk model) — report ranges, show
   the two TC routes, and run the Active-Share cross-check before believing them.
4. **Scheme-level, manager-change-contaminated** until the tenure DB exists — the binding gap for
   person-level statements.
5. **Benchmark identity is load-bearing** — `θ = R_p − R_B` is only as right as `R_B`; sensitivity-check it.
6. **Returns must be TOTAL return** (delisted names retained) — else IC/IR under-credit dividend payers and
   re-introduce survivorship bias (the analyser's lead-review correction).
7. **No `Math.random`/look-ahead in the IC estimate** — forecast (`a_t`) must predate the return (`r_{t+1}`);
   a contemporaneous correlation is the trend-chasing mirage, not skill.

---

*Canonical sources (read bottom-up):* Grinold (1989) "The Fundamental Law of Active Management," *J.
Portfolio Mgmt*; Grinold & Kahn, *Active Portfolio Management* (2nd ed., 1999), Chs 5–6 (the forecasting
rule, the law, value added); Clarke, de Silva & Thorley (2002) "Portfolio Constraints and the Fundamental
Law of Active Management," *Financial Analysts J.* (the transfer coefficient); Clarke-de Silva-Thorley
(2006) on the generalized law; Buckle (2004) and Qian & Hua (2004) "Active Risk and Information Ratio" (the
breadth/strategy-risk corrections); Cremers & Petajisto (2009, Active Share); Lo (2002) on the statistics of
the Sharpe/information ratio. Internal: [[FUND_MANAGER_ANALYSER_DESIGN]] (the realized-metric engine + luck
gate), [[FM_INTELLIGENCE]] (the expert-FM = transferable-IC maximiser), `arm_backtest.py` (our IC evidence).
