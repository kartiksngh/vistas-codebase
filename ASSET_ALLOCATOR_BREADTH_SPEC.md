# Vistas Asset Allocator — Market-Breadth Engine: the build contract

**Status:** build contract (ready to implement). **Scope:** a new **Asset Allocator** tab driven by a new
**market-breadth engine** (`vistas/breadth.py`), plus the relocation of the existing **Analyst Consensus
Flow** panel into this tab. **Discipline:** every metric below is given as *definition → method → why*, with
defaults, toggles, exact formulas, valid-universe rules, and honest caveats. "No score for error" — where a
number is descriptive-only, or not computable from what we hold, the spec says so in plain words and does not
dress it up as a signal. This is **READ-ONLY research** written against the live repo; it is the contract I
implement next, not code I am shipping now.

---

## 0. What "breadth" means, why it's the right question, and the one honest verdict up front

A market index is a **weighted average of its members**: a few mega-caps can carry the index up while most
stocks quietly bleed — the *level* says "all is well," the *participation* says otherwise. **Breadth** ignores
the index level and counts, directly, **how many individual stocks are doing a given thing** (making a new
high, sitting above their trend, in an uptrend). The user's literal question — *"what % of stocks are breaking
out to multi-year highs (or down to multi-year lows), and how broad is participation?"* — **is** the breadth
question, asked per-universe and per-sector.

**The honest verdict, stated first (recommendation, not survey):** I validated four breadth definitions on our
own Indian total-return data before designing anything (results in §1.4). The finding is that breadth on our
data is a **strong COINCIDENT / participation gauge** — it tells you, cleanly, how broad the move you are
*already in* is — but it is **NOT a forward allocator signal**: the apparent edge dies under honest
overlapping-window significance correction and is non-monotone. **Therefore this tab is built and LABELLED as a
descriptive market-health / regime cockpit, not a buy/sell engine.** We surface the participation picture
honestly; we do **not** build a thrust or divergence "signal" overlay, because on our data those sub-samples
are 1–10 events and the directional evidence is null or wrong-signed. This labelling discipline is the single
most important instruction in this contract.

Everything below is grounded in the actual on-disk panels (verified this session):

- **Indian stock TR panel** — `data/Stocks Data TR till Jun 25, 2026.csv`: wide daily **total-return**
  (dividend-adjusted) matrix, `Date` rows × **4,309 NSE symbols**, **2000-01-03 → present (~6,584 rows)**. One
  row = one trading day's adjusted close for every symbol. Daily-close only (no intraday → a "new high" is a
  **closing** high). Within NSE-500 the panel is survivorship-managed (delisted/suspended names are dropped,
  not carried flat). **This is the breakout/breadth source.**
- **NSE index TR panel** — `data/Indices Data TR till Jun 25, 2026.csv`: ~131 indices including ~25 **sector**
  indices (Nifty IT/Bank/Auto/Pharma/FMCG/Metal/Realty/Energy/…). Used only as the *index-level reference line*
  drawn behind a breadth chart (to show level-vs-participation), never as a breadth input.
- **Sector map** — `vistas/stock_intel.py::load_industry_map` (reads `data/stock_industry.json`, NSE-500
  "Industry" column, 22 macro groups) covers **752 / 4,309 = 17.5%** of the panel; broadened by
  `vistas/funds_portfolio_viz.py::_extended_secmap` (NSE-500 base + AMC-disclosed-industry crosswalk learned
  from `data/funds/history/holdings_history.parquet`, majority-vote) to **~1,606 / 4,309 = 37.3%**.
  `vistas/stock_intel.py::INDUSTRY_TO_SECTOR` maps 13 industries → a tradable Nifty sector index.
- **Per-stock technicals precedent** — `vistas/stock_intel.py::_market_behaviour` (verified at **lines
  289–398**): 52-week high/low via `s.tail(252).max()/.min()`; `dist_from_52w_high = px/max(252) − 1`;
  50/200-DMA `above = px >= mean(N)`; `golden_cross = 50DMA >= 200DMA`; drawdown `= px/cummax − 1`. **The new
  engine mirrors this exact logic but vectorised at the *panel* level** (all stocks at once) so the single-name
  cockpit and the breadth cockpit can never disagree by convention.
