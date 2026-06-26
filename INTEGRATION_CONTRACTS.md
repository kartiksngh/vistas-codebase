# INTEGRATION CONTRACTS — Wave-1 agent outputs → vistas.js / bake wiring (2026-06-26)

> Durable record of each engine agent's data contract, so the single shared-file
> integration pass (vistas.js + bake) is fully informed and survives compaction.
> `[x]` = agent done, contract captured · `[ ]` = still running.

## [x] A1 — Flow decomposition (#51)  ·  file: vistas/funds_flows.py  (per-stock quant JSON, no bake change needed)
Per-stock `data/quant/<SYM>.json` → `smart_money_flow` now ALSO carries (all aligned to existing `months[]`):
- `gross[]`   = raw rupee change `MV_end−MV_start` (price+inflow+conviction)
- `price_adj[]` = strips price only `MV_end−MV_start·(1+r)`  (== existing `flow`; still inflow-contaminated)
- `net_active[]` = CONVICTION, weight-space, inflow-immune `AUM·(1+R_p)·Δw_active`
- `na_intensity[]`, `na_rank[]`, `na_nclean[]` = size-neutral rank context for net-active
- `decomp:{ym,gross_cr,price_adj_cr,net_active_cr,na_intensity,na_rank,na_nclean}` = current snapshot (UI default)
**JS task:** in the Quant "Smart-money flow" panel add a 3-way toggle Gross / Price-adj / Net-active →
switch which history array drives the chart + which `decomp` scalar shows. Default = Net-active (conviction).
Existing `flow`/`rank` unchanged (back-comat). No bake-wiring edit (carried verbatim via stock_intel.py:888).
Validated: net_active sums to 0 across a scheme book in pure-inflow months (Parag Parikh, Nippon LC, etc.). ✅

## [x] A2 — Tilt taxonomy unify (#81)  ·  file: vistas/funds_portfolio_viz.py  (canonical taxonomy authority: canonical_vst_sector_map(), canonical_label_map(), canonical_sector())
Bug: fund side spoke granular SEBI labels ("Banks") + "Unclassified" gaps; benchmark side spoke NSE macro ("Financial Services") → phantom tilts.
**ACTIVATION DONE (me):** hook 1 = deck.py canonicalizes the fund equity book sectors → macro (the benchmark-relative tilt fix, activates next build). hook 2a = benchmarks.py canonicalizes constituent sectors (future-proof). Re-tag of existing benchmark JSONs = NO-OP (0/21 changed → benchmark side already macro, CONFIRMED). hook 3 (Funds-tab granular industry path) DEFERRED — not needed for the benchmark-relative tilt; would need a full funds regen. ✅ core done.

## [x] A3 — Quadrant rotation (#44)  ·  files: vistas/screens.py + vistas/rotation.py  ·  KV TOP PRIORITY
STOCK TRAIL: each smart_vs_street.json row now has `traj:[{date,arm,flow,quad}]` (≤36mo) + `arm_history_mode` ("ffill"/"flat"/"none"). ARM history real (1998-2026 from arm.py).
CENTROIDS: rotation.build_centroids(site_data_dir, root) → site `data/_rotation/centroids.json` (6.35MB, 872 entities=803 funds+48 AMCs+21 categories; points[], own_pctile{arm,flow}, peer_group, latest). **LAZY-FETCH (6MB), not baked.**
**BAKE WIRING DONE (me):** deck.py now calls rotation.build_centroids into site data dir each build (graceful).
**JS: rotation spec SENT to agent aa0430a0dfefaa25f** (stock trail in Screen + centroid rotation view + peer overlay + own_pctile readout). Quadrants Q1 rec+buy(TR) … Q4 notrec+sell(BL).

## [x] A5 — Multi-force FM brains (#85)  ·  files: vistas/amc_firm.py + vistas/amc_replay.py
4 distinct brains: core_multifactor / momentum_led / value_revision / regime_switch. `score_universe(uni,asof,brain_id)`, `brain_for_mandate()`. Pilots: ICICI LC→core, SBI Hybrid→regime_switch, ABSL Flexi→core, Quant SC→momentum_led. Validated genuinely different (20-33% cross-family overlap). Licensing guard holds (no raw ARM in books).
**DIGITAL-AMC re-run: agent a4609b3897de06489** (re-run replay w/ brains + add benchmark NAV series to amc_replay + rebuild amc_site; I publish /digital-amc/ after).

