# Vistas — data pipeline & terminal architecture

> The single reference for how Vistas pulls data, joins it, builds the terminal, and publishes —
> end to end. If you ever had to **rebuild the whole terminal from an empty machine without help,
> this document is enough to do it.** It also explains the one nightly job that keeps everything
> fresh, and how to extend the system without making it fragile.

Vistas is KV's self-hosted "Bloomberg" — a Flask + Plotly analytics terminal for NSE total-return
indices, plus per-company fundamentals, a per-stock quant cockpit, mutual-fund NAVs & portfolios,
and India macro. The thing the public sees is a **hosted, lazy-load static site** on GitHub Pages
(`https://kartiksngh.github.io/vistas/terminal/`), **not** the Flask app. The Flask app
(`python app.py`) is the local cockpit you use while developing; the published site is a baked
snapshot of its data + a JavaScript engine that recomputes analytics in the browser.

---

## 1. The four layers (the whole system in one picture)

```
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │ LAYER 1 — DATA      ~10 independent public feeds, each its own fetcher        │
  │  NSE indices · NSE bhavcopy · Yahoo stocks/world · MOSPI/FBIL/BIS macro ·     │
  │  AMFI fund NAV + market cap · AMC fund portfolios · Screener fundamentals     │
  │        ↓ each writes a CSV / JSON snapshot into  data/                        │
  ├─────────────────────────────────────────────────────────────────────────────┤
  │ LAYER 2 — IDENTITY   one immutable surrogate key `vst_id` is the spine        │
  │  vistas/idmap.py joins every source on ISIN → vst_id → current NSE symbol      │
  ├─────────────────────────────────────────────────────────────────────────────┤
  │ LAYER 3 — BUILD      deck.save_terminal_site() bakes the lazy-load site        │
  │  shell (index.html + JS engine) + per-symbol JSON; analytics in Python →      │
  │  embedded as values (display-plane) OR recomputed in JS (parity-plane)        │
  │        ↓ writes  output/terminal_site/                                        │
  ├─────────────────────────────────────────────────────────────────────────────┤
  │ LAYER 4 — PUBLISH    publish_terminal.py validates then mirrors → git push    │
  │  a faulty shell is NEVER published; live in ~1 min on GitHub Pages             │
  └─────────────────────────────────────────────────────────────────────────────┘

  The DAILY REFRESH ENGINE (vistas/pipeline.py) runs Layer 1 → reload → Layer 3 → Layer 4
  for you, every night, robustly. That is what  pipeline/Daily Refresh Vistas.bat  triggers.
```

Two design rules make the whole thing extensible instead of fragile:

- **Display-plane vs parity-plane.** Anything computed in `vistas/analytics.py` (NAV / returns /
  alpha / risk for the Prices view) is *also* implemented in `static/vistas_analytics.js` so the
  offline/published deck recomputes it identically — these two MUST stay numerically identical
  (the **parity discipline**, §7). Everything else (fundamentals, quant, funds, macro) is computed
  **once in Python and embedded as values**; the browser only renders it, so there is **no parity
  burden** — that is why new tabs are cheap to add.
- **Adaptable refresh.** The list of data feeds is a **declarative registry** in
  `vistas/pipeline.py`. Adding a feed = append one line. The build step auto-discovers every tab's
  data, so a **new tab needs no pipeline change at all**.

---

## 2. Layer 1 — the data sources

Every fetcher degrades gracefully (network is optional: if a feed is unreachable the last good
snapshot on disk is kept). Each writes the newest-dated file into `data/`; the app/build always
picks the newest dated file automatically.

