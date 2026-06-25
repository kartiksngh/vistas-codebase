# Vistas Analyst Goldmine — Design Blueprint
### "The network of analysts, made legible"

The **Analyst persona** of the Vistas CIO stack. It turns LSEG StarMine's **Analyst Revision Model
(ARM)** — a 0–100 regional percentile of how analyst estimate-revisions are moving — from a per-stock
card into a **two-level intelligence**: (1) which **sectors/themes** are being upgraded/downgraded, how
hard and how fast, vs the sector's *own* history; and (2) **inside** a turning sector, which **stocks**
lead vs lag the revisions and what they have in common. It sits below the CIO synthesis
([[CIO_INTELLIGENCE]]) and beside the Fund-Manager persona ([[FUND_MANAGER_ANALYSER_DESIGN]],
[[FM_INTELLIGENCE]]). It reuses the Market-Forces Mesh substrate ([[MESH_DESIGN]]).

Status: **design blueprint** for #46 (Analyst Consensus Flow panel) and the deeper Goldmine engines.
Stamped 2026-06-26. House discipline: every number is **historical-data-based, rule-based, no
curve-fit**, every score **self-explaining** (Definition · Method · Why · is-it-fair · proven-if-predictive),
and anything not yet backtested ships as **diagnostic context, never an actionable**.

---

## 0. The one sentence the whole thing rests on

> **A revision is information arriving. The LEVEL of ARM tells you where the consensus already is
> (context, often already priced); the CHANGE in ARM tells you that the consensus is *moving* (the edge);
> and the edge has a CLOCK — it works over ~1–6 months and decays, so a stale revision is not a signal.**

This is the same organizing law the Mesh forces obey ([[MESH_DESIGN]] §0): **LEVEL = context, CHANGE =
edge, every edge has a CLOCK.** It is why the Goldmine stores every analyst quantity as a triple
`{level, change, percentile}` and stamps every actionable with a horizon. Burned-in caveat: our own ARM
information coefficient is **IC ≈ 0.03–0.045 at a 1-month horizon** (`arm_backtest.py`) — *same order of
magnitude* as StarMine's published India figure (~0.05), **NOT a replication**, and it is a **portfolio
tilt, not a per-name guarantee** (single-name hit-rate ≈ 50%).

*Tiny worked example.* Two cement names both sit at ARM = 70 (high level — analysts already like them).
One has been at 70 for six months (change ≈ 0 → nothing new, no edge); the other jumped 45 → 70 over the
last month (change = +25 → consensus is *turning up now* → that is the tradeable event). Same level,
opposite signal. The Goldmine must never confuse the two.

---

## 1. What ARM actually is (so the rollup is honest)

ARM (StarMine **Analyst Revision Model**) scores a stock 0–100 by where the **momentum of analysts'
estimate revisions** ranks within its region. Mechanically: take the recent direction and magnitude of
changes to broker estimates (more weight to bolder, more-accurate, more-recent analysts), and rank it
cross-sectionally. 100 = revisions surging up vs peers; 0 = collapsing. It is a **revision-momentum
percentile**, not an estimate level.

- **Composite:** `ARM_100_REG` is a **non-linear blend** of four components — it is **NOT a sum or mean**
  of them. We display the composite as the headline and the four components as the anatomy:
  - `ARM_REVENUE_COMP_100` — **sales / revenue** revisions.
  - `ARM_PREF_EARN_COMP_100` — **EPS / preferred-earnings** revisions.
  - `ARM_SEC_EARN_COMP_100` — **EBITDA / secondary-earnings** revisions.
  - `ARM_REC_COMP_100` — **recommendation / rating** revisions (broker up/downgrades).
