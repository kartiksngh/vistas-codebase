# Vistas Agentic AMC — Blueprint
### "A living, agent-run asset manager that sits on the terminal, paper-trades real mandates, and learns"

The **north-star** of the Vistas project. Everything else in the terminal (data, forces, personas, scores)
is, in the end, the **research infrastructure of a firm**; this doc describes the firm that runs on it — a
team of **agents** (sector/theme analysts, fund managers, a CIO) that cover the market, pitch, debate,
construct mandate-constrained portfolios, **paper-trade**, are scored honestly, and **improve iteratively**.
KV's framing (2026-06-26): *"a live experimental AMC fueled by our current terminal, which keeps growing —
and the analysts, FMs and CIO keep benefiting from it almost instantly."*

Status: **vision + blueprint.** Built on [[ANALYST_GOLDMINE]], [[FM_INTELLIGENCE]], [[CIO_INTELLIGENCE]],
[[MESH_DESIGN]], [[FUNDAMENTAL_LAW]], [[FUND_MANAGER_ANALYSER_DESIGN]]. Stamped 2026-06-26. Discipline:
historical-data + rule-based, **no curve-fit**, every decision self-explaining and defensible; **paper-money
only**; learning is **pre-registered** so it can never become hindsight overfitting.

---

## 0. Can it be done? — the honest verdict first

**Yes — as an experimental, paper-trading research firm — and most of the hard parts already exist.** The
three things a real AMC needs are *information*, *people*, and *a process that learns*. We map each:

- **Information** = the terminal (the ontology: `vst_id` objects, the Mesh forces, ARM, flows, ownership,
  fundamentals, benchmarks). Already built and growing daily. The agents **read** it; they never invent data.
