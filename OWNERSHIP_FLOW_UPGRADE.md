# Ownership & Flow tab — upgrade spec  (STATUS: spec, queued — 2026-06-30)

> KV-requested upgrade to the **Ownership & Flow** tab (the `flow_waterfall` panels). Display-plane only
> (`static/vistas.js` render + a small `vistas/flow_waterfall.py` addition for AUM denominators). The flow
> panels render from EMBEDDED data (no in-browser recompute), so **no analytics-parity port is needed**
> (unlike the skill metrics). Build after the benchmark fix unless KV reprioritizes.

## The data spine (what already exists — confirmed in `flow_waterfall.py`)
`build_waterfall` emits a monthly cube aligned to `months`, with the THREE additive, reconciling components
(`price_action + implied_inflow + net_active == gross`) at three grains:
- **AMC × sector** (`cells` → the inline `crowd_index`),
- **scheme × sector** (`scheme_cells`, keyed `navindia_code`) — the per-fund drill-down,
- **stock** (`stock_cells`, lazy per-stock files) — each carries the per-AMC (and per-scheme) breakdown.
`mv` = market value held (= Ownership). The crowding JS already resolves a selection → `{label, amcs:{amc:node}, agg:node}` and draws `agg`; `amcs[amc]` (each AMC's monthly series) is already loaded.

**The one NEW data piece:** per-AMC and per-scheme **equity-AUM denominators per month** (Σ holdings `mv`) so flow can be shown as **% of AUM**. Add to `build_waterfall` (cheap; it already sums `mv`).

## The four asks

### A — Cross-AMC crowding: a LOCAL date picker
Today the crowding TABLE (`wf-crowd-tbl`) shows a single "as of <latest>" snapshot; its date is tied to the waterfall's far-away AS-OF slider. Add a **second date dropdown inside the crowding panel** (month-end list, default = latest, with a "latest" button) that drives the table's as-of snapshot. Keep it optionally synced to the waterfall (a "← sync to waterfall date" affordance) but independently selectable. The chart already shows full history; only the table snapshot follows the picker.

### B — Cross-AMC crowding: an AMC filter
Add an **AMC dropdown** (default "All AMCs (aggregate)", plus each AMC). When an AMC is chosen, the CHART (`plot-wf-crowd`) draws **that AMC's** flow into the selected stock/sector over time (gross / price-adj / net-active stacked), instead of the aggregate — answering "how did ICICI build its ACC position over the years." Data = the already-loaded `sel.amcs[amc]`. The table can highlight/scroll to that AMC.

### C — NEW panel: the cross-sectional FLOW MATRIX (the heatmap)
A separate new panel — *"Flow matrix: how money moved into &lt;target&gt; across AMCs over time."*
- **Target selector** = a STOCK or SECTOR or THEME (the *output* — where money flows TO).
- **Rows = AMCs**, sorted by total |flow| over the window (conviction-first); each row is **expandable** → its **schemes** (the exact funds driving that AMC's flow into the target).
- **Columns = month-end dates** (windowed 1Y/2Y/MAX).
- **Cells = the chosen measure**, **color-coded** diverging (green buy/inflow, red sell/outflow, intensity = magnitude). Hover → exact ₹cr + % of AUM + all three components for that (AMC|scheme, month).
- **Measure toggle:** gross / price-adj / **net-active** (default — the conviction signal).
- **Unit toggle:** **₹cr** vs **% of AUM** — at AMC level = % of AMC equity AUM; when a row is expanded, scheme cells = % of *that scheme's* AUM (so a small fund's big conviction bet shows even when its ₹cr is small — the relative-conviction view KV wants).
- **Totals:** a top "market" row (Σ all AMCs = the market's flow into the target) and a right "Σ window" column (each AMC's cumulative flow). 
- **Why a heatmap matrix:** scan a ROW = one AMC's accumulation/distribution trajectory into the target; scan a COLUMN = which AMCs moved together that month (a crowding event); color makes accumulation vs distribution patterns pop; expand+%AUM answers "which exact funds, how big for their size." This is the "output (stock/sector/theme) ← input (AMC/scheme), in what form (gross/priceadj/net)" question in one view.
- Keep the **existing stacked-bar time-series chart** (with the A/B additions) as the per-target companion to the matrix.

### D — Sort + filter on table columns
Across ALL the flow tables (crowding AMC table, the new matrix, the waterfall pivot): **clickable column headers** to sort asc/desc, and a **per-column filter** — text search on name columns; min/max threshold on numeric columns (e.g. show only AMCs with |net-active| ≥ X, or a date-range filter on the matrix columns). One small reusable client-side sort/filter helper.

## Build plan
1. `flow_waterfall.py`: add the per-AMC & per-scheme monthly **AUM denominators** to the cube (for the % toggle) + ensure the lazy per-stock/sector files carry the scheme breakdown for the matrix expand.
2. `static/vistas.js`: (A) crowding date dropdown; (B) crowding AMC filter (redraw `plot-wf-crowd` from `sel.amcs[amc]`); (C) the new flow-matrix panel (render + measure/unit toggles + row-expand + hover); (D) the reusable column sort/filter helper wired into all flow tables.
3. `vistas.css`: heatmap cell styling (diverging scale), expandable rows, sticky header/first-column for the matrix.
4. Validate: `_deck_runtime_test.js` PASS (panels render, 0 errors) + the real-Plotly probe; **never set a Plotly trace key (marker/line/mode/fill) to `undefined`**. No parity port (render-only).
5. KV publishes via the one-click flow once it's green.

## Notes / honesty
- Per-stock data is **lazy-loaded** (one file per stock) — the matrix for a stock fetches that file; for a sector/theme it uses the inline cube. Keep files lean (the existing peak-|gross| pruning).
- `net_active` reconciles only on the **priced subset**; show the same coverage caveat the panels already carry.
- `mv`-summed AUM is the **equity-sleeve** AUM (not total scheme AUM incl. debt/cash) — label the % accordingly.
