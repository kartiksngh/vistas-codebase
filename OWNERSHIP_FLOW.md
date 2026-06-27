# OWNERSHIP_FLOW.md — the money-flow waterfall (KV vision, 2026-06-27)

**The idea (KV):** track the *long chain of money* — an **AMC** raises capital through its **schemes** →
schemes **deploy** it into **sectors/themes** → into **stocks** — and watch how that liquidity propagates
**over time**, separating what is *price*, what is *implied inflow deployment*, and what is *genuine active
conviction*. Use it to see **where smart money is allocating** as an input to fund-management and
asset-allocation decisions. A dedicated **Ownership & Flow** tab, four levels: **stock · sector/theme ·
fund · AMC**, with pivot-style drill-down and time navigation.

This is North-Star-aligned: it is a primary tool on the **analyst / FM / CIO** desks (where is real money
tilting, and is it conviction or just inflow?). See `AGENTIC_AMC.md`, `FUNDAMENTAL_LAW.md`.

## The one engine it all stands on (no new licensed data)

Every figure derives from `vistas/funds_flows.py` (holdings_history.parquet + tr_returns_monthly.parquet),
per (fund, stock, month):

- **gross** = MV_end − MV_start                      → raw change in ownership value (price + inflow + active)
- **price_adj** = MV_end − MV_start·(1+r)            → price stripped (= implied-inflow + net-active)
- **net_active** = AUM·(1+R_p)·Δw_active            → price AND scheme-inflow stripped (weight-space CONVICTION)

From which the **three additive components** (they reconcile exactly, verified on the consensus bake):

- **Price action**   = gross − price_adj   = MV_start·r        (the holdings simply moved with the market)
- **Implied inflow** = price_adj − net_active                  (fresh money deployed pro-rata; no view change)
- **Net active**     = net_active                              (genuine reweighting — the smart-money signal)

**This is the whole waterfall.** "AMC infuses money via schemes" = the **implied-inflow** leg; "deploys into
sectors/stocks" = where that leg + the net-active leg land; "clubbed with market via price returns" = the
**price-action** leg. The same decomposition, summed at any grouping, is the entire model.

## The aggregation lattice (one cube, four roll-ups)

Base cube: `flow[fund, stock, month] = {gross, price_adj, net_active}` (already produced per stock; extend
the per-fund table the same way). Then sum over axes — every level is a GROUP-BY of the SAME cube, so every
level reconciles with every other:

- **Stock**  = Σ over funds            (`build_stock_series` — exists; carries the 3 figures)
- **Fund**   = Σ over stocks           (`build_fund_series` — exists; extend to 3 figures)
- **AMC**    = Σ over its funds         (group funds by AMC — new thin rollup)
- **Sector/theme** = Σ over stocks in the sector  (the consensus bake — DONE for sectors; add themes)

