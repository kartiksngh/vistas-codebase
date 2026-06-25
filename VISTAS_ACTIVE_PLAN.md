# Vistas Active — Build Plan (active Indian mutual-fund schemes: NAV + portfolios)

> Produced by the `vistas-active-mf-plan` research workflow (6 agents) on 2026-06-22.
> This is the decision record for the next major workstream after Quant & MI. Source of truth
> for the Active-MF build; update as phases ship.

**Plain-English one-liner of the goal:** add live mutual-fund *schemes* (the actual funds people buy, run by managers — "active" because a human picks stocks, vs a passive index) into Vistas in two ways: (1) their **NAV** (Net Asset Value — the fund's per-unit price, the thing that goes up and down) as a selectable price series next to indices and stocks, and (2) their **portfolio holdings over time** (which stocks the fund owns, and how much) for a brand-new analytics tab.

The whole plan honours the Vistas philosophy already in the codebase: **one standalone fetcher module per source** (own session, polite pacing, local cache, `ok:False` graceful-degrade), **clean in-house, then publish**, and **"no score for error."**

---

## 1. SOURCE DECISIONS

A note on one term used throughout: **TRI** = Total Return Index, an index that assumes dividends are reinvested; mutual-fund NAVs are already total-return (dividends flow back into NAV in a Growth-option scheme), so NAV is directly comparable to a benchmark's TR series — which is exactly what Vistas already stores.

| Data need | PRIMARY (exact source/format) | FALLBACK | CROSS-CHECK | Reliability rationale + ToS caveat |
|---|---|---|---|---|
| **Scheme NAV history** | `api.mfapi.in/mf/{schemeCode}` → JSON `{meta, data:[{date "DD-MM-YYYY", nav "decimal-string"}]}`; master list `api.mfapi.in/mf`. 20+ yrs depth, ~42k schemes with valid ISIN. | **AMFI official** `portal.amfiindia.com/DownloadNAVHistoryReport` (form POST, `frmdt`/`todt`; semicolon-delimited: Code;ISIN-payout;ISIN-reinvest;Name;NAV;Date). Slower, but it is the source mfapi mirrors. | Latest daily NAV from the scheme's own **AMC factsheet** (month-end value) must equal mfapi's value for that date. | mfapi is a free, no-key, stable-since-2018 community mirror **built on AMFI's own published NAVs** — so the *underlying* data is authoritative; mfapi is just convenient transport. ToS: no published API ToS; AMFI NAV is public, redistribution of derived NAV with attribution ("Data from AMFI") is fine for a self-host. **We do not depend on mfapi as authority — AMFI is the authority, mfapi is the pipe, and the fallback proves it.** |
| **Full portfolio holdings (every stock, not just top-10)** | **AMC monthly disclosure XLSX** off each AMC's statutory-disclosure page (SEBI Reg 59A mandate; equity monthly within 10 days of month-end). Fields per SEBI MFD/CIR/9/120/2000: ISIN, Security Name, Industry, Quantity, Market Value, **% to NAV**. Best-organised: Nippon, HDFC (`hdfcfund.com/statutory-disclosure/monthly-portfolio`). | **AdvisorKhoj aggregator** (`advisorkhoj.com/form-download-centre/Mutual`) — consolidated month/year downloads, 3–5 yr archive for major AMCs — when an AMC's own site is JS-gated or drops old months. | Sum of `% to NAV` ≈ 100; and the scheme's top holdings vs the **AMC's own monthly factsheet** top-10. | This is **SEBI-mandated, fund-manager-signed official disclosure** — the highest-trust holdings source. Caveat: no API, per-AMC layout drift, historical depth on own sites only 6–18 months (AdvisorKhoj backfills). ToS: official disclosures are public; we publish *computed analytics*, not raw redistributed files. |
| **Factsheet stats (P/E, P/B, sector mix, AUM, expense ratio, turnover, top-10)** | **AMC monthly factsheet PDF** (same statutory pages), parsed with `pdfplumber`. | AdvisorKhoj factsheet archive. | Our **own look-through** computation (section 4) should land near the factsheet's stated weighted P/E — a free correctness check. | Official, audited, monthly. We treat these as a **cross-check on our own look-through**, not a primary store, because formats drift hard across 40+ AMCs. Defer heavy PDF parsing — see phasing. |
| **ISIN → NSE symbol** | **Reuse, already in repo:** NSE `EQUITY_L.csv` (`nsearchives.nseindia.com/content/equities/EQUITY_L.csv`, cols SYMBOL/ISIN/NAME/SERIES) → new tiny cache `data/funds/isin_to_nse.json`. | **Reuse:** `data/stock_security_master.json` (`{permid:{isins,symbols,latest_symbol,name}}` from `bhav_prices.py`) for delisted/renamed tickers. | Resolved symbol must exist in `stocks.load()` panel. | Both already maintained in Vistas, cover 4370+ equities **with ISIN lineage** (handles symbol changes). Zero new external dependency. |
| **Scheme category + benchmark** | **Category:** `meta.scheme_category` from mfapi (already SEBI-2017 aligned, e.g. "Equity Scheme - Mid Cap Fund"). **Benchmark:** a hand-maintained **category→benchmark map** (Large Cap→NIFTY 50 TRI, Mid Cap→NIFTY Midcap 150 TRI, Multi/Flexi→NIFTY 500 TRI, etc.) keyed to indices we already hold. | Parse benchmark name from the AMC factsheet header (Phase 2). | Scheme-name keyword ("Nifty 50 Index Fund") vs the mapped benchmark. | Category from the same call as NAV = free. A category-level benchmark map is small, stable, and good enough for MVP; per-scheme factsheet benchmark is a Phase-2 refinement. Caveat: declared benchmark occasionally differs from category default — flag mismatches, don't silently trust. |
| **Index constituents + weights (for active-share/overlap)** | **NSE Indices CSV** `niftyindices.com/IndexConstituent/ind_{index}list.csv` (cols: Company Name, Industry, **Symbol**, Series, **ISIN**). | `nsearchives.nseindia.com/content/indices/ind_{index}list.csv` mirror. | Constituent count vs the index's published size (NIFTY 50 → 50 rows). | Official NSE Indices (SEBI-regulated index provider). **Caveat: the constituent CSV has NO weights** — weights come from the monthly **factsheet PDF** (`niftyindices.com/Factsheet/ind_{index}.pdf`) or are recomputed from free-float mcap. ToS: download/clean/cache for in-house analytics is permitted; **do NOT expose raw NSE weights via an API** — publish computed results only. |

---

## 2. DATA ARCHITECTURE

New modules, each mirroring the existing one-file-per-source pattern (`load`/`available`/`coverage` like `world.py`/`stocks.py`/`macro.py`; `build_fresh`-style fetch like `fetch.py`). **Graceful-degrade contract everywhere: on network/parse failure return `{"ok": False, "error": ...}` and keep serving the last cached snapshot — never crash, never publish a hole.**

| New module | Responsibility | Graceful-degrade |
|---|---|---|
| `vistas/funds_nav.py` | AMFI/mfapi NAV fetcher + cache. `build_fresh()` enumerates schemes from `/mf`, lazy/batch-pulls `/mf/{code}`, writes per-scheme `data/funds/nav/{code}.json` and a wide snapshot `data/funds/MF NAV till {date}.csv` (Date × schemeCode). Exposes `load()/available()/coverage()` identical in shape to `stocks.py`. | NSE/mfapi down → `ok:False`, serve last `MF NAV till *.csv`. |
| `vistas/funds_portfolio.py` | Per-AMC monthly-disclosure fetcher + XLSX/PDF parser + normaliser. One small **per-AMC adapter** (URL pattern + column map) so format drift is isolated. Output: tidy `data/funds/portfolio/{schemeCode}/{YYYYMM}.csv` with normalised `[isin, security_name, industry, quantity, market_value, pct_to_nav, resolved_symbol]`. | Any AMC page JS-gated/changed → that AMC's adapter returns `ok:False`, fall back to AdvisorKhoj, else skip that month and keep prior snapshots. **One AMC breaking never blocks the others.** |
| `vistas/funds.py` | Catalog/loader + the join layer. Builds `data/funds/scheme_master.json` (`{code:{name, category, fundHouse, isinGrowth, benchmark}}`), resolves ISIN→symbol via the reused maps, and serves the picker universe (a "Mutual Funds" group, grouped by category) alongside indices/stocks. Holds the category→benchmark map. | Master rebuild fails → serve cached `scheme_master.json`. |
| `vistas/funds_lookthrough.py` *(Phase 3)* | Joins a scheme's holdings to our per-stock fundamentals + stock TR panel to compute weighted portfolio quality/valuation, active-share, overlap, churn. Pure compute over cached data — no network. | Missing fundamentals for a holding → exclude from weighted stat, report the coverage % (never silently renormalise without flagging). |

**Reused (imported, not rebuilt):** `data.py` index TR levels (benchmarks + the comparison/rebase engine), `stocks.load()` (stock TR panel for look-through), `data/stock_security_master.json` + NSE `EQUITY_L.csv` (ISIN→symbol), `fundamentals.py`/`screener.py` (per-stock quality/valuation for look-through), `analytics.py` (all NAV performance math — CAGR/Sharpe/alpha/beta — works unchanged on a NAV level series), `catalog.py` (picker), `fetch.py` (the graceful-degrade + cookie/pacing template).

**Cache layout under `data/funds/`:** `nav/{code}.json`, `MF NAV till {date}.csv`, `portfolio/{code}/{YYYYMM}.csv`, `scheme_master.json`, `isin_to_nse.json`, `index_constituents/{index}.csv`.

**No JS-parity burden for the Funds tab.** Following the Macro-tab precedent in CLAUDE.md, portfolio analytics are computed **once in Python** and embedded as values (`window.VISTAS_FUNDS = {...}`); JS only renders. This keeps `analytics.py`/`vistas_analytics.js` untouched — **except** scheme NAV in the Prices view, which rides the *existing* parity-clean engine (it's just another level series), so no new parity risk there either.

---

## 3. IN-HOUSE CLEANING / QA ("no score for error")

Every check below has a definition + method so it's reproducible, and each one **flags rather than silently fixes** wherever a "fix" could hide a real error.

1. **NAV validity + continuity.** Drop NAV ≤ 0 or non-numeric; enforce strictly increasing dates after `dayfirst` parse; de-dup repeated dates (keep last). Reuse the **coverage gate** convention from `data.py` (MIN_OBS_PER_YEAR=150, MAX_INTERNAL_GAP_ROWS=25) so a sparse/closed scheme can't render fabricated analytics.
2. **Stale-day filter.** Apply the same rule the index engine uses: a NAV unchanged vs the prior business day across the fund universe on a non-trading day is dropped. (MF NAV follows the NSE holiday calendar, so this aligns with the existing index stale-day logic.)
3. **Two-source NAV cross-check.** For a sample of schemes per refresh, compare mfapi's NAV on a given date vs AMFI's `DownloadNAVHistoryReport` value (and vs the AMC factsheet month-end). Tolerance ~0.01%. Any mismatch beyond tolerance → flag the scheme, prefer the AMFI value. **This is the check that lets us trust the convenient mirror without depending on it.**
4. **% -to-NAV sums to ~100.** For each monthly portfolio, Σ`pct_to_nav` (equity + debt + cash/others) must be 99–101%. Outside that → flag the file as a parse error (almost always a missed column or a units/cash row), don't ingest.
5. **ISIN resolution rate.** Report the fraction of holdings (by weight) we mapped ISIN→symbol. Below a threshold (say <95% by weight) → flag; unresolved names are usually unlisted/AT1/foreign/debt and must be **labelled "unresolved," not dropped from the denominator** (otherwise weights and active-share are wrong).
6. **Month-over-month portfolio diff sanity.** Compute turnover proxy = ½·Σ|wₜ − wₜ₋₁| over holdings. An implausible jump (e.g. >60%/month for a large-cap equity fund) signals a parse mismatch (wrong month, units vs % confusion) — flag for review before it pollutes the churn chart.
7. **Survivorship handling.** Keep merged/closed schemes in `scheme_master.json` with a `status` and an `effective_end`/`successor_code` field. Never silently drop a wound-up scheme from history — that would bias any peer or backtest analysis upward. Mark it; let the picker hide it by default but keep the NAV/holdings retrievable.
8. **Benchmark sanity.** Where a factsheet benchmark is parsed (Phase 2), flag any disagreement with the category-default map rather than overwriting blindly.

---

## 4. MVP-1 SCOPE

**(a) Scheme NAV in the Prices/Performance view.** A mutual-fund scheme becomes a selectable series in the existing picker (new "Mutual Funds" group, sub-grouped by SEBI category). Because NAV is already a total-return level, it flows straight through the **existing** rebase/CAGR/alpha/beta/rolling/capture engine — pick a scheme + its benchmark index (or a stock) over any t1→t2, read rebased TR and comparative stats. **Zero new analytics; reuses the parity-clean engine and the fair common-overlap window** (so a 2015-inception fund vs NIFTY 500 TRI is compared on the common window, per the existing convention). This alone is high value and low risk.

**(b) NEW "Funds" portfolio-analytics tab.** Inputs are the cached monthly portfolios + reused index constituents + reused per-stock fundamentals. Panels:

- **Holdings table** — security, industry, %-to-NAV, resolved NSE symbol, market value (reused table component).
- **Asset + sector allocation** — equity/debt/cash and sector pie/bar (reused Plotly bar/pie).
- **Top-10 concentration** — sum of top-10 weights + a Herfindahl concentration number over time (reused bar + a small new time series).
- **Active share & overlap vs benchmark** — *genuinely new.* **Active share** = ½·Σ|w_fund(i) − w_bench(i)| across the union of holdings — plain English: "what % of the fund is *different* from its index" (0% = closet index-hugger, 100% = totally different). **Overlap** = Σ min(w_fund(i), w_bench(i)). Both need the NSE constituent weights (section 1).
- **Month-on-month churn (turnover proxy)** — *new* — the ½·Σ|Δw| series from QA-check 6, a cheap proxy for trading activity.
- **LOOK-THROUGH fundamentals** — *the standout new feature.* Aggregate the portfolio's **weighted** quality/valuation by joining each holding to our per-stock fundamentals: portfolio weighted P/E, P/B, ROE, debt/equity, earnings growth = Σ wᵢ·metricᵢ over resolved equity holdings. Plain English: "if this fund were one giant stock, what would its P/E and ROE be?" Cross-checked against the factsheet's stated weighted P/E (QA). Reuses `fundamentals.py`; the aggregation is new.
- **Category-peer benchmark** — rank the scheme vs same-category peers on the NAV-derived stats (CAGR/Sharpe/MaxDD from the existing engine).

**Genuinely-new charts/components** (everything else is reused): active-share/overlap-vs-benchmark, month-on-month churn series, concentration-over-time, and the look-through weighted-fundamentals panel. The holdings table, allocation pies, NAV performance, and all return/risk math are reused.

---

## 5. PHASED BUILD ORDER (effort, risk, what's deferred)

- **Phase 0 — ISIN map + scheme master (~0.5 day, low risk).** Build `isin_to_nse.json` from the already-fetched `EQUITY_L.csv`; build `scheme_master.json` from mfapi `/mf` + category. Pure reuse. *Risk:* minimal.
- **Phase 1 — NAV in Prices view (~2 days, low risk). SHIP FIRST.** `funds_nav.py` (`build_fresh` + snapshot + `load/available/coverage`), picker integration, two-source NAV cross-check. Delivers immediate value through the existing engine. *Risk:* mfapi rate/availability → mitigated by AMFI fallback + cached snapshot. **Defer:** fetching all 42k schemes — start with a curated **top-N-by-AUM equity universe** (covers most retail assets); expand later.
- **Phase 2 — Funds tab on holdings, 5–8 major AMCs (~1.5–2 weeks, medium-high risk).** `funds_portfolio.py` with per-AMC adapters (start Nippon + HDFC — best-organised XLSX), the QA pipeline, then the Funds tab with allocation/concentration/active-share/overlap/churn. *Risk:* **per-AMC format drift** (isolate in adapters), **JS-gated portals** (AdvisorKhoj fallback), **historical depth** (own sites 6–18 mo; backfill from AdvisorKhoj where present). **Defer:** debt-scheme fields (coupon/YTM/rating/duration) — placeholder columns; heavy factsheet-PDF stat extraction.
- **Phase 3 — Look-through fundamentals + category-peer ranking (~3–4 days, medium risk).** `funds_lookthrough.py` joining holdings → fundamentals + stock panel. *Risk:* fundamentals coverage gaps on smaller holdings → report coverage %, never silently renormalise. **Defer:** full-portfolio reconstruction from annual/semi-annual reports (only needed if monthly disclosures prove incomplete).
- **Cross-cutting:** publish through the existing one-double-click flow (extend `publish_*` / add a `Pull MF Data.bat`), keep the Funds tab data embedded as `window.VISTAS_FUNDS` (no JS-parity port), and gate publish on the QA flags (a flagged-fail scheme/month never goes live).

**Labelled placeholders to defer cleanly:** debt-fund analytics, full historical (>5y) holdings backfill, per-scheme factsheet-parsed benchmark/stats, the full 42k-scheme NAV universe, and direct-AMC PDF stat extraction.

---

## 6. OPEN DECISIONS FOR KV (only the few that genuinely need your call)

1. **NAV universe size for Phase 1:** curated **top-N-by-AUM active equity schemes** (recommended — fast, covers most assets, clean) vs all ~42k (heavy, mostly noise/closed)? Recommend top-N, expandable.
2. **AMC coverage order for holdings:** propose **Nippon + HDFC first** (best-organised XLSX), then ICICI/SBI/Axis/Kotak/Aditya Birla. Confirm this priority matches the funds you actually care about.
3. **mfapi vs AMFI as the day-to-day NAV pipe:** recommend mfapi as primary (convenient) with AMFI as the authoritative cross-check/fallback. If you'd rather pull AMFI directly as primary for purity (slower, one fewer middleman), say so — the architecture supports either with a one-line switch.
4. **Holdings historical depth target (REVISED per KV 2026-06-22):** KV is right that the data is mandated and
   published, so depth is an acquisition-effort question, not an availability one. The real boundaries are:
   (a) **monthly** granularity is only mandated since ~Oct-2018 (half-yearly before — from annual/semi-annual
   reports); (b) AMC websites retain only the latest few months. So the **acquisition tiers** are: latest-6-18mo
   (AMC sites, trivial) → **~3–5 yr (DEFAULT: AdvisorKhoj/aggregator archives + AMC multi-year pages)** → deep
   ~2018→now (per-AMC archives + **web.archive.org Wayback snapshots** of old disclosure pages) → pre-2018
   half-yearly (parse annual reports). **New default = build to the full monthly era (~2018→now) for the schemes
   KV tracks, with the same per-file QA**; 6–18mo is only the throwaway first cut, not the ceiling.
5. **Direct/Regular plan convention:** schemes have Direct and Regular variants (different expense ratios → different NAV). Recommend standardising on **Direct-Growth** as the canonical series (cleanest, no distributor fee drag) and exposing Regular only on request. Confirm.

---

### Default decisions taken (autonomy; reversible)
Per KV's "make your best decisions, go" — for Phase 1 I will proceed with: **top-N-by-AUM active equity universe**, **mfapi primary + AMFI cross-check/fallback**, **Direct-Growth canonical**, **Nippon+HDFC first for holdings**, **shallow-now historical depth**. Any of these flips with a one-line change if KV prefers otherwise.

---

## BUILD LOG — Phase 0+1 (2026-06-22, autonomous)

**Status: BUILT + VERIFIED; integrated into the Prices view; deck rebuild in flight → publish.**

- **Universe decision refined (better than "top-N by AUM"):** AMFI `NAVAll.txt` groups every scheme
  under SEBI category headers in ONE ~5 MB download, so instead of an AUM cutoff I can't compute from free
  data, I take the **complete open-ended ACTIVE-EQUITY Direct-Growth set = 566 schemes** (Flexi 45, ELSS 41,
  Large 36, Mid 33, Multi 33, Focused 28, Large&Mid 34, Contra 3, + Small/Value/Sectoral/Dividend-Yield). Clean,
  complete, reproducible. AUM-ranking deferred (needs AMFI monthly-AUM parse).
- **`vistas/funds_nav.py` (new, no parity port):** `fetch_amfi_master()` (categorized master + free latest NAV),
  `fetch_nav_history()` (mfapi `/mf/{code}`), `build_snapshot()` (per-scheme cache `data/funds/nav/<code>.json`
  + wide `data/funds/MF NAV till <date>.csv` + `scheme_master.json`, fresh-wins merge), `load/load_named/
  available/coverage/names/categories`, `build_isin_map()` (from in-repo `stock_security_master.json`).
- **Verified:** snapshot = **566 schemes × 5017 days, 2006-04-03 → 2026-06-19**. **Two-source NAV cross-check
  (mfapi-latest vs AMFI-latest) = 566 checked, 0 mismatch** (≤0.1% tol) — strong "no score for error" pass.
  ISIN→symbol map = 6633 ISINs (e.g. INE002A01018→RELIANCE), ready for Phase-2 holdings look-through.
- **Integration (mirrors `world`, low-risk):** `catalog.py` adds a "Mutual Funds · <category>" group (566 items);
  `deck.py` writes per-fund `data/funds_nav/<name>.json` + `LAZY.funds`; `vistas.js` adds `VISTAS_FUNDS` to the
  `mergeLevel` resolver + `ensureFundsLoaded()` + the Prices-view prefetch — so a scheme NAV charts like-for-like
  through the EXISTING parity-clean engine (no new analytics). Runtime test gains a fund-selection exercise.
- **Next:** deck rebuild → runtime test (fund must resolve into the GP) → publish. Then Phase 2 (holdings/Funds tab).