- **Global / world** — `vistas/world.py` + `data/World Data PX till Jun 25, 2026.csv`: ~84 instruments,
  **index/ETF LEVEL price only**. CONFIRMED: **no constituent / membership columns**. The only ETFs present are
  bond/credit (AGG/EMB/HYG/IEF/LQD/SHY/TIP/TLT); the equity series are underlying **indices** (^GSPC/^IXIC/^RUT
  …), not SPY/EFA/EEM. **⇒ True "breadth of a world ETF" (% of its MEMBERS at highs) is NOT computable from
  what we hold.** Global breadth is delivered as **level-proxy diffusion now + an explicit shopping-list
  placeholder** (§6). This limit is stated on the panel itself, not hidden.
- **No existing breadth / advance-decline / new-high engine in the repo** — grepped; nothing to duplicate.

---

## 1. THE BREAKOUT / BREADTH DEFINITIONS (final: defaults, toggles, formulas, valid-universe rules)

Let `P` be the adjusted-TR close panel — one column per stock. All formulas operate on `P` directly (no
returns), so corporate actions that are already baked into the TR level need no extra handling.

### 1.1 BREAKOUT — bullish multi-year new high (the core rule)

**Definition.** A stock is "at a new high" on date `t` for lookback `W` (trading days) when today's adjusted
close is the highest close over the trailing `W`-day window **including today**.