Cross-tabs the UI needs: **AMC × sector** (how an AMC's money split across sectors), **AMC × stock**
(its biggest active bets), **sector × AMC** (who is crowding into a sector). All are group-bys of the cube.

## UX (a new tab; obeys the charting guideline)

- **Level switch:** Stock · Sector/Theme · Fund · AMC.
- **Figure switch:** Gross · Price · Implied-inflow · **Net-active** (default; the conviction read), shown as
  the 3-component stacked decomposition (same pattern just shipped on the consensus flow chart).
- **Pivot drill-down (Excel-style):** a table at the chosen level; click an **AMC** row → expand its
  **schemes**; click a scheme → its **sector/stock** allocation. Each row shows ownership ₹cr + the 3-way
  flow split + its derivation. (Snapshot at the selected month.)
- **Time navigation (REQUIRED per the charting guideline):** the snapshot table has a **date slider +
  date-input**; and a **single time-series plot** shows how the chosen cell (e.g. "ICICI → Financials
  net-active") evolved — with tick/untick of the 3 components. Reuse `dateNavControl`/`seriesValAt`.

## Honesty / correctness rails (no score for error)

- **Implied-inflow ≠ raw subscriptions.** It is inflow *deployment inferred from holdings*, not AMFI
  subscription/redemption data. Label it "implied" everywhere; if true scheme-flow data is wired later
  (#100 MFI NAV gives AUM; subscriptions need a separate feed), show both and reconcile.
- **Coverage:** only funds present in both months count; CA-immunity via TR + merger bridge (inherited).
- **Licensing:** pure holdings/price derivation — no ARM, no licensed data. Safe to bake + publish.
- **Survivorship:** the holdings panel retains dead funds (already survivorship-aware); keep that.
- **Monthly cadence** (disclosures are monthly) → a #99 MONTHLY-bucket bake.

## Phased build (each phase ships + is probed)

- **P0 (DONE ✓, LIVE 2026-06-27, commit `6323213`):** fix the consensus flow mislabel → net-active
  headline + 3-component decomposition.
- **P1 (DONE ✓, 2026-06-27):** AMC × sector waterfall cube engine = `vistas/flow_waterfall.py`. Stands on
  `funds_flows._pair_flows_active` (already carries gross/price_adj/net_active + amc); attaches AMC + macro
  sector (`canonical_vst_sector_map`, 23 sectors) and group-by-sums to (AMC×sector) cells + AMC/sector/market
  roll-ups, 36-mo history. `build_waterfall()` → {months, amcs, sectors, cube, sector_total, market_total,
  meta}. **Reconciliation self-audit PASSES** (price+inflow+net_active==gross every month; 4.2 cr/48,774 cr
  residual = 0.0086% display-rounding, not a bug). `python -m vistas.flow_waterfall` = the audit.
- **P2 (IN PROGRESS, core 2026-06-27):** the **Ownership & Flow tab** — baked `window.VISTAS_WATERFALL`
  (deck.py 2f-waterfall), dynamically-injected tab after Asset Allocator. Core shipped: AMC + sector
  selectors → a (AMC×sector / AMC-total / sector-total / market-total) **3-component stacked decomposition
  plot** over time (price #9aa6b2 · implied-inflow #d99a2b · net-active #1f9e89, barmode relative, legend
  tick/untick) + 1Y/2Y/MAX horizon + a **snapshot table** (per-sector net-active/inflow/price/gross) with a
  **date slider** (time-nav). Defaults chosen per the open-Qs: macro-sector taxonomy · AMC-first ·
  "implied inflow" labeled as the deployment proxy. Engine `renderOwnership` in `static/vistas.js` (reads
  the baked cube — no JS↔Python parity port). Probe: `_pup_allocator.js` OWNERSHIP block.
- **P3 (DONE ✓, LIVE 2026-06-27, src backup `b3baf62`):** the Excel-style **pivot drill-down** — root = the
  AMC dropdown (All AMCs → AMC → scheme → sector; or one AMC → scheme → sector). Each row shows **Ownership**
  (priced MV held) + the 3-way flow split (net-active / implied-inflow / price-action) + gross, sorted by
  net-active. Click a row to expand (Excel-style) AND refocus the time-series chart above onto that exact cell
  (market / sector / AMC / AMC×sector from the inline cube; scheme / scheme×sector from the lazy file). Date
  slider time-navigates the whole pivot. **Engine:** `flow_waterfall.build_waterfall(with_drilldown=True)` now
  also emits, per AMC, every scheme × sector (+ MV) over all months — reconciles (scheme total = Σ its sectors;
  price+inflow+net-active = gross), prunes debt/no-equity schemes (peak |gross| < ₹5cr). **Deck** writes one
  lazy file per AMC → `data/ownership/<slug>.json` (47 files, 12 MB total, fetched on first expand) + keeps
  only a tiny `{amc: slug}` index inline (shell stays light). FE: `renderOwnership`/`_wfPivotRender` in
  `static/vistas.js` (+ `.wf-pivot` CSS). Probe `_pup_allocator.js` WF-PIVOT block PASS (47 AMC rows → expand →
  11 schemes lazy-load → 22 sectors → click sector refocuses chart; 0 errors). Aggregates only (bake-safe).
- **P4 (DONE ✓, LIVE 2026-06-27, src backup `8673b1b`):** **stock leaf + NSE thematic-index theme lens.**
  - **Stock leaf** — the pivot drills a 5th level AMC→scheme→sector→**stock**; under each scheme it keeps the
    **top-15 holdings by ownership ∪ any holding peak MV > ₹100cr** (KV's spec), nested by sector. Each stock row
    = Ownership + 3-way split + gross, reconciles, and clicking it charts the stock's flow history. Labels from
    the holdings table (`vid_name`/`nse_symbol`, no extra source). Engine: `(navindia_code, vst_id)` cells in the
    same month loop; per-AMC lazy files grew 12→32 MB total (biggest ~2.8 MB, fetched on expand, gzip-served).
  - **Theme lens** — a parallel **"Flow by NSE theme"** panel (theme selector → decomposition chart + a snapshot
    table of all themes sorted by net-active). `theme_total` baked INLINE (~5 KB, all months). Built from a
    committed `{vst_id: [themes]}` map (`data/themes/theme_constituents.json`, fetched once from niftyindices.com
    via `flow_waterfall.build_theme_map()` → 9 cross-sector themes: Consumption, Energy, Commodities,
    Infrastructure, CPSE, PSU, Healthcare, MNC, Services). **OVERLAPPING → labeled NOT additive** to the market.
    **Macro-sector backbone unchanged.** ⚠️ Manufacturing/Digital/EV/Defence are NOT on NSE's public constituent
    endpoint (CSVs return empty) → absent; add later if a constituent source appears (engine is data-driven).
  - Probe `_pup_allocator.js` WF-PIVOT (drills to a stock, refocuses chart) + WF-THEME blocks PASS, 0 errors.
- **P4b (REMAINING) — cross-AMC crowding** ("who is buying this stock/sector", conviction vs inflow): a
  stock/sector → which AMCs are tilting view. Derivable from the same cube; deferred to its own ship.
- **P5 — agent hook:** expose "net-active tilt by sector/AMC/theme, last N months" to the analyst/FM/CIO desks.

## Open questions for KV (don't block P1)
1. **Theme** vs sector — use the existing macro-sector taxonomy first; add NSE thematic indices later?
2. Pivot default level — AMC-first (your example) vs sector-first?
3. Wire **real** scheme subscription data eventually, or keep "implied inflow" as the deployment proxy?
