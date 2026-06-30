# Skill-Engine Build — the LOCKED contract

> The build-side companion to `SKILL_ENGINE_REDESIGN.md` (the design spec). This doc LOCKS the file
> map, the EXACT shared data shapes every `vistas/skill_*.py` module produces/consumes, the single
> integration point, and the non-negotiable guardrails. Scaffold drafted 2026-06-30 by a grounded
> agent that read the full spec + the named existing code before writing a line. STATUS: contract
> locked, modules are skeletons (signatures + docstrings + `NotImplementedError`, no logic yet). The
> build fills the bodies phase by phase, audit-before-publish. Pairs with `FUNDAMENTAL_LAW.md`,
> `FUND_MANAGER_ANALYSER_DESIGN.md`; disciplines: `signal-backtest`, `flag-validation`,
> `first-principles-thinking`.

---

## 0. What this build is (one paragraph)

Replace the NAV-IR·√years binary skill gate (needs 15-25y of history, too cheap, gross,
un-deflated, no multiple-testing) with a **bet-level → Bayesian-posterior → honesty-rails** engine
that is judgeable in ~1-2y and works for NFOs. It measures skill at the **holdings cross-section**
and the **trades** (hundreds of bets/yr, not ~2), shrinks each fund's noisy estimate to its
category prior, maps it to a net %/yr posterior with `P(skilled)`, and gates honesty with
factor/sector deflation + net-of-fee + FDR. The output is a per-fund `skill` JSON block
(`schema_version:2`) that **adds to**, never replaces, the legacy keys. Honest ceiling: the signals
are weak (holding-IC split-half persistence 0.097) → the win is **honesty + ranking, not magic**;
the gross 20.5% "skilled" is expected to fall to ~3-7% defensible.

---

## 1. The file map (all NEW `vistas/skill_*.py`; additive)

| File | Component / Rail (spec §) | Role |
|---|---|---|
| `vistas/skill_factors.py` | C / **Rail 1** (L388-422) | Factor library (MKT/SMB/HML/WML/QMJ monthly long-short legs) + `build_sector_leg` + `deflate(A_monthly, legs, sector_S=None)` → residual α + Newey-West SE + t + betas + R² + leg_corr. |
| `vistas/skill_signals.py` | **A** (L13-213) | Per-fund high-breadth signals → signal tuples: cleaned holding-IC (peer-consensus route 2), trade-alpha (Add-vs-Trim + IC-of-trades on `funds_flows` `net_active`/`dw_active` vs fwd ret), batting vs empirical-null. `effective_breadth`. Slug = retired-to-diagnostic. |
| `vistas/skill_posterior.py` | **B** (L219-374; tag L630-644) | Empirical-Bayes category prior + normal-normal shrinkage + posterior + `p_skilled` + IC→%/yr Fundamental-Law mapping + the 5-state tag. `_norm_cdf`. |
| `vistas/skill_rails.py` | C / **Rails 2-3** (L426-470) | Net-of-fee (category-median TER proxy, flagged) + FDR Benjamini-Hochberg(q=0.10) + BSW Storey π0. |
| `vistas/skill_engine.py` | **integrator** (D.1 L534-616) | `compute_skill(...)` wires A→C.deflate→B→C.FDR→D into the `skill` block; `fdr_and_rank` (panel-relative) + `manifest_fields` + `apply_to_record` build-hook helpers. |
| `vistas/skill_validate.py` | **D.2** (L662-695) | Walk-forward OOS decile-spread harness (no look-ahead; block-bootstrap CI + within-category label-shuffle null). |
| `vistas/skill_audit.py` | **D.3.1** (L699-732) | Before/after migration audit generator → `output/SKILL_MIGRATION_AUDIT.md`. |

**Grounded existing code each module reads (the substrate — already built):**
- `funds_attribution.load_panel` (the scheme-month panel `A=rp−rb`, per-month equity weights `w`,
  the monthly `_ic` holding-rank IC L122-127, `port_hit_*`/`port_slug_*` L142-168, the `_CAT_BENCH`
  category-benchmark map) · `scheme_metrics` (`ic_mean`/`ic_t` L215-217, `info_ratio`/`t_stat` L211-212,
  the per-month `ts[]` block with key `"ic"`, the verdict tree, `basis` string) · `_block_bootstrap_mean`
  (circular block bootstrap, the autocorr-honest SE source) · `build_all`/`_manifest.json`.
