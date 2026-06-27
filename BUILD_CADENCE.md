# BUILD_CADENCE.md — cadence-partitioned terminal build (tasks #98 + #99)

**Goal (KV 2026-06-27):** stop rebuilding *everything* on every build. Rebuild each artifact at the
**frequency of its underlying data**: club the **daily** artifacts into one fast job; run the slower
artifacts (weekly / monthly / quarterly) as separate jobs on their own cadence. A weekly **forced FULL
rebuild** is the safety backstop.

**HARD CONSTRAINT (KV):** *the tabs and the published site do NOT change — byte-for-byte identical
behaviour.* This is a pure **build-infrastructure** change. The browser still loads one coherent dataset;
it is simply **assembled** from artifacts that were each refreshed at a different cadence. No UI, no
`vistas.js`, no `vistas_analytics.js`, no panel touched. No JS↔Python parity surface touched.

---

## The one architectural idea: separate COMPUTE from ASSEMBLE

The build today does both in one monolithic pass (`deck.save_terminal_site`): it *computes* every payload
AND *assembles* the shell, every time. We split that in two:

1. **COMPUTE stages** (gated by cadence) — each stage produces its artifacts: the per-file JSONs under
   `output/terminal_site/data/...` AND, for anything embedded inline in the shell, a **cache payload** under
   `data/_cache/<stage>.json`. A stage **runs only if its input fingerprint changed** since the last build
   (or a forced-full is requested); otherwise its prior artifacts/caches are **left in place**.

2. **ASSEMBLE stage** (runs EVERY build, cheap) — stitches `index.html` from whatever artifacts + caches
   are currently on disk, then **validates** (the Node runtime smoke-test) and (on publish) pushes.

**Why this is safe and tab-identical:** ASSEMBLE always reads the *latest-good* cache for every inline
dataset (`VISTAS_CONSENSUS`, `VISTAS_BREADTH`, `VISTAS_MACRO`, the manifests, survivorship, market-flows).
Skipping a slow COMPUTE stage just means its cache wasn't refreshed this run — the shell still embeds the
last-good payload. The published site is therefore **always** "all artifacts at their last-refreshed
state," which is exactly the intent. It can never be half-broken, because the shell is never *partially*
rebuilt — only re-stitched from complete caches. Validation runs every build regardless.

---

## Cadence buckets (the stage → bucket map)

Source of truth = `deck.save_terminal_site`. Cadence is the data-publish frequency of each stage's input.

