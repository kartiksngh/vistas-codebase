I have everything I need. The schema field paths are confirmed: `fundamentals.compute()` emits `growth.PAT.{ttm_yoy, accel, cagr.{3y,5y,10y}}`, `valuation.{pe_now, pe_percentile, median_pe}`, `quality.score`; `starmine.headline.score` is `ARM_100_REG`; `funds_flows.stock_flows` emits `net_flow_cr, dbreadth, breadth_end, buyers, sellers, ret_1m`; `stock_intel._ownership` emits `holders.{Promoter,FII,DII,Public}.{latest_pct, chg_1y_pp}`.

Note the ARM history-axis gap: `arm_backtest._arm_symbol_series` gives full ARM step-series per symbol (needed for the **trend** of the analyst force), but `screens.py` only reads the latest `starmine.headline.score` (a level). I'll flag that the trend/percentile bake needs the step-series, not just the card.

Now I'll synthesize the full design.

---

# VISTAS MARKET-FORCES MESH — Phase-3 Full Design

Lead-architect synthesis. Cross-checked against `MARKET_FORCES_MESH.md`, `vistas/screens.py`, `vistas/funds_flows.py`, `vistas/stock_intel.py`, `vistas/fundamentals.py`, `vistas/arm_backtest.py`. **Verdict: ~80% of the substrate already exists.** The mesh is mostly an *assembly + horizon-tagging + validation* layer, not new engines. Below, "REUSE" = already built; "NEW" = the thin layer to add.

---

## 0. The one organizing principle the research forces on us

Every piece of the literature collapses to a single rule that must govern the whole mesh:

> **LEVEL is context (often a spent or crowding signal); CHANGE/TREND is the forward edge; and every edge has a CLOCK (horizon) you must not mix.**

- Ownership LEVEL → mostly a spent compositional effect (Gompers-Metrick) or, at the extreme, a *reversal risk* (Frazzini-Lamont dumb-money). **Never "high MF-%-mcap = buy."**
- Ownership/breadth CHANGE → the durable forward signal (Chen-Hong-Stein breadth, Yan-Zhang short-term institutions).
- Two clocks: the flow/revision edge is **fast (1q–6M)**; the dumb-money/over-optimism penalty is **slow (1–3y)**. A signal cannot serve both — so **every signal carries an explicit horizon tag**, exactly as our own ARM backtest already shows (IC peaks 3-6M, decays by 12M).

This is why the mesh stores each force as `{level, trend, percentile}` (so LEVEL and CHANGE never collapse into one number) and why every signal is horizon-stamped.

---

## 1. PER-STOCK MESH STATE — the schema and where it's baked

### 1.1 Where it lives — a NEW module `vistas/mesh.py`, NOT extended `screens.py` rows

**Decision: new module.** Reasons (first-principles, not convention):
- `screens.py` builds *one screen* (Smart-vs-Street) over a *pre-filtered* watchlist (correction + deteriorating). The mesh state must exist for the **whole universe** (NSE-500+ / all 1,032 flow-covered names), unfiltered, because signals slice it differently.
- The mesh state is the **shared substrate** that `screens.py`, the per-stock cockpit (`stock_intel`), and the fund-tie all read. Putting it in `screens.py` would invert the dependency. So: `mesh.py` builds `data/_mesh/<SYM>.json` (and a compact `data/_mesh/_index.json` for cross-sectional ranking); `screens.py` and the fund-tie consume it.
- **REUSE:** `mesh.py` does *no* new fetching/compute of raw forces. It reads the already-built per-symbol `quant/<SYM>.json` (which already carries `smart_money_flow`, `fund_holders`, `ownership`), `fundamentals/<SYM>.json`, and the ARM step-series. It assembles `{level, trend, percentile}` per force + the signal flags. Display-plane, Python-baked, no JS-parity port (analytics.py untouched) — consistent with the discipline already in the codebase.

### 1.2 The schema (one stock-month)

Five forces. Each force = `{level, trend, pctile}` where **level** = current value, **trend** = the change (direction is the edge), **pctile** = cross-sectional rank 0-100 in the universe *this month* (so signals are universe-relative, not absolute-threshold-fragile).

