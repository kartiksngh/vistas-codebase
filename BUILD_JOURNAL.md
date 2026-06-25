# Vistas Terminal — Build Journal (lab notebook)

> **Purpose.** The compounding memory of *how the terminal is being built* — experiments, trials,
> mistakes, root-causes and learnings — so we **evolve in spirals, never loop**. Each failure must
> make the framework stronger and is recorded here so we never re-derive it. Structured so a future
> **auto-researcher** (or future-me after a cold start) can read §1–§2 to know exactly where we are,
> §3 to know what's been tried and what broke, and §4 for the laws that must never be relearned.
>
> **Companion docs:** `MEMORY.md` (one-paragraph resume point) · `pipeline/README.md` (refresh-engine
> architecture) · `pipeline/NSE_DATAPULL_PLAYBOOK.md` (NSE-specific antifragile framework) ·
> `FUND_MANAGER_ANALYSER_DESIGN.md` (attribution design) · `CLAUDE.md` (conventions + parity rule).
>
> **Discipline:** append to §3 every session (dated, honest, with the experiment that settled it);
> refresh §1–§2 as state changes; promote any cross-project learning to a global skill.

---

## 1. Build status (LIVING — update each session)

**Product:** self-hosted Bloomberg for Indian markets. Hosted lazy-load site → GitHub Pages.
Live: <https://kartiksngh.github.io/vistas/terminal/>. Dev: Flask `app.py` (127.0.0.1:8753).