| Source | Module | Incremental refresh call | Writes | Cache | Manual `.bat` |
|---|---|---|---|---|---|
| NSE TR indices | `vistas/fetch.py` | `update(dry=False)` | `data/Indices Data TR till <date>.csv` | NSE cookies | (in publishers) |
| NSE PR + valuation (PE/PB/DY) | `vistas/fetch.py` | `update_measures(groups=("PR","VAL"))` | `data/Indices Data {PR,PE,PB,DY} till <date>.csv` | NSE cookies | `Pull PR + Valuation.bat` |
| Stock prices (Yahoo) | `vistas/stocks.py` | `update_stocks()` | `data/Stocks Data PX till <date>.csv` | yfinance | `Refresh Stocks.bat`, `Pull All Indian Stocks.bat` |
| Stock TR + identity master | `vistas/bhav_prices.py` | `build_cache()` then `build_panel(promote=True)` | `data/Stocks Data TR till <date>.csv`, `data/stock_security_master.json` | `data/_bhavzip/` zip cache | `Pull NSE Bhavcopy.bat` |
| Market internals | `vistas/bhav_derived.py` | computed at build from the TR panel | (none — in-memory) | — | (in build) |
| World / cross-asset | `vistas/world.py` | `update_world()` | `data/World Data PX till <date>.csv` | yfinance | `Pull World Markets.bat` |
| India macro | `vistas/macro.py` | `build_snapshot()` | `data/India Macro till <date>.csv` | per-series JSON | `Pull India Macro.bat` |
| Market cap (AMFI bulk) | `vistas/shares.py` | `build_from_amfi()` | `data/shares.json` | the JSON itself | `Pull NSE Shares + Market Cap.bat` |
| Exact issued shares (NSE) | `vistas/shares.py` | `build(symbols)` | `data/shares.json` | the JSON itself | `Pull NSE Shares + Market Cap.bat --issued-only` |
| Mutual-fund NAVs | `vistas/funds_nav.py` | `build_snapshot()` | `data/India Mutual Fund NAV till <date>.csv` | `data/mf_nav_cache/` | (in build) |
| Fund portfolios (holdings) | `vistas/funds_portfolio.py` | `build_all()` | `data/funds_portfolio/<key>.json` | `data/funds/portfolio_cache/` (workbook bytes) | (in build) |
| Screener fundamentals | `vistas/screener.py` | `refresh(symbols, full=False)` | `data/screener/<SYM>.json` | per-company JSON (30-day TTL) | `Pull Screener Fundamentals.bat` |
| StarMine ARM (gated, licensed) | `vistas/starmine.py` | reads a user-supplied CSV | (in-memory, merged into fundamentals) | — | — |

**Notes that bite if you forget them:**
- **NSE feeds throttle.** Run them when NSE has cooled down; all degrade rather than crash.
- **NSE quote API (exact issued shares) is Akamai-WAF'd** → 403 from a datacenter IP. It works from
  KV's own runtime; the pipeline pulls it in bounded slices and degrades elsewhere.
- **Fund portfolios are fetched from each AMC's CDN**, not AMFI (there is no consolidated bulk
  feed). Workbooks are cached as raw bytes so a rebuild never re-downloads (monthly files).

---

## 3. Layer 2 — the identity spine (`vst_id`)

The trap in any multi-source pipeline: the **same** security is named differently by every vendor
(NSE symbol, ISIN, Bloomberg ticker, LSEG id), and those names **change** (renames; re-issued
ISINs). A wrong join corrupts every downstream number **with no error**.

Vistas mints its own permanent surrogate key **`vst_id`** (e.g. `VST00019`) that never changes.
ISIN / symbol / Bloomberg-ticker / LSEG-id are time-varying **attributes** pointing at it. The one
resolver — `vistas/idmap.py` — does ISIN → `vst_id` → current NSE symbol for **every** source, with
the full ISIN lineage (so an old/renamed ISIN still resolves) and an ISO-6166 **check-digit**
guard. Master file: `data/stock_security_master.json` (rebuilt by `bhav_prices.build_lineage`).
Public columns (vst_id, ISIN, symbol) are publishable; the **licensed** Bloomberg/LSEG columns live
only in the gated `vistas_gated/` tree and must never reach the published site (see §7 leak check).
Full method: the global `identifier-resolution` skill.

**Join on `vst_id`; display the current symbol.** Never string-join on a vendor ticker.

---

## 4. Layer 3 — building the terminal site

`vistas/deck.py → save_terminal_site(reason)` builds `output/terminal_site/`:

- `index.html` — the light **shell** (~8–9 MB): the page + Plotly + `static/vistas.js` controls +
  `static/vistas_analytics.js` engine + the default index selection + macro inline + manifests.
- `data/stocks/<SYM>.json`, `data/fundamentals/<SYM>.json`, `data/quant/<SYM>.json`,
  `data/indices/<measure>/<NAME>.json`, `data/world/…`, `data/funds_nav/…`,
  `data/funds_portfolio/<key>.json` — **per-symbol files fetched on demand** so the shell stays
  small and the page loads fast.

The build **auto-discovers** everything: it walks the loaded data and the fundamentals/quant/funds
datasets and writes a file per item plus a manifest. **That is why adding a new tab requires no
pipeline change** — only a new *data feed* does (one registry line in `pipeline.py`). Every exported
per-stock/per-company file is stamped with its `isin` + `vst_id` so the data stays joinable by the
stable id even after a rename.

---

## 5. Layer 4 — publishing

`publish_terminal.py` is the gate. One run does: (optional NSE pull) → `build_site()` →
**`validate(shell)`** (a Node runtime smoke-test that renders every panel headless) → `publish_site()`
(robocopy `/MIR` the built site into the publish repo, then `git commit` + `git push`).

- **A faulty shell is NEVER published** — validation must pass first; the last good site stays live.
- The **publish repo is a separate git repo** (its own `.git` + remote) living inside this folder at
  `_pages/` (git-ignored, so the dev repo never tracks it). Override with env `VISTAS_PUBLISH_DIR` /
  `VISTAS_REMOTE`. GitHub Pages source = **Deploy from a branch → main / root**.