**Method (exact formula).**
```
roll_high(s, t, W) = max( P[s, t-W+1 : t] )          # rolling max INCLUDING today
at_new_high(s, t, W) = ( P[s, t] >= roll_high(s, t, W) )
```
Empirically verified on our panel: `P[s,t] >= max(window incl. t)` and the stricter `P[s,t] > max(window excl.
t)` give **identical** counts (it's a daily-close panel; no ties at the exact top). **Use `>=` against the
window-including-today** — simplest, tie-robust, and matches `_market_behaviour`'s `dist_from_52w_high`
convention (a value of 0 there = at the high). The all-time-high case is just `W = ALL` (whole available
history of that column; 19 names today on the NSE-500 slice — a viable, rare, durable-leadership sub-case).

**Why.** A new high is the cleanest mechanical proof that price has cleared all recent overhead supply
(Livermore's "line of least resistance"; Darvas box-top; O'Neil's 52-week pivot; Minervini's Stage-2). The
**count of stocks at new highs** is the canonical breadth primitive of the entire new-high/new-low literature.

**Lookback `W` — default vs toggle (recommendation).** I ran all candidates live on the NSE-500 fresh-name
slice (today's readings): 252d **3.0%** (valid 737), 756d **2.4%** (670), 1260d **2.1%** (623), ATH (19
names). The windows are monotone and well-separated (a 5-year high is a strictly harder bar than a 52-week
high), and **history cost rises with `W`** — a 5-year rule silently drops ~⅓ of the universe and biases toward
older/larger names. **Recommendation:**
- **DEFAULT `W = 252` (52-week / 1-year high).** It is the canonical TA reference, keeps the **widest valid
  universe** (least new-listing bias), and is the most sensitive divergence detector → it drives the headline
  breadth line.
- **Toggle `W ∈ {252 (52w) · 504 (2y) · 756 (3y) · 1260 (5y) · ALL (all-time)}.`** The "multi-year highs" the
  user asked about are the 3y/5y settings (rarer, higher-conviction, durable-leadership reads). UI defaults to
  252 and additionally shows the **3y line as a faint companion** so the "multi-year" read is one glance away.

### 1.2 BREAKDOWN — bearish multi-year new low (the mirror)

**Definition / method.** Symmetric to §1.1:
```
roll_low(s, t, W) = min( P[s, t-W+1 : t] )
at_new_low(s, t, W) = ( P[s, t] <= roll_low(s, t, W) )
```
Same `W` toggle, same default (252). **Why.** New-low expansion is the classic distribution / capitulation
tell and the bear half of NH-NL (§1.5).

### 1.3 TREND PARTICIPATION — above the 200-DMA (and 50-DMA), golden cross

**Definition.** A stock "participates in the uptrend" when its close is at or above its `N`-day simple moving
average; "golden cross" when its 50-DMA ≥ its 200-DMA.

**Method (exact formula).**
```
above_dma(s, t, N) = ( P[s, t] >= mean( P[s, t-N+1 : t] ) )      # N = 50 or 200
golden_cross(s, t) = ( mean(P[s, last 50]) >= mean(P[s, last 200]) )
```
Mirrors `_market_behaviour` lines 308–319 exactly.

**Why.** "% above the 200-DMA" is the **smoothest, every-day-valid** participation gauge and (per §1.4) the
single definition with any forward gradient and the highest coincident correlation with trailing market moves
(+0.74). It is the right number to *score a rally's quality* / flag a thinning tape **descriptively**. The
above-200-DMA filter is also the Minervini Stage-2 confirmation that screens out "new high in a downtrend"
false positives (see §3 toggle).

### 1.4 Which definition is the headline — and the honest caveat (validation result)

I validated the four definitions before designing the tab. **Universe:** 752 NSE-500 stocks in the panel;
daily breadth, month-end sampled, 2010-06 → 2026-06 (~187 monthly obs); forward returns on NIFTY 500 TR.
**Findings:**
- **LEVEL vs TRAILING 3m return:** corr +0.61 (252h) / +0.56 (756h) / **+0.74 (a200)** / +0.64 (nh_nl) —
  strongly **coincident**.
- **LEVEL vs FORWARD 6m return:** corr collapses to +0.09 / +0.03 / +0.10 / +0.06 — **essentially zero
  predictive content.** Tertile fwd6m: a200 low 5.89% vs high 9.01% (the only real gradient) but 252h
  8.06↔7.64 and 756h 7.92↔7.91 are flat. The a200 effect **FAILS robustness:** quintile fwd6m is
  U-shaped/non-monotone (7.59 / 1.70 / 5.31 / 5.92 / 11.33), and the high-minus-low t-stat deflates to **~0.58**
  once overlapping monthly windows are accounted for (naïve t=1.42 ÷ √6).
- **THRUST** (a200 jumping ≤20 → ≥40 within 2m): only **5 events**, fwd6m 3.54% (BELOW the 6.40% baseline),
  50% positive. The 252h 0→10 thrust fired **once**. Too rare to trust.
- **DIVERGENCE** (index near 12m high while %252d-high falls >5pp over 3m): **10 events**, fwd6m +5.88%, 60%
  positive — **NOT a drawdown precursor**; no separation in the worst-forward months.

**Conclusion (drives the whole UI):** **breadth tracks where the market HAS BEEN, not where it is going.**
Recommendation: **default headline = % above 200-DMA (`pct_a200`)**, framed as a **participation / regime /
health** chart; keep `pct_new_high` and `nh_nl_net` as **secondary companion lines** on the same panel for
at-the-extremes texture. **Do NOT promote any of them to a buy/sell trigger, and do NOT build a thrust or
divergence "signal" overlay.** (Caveats that bound this verdict: monthly forward windows OVERLAP → naïve
significance inflated ~√h; ~187 month-ends span only ~2–3 cycles; thrust/divergence sub-samples are 1–10
events; universe is today's NSE-500 map → mild survivorship; breadth computed on ~752 names not the full
~4,300; eligibility gates rest early-2010 readings on a thinner cross-section.)

### 1.5 NH-NL NET (companion composite)

**Definition / method.** `nh_nl_net(t, W) = pct_new_high(t, W) − pct_new_low(t, W)` (percentage points).
**Why.** The classic Lowry/Hindenburg-style net new-high line in one number — a compact "is the tape expanding
or distributing" read. Companion line only.

### 1.6 Valid-universe / minimum-history rules (so we never fabricate a high)

A stock is **eligible on date `t` for window `W`** only if all hold (these prevent a 3-month-old listing from
"making a 5-year high"):
- **Enough span:** the stock has at least `min_span(W)` non-NaN observations ending at `t`, where `min_span =
  W` for new-high/low and `= N` for the DMA test. A short-history name is simply **excluded from that window's
  denominator**, not counted as a non-high.
- **Density floor (continuity):** mirroring `data.py`'s coverage gate — at least `MIN_OBS_PER_YEAR = 150`
  observations per year of span and no internal gap > `MAX_INTERNAL_GAP_ROWS = 25` rows. A sparse/snapshot
  series cannot contribute (its "high" would be a stale artefact).
- **Cross-section floor:** a breadth point for a (universe/sector, date) is emitted only if **≥ `MIN_BREADTH_N`
  eligible stocks** that day (default **100** market-wide, **5** per sector — sectors are thinner). Below the
  floor the value is `null` (rendered as a gap, never as 0%). This is the "no score for error" guard: a sector
  with 3 eligible names does not get a breadth number.
- **Denominator is always the eligible count**, reported alongside every percentage, so the reader can see
  *"3.0% of 737"* and judge the base.
- **Liquidity (optional, OFF by default, flagged):** an optional screen to the tradable universe (median daily
  turnover ≥ a floor) is *available* but **off by default and labelled**, because a wide turnover panel aligned
  to all 4,309 symbols is not yet confirmed on disk (it is item 6 of the shopping list). When the turnover
  panel lands, this becomes a toggle; until then breadth is unscreened and says so.

---

## 2. THE ENGINE — `vistas/breadth.py` (proposed)

A **new, self-contained, licensing-clean** module: **price-derived only** (the stock TR panel + the sector
map). **It reads NO ARM / LSEG StarMine data** — nothing licensed is touched, so its outputs are free to bake
into the public deck. It does **not** import `analytics.py` and adds **no** JS-parity obligation (§7).

### 2.1 Inputs (RAM-bounded — mandatory)

- `panel`: the newest `data/Stocks Data TR till *.csv`, but loaded **with `usecols` restricted to the symbols
  we actually breadth** (default the resolved NSE-500 universe, ~752 cols) plus `Date`, parsed to a float frame
  — **never the full 4,309-wide frame** (a build + a workflow are already using memory). The universe symbol
  list is resolved first (from the sector map), then passed as `usecols`.
- `secmap`: `{SYMBOL → sector}` from `load_industry_map` (default) or `_extended_secmap` (broader, opt-in).
  Sectors with < `MIN_BREADTH_N` eligible names are folded into an `"(thin)"` bucket, not dropped silently.
- Config: `windows = (252, 504, 756, 1260, "ALL")`, `dmas = (50, 200)`, the eligibility constants from §1.6.

### 2.2 Core builders (vectorised, mirror `_market_behaviour`)

All use pandas rolling ops over the panel so every stock is evaluated at once:

| function | returns | formula (per §1) |
|---|---|---|
| `rolling_new_high(panel, W)` | bool frame `at_new_high[s,t]` | `panel >= panel.rolling(W, min_periods=W).max()` |
| `rolling_new_low(panel, W)` | bool frame | `panel <= panel.rolling(W, min_periods=W).min()` |
| `above_dma(panel, N)` | bool frame | `panel >= panel.rolling(N, min_periods=N).mean()` |
| `golden_cross(panel)` | bool frame | `dma50 >= dma200` (both with `min_periods`) |
| `eligible_mask(panel, W)` | bool frame | span + density gate (§1.6) |

Each `True` only where the matching `eligible_mask` is `True`; elsewhere `NaN` (excluded from the
denominator). The density/continuity gate is computed **once** per stripe and reused.

### 2.3 Aggregators → the breadth panel

```
build_breadth_panel(panel, secmap, cfg) -> dict
```
For the **market** universe and for **each sector** group, and for each requested `W`, produce a daily time
series `pct = 100 * eligible_true.sum(axis=1) / eligible_total.sum(axis=1)`, masked to `null` where
`eligible_total < MIN_BREADTH_N`. The returned structure (the thing baked into the deck):
```
VISTAS_BREADTH = {
  "asof": "2026-06-25",
  "meta": { "universe": "NSE-500", "n_symbols": 752, "windows": [252,504,756,1260,"ALL"],
            "dmas": [50,200], "eligibility": {...§1.6 constants...},
            "caveat": "Descriptive / coincident participation gauge, validated on our own TR data — NOT a forward signal. India stocks only." },
  "dates": [...],                                  # shared date axis (downsampled, see below)
  "market": {
     "pct_new_high":   { "252":[...], "756":[...], "1260":[...] },   # default + companions
     "pct_new_low":    { "252":[...] },
     "pct_above_200dma":[...],                      # the recommended HEADLINE line
     "pct_above_50dma": [...],
     "nh_nl_net":      { "252":[...] },
     "pct_golden_cross":[...],
     "eligible_n":     [...]                        # denominator per date (for honest base display)
  },
  "sectors": { "<sector>": { ...same keys... , "eligible_n":[...] }, ... },
  "snapshot": {                                     # CURRENT day, for the §3c m%-threshold screen
     "asof": "...",
     "market": { "pct_new_high_252": 3.0, "pct_above_200dma": ..., "pct_golden_cross": ..., "eligible_n": 737 },
     "sectors": [ { "sector":"IT","pct_new_high_252":..,"pct_new_high_756":..,"pct_above_200dma":..,
                    "pct_golden_cross":..,"eligible_n":.., "names_new_high_252":[...], "names_golden_cross":[...] }, ... ]
  }
}
```
- **`names_*` lists** in the snapshot are the actual symbols satisfying each rule **today**, so the §3c screen
  can drill from "sector X has ≥ m% in breakout" straight to the names — no recompute in the browser.
- **Size control:** the full daily history × many series is heavy. Bake **month-end (or weekly) sampled**
  history for the time-series chart (breadth is a slow regime gauge — daily resolution adds noise, not signal,
  per §1.4) and keep the **snapshot at daily** resolution. This keeps `window.VISTAS_BREADTH` small enough to
  inline; if it still exceeds the deck's inline budget, ship it **lazy** (a sibling JSON loaded on first open
  of the Asset Allocator tab), exactly like the consensus lazy-load path in `deck.py` (§4).