- **Clock:** revision serial-correlation is low (~0.15) — ARM is a **fast** signal (refresh weekly; #35).
- **Asymmetry (motivation, not a trade rule):** StarMine reports IC ≈ **+0.118** when a revision *continues*
  and ≈ **−0.155** when it *reverses*. The −0.155 is the **cost of being wrong about persistence**,
  measured *knowing the next revision reverses* — it is **not an ex-ante short rule**. It is why we read
  *direction* and refresh often; it does not say "short when ARM is low."

**Coverage / identity (from the feasibility pass).** Our compiled India ARM cache (`vistas/arm.py`,
git-ignored `arm_repo/`) holds **~1,924 mapped stocks, 1998–2026**, weekly. A **sector tag** exists for
~724 of them — the NIFTY-500-investable cohort — which is the universe the rollup runs on. Identity is on
`vst_id` (the spine), mapping verified ~87.6%; an ARM stale > 90 days is flagged *not-recommending*.

**★ Feasibility ceiling — read before promising the "deep engine."** Our dump carries **NO forward
estimate LEVELS** (`multihorizon = NONE`): we have ARM percentiles and the four component percentiles,
densely through time, and that is *all*. So the parts of KV's "super-consensus term structure" that need
*forecast levels at multiple horizons* (e.g. FY1 vs FY2 vs FY3 EPS, implied growth term structure) are
**not buildable from current data** — they are a **data-acquisition task**, not a modelling task. What we
*can* build from ARM alone is described below and is substantial. The raw StarMine dump *also* carries
**earnings surprise + actuals + pre-announcement dates** that the current `KEEP_MNEM` filter discards —
**addable** (a one-line widening of the keep-list + a re-bake), and that is the cheapest route to a real
multi-horizon view.

---

## 2. LEVEL 1 — sector / theme consensus flow (the #46 panel)

**Goal:** "Which sectors/themes are getting upgraded or downgraded — in the composite AND in each
component (sales, EPS, EBITDA, rating) — how *intense* and how *fast*, and is this *normal* for the sector
or an *event*?" On **two weighting bases**, with a **historical flow** view.

### 2.1 The two bases — why both, stated plainly

- **Equal-weight (EW) — breadth.** Every stock in the sector gets one vote. Answers: *"are analysts
  upgrading the sector broadly, or is it one mega-cap dragging the average?"* This is the breadth read.
- **Free-float-market-cap (FF) — money-weighted.** Each stock weighted by `mcap × free_float`, where
  free-float is proxied `mcap × (1 − promoter%)` (the same convention as the reconstructed benchmarks,
  [[BENCHMARK_PORTFOLIO_PLAN]] / `vistas/benchmarks.py`). Answers: *"where the investable money actually
  is, is the consensus improving?"* This is the index-relevant read.

**The gap between EW and FF is itself a signal** and we surface it: EW ≫ FF = the *small/mid* names in the
sector are being upgraded while the heavyweights lag (early-breadth / down-cap rotation); FF ≫ EW = the
*leaders* are being upgraded while the tail lags (narrow, top-heavy).

### 2.2 The exact aggregation (reproducible)

For sector `g`, basis `b ∈ {EW, FF}`, at month `t`, for each ARM series `s ∈ {composite, revenue, EPS,
EBITDA, rec}`:

- **Definition (LEVEL):** `ARM_g,b,s(t) = Σ_{i∈g} ω_{i,b} · ARM_{i,s}(t)`, where `ω_{i,EW} = 1/N_g` and
  `ω_{i,FF} = ff_i / Σ_{j∈g} ff_j`, `ff_i = mcap_i · (1 − promoter%_i)`.
- **Method / data source:** `ARM_{i,s}(t)` from the compiled ARM cache (`vistas/arm.py`,
  `_arm_symbol_series`, ff-filled to month-ends); sector tag + `mcap`/`promoter%` from the per-stock
  store (`stock_intel` / `fundamentals`). Row granularity = one (stock, month). Min-N guard: a sector with
  `N_g < 5` covered names renders as *thin — not scored* (no fabricated average).
- **CHANGE:** `ΔARM_g(t,k) = ARM_g(t) − ARM_g(t−k)` for `k ∈ {1M, 3M}` (1M = the inflection, 3M = the
  smoother *direction*). This is the **edge** quantity.
- **Why an average of percentiles is legitimate here:** ARM is already a *cross-sectional rank*, so the
  sector mean is a "breadth of revision-momentum" reading — interpretable as *how much of the sector is
  in revision up-swing*. We label it exactly that, never as "the sector's expected return."

**Caveat carried on the panel (per reporting discipline):** because ARM is a regional percentile, a
sector mean near 50 is the *neutral* point by construction, not "neutral fundamentals." Reading is always
**relative** (this sector vs other sectors; this sector now vs its own history), never absolute.

### 2.3 Normal vs event — vs the sector's OWN history (the "cyclical-vs-event" read)

A sector at composite ARM = 68 means nothing until you know its history. So next to the level we show its
**own-history percentile**: `pctile_own(ARM_g(t))` over the sector's trailing ≥3-year monthly series.

- **High level + high own-history pctile + small change** → *cyclically extended* (analysts already
  maximally bulled — a spent / crowding context, à la the LEVEL-is-context law). Posture-relevant for the
  Goldmine recommender: this is **not** a fresh add.
- **Mid level + large positive change + own-history pctile rising fast** → *event / inflection* (the
  consensus is turning **now** — the tradeable case).
- **The catalyst tag (descriptive):** decompose *which component* drove the change — a jump led by
  `ARM_REC` (ratings) reads as a *re-rating event*; led by `ARM_REVENUE`/`ARM_EBITDA` reads as a
  *fundamental/demand* event. We **label** the dominant component as the apparent catalyst; we do **not**
  claim to have identified the news.

### 2.4 The historical flow ("flowchart")

A **sector × month heatmap** of `ARM_g(t)` (and a toggle for each component and for `ΔARM`), EW and FF
side-by-side, 1998→now where coverage allows. This is the "flowchart of how analyst expectations flow
through sectors over time" — read top-to-bottom you see *rotations* (consensus draining one sector and
filling another). It is **purely descriptive** (a re-display of historical ARM), so it ships immediately
with no validation gate — it makes no forward claim.

