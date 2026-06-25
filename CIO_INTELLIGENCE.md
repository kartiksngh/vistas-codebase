# Vistas CIO Intelligence — Design Blueprint
### "Make the data speak to each other, and turn loose threads into ranked doables"

The **CIO layer** — the synthesis that sits **on top of** every other persona (Analyst
[[ANALYST_GOLDMINE]], Fund-Manager [[FM_INTELLIGENCE]] / [[FUND_MANAGER_ANALYSER_DESIGN]], Quant
[shipped], Risk) and the Market-Forces Mesh substrate ([[MESH_DESIGN]]). Its job is KV's question
verbatim: *"how do we make our data speak to each other intelligently to arrive at **doables — with
priority, urgency, and timeline** … CIO-level intelligence which sits on top of every other
intelligence, mapping all loose threads pointing towards decision-making on asset allocation."*

The model is **Palantir's ontology**, re-derived from first principles for our data — not adopted on
authority. Status: **design blueprint.** The **descriptive** CIO pieces (3-lens pulse, AMC systemic-risk
map) are buildable now from validated substrate; the **prescriptive** engines (recommendation,
allocation) are **gated research, never black-box**. Stamped 2026-06-26. House discipline: every doable
is historical-data-based, rule-based, **no curve-fit**, **self-explaining**, **auditable to an IC**, and
carries its **horizon/clock**; unproven ⇒ context, never an action.

---

## 0. The lesson from Palantir (re-derived, not imported)

Palantir's value is not a model — it is an **ontology**: messy source feeds are resolved into **typed
objects**, connected by **typed links**, with **functions defined on the objects**, and an **audited
action layer** where a human decision is taken and its provenance recorded. Strip the marketing and four
primitives remain — and **we already have three of them:**

| Palantir primitive | Our equivalent | Status |
|---|---|---|
| **Entity resolution** (one real thing = one object) | the **`vst_id` identity spine** (stock & scheme) | ✅ built |
| **Typed object + link model** | stock ⋈ fund-holding ⋈ benchmark ⋈ sector ⋈ AMC, all `vst_id`-keyed | ✅ mostly built |
| **Functions on objects** | the **Mesh forces** `{level, trend, pctile}` per stock + the persona engines | ✅ substrate built |
| **Audited action / "doable" layer** | **THIS doc's §4** — ranked actionables with provenance + horizon | ⛳ to build |

So the CIO layer is **not a new data project** — it is the **assembly + ranking + provenance** layer on
top of the ontology we already have. That is why it is feasible without a "no-shortcut" multi-month data
build: the hard part (identity + forces) is done.

---

## 1. The layered stack (where every engine sits)

```
 L3  CIO SYNTHESIS        doables (priority · urgency · timeline) · allocation across mandates · risk map
        ▲  consumes ▲
 L2  PERSONAS            Analyst (Goldmine)   FM (reflect+construct)   Quant (flows/crowding) ✅   Risk
        ▲  read ▲
 L1  FORCES (Mesh)       analyst · flow · breadth · ownership · fundamentals · price  — each {level,trend,pctile}, each gated
        ▲  computed on ▲
 L0  ONTOLOGY            vst_id objects + typed links (stock⋈holding⋈benchmark⋈sector⋈AMC)  ✅
```

**The rule that makes synthesis legible** ([[MESH_DESIGN]] §0, repeated because it governs the CIO too):
**LEVEL = context, CHANGE = edge, every edge has a CLOCK.** The CIO's distinctive trick: **agreement of
*change* across forces/personas = conviction; agreement of *level* (everyone already in, breadth at peak)
= crowding/fragility.** Same direction, opposite meaning. This single asymmetry drives both the buy-side
synthesis and the risk map.

---

## 2. The descriptive CIO engines (buildable now — no forecast)

These make no forward claim, so they ship without a NAV backtest; they are the highest-value, lowest-risk
CIO deliverables.

### 2.1 The 3-lens market pulse — *the gaps are the signal*
Three independent reads of the **same** universe, sliced by sector/theme and stock:
- **Street lens** = where **analysts** are (ARM level + change) — [[ANALYST_GOLDMINE]].
- **Smart-money lens** = where **funds** are actually moving rupees (cross-AMC net active flow + breadth) —
  the Quant engine, shipped.
- **Reward lens** = what the **market** has paid (price RS, drawdown, above/below 200DMA).

**Definition of the signal:** the **disagreements between lenses**, each a named, defensible state:
- Street↑ + Smart-money↑ + Reward flat → *informed accumulation not yet priced* (the `CONVICTION_ADD`
  setup) — **the most actionable gap.**
- Smart-money↑ + Street↓ → *funds ahead of the analysts* (`SMART_MONEY_AHEAD`).
- Street↑ + Smart-money↓ → *analysts ahead, funds unconvinced* (`STREET_AHEAD` — catch-up or trap).
- All three high + breadth at own-peak → *consensus crowded* (a **risk** read, §3).