```jsonc
{
  "symbol": "TCS", "vst_id": "...", "name": "...", "sector": "...", "asof": "2026-05",
  "universe": "NSE500", "n_universe": 498,

  "forces": {
    "analyst": {                         // StarMine ARM — REUSE starmine.headline.score (ARM_100_REG)
      "level":  62.0,                    //   ARM score 0-100 (the score-in-force at month-end)
      "trend":  -8.0,                    //   Δ ARM over trailing 1M (NEW: needs ARM step-series, see 1.3)
      "trend_3m": -4.0,                  //   Δ ARM over trailing 3M (smoother, the "direction")
      "pctile": 71,                      //   cross-sectional pctile of LEVEL
      "trend_pctile": 22,                //   cross-sectional pctile of TREND (so a falling-ARM name ranks low)
      "horizon": "immediate"             //   1M; refresh weekly (revision serial corr ~0.15)
    },
    "flow": {                            // cross-AMC net active flow — REUSE funds_flows / quant.smart_money_flow
      "level_3m":  124.0,                //   ₹cr summed trailing-3M net active flow (conviction window)
      "level_1m":  18.0,                 //   ₹cr latest month (the inflection read)
      "level_6m":  210.0, "level_12m": 305.0,
      "trend":  "accelerating",          //   sign-agreement + run-rate: 1m>3m/3 => accelerating (NEW)
      "intensity_pctile": 64,            //   flow ÷ mcap, pctile (size-neutral — NOT a size proxy)
      "agreement": "confirmed",          //   1m vs 3m sign agree? (REUSE screens.py flow_agreement)
      "horizon": "immediate-medium"      //   <1M..6M
    },
    "breadth": {                         // # distinct funds holding — REUSE stock_flows breadth_end/dbreadth
      "level":  142,                     //   breadth_end (# active funds holding)
      "trend":  +6,                      //   dbreadth (buyers−sellers of POSITIONS this month)
      "buyers": 19, "sellers": 13, "net_breadth": 6,
      "pctile": 88,                      //   crowding LEVEL pctile (high = most broadly held = fragility input)
      "trend_pctile": 79,               //   Δbreadth pctile (THE Chen-Hong-Stein signal)
      "at_own_peak": true,               //   is breadth at/near its own 24M max? (crowding-rollover input, NEW)
      "horizon": "medium"                //   1-6M
    },
    "ownership": {                       // quarterly % — REUSE stock_intel._ownership holders + chg_1y_pp
      "mf_pct_mcap": 8.4,                //   MF holding ₹cr ÷ mcap (LEVEL = crowding context, NOT a buy)
      "fii_pct": 24.1, "fii_chg_1y_pp": -1.8,
      "dii_pct": 18.9, "dii_chg_1y_pp": +2.3,
      "promoter_pct": 71.0, "promoter_chg_1y_pp": +0.1, "pledge_pct": 0.0,
      "rotation": "FII_down_DII_up",     //   regime tag (NEW)
      "horizon": "long",                 //   >1Y (quarterly, lagged)
      "note": "MF ⊂ DII — never summed; Public ≠ retail"
    },
    "fundamentals": {                    // P&L growth + valuation — REUSE fundamentals growth/valuation/quality
      "pat_ttm_yoy": 0.14, "pat_accel": 0.02,
      "pat_cagr_3y": 0.18, "pat_cagr_5y": 0.16,
      "quality_score": 78,
      "value": { "pe_now": 28.4, "pe_pctile_own10y": 81, "ey": 3.5, "pb": 9.1, "peg": 1.8 },
      "value_pctile_xs": 22,             //   cross-sectional cheapness pctile (low=cheap) for VALUE_x_REVISION (NEW)
      "cyclical_caveat": null,
      "horizon": "long"                  //   >1Y
    },
    "price": {                           // market behaviour — REUSE stock_intel.market
      "ret_1m": -0.03, "ret_3m": 0.05, "ret_6m": -0.08, "ret_12m": 0.12,
      "rs_500_12m_pp": -4.0,             //   relative strength vs NIFTY 500
      "dd_52w": -14.0, "above_200dma": true,
      "horizon": "immediate-medium"
    }
  },

  "signals": [                           // the cross-force flags that FIRED this month (catalog §2)
    {"name": "CONVICTION_ADD", "fired": true, "score": 1.7, "horizon": "medium",
     "persona": ["FM","analyst"], "validated": false, "why": "flow + breadth + ARM all up"}
  ],

  "data_quality": {                      // honest gates
    "has_arm": true, "has_flow": true, "flow_ca_flagged": false,
    "ownership_asof": "2026-03", "thin_liquidity": false
  }
}
```

