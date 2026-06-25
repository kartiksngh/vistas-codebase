# Vistas — Passive NSE Index Terminal

Your own Bloomberg-style **GP / COMP** workbench for NSE total-return indices.
Pick any indices and any benchmarks, choose any date window, and read NAV,
comparative statistics, rolling alpha/beta, rolling risk, capture, calendar-year
returns, a monthly-return heatmap and the return distribution — all interactive.

It is **self-contained and Claude-free**: a plain Flask app + a Plotly front-end.
Run it with one command locally; deploy the same repo to a free web host when you
want it online.

---

## Prerequisites

- **Python 3.10+** — required (the app + data + analytics).
- **Node.js** — *optional*, only to validate the offline deck before publishing
  and to run the JS↔Python parity check.
- **Git + a GitHub SSH key** — *optional*, only to publish the live online link
  (see *Publish to GitHub Pages* below).

## Quick start (local)

```bash
cd Vistas
pip install -r requirements.txt          # one-time
python app.py
```

Then open **http://127.0.0.1:8753** in your browser. That's it.

- Change the port/host with env vars: `set VISTAS_PORT=9000` (Windows) before `python app.py`.
- On Windows it serves via **waitress**; if waitress isn't present it falls back to Flask's dev server. Either is fine for single-user desk use.

---

## What you can do

| Panel | Bloomberg analogue | Shows |
|---|---|---|
| **GP** | `GP` | Indexed NAV (rebased to 100), log toggle, range slider, crosshair hover |
| **COMP** | `COMP` | Stat table — CAGR, total, vol, Sharpe, Sortino, MaxDD, Calmar, best/worst 1Y, α — plus a CAGR/α bar |
| **Rolling α / β** | — | Rolling excess **or** Jensen's alpha, and rolling beta, vs each benchmark |
| **Rolling risk** | — | Underwater drawdown, rolling vol / Sharpe / correlation / relative strength |
| **Correlation matrix** | — | Pairwise return correlation of **all** selected series, red→yellow→green grading |
| **Capture** | — | Up / down capture, capture ratio, β, tracking error, info ratio + an up-vs-down scatter |
| **Calendar-year** | — | Year-by-year total return **and** alpha vs the primary benchmark, with % years +/− |
| **Monthly heatmap** | — | Month×year return grid (seasonality), green-high / red-low |
| **Distribution** | — | Rolling-return **and** alpha density curves (KDE) at 4 horizons, μ and σ |

**Controls:** multi-select **Indices** and **Benchmarks** (searchable), **From/To**
dates + quick ranges (1Y/3Y/5Y/10Y/Max), **Daily/Weekly** frequency, a **rolling
window** (1M…5Y), **Excess vs Jensen** alpha, a **risk-free %**, and a **log NAV**
toggle. Every panel has an inline *Definition · Method · Why* and a **CSV** button.

The first benchmark you add is the **primary** benchmark (used for the α columns
and the calendar-year alpha view).

---

## The data, and keeping it fresh

- **Bundled snapshot:** `data/Indices Data TR till <date>.csv` — wide daily
  total-return index levels (dividends reinvested), **2000-01-01 onward** (a few
  indices — NIFTY 50/100/200/500, BANK, FMCG — reach back to 2000; the rest begin
  at their NSE inception), **131 NSE indices** out of the box. The newest dated CSV
  in `data/` is used automatically. (The history floor is `data.HISTORY_FLOOR`.)
- **Full universe / add an index:** the picker lists every index in NSE's
  `IndexMapping.json`. Indices already in the snapshot load instantly; selecting
  one that isn't local offers to **fetch its full history** from NSE (one-time,
  needs internet). You can also use **＋ Add index** and type an exact NSE name.
- **⟳ Refresh to today:** pulls `last date → today` for every local index and
  writes a new dated snapshot, then reloads.
- **⬇ Fetch ALL NSE indices:** one click pulls full history for every not-yet-local
  index (background, with progress).
- **⬇ Export all NAV (Excel):** downloads a full multi-sheet workbook of the entire
  local dataset (every index, every date) — an **All NAV** sheet plus one sheet per
  NSE category (Broad market / Sector-thematic / Factor / …) and an **About** sheet.
  This is the complete data dump for other analysis, distinct from the per-chart CSV
  (which exports only the plotted series).

