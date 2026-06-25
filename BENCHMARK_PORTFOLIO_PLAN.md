# Benchmark Portfolios in the Funds Tab — build plan (KV directive 2026-06-25)

## Goal (KV's words, decoded)
Build **benchmark "portfolios"** for NSE passive indices (the ones we already have TRI for), with **two
weighting schemes** — **equal-weight (EW)** and **free-float market-cap (FF-mcap, reconstructed per NSE
methodology)** — and feed them into the **Funds tab** so the SAME holdings analytics that run on funds
(active share, sector exposure/rotation, hit rate, slug, top over/under-weights) also run on the benchmark.
Then let a user pick a benchmark from a **dropdown** and compare **one or more fund schemes side-by-side vs
that benchmark** (and vs each other) — mirroring the multi-series comparability we already have in the
**Prices** tab, now for **holdings** in the Funds tab. **Build + publish.**

## Why (objective)
Vistas = KV's Bloomberg. Prices tab already compares any series vs any benchmark. The Funds tab compares
funds to each other and (today) to a *peer-consensus* proxy. The missing piece is a real **index benchmark**
to measure a fund against — for true(ish) **benchmark-relative active share**, sector tilts vs the index, and
overlap. This closes the "compare to benchmark" gap and upgrades the active-share metric KV flagged.

## Data we have / lack (scouted 2026-06-25)
- **TRI:** 131 NSE indices in `data/Indices Data TR till <date>.csv` (Nifty 50/100/200/500, Next 50, Midcap
  150, Smallcap 250, LargeMidcap 250, factor + sectoral). ← the benchmark universe.
- **Market cap:** `data/_shares/amfi_mcap.json` = AMFI official **avg FULL market cap** per stock + SEBI size
  cohort (31-Dec-2025, 6-monthly). `issued_shares.json` empty (NSE WAF-gated). → FF-weighting uses **full
  mcap as the documented approximation** (no free-float IWF available). EW = 1/N.
- **Stock identity:** `data/stock_security_master.json` (ISIN/symbol/vst_id) → map constituents to our panel.
- **MISSING / open:** (a) robust **constituent-list fetch** past niftyindices WAF; (b) **free-float IWF**
  (approximate with full mcap); (c) **historical constituent membership** (niftyindices doesn't freely
  publish it) → rolling-over-time benchmark holdings are an approximation, see Phase 2 caveat.

## Phases & to-do
### Phase 0 — RESEARCH (workflow, parallel) ← launching now
- R1 NSE/niftyindices **methodology**: exact FF-mcap weight formula, IWF, capping rules (sector/thematic
  caps, 25%/single-stock caps), reconstitution cadence. Output → the weighting spec.
- R2 **Constituent fetch**: a robust FREE method for current constituents of NSE indices (niftyindices CSV
  endpoints / NSE archives / WAF workaround), TESTED on Nifty 50 + Nifty 500; return working method + lists.
- R3 **SEBI-category → benchmark map**: the standard benchmark index per equity category (Large Cap→Nifty 100
  TRI, Mid→Nifty Midcap 150 TRI, Small→Nifty Smallcap 250 TRI, Flexi/Multi→Nifty 500 TRI, ELSS→Nifty 500,
  Large&Mid→Nifty LargeMidcap 250, Focused→Nifty 500, Value→Nifty 500 Value 50, etc.) + which of our 131 TRI.

### Phase 1 — DATA: benchmark portfolios (`vistas/benchmarks.py`)
- Fetch current constituents for the priority indices (standard fund benchmarks first; then all feasible).
- Map symbol→vst_id; pull full mcap from `amfi_mcap.json`.
- Build per index: EW weights (1/N) + FF-mcap weights (mcap/Σmcap, capping per R1), with a `methodology_note`
  ("full-mcap approximation; no IWF").  Attach sector (from fundamentals/AMFI).
- Output `data/benchmarks/<index_slug>.json` = {index, asof, weighting_variants, constituents:[{symbol,
  vst_id, name, sector, weight_ew, weight_ffmcap}], note}.  Self-describing.

### Phase 2 — ANALYTICS: benchmark as a pseudo-fund
- Adapter so the Funds holdings analytics treat a benchmark portfolio as a "fund": sector exposure, top
  holdings, **benchmark-relative active share** = ½·Σ|w_fund − w_bench| (the real one, complementing the
  peer proxy), holdings overlap, active over/under-weights.
- CAVEAT (be loud): current-snapshot is clean; **rolling-over-time** (sector rotation / hit-rate history)
  needs historical membership we lack → either (a) current-membership back-projected (look-ahead, labeled)
  or (b) restrict rolling analytics to the snapshot. Decide in Phase 2 from R1/R2 findings.

### Phase 3 — UI: comparison in the Funds tab
- Benchmark **dropdown** (per index × {EW, FF-mcap}); default = the fund's category benchmark (R3 map).
- Side-by-side: fund(s) vs benchmark — active share, sector tilt bars, top over/under-weights, overlap %.
- Mirror the Prices-tab multi-select comparability (compare ≥1 scheme to the chosen benchmark and to each other).

### Phase 4 — BUILD + VERIFY + PUBLISH
- Rebuild terminal, `_pup_fundskill.js` (+ new benchmark-panel gate), publish.

## Conventions / honesty
- Label FF-mcap weights "reconstructed, full-mcap approximation (no free-float IWF)".
- Benchmark-relative active share is the *real* metric (vs index) — keep the peer-relative one too (KV: both
  are useful, distinct lenses).
- No parity port needed (display-plane, Python-baked, puppeteer-verified) — same discipline as the funds work.