### 2.4 Global (level-proxy) builder

```
build_global_proxy(world_panel) -> dict
```
Because we have **no constituents**, "global breadth" is a **level-proxy diffusion**: for each of the ~31
global equity **index** level series in the world panel, compute its own `at_new_high(252)`, `above_200dma`,
and `% from 52w high`, then report **"X of N global indices are above their 200-DMA / at a 52-week high."**
This is a cross-region *diffusion of index levels*, **explicitly NOT** member-level breadth. The output carries
a hard-coded `"placeholder": true` + the shopping-list note (§6) so the UI states the limitation in words.

### 2.5 Licensing / provenance

Price-derived only; no ARM. Header note in the module: *"Mirrors `stock_intel._market_behaviour` conventions at
the panel level; reads only the public TR price panel + the NSE-500 industry map; baked into the public deck."*

---

## 3. THE TAB UI — new **"Asset Allocator"** tab

Add `<button class="tab" data-view="allocator">Asset Allocator</button>` to `#tabs` in `static/index.html`
(after **Macro**, before any future tab), and a matching `<div class="view" id="view-allocator" hidden>` pane
(same `.view` show/hide machinery the other panes use). Render functions live in `static/vistas.js`, reading
the baked `window.VISTAS_BREADTH` (display-only; no recompute → no parity port, §7). Every panel carries a
`<details>` "Definition · Method · Why" block (the house style already used by the crowd panel at vistas.js
~line 2763), and the **descriptive-not-predictive caveat** is printed at the top of the tab.

