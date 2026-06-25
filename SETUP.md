# Vistas — Setup & Bootstrap (plug-and-go on a fresh machine)

This is the **Vistas codebase** — everything needed to build, run, and update the terminal from scratch.
Clone it, install, (optionally) fetch fresh data, run. Heavy regenerable data and licensed third-party
feeds are **not** shipped (see §5) — the fetchers rebuild them locally.

> One-line mental model: **this repo is the engine; the data is fetched/rebuilt by the engine.** A small
> seed (the index total-return snapshot + config maps) ships so the core terminal runs immediately.

---

## 1. Prerequisites
- **Python 3.10+** (3.12 recommended) and `pip`.
- **git**.
- **Node.js 18+** — *optional*, only for the deck-validation smoke-test and the JS↔Python parity check.
- Internet access to the public data sources (NSE / Yahoo / MOSPI / FBIL / AMFI) **only if you want to
  refresh data**; the seed snapshot lets the core app run offline.

## 2. Get the code
```bash
git clone git@github.com:kartiksngh/vistas-codebase.git
cd vistas-codebase
```

## 3. Python environment
```bash
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# Windows (Git Bash):    source .venv/Scripts/activate
# macOS/Linux:           source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Run the terminal (local)
```bash
python app.py
# → open http://127.0.0.1:8753
```
The core index-analytics terminal runs immediately off the seeded `data/Indices Data TR till <date>.csv`.
The stock / funds / macro layers light up once you fetch their data (§6).

## 5. What ships vs what you fetch vs what's licensed
| Layer | In the repo? | How to get it |
|---|---|---|
| **All code** (Flask app, `vistas/` engines, `static/` front-end, fetchers, pipeline, publishers, harnesses) | ✅ yes | — |
| **Seed data**: newest **Index TR** CSV (~5 MB), World/Macro snapshots, `IndexMapping.json` + small JSON maps, `fundamentals_annual_consolidated.csv` | ✅ yes | — |
| **Heavy regenerable data**: per-stock TR/PX panels (130–185 MB each), funds `holdings_history.parquet`, `legacy_mcap_daily.csv`, `export/`, raw caches | ❌ **git-ignored** (too big for GitHub, all rebuildable) | run the fetchers (§6) |
| **Licensed / gated**: LSEG **StarMine ARM** raw parquet (`arm_repo/`), any `vistas_gated/` / `data/_gated/` | ❌ **never committed** (paid third-party IP) | obtain your **own LSEG licence**, point `$VISTAS_ARM_DIR` at the dump; only ABSL-signed-off ARM *cards* baked into the shipped JSON may be shared |

## 6. Refresh / rebuild data from scratch
All fetchers degrade gracefully (a feed that's unreachable keeps the last-good data).
```bash
# Daily all-source refresh → reload → rebuild → validate (the orchestrator):
python -m vistas.pipeline                 # or: pipeline/Daily Refresh Vistas.bat

# Individual feeds:
python -c "from vistas import fetch; fetch.build_fresh()"   # full NSE index TR rebuild from 2000
#  (Windows .bat wrappers also exist: Pull World Markets.bat, Pull India Macro.bat, etc.)
```
- **Env vars (optional):** `DATA_GOV_API_KEY` (free; WPI breakdown), `VISTAS_ARM_DIR` (ARM dump location),
  `VISTAS_PUBLISH_DIR` / `VISTAS_REMOTE` (publish target — see §8).

## 7. Build the published terminal site (offline, hosted)
```bash
python publish_terminal.py --no-fetch --no-push   # full rebuild + validate, no publish (~45–55 min)
```
This bakes the per-stock fundamentals/quant JSONs, inlines the single-page site into
`output/terminal_site/`, and runs the Node runtime smoke-test (all panels render, 0 errors).

## 8. Publish (optional — needs the separate Pages repo)
The live site is served from a **separate git repo** (its own remote), kept locally at `_pages/`
(git-ignored here so this codebase never tracks it).
```bash
python publish_terminal.py --no-rebuild           # validate + push the already-built site
# configurable via env: VISTAS_PUBLISH_DIR (default ./_pages), VISTAS_REMOTE
```

## 9. ★ The Parity Discipline (the #1 rule — read before touching analytics)
The offline deck recomputes everything in the browser via `static/vistas_analytics.js`, a hand-port of
`vistas/analytics.py`. **If you change ANY formula in `analytics.py`, mirror it in `vistas_analytics.js`,
then run:**
```bash
python _parity_dump.py        # dumps Python results for 12 configs
node _parity_check.js          # must print 0 mismatches
node _deck_runtime_test.js     # must print PASS (all panels render)
```
Skipping this makes the offline deck silently disagree with the live app.

## 10. Where to read more
- **`CLAUDE.md`** — the full architecture + conventions + the project's north-star vision (the Agentic AMC).
- **`README.md`** — the long-form run/refresh/deck/publish guide. **`How to run.txt`** — the short version.
- **`pipeline/README.md`** — the daily-refresh engine, buildable bottoms-up.
- Design blueprints: `MESH_DESIGN.md`, `ANALYST_GOLDMINE.md`, `FM_INTELLIGENCE.md`, `CIO_INTELLIGENCE.md`,
  `FUNDAMENTAL_LAW.md`, `AGENTIC_AMC.md`, `FLOW_DECOMPOSITION.md`, `SCORING_AUDIT.md`.

---
*Generated 2026-06-26. This repo is the plug-and-go codebase; the published data lives in the separate
GitHub-Pages repo (`kartiksngh/vistas`).*
