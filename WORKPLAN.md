# WORKPLAN — live session tracker (2026-06-26)

> Single source of truth for what's in flight, broken into sub-steps so the big builds
> don't get lost behind in-the-moment side-quests. `[x]` done · `[~]` in progress · `[ ]` to do.
> Mirrors the task widget (task #s in brackets). KV asked for this 2026-06-26.

## 🚧 IN FLIGHT — 2026-06-27 (two builds, both checkpointed)
### A) LIVE-FORWARD LLM cadence (#52/#69/#93-96) — engine DONE+validated, round PENDING
- Design = `LIVE_FORWARD.md`. Engine = **`vistas/amc_live.py`** BUILT + smoke-validated: `prepare_desk` (FM
  desk: inherited book + candidate universe w/ ARM/z_mom/z_val/brain_score/flow + quant baseline + mandate +
  scorecard, no look-ahead) · `enforce_guardrails` (reuses `deploy_with_floor`; faithfully reproduces the
  quant baseline at active-share 0%, keeps any LLM proposal mandate/liquidity/floor-compliant — verified on
  ICICI) · `apply_decision` (diff vs book→trades, blotter+prereg, **raw-ARM scrub guard**, fact_sheet mark) ·
  `compare_to_quant` · round driver `prepare_round`/`apply_round` (4 pilots, same set as replay_pilots).
- REMAINING: author `_amc_rebalance.js` Workflow (per-scheme FM agents read desk file → TradeTickets; CIO
  review) → run first round at the seam (2026-06-25) → apply → rules-vs-LLM comparison → surface in amc_site
  → Cron (daily mark + monthly rebalance). NOTE: apply_decision MUTATES amc_book/<>/book.json (git-tracked,
  restorable) — only run the real round with real LLM tickets.
### B) TIME-NAV for snapshot plots (#97, KV charting guideline [[vistas-charting-guideline]]) — ALL FRONT-END
- KV standing rule: every snapshot-in-time plot needs time-navigation (date slider/dropdown); trajectory/
  component plots need tick-untick. **DATA FINDING: all 4 requested panels already have history baked → pure
  vistas.js work, no heavy bake:** (1) Allocator "≥m% broke out" screen ← breadth.json sectors[] series (318
  month-ends); (2) Consensus "where street stands" ← VISTAS_CONSENSUS EW+components monthly (FF latest-only,
  flag); (3) per-stock ARM histogram ← components[].series + headline.series (~25mo); (4) ARM trajectory ←
  plot headline + 4 component series w/ legend tick-untick. Then ONE full rebuild → `_pup` probe → publish.

## 🔧 FOLLOW-UP CYCLE — 2026-06-27 ~06:45 (TC-aware brains + breadth bug) — SHIPPING in one push
- **#91 TC-AWARE FM DEPLOYMENT (the IR-leak fix).** New `amc_firm.deploy_with_floor()` + `LIQ_DAYS_MAX=60`:
  when the tight (LIQ_DAYS=20) liquidity cap would leave a book below its MANDATE EQUITY FLOOR (`equity_min`),
  a real FM widens breadth beyond n_hi (≤3×) + relaxes to a quarter-long accumulation, then re-fills to the
  0.95 equity_target. Only fires for capacity-stressed books; fully-invested books pass through UNCHANGED.
  Wired into `amc_replay.construct_targets` (drives scorecards) + `amc_firm.build_rules_v0` (live book).
  Re-ran 4 pilots (`replay_pilots.py`, saved): **ICICI IDENTICAL** (13.29/IR0.24/β1.09 — never triggered);
  **SBI improved** 14.03→14.46 / IR0.28→0.34 / β0.86 unch; **ABSL improved** 16.43→18.22 / IR0.37→0.50 /
  **β1.04→1.15** (the one caveat — extra deployment adds beta; IR still up so it's earned); **QUANT FIXED**
  7.12→**19.63** / IR**−0.47→+0.92** / β0.28→0.92 (same IC 0.045 — pure implementation, the TC story proven).
  HONEST caveat: some Quant excess is an equal-weight/size tilt vs the cap-weighted SMALLCAP-250, not pure ARM.
  KV gate was "publish if other 3 unchanged" → only ICICI identical, SBI/ABSL *improved not regressed* → KV
  said "continue, don't hold up" → SHIP. Licensing re-verified clean (0 raw ARM in amc_book/).
- **#92 BREADTH/ROTATION re-plot bug.** Charts went blank on dropdown/slider CHANGE: `el.innerHTML=""` before
  `Plotly.react` corrupts Plotly's state (DOM gone, state stale) → 2nd draw renders nothing. Fixed 4 spots in
  `vistas.js` (`_abDrawMarket`, `_abDrawSector`, `drawRotStock`, `drawRotCentroid`) → `Plotly.purge(id)` (the
  codebase's own correct pattern, lines 1814/3695). Enhanced `_pup_allocator.js` to change the dropdowns +
  assert re-plot. Ships in the terminal rebuild running now.
- **FINISH:** terminal rebuild (ba33qutpb) → probe `_pup_allocator.js` (breadth re-plot + rotation) → rebuild
  `amc_site` (TC scorecards + bench overlay) → stage `_pages/digital-amc/` → `publish_terminal.py --no-rebuild`
  ships BOTH + backup. Remaining open (unchanged): TC follow-through for the live-forward cadence (#52/#69).

## ✅✅✅ SHIPPED & LIVE — 2026-06-27 00:18 (terminal commit on `_pages` main; source backup `cbc2e42`)
**One push took BOTH sites live** (terminal `git add -A` on `_pages` picked up terminal/ + digital-amc/). Validation 0 errors; real-Chromium probe `_pup_allocator.js` = **PASS** (Allocator tab 4 traced plots + breadth + Consensus moved; rotation trail plot 37 markers; 0 console errors). Gated-marker scan 0/0; 0 raw-ARM leak.
- **R8 Asset-Allocator tab** [#90] — market breadth (%new-high/low 1/3/5y, NH−NL, %>200/50DMA, golden-cross), per-sector breadth, per-sector ≥m% breakout/golden-cross screen w/ `m` slider, snapshot drill-downs; **Analyst Consensus Flow MOVED here from Macro**.
- **R2 Quadrant rotation** [#44/#86/#87/#88/#89] — stock trail in Screen (ARM×flow monthly, fading markers, play/slider) + portfolio centroids (803 funds + 48 AMCs + 21 categories) w/ peer overlay + own-history percentile.
- **R3 multi-force FM brains** [#85/#41] — 4 distinct brains (core_multifactor / regime_switch / value_revision / momentum_led) wired into pilots + replayed. HONEST finding below.
- **R1 Mesh** [#41] — verdict shipped (`MESH_RESEARCH_FINDINGS.md`); brains are the genuine multi-force use.
- **R4 digital-AMC surfacing** [#69 partial] — Schemes&Books tab: NAV sparkline **w/ benchmark overlay line**, scorecard (IC·√BR·TC·IR + plain-English), fact sheet (top-12 + play-type + sector mix), trade register. 4 pilot books re-run w/ new brains.
- **#51 flow-decomp 3-way** (Gross/Price-adj/Net-active) · **#49 rebase toggle** · **#47 cycle-percentile** · **#81 tilt-taxonomy fix** — all live.
**HONEST IR finding (Fundamental-Law lens):** new brains raised per-bet IC on 3/4 desks, but realized IR is gated by TC (long-only/liquidity leak). ICICI +2.3%/IR0.24/β1.09 · SBI +2.0%/IR0.28/β0.86 · **ABSL +4.4%/IR0.37/β1.04** (was a β1.1 beta-tilt → now honest alpha) · **Quant SmallCap −7.0%/IR−0.47/β0.28** = strong IC (0.045 t5.0) thrown away by an under-deployed low-beta book (TC problem, not skill). Fix = TC-aware implementation (deferred).
**RESUME POINT for next session:** core build campaign DONE & live. Open follow-ups (none blocking): (1) **#69/#52 live-forward LLM cadence** for the digital-AMC (the agents actually running forward daily) — the North Star piece; (2) digital-AMC **TC-aware brain** (turnover/constraint control to stop the IR leak, esp. Quant); (3) **W5 deep-analyst dossier panel** surfacing (engine `equity_research.py` built; bake `data/research/<SYM>.json` + a per-stock JS panel); (4) #39 actionable aggregation (verify-or-fold vs #45); (5) #81 hook-3 (Funds-tab granular industry path, needs a funds regen).

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
