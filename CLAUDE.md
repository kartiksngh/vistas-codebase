# CLAUDE.md — Vistas (Passive NSE Index Terminal)

Guidance for Claude Code when working in this repo. This is a **standalone project**
— it has **no dependency on any other project**.

## What this is

**Vistas** is KV's own Bloomberg — a self-hosted analytics terminal for **NSE
total-return indices**, with GP-/COMP-style functions: pick any index/indices + any
benchmark(s), any date window t1→t2, any frequency, and read NAV / total return /
comparative stats / rolling alpha+beta / rolling risk / capture / calendar-year /
monthly heatmap / return distribution / correlation matrix. "Vistas" is KV's brand
name for the interface.

- **Terminal v2 = BUILT, verified, and LIVE** (Performance + Fundamentals + Macro tabs). Folder = this one.
- **Active sibling (mutual-fund NAVs via an MFI loader, category benchmarks) = planned**, same shape.
- **Live link:** <https://kartiksngh.github.io/vistas/terminal/> (the hosted Terminal v2 site — the main
  product). The legacy single-file **passive** deck is **RETAINED** at `/vistas/passive/` as a live-data
  source for the **FFT** project — **do NOT delete or disturb it**; keep the two products separate. The bare
  `/vistas/` URL redirects to `/terminal/`. Publish terminal via `publish_terminal.py` / `Refresh Vistas
  Terminal.bat`; passive via `publish_passive.py`. (Next build-in-progress: a per-stock **Quant & Market
  Intelligence** cockpit tab.)

## ★ NORTH STAR — the Agentic AMC (the destination; keep every build pointed here, KV 2026-06-26)

