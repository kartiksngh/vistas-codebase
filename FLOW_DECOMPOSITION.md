# Flow Decomposition — price vs scheme-inflow vs net-active (the conviction flow)

**Observation (KV, 2026-06-26), keenly noted.** A fund's change in rupee holding of a stock is driven by
THREE forces, only one of which is a conviction decision. Our current flow metric strips one of the two
contaminants but **not** the other, so the 1m/3m/6m flow is **not, by itself, a buy signal** — especially
in the current heavy net-SIP-inflow regime where the un-stripped contaminant is large and positive almost
everywhere. This doc is the spec to fix it. Affects the Quant smart-money panel, the Funds cockpit active
trades, the Screen flow columns, and the Mesh `flow` force ([[MESH_DESIGN]] §1). Engines live in
`vistas/funds_flows.py`.

---

## 0. The three forces on a holding's value

For fund `f`, stock `i`, period `t → t+1`, let `MV` = market value of the holding, `r_i` = stock total
return, `w_i` = the holding's weight in the fund's **equity book** (normalised so `Σ_i w_i = 1`), `R_p =
Σ_i w_i(t)·r_i` = the book's drift (do-nothing) return, `F` = the scheme's net new money over the period
(unit creations − redemptions, in ₹).

```
ΔMV_i = MV_i(t+1) − MV_i(t)
      = MV_i(t)·r_i        (1) PRICE ACTION         — stock moved; no decision
      + F·w_i(t)           (2) INFLOW DEPLOYMENT     — scheme grew; deployed pro-rata; no per-name decision
      + active_trade_i     (3) NET ACTIVE TRADE      — the manager changed the WEIGHT; the only conviction
```
(approximate, ignoring intra-period timing; (2) assumes pro-rata deployment — a *non*-pro-rata tilt is
itself an active decision and correctly lands in (3).)

**Worked example.** Fund holds ₹100cr of X (5% of a ₹2,000cr book). X returns +10%; scheme takes ₹200cr
net inflow deployed pro-rata. With ZERO active decision, X ends at `100·1.10 + 200·0.05 = ₹120cr` — a
₹20cr "increase" that is **all** price + inflow, **no** conviction.

---

## 1. What each figure strips (and the contamination it leaves)

| Figure | Formula | Strips | Leaves / contaminant |
|---|---|---|---|
| **Gross** (₹ value change) | `MV_e − MV_s` | nothing | price + inflow + active (raw) |
| **Price-adjusted** *(current metric)* | `MV_e − MV_s·(1+r_i)` | price (1) | **inflow deployment (2)** + active (3) |
| **Net active** *(the conviction flow)* | weight-space, below | price (1) **and** inflow (2) | active (3) only |

**Why price-adjusted is still contaminated.** With a pro-rata inflow `F`:
`MV_i(t+1) = MV_i(t)(1+r_i) + F·w_i(t)` ⇒ `price_adj_i = MV_i(t+1) − MV_i(t)(1+r_i) = F·w_i(t)` — a
**phantom buy of `F·w_i` in EVERY held name**, even with no active decision. In a strong-inflow regime
(India 2024–26), this makes the price-adjusted metric read "everything is being accumulated," which is
mostly SIP money landing, not conviction. This is the artifact KV flagged.

---

## 2. The fix — measure the decision in WEIGHT space

Pouring money in pro-rata does not change any weight, so measuring the active decision as a **weight
change** cancels the inflow contamination by construction. The decision quantity is the **drift-adjusted
active weight change**:

```
w_i^drift(t+1) = w_i(t)·(1+r_i)/(1+R_p)        # weight if the manager did NOTHING (price drift only)
Δw_active(i)   = w_i(t+1) − w_i^drift(t+1)      # the genuine reweighting;  Σ_i Δw_active = 0 (zero-sum)
```

**Net active flow in ₹** (state the convention — value the reweighting at the post-drift book size):
```
active_flow_i (₹) = AUM_eq(t)·(1+R_p) · Δw_active(i)
```

**Sanity tests it must pass:**
- Manager does nothing → `w_i(t+1) = w_i^drift` → `Δw_active = 0` → flow 0. ✔
- Pro-rata inflow deployment → weights unchanged → `Δw_active = 0` → flow 0 (price-adjusted would show
  `F·w_i`). ✔ — this is the whole point.
- Stock doubles, shares untouched → weight rises exactly to `w^drift` → `Δw_active = 0`. ✔ (price stripped)
- Trim into a rally (sell shares, weight falls below drift) → `Δw_active < 0` → genuine distribution. ✔

Because weights are normalised over the **equity sleeve**, money that flows to cash/debt never enters; a
non-pro-rata equity tilt correctly registers as active. The `Σ_i Δw_active = 0` identity is the build's
self-audit (active rebalancing is zero-sum across the book).

---

## 3. Cross-AMC stock-level aggregation (the smart-money signal)

For stock `i`, the market-wide net active flow = `Σ_f active_flow_{f,i}` — sum each fund's active rupee
trade. Bigger funds count more (more money ⇒ more impact — intended for a money-flow read; the breadth
read counts signs separately, as the smart-money panel already does). This is the genuine "smart money is
rotating INTO this stock" object, free of the "lots of funds got inflows and pro-rata bought everything"
artifact.

A scheme-level **implied net inflow** can also be surfaced for context: `F ≈ ΔAUM_eq − price
appreciation of the book` (descriptive only).