**Why it's defensible:** it is a **re-display of three already-validated forces** + their pairwise gaps —
no new claim, no fit. It ships as the CIO dashboard's top panel. (The *forward* edge of each named gap is
the Mesh signal that must pass its own gate before the gap becomes a *buy*, not just a *view*.)

### 2.2 AMC systemic & systematic risk — *the inter-scheme fragility map*
KV's engine (e), the part no per-fund view sees. For an AMC (or the whole market):
- **Cross-scheme overlap / crowding** — `vst_id`-keyed: how concentrated is the AMC's *aggregate* book;
  how correlated are its schemes' active bets (the "brilliant clone" problem from
  [[FUND_MANAGER_ANALYSER_DESIGN]] §5 — α-correlation → 1 kills diversification).
- **Crowded × illiquid fragility** — names that are simultaneously (a) broadly/heavily held (breadth at
  own-peak, high MF-%-mcap) and (b) thin vs ADV → the **fire-sale** exposure: if flows reverse, the exit
  is disorderly (Coval-Stafford forced-sale → ~24M reversion; Greenwood-Thesmar concentration → fragility).
- **Co-crash proxy** — how many of an AMC's (or a fund's) holdings sit simultaneously in
  `CROWDED_REVERSAL` ([[MESH_DESIGN]] S5) → a *fragility co-movement* read.

**★ Honesty stamp (mandatory):** we have **no leverage / no positioning data**, so this is a
**fragility proxy, medium confidence — NOT a measured systematic-crowding number** (the Aug-2007
crowded-factor-unwind dimension is unobservable for us). The map surfaces *where the kindling is*, not a
probability of fire. Label exactly, every time.

**Why it's defensible:** it is descriptive structure (overlap, concentration, liquidity ratios computed
from holdings + the validated flow/breadth forces) — it flags *fragility*, it does not *forecast a crash*.

### 2.3 The doables surface (the audited action layer) — see §4.

---

## 3. The prescriptive CIO engines (GATED — never black-box)

KV's engines (a) recommendation, (d) asset allocation. These make forward claims, so they are **research
behind a gate** and render as labelled-unproven until they pass.

- **(a) Stock/theme recommendation** = the **posture map** ([[ANALYST_GOLDMINE]] §4.3): a *rule
  combination* of already-validated Mesh signals → aggressive/neutral/benchmark/defensive/exit per sector,
  then the revision-leaders crossed with flow within aggressive sectors. **Gated** as a whole (the
  combination is backtested, all-starts × ≥10k random, size/value controls, beats the single best
  signal). The combination of validated parts is **not** automatically validated — burned lesson:
  the equal-weight Mesh blend **failed** (`mesh_backtest.py`, blend < ARM-alone).
- **(d) Asset allocation under mandates/constraints** = take the per-mandate **expert-FM portfolios**
  ([[FM_INTELLIGENCE]] §2) and combine them under an **investor mandate** (risk budget, category limits,
  liquidity, the SEBI constraints) — a constrained Markowitz/Black-Litterman tilt where the "views" are
  *only* the validated forces and the prior is the policy benchmark. **Gated** like a product (net-of-fee,
  era-stable, beats the policy benchmark and the naive equal-mandate split OOS).