The terminal is not the end — it is the **research infrastructure of a firm we intend to run**. The vision
(blueprint: **`AGENTIC_AMC.md`**) is a **live, experimental, paper-trading AMC built ON the terminal**: a
team of **agents** — sector/theme **analysts** (dynamic headcount by coverage load), per-scheme **fund
managers**, and a **CIO** — that cover the market, **pitch / debate / escalate**, construct
mandate-constrained portfolios, **paper-trade**, are scored honestly, and **learn iteratively**, with
regular meetings and a closed decision→outcome→lesson loop. As the terminal grows, the agents benefit
instantly. **So when building ANYTHING here, ask: does this feed the analyst, FM, or CIO agent — as a data
source, a force, a score, or a decision input?** Every panel is ultimately a tool on some agent's desk.
- The three roles are designed in **`ANALYST_GOLDMINE.md`** / **`FM_INTELLIGENCE.md`** / **`CIO_INTELLIGENCE.md`**
  (read as the agents' charters + skill manuals); forces substrate = **`MESH_DESIGN.md`**.
- **Evaluation backbone = the Fundamental Law of Active Management** (`IR = IC·√BR·TC`, **`FUNDAMENTAL_LAW.md`**):
  analysts scored on **IC**, FMs on **IR decomposed into IC·√BR·TC** (skill vs the long-only transfer leak),
  the firm on **breadth** (de-correlated desks) — the same law also drives the dynamic analyst-headcount rule.
- **Non-negotiable discipline:** agents *synthesize* validated signals, they never *manufacture* alpha;
  learning is **pre-registered** (anti-hindsight); **paper-money only**; honest, defensible, no curve-fit.
- This is the **standing vision** — log new capabilities against it; it is the reason the persona/CIO/flow
  work exists. (Also in memory `vistas-agentic-amc` + the scoring/CIO memory.)

> **Provenance:** Vistas was split out of the FFT sector-rotation research project on
> **2026-06-18** to live as its own project (so closing the FFT project doesn't close
> Vistas). It **re-implements** the research engine's formula conventions locally
> (provenance noted in `vistas/analytics.py` and `vistas/fetch.py` headers) and imports
> **nothing** from that project. A reference copy still exists in the FFT tree at
> `…/FFT/December 2025/Claude/Vistas passive/` — this folder is the live one going forward.

## Environment

- **Python 3.10+**, Flask + a Plotly front-end (Plotly is **vendored** in
  `static/vendor/plotly.min.js` so charts work fully offline).
- **Node.js** — optional, only for the deck-validation runtime smoke-test and the
  JS↔Python parity check.
- Install: `pip install -r requirements.txt`. Run: `python app.py` → http://127.0.0.1:8753.
- See `README.md` (full) and `How to run.txt` (short) for run / refresh / deck / publish steps.

## Architecture

```
app.py                      Flask: UI + JSON API (/api/catalog,/analyze,/refresh,/add_index,
                            /export_excel,/fetch_all,/save_deck); auto-saves a deck on data updates + startup
vistas/
  data.py                   loads newest snapshot CSV, stale-day filter, windowed slices (HISTORY_FLOOR=2000), coverage gate
  analytics.py              ★ ALL quant metrics — the SOURCE OF TRUTH (conventions inline)
  fetch.py                  standalone NSE fetcher (port of the research data_update) + local IndexMapping cache; degrades gracefully
  catalog.py                picker universe (local history + fetchable list)
  export.py                 full multi-sheet NAV → Excel
  deck.py                   assembles the self-contained offline deck (inlines page + Plotly + full dataset + JS engine) → output/
  pipeline.py               ★ the DAILY refresh engine: a declarative source REGISTRY → refresh every
                            feed (graceful-degrade) → reload → rebuild → validate → publish if green.
                            Add a feed = append one Source(...) line. Run: python -m vistas.pipeline
static/
  index.html                the terminal page (9 panels)
  vistas.js                 controls, multi-selects, Plotly render, CSV export; works ONLINE (server) or OFFLINE (embedded data)
  vistas_analytics.js       ★ JS PORT of analytics.py (used by offline decks) — must stay parity-identical
  vistas.css, vendor/plotly.min.js
data/                       bundled snapshot CSV(s) + IndexMapping.json cache (committed on purpose)
output/                     generated offline decks (git-ignored, regenerable)
publish_passive.py          builds + validates + publishes the deck to the GitHub-Pages repo
publish_terminal.py         builds + validates + publishes the hosted Terminal v2 site (terminal/)
Refresh Vistas Passive.bat  double-click wrapper for publish_passive.py
pipeline/                   ★ the DAILY-REFRESH job (distinct folder): Daily Refresh Vistas.bat
                            (refresh→build→validate→publish if green) · Nightly Build (no publish).bat
                            (failsafe) · Publish Last Build.bat (one-click push) · README.md = the
                            full bottoms-up architecture doc (data→identity→build→publish + the engine)
_parity_dump.py · _parity_check.js · _deck_runtime_test.js   regression harness
requirements.txt · Procfile · render.yaml · .gitignore
```

## ★ THE PARITY DISCIPLINE (the #1 rule — read before touching analytics)

The offline deck recomputes everything **in the browser** using `static/vistas_analytics.js`,
which is a **hand port** of `vistas/analytics.py`. They must stay numerically identical.

**RULE: if you change ANY formula in `vistas/analytics.py`, mirror the exact change in
`static/vistas_analytics.js`, then run:**

```
python _parity_dump.py        # dumps Python results for 12 configs
node _parity_check.js          # must print 0 mismatches
node _deck_runtime_test.js     # must print PASS (all 9 panels render, 0 errors)
```

If you skip this, the offline deck silently disagrees with the live app.

**Hard-won lesson (do not forget):** JS↔Python parity proves the two implementations
**AGREE**, NOT that the shared convention is **correct** — a faithful port mirrors a
flawed convention just as faithfully. Audit the *conventions* separately, especially
**cross-series comparison with mixed inception dates** (rebase / alpha / total return
must be on the **common overlap** window, not each series' own start). Also: `node --check`
/ structural checks miss **runtime** throws — a real render smoke-test (`_deck_runtime_test.js`)
is mandatory. **And even that VM stub-Plotly test misses REAL-Plotly `cleanData` throws** — e.g. a trace
with `marker:undefined` makes Plotly do `'line' in trace.marker` and throw *"Cannot use 'in' operator to
search for 'line' in undefined"*, which the panel `try/catch` swallows into a blank "—" (burned 2026-06-22,
blanked the whole Fundamentals tab). The stub now rejects `marker/line:undefined`; for true render
verification use the real headless-browser probe `_pup_fund.js` (puppeteer). **Never set a Plotly trace key
(marker/line/mode/fill) to `undefined` — omit it.**

## Analytics conventions (so numbers are reproducible)

- **Returns:** simple period returns drive vol/Sharpe/β/capture; displayed NAV is the
  rebased level. **Weekly = W-FRI** (Friday close). `ppy` = 252 daily / 52 weekly.
- **CAGR** = `(level_end/level_start) ** (365/calendar_days) − 1`. Rolling windows ≥1Y are
  annualized; <1Y are cumulative.
- **Vol** = `std(r)·√ppy`; **Sharpe** = `(mean−rf)·ppy / vol`; **Sortino** = downside dev;
  **MaxDD** = `min(level/cummax − 1)`; **Calmar** = `CAGR/|MaxDD|`.
- **Excess α** = `CAGR(idx) − CAGR(bench)`. **Jensen α/β** = intercept/slope of
  `r_idx − rf = α + β(r_bench − rf)`. **Capture** = `Σ(r_idx|r_bench≷0)/Σ(r_bench|r_bench≷0)`.
- **Stale-day filter:** on load, days where ≥25% of available indices are unchanged vs the
  prior day are dropped (vendor-stale / non-trading), matching the research engine.
- **★ FAIR COMMON-OVERLAP WINDOW:** `analytics.analyze` (and the JS mirror) anchor the
  comparison at the **latest date on which EVERY selected series has data** within the
  requested window — so NAV rebasing, total return, CAGR and alpha are all like-for-like.
  `meta` carries `requested_start/common_start/truncated`; each COMP row carries `inception`.
- **★ COVERAGE GATE** (`data.py`): drops non-continuous series (MIN_OBS_PER_YEAR=150,
  MAX_INTERNAL_GAP_ROWS=25) so a sparse/snapshot series can't render fabricated analytics.

## Data

- Bundled wide daily TR CSV `data/Indices Data TR till <date>.csv`, **2000-01-01 onward**,
  ~130 NSE indices (`HISTORY_FLOOR=2000`). The newest dated CSV in `data/` is used automatically.
- The picker lists every index in `IndexMapping.json`; not-yet-local indices are fetch-on-demand.
- `fetch.py` is a self-contained port (own session, cookie handshake, **single-quoted** cinfo
  payload, ≤350-day/≤1-year chunking, IndexMapping cache, polite jittered pacing). Network is
  **optional** — if NSE is unreachable it returns `ok:False` and keeps serving the snapshot.
- Full clean rebuild from 2000: `python -c "from vistas import fetch; fetch.build_fresh()"`.
- **World / cross-asset** (`world.py`, Yahoo, no key): `World Data PX till <date>.csv`, ~84 instruments
  (global equity, commodities, FX, US yields, credit ETFs, volatility, crypto), friendly-named.
  Tool: `Pull World Markets.bat`.
- **India-native MACRO** (`macro.py`, free official sources): `India Macro till <date>.csv` — **CPI
  Combined/Rural/Urban + official YoY, CURRENT to latest month, from MOSPI eSankhyiki**
  (`api.mospi.gov.in/api/cpi/getCpiData`, base 2024, Back+Current, paged; `verify=False` — govt
  self-signed cert); **IIP (4 sub-indices) + WPI All-commodities headline + WPI YoY also CURRENT
  from eSankhyiki** (`/api/iip/getIipData` type=General/Sectoral + `/api/wpi/getWpiRecords`
  major_group, `limit=5000` one-shot); only the WPI **Primary/Fuel/Mfg breakdown** stays on
  **data.gov.in** (`239ac3d0…`, *pivoted*/melted, lags ~Oct-2023, same 2011-12 base);
  91-day T-bill + India G-sec par-yield curve (1/5/10/30Y) from **FBIL** (`/wasdm/<prod>/fetchfiltered`;
  G-sec = per-date XLSX, "Par Yield" sheet, month-end sampled, both .xlsx+.xls via openpyxl/xlrd);
  **RBI repo rate** from **BIS** cbpol (`stats.bis.org`, RBI-attributed, step-compressed); FII/DII
  net from **NSE** (`fiidiiTradeReact`, latest-day → accumulate forward). Graceful-degrade contract.
  Env `DATA_GOV_API_KEY` (free; sample key caps at 10 rec/call). Tool: `Pull India Macro.bat`
  (`--list`/`--probe`). **Pending** (RBI DBIE is WAF-gated → needs browser auth): call money/WACR,
  forex reserves, bank credit, M3; merchandise trade (Commerce). Embedded inline as
  `window.VISTAS_MACRO={dates,series,meta}` (small) — no JS-parity port (`analytics.py` untouched).
  The Macro tab is India-first + global panels.

## Deploy / publish

- Live link = the **offline deck** served by GitHub Pages, NOT the Flask app.
- `publish_passive.py` (run via `Refresh Vistas Passive.bat`): refresh data → rebuild deck →
  **validate (Node runtime smoke-test)** → publish **only if good** (faulty deck never goes live).
- The **publish repo is a separate git repo** (its own `.git` + remote) holding `passive/index.html` +
  `terminal/` (the live site). It lives **inside this folder as `_pages/`** (git-ignored, so the dev repo
  never tracks it) — kept self-contained. Default = `<app>/_pages` (env-overridable via
  **`VISTAS_PUBLISH_DIR`**; remote via **`VISTAS_REMOTE`**). Moved out of the FFT tree on 2026-06-22.
- Pages source must be **Deploy from a branch → main / root** (not GitHub Actions).
- KV does not use git directly — keep publishing to **one double-click**.
- **★ DAILY REFRESH (`vistas/pipeline.py`, folder `pipeline/`):** one job refreshes EVERY source →
  reload → rebuild → validate → **auto-publish if the shell is valid**. Policy: **nothing but a
  faulty shell blocks publish** (degraded feeds are flagged, never gating); failsafe = build-only
  (`--no-push`) + one-click `pipeline/Publish Last Build.bat`. Health report → `data/_refresh/last_run.md`.
  **ADAPTABLE, not fragile:** feeds are a declarative registry (add one `Source(...)` line); the build
  auto-discovers tabs, so a **new tab needs NO pipeline change**. Schedule `Daily Refresh Vistas.bat`
  via Task Scheduler (~8pm IST). Full architecture (buildable bottoms-up without help) = `pipeline/README.md`.
- When I (Claude) build a feature, the build lands on KV's disk; KV publishes with one click
  (`pipeline/Publish Last Build.bat` = `publish_terminal.py --no-rebuild`) — **no full refresh needed
  per feature** (the `.bat` skips fetch + rebuild, just validates + pushes).

## Working style (KV)

Reply in plain, copious, bottom-up English; define terms at first use; recommendation, not
survey. Vistas serves real decisions — **"no score for error"**: when in doubt about a metric,
audit the convention, don't just trust parity. Keep ops one-double-click.

## Next

- **Vistas Active** sibling (MFI fund NAVs, category benchmarks) — same architecture, add an
  `active/` folder in the publish repo when built.