- `funds_flows._pair_flows_active` (the drift-adjusted active trade `dw_active`/`net_active`,
  inflow-immune + CA-bridged, L224-229) · `stock_active_flows` · `build_stock_series` ·
  `build_active_share` (the ex-self AUM-weighted peer consensus `cons = exagg/extotal` L699 — the
  cleaned-IC `Ŵ_i`).
- `amc_replay._THEME_BENCHMARK`/`_bench_for` (sector-TR-index map for thematic sector deflation) ·
  `_tc_sample` (TC proxy) · `_spearman`/`_pearson` · `scorecard` fundamental-law block (IC·√BR·TC
  conventions to mirror).
- Data: `data/funds/history/holdings_history.parquet`, `tr_returns_monthly.parquet` (TOTAL return,
  incl. delisted), `data/funds/_amfi_nav_panel.parquet` (survivorship-free NAV — the OOS outcome),
  `data/fundamentals_annual_consolidated.csv` (cols `sym,fy,sales,pat,networth,total_assets,total_debt,
  capex` — value/quality legs), `data/Stocks Data TR till <date>.csv` (4308-stock daily TR — momentum
  leg + monthly returns + turnover size proxy). Confirmed: `scheme_master.json`/`_amfi_census.json`
  carry NO TER field → Rail 2 runs on the flagged category-median proxy.

---

## 2. The EXACT shared data shapes (LOCKED)

### (a) Per-fund SIGNAL TUPLE — `skill_signals.*` → `skill_posterior`
```python
{
  "name":         str,    # "trade_ic" | "holding_ic_cons" | "add_minus_trim" | "batting"
  "x_hat":        float,  # point estimate on the signal's native axis (an IC, a spread, a rate)
  "se":           float,  # standard error — Newey-West HAC / block-bootstrap, NOT naive 1/√T
  "T":            int,    # # monthly cross-sections in the window (effective sample, months)
  "n_bets_eff":   float,  # EFFECTIVE breadth = N/(1+(N-1)·ρ̄) × periods  (independent bets, NOT N×T)
  "n_bets_naive": int,    # raw bet count N×T (honesty: how much breadth correlation ate)
  "rho_bar":      float,  # avg pairwise active-bet correlation used to deflate breadth
  "fm_t":         float,  # Fama-MacBeth t = x_hat/(std_t/√T) on the RAW monthly series
  "route":        str,    # "peer-consensus-demeaned" | "raw-cap-tilt" | "dw_active" | ...
  "caveats":      list[str],  # ["cap-tilt-contaminated (no W-HIST)","trim-leg long-only lower-bound",
                              #  "slug=look-ahead diagnostic-only","scheme-level (may blend managers)"]
}
# optional series alongside (return_series=True): {"ym":[...], "x":[...]}
```

### (b) FACTOR-LEG FRAME + `deflate()` return — `skill_factors`
```python
# legs  (build_factor_legs):
#   pandas.DataFrame, index = month-end DatetimeIndex (ascending),
#   cols = EXACTLY ["MKT","SMB","HML","WML","QMJ"], values = decimal monthly long-short leg returns (NaN ok)
# sector_S (build_sector_leg, optional, thematic): pandas.Series, SAME month-end index,
#   value = sector_TR_ret − NIFTY500_ret  (None for diversified / no sector match)

deflate(A_monthly, legs, sector_S=None, gross_excess_ann=None, nw_lag=3) -> {
  "alpha":              float,   # MONTHLY residual α (intercept), decimal/month
  "alpha_ann":          float,   # annualised ((1+alpha)**12 − 1), decimal/yr
  "se_nw":              float,   # Newey-West HAC SE of the MONTHLY α
  "t":                  float,   # skill_t = alpha / se_nw   (THE rail verdict statistic)
  "betas":              {"MKT":float,"SMB":float,"HML":float,"WML":float,"QMJ":float[,"SECTOR":float]},
  "r2":                 float,
  "leg_corr":           {"MKT":{...},...},   # leg×leg Pearson correlation matrix (collinearity audit)
  "n_obs":              int,
  "nw_lag":             int,
  "factor_alpha_share": float|None,          # alpha_ann / gross_excess_ann (share of gross that SURVIVED)
  "factor_deflated":    True,                # provenance stamp
  "sector_deflated":    bool,                # True iff a SECTOR regressor was included
  "caveats":            list[str],
  "ok":                 bool,                # False (reason in caveats) when n_obs < 24
}
```