## [x] W5 — Deep equity-analyst engine (#64/#65)  ·  NEW file vistas/equity_research.py (+ _equity_research_validate.py)
`research(symbol, ctx=None) -> dossier` (7 sections: valuation/quality/momentum/revisions/smart_money/peers/synthesis + Fundamental-Law read). Persists nothing; no forecasts. Validated RELIANCE/INFY/HDFCBANK (distinct theses). 
**SURFACING DEFERRED (clean follow-up):** bake `data/research/<SYM>.json` at deck build (via a build_all writer, reuse loaded bundles) + a per-stock "Analyst Dossier" JS panel. Display-plane, no parity port. NOT in this batch (avoids build-time + risk).
## [x] A4 — Market breadth (W7)  ·  file: vistas/breadth.py → data/_breadth/breadth.json (400KB, 318 month-ends 2000→2026 + daily snapshot)
**Bake task:** inline `data/_breadth/breadth.json` → `window.VISTAS_BREADTH` during the terminal build (same pattern as VISTAS_CONSENSUS/VISTAS_MACRO). No JS-parity port (display plane).
Schema: `{meta, dates[], market:{pct_new_high:{"1","3","5"}, pct_new_low:{...}, nh_minus_nl:{...}, pct_above_200dma[], pct_above_50dma[], pct_golden_cross[], eligible_n[]}, sectors:{<sec>:{...same 7 keys...}} (19 sectors), screen_current:{<sec>:{n,thin,pct_breakout,pct_breakout_3y,pct_breakout_5y,pct_golden_cross,pct_above_200dma}}, snapshot:{asof, market{...}, sectors:[{sector,n,thin,pct_new_high_{1,3,5}y,pct_above_200dma,pct_golden_cross,names_new_high_1y[],names_golden_cross[]}]}}`
**BAKE WIRING: DONE (me)** — `vistas/deck.py`: builds breadth FRESH each deck build (graceful fallback to cached `breadth.OUT_FILE`), adds `site_embed["breadth"]`, bakes `window.VISTAS_BREADTH`. So the JS just reads `window.VISTAS_BREADTH`.
**JS FRONT-END: assigned to agent aa0430a0dfefaa25f** (sole vistas.js owner; building breadth tab + Consensus move + flow-decomp toggle + rebase toggle; rotation to be sent via SendMessage when A3 lands).
**JS task (new Asset-Allocator tab):** (1) market breadth time-series chart — 1/3/5y toggle for new-high/low/NH−NL + lines for %>200DMA/%>50DMA/golden-cross; (2) per-sector breadth (19 sectors, heatmap or selectable); (3) **per-sector "≥ m% broke out / golden-crossed" screen** driven by `screen_current` + an `m` input slider (e.g. 50/60%); (4) snapshot panel w/ per-sector name drill-downs. Display per-rule denominator ("X% of N"). Caveat (coincident, not forward) is in `meta.caveat`.
Validated textbook: GFC 0.99% / COVID 7.4% / 2017 top 84% / today 54.8% >200DMA; brute-force cross-check matched. Price-derived, no ARM — public-safe. ✅
## [ ] A5 — Multi-force FM brains (#85)  ·  files: vistas/amc_firm.py + vistas/amc_replay.py
## [x] A6 — Digital-AMC surfacing (#69)  ·  file: vistas/amc_site.py  (separate publish path: output/_amc/site → /digital-amc/)
amc_site.build() now renders a 2-tab site: "Trading Floor" (existing) + "Schemes & Books" — schemes_overview() (NAV SVG sparkline, since-incep, book CAGR vs bench, excess), factsheet_block() (top-12 holdings + play-type + sector mix), trade_register() (blotter.jsonl audit table), scorecard_block() (IC·√BR·TC·IR + plain-English + defs). Reads amc_book/<amc>/<scheme>/{book.json, replay/scorecard.json, replay/monthly_summary.json, replay/nav.csv, daily/*.json, blotter.jsonl} data-driven (4 pilot books). Built+grep-verified output/_amc/site/index.html (565KB). Licensing clean (no raw ARM; SAFE_DROP guard added).
**Remaining for W3 publish (after A5 lands):** (1) re-run amc_replay with the NEW multi-force brains → regenerates the 4 books; (2) **also dump a benchmark NAV *series*** in the replay (A6 flagged: only bench scalars persisted now → no bench line on the NAV sparkline; add a bench NAV path so the chart can overlay it); (3) amc_site.build() again; (4) publish /digital-amc/.
**No vistas.js impact** (separate site). ✅ UI built.