### (a) Market breadth chart
The headline panel. Default line = **`pct_above_200dma`** (the recommended headline, §1.4), with `pct_new_high`
and `nh_nl_net` as faint **companion lines**, and the matching **NSE index TR level** drawn on a secondary axis
behind it (to show *level vs participation* — the divergence picture, descriptively). **Toggle `W` ∈ {52w · 2y
· 3y · 5y · all-time}** drives the new-high/low/nh-nl lines (DMA lines are `W`-independent). Apply the
`chart-plotting` skill rules (auto-rescale Y on zoom, purge-before-react, resize-on-show, clip X to data span).
Label: *"Participation / market-health — descriptive, not a forward signal."*

### (b) Per-sector breadth — small-multiples / heatmap
A **heatmap** (sectors × time, colour = the chosen breadth metric) with a metric selector
{new-high(W) · above-200-DMA · golden-cross · nh-nl}, plus a **small-multiples** alternative (one mini
sparkline per sector). Reads `VISTAS_BREADTH.sectors`. Thin sectors (below the cross-section floor) render as
greyed cells, not zeros. **Why:** rotation/leadership is read sector-by-sector — this is the "where is
participation concentrated" view.

### (c) The m%-threshold screen (the allocator's headline tool)
A **numeric input / slider `m` (0–100, default 50%)**. Lists every **sector/theme where ≥ m% of its eligible
constituents are in breakout** (new-high at the chosen `W`) **or** golden-cross (a second toggle picks the
rule), sorted by the breadth %. Each row shows `sector · breadth% · eligible_n` and **drills to the names**
(the `names_new_high_W` / `names_golden_cross` lists from the snapshot). Reads `VISTAS_BREADTH.snapshot` — no
browser recompute. **Why:** this is the direct, literal answer to *"which sectors/themes have ≥ m% of their
stocks breaking out right now."* Labelled descriptive (a high-breadth sector is one already participating, not
a forecast).