**Percentile convention (must be stated, per reporting discipline):** `pctile(x) = 100 · #{universe ≤ x} / N`, computed within the month's cross-section, ties resolved by the count-≤ rule. For *trend* percentiles of a "lower is better" quantity (e.g. cheapness), we store the cheapness pctile so low pctile = cheap; documented per field.

### 1.3 The ONE genuine new compute: the analyst TREND

This is the gap I want to flag loudest. `screens.py` and `stock_intel` only read the **latest ARM score** (`starmine.headline.score`) — a LEVEL. But the entire ARM literature says **direction, not level, is the trigger** (StarMine: IC +0.118 when revision continues, −0.155 when it reverses; serial corr ~0.15). The mesh therefore *must* compute `Δ ARM` from the **step-series**, which already exists and is already loaded by `arm_backtest._arm_symbol_series()` (`{symbol → pd.Series of ARM_100_REG indexed by date}`, ffill to month-ends).

**REUSE that function** — lift `_arm_symbol_series` into `mesh.py` (or import it) to get `trend = ARM(t) − ARM(t−1M)` and `trend_3m`. Without this, the mesh would ship a level-only analyst force and miss the single most-validated mechanic we have.

---

## 2. SIGNAL CATALOG

Format per signal: **rationale · construction (our exact fields) · forces · horizon · persona · VALIDATION GATE · academic grounding · failure mode.** "Validated:false" until its gate passes — an unvalidated signal renders as *diagnostic context*, never as an actionable card. This is the "no score for error" discipline.

The catalog refines the six placeholders in `MARKET_FORCES_MESH.md §3` with exact construction + gates.

---