Flags: `--no-fetch` (rebuild from data on disk), `--no-rebuild` (publish the on-disk site as-is —
fastest), `--no-push` (build + validate only), `--email` (also email the single-file deck).

---

## 6. The daily refresh engine (`vistas/pipeline.py`)

One process that does Layer 1 → reload → Layer 3 → Layer 4, robustly, every night.

**Policy (KV):** **auto-publish if the shell is valid**, with a **build-only failsafe** + one-click
publish if the auto-publish ever fails; and **nothing but a faulty shell blocks the publish** —
degraded feeds are flagged in the report, never gating.

**The `.bat`s in this folder:**

| File | What it does |
|---|---|
| `Daily Refresh Vistas.bat` | the nightly job: refresh all → rebuild → validate → **publish if green** |
| `Nightly Build (no publish).bat` | failsafe: refresh → rebuild → validate, **no push** (eyeball, then publish by hand) |
| `Publish Last Build.bat` | push whatever is already built on disk (failsafe retry, or publish a Claude-built feature) |

**Under the hood:** `python -m vistas.pipeline`. Flags: `--no-push`, `--dry-run`, `--light` (skip
the heavy feeds: bhavcopy, screener, issued shares), `--skip a,b`, `--only a,b`. Every run writes
a health report to **`data/_refresh/last_run.md`** (+ `run-<date>.json`): per-source status
(fresh / no-op / degraded), as-of dates, timings, what published. A market holiday is a **no-op**,
not a failure.

### How to ADD a data source (the adaptable part)

Append **one line** to `build_sources()` in `vistas/pipeline.py`:

```python
S.append(Source("my_feed", "My new feed",
                run=lambda: __import__("vistas.my_module", fromlist=["x"]).update(),
                reads="data/My Data till <date>.csv"))
```

`Source(key, title, run, heavy=False, reads="")` — `run` is a zero-arg callable that does the
incremental pull and returns the fetcher's native status dict (the generic normaliser sniffs
`ok` / `asof` / `rows` etc., so a custom adapter is rarely needed). Tag slow feeds `heavy=True` so
`--light` can skip them. That's it — the runner, audit, report, and publish handle it unchanged.

### Scheduling it (Windows Task Scheduler)

Create a Basic Task → Daily → ~8:00 PM IST (after EOD files post) → *Start a program*:

```
Program/script:   cmd.exe
Add arguments:    /c "C:\Users\Administrator\Documents\Projects\Vistas\pipeline\Daily Refresh Vistas.bat"
```

(Or schedule `python -m vistas.pipeline` directly with the working directory set to the repo root.)
The job is idempotent and resumable — running it twice in a day is safe.

---

## 7. The two disciplines you must not skip

1. **Parity.** If you change ANY formula in `vistas/analytics.py`, mirror the exact change in
   `static/vistas_analytics.js`, then run `python _parity_dump.py` → `node _parity_check.js`
   (must print 0 mismatches) → `node _deck_runtime_test.js` (must print PASS). Skipping this makes
   the published deck silently disagree with the live app. Fundamentals/quant/funds/macro are
   display-plane (no parity burden) but ALWAYS runtime-test the shell.
2. **Licensing leak check.** The published site must carry only **public** identifiers (`isin`,
   `vst_id`, NSE symbol) — never a licensed Bloomberg ticker or LSEG `secid`. Before/at publish,
   grep `output/terminal_site/` for `secid` / `lseg` / Bloomberg tickers; the gated layer must never
   leak into the public files.

---

## 8. Build it bottoms-up from an empty machine

1. **Install:** Python 3.10+, `pip install -r requirements.txt`. (Node.js optional, for validation
   + parity checks. Git + a GitHub SSH key only to publish.)
2. **Seed the data:** the repo ships bundled snapshots in `data/` (committed on purpose). To rebuild
   from scratch instead: `python -c "from vistas import fetch; fetch.build_fresh()"` (indices), then
   the per-source pulls (`Pull NSE Bhavcopy.bat`, `Refresh Stocks.bat`, `Pull World Markets.bat`,
   `Pull India Macro.bat`, `Pull Screener Fundamentals.bat`, `Pull NSE Shares + Market Cap.bat`).
3. **Identity:** `data/stock_security_master.json` is rebuilt by the bhavcopy pipeline
   (`bhav_prices.build_panel`). idmap reads it.
4. **Run locally:** `python app.py` → http://127.0.0.1:8753 — the full cockpit.
5. **Build the site:** `python -c "from vistas import deck; deck.save_terminal_site()"` →
   `output/terminal_site/`. Preview with `Preview Terminal Site.bat`.
6. **Publish:** `python publish_terminal.py` (validates, then pushes). Set Pages → branch main/root.
7. **Automate:** schedule `pipeline/Daily Refresh Vistas.bat` (§6).

After that, the terminal runs and refreshes itself; you only touch code to add a feature (and then
publish with `Publish Last Build.bat`).
