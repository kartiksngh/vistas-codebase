# Market-Forces Mesh — program log & design

> KV's "goldmine tracker": stitch the market forces acting on a stock — **analyst (ARM) · money-flow (MF) ·
> price · fundamentals (P&L/BS/CF) · ownership** — into a time-aware mesh, track their interactions/feedbacks,
> and convert into **horizon-tagged actionables** (immediate <1M · medium 1-6M · long >1Y) for **analyst / fund-
> manager / CIO** personas, fund-tied for FM/CIO. First-principles, scientifically validated, no hype.
> Started 2026-06-25. This file is the running log + design source of truth (program task #41).

## 0. Disciplines (non-negotiable, KV)
- **No score for error** — validate every signal before it drives an actionable; audit conventions, don't trust parity.
- **No hype** — report Definition·Method·Why for every metric; same order-of-magnitude ≠ "exact match"; flag failures openly.
- **Survivorship-free** — keep dead names in the research panels (they make backtests honest); exclude them only from *live* cards.
- **Display-plane** — screen/quant/fund JSONs carry Python-baked values; no JS-parity port (analytics.py untouched).

## 1. Validated foundation (what we can build on)

### 1a. Data audit (2026-06-25, verified in-repo)
| Force | Source (verified) | Coverage | Note |
|---|---|---|---|
| Analyst (ARM) | `arm_repo/compiled/arm_india.parquet` → `starmine` cards | 1,924 mapped / 1,641 live | full hist 1998→2026-06-24 |
| MF flow (corp-action-immune) | `funds_flows.stock_flows` `end−start·(1+TR)` | 1,034 stocks | monthly 2013→2026; **6/12m = sum baked series** |
| MF holdings + #funds | `funds_flows.build_stock_holders` | 1,032 | ₹cr value → MF-%-of-mcap computable |
| Ownership %: Promoter/FII/DII/Public | `stock_intel._ownership` (screener) | 1,032 | quarterly; **+pledge%**, +1y chg |
| Market cap | `shares.mcap_resolved` (AMFI 6-mo avg) | 2,238 | corr 0.9947 vs Bloomberg |
| P&L/BS/CF + growth | `fundamentals.py` (screener) | 2,604 | yoy/accel/**cagr{3,5,10y}**/ttm already computed |

**Honest gaps:** (1) **Retail is NOT separable** — only "Public %" (= retail + HNI + non-promoter corporates); true retail needs a SEBI/NSE detail feed. (2) NSE point-in-time issued-shares cache empty → AMFI avg used (fine). (3) **MF ⊂ DII** — show both, never sum.

### 1b. ARM signal-backtest (validated on our TR panel, `vistas/arm_backtest.py`)
Monthly cross-sections (~1,000 stocks), ARM_100_REG vs forward TR, 2005–2026 (255 months), Spearman IC + decile spread.

| Horizon | IC | t-stat | decile spread (ann.) | %months IC>0 |
|---|---|---|---|---|
| 1M | 0.030 | 7.9 | 20.6%/yr | 74% |
| 3M | 0.042 | 10.7 | 16.4%/yr | 74% |
| 6M | 0.045 | 11.3 | 13.1%/yr | 75% |
| 12M | 0.040 | 10.7 | 10.1%/yr | 78% |

- **Real & significant** (t≈8–11); **short-horizon** (annualized spread biggest at 1M, decays to 12M); **positive every era** (IC 0.065/0.026/0.032 for 2005-12/13-19/20-26).
- A significant IC across ~1,000 stocks also **confirms the ISIN→symbol mapping** (a wrong map → IC≈0).
- **Caveat (corrected after KV pushback):** this is NOT an exact match to the StarMine white-paper India figure (IC 0.050, spread 15.94%/yr at 1M). Mine at 1M is IC 0.030 / spread 20.6% — *same order of magnitude only*. Different period/universe/construction.

### 1c. ARM nature (from the StarMine white paper, grounded)
Low-persistence **revision-trend** signal: month-to-month serial correlation of revisions ≈ **0.15**; the basic factor's IC is +0.118 when the next revision *continues* same-direction (37% of cases) but **−0.155 when it reverses** (37%). ⇒ edge is short-lived, must be **refreshed often** (→ weekly ARM cadence is essential), read **direction not just level**, and is best paired with a slow signal (**value** — Ambit India: cheapest-value × best-revision ≈ +9% alpha since 2006).

## 2. Shipped
- **2026-06-25** Full ARM history patched (469→1,641 live cards); Screens analyst axis now ~4× coverage. Multi-fund side-by-side compare. Scatter corner-clip fix. **All live.**
- **2026-06-25 (in flight, rebuild9)** Screen enrichment #43: flow 1/3/6/12m columns, ownership Promoter/FII/DII/Public %, MF-%-of-mcap, **%↔₹ toggle**, key 3-statement CAGR baked. Honest labeling (Public≠retail; MF⊂DII).

## 3. Architecture (first-principles)
- **Per-stock mesh state** (baked into the screen rows / a `mesh/<SYM>.json`): each force as {level, trend, percentile-in-universe} + the cross-force **interaction signals**.
- **Signal catalog** (each needs its own validation gate before it drives an actionable):
  - `ANALYST_UP_CHEAP` — rising ARM + cheap (value) → constructive (Ambit-validated combo).
  - `SMART_MONEY_AHEAD` — MF accumulating + ARM not yet up (Q3) → FM leading the street.
  - `STREET_AHEAD` — ARM up + MF not buying (Q2) → catch-up candidate / or a trap.
  - `CROWDED_REVERSAL` — high MF-%-mcap + breadth peaking + ARM rolling over → de-risk (crowding→reversal).
  - `CONVICTION_ADD` — flow persistent across 1/3/6/12m windows + breadth positive → durable accumulation.
  - `OWNERSHIP_ROTATION` — FII↓ DII↑ (or vice-versa) at a fundamentals inflection.
- **Horizon mapping:** ARM/flow-1m = immediate (<1M); flow-3/6m + revision trend = medium (1-6M); fundamentals growth + ownership trend + value = long (>1Y).
- **Persona lens:** Analyst = revision breadth & where the street is turning; FM = own-book vs the mesh (am I with/against smart money?); CIO = aggregate fund-level posture + crowding risk.
- **Fund-tie (FM/CIO):** join a scheme's holdings to each stock's mesh state → "% of book in each quadrant", flow-adjusted additions per quadrant per window, anti-consensus intensity → plain-English FM actionables (#38/#39), **time-windowed to isolate the manager**.

## 3b. VALIDATED DESIGN — mesh-design-v2 (2026-06-25, full detail in `MESH_DESIGN.md`)
Research leg re-run successfully (real citations, no stub). The design is now research-grounded.

**The one organizing principle (drives everything):** **LEVEL is context (often a *spent* or *crowding* signal); CHANGE/TREND is the forward edge; every edge has a CLOCK (horizon) you must not mix.**
- Ownership **LEVEL** ≠ buy. High instit. level is a largely-spent compositional effect (Gompers-Metrick) or, at the extreme, a *reversal risk* (Frazzini-Lamont dumb-money).
- **CHANGE** is the edge: change-in-breadth (# distinct funds) predicts ~5–6%/12M (Chen-Hong-Stein); short-term-institution flow is the informed, non-reversing part (Yan-Zhang).
- **India caveat (important):** **FII net flow is mostly an *effect* of returns (trend-chasing), a weak forward predictor** (Chakrabarti, Griffin) — treat as a contemporaneous sentiment gauge, NOT a buy signal. DII/MF flows stabilising, little clean forward causality. So our defensible forward signals = **Δbreadth + persistent multi-window MF accumulation**, short-to-medium horizon.

**Per-stock mesh state** → NEW module `vistas/mesh.py` → `data/_mesh/<SYM>.json` + `_index.json`, over the WHOLE universe (not the filtered screen). Reuses already-baked forces (quant/fundamentals JSON + ARM step-series); display-plane, no JS-parity port. 5 forces, each as **{level, trend, percentile-in-universe}**: analyst(ARM) · flow · breadth · ownership · fundamentals(+value) · price.
- **The one genuine new compute = the analyst TREND** (ΔARM): the screen reads only the latest ARM *level*, but the literature says *direction* is the trigger (IC +0.118 continue / −0.155 reverse). Lift `arm_backtest._arm_symbol_series` to compute ΔARM(1M/3M).
- **NEW value composite** for VALUE×REVISION: cross-sectional z(E/P,B/P,S/P,div-yld) — we only have own-history P/E percentile today.

**Signal catalog** (each: construction · horizon · persona · **validation gate** · grounding · failure mode; renders as *diagnostic context* until its gate passes — "no score for error"):
- **S1 CONVICTION_ADD** (flagship): flow↑(3m,accelerating) + Δbreadth>0 + ΔARM≥0 + ARM≥50. Medium. Gate: decile signal-backtest, must beat plain-high-ARM and survive size/value control + Granger (flow *precedes* return). Grounding: Chen-Hong-Stein, Yan-Zhang, Wermers.
- **S2 SMART_MONEY_AHEAD** (Q3): flow↑ + ARM<50. Medium/~1q (decays — Frazzini-Lamont). Gate: Q3 forward > Q4 (ideally > Q2).
- **S3 STREET_AHEAD** (Q2): ARM↑ + flow≤0 — catch-up OR trap; ship as *watch* only until proven below Q1/Q3.
- **S4 VALUE×REVISION** (Ambit ~9% alpha): cheapest value-quintile × best ΔARM, Q1/Q2 safety-net hold. Gate: **Ambit's number, not ours** — backtest VALUE×ARM on our panel; ARM≠raw-6M-EPS-revision so 9% won't transfer 1:1; −0.5 corr → +1 in panics.
- **S5 CROWDED_REVERSAL** (confluence, not single-trigger): fire only when MF-%-mcap top-quintile (fragility) + breadth at own 24M peak rolling over + ARM rolling over, *together*. Medium→long tail (Coval-Stafford 24M fire-sale). Grounding: Wermers herding, Coval-Stafford, Greenwood-Thesmar fragility.
- **S6 OVER_OPTIMISM_FADE** (long >1Y): euphoric consensus + unsustainable growth expectations → slow de-rate (La Porta / diagnostic expectations).

**Fund-tie (FM/CIO):** join a scheme's holdings to the mesh → %book/%AUM per quadrant, flow-adjusted additions per window, anti-consensus intensity, **start/end window to isolate the manager**. Detail in `MESH_DESIGN.md` §4.

## 4. Phased roadmap (each phase independently shippable + probe-verified)
1. ✅/▶ **Screen enrichment** (#43) — flows 6/12m, ownership, MF/mcap, %↔₹ toggle. *(rebuild9)*
2. **Screen universe selector** (#37) — mcap/category/sector/theme; sortable+filterable rolling-return columns; drop fixed −3m boundary.
3. **Per-stock mesh state + signal catalog** — bake the interaction signals + horizon tags.
4. **Fund-level quadrant + manager isolation** (#38) — %book/%AUM per quadrant, flow-adjusted additions, start/end window.
5. **Actionable engine** (#39) — horizon-tagged, per-persona, fund-tied recommendations with the validation each carries.
6. **Chatbot** (#40, low priority) — NL Q&A grounded in the mesh.

## 5. Open / to-do
- ~~Academic-research leg failed~~ → **re-run succeeded (mesh-design-v2)**; full validated design in `MESH_DESIGN.md`, summary in §3b.
- Validate each catalog signal before it drives an actionable. **S1 CONVICTION_ADD: FAILED its gate (2026-06-25, `vistas/mesh_backtest.py`).** On the fair same-universe (679 stocks, all forces present) head-to-head, the equal-weight z-sum (flow+breadth+ΔARM) gives IC6m 0.054 / spread 9.3% — *worse* than plain **ARM-level alone (IC 0.071 / spread 13.4%)** on that same universe. Equal-weighting a strong signal (ARM) with weaker correlated ones (flow 0.045, ΔARM 0.026) DILUTES it. Gate B (flow leads returns) passed (β t=3.85 after contemporaneous control). ⇒ do NOT ship CONVICTION_ADD as an equal-weight blend. Notable: ARM-level is *strongest on the institutionally-owned 679-universe* (0.071) vs the full 963 (0.056) — analyst revisions work best on well-covered names. dbreadth alone (0.053) corroborates Chen-Hong-Stein. NEXT ITERATION: recast S1 as **ARM-level as the signal, flow+breadth as a CONFIRMATION filter** (does "high ARM AND accumulating AND breadth-rising" beat plain high-ARM as a subset?), not an additive blend — re-run the same harness.
- S4 VALUE×ARM still to validate.
- Build the value z-composite (E/P,B/P,S/P,div-yld) — only own-history P/E percentile exists today.
- Codify a reusable **multi-force / ownership-flow methodology skill** once 2–3 signals are validated.
- Weekly ARM pipeline wiring (#35) — fold KV's fetch script when provided.