### S1 — `CONVICTION_ADD`  (the flagship buy-side confluence)
- **Rationale (plain):** When the analysts, the breadth of fund owners, AND the rupees funds are actually moving all point up together, that's the highest-conviction, most durable accumulation — the cross-force agreement the whole mesh exists to find.
- **Construction:** `flow.level_3m > 0` AND `flow.trend == accelerating` AND `breadth.trend > 0` (dbreadth>0, buyers>sellers) AND `analyst.trend_3m ≥ 0` AND `analyst.level ≥ 50`. Score = `z(flow.intensity_pctile) + z(breadth.trend_pctile) + z(analyst.trend_pctile)`.
- **Forces:** flow + breadth + analyst (price as confirmation).
- **Horizon:** medium (1-6M). **Persona:** FM (own-book leader), Analyst (street-turning).
- **VALIDATION GATE:** signal-backtest the decile of `CONVICTION_ADD` score vs forward 3/6/12M TR, all-start months vs ≥10k random portfolios, dead names retained. Pass = positive non-reversing spread that **survives a size/value control** and **beats a plain high-ARM long** (else it's just ARM repackaged). Lead-lag/Granger: flow must *precede* return, not co-move.
- **Grounding:** Chen-Hong-Stein (rising breadth bullish, +4.95% char-adj/yr); Yan-Zhang (short-term-institution flow forecasts, non-reversing); Wermers copycat; our own ARM IC.
- **Failure mode:** trend-chasing mirage (flow co-moving with price, not leading) → caught by the Granger gate. Also can load on small-caps where flow edge is strongest but costs eat it.

### S2 — `SMART_MONEY_AHEAD`  (FM leading the street; Q3)
- **Rationale:** Funds are accumulating *before* analyst upgrades arrive — the informed-money-ahead-of-consensus case.
- **Construction:** `flow.level_3m > 0` AND `flow.trend == accelerating` AND `analyst.level < 50` (or `analyst.trend_3m ≤ 0`). (REUSE: this is `screens.py` quadrant 3.)
- **Forces:** flow + analyst. **Horizon:** medium. **Persona:** FM.
- **GATE:** does Q3 beat Q2 (`STREET_AHEAD`) forward? Backtest both quadrant labels separately on the panel; confirm Q3's forward TR > Q4 and ideally > Q2. Confirm flow is corp-action-immune on merger/swap names (REUSE the bridge).
- **Grounding:** Yan-Zhang (short-term institutions informed); Wermers smart-money (~1 quarter).
- **Failure mode:** the smart-money edge is only ~1 quarter (Frazzini-Lamont) — if held longer it decays; tag strictly medium and refresh.

### S3 — `STREET_AHEAD`  (catch-up OR trap; Q2)
- **Rationale:** Analysts are upgrading but funds aren't buying — either funds will catch up (entry) or the street is wrong (trap).
- **Construction:** `analyst.level ≥ 50 AND analyst.trend_3m > 0` AND `flow.level_3m ≤ 0`. (REUSE quadrant 2.)
- **Forces:** analyst + flow. **Horizon:** immediate-medium. **Persona:** Analyst, FM.
- **GATE:** ambiguous by design — backtest must report its forward TR is *materially below* Q1/Q3 (else it's not a distinct label). Ship only as *watch*, never a buy, until proven.
- **Grounding:** ARM reversal asymmetry (−0.155 when revisions reverse) → "street ahead" is exactly where a reversal can bite.
- **Failure mode:** the analyst-only euphoria that La Porta/diagnostic-expectations says under-performs at >1Y.

### S4 — `VALUE_x_REVISION`  (the Ambit confluence — highest-conviction in the local evidence)
- **Rationale:** Cheap + improving-revisions is the single best-documented India combo (~9% alpha over BSE200 since 2006, 79% of 3y windows beat vs 55% for value alone) because value (slow) and revision (fast) are ~−0.5 correlated and cover each other's drawdowns.
- **Construction:** Universe → value quintile from cross-sectional `fundamentals.value_pctile_xs` (z-composite of E/P, B/P, S/P, div-yield — Ambit's value score; **NEW: build the composite**, we currently only have own-history P/E percentile). Take cheapest quintile; within it rank by `analyst.trend_3m` (our richer ARM-as-revision proxy). Apply a Q1/Q2 safety-net hold rule to cut churn.
- **Forces:** fundamentals(value) + analyst(revision). **Horizon:** medium entry, >1Y hold cycle. **Persona:** FM, CIO.
- **VALIDATION GATE:** **This is Ambit's number, NOT ours.** Must signal-backtest `VALUE_x_ARM` on our TR panel (all-starts vs ≥10k random, ≥5y windows) before it drives a card. Confirm (a) the value z-composite produces a sensible cheap quintile, (b) the combo beats value-alone and ARM-alone, (c) the −0.5 value/revision correlation reproduces on our data.
- **Grounding:** Ambit Jan/Apr-2026; Asness QMJ (quality diversifies value+momentum); StarMine ARM construction.
- **Failure mode:** (1) ARM ≠ Ambit's raw 6M-forward-EPS-revision — it's a *different, richer* proxy, so the 9% won't transfer 1:1 (flag explicitly). (2) In a true panic the −0.5 correlation → +1 and the pair draws down together (structural, not absolute hedge). (3) Single-name hit-rate is only ~50% — it's a portfolio tilt, never "this stock will beat."

### S5 — `CROWDED_REVERSAL`  (de-risk flag — confluence GATE, not single-trigger)
- **Rationale:** High crowding alone is NOT bearish (Wermers: herd-bought beats herd-sold +4%/6M). Reversal reliably attaches only to **forced/rolling-over** crowding. So fire ONLY at the confluence of fragility + positioning rolling over + analyst turning down.
- **Construction — all three must hold:** (1) **Fragility:** `breadth.pctile ≥ 80` OR `ownership.mf_pct_mcap` top-quintile. (2) **Rolling over:** `breadth.trend < 0` (dbreadth<0) AND `flow.level_1m < 0` AND `flow.level_1m < flow.level_3m/3` (decelerating vs run-rate) AND `breadth.at_own_peak` recently true. (3) **Analyst down:** `analyst.trend < 0` (ideally with `analyst.level` still high). Score = `z(fragility) · max(0,−z(dbreadth)) · max(0,−z(analyst.trend))`.
- **Forces:** breadth + ownership + flow + analyst. **Horizon:** medium for the rollover; fragility/fire-sale reversion tail extends >1Y (Coval-Stafford 24M). **Persona:** CIO (book-level de-risk), FM.
- **VALIDATION GATE:** signal-backtest the *gated* flag's forward TR (all-starts) — must show **negative/below-index** forward drift, **add beyond a plain low-ARM short**, NOT just reproduce small-cap beta (size-control mandatory). Verify dbreadth<0 + flow-flip **precede** underperformance, not coincident.
- **Grounding:** Coval-Stafford (forced fire-sale, 24M reversion); Greenwood-Thesmar (concentration→fragility→negative returns); Frazzini-Lamont dumb-money; our herding ρ≈−0.10.
- **Failure mode:** firing on healthy continuation-herding (Wermers) → the three-way gate prevents it. We have **no leverage data**, so the Aug-2007 crowded-factor-crash dimension is only a *fragility proxy* (confidence medium) — must be labeled as such, not "systematic crowding measured."

### S6 — `OVER_OPTIMISM_FADE`  (slow, long-horizon de-rate)
- **Rationale:** The most euphoric-expectation, richly-valued names disappoint slowly (diagnostic expectations / La Porta) — a >1Y fade, a *different clock* from S5.
- **Construction:** `fundamentals.value.pe_pctile_own10y ≥ 80` (rich) AND `fundamentals.pat_cagr_5y` top-quintile (past growth high = euphoria) AND `analyst.trend_3m < 0` (consensus turning). Rank = `z(valuation) + z(past_growth) − z(analyst.trend)`.
- **Forces:** fundamentals + analyst (MF-%-mcap overlay). **Horizon:** long (>1Y). **Persona:** CIO.
- **VALIDATION GATE:** **flag-validation framework** (predictive validity for the *left tail* of forward 1-3Y returns), NOT a NAV backtest. Confirm the fade survives controlling for plain value. **Caveat the proxy gap:** trailing CAGR is a weak stand-in for analyst LTG forecasts.
- **Grounding:** La Porta (1996); Bordalo-Gennaioli-Shleifer diagnostic expectations.
- **Failure mode:** trailing growth ≠ forward LTG forecast (we lack LTG); risk of just re-discovering plain value/growth — the value-control in the gate guards this.

### S7 — `OWNERSHIP_ROTATION`  (regime context, NOT a forward buy)
- **Rationale:** FII↓/DII↑ (or reverse) at a fundamentals inflection = ownership-regime context. In India FII flow is *positive-feedback* (an effect of returns), so this is **contemporaneous regime**, never a standalone forward predictor.
- **Construction:** `ownership.rotation` from `fii_chg_1y_pp` vs `dii_chg_1y_pp` signs, cross-referenced with `fundamentals.pat_accel` inflection.
- **Forces:** ownership + fundamentals. **Horizon:** immediate sentiment / medium rotation. **Persona:** CIO, FM.
- **VALIDATION GATE:** **lead-lag/Granger on India data** confirming FII-flow→return is weak/positive-feedback *before* exposing any "follow FII." Ship only as regime context with the trend-chasing caveat in the UI.
- **Grounding:** Chakrabarti (2001), Griffin (2002) (FII = effect of returns); the India study (Δforeign-ownership negatively related to forward returns).
- **Failure mode:** the seductive contemporaneous FII-NIFTY correlation tempts a "follow FII" actionable that is buying past returns — the Granger gate is mandatory.

---

## 3. INTERACTION / FEEDBACK MAP

### 3.1 Confirm vs contradict (which forces agree)
```
                    CONFIRMS forward edge when ↑ together         CONTRADICTS / tension
  analyst.trend  +  flow.trend      → CONVICTION_ADD (S1)         analyst↑ + flow↓  → STREET_AHEAD trap (S3)
  flow.trend     +  breadth.trend   → durable accumulation        flow↑(value) + breadth↑(positions) but
                                                                   1 fund driving → false breadth (gate)
  analyst        +  value(cheap)    → VALUE_x_REVISION (S4) ★      analyst↑ + value(rich) + crowding↑
                                       the −0.5 corr = the hedge     → OVER_OPTIMISM_FADE setup (S6)
  fundamentals.accel + flow.trend   → fundamentally-backed flow    accel↓ while flow↑ → late-cycle chase
```
Key asymmetry to encode: **agreement of CHANGE across forces = conviction; agreement of LEVEL across forces (everyone already in, breadth at peak) = crowding risk.** Same direction, opposite meaning depending on level-vs-change. The mesh's `{level, trend, pctile}` split is precisely what lets a card say "all the *trends* agree up (add)" vs "all the *levels* are maxed and trends rolling over (de-risk)."

### 3.2 The crowding → reversal feedback loop (the thing to watch)
```
  rising flow + rising breadth  →  breadth approaches own peak (LEVEL pctile → 90+)
        ↓ (continuation, Wermers — still bullish, DO NOT fade yet)
  breadth at peak + flow decelerating (1m < 3m run-rate) + ARM rolling over
        ↓  ← THIS is the trigger, not crowding level alone
  forced/voluntary exit begins (dbreadth<0, flow-flip negative)
        ↓
  fragility realized → fire-sale price pressure (Coval-Stafford), reverts over ~24M
```
The mesh watches the **second derivative**: it's the *transition* from "crowded-and-still-rising" (hold) to "crowded-and-rolling-over" (de-risk, S5) that matters. `breadth.at_own_peak` + `flow` deceleration + `analyst.trend<0` is the tripwire. Crowding level by itself only raises the *fragility* prior; it never fires S5 alone.

### 3.3 Portfolio-level co-crash (CIO lens, flagged-not-measured)
Crowded factor trades unwind together (Aug-2007). We have **no leverage/positioning data**, so at the book level the mesh can only surface *fragility co-movement* (how many of a fund's holdings are simultaneously in `CROWDED_REVERSAL`) as a medium-confidence proxy — explicitly **not** a measured systematic-crowding number.

---

## 4. FUND-TIE METHOD

Joins a scheme's holdings to each stock's mesh state. **REUSE:** `funds_flows._load()` (the holdings store, vst_id-keyed), `build_equity_books()` (per-fund `{vst_id, pct}`), `_pair_flows()` (the per-fund-per-stock corp-action-immune flow), and the mesh `_index.json`. The fund-tie is a **NEW thin join module** `vistas/mesh_funds.py`; it computes nothing new about forces — it aggregates the mesh over a fund's book.

**The join key is `vst_id`** (the identity layer), not symbol — both the mesh and the holdings store carry `vst_id`. This avoids the symbol-collision class of bugs.

### 4.1 %book / %AUM per quadrant
For fund `f` at month `t`: take its equity book `{vst_id → pct}` (REUSE `build_equity_books`). For each held vst_id, look up its mesh signal/quadrant. Then:
- `pct_book_in_quadrant[q] = Σ pct` over holdings whose `(analyst,flow)` quadrant = q (Q1..Q4 as in `screens.py`).
- `pct_aum_in_signal[s] = Σ (market_value)` over holdings firing signal s, ÷ fund equity AUM.
Output: "% of this fund's equity book that is in CONVICTION_ADD / CROWDED_REVERSAL / VALUE_x_REVISION."

### 4.2 Flow-adjusted additions per window
The fund's *own* net active flow per held stock, per window (1/3/6/12M) — **REUSE** `_pair_flows(ym_to)` which already returns per-`(navindia_code, vst_id)` `flow = mv_e − mv_s·(1+r)`. Cross-tab the fund's additions against each stock's mesh quadrant: "this manager added ₹X cr into CONVICTION_ADD names and sold ₹Y cr out of CROWDED_REVERSAL names over 3M." This is the manager's *forward posture* in mesh terms — corp-action-immune by construction (the metric's whole point).

### 4.3 Anti-consensus intensity
**REUSE** the per-fund herding engine (`build_fund_series`): `herding_avg` (trade-size-weighted sign-agreement vs the ex-self crowd, validated ρ≈−0.10 vs excess return) and `contrarian_pctile`. The fund-tie surfaces it as "how much is this manager leaning against the crowd," and crucially **crosses it with the mesh**: anti-consensus *into* CONVICTION_ADD names = informed leadership; anti-consensus *into* CROWDED_REVERSAL names that the manager is *buying* = a warning. Anti-consensus is conviction-scaling context, **not** a factor-timing switch (Asness: factor timing is deceptively difficult — encode it as a conviction dial, never an on/off).

### 4.4 Time-windowed manager isolation
**REUSE** `build_fund_series(end_ym=...)` and `_pair_flows` accept an end month; the store is monthly back to 2013. To isolate a *manager's* posture (not the fund's legacy book): compute all fund-tie aggregates over a **chosen [start_ym, end_ym] window** (the manager's tenure), using only flows/additions inside that window — so an incoming manager isn't judged on the prior manager's inherited positions. The flow-adjusted-additions view (4.2) is naturally tenure-isolatable because it measures *moves*, not the standing book.

**Honest labels (must carry through, per the codebase's own discipline):** MF ⊂ DII (never summed); "Public %" ≠ retail; Active Share scoping guards already in `build_active_share` apply when the fund-tie shows differentiation.

---

## 5. PHASE-3 BUILD SPEC

Ship in vertical slices, each independently probe-verified and reversible. Order = substrate first, then the 2-3 highest-evidence signals validated-then-shipped.

### Step 1 — Bake the mesh state (`vistas/mesh.py`) — NEW module, no new fetch
1. `_arm_symbol_series()` → lift/import from `arm_backtest` to get the **ARM step-series**; compute `analyst.trend` / `trend_3m` (the genuine new compute, §1.3).
2. For each universe symbol: read `quant/<SYM>.json` (flow, fund_holders, ownership, market) + `fundamentals/<SYM>.json` (growth/valuation/quality) — all already built in the same deck process. Assemble `forces.{analyst,flow,breadth,ownership,fundamentals,price}` as `{level, trend, pctile}`.
3. Compute cross-sectional pctiles over the month's universe; build `value_pctile_xs` (the **NEW** E/P+B/P+S/P+div-yield z-composite — the only new fundamental field).
4. Evaluate the signal catalog → `signals[]` with `validated:false` initially.
5. Write `data/_mesh/<SYM>.json` + a compact `data/_mesh/_index.json` (symbol → forces-pctiles + fired-signals) for cross-sectional ranking and the fund-tie.
6. Probe: deck runtime smoke-test; spot-check 5 names against `screens.py` numbers for agreement.

### Step 2 — Wire the mesh into `screens.py` (REUSE, don't duplicate)
`screens.py` reads `data/_mesh/_index.json` instead of recomputing flow/breadth/ownership — single source of truth. The Smart-vs-Street screen becomes one *view* over the mesh.

### Step 3 — Validate-then-ship the FIRST signals
Ship the validation harness wins, in evidence-priority order. **Until a gate passes, the signal renders as diagnostic context, not an actionable.**

**FIRST: S4 `VALUE_x_REVISION`** — highest local evidence (Ambit ~9%), and it stress-tests the value-composite. **SECOND: S1 `CONVICTION_ADD`** — the flagship cross-force buy, tests the flow-leads-return question. **THIRD: S5 `CROWDED_REVERSAL`** — the de-risk flag, tests the crowding→reversal feedback. (S2/S3/S7 ship as labeled context; S6 uses the flag-validation harness later.)

**Backtest design (one harness, applied per signal):**
- **REUSE** the `signal-backtest` skill discipline (the project's reference is the FFT `full_backtest()`; our cross-sectional analogue is `arm_backtest.run()`). For S1/S4: monthly cross-sections, score deciles vs forward 3/6/12M TR, **all start months**, **≥10k random portfolios** as the null, judge the **distribution of percentile-vs-random across starts** (not one path, not CAGR alone), fixed ≥5y windows.
- **Survivorship-free:** dead names retained in the research panel (live cards exclude them) — else breadth/flow signals look better than they are.
- **Controls (mandatory):** size and value controls; for S1, must beat plain high-ARM; for S5, must add beyond a plain low-ARM short and not be small-cap beta; **lead-lag/Granger** for any flow-driven signal (flow must precede return).
- **Pass bar:** positive, non-reversing spread, survives controls, sign + rough magnitude consistent with the cited literature (replicate the *sign*, don't import US magnitudes).
- For S6 later: **flag-validation** harness (predictive validity for the left tail), not NAV.

### Step 4 — Fund-tie (`vistas/mesh_funds.py`) — REUSE the flow/herding/books engines
%book-per-quadrant, flow-adjusted additions per window, anti-consensus×mesh cross, time-windowed manager isolation (§4). Powers the FM/CIO cards.

### Step 5 — Persona actionable surfaces (the existing roadmap #38/#39)
Analyst = `analyst.trend` breadth + where-the-street-is-turning (<1M). FM = own-book-vs-mesh quadrant + anti-consensus intensity (1-6M). CIO = aggregate posture + crowding/co-crash fragility (>1Y). Each card stamped with its horizon, its validation status, and Definition·Method·Why.

### Step 6 — Codify the skill (§6).

**Reversibility:** every step writes new `data/_mesh/*` files and reads existing JSONs — nothing mutates the validated engines (`analytics.py`, `funds_flows.py` formulas) — so any step can be dropped without touching the live terminal.

---

## 6. SKILL OUTLINE — `multi-force-mesh` (reusable methodology)

The reusable disciplines, abstracted from this build:
1. **Level vs Change vs Percentile, always split.** Never collapse a force into one number; store `{level, trend, pctile}`. The forward edge is almost always the *change*; the level is context or crowding.
2. **Every signal carries a horizon tag and you never mix clocks.** Fast (flow/revision, 1q-6M) and slow (dumb-money/over-optimism, 1-3y) are different signals even on the same force.
3. **Confluence-gate reversal signals; never fire on a level alone.** Crowding/herding is bullish until it *rolls over* (fragility + positioning-down + analyst-down together). Watch the second derivative.
4. **Validate before actionable ("no score for error").** signal-backtest (all-starts × ≥10k random, fixed windows, survivorship-free) for NAV-type edges; flag-validation (left-tail predictive validity) for risk flags; **lead-lag/Granger** for any flow signal (flow must lead, not co-move) to avoid trend-chasing mirages.
5. **Mandatory controls:** size + value + "beats the single-force baseline." A combined signal must beat its strongest component or it's repackaging.
6. **Pair anti-correlated forces for path, not just return** (value −0.5 revision); but label the hedge **structural, not absolute** (correlations → 1 in a panic).
7. **Conviction-scaling, not factor-timing** (Asness): use cross-force state and crowding to size, never to switch sleeves on/off.
8. **Replicate the SIGN on your own data; never import foreign magnitudes** (US 13F effect sizes ≠ India MF-panel magnitudes).
9. **Honest identity & label discipline:** join on `vst_id`; MF ⊂ DII; "Public" ≠ retail; corp-action-immune flow (`end − start·(1+TR)`) + merger bridge.
10. **Display-plane, single source of truth:** bake forces once (Python), assemble signals from the baked substrate, no JS-parity port; downstream views *read* the mesh, never recompute it.

---

## 7. FLAGGED — what the research left UNVERIFIED (must not become a silent assumption)

1. **The ~9% Ambit `VALUE_x_REVISION` alpha is NOT ours.** It is BSE200, Ambit's raw 6M-forward-EPS-revision factor. We have StarMine ARM (a *different, richer* composite) and **no value z-composite yet**. Treat as a hypothesis to backtest, not an in-house number.
2. **ARM ≠ Ambit's revision factor.** Different construction; the 9% won't transfer 1:1.
3. **Our ARM IC (0.030 @1M) is same-order-of-magnitude as StarMine's India 0.050 — NOT a replication.** Already corrected once after KV pushback; keep the caveat.
4. **The −0.155 ARM reversal IC is conditional on knowing the next revision reverses** — it is the *cost of being wrong about persistence*, not an ex-ante short rule. It motivates reading direction + refreshing weekly; it is not "short when IC negative."
5. **No leverage/positioning data** → the Aug-2007 crowded-factor co-crash is a *fragility proxy* only (medium confidence), never a measured systematic-crowding number.
6. **India FII flow forward-predictivity is unverified on OUR panel** — the literature says positive-feedback; the Granger gate on S7 must run before any "follow FII."
7. **`breadth.at_own_peak` and the analyst step-series TREND are NEW computes** not yet in the codebase (the step-series exists in `arm_backtest`, the peak-detector does not).
8. **MF-holdings panel starts 2013, ~1,032 stocks** — shorter cross-section than the US studies; magnitudes will differ; re-validate.
9. **`OVER_OPTIMISM_FADE` uses trailing CAGR as a proxy for analyst LTG forecasts** (we lack LTG) — a real proxy gap to caveat.
10. **The herding ρ≈−0.10 is a fund-level diagnostic, not a forward-tested stock edge** — don't let it silently justify a stock-level CROWDED_REVERSAL without S5's own gate.

**Relevant file paths:** `C:/Users/Administrator/Documents/Projects/Vistas/MARKET_FORCES_MESH.md` (design log to update), `vistas/screens.py` (the Smart-vs-Street view to re-point at the mesh), `vistas/funds_flows.py` (REUSE: flow/breadth/herding/books/holders engines), `vistas/stock_intel.py` (REUSE: per-stock market/ownership/fundamentals assembly), `vistas/fundamentals.py` (REUSE: growth/valuation/quality fields), `vistas/arm_backtest.py` (REUSE `_arm_symbol_series` for the analyst TREND + `run()` as the cross-sectional backtest template). NEW modules to create: `vistas/mesh.py`, `vistas/mesh_funds.py`; NEW data: `data/_mesh/<SYM>.json` + `data/_mesh/_index.json`. NEW skill: `multi-force-mesh`.