### (d) Global breadth (proxy now + shopping-list note)
Reads `VISTAS_BREADTH` global proxy (§2.4): *"X of N global equity indices above their 200-DMA / at a 52-week
high."* Directly beneath it, a boxed **note**: *"True breadth of a global index/ETF = % of its MEMBERS at
highs. We do not yet hold constituent/membership data (the world panel is index-LEVEL only), so this is a
level-proxy diffusion across indices, not member breadth. To upgrade, see SHOPPING_LIST.md items 1–4."* —
honesty on the panel, no overclaim.

### (e) RELOCATED — "Analyst Consensus Flow" panel (moved here from the Macro tab)
The existing Analyst Consensus Flow cockpit moves **from Macro to Asset Allocator** (it is an allocation-lens
panel — sector ARM consensus rolled to the 11 desks — so it belongs with breadth). **Exact wiring needed
(verified):**
- **Markup move:** in `static/index.html`, move the line `<div id="consensus-cockpit"></div>` (currently
  **line 421**, inside `#view-macro` which opens at **line 413**) into the new `#view-allocator` pane.
- **Render fn:** `renderConsensus()` (`static/vistas.js` **line 3987**) reads `window.VISTAS_CONSENSUS`
  (**line 3989**) into `$("consensus-cockpit")` (**line 3988**) — it is host-id-driven, so it keeps working
  unchanged once the `#consensus-cockpit` div lives in the new pane. The dispatch that currently calls
  `renderConsensus()` on Macro open (`static/vistas.js` **line 4063**) must be **re-pointed to fire when the
  Asset Allocator view opens** instead (and removed from the Macro path).
- **Deck embed:** unchanged. `window.VISTAS_CONSENSUS` is baked by `vistas/deck.py` **line 549** (lazy) from
  `site_embed['consensus']`, which is assembled at **deck.py lines 920–959** via
  `vistas/arm_sectors.py::build_consensus_dataset` (sector AGGREGATES only → no licensed per-stock ARM ships).
  No data change — only the tab the div lives under changes.
- **Probe:** the existing headless probe `_pup_consensus.js` must still PASS after the move (it asserts the
  consensus cockpit renders); point it at the Asset Allocator tab.

---

## 4. Deck baking (where `VISTAS_BREADTH` lives)

Mirror the consensus pattern in `vistas/deck.py`:
- Assemble `breadth = vistas.breadth.build_breadth_panel(...)` in the site-embed block (near the consensus
  assembly at deck.py ~lines 920–959), wrapped in `try/except` with a **graceful-degrade** print (a breadth
  failure must never block the deck — the rest of the tab/site still ships).
