# WORKPLAN — live session tracker (2026-06-26)

> Single source of truth for what's in flight, broken into sub-steps so the big builds
> don't get lost behind in-the-moment side-quests. `[x]` done · `[~]` in progress · `[ ]` to do.
> Mirrors the task widget (task #s in brackets). KV asked for this 2026-06-26.

## ⛔ CURRENT BLOCKERS / IN-FLIGHT
- **A full terminal build is RUNNING** (PID 35268, started 20:56). Holds `data/_refresh/.build.lock`.
  - It will regenerate the shell → **surfaces the new valuation charts (EV/EBITDA·P/S·EV/Sales·P/B·DY·FCFy)** when it completes+publishes. Wiring already correct; was just a stale shell.
  - **RULE: no second build, no edits to `static/vistas.js` / `vistas/*.py` build-inputs until this lock releases.**
  - As of ~21:47 still running (~50 min in, 2.7 GB; shell not yet rebuilt). Re-check `.build.lock` before any edit/build.
- **W2 Mesh research workflow RUNNING** (`mesh-multiforce-research`, read-only — safe alongside the build).
- **W7 breadth DESIGN workflow DONE** → `ASSET_ALLOCATOR_BREADTH_SPEC.md` + shopping list (global-ETF breadth needs constituents we lack).

## ★ THE 5 WORKSTREAMS (this session — finish all)

### W1 — Quadrant ROTATION over time  [#44, subsumes #38]  ·  TOP PRIORITY
How an entity sits in the Analyst(ARM)×FM(flow) quadrant **over time** (monthly trail), relative to self + peers.
Data CONFIRMED available + shippable (raw ARM published w/ ABSL sign-off; flow already shipped).
- [ ] a. Stock trail in the **Screen**: bake per-stock monthly ARM (ffill) + flow history into `smart_vs_street.json`; JS draws the trajectory (line+fading markers) with a month slider.
- [ ] b. **Portfolio centroid** (holding-weighted ARM×flow) per month for: individual **fund** scheme · whole **AMC** book · **category** — from `holdings_history.parquet` (158mo, key `vst_id`), equity sleeve renormalised.
- [ ] c. **Peer overlay** (other funds in category / other AMCs / category benchmark as static peer) + "relative to self" = own-history percentile.
- [ ] d. Build + browser-probe verify + (after lock free) rebuild + publish.

### W2 — The FM BRAIN: multi-force, interaction-aware, novel-per-manager  [#41 Mesh ⊕ digital-AMC FM redesign]
Fixes BOTH the dead Mesh signal AND KV's "FMs are ARM-clones" critique. Confirmed: pilots are identical ARM water-fill, only mandate caps differ (`amc_firm.build_rules_v0` line 516 `score=arm`). Equal-weight blend FAILED because it *dilutes* ARM (IC 0.071→0.054).
- [x] a/b/c. **Research workflow DONE + VALIDATED** (`MESH_RESEARCH_FINDINGS.md`): a multi-force combo BEATS ARM-alone robustly walk-forward (+0.037 OOS IC@6m, 100% of OOS 5y windows). Edge source = **momentum (0.098) + value, near-orthogonal to ARM (0.080)** — momentum alone already beats ARM; ARM+mom+value ≈ 0.12. Defensible = simple ARM+momentum+value (orthogonalized-residual), not fragile Σ⁻¹. ⇒ FMs must be multi-force, not ARM-only (vindicates KV).
- [ ] d. **Distinct FM lenses**: give each virtual FM a *different* multi-force brain (not ARM-clone). Tie each to the Fundamental Law (IC·√BR·TC).
- [ ] e. Wire the lenses into `amc_firm` / `amc_replay` construction.

### W3 — SURFACE the Digital AMC  [#69, uses #68 replay]
Built-but-hidden artifacts exist on disk; the live `digital-amc/` page still shows the old P0 floor only.
- [ ] a. FM-brain redesign LIVE in the books (depends on W2d/e).
- [ ] b. Surface **scheme NAV** (`amc_book/<amc>/<scheme>/replay/nav.csv`) + **daily fact sheet** (`daily/*.json`) panels.
- [ ] c. Surface **trade register** (`blotter.jsonl`) as an audit trail.
- [ ] d. Surface **scorecards** (`replay/scorecard.json`: IC·TC·IR vs benchmark + real scheme NAV + Fundamental-Law decomposition).
- [ ] e. Extend `amc_site.py` build → rebuild + validate + publish digital-amc.
- LICENSING: weights/play-types/aggregate IC/TC/NAV OK; **never** per-stock raw ARM.

### W4 — Quick wins
- [ ] #47 Cycle-position percentile system (own-history for snapshots, cross-sectional for aggregates).
- [ ] #49 "Rebase to view" vs "Absolute" toggle on level/NAV charts.
- [ ] #51 Flow decomposition (weight-space net-active; 3 switchable figures) — spec'd in `FLOW_DECOMPOSITION.md`.
- [ ] #81 Unify benchmark sector-tilt taxonomy (fund disclosed vs benchmark macro) on the broadened map.

### W5 — Deep equity-analyst engine  [#62/#64/#65]  (substrate #63 done)
- [ ] Build `vistas/equity_research.py` deterministic engine → validate on RELIANCE / an IT name / a bank.

### W7 — Asset Allocator tab: market BREADTH  [NEW, KV 2026-06-26]
- [x] a. DESIGN workflow DONE: best setup (% multi-yr high/low + %>200DMA validated best) + data inventory + 6-item shopping list + informativeness validation → `ASSET_ALLOCATOR_BREADTH_SPEC.md`.
- [ ] b. Engine `vistas/breadth.py`: per-date {market, per-sector} breadth = %@new N-yr high/low, %>200DMA, NH−NL, %golden-cross (from stock TR panel + sector map; price-derived, licensing-clean).
- [ ] c. New **Asset Allocator** tab: market breadth chart (1/3/5y toggle) · per-sector heatmap · **per-sector "≥ m% broke out / golden-crossed" screen (m = user input)** · global breadth (proxy now; constituents = shopping list).
- [ ] d. **MOVE Analyst Consensus Flow** panel from Macro → Asset Allocator tab.
- [ ] e. Global-ETF constituent data → `SHOPPING_LIST.md`.

### W6 — Verify-and-close
- [ ] #39 Actionable aggregation: fund-manager recommendations from Analyst×FM×Market view.
  - [ ] a. Per-fund quadrant bets (where is each fund leaning).  [overlaps #45 — verify not duplicate]
  - [ ] b. Aggregate across funds → "smart-money consensus actionables".
  - [ ] c. Surface (Screen or Funds tab) or fold into W1/W3; close if superseded.
- [x] Valuation charts (#77/#78) — surfaced by the in-flight build (stale-shell, not a bug).

## 🪧 SIDE-QUEST / INTERRUPTION LOG (why the big tasks slipped — so nothing's lost)
- Valuation charts not visible → diagnosed: stale shell, in-flight build surfaces them. ✅
- FederalBank 1.6× mcap gap → explained (derived current vs AMFI 6-mo-avg). ✅
- MoneyBall rename + move below sector chart. ✅
- Nippon sector coverage (Unclassified 18.9%→3.7%). ✅ (#79)
- Tilt hover/label desync pinned. ✅ (#80)
- Task-status reconciliation (the "running" widget alarm). ✅

## SEQUENCING  (KV 2026-06-26: EASY FIRST, complex LAST — long-stuck complex tasks need a big context window, do them after the quick wins so compaction can't disrupt mid-build)
Everything that EDITS build-input files is gated on the in-flight build (PID 35268) releasing `data/_refresh/.build.lock`.
**Compaction safety:** the complex RESEARCH (W2 Mesh/FM-brain, W7 breadth design, later W5) runs in ISOLATED WORKFLOW contexts — immune to my main-thread compaction; their durable outputs are `.md` spec docs. So a compact mid-session does NOT lose their progress. Only the final integration/edits run on the main thread.

Order on lock-free:
1. **EASY first (one batch):** #49 rebase toggle · #81 tilt-taxonomy unify · #51 flow-decomp · #47 cycle-position · relocate Analyst Consensus Flow (W7d). [valuation charts already surfaced by the in-flight build]
2. **MEDIUM:** W1a stock rotation trail · W7 breadth tab (after spec lands) · W1b/c portfolio centroids · #39 verify/close.
3. **COMPLEX last (need W2/W7 research outputs):** W3 digital-AMC surfacing + FM-brain rewire · W2 Mesh integration · W5 deep analyst engine.
All terminal builds serialize through the one lock. Batch the edits → ONE full `publish_terminal.py` rebuild at the end (KV: no shortcut, full rebuild).