**Tabs (live unless noted):**
| Tab | Source | State | Notes |
|-----|--------|-------|-------|
| Prices | NSE TR indices (`fetch.py`) | LIVE | ✅ refreshed to **Jun 23** (2026-06-24) — root-caused the stall as an NSE-WAF rate-limit on KV's residential IP from retry-storms; cure = **curl_cffi Chrome fingerprint** + canary fast-fail + no retry-storm. Pulled all 131 indices, 0 failures. |
| Fundamentals | Screener + StarMine ARM | LIVE | per-company lazy JSON |
| Macro | MOSPI/FBIL/BIS/NSE (`macro.py`) | LIVE | India-first + global |
| Quant & MI | `stock_intel.py` | LIVE | per-stock cockpit |
| Funds (holdings) | AMC monthly XLSX (`funds_portfolio.py`) | LIVE | ⚠ raw line-item dump — categorize-by-sector in progress (#45) |
| Fund Skill | 13-yr holdings × TR (`funds_attribution.py` + `funds_portfolio_viz.py`) | LIVE | skill verdict + PORTFOLIO section (sector donut, 13-yr rotation, categorized book, concentration) + ★ **window-adaptive** (any start→end span recomputes EVERY metric+verdict in-browser from baked `ts[]`) + ★ **portfolio-level batting/slug** (stock cross-section, KV's MoneyBall) alongside NAV-level + ★ **peer-envelope vantage plots** (per-category min/p25/p50/p75/max across funds, fund line inside, category dropdown, NAV/Portfolio toggle) |

**★ DAILY DATA REFRESH (2026-06-24) — adaptive agent + deterministic backstop:**
- **Cadence-aware pipeline** (`pipeline.py`): prices **daily** (NSE-TR indices · stock TR from bhavcopy + breadth ·
  Yahoo close · fund NAVs · world), fundamentals/macro/mcap **weekly**, holdings/issued-shares **monthly**.
  One nightly job; each feed runs only when due (`_cadence_state.json`); `--all` forces a full refresh.
- **NSE pull cure:** curl_cffi Chrome fingerprint + canary fast-fail + dead-column prune → beats the WAF
  rate-limit on the residential IP (see playbook F8). Prices verified fresh to **Jun 23**.
- **The agent** (`pipeline/DAILY_REFRESH_AGENT.md` SOP + `Daily Refresh Agent.bat`): headless Claude, **Supervised**
  — runs the pipeline, diagnoses+repairs degraded feeds with DATA actions (wait-out-cooldown-once/retry/re-pace),
  publishes ONLY validated data, logs to `agent_journal.md`, and FLAGS (never makes) code changes to
  `NEEDS_REVIEW.md`. The adaptive layer over the deterministic core — because NSE failures are probabilistic.
  *Scheduling is KV's (self-scheduled): auto-scheduling an unattended skip-permissions agent was correctly
  blocked by the safety gate.*
- **The watchdog** (`pipeline/watchdog.py` + `.bat`, scheduled 10:30pm): DETERMINISTIC backstop — if prices go
  stale on a trading day OR the agent stops running (>26h), it writes `WATCHDOG_ALERT.txt` + a Windows pop-up.
  Independent of the agent, so a silently-dead agent can't go unnoticed.

**Data stores:** `holdings_history.parquet` (3.52M rows, 196 monthly snapshots Apr-2013→Oct-2025);
`tr_returns_monthly.parquet`; `bbg_identity_bridge.csv`; `stock_industry.json` (753 names);
`scheme_master.json` + `nav/*.json` (566 Direct-plan NAVs); `funds_attribution/*.json` (767 schemes).

**Verification gates (must pass before publish):** ① JS↔Python parity (`_parity_check.js`) for the
offline deck; ② VM-stub runtime test (`_deck_runtime_test.js`) — *necessary, NOT sufficient*;
③ **real-browser puppeteer probe** (`_pup_*.js`) — the true gate; ④ leak-check (no gated data).

## 2. Gaps & next (LIVING — prioritized)

**DONE & LIVE (2026-06-24):** bedrock dedup (#43) · clean pickers (#46) · batting metrics (#47) ·
categorized Funds tab (#45) · NSE TR-only (#44 connection) · ★ Fund-Skill **window-adaptive** scorecard +
date controls (#50) · ★ **portfolio-level batting/slug** (MoneyBall stock cross-section) + **peer-envelope
vantage plots** (per-category min/p25/p50/p75/max, category dropdown, NAV/Portfolio toggle, fund line inside
band — verified 532/532 inside) (#51).
**KNOWN FRICTION:** `save_terminal_site` recomputes all 2365 fundamentals+quant every rebuild (~18 min wall)
even for a JS-only change → add content-hash skip / `--only` flag (#52).

**NEXT (prioritized):**
1. **★ AMFI scheme-identity spine** (#48) — build the return-fingerprint (TE) + ISIN matcher → >99%
   navindia_code→AMFI. Unlocks the **NAV return-gap** (#40). The cross-project keystone.
2. **Charting upgrade** (#49) — rebase-to-visible-window + date controls on all NAV/price charts.
3. **Remaining audit fixes** — return-join drops (16 vst_ids incl. Tata Motors-DVR), cross-company
   vst_id contamination (Genus, Kirloskar), ISIN↔vst_id 1:1 invariant check.
4. **Prune dead/renamed index columns** from the TR snapshot (the slow-pull tail) → fast full refresh + fresh prices.
5. **Funds coverage → all 55 AMCs** (#33) · **NSE issuedSize mcap** (#28, KV's runtime).
6. **Future MoneyBall phases:** India factor lib (FFC-α + active share) · PIT bench weights/TER · manager-tenure DB + team construction.

## 3. Experiment / trial / mistake log (APPEND-ONLY — dated)

> Format per entry: **context → experiment → observation → decision; MISTAKE (if any) + root-cause; LEARNING.**

- **2026-06-24 · Funds tab showed OBJECTIVE/DISCLAIMER text as scheme names (147/1298) — caught by KV, not
  my audit.** *Symptom:* the Funds-tab scheme picker listed "(An open ended fund replicating the BSE…)",
  "*Investors should consult…", "• Safety and liquidity…" as schemes, WITH real holdings. *Root cause:*
  `funds_portfolio.py::parse_sheet` scores name candidates by "contains fund/index + word-count", so a
  verbose OBJECTIVE sentence out-scores the short real name. *Why the bedrock audit missed it:* that audit
  scoped the **Fund-Skill** path (funds_attribution / navindia_code / holdings parquet) — NOT the Funds-tab
  **XLSX name parser** (funds_portfolio). Different pipeline. **LEARNING: "the audit" must name its SCOPE;
  a clean identity-layer audit says nothing about the display parser.** *Fix (adversarial workflow, 10
  agents — classify 1298 → fix-spec → VERIFY):* the verify caught the first fix would WRONGLY DROP 14 real
  schemes (parenthetical objective "(An open ended…)" the strip missed) + KEEP 80 ("PORTFOLIO STATEMENT OF
  <name> AS ON <date>" wrappers). Corrected: un-wrap statements + strip paren-led objective + recover real
  head + drop-guard only when unrecoverable. Verified on all 1298: **0 false-drops, 31 dropped, 101
  recovered.** **META-LEARNING: the adversarial VERIFY phase is what made the workflow worth it — the
  first-pass fix-spec was unsafe; a single-path fix would have silently deleted 14 real funds.**
- **2026-06-24 · Funds tab vs Fund Skill are NOT one dataset (they must be) — KV's architecture call.**
  *Observation:* Fund Skill stops at Oct-2025; Funds tab shows only May-2026. *Root cause:* TWO sources —
  Funds tab parses the **live AMC XLSX** (current month only → May-2026); Fund Skill reads the consolidated
  **`holdings_history.parquet`** (Capitaline, ends Oct-2025). No reconciliation. *KV's intent:* ONE store
  (historical backbone + live monthly appended) feeding BOTH tabs. *Data FOUND:* `…/Consolidated reverse
  Dumps/…/Cline portfolios July'25 to May'26/MF Data - {Jul25..May26}.xlsx` — 11 Capitaline monthlies,
  EXACT schema that built the store (NAVIndia Code/Co_Code/ISIN/pct/Sebi Category/date) → direct append via
  the existing Co_Code→vst_id master. **PLAN (task #53):** append Nov25→May26 to holdings_history (reconcile
  Oct25 overlap) → re-run attribution (tr_returns already to Jun26) → Fund Skill to May-2026 → point the
  Funds tab at the same store (full-history slider). **LEARNING: Capitaline "MF Data" dumps carry the
  identity keys natively (Co_Code/navindia_code) — they're the clean backbone; only the live AMC-XLSX path
  needs name→navindia_code bridging.**

- **2026-06-24 · Fund Skill: PORTFOLIO-LEVEL batting/slug + PEER-ENVELOPE vantage (reused KV's MoneyBall
  defs, didn't reinvent).** *KV's correction on the first cut:* (1) the percentile band must be a PEER
  envelope — the cross-section ACROSS funds in the same category (min→max shaded + 25/75 dotted), not the
  fund's own history (a single number means nothing until you know the field); fixed per (category,metric),
  independent of the selected scheme; category dropdown + separate batting/slug plots — exactly his FFT
  strategy-vs-random-portfolio envelope. (2) I'd computed batting/slug only on the NAV (aggregated fund
  return); he also built them at the PORTFOLIO (stock cross-section) level in MoneyBall and wanted BOTH.
  *Recon (Explore agent over his notebooks, task #34 "build on, don't reinvent"):* found the exact formulas
  in `MoneyBall/Jan 2025/Moneball 2025 -Vantage Point.ipynb` (Cell 22) + `.../June 2025/...March 2025.ipynb`
  (Cell 27). **Portfolio HIT RATE** = per holding alpha_i=r_i−r_bench; count = share with alpha≥0; AUM =
  Σwᵢ·1[alpha≥0]; allocation-benefit = AUM−count. **Portfolio SLUG** = top/bottom QUARTILE (his int(0.25·N),
  labelled "20%") of the FULL tradeable universe by that month's return; slug = %top − %bottom (count & AUM).
  **Envelope** = groupby(['Category',date]).quantile({.25,.5,.75,1.0}) across funds + ~3-month rolling-mean
  smooth. *Build:* `load_panel` now also aggregates per scheme-month from the stock-level join (hit cnt/aum,
  slug cnt/aum); baked into `ts` (hc/ha/sc/sa2); `build_envelopes` writes one `_envelopes.json` (0.54 MB, 20
  categories × 4 metrics: port_hit_aum & port_slug_aum 3m-smoothed, nav_bat & nav_slug 36m-rolling). JS
  lazy-loads it; the vantage panel draws shaded min–max + dotted 25/75 + median + the fund's own line.
  *Verification:* JS `fsVantageSeries` proven **byte-identical** to Python `fund_vantage_series` (528 pts,
  max|diff|=0) before any rebuild; puppeteer then checks the fund line lies INSIDE its own peer band (it IS
  one of the cross-section members → min≤fund≤max by construction; a violation = the two smoothings diverged)
  + window-recompute of the portfolio means matches baked. **LEARNING: "60% batting" is meaningless without
  the field — a metric needs its PEER cross-section to be read; and there are TWO honest truths (NAV-aggregate
  vs stock-cross-section), show both. Always pull KV's own MoneyBall formula from his notebook (Explore agent)
  before coding a metric he's already defined — reuse, don't reinvent (his int(0.25·N) "20%" quirk included).**
- **2026-06-24 · Fund Skill made WINDOW-ADAPTIVE + vantage-point rolling plots (KV: managers change → a
  single full-life verdict blends regimes).** *Need:* judge a fund over a chosen span (a manager's tenure)
  and SEE the batting metrics through time, not one headline number. *Design (first principles):* the
  identity of a fund's skill lives in its **monthly active-return series A = fund − benchmark**, already
  baked per-scheme as `ts[]`. So the browser can recompute EVERY metric over any window with zero new
  server calls — a faithful JS port of `scheme_metrics` restricted to `ts[i0..i1]` (geometric CAGR over the
  calendar span, IR=mean/sd·√12, t=IR·√yrs, TE, batting=share(A>0), slugging=mean(A|A>0)/|mean(A|A<0)|,
  mag-hit, IC/IC-t, eff-N from `herf`, sizing from `sz`, + the circular block-bootstrap verdict ladder).
  Added `herf`+`sz` to `ts[]` (Python) so eff-holdings & sizing are window-aware too. *Display:* a start→end
  month dropdown + Full/10Y/5Y/3Y presets recompute the scorecard; baked Python is shown verbatim for the
  FULL window (no rounding drift), only a NARROWED window triggers recompute. A "SKILL THROUGH TIME" panel
  rolls batting/slugging/excess over a trailing window (12/24/36/60m) with the fund's own 25/50/75th-%ile
  reference lines + a neutral anchor (coin-flip 50% / 1× / 0) + the picked window shaded. *Verification
  (the real gate):* extended `_pup_fundskill.js` to assert the JS **full-window recompute reproduces the
  baked Python** across 6 schemes × all metrics — **0 mismatches** after fixing one tolerance (sizing_edge
  is a difference of two large compounded LEVELS ~tens, so its rounding error scales with the level → added
  a 3e-4·|value| relative term; a flat absolute bound false-flagged it). Independent Python spot-check on
  Kotak Large Cap: full-life "skilled" hides a weak 2017-19 stretch (slug 0.59×, excess −0.98%/yr) the
  window now exposes. **LEARNING: when a tolerance trips, ask whether the *metric's construction* (a
  difference of large levels) makes a flat absolute bound wrong before loosening blindly — scale the
  tolerance to the quantity. And: bake the raw SERIES, not just summary stats, and the display plane can
  recompute any slice client-side — no server, no parity port of new analytics, the full-window-equals-baked
  check IS the parity gate.** This also delivers the rebase-to-window charting ask (#49) for the Fund-Skill
  growth chart (sliced + rebased to ₹1 at window start).

- **2026-06-24 · NSE "hang" — the FULL diagnosis (3 stacked causes).** The daily pull hung for many
  minutes. *Experiments, in order:* single request → 0.1s; **25-request burst → all 0.3s, 0 fail** (so NOT
  rate-limited / IP-blocked); the *module's* loop hung at 0% CPU. *Three causes, peeled one at a time:*
  (1) `slow` pacing profile slept 4–9s/idx (~50min) → default `normal`; (2) **★ keep-alive socket
  poisoning** — NSE's WAF resets long-lived sockets, `requests` reuses the dead one → every later call
  blocks to its 30s timeout → `Connection: close`; (3) the HTML-page **cookie handshake** intermittently
  ReadTimeouts and returns **0 cookies anyway** (data endpoints are cookie-less) → 8s timeout. Result:
  full TR loop 8/8 in 4.0s. Remaining slowness = **dead/renamed index columns** in the snapshot retrying
  to timeout → TR timeout 30→10s + attempts 3→2 to bound it (real fix = prune dead columns, queued).
  Also: **TR endpoint = `getTotalReturnIndexString`** (NOT `getHistoricaldatatabletoString` — that's PR);
  PR/VAL dropped from the terminal entirely (never worked, unused). **META-LEARNING: don't stop at the
  first plausible cause — burst-test to isolate volume, step-isolate session vs loop vs endpoint; three
  bugs all looked like "NSE is slow."** Full detail in `NSE_DATAPULL_PLAYBOOK.md`.
- **2026-06-24 · Scheme-identity crack (the cross-project core problem: stable IDs).** KV's mappings always
  go stale because they anchor on the NAME (which AMFI/rebrands/mergers churn). *Re-derived from first
  principles:* the invariant is the **return fingerprint**, not the label. *Experiment 1 (naive):* match
  navindia_code→AMFI by max correlation of holdings-implied vs NAV returns → median 0.995, looks great —
  but only **4% decisively separated** and the name-disagreement matches are WRONG. *Root cause:* the
  **market factor dominates** — every equity fund correlates ~0.97 with every other. *Experiment 2 (fix):*
  rank by **tracking error** = std(holdings_implied − NAV) (the idiosyncratic signal) → confident matches
  (low TE + decisive) where **name independently agrees 97%**, and it CAUGHT real renames the name misses
  ("Groww, formerly Indiabulls Blue Chip"). **LEARNING: correlation is a market-beta mirage; the
  idiosyncratic *difference* (TE) is the true fingerprint. Anchor identity on return-fingerprint + ISIN
  (invariants), name/AMC/category as tie-breakers — not the reverse.** Hybrids need their full (not
  equity-sleeve) return; clones need the name tie-breaker. Prototype: `_fingerprint_match.py`. Build = #48.
- **2026-06-24 · Bedrock audit (adversarial workflow, 15 agents) — paid off.** Confirmed my inline finding
  (EXACTLY 2 re-code splits: Kotak 1223←262, Canara 10291←456; no hidden 3rd) AND caught what I'd missed:
  16 vst_ids held but absent from tr_returns (silent return-join drop ~0.33% MV, led by Tata Motors-DVR);
  cross-company vst_id contamination (Genus Power/Paper, Kirloskar); 32 single-month Arbitrage artifacts.
  Fixed: alias-merge + arbitrage/n<6 gate + orphan purge (703 clean schemes). **LEARNING: an independent
  adversarial pass finds the bugs your own single path doesn't — for "non-negotiable" bedrock, always run one.**

- **2026-06-24 · NSE pull "hangs"** — *Hypothesis:* datacenter IP blocked. *Experiment:* 3-request timed
  probe from this runtime. *Observation:* page/map/POST all 200 in <0.3s — NOT blocked. *Root cause:* the
  default `slow` pacing profile sleeps 4–9s/index → ~50 min of `sleep()`. *Decision:* default→`normal`.
  **LEARNING:** a process alive at ~0% CPU is *sleeping*, not blocked — probe before blaming the source.
  → codified in `NSE_DATAPULL_PLAYBOOK.md`.
- **2026-06-24 · Scheme-identity bedrock crack** — *Observation (KV):* "Kotak Large Cap Fund" shows TWICE
  in Fund Skill (skilled 147mo + insufficient 3mo). *Diagnosis:* feed-boundary re-code — old code stops
  2025-07, new code carries Aug–Oct under a different `navindia_code` → one fund torn in two. *Status:*
  inline audit found exactly 2 codes stopping at 2025-07 (both matched), workflow auditing full extent.
  **LEARNING:** `navindia_code` is NOT a stable scheme key across feed boundaries — needs a scheme-identity
  merge layer, the same way stocks needed `vst_id`. Don't trust a vendor key as an identity. Bake into [[identifier-resolution]].
- **2026-06-23 · Sector coverage** — small-cap funds showed ~67% sector coverage (Nifty-500 map only).
  *Experiment:* union the Nifty Total-Market list. *Result:* map 500→753, coverage →93.4% MV. **LEARNING:**
  Total-Market (750) = Nifty 500 + Microcap 250 by definition, so the smaller lists add nothing; the deep
  delisted tail has no free source → show "Unclassified" honestly, never hide it.
- **2026-06-23 · Blank Fund Skill tab shipped** — *Mistake:* the `view-fundskill` hidden-toggle line was
  missing in `setView`; the VM-stub runtime test PASSED (it ignores the `hidden` attribute) so it shipped
  blank. *Fix:* one line + a real-browser puppeteer probe. **LEARNING:** the VM stub is necessary but NOT
  sufficient — only a headless-browser probe catches `hidden`/cleanData-throw bugs. Probe before publish, always.
- **2026-06-22 · Plotly `marker:undefined` cleanData throw** — a trace with `marker:undefined` made Plotly
  do `'line' in trace.marker` → throw, swallowed into a blank panel. **LEARNING:** never set a Plotly trace
  key (marker/line/mode/fill) to `undefined` — omit it. Stub now rejects it; real probe = `_pup_fund.js`.
- **(parity law, standing)** — JS↔Python parity proves the two impls AGREE, not that the convention is
  CORRECT. Audit conventions separately (esp. cross-series common-overlap windows). See §4.

## 4. Standing disciplines (hard-won laws — never relearn)

1. **Probe before blaming the source** (NSE §0). Alive + 0% CPU = sleeping, not blocked.
2. **Real-browser probe before publish.** VM stub ≠ truth (misses `hidden`, real-Plotly `cleanData`).
3. **Never set a Plotly trace key to `undefined`** — omit it.
4. **Parity = agreement, not correctness.** Audit the convention separately.
5. **Vendor keys aren't identities.** `Co_Code`/`navindia_code` drift across feeds — resolve to a surrogate (`vst_id`, scheme-merge).
6. **Coverage by VALUE, surfaced — never hidden.** Show "Unclassified"/degraded %; no silent truncation.
7. **"No score for error."** Audit bedrock independently (adversarial verify) before building on it.
8. **Graceful degrade; only a faulty shell blocks publish.** Degraded feeds are flagged, never gating.

## 5. Pointers (deeper docs)
`MEMORY.md` · `pipeline/README.md` · `pipeline/NSE_DATAPULL_PLAYBOOK.md` · `FUND_MANAGER_ANALYSER_DESIGN.md` ·
`CLAUDE.md` · global skills: `first-principles-thinking`, `become-subject-matter-expert`, `curate-memory`.
