# Vistas × Dashboard × StarMine — Integration, Audit & Identifier-Matching Plan

> Produced by the `vistas-dashboard-starmine-recon` workflow (2026-06-22). Decision record for
> bringing KV's external Bloomberg dashboard (breadth/cycle) + LSEG StarMine Smart Estimates into
> the terminal. **Measured facts are verified by opening the real files; inferred items are flagged.**
>
> **UPDATE 2026-06-22:** ARM whitepaper re-read complete → §4 "correct-usage" detail is now confirmed
> (point-in-time, region-relative 0–100 revision-momentum percentile, ~1-month horizon, mean-reverts,
> combine-with-valuation, rebalance monthly; high|PS|≥2% predicts surprise sign ~66%, ~74% when
> corroborated by consensus revisions). Identifier bridge BUILT (`vistas_gated/idmap.py`) and the 100%
> match **independently re-verified by the idmap code itself: 472/472 unique ARM ISINs → NSE symbol,
> 0 unmatched** (the "500" earlier was nominal NSE500 membership; the extract carries 472 securities).
> The dashboard chart-inventory reader still needs a re-run — §3's exact chart list stays provisional.

## ★ 0. THE GOVERNING RULE — licensed data must NEVER hit the public deck
The public GitHub-Pages terminal may plot **only data we sourced from public endpoints**. Anything from
**Bloomberg** (price/volume/market-cap) or **LSEG/Refinitiv StarMine** (ARM/Smart Estimates) is **paid,
proprietary, third-party IP** and may live **only** in a local or password-gated build, used to **audit**
our data — never embedded in a published deck. **Why it's dangerous:** `deck.py` INLINES the whole dataset
into `index.html` as plaintext `window.VISTAS_*={...}`; publishing it ships the licensed data to the world,
where it is scraped/cached/indexed/forked **irreversibly**. The gate must be a **build-time data separation**,
not a UI toggle.

**Architecture = two trees, one codebase:**
- `vistas/` (today) stays 100% public-sourced → `deck.py` → `output/` → Pages. Unchanged.
- **NEW `vistas_gated/`** (git-ignored / private repo): `idmap.py`, `audit.py`, `starmine.py`, Bloomberg loaders.
  Runs only locally (`python app.py` with `VISTAS_GATED=1`) or on a private auth host. The Fundamentals tab reads
  StarMine fields **at runtime from the local Flask app**, never from the deck.
- **Safety net:** add a build-time assertion in `deck.py` — fail the build if any embed key starts with
  `STARMINE_`/`BBG_`/`GATED_`. Makes an accidental publish impossible, not just unlikely.

| Data | Source | Class |
|---|---|---|
| NSE index TR, per-stock adjusted TR/PX, bhav OHLCV/turnover/breadth, Screener fundamentals, India macro, World PX | our own public fetch | **Public-OK** (publishes) |
| Bloomberg market-cap / price / volume series | Bloomberg | **GATED — audit only** |
| StarMine ARM / Smart Estimates | LSEG (paid) | **GATED — hard, never publishes** |
| ISIN ↔ name/symbol crosswalk (the *identifiers*, not the paid field values) | public ISIN registry | Public-OK |

## ★ 1. IDENTIFIER MATCHING — solved & measured (ISIN is the key, NOT Bloomberg ticker)
External data names stocks by **ISIN** (`INE002A01018`) + **Bloomberg ticker** (`RELIANCE IN Equity`); we use
**NSE symbol** (`RELIANCE`). Measured on the ARM 500-name universe:
- **ISIN → our NSE symbol = 500/500 = 100.0%** (via `stock_security_master.json` `master[*].isins[]` lineage — the
  whole lineage list, so renamed/old ISINs resolve for free). **Use this.**
- **Bloomberg short-ticker == NSE symbol = only 201/500 = 40.2%** — Bloomberg house abbreviations differ
  (`ADSEZ`→ADANIPORTS, `ACEM`→AMBUJACEM, `ABCAP`→ABCAPITAL…). **Never join on the ticker** (silently mis-maps ~60%).