### Offline decks — view Vistas without running the app

Fetching/refreshing data is slow, but viewing shouldn't be. Vistas can save a
**self-contained, fully interactive HTML deck** you open in any browser with **no
server and no internet**:

- The deck embeds the **entire dataset** (every index, every date) **and a JavaScript
  port of the metric engine** (`static/vistas_analytics.js`). So inside the saved
  file you can still **re-pick indices/benchmarks, change the window, switch
  daily/weekly, toggle Jensen/excess α** — every panel recomputes in the browser.
- The JS engine is **verified numerically identical** to the server-side
  `analytics.py` (parity harness `_parity_dump.py` + `_parity_check.js`, 0
  mismatches), so the offline numbers match the live app exactly.
- **When decks are written** (to the `output/` folder):
  - automatically on **every data update** — ⟳ Refresh, ⬇ Fetch ALL, ＋ Add index —
    and on **app startup** (so the saved deck is never stale);
  - on demand via the **💾 Save offline deck (HTML)** button.
  - Each save writes a timestamped `Vistas_Passive_Deck_<date>_<time>.html` (multiple
    saves a day don't clobber) plus `Vistas_Passive_Deck_latest.html` (always newest).
- Typical size ≈ 11 MB (full history + vendored Plotly, all inline). `output/` is
  git-ignored.

**Polite fetching (so NSE never flags us):** all fetches use jittered pacing (no
fixed-interval bursts), a longer breather every ~120 requests, retry with
exponential back-off on rate-limits, a single persistent browser-like session with
periodic cookie refresh, and strictly sequential requests. Data is cached, so normal
use only ever fetches new days. A full rebuild from 2000 is available via
`python -c "from vistas import fetch; fetch.build_fresh()"` (chunked ≤1-year per NSE's
cap, calendar-gated to real trading days).

**Data quality filter:** on load, days where ≥25% of available indices are
unchanged vs the prior day are dropped as vendor-stale / non-trading days — the
same filter the research engine applies, so Vistas's NAVs line up with it.

### How the metrics are computed (so you can reproduce them)

All analytics are computed server-side in `vistas/analytics.py`, reusing the
project's exact conventions (see the provenance header in that file):

- **Returns:** simple period returns drive vol/Sharpe/β/capture; displayed NAV is
  the rebased index level. **Weekly** = W-FRI (Friday close).
- **CAGR** = `(level_end / level_start) ** (365 / calendar_days) − 1`.
- **Annualization:** rolling windows ≥ 1Y are annualized; < 1Y are cumulative.
- **Vol** = `std(returns) × √ppy`; **Sharpe** = `(mean − rf)·ppy / vol`;
  **Sortino** uses downside deviation; **MaxDD** = `min(level/cummax − 1)`;
  **Calmar** = `CAGR / |MaxDD|`. `ppy` = 252 daily / 52 weekly.
- **Excess α** = `CAGR(index) − CAGR(benchmark)`. **Jensen α / β** = intercept /
  slope of `r_index − rf = α + β(r_bench − rf)` over the rolling window.
- **Capture (up/down)** = `Σ(r_index | r_bench>0) / Σ(r_bench | r_bench>0)` (and `<0`).
- **Tracking error** = `std(r_index − r_bench)·√ppy`; **Info ratio** = `mean·ppy / TE`.

---

## Publish to GitHub Pages (the live shareable link)

This is how the **actual live link** is produced:

> **https://kartiksngh.github.io/vistas/passive/**

It serves the **offline deck** (a static HTML file) — *not* the Flask app. To
update it, just **double-click `Refresh Vistas Passive.bat`** (or run
`python publish_passive.py`). One run:
1. pulls the latest NSE data (best-effort; `--no-fetch` to skip),
2. rebuilds the self-contained offline deck,
3. **validates it** (Node runtime render smoke-test; structural fallback if Node
   is absent), and
4. **only if the deck is good**, copies it into the publish repo's
   `passive/index.html`, commits and pushes → Pages updates in ~1 min.
   A faulty deck is **never** published — the last good one stays live.

Flags: `--no-fetch` (rebuild from current data), `--no-push` (build + validate only).

**The publish repo is a separate git repo** (its own `.git` + remote) that holds
`passive/index.html` + `terminal/` (the live site). It lives **inside this folder as
`_pages/`** (git-ignored, so the dev repo never tracks it) — default `<app>/_pages`,
baked into `publish_passive.py` as `DEFAULT_PUB_DIR`. If you keep it elsewhere, set the
env var **`VISTAS_PUBLISH_DIR`** to its path — no code edit needed. To push to a
different GitHub repo (or over HTTPS instead of SSH), set **`VISTAS_REMOTE`**.

**One-time setup** (only if the publish repo doesn't exist yet): create an *empty*
public repo named `vistas` at <https://github.com/new> (no README/license), enable
**Pages → Deploy from a branch → `main` / root**, and register a GitHub **SSH key**
on the machine (or set `VISTAS_REMOTE` to an `https://…` URL). `publish_passive.py`
prints these hints if the push fails.

## Deploy the live *app* to a web host (optional, separate from the above)

If instead you want the full interactive Flask **app** online (live ⟳ Refresh /
Add-index), it's a standard Flask repo — push **this folder** to its own GitHub repo
and host it:

- **Render** (free): New ＋ → **Blueprint** → pick the repo (`render.yaml` is
  included). Or New ＋ → Web Service, build `pip install -r requirements.txt`,
  start `gunicorn app:app --bind 0.0.0.0:$PORT`.
- **Railway / Fly.io:** use the included `Procfile`.
- **Hugging Face Spaces (Docker/Python):** `gunicorn app:app --bind 0.0.0.0:7860`.

**Important caveat — live fetch on a host.** Free cloud hosts often **cannot reach
the niftyindices API** (it needs browser cookies/UA and may block datacenter
IPs/geos). So on a hosted Vistas the **⟳ Refresh** button and on-demand
**Add index** may fail — by design the app then just keeps serving the bundled
snapshot. Keep data current by either:
1. refreshing **locally** (`python app.py` → ⟳ Refresh) and `git push` the new
   `data/*.csv`, or
2. a scheduled **GitHub Action** that runs the fetch and commits the snapshot.

---

## Architecture

```
Vistas/
├── app.py                 Flask: serves the UI + JSON API (/api/catalog, /analyze, /refresh, /add_index, /save_deck); auto-saves a deck on data updates + startup
├── vistas/
│   ├── data.py            loads the newest snapshot, stale-day filter, windowed slices (HISTORY_FLOOR = 2000)
│   ├── analytics.py       all quant metrics (exact engine conventions; provenance inline) — the source of truth
│   ├── fetch.py           standalone NSE fetcher + local IndexMapping cache (graceful degrade)
│   ├── catalog.py         picker universe (local history + fetchable list)
│   ├── export.py          full multi-sheet NAV → Excel
│   └── deck.py            assembles the self-contained offline deck (inlines everything) → output/
├── static/
│   ├── index.html         the terminal page (9 panels)
│   ├── vistas.js          controls, multi-selects, Plotly rendering, CSV export; online OR offline (embedded-data) mode
│   ├── vistas_analytics.js  JS port of analytics.py (used by offline decks; parity-verified)
│   ├── vistas.css         house style
│   └── vendor/plotly.min.js   vendored so charts work fully offline
├── data/                  bundled snapshot CSV + IndexMapping.json cache
├── output/                generated offline decks (git-ignored)
├── _parity_dump.py · _parity_check.js · _deck_runtime_test.js   regression harness (JS↔Python parity + offline render smoke-test)
├── requirements.txt · Procfile · render.yaml · .gitignore
```

> **Keeping the offline numbers honest:** `static/vistas_analytics.js` is a hand
> port of `vistas/analytics.py`. If you change a formula in `analytics.py`, mirror
> it in the JS and re-run the parity check (`python _parity_dump.py && node
> _parity_check.js`, expect 0 mismatches) so the offline deck can't drift.

It does **not** import the research `strategy/` package — the formulas are
re-implemented here (with provenance) so the folder is portable. An **Active**
sibling terminal (mutual-fund NAVs) is planned with the same shape.
