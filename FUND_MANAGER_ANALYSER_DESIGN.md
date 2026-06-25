# Vistas Fund-Manager Analyser — Design Blueprint
### "The Free-Body Diagram of a Fund"

A state-of-art, holdings-based attribution + skill-vs-luck terminal for the 777-scheme / 46-AMC / vst_id-keyed panel (2013-04 → 2025-10, ~150 monthly snapshots). Blueprint for critical review before build. (SME workflow `fund-manager-sme`, 7 first-principles lenses → synthesis; 2026-06-23.)

---

## 0. The one sentence the whole thing rests on

A fund's excess return over its benchmark is, **exactly and always**, the dot product of its bets with what happened:

$$A \;=\; R_p - R_b \;=\; \sum_i a_i\, r_i,\qquad a_i \equiv w_i - W_i$$

where $w_i$ = the fund's weight in name $i$ at the **start** of the period, $W_i$ = the benchmark's weight, $a_i$ = the **active weight** ($\sum_i a_i = 0$), and $r_i$ = name $i$'s **total return** (price + dividend) over the period. This is an **accounting identity, not a model** — it cannot lie. Every metric below is a *grouping*, a *conditioning*, or a *significance test* applied to this one line.

*Tiny worked example.* Hold HDFC Bank at 8% when the index holds 6% → $a=+2\%$. HDFC returns +5% vs index +3% → that bet contributes $0.02\times(0.05-0.03)=+0.04\%$ to active return. Sum over all names = the fund's entire edge.

---

## 1. The Free-Body Diagram — the additive identity

### 1.1 Intuition (Feynman first)
Excess return is a body with forces on it. The benchmark is **gravity** (free market pull). On top, the manager applies forces by deviating from the index: *which buckets* he over/under-weighted (allocation), *which names inside each bucket* (selection), the *synergy* (interaction), how *aggressively he sized* (sizing/conviction), and the **friction** of trading (cost drag). A *style wind* (size/value/momentum/quality tilts) blows the body along — a **re-labelling** of the same forces, not an extra one; the factor lens subtracts the wind so only the manager's own *thrust* (true alpha) remains.

The deepest trap: **these lenses overlap** — allocation, selection, sizing, factor-beta are four decompositions *of the same $\sum a_i r_i$*. Add them naively → triple-count. The identity is built so the pieces **sum exactly to realized excess return with no double-count**; each lens is flagged *partition* (adds up) vs *re-projection* (alternative view of the same dollars).

### 1.2 The rigorous identity

**Decomposition I — Brinson partition (primary, exact, additive).** Group names into buckets $g$ (sector, OR cap band, OR style tercile — one grouping per run):

$$A = \underbrace{\sum_g (w_g - W_g)(b_g - R_b)}_{\text{ALLOCATION}} + \underbrace{\sum_g W_g (r_g - b_g)}_{\text{SELECTION}} + \underbrace{\sum_g (w_g - W_g)(r_g - b_g)}_{\text{INTERACTION}}$$

- **Allocation** = "did I overweight the buckets that beat the index?" ($-R_b$ = **Brinson-Fachler** refinement → an index-weight bucket contributes exactly 0).
- **Selection** = "inside each bucket, did *my* stocks beat the *index's* stocks?" (held at benchmark weight $W_g$ so picking is scored independent of bet size).
- **Interaction** = "did I concentrate where my picking was good?" **Kept as its own line, never folded into selection** — folding a negative interaction into selection is exactly how a bad sector tilt gets mislabelled "good stock-picking."

*Closure is exact* (proof: the allocation $-R_b$ piece sums to $-R_b(1-1)=0$, the rest telescopes to $w_g r_g - W_g b_g = R_p-R_b$). **The closure residual is the build's self-audit: <1 bp/yr after linking, or the attribution is wrong.**