Identifier columns found (verbatim): ARM CSV → `ISIN, SECCODE, CMPNAME, SECID`; NSE500 members xlsx →
`Ticker (" IN Equity" suffix), market Cap, Company Name, ISIN Code`; Bloomberg mcap CSV → wide, columns = BBG
tickers, rows = dates. Bridge (`vistas_gated/idmap.py`): normalize ISIN → explode our master's `isins[]` →
NSE symbol. Filter external ISINs to `INE`/`IN9` (drops ADRs). Misses → fuzzy name-match (token-set ≥92) **staged
for human confirm**, never auto-accepted (fires 0× today). Bloomberg ticker = display label only.

## ★ 2. CROSS-VERIFICATION AUDIT — the gate ("is our data good enough?")
Ground truth = the Bloomberg **cleaned price/cap panels** (`cap_df_cleaned.csv`, `price_df_cleaned.csv`) in the
local Dashboard dir, default `…\Projects\Dashboard\2026\4. April 13, 2026\` (env-overridable via `VISTAS_BBG_DIR`;
LICENSED → read-only, gated, never published — NOT in the Vistas repo). Harness `vistas_gated/audit.py`, on ISIN:

| Field | Our source | Metric | PASS |
|---|---|---|---|
| Close (level) | our **unadjusted** close vs BBG | per-name median abs %diff | <0.5% (95th pct <2%) |
| Daily return | our close/prev−1 vs BBG-implied | Spearman ρ over overlap | ≥0.98 |
| Market cap | our close×shares vs BBG mcap | cross-sectional rank ρ + median abs %diff | rank ρ ≥0.99, %diff <3% |
| Coverage | — | % of Top-1000 we can price | ≥97% |

**Gotchas to control (will cause false fails):** (1) **adjusted vs unadjusted** — compare our UNADJUSTED PX to BBG
(or compare returns, adjustment-invariant); (2) **TR vs price** — never audit on our total-return line (drifts up by
dividend yield); (3) **free-float vs full mcap** — BBG is full; a level mismatch that survives the rank test is
cosmetic (still GREEN for breadth/cycle, which is rank/threshold-based); (4) INR=INR, assert no FX; (5) restrict to
the common-alive overlap (survivorship). **Gate:** all GREEN → plot the dashboard from our feed. Returns-ρ fails →
RED, fix the price pipeline first.

## ★ 3. DASHBOARD INCORPORATION — extend `bhav_derived.py`, plot from OUR data (audit-gated)
`bhav_derived.breadth()` already has advances/declines, A/D line, `pct_above_50dma`/`pct_above_200dma`,
`new_high_52w`/`new_low_52w`, `net_new_high_pct`. Add a **"Market Breadth / Cycle"** sub-area. Prioritise the
**differentiated** ("where others aren't looking") charts, gated behind the audit (esp. anything cap-weighted):
1. **Breadth thrust (Zweig)** — 10-day EMA of advancers/(adv+dec) crossing <0.40→>0.615 in ≤10d (rare regime signal).
2. **% above 200-DMA as a cycle oscillator** with historical regime bands (early/mid/late cycle).
3. **Net new-highs vs new-lows divergence vs the index** (index up while internals roll over = hidden rot).
4. **Cap-cohort breadth divergence** (mega/large/mid/small % above 200-DMA — needs the mcap audit GREEN).
Standard/table-stakes (already have the data, no audit dependency): A/D line, %-up, advance-decline ratio,
turnover thrust, Parkinson/Garman-Klass realized-vol regime. Build standard first, differentiated after the audit.

## ★ 4. SMART ESTIMATES (StarMine ARM) → Fundamentals tab (GATED, runtime-only)
**ARM = Analyst Revision Model**: a 0–100 score, high = analysts RAISING estimates (positive revision momentum),
low = cutting. A **short-horizon momentum/timing** signal (the revision-drift effect), NOT a valuation verdict —
label it so. Data on hand: `ARM scores extracted April 6, 2026.csv` = 458,359 rows, **point-in-time DAILY series**,
472 securities, 2024-07-01→2026-03-25, long-form (`MNEMONIC/ITEM/ITEMNAME/VALUE_`). 7 components per ISIN per day:

| ITEM | MNEMONIC | meaning |
|---|---|---|
| 44 | `ARM_100_REG` | **headline Analyst Revisions Score (regional 0–100)** |
| 45 | `ARM_100_GLOBAL` | global rank |
| 46 | `ARM_PREF_EARN_COMP_100` | preferred-earnings component |
| 47 | `ARM_REC_COMP_100` | recommendations component |
| 48 | `ARM_REVENUE_COMP_100` | revenue component |
| 49 | `ARM_5_REG` | coarse 1–5 bucket |
| 50 | `ARM_SEC_EARN_COMP_100` | secondary-earnings component |

**Surface per stock (gated build only):** headline ARM (item 44) + 0–100 bar; component breakdown (46/47/48/50)
as a small bar group (shows WHAT drives the revision); ARM **time-series sparkline** (direction matters more than
level — rising vs falling); regional/global rank. **Correct-usage notes on the panel:** point-in-time, short-horizon
revision signal → a timing overlay, not a buy-and-hold thesis; high ARM + cheap valuation is the constructive combo;
ARM mean-reverts, so falling-from-high is a warning even when still high. **Predicted Surprise / SmartEstimate-vs-
consensus** need a SECOND extraction (`Sm2DEqAp`/`Sm2Item`) — current CSV is ARM-only.

## ★ 5. PHASED PLAN
- **A — Bridge + gate scaffolding: ✅ DONE (2026-06-22).** `vistas_gated/` (git-ignored, in `.gitignore`)
  with `__init__.py` + `idmap.py` (ISIN→symbol, **472/472 = 100% verified**, valid-ISIN filter drops 2,526
  `SYM:` placeholders, bidirectional). `deck.py` carries `_assert_publishable` + `_GATED_MARKERS` (build-time
  hard stop on any StarMine/Bloomberg marker; raises before write/publish — tested: fires on planted marker,
  clean on current assets). **Still TODO in A:** the `VISTAS_GATED` flag wiring in `app.py` (deferred until a
  gated panel actually consumes it — Phase D).
- **B — Cross-verification audit:** `vistas_gated/audit.py` vs the Bloomberg mcap series → PASS/FAIL card. Decision gate.
- **C — Breadth/Cycle charts:** extend `bhav_derived.py` + Market-Internals tab; standard charts now, differentiated
  after B is GREEN. Publishes normally (our data).
- **D — StarMine ARM card:** `starmine.py` + the gated Fundamentals ARM card. Never publishes.

## ★ 6. OPEN DECISIONS FOR KV
1. **Deployment of the gated data** — local-only (simplest, zero leak; ARM is a static CSV) vs private auth host.
   *KV answered: private auth host eventually — but said "publish publicly for now."* **⚠ See the licensing rule §0:
   publishing licensed StarMine/Bloomberg data publicly is irreversible and breaches the LSEG/Bloomberg licence —
   strongly advise local-now / private-auth-soon instead, which gives full use + sharing with ZERO leak.**
2. **ARM refresh cadence** — recurring monthly pull vs periodic manual drop (recommend monthly manual drop until stable).
3. **Which models beyond ARM** — pull Predicted Surprise + SmartEstimate-vs-consensus next (highest marginal value);
   defer the full StarMine suite (Value, Quality, Price-Momentum) until the ARM card proves useful.
4. **Mcap basis — RESOLVED (KV, 2026-06-22):** Bloomberg `cap_df` = **FULL market cap** = "total current market
   value of a company's outstanding shares, in primary currency (INR)" — NOT free-float. So reconstruct mcap with
   **TOTAL issued shares** (not free-float); the level should match Bloomberg up to a fixed unit factor (Bloomberg
   appears to be in ₹-million, ours in ₹-crore → ~10×), and the gate is **rank ρ ≥ 0.99** (`audit.audit_mcap`).

## ★ 7. BUILD PROGRESS & FINDINGS (2026-06-22, autonomous run)
**Phase A — DONE & verified** (see §5). Guardrail + `idmap` (472/472 ARM ISINs → symbol, 100%).

**Phase B — audit BUILT & RUN → VERDICT: GREEN on price.** `vistas_gated/audit.py` compares our RAW NSE
bhavcopy close (`bhav_prices.load_ohlcv`) vs the April-13-2026 Bloomberg `price_df_cleaned.csv`, joined
ticker→ISIN (stacked `Members of NSE500*.xlsx`, 24 sheets)→symbol. Result on **547 names (99.6% coverage
of 549 resolved, of 994 BBG columns)**:
- **Daily-return Spearman ρ: median 0.9983** (gate ≥0.98) ✓. Test discriminates (worst ~0.90; 70/547 <0.98),
  so the high median is real, not degenerate.
- **Level |our/bbg−1| (trailing 60d): median 0.000%, p95 0.000%** ✓ — both series ARE the NSE official close,
  so identical to the paise. Mechanism explains the number.
- Outliers (~1%): ANGELONE 907%, IRB/ECLERX exactly 100%(=2×) → split/bonus-alignment or reused-ticker
  crosswalk artifacts on a handful of names; flagged, don't move the aggregate. **Conclusion: our PRICE feed
  is good enough to use as our own data stream.** Summary persisted to `vistas_gated/audit_summary.json`.

**CORRECTION to §3 — the dashboard is NOT an advance/decline breadth board.** The notebook inventory
(`Dashboard script.ipynb`, April-13) shows it is a **valuation + growth + cycle COHORT** board. Its real
charts: aggregate (cap-weighted) earnings-yield & book-yield; P/E·P/B·P/S **distributions by cap cohort**;
sales/PAT-growth **breadth** (% of universe over X% growth, TTM CAGR); median ROE; % firms with debt-growth>
networth-growth; **returns by cap cohort**; **fall-from-52w-high by cohort** (vs index = relative-weakness
divergence); **alpha breadth** (% beating index by >0/10/20/30%); **historical-price-percentile** (cycle/
exhaustion); **“too-much-gain” 2×/5×/10× in 5y**; **stocks accounting for N% of trading value** (liquidity
concentration). **~85% of these need MARKET CAP** (cohort selection + cap-weighting) + trailing quarterly
fundamentals (PAT/sales/networth/debt — which we already have per stock in the Screener bundles).

**THE MCAP UNLOCK (the one gap is dissolvable).** We have no shares-outstanding field, BUT Screener bundles
carry a **PE time series** and **EPS time series** and full **P&L (PAT)**. Since `PE = mcap/earnings`,
**mcap = PE × PAT_ttm**; and `shares = PAT/EPS`, so a mcap *time series* = `our_price_t × shares` with
`shares = (PE×PAT)/price` anchored from Screener. **No shares/face-value scrape needed for a first cut.**
**KEYSTONE PROTOTYPE RUN (2026-06-22): mcap reconstruction WORKS but is just under the gate.** On **504
names** (our raw price × shares, shares=NetProfit/EPS from Screener annual P&L) vs Bloomberg `cap_df` latest:
**Spearman rank ρ = 0.969** (gate ≥0.99). Level sanity: our RELIANCE = ₹21.4 lakh cr (correct); Bloomberg
cap_df is in **₹-million** units (rank-irrelevant). The ~3% rank noise + ~1.2× level bias trace to the
**EPS-implied** share count (Screener's "Net Profit" row ≠ exactly the profit behind "EPS in Rs" — diluted/
exceptional-item gaps). **NOT GREEN yet — do NOT plot the cohort dashboard from our data on ρ=0.969.** FIX
(small, well-defined): use the ACTUAL share count — balance-sheet **Equity Capital ÷ face value** (a true
quarterly shares *series*, also fixes share-count drift), or scrape Screener's displayed **Market Cap** field
directly. Re-run the keystone → expect ρ≥0.99 → then the cohort dashboard is reproducible from OUR data and
publishes (our data, no Bloomberg). Verify before scaling.

**★ KV DIRECTIVE (2026-06-22) — NO ESTIMATION.** Do NOT derive mcap from PE×PAT or shares=PAT/EPS or any
fundamentals ratio — fundamentals carry a score-of-error that magnifies in the mcap (that is exactly why the
keystone only hit ρ=0.969). Instead **COLLECT actual market-cap / shares-outstanding from a reliable PUBLIC
source (NSE / bhavcopy / BSE)**. mcap = our GREEN price × real issued shares (shares change only on corporate
actions, so a periodic refresh suffices). Workflow `nse-mcap-source-hunt` (w4cv08unq) is ranking the candidate
authoritative sources (NSE quote-API issuedSize, NSE/BSE bulk securities/market-cap files, index free-float,
local Screener shareholding). Reuse `vistas/fetch.py` NSE cookie/session handshake. Then re-run the keystone
(rank ρ vs Bloomberg cap_df ≥0.99) on COLLECTED shares before declaring the cohort dashboard reproducible.

**★ RESULT — mcap layer BUILT (`vistas/shares.py`), and COLLECTED mcap clears the bar (2026-06-22).**
Source hunt = workflow `nse-mcap-source-hunt`. Two collectors, NO fundamentals anywhere:
- **NSE `securityInfo.issuedSize`** (PRIMARY, exact): raw integer issued-share count, point-in-time, ISIN-
  tagged, from `/api/quote-equity`. Built + hardened (Sec-Fetch/sec-ch-ua client-hints + double page-warm for
  Akamai). **Blocked from THIS sandbox's datacenter IP (403 on /api/), but runs in KV's normal runtime** (his
  app already fetches NSE fine). mcap = our GREEN close × issuedSize → exact point-in-time mcap.
- **AMFI half-yearly bulk XLSX** (no-WAF, validated): the one bulk official file — published FULL mcap + SEBI
  Large/Mid/Small label per company, ISIN+NSE-symbol keyed, plain GET. `build_from_amfi()` collected **2,238
  NSE names** (100 Large / 148 Mid / 1,990 Small) → `data/_shares/amfi_mcap.json`.

**VERIFICATION (`audit.audit_mcap`, rank ρ vs Bloomberg cap):**
- AMFI published mcap vs Bloomberg, TIME-ALIGNED (AMFI 31Dec2025 vs Dec-2025 Bloomberg): **ρ = 0.9947 → GREEN.**
  (vs the mis-aligned Apr-2026 snapshot it was 0.9775 — pure time-gap artifact: 6-mo-avg vs 4-mo-later point.)
  → **Two independent reputable sources agree to ρ≈0.995 ⇒ real collected mcap reproduces Bloomberg's size
  ranking.** The cohort dashboard IS reproducible from collected data.
- our price × AMFI-DERIVED shares (shares = avg_mcap/avg_price over the same 6-mo window) vs Bloomberg: **ρ =
  0.988** — just under 0.99; residual = the AMFI 6-mo-average ÷ price approximation (worst names = volatile
  mid-caps TATAINVEST/NUVAMA/CAMS). EXACT `issuedSize` removes this approximation → expect ≥0.99.
**Net:** for cohort labels + point-in-time mcap, use AMFI directly (ρ=0.995, GREEN now). For a DAILY mcap
series, mcap = our GREEN close × shares: issuedSize where available (exact, KV runtime), AMFI-derived as the
always-available fallback (ρ=0.988, ample for coarse large/mid/small cohorts). No fundamentals in the chain.

**Already LIVE (unrelated):** the per-chart-copy + snapshot fixes finished publishing (task bcshkz1kn, exit 0).

## Key paths
- ISIN↔symbol master (the bridge): `Vistas/data/stock_security_master.json` (4,530 cos, 6,633 ISINs, 100% ARM match)
- Audit harness: `vistas_gated/audit.py` (GREEN); BBG ground truth dir via env `VISTAS_BBG_DIR`
  (default = Dashboard `4. April 13, 2026`); crosswalk via `VISTAS_MEMBERS_GLOB`.
- Dashboard notebook (chart source of truth): `…\Dashboard\2026\4. April 13, 2026\Dashboard script.ipynb`
- ARM scores (gated): `…\ABSL Quant\ABQ Datewise\3. RQA Estimates\2026\Data on April 6, 2026\ARM scores extracted April 6, 2026.csv`
- ARM↔ISIN crosswalk: `…\Data on April 6, 2026\Members of NSE500 at April 6, 2026.xlsx`
- Bloomberg mcap ground truth: `cap_df_cleaned.csv` in the Dashboard dir (env `VISTAS_BBG_DIR`; licensed, gated, never published)
- Breadth engine to extend: `Vistas/vistas/bhav_derived.py`; public deck builder to keep clean: `Vistas/vistas/deck.py`