| # | Stage | Engine / source | **Bucket** | Fingerprint inputs |
|---|-------|-----------------|-----------|--------------------|
| 1 | Per-stock **price** files | `stocks.load()` (TR panel) | **DAILY** | the dated stocks/TR CSV |
| 5 | Per-**index** measure files | `data.load(m)` | **DAILY** | the dated `Indices Data TR …csv` |
| 6 | Per-**world** files | `world.load_named()` | **DAILY** | `World Data PX …csv` |
| 7 | Per-**fund NAV** files | `funds_nav.load_named()` | **DAILY** | the NAV store (← #100 MFI daily pull) |
| 3a | Quant **market** block | `stock_intel._market_behaviour` (price panel) | **DAILY** | TR panel + turnover |
| — | **breadth latest** snapshot | `breadth.py` (daily prices) | **DAILY** | TR panel |
| — | daily **macro** (G-sec/T-bill/FX/FII-DII) | `macro.py` | **DAILY** | those macro CSVs |
| 8' | **ARM** + Consensus rollup | `arm.py` / `arm_sectors.py` | **WEEKLY** | the ARM parquet drop |
| 4 | Smart-money **flows / holders** | `funds_flows.build_stock_series` | **MONTHLY** | monthly portfolio disclosures |
| 8 | Per-scheme **fund holdings** | `funds_portfolio.build_all` | **MONTHLY** | AMC monthly portfolios |
| 9 | Per-scheme **attribution** + crowd/active-share/books | `funds_flows.build_fund_series` … | **MONTHLY** | holdings + NAV history |
| 10 | **Survivorship** report | `data/funds/_survivorship_*.json` | **MONTHLY** | AMFI census |
| — | monthly **macro** (CPI/IIP/WPI) | `macro.py` | **MONTHLY** | those macro CSVs |
| 2 | Per-company **fundamentals** files | `_fundamentals_dataset()` (Screener) | **QUARTERLY** | the Screener bundle store |
| 3b | Quant **slow** block (business/valuation/ownership) | `stock_intel` (reuses `fa` + flows) | **QUARTERLY/MONTHLY** | fundamentals bundle + flows cache |
| A | **ASSEMBLE** shell + validate | `deck` + `_deck_runtime_test.js` | **EVERY build** | all caches/artifacts |

> Buckets are defaults from data-publish frequency — KV can retune any row (e.g. fundamentals weekly if he
> pulls Screener more often). The forced-full makes a too-slow bucket safe (worst case = one extra stale
> day until the weekly full).

### The split that unlocks the quant file (#3)
The per-stock `data/quant/<SYM>.json` **fuses** a daily block (`market`) with slow blocks
(business/valuation/ownership/flows). Verified by reading `stock_intel.compute`: only `_market_behaviour`
reads the daily price panel; `_valuation_context` reuses the **pre-baked** P/E etc. from the fundamentals
bundle (NOT recomputed daily). So we **split the file** into:
- `data/quant/<SYM>.json` — the **slow** block (business/valuation/ownership/flows), rebuilt monthly/quarterly.
- `data/quant_market/<SYM>.json` — the **daily** `market` block, rebuilt daily.

The Quant cockpit JS already fetches one file per symbol; it will fetch the two and **merge them in the
loader** (a 3-line change in the lazy-fetch, invisible to every panel — the merged object is identical to
today's). *This is the only front-end-adjacent change, and it changes no panel logic or output.*

---

## Fingerprint gating (the correctness guard)

- Each COMPUTE stage declares its **input files**. Its fingerprint = `sha1(stage_version ‖ for each input:
  content-hash or (mtime,size))`. Recorded in `data/_refresh/build_manifest.json`:
  `{stage: {fp, built_at, n_artifacts}}`.
- On a build, a stage runs **iff** `fp ≠ manifest[fp]` **or** `--full` **or** any declared input is
  missing/unreadable (**fail-safe = rebuild; never skip on doubt**).
- `--full` (and the weekly schedule) ignores the manifest → rebuilds everything → also re-runs JS↔Python
  parity. This is the backstop that makes any dependency-graph bug self-healing within a week.
- Skipping a stage = **leave its existing artifacts + cache in place**. ASSEMBLE re-embeds from cache.

## Fetch staleness-gate (refresh only if stale — KV 2026-06-27)

Upstream of COMPUTE is the **data REFRESH (fetch)**. Prices/NAV/most feeds publish **once per day**, so:

- **Within-day publishes do NOT re-fetch.** When KV ships a *feature* during the day, the data is already
  today's — skip the fetch entirely (`--no-fetch`), go straight to BUILD → VALIDATE → PUBLISH.
- **Refresh a source only if it is NOT updated till date** — i.e. its latest stored date `<` the latest
  *available* date (today / last trading day). Each `Source(...)` in the registry exposes a cheap
  `latest_local_date`; the daily refresh fetches a source **iff** it's stale, else skips it. This is the
  fetch-layer twin of the compute-layer fingerprint gate.
- **This refines the old "always full rebuild" rule, it does not break it.** The thing that rule banned —
  shipping a *bake-only* artifact without re-assembling + validating — never happens: every publish is a
  real ASSEMBLE + Node validation. What we stop doing is **re-FETCHING data that's already current** and
  **re-COMPUTING stages whose inputs didn't change**. Publishing stays one double-click.

**Immediate application (date-nav, #97):** the in-flight build is `--no-fetch` (data already current →
correct). Its publish is `publish_terminal.py --no-rebuild` = validate + push the freshly-built shell +
off-machine backup. **No fetch, no redundant rebuild** — exactly "no need to refresh everything within the
day."

## Scheduling (Task Scheduler / `pipeline.py`)
- **`Daily Refresh`** → daily bucket only (price/index/world/NAV/quant-market/breadth-latest/daily-macro) +
  ASSEMBLE + validate + publish. Fast (the parallelized quant-market bake, #98).
- **`Weekly Refresh`** → `--full` (everything, parity re-check). The backstop.
- Monthly/quarterly inputs are picked up automatically the next daily run after their source file changes
  (the fingerprint flips), so they need no separate schedule — but a monthly job can force them if desired.

---

## #98 — parallelize the daily quant-market bake (do FIRST, no rule change)
The daily bottleneck is the per-stock loop. Fan it across cores with `ProcessPoolExecutor`
(Windows = spawn → a worker **initializer** builds the shared read-only context once per worker; tasks
return nothing, each worker writes its own file). **Identical output, no caching/skipping, zero staleness
risk** — just uses the ~8 cores already present. **Step 0 = instrument one build** (`_build_profile.py`) to
confirm whether compute or serialize+disk dominates, so we parallelize the real bottleneck. Verify by
diffing a sample of post-parallel quant files against the single-thread output (must be byte-identical).

## Order of work
0. (after the in-flight build frees) publish the date-nav build (#97), then run `_build_profile.py`.
1. #98 parallelize → test build → byte-diff verify → keep.
2. #99 split quant file + COMPUTE/ASSEMBLE separation + fingerprint manifest + `--full` + schedules.
3. #100 daily funds-NAV via MFI slots into the DAILY bucket (deferred until pipeline workflows finish).

**Never:** skip on doubt (always rebuild), change a tab, or drop the weekly forced-full. Publishing a
*daily* build is still a real ASSEMBLE + validate — not a bake-only shortcut.