---

## 4. UI — three figures, ONE switchable column (no column explosion)

A single segmented toggle / dropdown on the flow column: **Gross · Price-adjusted · Net active**. The
1m/3m/6m sub-columns all re-render to the chosen basis. Default = **Net active** for any *signal* context;
Gross/Price-adjusted available as descriptive views. Each carries its Definition·Method·Why tooltip and the
contamination note. Net active is the default the conviction story rests on.

---

## 5. Feasibility on our data — all three computable today

The `funds_flows` store carries per-holding `mv_start_cr` / `mv_end_cr` (⇒ weights and `AUM_eq = Σ MV`) and
we have the TR panel for `r_i` and `R_p`. So: **Gross** trivial; **Price-adjusted** already built (the
corp-action-immune `_pair_flows`: `mv_e − mv_s·(1+r)`); **Net active** = new weight-space compute, built
**on top of** the existing corp-action-immune pairing + merger-swap bridge (so splits/mergers stay handled
— per [[FUND_MANAGER_ANALYSER_DESIGN]] §1.4, drift adjustment must be on split-adjusted shares).

---

## 6. Signal implication (defensibility)

- Only **Net active** can be a *signal*; Gross and Price-adjusted are descriptive.
- Even Net active is **positioning/diagnostic until it passes the lead-lag/Granger gate** (flow must
  *precede* return, not co-move) — [[MESH_DESIGN]] S1 gate. This refinement *strengthens* the case: it
  removes a known artifact (inflow deployment) before the signal is tested, so a Granger-passing net-active
  flow is a cleaner conviction read than the current metric.
- Re-validate `CONVICTION_ADD` (S1) and the smart-money panel on the **Net active** figure once built; the
  prior validation used the price-adjusted (contaminated) flow.

---

## 7. Build steps

1. `vistas/funds_flows.py`: add weight-space `Δw_active` + `active_flow_i` alongside the existing
   price-adjusted `_pair_flows`; expose all three (gross / price-adj / net-active) per fund-stock-window.
2. Re-bake the per-stock Quant `smart_money_flow` block to carry all three (+ the implied-inflow context).
3. Funds cockpit + Screen + Mesh flow force read the chosen basis; default Net active for signals.
4. Front-end: the single switchable flow column (§4) + tooltips + the contamination note.
5. Re-run the S1 / smart-money validation on Net active; update verdicts.
6. Probe (`_pup_*`) + spot-check a high-inflow fund (where the gap between price-adj and net-active is
   largest) by hand.

**Logs/resumability:** append to a `FLOW_DECOMPOSITION_LOG.md`; reversible (new fields + display, no
mutation of `analytics.py`, no JS-parity port — display-plane).

### 7.1 Implementation notes from the current engine (read 2026-06-26)
- The price-adjusted flow lives in `_pair_flows()` (`funds_flows.py:168`): `flow = mv_e − mv_s·(1+r)`,
  per `(navindia_code = scheme, vst_id = stock)`. This is **figure #2** and is the shared core for both
  `stock_flows()` and the fund-level views — change it carefully (one source of truth).
- **★ It restricts to `common` = stocks held in BOTH months** (`set(a)&set(b)`), so **entries (fresh buys)
  and full exits are dropped** — yet those are the *largest* active decisions. Net-active (#3) must include
  them: an entry = `w(t)=0 → Δw_active = w(t+1)` (full conviction add); an exit = `w(t+1)=0 → Δw_active =
  −w(t)(1+r)/(1+R_p)` (full distribution). So the net-active path needs the **full per-fund book**, not the
  intersection.
- **Net-active needs two per-fund aggregates** the current core does not carry: `AUM_eq(t) = Σ_i mv_s` over
  the fund's whole equity book, and the drift return `R_p = Σ_i w_i(t)·r_i` (weights over the full book).
  Compute these per `navindia_code` first, then `Δw_active(i)` and `active_flow_i` per holding.
- **Reuse the merger bridge + CA quarantine** from `stock_flows()` (`_detect_merger_pairs`, `_load_ca_events`)
  unchanged — net-active inherits corp-action immunity.
- Suggested shape: a new `_pair_flows_active(ym_to, h, ret)` that returns per-(fund,stock)
  `{gross, price_adj, net_active}` over the full book, with `stock_flows()` / `build_stock_series()` /
  `build_fund_series()` gaining a `basis ∈ {gross, price_adj, net_active}` arg (default `net_active` for
  signal contexts, `price_adj` retained for back-compat where a panel asks for it). Bake all three figures
  into the per-stock Quant block so the front-end toggle is a pure display switch.

---

## 8. FLAGGED

1. **Price-adjusted flow overstates accumulation in net-inflow regimes** by `F·w_i` per name — material in
   2024–26. Do not present it as conviction.
2. **Net active flow is the only conviction figure**, and still needs the lead-lag/Granger gate before it
   is an actionable buy.
3. **Rupee convention for net-active is stated** (post-drift book size) — keep it consistent across panels;
   the weight-space `Δw_active` is convention-free, the ₹ scaling is the only choice.
4. **Build on the existing corp-action-immune pairing + merger bridge** — do not reintroduce split/merger
   artifacts.
5. **Equity-sleeve normalisation** — weights over the equity book only; cash/debt inflows never enter
   (consistent with the analyser's equity-renormalisation rule).