### (c) CATEGORY PRIOR TABLE — `skill_posterior.build_category_prior` (baked as DATA for JS parity)
```python
{
  "<sebi_category>": {"prior_mean": float, "prior_sd": float, "n_peers": int, "n0": float},
  ...,
  "_universe":       {"prior_mean": μ0,    "prior_sd": τ0,    "n_peers": N,   "n0": float},  # grand backstop
}
# prior_sd = √τ², τ² = Var_xsec(x̂) − E[s²] (method-of-moments BETWEEN-fund true-skill var, floored >0)
# n0 = prior pseudo-count; shrinkage = n_eff/(n_eff+n0)
# LIVE defaults (baked): μ0=+0.43 IR; category-IR means Multi 0.76, Value 0.57, Large 0.49, Flexi 0.47,
#   Small 0.45, ELSS 0.43, Focused 0.40, Mid 0.29; true-skill var≈0.002 (SD≈0.05); monthly-IC noise SD 0.186
```

### (d) TER PROXY TABLE — `skill_rails.TER_PROXY` (FLAGGED category-median proxy, baked DATA)
```python
{
  "<sebi_category>": {"regular": float, "direct": float},   # annual TER, decimal/yr
  ...,
  "_default": {"regular": 0.019, "direct": 0.008},          # SEBI tiered-TER ranges; per-cap overrides in build
}
# net_basis is ALWAYS "category-median-proxy" until a real SEBI/AMFI TER feed (vistas/funds_ter.py) lands.
# plan (direct|regular) is DETECTED from the scheme name.
```