---

## 3. LEVEL 2 — within-sector leaders, laggards, and their fingerprint

Once a sector is turning, *"which stocks lead the revision and what do they have in common?"*

- **Lead/lag ranking (Definition):** within sector `g`, rank held names by `ΔARM_i(t,3M)` (the 3-month
  revision change). Leaders = top tercile of revision change; laggards = bottom tercile. **Method:** same
  ARM step-series; row = (stock, month); reported as the named lists + their `{level, change}`.
- **The common fingerprint (Definition):** for the leader set vs the laggard set, summarise the
  **distribution** of the Mesh forces and fundamentals already baked per stock ([[MESH_DESIGN]] §1):
  valuation cheapness percentile, PAT growth & acceleration, quality score, smart-money flow
  (cross-AMC net active flow), breadth change, FII/DII rotation. **Why:** it answers "are the upgraded
  names the *cheap* ones, the *high-growth* ones, the ones *smart money is already buying*?" — i.e. it
  characterises the *type* of revision (a value re-rate vs a growth chase vs an informed-flow confirmation).
- **Lead-lag with flow (the confirmation cross):** cross the revision-leaders with the smart-money-flow
  state. **ARM turning up + funds already accumulating** = the `CONVICTION_ADD` confluence (S1); **ARM
  turning up + funds NOT yet buying** = `STREET_AHEAD` (S2/S3 — analysts ahead, watch for catch-up or
  trap). These are the Mesh signals — the Goldmine *is* the analyst lens onto the Mesh.
- **Fairness note:** "leader/laggard" here is **descriptive of the revision**, not a forward prediction
  per name. The forward claim only attaches after the §5 gate, and even then as a *tilt*.

---

## 4. The deep engine — what's buildable, what's gated, what needs data

KV's vision named three deep capabilities. Honest status of each:

### 4.1 Super-consensus "term structure" — **PARTIAL (data-gated)**
- **Aspiration:** read the consensus at multiple forward horizons (FY1/FY2/FY3) as a *term structure* and
  trade its shape.
- **Reality:** we have **no forward estimate levels** (§1). So the true term structure is **not buildable
  today.** The *honest* analogue we can build now = the **ARM-change term structure across lookbacks**
  (1M vs 3M vs 6M change) — i.e. is the revision *accelerating or fading* — plus the **four components as
  a within-stock cross-section** (revenue vs EPS vs EBITDA vs rating revision can disagree; the spread is
  informative: revenue up but EPS flat = margin-pressure flag). **Unlock path:** widen `KEEP_MNEM` to pull
  StarMine **surprise + actuals + pre-announcement dates** (and, if licensed, the SmartEstimate forward
  levels) → then a genuine term structure becomes a modelling task. Flagged as a data acquisition item,
  not promised from current data.