- Add `"breadth": breadth` to `site_embed`.
- Emit `window.VISTAS_BREADTH = {...};` the same way as `consensus_line` (deck.py line 549) — **lazy** (sibling
  JSON) if the inline payload would bloat the deck, else inline. Month-end history + daily snapshot (§2.3) keeps
  it small.
- **Licensing:** price-derived, no ARM → safe in the public deck.

---

## 5. Build / parity / gate notes

- **Display-plane only — NO `analytics.py` JS-parity port.** `breadth.py` is computed **once in Python at bake
  time** and the browser only **reads** `window.VISTAS_BREADTH` (no client-side recompute), exactly like
  `VISTAS_CONSENSUS`. So the §"PARITY DISCIPLINE" rule in `CLAUDE.md` (mirror every `analytics.py` formula into
  `vistas_analytics.js`) **does not apply** — there is no shared formula to keep in lockstep. Do **not** add
  breadth math to `vistas_analytics.js`.
- **No Plotly `undefined` keys.** Per the hard-won lesson in `CLAUDE.md`: never set a trace key
  (`marker`/`line`/`mode`/`fill`) to `undefined` — **omit it** — or real-Plotly `cleanData` throws and the
  panel silently blanks.
- **Browser-probe gate (mandatory).** A `node --check` / VM stub-Plotly test misses real-Plotly throws. Add a
  **headless-browser probe** (puppeteer, like `_pup_consensus.js` / `_pup_fund.js`) — `_pup_allocator.js` —
  that opens the built deck, switches to the Asset Allocator tab, and asserts: market chart renders, the heatmap
  renders, the m%-screen lists rows and drills to names, the global-proxy panel renders, and the **relocated
  consensus cockpit still renders**. **PASS is the publish gate.** Also keep `_deck_runtime_test.js` green.
- **RAM discipline at bake.** Resolve the universe symbol list first, then load the stock panel with `usecols`
  = those symbols + `Date` only (§2.1). Never materialise the full 4,309-wide float frame; honour the existing
  build lock `data/_refresh/.build.lock` (never run a second build).
- **Publish path unchanged.** This is a new tab on the existing Terminal v2 site — it ships through the normal
  `publish_terminal.py` full rebuild (KV's "no shortcut" rule), and the off-machine backups in publish step
  [5/5] cover the new source automatically.

---

## 6. THE SHOPPING LIST (verbatim from the data inventory) — to live in `SHOPPING_LIST.md`

Create **`C:\Users\Administrator\Documents\Projects\Vistas\SHOPPING_LIST.md`** as a standing section (the
breadth engine references it from the global panel). Verbatim:

1. **Global equity index/ETF CONSTITUENT membership lists (point-in-time, per index: S&P 500, Nasdaq 100, FTSE
   100, Euro STOXX 50/600, Nikkei 225, MSCI EFA/EEM proxies).** *Why:* True "breadth of an ETF/index" = % of
   its members at 52w highs / above their 200-DMA. We have ZERO constituent data — the world panel is index
   LEVEL only — so member-level breadth is not computable. Membership is the spine that links each index to its
   underlying stocks. *Candidate source:* Each index provider's published holdings file: S&P (spglobal/SPDR ETF
   holdings CSV for SPY/IVV), Invesco QQQ holdings CSV (Nasdaq 100), iShares holdings CSV for EFA/EEM/IEFA/IEMG
   (blackrock.com holdings download), FTSE Russell factsheet, STOXX components, Nikkei Inc constituent list. For
   free/no-key: the iShares/SPDR/Invesco daily holdings CSVs are publicly downloadable per ETF.
2. **Daily prices for each global index's CONSTITUENTS (member-level OHLC/adjusted-close time series).** *Why:*
   Once we have membership, we still need each member stock's daily price history to compute its own 52w high
   and 50/200-DMA, then aggregate to the index's breadth. The world panel has none of the underlying
   single-name prices. *Candidate source:* stooq.com (free bulk world equity history, /q/d/l per ticker), Yahoo
   Finance via yfinance (free, per-member ticker), or Tiingo/Alpha Vantage free tiers. stooq is the cleanest
   free bulk source for US + global single names.