### (e) THE FULL D.1 `skill` DICT — `skill_engine.compute_skill` → `record["skill"]`
```python
{
  "schema_version": 2,
  "posterior": {            # skill_posterior.posterior() — the headline (net-of-fee, factor-deflated %/yr)
    "metric":"net_active_cagr","best":float,"lo90":float,"hi90":float,"lo50":float,"hi50":float,
    "sd":float,"p_skilled":float,"p_strong":float,"prior_mean":float,"prior_sd":float,
    "shrinkage":float,"basis":str },
  "tag":"likely_skilled","tag_label":"Likely skilled","tag_why":str,   # skill_posterior.skill_tag()
  "rank": {"basis":"p_skilled","within":"<category>","n_peers":int,"decile":int,"pctile":int},  # build hook
  "bet_level": {            # Component A holding-IC tuple, shrunk
    "ic":float,"ic_sd":float,"ic_t":float,"n_bets_eff":int,"n_bets_naive":int,"rho_bar":float,
    "ic_source":"holdings-cross-section (active-weight vs fwd residual return)" },
  "trade_alpha": {          # Component A trade tuples
    "ic":float,"ic_t":float,"add_minus_trim":float,"n_trades_eff":int,
    "source":"funds_flows.net_active (dw_active), inflow-immune, corp-action-bridged" },
  "nav_corroborator": {     # the SLOW NAV-IR, demoted GATE→CHECK
    "info_ratio":float,"t_stat":float,"years":float,"years_needed":int,
    "agrees_with_posterior":bool,"status":"confirms|contradicts|uninformative_yet","role":str },
  "rails": {                # provenance + every honesty flag
    "fee_adjusted":bool,"ter_annual":float,"net_basis":"category-median-proxy",
    "factor_deflated":bool,"factors":["MKT","SMB","HML","WML","QMJ"],"factor_alpha_share":float,
    "sector_deflated":bool,"fdr_q":0.10,"passes_fdr":bool,"fdr_note":str,
    "benchmark_sensitivity":"low|med|high","manager_tenure_contaminated":bool,"caveats":list[str] },
  "as_of":"YYYY-MM","n_months":int,"build_id":"YYYY-MM-DD","definition":str
}
```
`_manifest.json[<code>]` gains (via `manifest_fields`): `tag, p_skilled, post_best, post_lo90,
post_hi90, decile, ic_t, nav_ir, n_months, verdict`(= `tag` mirror so old code won't crash).
`_envelopes.json` gains a per-category `posterior_rank` block (same shape as the existing vantage
envelopes). `passes_fdr` and `rank.*` are panel-relative → filled by `fdr_and_rank`, not `compute_skill`.

### (f) THE INTEGRATION POINT (the ONLY edit to an existing file, in the Integrate phase)
```python
funds_attribution.build_all(..., posterior: bool = False)
```
Additive: when `posterior=True`, after `scheme_metrics`, for each kept fund call
`skill_engine.compute_skill(...)` + `apply_to_record` (→ `record["skill"]`, legacy keys retained),
then `skill_engine.fdr_and_rank` over all funds (fills `passes_fdr` + `rank`), then merge
`manifest_fields` into the manifest entry. `posterior=False` (the default) leaves today's output
**byte-for-byte unchanged**. No consumer default flips.

---

## 3. HARD GUARDRAILS (every agent, every phase — NON-NEGOTIABLE, "no score for error")

1. **ADDITIVE only.** Create new `vistas/skill_*.py` files. The ONLY edit to an existing file is the
   additive `build_all(posterior=...)` hook, made in the Integrate phase. Nothing else is mutated.
2. **NO publish, NO git commit/push.** Do NOT run `publish_terminal.py` / `pipeline.py` / any `*.bat`.
3. **Do NOT flip any consumer default.** `fsScorecardHTML` / `fsLeaderboardHTML` / `amc_context.packf`
   stay on the LEGACY verdict.
4. **Do NOT touch `static/vistas.js`.** The JS parity port is OUT OF SCOPE — a human does it after,
   under the parity harness (`_parity_dump.py` / `_parity_check.js` / `_deck_runtime_test.js`).
5. **TR pipeline is SACRED.** Never touch `vistas/data.py`, `vistas/analytics.py`, or the TR
   serve/deck path.
6. **Legacy `funds_attribution` JSON keys MUST stay intact.** `schema_version:2` ADDS a `skill`
   block; it removes nothing.
7. **Do NOT run the full 740-fund `build_all`; do NOT take `data/_refresh/.build.lock`.** Smoke-test
   by calling functions on a HANDFUL of funds directly. One build at a time is a hard rule — two =
   silent death.
8. **HONESTY: every rail may ONLY ever LOWER skill, never raise it; flag every gap inline.** Required
   inline flags: `factor_deflated`, `net_basis="category-median-proxy"`, W-HIST cap-tilt contamination
   (`bet_level.ic` is a cap-tilt-contaminated proxy until point-in-time index weights exist),
   scheme-level / manager-blended (`manager_tenure_contaminated`), trim-leg long-only lower-bound
   (trade-alpha is a LOWER BOUND on true forecasting skill — the transfer-coefficient leak), slug =
   look-ahead diagnostic-only. Ground EVERYTHING in the real code/data BEFORE writing a line.

---

## 4. Build order (each phase audit-before-the-next; skeletons first, now done)

1. **Skeletons** (DONE) — all 7 `skill_*.py` with signatures + docstrings + `NotImplementedError`;
   this contract doc locked.
2. **Rails-first** (spec discipline): `skill_factors` (legs + deflate) → `skill_rails` (net-of-fee +
   FDR) — the parts that can only LOWER skill, validated against the live numbers on a handful of funds.
3. **Signals** `skill_signals` (cleaned holding-IC, trade-alpha, batting null) — smoke-tested on a few
   funds against the existing `_ic` / `net_active` substrate.
4. **Posterior** `skill_posterior` (prior + shrinkage + tag) — closed-form, checked against the §2
   worked example (Fund X Mid-Cap → P(skilled)=0.74).
5. **Integrate** `skill_engine.compute_skill` + the additive `build_all(posterior=True)` hook — run on
   a handful of funds, never the full panel, never the lock.
6. **Validate** `skill_validate` (pre-registered OOS, prereg SHA-stamped first) → `output/SKILL_POSTERIOR_VALIDATION.md`.
7. **Audit** `skill_audit` → `output/SKILL_MIGRATION_AUDIT.md`. Human gate (KV reads both).
8. *(Later, separate workstream, NOT this build)* — JS parity port + consumer-default flip + publish.