### 4.2 Historical-analog conditioning — **BUILDABLE, GATED**
- **Plain idea:** "When this sector's revision-state looked like *this* before, what did prices do next?"
- **Defensible construction (no curve-fit):** define a **state vector** per (sector, month) from the
  rule-based quantities only — `{ARM level pctile_own, ΔARM_3M, EW–FF gap, dominant component, breadth
  change}`. For a query state, find its **k nearest historical neighbours** (same or other sectors) by a
  fixed distance metric, and report the **empirical distribution of forward 3/6/12M sector TR** in those
  neighbours. This is a **nearest-neighbour conditional**, not a fitted model — its parameters (which
  fields, k, distance) are pre-registered, and it must clear the §5 gate (the conditioned forward
  distribution must be *materially different* from the unconditional one, out-of-sample, or it ships as
  descriptive only). **Trap to avoid:** over-fitting the state vector until analogs look predictive —
  guard with a fixed, small, pre-registered feature set and an OOS split (fit the metric on 1998–2015,
  test 2016–26).

### 4.3 The "Instagram-like recommendation" → bet posture per sector — **GATED**
- **What KV means:** an algorithm that, like a feed-ranker, weighs many signals into a **posture per
  sector** — *aggressive / neutral / benchmark-weight / defensive / exit* — and then surfaces the stock
  picks within the aggressive sectors.
- **Defensible construction:** the posture is a **deterministic rule map** over the *validated* Mesh
  signals, **not** a learned black box. Skeleton (each clause carries its own gate):
  - **Aggressive** = sector `ΔARM_3M` top-quintile **AND** flow confirming (`CONVICTION_ADD` breadth) **AND**
    own-history pctile *not* already maxed (room to run).
  - **Defensive / Exit** = `CROWDED_REVERSAL` confluence at the sector level (breadth at own-peak +
    decelerating flow + `ΔARM < 0`) — the de-risk flag, itself gated.
  - **Benchmark-weight (default)** = no confluence fires → no view → hold the index weight (the honest
    null; we do **not** manufacture a view).
  - **Neutral** = mixed/contradictory forces.
- **Why this is defensible and not curve-fit:** every clause is one of the Mesh signals that **passed its
  own signal-backtest** ([[MESH_DESIGN]] §5); the posture is just their **logical combination**, and the
  *combination itself* is backtested as a whole (posture-weighted sector rotation vs equal-weight-sectors,
  all-starts × ≥10k random, ≥5y windows) before it can drive a card. **Until that passes it renders as a
  "what the rules currently say" diagnostic, explicitly unproven.** Stock picks within an aggressive
  sector = the §3 revision-leaders crossed with flow-confirmation — again a tilt, not a per-name promise.

---

## 5. The defensibility gate (shared with the Mesh)

No Goldmine actionable ships until it clears, on **our** TR panel:

1. **Signal-backtest** ([[signal-backtest]] discipline): score deciles vs forward 3/6/12M TR, **all start
   months**, **≥10k random portfolios** as the null, judge the **distribution of percentile-vs-random
   across starts** (not one path, not CAGR alone), fixed ≥5y windows, **survivorship-free** (dead names
   retained in the research panel).
2. **Controls (mandatory):** must survive a **size + value** control and must **beat the strongest single
   component** — a sector composite that doesn't beat "rank by `ΔARM_3M` alone" is repackaging.
3. **Lead-lag / Granger** for anything crossed with flow: the flow must *precede* the return, not co-move
   (kills the trend-chasing mirage).
4. **Sign-replication, not magnitude-import:** we replicate the *sign* of the literature on our data; we
   never quote a foreign magnitude (Ambit's ~9% `value × revision` is **their** BSE200 number on **their**
   raw 6M-EPS-revision factor — ARM is a *different, richer* proxy; the 9% will **not** transfer 1:1).