**Decomposition II — Sizing split (a RE-PROJECTION, not an addend).** Since $\sum a_i=0$, $A=\sum_i a_i(r_i-\bar r)\approx N\cdot\mathrm{Cov}(a_i,r_i)$ — active return IS the cross-sectional covariance of bet size with realized return (Lo 2008).
$$\text{SizingSkill} = \underbrace{\sum_i a_i r_i}_{\text{conviction-weighted}} - \underbrace{k\sum_i \text{sign}(a_i)\,r_i}_{\text{sign-only, risk-matched}},\quad k=\tfrac{\sum_i|a_i|}{N}$$
Positive ⇒ sizes by edge; ≈0 ⇒ right signs, random sizing; negative ⇒ over-sizes losers (over-confidence). Optimal $a_i^\star\propto\mu_i/\sigma_i^2$ (Kelly/Markowitz). **Reported alongside Brinson, never added.**

**Decomposition III — Factor deflation (RE-PROJECTION; "effect" → "skill vs beta").**
$$r_p-r_f = \alpha + \sum_f b_f F_f + e,\quad F\in\{\text{MKT,SMB,HML,WML,QMJ,BAB}\}$$
$\sum b_f F_f$ = the part a cheap ETF replicates (the "style wind"); $\alpha$ = the only part worth an active fee. **Anti-double-count law:** allocation/selection/interaction *partition* the return ("where did the money come from"); factor-$\alpha$ *deflates* it ("how much is skill"). The skill verdict is **factor-adjusted selection/$\alpha$** — never sum a Brinson allocation effect and a factor SMB return into one ledger.

**Decomposition IV — Trading / cost drag (the one genuinely additive separate force).** Everything above is **start-of-period holdings × forward returns** = *paper* return. Realized **NAV** differs by trading + frictions — the **return gap** (Kacperczyk-Sialm-Zheng 2008):
$$\text{gap}(t)=R^{\text{NAV}}(t)-\Big(\sum_i w_i(t)r_i(t)-\text{TER accrual}\Big)$$
= value of unobserved intra-snapshot trading − cost drag. Cost drag $\approx 2\times$turnover$\times c$ ($c\approx$10–15 bps + impact). **Trading earns its keep only if timing alpha > drag.**

### 1.3 The consolidated free-body equation
$$\boxed{\,R_p = R_b + \big[\text{Alloc}+\text{Sel}+\text{Inter}\big] + \text{return gap}\,}$$
with **two orthogonal audits on $A_{\text{paper}}$, not added to it**: the **sizing re-projection** (sign vs magnitude) and the **factor deflation** (cheap beta vs true $\alpha$).

### 1.4 Three things that must be right or the identity silently breaks
1. **Equity renormalisation** — strip debt/cash/foreign via `investment_type`, renormalise $\sum w_i=1$ over the equity sleeve, else hybrids show a phantom "cash allocation effect" and the identity won't close.
2. **Multi-period geometric linking** — single-month Brinson terms DON'T sum across 13 years (returns compound). Scale by Cariño (1999) $k_t=\frac{\ln(1+R_{p,t})-\ln(1+R_{b,t})}{R_{p,t}-R_{b,t}}$. Skipping it dumps a compounding residual into "selection."
3. **Drift-adjusted trades** — real traded change $\Delta w_i^{\text{traded}}=w_i(t{+}1)-w_i(t)\frac{1+r_i}{1+R_p}$ (after split-adjusting shares). Raw $\Delta w$ confuses the manager's decision with the market's move.

---

## 2. The metric set

Feasibility legend: **NOW** = computable today; **FACTOR-LIB** = needs the India factor-return library (§6 Phase 2); **TENURE** = needs the manager↔scheme↔dates DB; **W-HIST** = needs point-in-time benchmark constituent *weights*.

### 2.1 KV's Vantage-Point metrics — kept faithfully, then fixed