**The defensibility contract for both** (KV's global rule, verbatim intent): *decisions are historical-data
+ rule-based, no overfit, rooted in first-principles + common sense — something to be reasoned with, hence
to hold and defend.* Concretely: no free parameter fit to maximise backtest return; every clause traces to
a force that passed its own test; the engine is parameter-plateau-robust and era-stable; the whole is
backtested as a product; and the output always carries its reasons. **A recommendation we cannot defend in
front of an IC does not ship.**

---

## 4. The doable engine — priority · urgency · timeline (the heart of KV's ask)

How loose threads become a **ranked action list**. A *doable* is a typed object:

```jsonc
{
  "doable": "Add to Financials revision-leaders (sector posture: aggressive)",
  "kind": "add | trim | exit | watch | review-manager | de-risk-book",
  "scope": "stock | theme | fund | AMC | allocation",
  "evidence": [ {force/persona, level, change, pctile, signal, validated:true} ],   // provenance
  "conviction": 0.0-1.0,     // = strength × breadth of AGREEING validated forces
  "horizon":   "immediate(<1M) | medium(1-6M) | long(>1Y)",   // the CLOCK — never mixed
  "urgency":   "now | this-month | this-quarter",             // derived from horizon × decay-rate × freshness
  "priority":  rank,         // conviction × portfolio-materiality × (1/urgency-window)
  "why_now":   "the change that just fired",  "why_defensible": "the validated rule",
  "caveats":   [ ... ],      "validated": true|false
}
```

- **Priority** = conviction × **materiality** (how much of the book it moves) × urgency. A high-conviction
  signal on a 0.2% position outranks below a medium-conviction signal on a 5% position — the CIO thinks in
  *portfolio impact*, not signal strength alone.
- **Urgency vs Timeline are different axes** (a subtlety the engine must keep straight): **timeline/horizon**
  = *how long the edge takes to play out* (the clock); **urgency** = *how soon you must act before the
  signal decays / the window closes* (derived from horizon × the force's measured decay × how *fresh* the
  trigger is). A fast ARM signal (1–6M clock) that *just* fired is **urgent**; the same signal three months
  stale is **expired**, not urgent. A long value-cycle thread (>1Y clock) is rarely urgent.
- **Conviction comes from cross-force *change* agreement** (§1) — the more independent validated forces
  point the same way, the higher the conviction; a single force = low conviction by construction.
- **Provenance is mandatory** — every doable lists the exact forces/levels/changes that produced it
  (the audited action layer). No un-sourced recommendation. Unvalidated evidence ⇒ the doable is `watch`,
  never `add`/`exit`.

**Why this is the defensible synthesis and not an oracle:** the doable engine **invents no new signal** —
it *ranks and routes* the validated outputs of L1/L2 by impact and decay. Its only "model" is the priority
arithmetic above, which is transparent and auditable. It is the Palantir action layer, not a forecaster.

---

## 5. Build order, logs, resumability

**Phase 1 (now, descriptive — ships first):**
- `vistas/cio.py` (NEW) reads the baked Mesh substrate + persona outputs → bakes
  `data/_cio/pulse.json` (the 3-lens pulse, §2.1) and `data/_cio/fragility.json` (the AMC systemic map,
  §2.2). Front-end CIO tab: pulse panel (sector/stock, the lens-gap table) + fragility map + the
  doables list (descriptive doables only — `watch`/`review`/`de-risk` from validated context). Each item
  Definition·Method·Why·caveats, horizon-stamped. Probe + spot-check.

**Phase 2 (gated — prescriptive):** the posture-map recommender (a) and the allocation engine (d) — build
metric → run the §3 product/signal gate → ship on pass, else labelled-unproven diagnostic.

**Phase 3 (new data):** manager-tenure DB → person-level CIO manager-selection / team-build
([[FUND_MANAGER_ANALYSER_DESIGN]] §5); widen `KEEP_MNEM` for the analyst term structure
([[ANALYST_GOLDMINE]] §4.1); leverage/positioning data (if obtainable) to upgrade the fragility *proxy* to
a measured systemic-crowding number.

**Logs & resumability (KV standing requirement):** every phase appends to **`CIO_INTELLIGENCE_LOG.md`**
(built / gate numbers / verdict / next step); gate artifacts to `data/_cio/_backtests/`; the in-repo
`MEMORY.md` resume point updated. Reversible — new `data/_cio/*`, reads existing JSONs, **no mutation of
`analytics.py`/`funds_flows.py` formulas, no JS-parity port** (display-plane).

---

## 6. FLAGGED — what must not become a silent assumption

1. **Descriptive CIO (3-lens pulse, fragility map) ships now; prescriptive CIO (recommendation,
   allocation) is GATED** — the latter never ships as advice until it beats its benchmark net-of-fee,
   OOS, era-stable, beating the single best signal.
2. **A combination of validated signals is NOT automatically validated** — the equal-weight Mesh blend
   failed (`mesh_backtest.py`). Backtest the *whole* recommender/allocator, not just its parts.
3. **AMC systemic risk is a FRAGILITY PROXY, medium confidence** — no leverage/positioning data; never a
   measured crash probability; the Aug-2007 crowded-factor dimension is unobservable for us.
4. **No forward estimate levels** ([[ANALYST_GOLDMINE]]) caps the "super-estimate" depth — a data-unlock
   item, not promised from current data.
5. **Manager-level CIO = scheme-level proxy until the tenure DB exists** — the binding gap for
   manager-selection; stamp every person-level statement.
6. **FII flow is positive-feedback in India** (effect of returns) — any "follow-the-flow" CIO doable needs
   the lead-lag/Granger gate first; never route a contemporaneous FII-NIFTY correlation as an action.
7. **Urgency ≠ timeline** — keep the two axes separate, or the doable list will mis-rank a stale signal as
   urgent.
8. **Provenance is non-negotiable** — a doable without its source forces is a defect, not an action.

*Canonical sources:* the Palantir ontology pattern (entity-resolution → typed objects/links →
functions-on-objects → audited actions, re-derived); Black-Litterman (1992, view-blending under a prior);
Markowitz (1952), Grinold-Kahn (1999, team IR); Coval-Stafford (2007, fire-sale), Greenwood-Thesmar
(2011, fragility), Frazzini-Lamont (2008, dumb-money) for the systemic/fragility lens; Chen-Hong-Stein
(2002, breadth) & Yan-Zhang (2009, short-term institutions) for the smart-money lens; and the project's
own `arm_backtest.py`, `mesh_backtest.py`, [[MESH_DESIGN]], [[ANALYST_GOLDMINE]], [[FM_INTELLIGENCE]],
[[FUND_MANAGER_ANALYSER_DESIGN]].
