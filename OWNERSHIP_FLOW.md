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

- **P0 (DONE):** fix the consensus flow mislabel → net-active headline + 3-component decomposition. ✓
- **P1 — AMC × sector waterfall:** extend `build_fund_series` to the 3 figures; add an AMC roll-up; bake an
  `ownership_flow` cube (AMC→sector, AMC→top-stocks) with monthly history. New `vistas/flow_waterfall.py`.
- **P2 — the tab:** Ownership & Flow tab; level + figure switches; the 3-component decomposition plot;
  time-nav. Reads the baked cube (display-plane, no parity port).
- **P3 — pivot drill-down:** AMC → schemes → sector/stock expandable table at the selected month.
- **P4 — stock & theme level + cross-AMC crowding** (who is buying this stock/sector, conviction vs inflow).
- **P5 — agent hook:** expose "net-active tilt by sector/AMC, last N months" to the analyst/FM/CIO desks.

## Open questions for KV (don't block P1)
1. **Theme** vs sector — use the existing macro-sector taxonomy first; add NSE thematic indices later?
2. Pivot default level — AMC-first (your example) vs sector-first?
3. Wire **real** scheme subscription data eventually, or keep "implied inflow" as the deployment proxy?
