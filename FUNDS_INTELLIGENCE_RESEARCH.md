# Funds/Stock Intelligence — research notes (what the literature says + what WE can build)

Captured during the autonomous build session (2026-06-25). Each item: the finding, the source,
whether OUR data supports it, and the persona it serves. Discipline: verify on our data before shipping.

## 1. Active Share (Cremers & Petajisto, RFS 2009)
- **Finding:** Active Share = ½·Σ|w_fund,i − w_bench,i| = the fraction of a portfolio that differs from
  its benchmark. High Active Share predicts outperformance AND persistence; the top quintile beat the
  bottom by ~2.55%/yr. Low Active Share (<~60%) = "closet indexer" — charges active fees, delivers the
  index minus costs. The "three pillars": skill, conviction, opportunity.
- **Our data:** YES (holdings present). NEED benchmark constituent WEIGHTS (have constituents from the
  Quant index work; weights via niftyindices). Until then, a proxy = active share vs the category's
  aggregate held portfolio (cross-fund consensus) — still flags closet indexers.
- **Personas:** investor (value-for-fee), CIO (manager selection), FM (positioning vs peers).
- Source: Cremers/Petajisto, "How Active Is Your Fund Manager?" SSRN 891719; RFS 22(9):3329.

## 2. Herding / anti-herding reveals skill (Verardo et al., Journal of Finance)
- **Finding:** Anti-herding funds (trade AGAINST the institutional crowd) beat herding funds by ~2.3%/yr;
  the edge persists up to 2 years and is concentrated in stocks OUTSIDE the crowd. A stock-level
  contrarian score predicts returns after controlling for known signals.
- **Our data:** YES — we already compute per-fund trades net of drift (funds_flows) and the cross-AMC
  crowd flow per stock. A fund's herding score = alignment of its trades with the contemporaneous crowd.
- **Personas:** analyst/FM (is this manager a leader or a follower?), Quant (contrarian stock signal).
- This empirically backs D#1 crowd-alignment (#19) and the stock contrarian score.
- Source: Verardo, "Does Herding Behavior Reveal Skill?" JF; LSE eprint 86372. AlphaArchitect summary.

## 3. Holdings-based stock-return signals (Fang 2024; Wermers/Cohen-Coval-Pastor lineage)
- **Finding:** Style-segment-adjusted active mutual-fund holdings predict the cross-section of stock
  returns; aggregated holdings of skilled funds carry information beyond price/momentum.
- **Our data:** YES — all-AMC holdings × TR panel × fundamentals. A "smart-money ownership" stock signal
  (breadth among high-skill funds, net flow, conviction concentration) is buildable.
- **Personas:** Quant (alpha signal), analyst (confirmation), CIO (thematic crowding risk).

## Build implications (priority, data-ready first)
- D#1 stock smart-money flow + breadth — BUILT (Quant panel). [#18]
- Per-fund crowd-alignment / herding score (leader vs follower) — [#19], research-backed.
- Active Share (closet-indexer detector) — needs benchmark weights; proxy via cross-fund consensus first.
- Stock contrarian/crowd score (Quant signal) — extend D#1; verify it predicts forward returns on OUR data
  before claiming it as edge (signal-backtest discipline: %tile vs random over fixed windows).
- Layer B rotation/turnover (churn vs alpha) — process quality. [#21]
- Manager-tenure DB (skill lives in people) — needs a new scrape. [Layer C, later]

## 4. Turnover vs alpha (validated on our panel, 2026-06-25)
- **Finding on OUR data (18-mo window):** one-way annual turnover median ~57%; Spearman(turnover, excess)
  = **+0.24, p<0.0001, monotonic** (Q1 lowest −0.23%/mo → Q5 highest +0.44%/mo). i.e. higher turnover
  COINCIDED with higher return — the OPPOSITE of the naive "churn destroys value."
- **Skeptic's caveat (why we don't ship it as edge):** contemporaneous (same-month) + regime-specific
  (2025-26 was a momentum market; trading into winners shows both high turnover and high same-month excess).
  NOT a forward-predictive skill test. So ship turnover as a **descriptive process metric** (how actively does
  this manager trade?) with the turnover×alpha quadrant as a diagnostic — never claim "churn = bad" (our data
  refutes that here). A real verdict needs a forward, cost-aware, regime-spanning test.
- **Personas:** FM/CIO (process/style description), investor (is this a buy-hold or a trader?).

## HARD RULE (KV): verify any imported idea on our data before shipping as "edge".
A signal is only edge if it survives an honest test on our panel (fixed >=5y windows, %tile vs random),
not because a paper says so. Build the metric; SHOW the evidence; label unproven ones "diagnostic, unproven".