Everything that fails or is un-run is **labelled diagnostic / unproven** and rendered as context, never a
buy/sell. "No score for error."

---

## 6. Build order, logs, resumability

**Phase A — the #46 Consensus-Flow panel (descriptive, ships first, no gate needed).**
1. `vistas/arm_sectors.py` (NEW): read the ARM step-series + sector tags + ff weights → bake
   `data/_arm_flow/<sector>.json` = `{months, composite{EW,FF}, components{revenue,EPS,EBITDA,rec}×{EW,FF},
   change_1m, change_3m, pctile_own, n_covered, dominant_component}` and a compact `_index.json`.
   Reuse `_arm_symbol_series` (no new fetch). Display-plane, Python-baked, **no JS-parity port**
   (`analytics.py` untouched) — consistent with the Mesh discipline.
2. Front-end panel: sector table (sortable by level / Δ1M / Δ3M / own-pctile), EW⇄FF toggle, component
   toggle, the historical heatmap, the EW–FF-gap column, drill-through to §3 within-sector leaders. Each
   number carries its Definition·Method·Why tooltip; min-N and stale-ARM guards visible.
3. Probe (`_pup_*`) + spot-check 5 sectors vs hand-computed averages.

**Phase B — Level-2 within-sector fingerprint** (reuse the baked Mesh per-stock forces; descriptive).

**Phase C — gated engines** (analog conditioning §4.2, posture map §4.3): build the metric → run the §5
gate → ship only on pass, else keep as labelled diagnostic.

**Phase D — data unlock** (widen `KEEP_MNEM` → surprise/actuals/pre-announce; pursue forward levels) → then
the real term structure §4.1.

**Logs & resumability (KV's standing requirement):** every phase appends to **`ANALYST_GOLDMINE_LOG.md`**
(what was built, the gate result with the exact backtest numbers, the verdict, the next step) and the
in-repo `MEMORY.md` resume point is updated. Each gate's backtest artifacts (decile spreads, null
percentiles, IC series) are written to `data/_arm_flow/_backtests/` so a verdict is reproducible without
re-running. Nothing mutates the validated engines — every step writes new `data/_arm_flow/*` and reads
existing JSONs, so any step is reversible.

---

## 7. FLAGGED — what must not become a silent assumption

1. **No forward estimate levels** (`multihorizon = NONE`) — the full "term structure / super-estimate"
   vision needs **new data** (widen `KEEP_MNEM`; possibly license SmartEstimate). Do not promise it from
   the current dump.
2. **Sector tag covers ~724 of ~1,924 ARM names** — the rollup is the NIFTY-500-investable cohort; the
   tail is unscored. State coverage on the panel.
3. **ARM IC ≈ 0.03–0.045 @1M is same-order-of-magnitude as StarMine's ~0.05, NOT a replication** (already
   corrected once after KV pushback) — keep the caveat; it is a **tilt, not a per-name guarantee**.
4. **The −0.155 reversal IC is the cost of being wrong about persistence, not an ex-ante short rule.**
5. **Averaging percentiles** gives a *breadth-of-revision* reading, not an expected return — label exactly.
6. **FF weight uses `mcap × (1 − promoter%)` as a free-float proxy** (no exact free-float feed) — a proxy,
   stated; reconcile against `vistas/benchmarks.py` so the Goldmine and the benchmarks agree.
7. **Ambit's `value × revision` ~9% is theirs, not ours** — a hypothesis to backtest, never an in-house
   number; ARM ≠ Ambit's revision factor.
8. **The posture map is a rule combination of *already-validated* signals** — the combination itself still
   needs its own backtest before it drives a card; until then it is "what the rules say," unproven.

*Canonical sources:* StarMine ARM methodology (LSEG); Chan-Jegadeesh-Lakonishok (1996, revision momentum);
Womack (1996, recommendation revisions); Gleason-Lee (2003); Da-Warachka (2011); La Porta (1996) &
Bordalo-Gennaioli-Shleifer (diagnostic expectations) for the over-optimism fade; Ambit (2026, value ×
revision, India) — *sign-replication only*; and the project's own `arm_backtest.py` IC evidence.