3. **Historical (point-in-time) constituent membership, not just the current snapshot.** *Why:* Index
   membership changes over time (additions/deletions). Using only today's members to compute historical breadth
   introduces survivorship/look-ahead bias — past breadth would be measured on a roster that didn't exist then.
   We need add/drop-dated membership to build an honest breadth history. *Candidate source:* Index
   reconstitution announcements (S&P Dow Jones Indices press releases, FTSE Russell semi-annual reviews, Nasdaq
   annual reconstitution), or a paid PIT source (Norgate Data for US, or a vendor like FactSet/Bloomberg if
   budget allows). Free approximation: archive each provider's holdings CSV daily going forward to build PIT
   membership prospectively.
4. **US equity-ETF level series for direct level-proxy breadth (SPY/EFA/EEM/IWM/QQQ adjusted close).** *Why:*
   Lower-cost stopgap before full constituent breadth: track whether the broad ETFs themselves are at multi-year
   highs / above their own DMAs. Our world panel has the underlying INDICES (^GSPC, ^IXIC, ^RUT) but NOT the
   tradable ETFs; adding the ETF series gives a cleaner cross-asset level-breadth diffusion across regions/styles.
   *Candidate source:* Already in our world.py pipeline (Yahoo via vistas/world.py) — just append SPY, EFA, EEM,
   IWM, QQQ, VEA, VWO tickers to the world fetch list. Zero new infra, no key.
5. **Full-universe (beyond NSE-500) Indian symbol→sector tagging.** *Why:* Sector breadth currently covers only
   ~17.5% (NSE-500 map) to ~37.3% (extended, incl. AMC-disclosed) of the 4309-symbol panel; the small/micro-cap
   tail is "Unclassified," so per-sector breadth understates the small-cap universe and can't be computed for
   those names. *Candidate source:* NSE "Securities available for trading" / industry classification master
   (nseindia.com bhavcopy + the NSE Industry Allocation file), or AMFI/BSE sector classification; alternatively
   extend the existing AMC-disclosed-industry crosswalk in _extended_secmap by ingesting more scheme portfolios.
   Free from NSE/AMFI.
6. **Per-stock daily TRADED VALUE / volume panel aligned to the stock TR panel (for liquidity-screened
   breadth).** *Why:* Robust breadth often excludes illiquid names (a micro-cap at a "high" on no volume is
   noise). _market_behaviour already reads a turnover panel for a single name; a wide turnover/volume panel
   matching the 4309 symbols would let the breadth engine filter to a tradable universe and weight by liquidity.
   *Candidate source:* Already partially available via the bhavcopy turnover the repo ingests
   (vistas/bhav_prices.py / delivery.py); confirm a wide daily turnover CSV exists or derive one from the
   bhavcopy archive — no external source needed, internal build.

---

## 7. One-paragraph summary (the recommendation, restated)

Build `vistas/breadth.py` (price-derived, licensing-clean, mirroring `_market_behaviour` at panel level,
RAM-bounded by `usecols` to the NSE-500 universe), bake its output to `window.VISTAS_BREADTH` via `deck.py`
exactly like consensus, and add an **Asset Allocator** tab with five panels: a market-breadth chart
(headline = **% above 200-DMA**, default new-high window **252d**, with 3y/5y/ATH toggles and new-high/nh-nl
companions), a per-sector heatmap, the **m%-threshold sector screen** (the literal answer to the question, with
name drill-down), a **global level-proxy** panel that states it is *not* member-breadth, and the **relocated
Analyst Consensus Flow** cockpit. Ship it through the normal full `publish_terminal.py` rebuild, gated on a new
puppeteer probe `_pup_allocator.js`. **No `analytics.py`/`vistas_analytics.js` parity port** (display-plane
only). And — the non-negotiable — **label the whole tab descriptive / coincident, never a forward signal**,
because that is what our own data honestly supports.