| KV metric (kept) | How it is FOOLED | The IMPROVEMENT | Feasibility |
|---|---|---|---|
| **Hit rate** (count $r_i\ge R_b$ / N) | magnitude-blind (70% tiny wins + 30% catastrophic still "wins"); wrong null (~0.46–0.49 not 0.50 — median stock lags a cap-weighted index); benchmark-naive per name; $n_{\text{eff}}\ll N$; no luck bar | **cross-sectional rank-IC** $IC_t=\text{Spearman}(a_i,r_{i,\text{fwd}})$, skill = $\overline{IC}$ with **Fama-MacBeth t** $=\overline{IC}\sqrt T/s_{IC}$; keep a **magnitude-weighted hit rate** (fraction of active-return *dollars* from correct bets); empirical null; $n_{\text{eff}}$ | **NOW** |
| **Slug rate** (top-Q − bottom-Q held) | **look-ahead by construction** (quartiles from the returns being scored); tilt masquerade; coarsened IC; weight-agnostic | retire as a signal, keep as a **labelled ex-post diagnostic**; quartiles on full eligible universe within sector/cap cohorts (residual return); decile-spread vs active-weight rank + t | **NOW** |
| **Allocation benefit** (NAV − **equal-weight** basket) | **equal-weight is itself a size bet** → "NAV−EW" flips sign with the small-cap cycle; correct large-cap sizing reads as negative skill | report **3 counterfactuals**: vs **cap/benchmark-weight** (primary, strips size factor), vs EW (KV's, *label* "incl. size factor"), and EW−cap spread (= size-factor contribution); the clean object is the **Brinson allocation term** | **NOW** (EW & cap); benchmark-wt = **W-HIST** |
| **Tie-breaker-day** (\|bench\|>2% days) | **a beta thermometer, not skill** (low-beta names win down-days mechanically); magnitude-blind; tiny clustered sample; look-ahead | **beta-neutralise** (vs CAPM-expected move, split up/down stress); recast as **exposure timing** $\mathrm{Cov}(\Delta\text{exposure}_t,\text{next big move})$; block-bootstrap CI over episodes | **NOW** |
| **Peer quantile bands** (eyeballed) | **a rank is not significance** (someone is always top-quartile; max of 20 noisy draws ≈97th pct); no factor control, no FDR; survivorship-tilted | **placebo NULL** (bootstrap percentile vs ≥10k tilt-matched twins) + **Benjamini-Hochberg FDR** across 777; keep the band as descriptive only | **NOW** |

### 2.2 Net-new metrics
Brinson 3-way multi-period linked (closure <1 bp/yr) · cross-sectional IC + Fama-MacBeth t · sizing skill (sign vs magnitude) · realized IR + implied breadth $BR_{\text{eff}}=(IR/IC)^2$ · **Active Share** $\tfrac12\sum|w_i-W_i|$ (closet-indexer: $AS<0.6$ ⇒ no real selection) · **Carhart $\alpha$ + Newey-West t** + the $\alpha$ waterfall (CAPM→−SMB→−HML→−WML→−QMJ) · holdings-based point-in-time factor loadings · trade-timing IC + Add-vs-Trim spread · return gap · concentration (Herfindahl) vs IR · **bootstrap percentile-vs-null** (the luck bar on every metric).

---

## 3. The skill-vs-luck gate

Master identity: $\boxed{t = IR\cdot\sqrt{\text{years}}}$. *Derivation:* $t=\bar m/(s/\sqrt N)=(m/s)\sqrt N$, and $m/s$ annualised IS the Information Ratio. **Consequence:** an $IR=0.5$ manager needs **16 years** for $p<0.05$; $IR=1.0$ needs 4. Quadrupling history only doubles confidence. **Print `years_needed=(1.96/IR)²` beside every verdict** so "no skill" ≠ "not enough data yet."

Test stack: (1) **PRIMARY — random-portfolio bootstrap with tilt-matched twins** (≥10k twins holding the same # names drawn within the manager's own cap/sector/style buckets, so only *selection* is stripped; skill ⇒ actual ≥95th pct of its own null). (2) **t=IR√years with Newey-West HAC** (lag ≈ window overlap; naive OLS inflates t ~4×) + **OOS persistence** (rank 2013–19, test 2020–25). Pass = bootstrap AND (t≥2 OR OOS). (3) **factor survival** ($\alpha$ > fee after MKT+SMB+HML+WML+QMJ+BAB). (4) **book-level FDR** (Fama-French 2010; Barras-Scaillet-Wermers 2010 — 777 funds at p<0.05 ⇒ ~39 chance winners; control FDR ≤10%; skill = the *whole right tail shifted*).

**Minimum-data rule, stamped on every verdict.** Manager-level skill is **BLOCKED until the tenure DB exists** (a scheme spanning 3 managers gives an uninterpretable t).

---

## 4. Strengths / weaknesses diagnosis — the manager's fingerprint
- **Great selector, poor sizer** — high IC, *negative* SizingSkill (over-sizes losers, anti-Kelly). Overcome = move weights toward $\mu_i/\sigma_i^2$.
- **Alpha is just a momentum/size tilt** — $\alpha$ waterfall collapses to ~0 on full FFC+QMJ; $R^2>0.95$. Overcome = it's a closet factor product → buy the factor as an ETF.
- **Closet indexer** — $AS<0.6$ + low TE; selection can't matter. Overcome = take real active risk or stop charging.
- **Sector-timer, not stock-picker** — allocation ≫ selection; a beta-tilt edge → must clear factor survival.
- **Lucky tail** — passes one t>2 but fails bootstrap + OOS + FDR. Nothing to overcome; noise.

Legible via two 2×2s: **Active Share × Tracking Error** and **IC × Sizing-skill**.

---

## 5. AMC team construction — the portfolio-of-managers
Single manager: $IR=TC\cdot IC\sqrt{BR}$ (Grinold 1989; Clarke-de Silva-Thorley 2002 — transfer coefficient for long-only/capacity). Treat each manager's alpha stream as an asset (Markowitz):
$$IR_{\text{AMC}}=\frac{\sum_m k_m\mu_m}{\sqrt{\sum_{m,n}k_m k_n\rho_{mn}\sigma_m\sigma_n}}$$
Uncorrelated equal-skill ⇒ $IR_{\text{AMC}}=s\sqrt M$ (skill diversifies one level up, alpha-correlation = the new "independence"); **crowding ($\rho\to1$) kills $\sqrt M$** — pay $M$ desks for one manager's breadth. Recipe: (1) score on **factor-RESIDUAL** alpha; (2) build $\rho_{mn}$ + a **holdings-overlap** twin $\sum_i\min(a_{i,m},a_{i,n})$ (the "diversification of skill" object none of the 5 Vantage-Point metrics sees); (3) optimise $k^\star\propto\Sigma^{-1}\mu$ with **capacity caps** (Berk-Green 2004) + **Ledoit-Wolf shrinkage**; report marginal $\partial IR/\partial k_m$ + single-star fragility; (4) **walk-forward validate** vs naive equal-weight-of-all-managers OOS. **"Best combination" ≠ the M highest scorecards** — up-weight uncorrelated alpha, down-weight a brilliant clone of someone already on the team.

---

## 6. Build order on our data
**Phase 0 (verified):** holdings_history ⋈ TR returns ⋈ index constituents ⋈ cap/fundamentals; 99.4% equity value identified.
**Phase 1 (NOW, no new data):** core identity + holdings-based Brinson selection; IC + Fama-MacBeth t; magnitude-hit; SizingSkill; realized IR + breadth; concentration-vs-IR; drift-adjusted trade-timing + Add-vs-Trim; turnover + cost drag + return gap; **the bootstrap luck bar + OOS + Newey-West + FDR (build this EARLY, not last)**.
**Phase 2 (one build):** **India factor library** (MKT,SMB,HML,WML,QMJ,BAB) from our TR + mcap + fundamentals — highest-leverage missing piece; unlocks FFC $\alpha$ + waterfall + holdings loadings + the factor-adjusted twin of every metric.
**Phase 3 (new data):** time-varying vst_id→industry history; point-in-time benchmark constituent WEIGHTS; per-scheme TER history.
**Phase 4 (binding gap for PERSON-level):** **manager↔scheme↔dates tenure DB** — everything in 1–3 is SCHEME-level; until this exists, manager-level numbers are "scheme-level proxy, manager-change-contaminated."
**Caveats on the dashboard, not buried:** equity-only sleeve (~10% excluded); month-end snapshots (intra-month invisible — the return gap *measures* it); scheme survivorship (777 are today's survivors — join a closed-scheme registry); **stock-panel survivorship (delisted names must keep terminal returns — the #1 bias; verify first)**.

---

*Canonical sources:* Brinson-Hood-Beebower (1986), Brinson-Fachler (1985), Brinson-Singer-Beebower (1991), Cariño (1999), Menchero (2000/04), Grinold (1989), Grinold-Kahn (1999), Clarke-de Silva-Thorley (2002), Cremers-Petajisto (2009), Lo (2008), Kacperczyk-Sialm-Zheng (2008), Daniel-Grinblatt-Titman-Wermers (1997), Almgren-Chriss (2000), Carhart (1997), Fama-French (1993/2015/2010), Jensen (1968), Sharpe (1992), Asness-Frazzini-Pedersen (2019), Frazzini-Pedersen (2014), Kelly (1956), Markowitz (1952), Efron (1979), Kosowski-Timmermann-Wermers-White (2006), Politis-Romano (1994), Barras-Scaillet-Wermers (2010), Benjamini-Hochberg (1995), Newey-West (1987), Berk-Green (2004).

---

## LEAD ADVERSARIAL REVIEW (KS, 2026-06-23) — corrections before build

The blueprint is sound; I endorse the spine (the $A=\sum a_i r_i$ identity, partition-vs-re-projection discipline, the $t=IR\sqrt{\text{years}}$ gate, tilt-matched bootstrap, team-construction Markowitz). Five corrections/flags:

1. **★ Returns input MUST be the TOTAL-RETURN store, not our price-return stock panel.** Phase 0 says join to "Stocks Data TR" — but we just *proved* our NSE stock CSV is **price-return** (−4.6 bp/mo vs Bloomberg TR = missing dividends). Attribution must use `tr_returns_monthly.parquet` (BBG TR) or our *computed* NSE TR — NOT the price panel — or every selection/α number under-credits dividend-payers. (Wire `r_i` = total return, per the §0 definition, end-to-end.)
2. **★ The BBG TR store SOLVES the "#1 bias" the doc flags.** Stock-panel survivorship (delisted names must retain terminal returns) — our BBG TR matrix *includes* delisted tickers (the +131 delisted names + the dead BBG columns). So the worst bias in fund-skill studies is already mostly handled *because* of the BBG enrichment; verify the delisted terminal returns are present (they are) and use that store, not a survivors-only panel.
3. **Benchmark IDENTITY per scheme is underspecified and load-bearing.** $A=R_p-R_b$ is only as right as $R_b$. SEBI category → index is imperfect (flexicap vs multicap vs focused). Use the scheme's **stated SID benchmark** where available, category default otherwise, and run a **benchmark-sensitivity check** — a wrong benchmark contaminates allocation AND selection.
4. **India factor library quality is time-limited.** FFC-$\alpha$ deflation needs point-in-time fundamentals; our clean fundamentals start ~2015 (legacy backfill to 2000 is lower quality). So the factor-survival gate is firm post-2015, weaker before — stamp the α verdict with its factor-data confidence by era.
5. **★ The tenure DB (Phase 4) is the BINDING constraint for KV's actual questions.** KV asked about *fund managers'* strengths/weaknesses and the *best combination of managers* — both are PERSON-level. Everything Phase 1–3 is SCHEME-level. So while scheme-level attribution delivers huge value first, the **manager-tenure DB is the critical path to the headline questions** and should be scheduled in parallel, not deferred to "Phase 4 someday." Build a scheme-level engine now; start the manager-tenure scrape concurrently.