- **People** = agents. The primitives exist: the **Agent tool** (a subagent = a staff member with a charter,
  tools, and its own context), **persistent memory** (each agent's evolving theses + track record),
  **structured output** (typed pitches/memos), **Workflow** (deterministic orchestration of a research round
  or a meeting), **Cron** (the firm's calendar — daily monitoring, weekly meetings).
- **A process that learns** = the **paper book + blotter + attribution engine** (the FM analyser, Brinson,
  the skill-vs-luck gate) + a **pre-registered thesis log**, closing the loop from decision → outcome →
  attributed lesson → updated charter.

**The one hard truth that keeps it honest:** the agents do **not** manufacture new alpha by being clever in
isolation. The *edge* lives in the terminal's **validated signals**; the agents add **coverage breadth,
cross-signal synthesis, catalyst/narrative judgment, conflict resolution, and mandate-aware construction** —
the things a research org adds on top of a data vendor. Claiming an agent "found alpha" that the terminal's
signals don't support would violate the no-curve-fit rule and is a defect, not a feature.

**The one trick that makes the learning tractable:** we don't wait years of real-time paper trading to get a
track record (the Fundamental Law's `t = IR·√years` says that's painfully slow). The terminal is a
**point-in-time time series**, so we **replay history**: each agent makes as-of-past-date decisions with
**no look-ahead**, and we score the resulting walk-forward paper track. A decade of paper trades can be
generated in a controlled backtest *before* the live-forward clock even starts — then the live paper-AMC
runs forward from today as the true out-of-sample test.

---

## 1. The firm on one page (the org, mapped to what we have)

```
                                 ┌─────────────────────────┐
                                 │   CIO  (synthesis)      │  ← CIO_INTELLIGENCE.md
                                 │  allocation · risk ·    │     escalation court · house view ·
                                 │  conflict court · book  │     book-level fragility (AMC systemic)
                                 └───────────▲─────────────┘
                       escalate (conflict /  │  house view, risk limits, sizing rulings
                        large size / risk)   │
              ┌──────────────────────────────┼──────────────────────────────┐
              │                               │                              │
      ┌───────▼────────┐             ┌────────▼────────┐            ┌────────▼────────┐
      │  FM · Scheme A  │            │  FM · Scheme B   │   …        │  FM · Scheme N  │  ← FM_INTELLIGENCE.md
      │ category mandate│            │ category mandate │            │ (one per fund)  │     (the expert-FM
      │ takes pitches · │            │ construct book · │            │                 │      constructor +
      │ owns the book · │            │ asset-alloc skill│            │                 │      reflect engine)
      └───────▲────────┘             └────────▲─────────┘            └────────▲────────┘
              │  pitches (typed), by need & urgency                            │
   ┌──────────┴───────────┬───────────────────┬───────────────────┬───────────┴────────┐
   │ Analyst · Financials │ Analyst · IT (×k)  │ Analyst · Pharma  │ Analyst · Energy …  │ ← ANALYST_GOLDMINE.md
   │ covers its universe ·│ k scales w/ #stocks│ tracks ARM/flows/ │ (sector/theme desks;│   (the super-analyst
   │ pitches FMs/CIO      │ + dispersion       │ fundamentals      │  dynamic headcount) │    on the Mesh)
   └──────────────────────┴───────────────────┴───────────────────┴─────────────────────┘
                                 ▲ reads ▲
                ┌────────────────┴─────────────────────────────────────────┐
                │   THE TERMINAL  (ontology + forces + ARM + flows + …)     │  ← grows daily; agents benefit instantly
                └──────────────────────────────────────────────────────────┘
   Support desks (cross-cutting): Risk Officer · Performance/Attribution (Ops) · Data/Quant desk
```

The three line roles are exactly the **persona stack** already designed — now instantiated as **persistent,
stateful agents** instead of one-shot analyses. Each persona doc is that role's *charter and skill manual*.

---

## 2. The roles — charter, skills, inputs/outputs, and (critically) the scorecard

Every agent is defined by five things: **Charter** (its job + mandate), **Skills/Tools** (what terminal
functions it may call), **Memory** (persistent theses + lessons + track record), **I/O** (the typed
artifacts it consumes and emits), and a **Scorecard** (how it's judged — and the Fundamental Law is the
backbone of all three scorecards).

### 2.1 Sector / Theme Analyst
- **Charter:** own a coverage universe; continuously read its stocks' Mesh state ([[MESH_DESIGN]]); detect
  when revisions/flows/fundamentals turn; form a **thesis** per name with a horizon and a catalyst; **pitch**
  the relevant FM(s) and/or the CIO when an actionable clears a conviction bar.
- **Skills/tools:** the Goldmine ([[ANALYST_GOLDMINE]]) — ARM level/change + components, EW/FF sector rollup,
  within-sector lead/lag, the Mesh forces, flows/breadth, ownership, fundamentals/valuation cycle.
- **Memory:** open theses (entry state, thesis, target horizon, what would falsify it), a thesis ledger
  (closed theses + outcome), lessons ("my IT upgrades fade after 1 quarter — refresh weekly").
- **I/O:** in = terminal reads + CIO house view; out = **Pitch** objects + monitoring **Notes**.
- **★ Scorecard = IC.** Definition: the rank-correlation between the analyst's *ex-ante* pitch direction and
  the realized forward residual return of the pitched names (Fama-MacBeth mean + `t = IC·√N`). An analyst is
  "good" at IC ≈ 0.05; the luck bar and `years_needed = (1.96/IR)²` are stamped so a short record isn't
  over-read. Also tracked: hit-rate (magnitude-weighted), calibration (were "high conviction" pitches better),
  and **decorrelation from other analysts** (a desk whose calls just echo another's adds no *breadth*).

### 2.2 Fund Manager (one per scheme)
- **Charter:** deliver **consistent category-relative outperformance with a good investor experience**
  (drawdown/consistency, not just CAGR) and a **quality, mandate-compliant portfolio**; take analyst pitches,
  apply own judgment + asset-allocation/asset-class skill, decide the book, size positions, manage turnover.
- **Skills/tools:** the FM engine ([[FM_INTELLIGENCE]]) — the expert-FM constructor (tilt the category
  benchmark toward validated forces under TE/active-share/liquidity/turnover constraints), the reflect engine
  (Brinson attribution, sizing skill), asset-allocation skills (cross-asset/macro for hybrid/multi-asset
  mandates), the benchmark library.
- **Memory:** the live book + rationale ledger (per-name reason strings), a decision journal (why I took /
  declined each pitch), lessons ("I over-sized momentum into the 2018 small-cap unwind").
- **I/O:** in = Pitches, CIO house view + risk limits, the terminal; out = **TradeTickets** (paper),
  a **DecisionJournal**, **EscalationRequests** to the CIO (on conflict or large size).
- **★ Scorecard = IR, decomposed by the Fundamental Law `IR = IC·√BR·TC`.** Realized information ratio vs the
  reconstructed category benchmark (holdings-based + NAV-based, Cariño-linked), with the skill-vs-luck gate
  ([[FUND_MANAGER_ANALYSER_DESIGN]] §3). Crucially we **decompose**: is a weak FM an **IC** problem (bad
  pitch-selection/judgment) or a **TC** problem (a sound book strangled by the mandate)? Opposite fixes. Plus
  investor-experience metrics (rolling-1y/3y win-rate, max drawdown, Ulcer) — the "good experience" mandate.

### 2.3 CIO
- **Charter:** set the **house view**; run the **escalation court** (resolve analyst↔FM conflicts, rule on
  large sizings); enforce **risk limits**; allocate across mandates where the firm runs a fund-of-mandates;
  watch **book-level & AMC-systemic risk** (crowding/fragility across schemes); chair the meetings.
- **Skills/tools:** the CIO engine ([[CIO_INTELLIGENCE]]) — the 3-lens market pulse, the AMC fragility map,
  the doable engine (priority·urgency·timeline), the team-build (combine FMs' α-streams by *uncorrelated*
  skill, not by scorecard rank).
- **Memory:** the house view + its evolution, conflict rulings + their outcomes, risk events.
- **I/O:** in = EscalationRequests, the analyst/FM scorecards, the terminal; out = **HouseView**,
  **Rulings**, **RiskLimits**, **MeetingMinutes**, allocation decisions.
- **★ Scorecard = firm-level IR + breadth + risk-adjusted consistency.** Did the CIO's combination of
  managers/views raise the *firm's* information ratio above the average manager (the team-IR = `s·√M` only if
  the desks are de-correlated — so the CIO is graded on whether they **cultivated breadth and killed
  crowding**), and did the risk map pre-empt drawdowns?

### 2.4 Support desks (cross-cutting, lightweight)
- **Risk Officer** — independent check: position/sector/liquidity limits, the fragility map, "this trade
  concentrates the firm into a crowded name." A veto/flag voice, not a P&L seat.
- **Performance / Attribution (Ops)** — runs the attribution + scorecards nightly; produces the meeting packs;
  owns the blotter and the pre-registration log (so no one grades their own homework).
- **Data / Quant desk** — owns the terminal-as-data-API for agents, flags data-quality issues, builds new
  forces; the bridge between "the terminal grows" and "the agents benefit instantly."

---

## 3. Dynamic analyst headcount — derive it, don't hand-pick it

KV's instinct (75-stock sector → ~3 analysts, <20 → 1) is right in *shape*; we make it a **rule**, then
optimize it. **First-principles driver:** an analyst's job is *coverage breadth × depth*, so headcount should
scale with the **work**, not just the stock count. Proposed coverage-load score per sector `g`:

```
load(g) = w1·N_stocks(g) + w2·dispersion(g) + w3·activity(g) + w4·AUM_relevance(g)
n_analysts(g) = clip( ceil( load(g) / CAP ), 1, n_max )
```
- `N_stocks` — names to cover (the obvious term).
- `dispersion` — cross-sectional return/fundamental spread (a tight, all-correlated sector needs *fewer*
  analysts — it's really one bet; a dispersed sector rewards more eyes). **This is the Fundamental-Law
  breadth idea applied to staffing**: don't pay 3 analysts for 1 effective bet.
- `activity` — revision/flow churn (a sector where ARM/flows move a lot needs more monitoring).
- `AUM_relevance` — how much of the firm's mandates the sector touches.
- **Optimization (not a guess):** backtest analyst-IC and coverage-staleness vs `n_analysts(g)` on historical
  replay — find the headcount where adding an analyst stops improving *de-correlated* coverage (diminishing
  breadth). Start with the heuristic; let the data set `CAP`, `w*`, `n_max`. Multiple analysts on one big
  sector get **disjoint sub-universes** (e.g. IT-largecap vs IT-midcap/ER&D) so their calls are independent
  (breadth), not echoes.

---

## 4. Cross-departmental communication — the typed protocol

Agents talk through **typed artifacts on a shared bus** (files/records the Ops desk persists), never free-form
chatter — so every message is logged, attributable, and replayable. Core message types:

| Artifact | From → To | Carries | Triggers |
|---|---|---|---|
| **Pitch** | Analyst → FM and/or CIO | name, direction, thesis, evidence (the validated forces + levels/changes), horizon, conviction, suggested size, falsifier | when an actionable clears the analyst's conviction bar |
| **Note** | Analyst → desk log | monitoring update, thesis still-valid/decaying | routine (daily/weekly) |
| **TradeTicket** | FM → blotter | name, side, target weight, rationale ledger, mandate-check result | an FM decision |
| **EscalationRequest** | Analyst/FM → CIO | the conflict or the large-size ask + both sides' cases | **conflict** (analyst vs FM disagree) OR **large size** (>X% of book / firm) OR **risk breach** |
| **Ruling / HouseView / RiskLimit** | CIO → all | the decision + reasons, or the house tilt + limits | escalation resolved / scheduled view |
| **MeetingMinutes** | chair → all | decisions, action items, scorecard deltas | every meeting |
| **ReviewNote** | Ops/Risk → agent | attributed outcome + lesson | post-trade, at review |

**Routing by need & urgency (KV's ask):** the Pitch carries `urgency ∈ {now, this-week, this-quarter}`
(derived from the signal's horizon × decay × freshness — the CIO doc's urgency≠timeline rule). `now` +
large-size routes to **both** FM and CIO immediately; routine theses go to the FM and wait for the next
meeting. **Conflict and large-size are the two hard escalation triggers** — exactly as KV specified.

---

## 5. The cadence — the firm's calendar (Cron-scheduled)

Meetings are **scheduled Workflows** (deterministic multi-agent rounds), the calendar is **Cron**:

- **Daily — desk monitoring (cheap):** each analyst sweeps its universe (the Mesh delta), emits Notes,
  fires Pitches only on real change. Cheap model for the sweep; escalate to a strong model on a fire.
- **Weekly — Analyst team meeting:** desks share top theses; the CIO surfaces the 3-lens pulse; cross-sector
  conflicts and crowding flagged; the **breadth check** (are our theses de-correlated, or is everyone long
  the same momentum?).
- **Weekly — FM ↔ Analyst:** FMs review the week's Pitches, decide/defer, log DecisionJournals; ARM-driven
  fast names refreshed (ARM clock is ~weekly).
- **Monthly — Investment Committee (CIO chair):** holdings snapshot lands → full attribution + scorecards →
  rebalance decisions, allocation across mandates, risk review (fragility map), and the **learning review**
  (§7). The big decision forum.
- **Event-driven — anytime:** an EscalationRequest convenes an ad-hoc 3-way (analyst, FM, CIO) Workflow.

**Cost governance** is a first-class constraint: cheap models + the Mesh delta for routine monitoring,
strong models reserved for pitches, conflicts, and committee decisions; meeting fan-out bounded; the firm's
token budget is a managed resource (the Workflow budget mechanic fits this exactly).

---

## 6. The decision & paper-trade loop (and how we track it)

```
 terminal state ──► Analyst thesis (PRE-REGISTERED: entry state, horizon, falsifier)
        │                      │ Pitch
        ▼                      ▼
   monitoring            FM decision (mandate-checked) ──► TradeTicket ──► PAPER BOOK + BLOTTER
        │                      │                                                │
        └──────────────────────┴───────────► outcome (forward TR vs benchmark) ◄┘
                                                       │ ATTRIBUTION (Brinson, IR=IC·√BR·TC)
                                                       ▼
                                          SCORECARDS (analyst IC, FM IR/TC, CIO firm-IR)
                                                       │ pre-registered ⇒ honest
                                                       ▼
                                          LESSON ──► agent memory / charter update
```

**State & infra (what we persist) — the "firm OS":**
- `amc/charters/` — each agent's charter + skill manifest (versioned).
- `amc/memory/<agent>/` — open theses, thesis ledger, decision journal, lessons.
- `amc/book/<scheme>.json` — the live paper portfolio (weights, cash, mandate state).
- `amc/blotter.jsonl` — every TradeTicket, append-only (the audit trail).
- `amc/prereg.jsonl` — **every thesis logged with its falsifier BEFORE the outcome is known** (the
  anti-hindsight spine).
- `amc/messages/` — the typed bus (Pitches, Escalations, Rulings, Minutes).
- `amc/scorecards/` — nightly attribution + the Fundamental-Law decomposition per agent.
- `amc/calendar.json` + Cron jobs — the meeting schedule.

**Performance tracking** reuses the built engines: the paper book is just another portfolio, so
`funds_attribution.py` (Brinson), the benchmark library, and the skill-vs-luck gate score it exactly like a
real fund — and the terminal can show the **paper-AMC as one more "fund"** in the Funds cockpit, compared to
real ABSL/peer schemes. The firm watches its own NAV, per-scheme NAV, blotter, and scorecards live.

---

## 7. How it learns — without curve-fitting itself into a hole

The learning loop is the most dangerous part: "learn from mistakes" is one keystroke from **hindsight
overfitting**. Four guardrails make it honest:

1. **Pre-registration.** Every thesis is logged with its *falsifier* before the outcome (`amc/prereg.jsonl`).
   A lesson may only be drawn against a pre-registered expectation — you cannot rewrite the thesis after
   seeing the result.
2. **Attribution before judgment.** Distinguish a *bad decision* from *bad luck* via the skill-vs-luck gate
   and the `t = IR·√years` patience — an agent is not "demoted" on a few unlucky draws; you need the data the
   law demands. (This protects against firing a good analyst mid-drawdown.)
3. **Charter updates are themselves experiments.** When an agent's charter is revised ("refresh IT theses
   weekly"), that change is pre-registered and its forward effect measured — improvements must *replicate
   out-of-sample*, not just fit the past.
4. **Walk-forward replay for the track record, live-forward for the truth.** Bootstrap skill on historical
   as-of replay (no look-ahead), but the **only** verdict that counts for "this agent is skilled" is the
   live-forward paper track from go-live — the genuine OOS clock.

The firm improves the way a real one should: better coverage allocation (§3), better pitch calibration,
better conflict rulings, killing crowded/correlated bets (raising effective breadth) — all **measured**, all
defensible, none curve-fit.

---

## 8. Perfect individually, and as a team

- **Individually** = each agent has a clear **charter**, the right **skills/tools**, an honest **scorecard**
  (IC / IR·TC / firm-IR), persistent **memory**, and a **measured improvement loop**. A "perfect" analyst has
  high, calibrated, *de-correlated* IC; a "perfect" FM has high IR with high TC (transfers skill through the
  mandate) and a good investor experience; a "perfect" CIO raises firm-IR by cultivating breadth and
  pre-empting risk.
- **As a team** = the **Fundamental Law at the firm level**: team-IR `= s·√M` **only if the M desks are
  de-correlated**. So the org is engineered for **breadth** (disjoint coverage, diverse lenses, anti-
  groupthink), with the **CIO as the crowding controller** (down-weight a brilliant clone of an existing
  desk) and the **conflict court** turning disagreement into signal rather than noise. The team's job is to
  convert many modest, independent IC's into a high firm IR — exactly what the law says is the only scalable
  path (skill is cheap, *independent breadth* is the prize).

---

## ★ The agents' EVOLVING knowledge base — cross-project skill inheritance (KV 2026-06-26)

The agents are **not static prompt templates** — each is backed by a **living knowledge base** it inherits
and that **keeps evolving** with every project, every session, every method discovered. KV's framing: *"the
FMs inherit the strategies, skills and knowledge — the truth, the fallacies, the practical, the pitfalls —
from the various projects we work on; the FM, Analyst and CIO skills keep evolving and adapting with time,
information and method discovery."*

**Where the knowledge already lives (and compounds):** our global **skills** (`~/.claude/skills/`:
first-principles-thinking, signal-backtest, grid-search, holdability, become-subject-matter-expert,
research-discipline) + the **project memories** (FFT, ABQ, LS-SIF, Vistas). These ARE the accumulated
cross-project methodology — and crucially they record **negative knowledge** (refuted fallacies + pitfalls),
not just what works. An agent's charter **loads the relevant slice**, so it stands on everything learned and
**avoids every known dead-end**.

**The inheritance map:**
- **Universal (all desks):** first-principles + no-curve-fit; the **signal-backtest** discipline (all-starts
  × random, ≥5y windows, %tile-vs-random); the **Fundamental Law** lens (`IC·√BR·TC`); the
  reporting/defensibility discipline; *recheck every favourable result by an independent method*.
- **Analyst desks** inherit the *edge* knowledge: ARM IC ≈ 0.03–0.045 (a tilt, not a per-name guarantee);
  **herding does NOT predict forward returns** (a live claim we refuted); the **flow decomposition** (price
  vs scheme-inflow vs net-active); FFT's *"the edge is category ROTATION, not within-category selection"*;
  value×revision is a *sign* to test, not Ambit's 9%; the over-optimism fade.
- **FM desks** inherit the *construction / holdability* knowledge: ABQ's holdability findings (the **dn−up
  capture gap IS the alpha**; momentum-crash = a short-leg event long-only already avoids; **no in-mandate
  static hedge exists**; the V2 `dms` smoother); the **TC long-only leak**; FFT's *combine-de-correlated-
  sleeves = a breadth win*; turnover-as-style-not-churn; capacity ceilings (~₹1–2.5k cr for momentum tilts).
- **CIO desk** inherits the *allocation / risk* knowledge: LS-SIF findings (value works; **bonds are not a
  hedge**; *"buy VIX" is a mirage*; E/P beats the Fed model); FFT's FoF suite (the **debt anchor = the
  absolute-drawdown fix**; the DAA trend-glide); the AMC-systemic fragility lens; the team-IR **breadth**
  principle.

**The evolution mechanism (why it compounds):** every new finding flows into the skills/memories during
normal work; the agents **re-load the updated knowledge on their next run** and benefit instantly — the same
"terminal grows → agents benefit immediately" loop, now extended from *data* to *knowledge*. **Honesty
guardrail:** knowledge updates follow the same **pre-registration** discipline (a "lesson" must beat a
pre-registered expectation and replicate OOS) so evolution is genuine learning, never hindsight curve-fit.
**Negative knowledge is first-class** — a refuted fallacy or a pitfall is as valuable as a truth; it stops an
agent re-walking a dead-end (the `research-discipline` skill's core rule).

This is what makes the firm **antifragile and compounding**: it is not a snapshot of today's signals, it is
the *distilled experience of every project we have ever run*, getting sharper with time. (Build implication:
agent charters reference the relevant skills + memories by name, and a periodic "knowledge sync" step folds
new findings into the charters — versioned, pre-registered.)

## 9. What's with us vs what's needed

**With us (ready):** the terminal/ontology + all forces; the three persona engines (Analyst/FM/CIO designs);
the attribution + skill-vs-luck + benchmark engines; the Fundamental-Law evaluation backbone; the agent
primitives (Agent / Workflow / Cron / memory / structured output); historical point-in-time data for replay;
the publish/ops discipline.

**Needed (to build):**
1. **The terminal-as-data-API for agents** — a thin tool layer so an agent can *query* the baked data/forces
   programmatically (today it's a UI). Mostly wrapping existing Python engines + JSONs.
2. **The firm OS** — the persistent state stores (§6) + the typed message bus + the scheduler wiring.
3. **The comms protocol** — the typed artifacts (§4) as schemas (structured-output contracts).
4. **The orchestration** — meetings/rounds as Workflows; the daily/weekly/monthly Cron calendar; cost
   governance.
5. **The scorecard/learning engine** — nightly attribution + the Fundamental-Law decomposition + the
   pre-registration discipline + charter-update experiments.
6. **The paper-AMC surface in the terminal** — show the firm's NAV, books, blotter, scorecards, and the
   meeting minutes as a live tab (so KV watches the AMC run).

---

## 10. Phased plan (each phase ships something watchable + reversible)

- **Phase 0 — Skeleton (1 sector, 1 scheme, manual cadence).** One Analyst (e.g. Financials) + one FM
  (a Flexicap mandate) + the CIO, on the existing data, with the typed Pitch/TradeTicket/Escalation artifacts
  and a paper book. Run a few rounds by hand. Proves the protocol end-to-end.
- **Phase 1 — Historical replay track record.** Run the Phase-0 trio as-of past dates (walk-forward, no
  look-ahead) to generate a paper track + the first scorecards (analyst IC, FM IR/TC). Proves the learning
  loop and the evaluation backbone.
- **Phase 2 — Scale the desks.** Dynamic analyst headcount (§3) across all sectors; multiple FMs across real
  category mandates; the weekly/monthly meetings as scheduled Workflows; the firm OS state stores.
- **Phase 3 — Live-forward paper-AMC.** Go-live from today; the genuine OOS clock starts; the terminal grows
  the firm benefits instantly; the paper-AMC tab shows it running.
- **Phase 4 — The learning org.** Charter-update experiments, breadth/crowding management, the CIO team-build,
  the AMC-systemic risk loop — the firm getting measurably better, defensibly.

---

## 11. FLAGGED — risks and the honest limits

1. **Agents synthesize, they don't manufacture alpha** — the edge is the terminal's validated signals; an
   agent "insight" the signals don't support is a hallucination, not alpha. Every pitch must cite validated
   forces.
2. **Learning ⇒ overfitting risk** — mitigated by pre-registration, attribution-before-judgment, charter
   updates as OOS experiments, and the `t = IR·√years` patience. This is the #1 trap; the guardrails are
   non-negotiable.
3. **Paper ≠ live** — no execution/impact/liquidity friction is fully modeled; capacity caps and realistic
   costs must be in the FM mandate or the paper IR is flattered.
4. **Look-ahead in replay is fatal** — the as-of discipline must be airtight (point-in-time data only;
   delisted names retained; total-return). The terminal's vintage discipline carries here.
5. **Cost/latency** — many agents × frequent meetings = real token cost; cheap-model monitoring + bounded
   fan-out + budgeted committees are mandatory, not optional.
6. **Groupthink/crowding** — agents sharing a model can converge (low effective breadth); the disjoint-
   coverage + diverse-lens + CIO-crowding-control design fights it, but it must be *measured* (desk-IC
   correlation), not assumed.
7. **Scheme-level until a manager-tenure analogue exists** — our real-fund benchmarks are scheme-level; the
   paper-AMC's *own* managers are clean by construction (we define them), which is actually an advantage.
8. **This is an experiment, not a product** — it is a research harness to learn how the personas combine and
   to pressure-test the terminal; it is **not** investment advice and trades **no real money**.

*Internal dependencies:* [[ANALYST_GOLDMINE]] · [[FM_INTELLIGENCE]] · [[CIO_INTELLIGENCE]] · [[MESH_DESIGN]]
· [[FUNDAMENTAL_LAW]] (the evaluation backbone) · [[FUND_MANAGER_ANALYSER_DESIGN]] (the attribution engine) ·
the Agent/Workflow/Cron/memory primitives.
