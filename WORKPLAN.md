# WORKPLAN — live session tracker (2026-06-26)

> Single source of truth for what's in flight, broken into sub-steps so the big builds
> don't get lost behind in-the-moment side-quests. `[x]` done · `[~]` in progress · `[ ]` to do.
> Mirrors the task widget (task #s in brackets). KV asked for this 2026-06-26.

## ✅ UNBLOCKED — 2026-06-26 ~22:20
- **The in-flight build FINISHED + PUBLISHED CLEAN** (commit `a93e9cbeb` 22:15, tree clean). Lock released.
  - **Valuation charts are LIVE** (EV/EBITDA·P/S·EV/Sales·P/B·DY·FCFy in Fundamentals — `valMultiChart` ×7 in the published shell). #77/#78 ✅ R6 closed.
- All four research/spec docs on disk: `FLOW_DECOMPOSITION.md`, `ASSET_ALLOCATOR_BREADTH_SPEC.md`, `MESH_RESEARCH_FINDINGS.md`, `FUNDAMENTAL_LAW.md`.

## 🧭 EXECUTION PLAN (post-unblock — easy first, complex last, batched into builds)
**Two independent publish paths:** (1) TERMINAL (`publish_terminal.py` → vistas.js + engines + baked globals) and (2) DIGITAL-AMC (`amc_site.py` → /digital-amc/). They don't share a build, so run them as two batches.

- **WAVE 1 (parallel agents, DISTINCT files, engines+data only — NO builds, NO lock, NO JS):**
  - A1 flow-decomp engine → `vistas/funds_flows.py` (+ data output) per `FLOW_DECOMPOSITION.md`  [#51]
  - A2 tilt-taxonomy unify → `vistas/funds_portfolio_viz.py`  [#81]
  - A3 rotation data → `vistas/screens.py` traj + NEW centroid engine (+ data output)  [#44/#86/#87/#88]
  - A4 breadth engine → NEW `vistas/breadth.py` (+ data output) per `ASSET_ALLOCATOR_BREADTH_SPEC.md`  [#W7b]
  - A5 FM multi-force lenses → `vistas/amc_firm.py` + `vistas/amc_replay.py` per `MESH_RESEARCH_FINDINGS.md`  [#85/W2]
  - A6 digital-AMC surfacing → `vistas/amc_site.py` (books/NAV/blotter/scorecards)  [#69/W3]
- **WAVE 2 (single integration pass — shared files `static/vistas.js` + the bake-wiring):** wire all new baked globals + all UI (rebase toggle #49, cycle-position #47, flow-decomp UI #51, NEW Asset-Allocator tab + breadth charts + MOVE Consensus panel W7d, rotation trail UI W1a).
- **WAVE 3 builds:** (1) `publish_terminal.py` full rebuild → validate → publish; (2) digital-AMC site build → validate → publish.
- **LAST / complex:** W5 deep equity-analyst engine (`vistas/equity_research.py`); #39 actionable aggregation (verify-or-fold).

## ▶▶▶ LIVE STATE — 2026-06-26 (post all engine agents)  ·  full detail in `INTEGRATION_CONTRACTS.md`
**ALL 7 ENGINE AGENTS DONE:** A1 flow-decomp ✓ · A2 tilt ✓ · A3 rotation ✓ · A4 breadth ✓ · A5 FM-brains ✓ · A6 amc-site-UI ✓ · W5 deep-analyst engine ✓.
**MY deck/engine edits DONE:** deck.py = breadth bake (`window.VISTAS_BREADTH`, fresh-build) + rotation centroids wiring + tilt hook 1 (canonical fund equity book). benchmarks.py = tilt hook 2a (future-proof; benchmark side already macro — re-tag no-op). 
**TWO AGENTS STILL RUNNING:**
  - `aa0430a0dfefaa25f` = JS front-end (sole vistas.js owner): Asset-Allocator tab + breadth charts + m% screen + MOVE Consensus + flow-decomp 3-way toggle + rebase toggle + **rotation UI (sent via SendMessage)**. Will run `node --check`.
  - `a4609b3897de06489` = digital-AMC: re-run replay w/ 4 brains + add benchmark-NAV series to amc_replay + rebuild amc_site (output/_amc/site). NOT publishing.
**ALL 8 AGENTS DONE** (incl. digital-AMC replay re-run). FINISH SEQUENCE (one push ships BOTH sites — publish_site() does `git add -A` on _pages, so staging digital-amc/ before --no-rebuild publishes terminal+digital-amc together):
  1. ✅ **TERMINAL BUILD RUNNING (bkdf5nehc, background):** `python publish_terminal.py --no-push --no-fetch` (FULL rebuild — re-inlines vistas.js, bakes VISTAS_BREADTH, builds rotation centroids + breadth fresh, runs Node validation; NO publish yet). --no-fetch is the sanctioned feature-publish path (today's 20:00 pipeline already fetched; the 4 degraded feeds would only hang). Full REBUILD still happens (not the banned bake-only shortcut).
  2. On build done → `node _pup_allocator.js` (verify Asset-Allocator tab + rotation render in real Chromium, 0 errors — the stub can't see the new tab). Also _deck_runtime_test/_parity already PASS (JS agent ran them).
  3. `cp output/_amc/site/index.html _pages/digital-amc/index.html` (stage the new digital-AMC site — new brains + NAV-vs-benchmark + blotter + scorecards). NOTE the honest IR finding: brains raised IC on 3/4 desks but realized IR FELL where TC leaked (ABSL 0.79→0.37 [old was β-tilt], Quant −0.35→−0.47); SBI improved all axes. Licensing grep = 0 raw ARM.
  4. If probe green → `python publish_terminal.py --no-rebuild` → robocopy terminal_site→_pages/terminal/, `git add -A` (picks up terminal/ + digital-amc/), commit, push BOTH live; then backup_codebase() (step 5/5) → vistas-codebase + arm encrypted mirror. If probe RED → fix the JS via the JS agent (SendMessage aa0430a0dfefaa25f), rebuild, re-probe.
**DEFERRED (clean follow-ups, NOT blockers):** W5 dossier panel surfacing (bake data/research/<SYM>.json + JS panel) · #81 hook 3 (Funds-tab granular path, needs funds regen) · #39 actionable aggregation (verify-or-fold vs #45) · digital-AMC: TC-aware brain implementation (turnover/constraint control — the IC-up-IR-down finding).

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
