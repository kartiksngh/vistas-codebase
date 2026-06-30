"""
skill_posterior.py — Component B: the Bayesian posterior layer (spec Component B, L219-374;
the 5-state tag, L630-644).

WHAT THIS IS (one breath)
-------------------------
Component A hands B, per fund, a NOISY point estimate of skill PLUS how noisy it is: (x̂, s, T).
B's only job is to answer what an allocator actually asks about a 1-2-year-old fund:
  "Given this fund's short noisy record AND everything we know about how funds like it behave, what
   is our honest best guess of its true skill, how uncertain are we, and what is P(it is genuinely
   skilled — not lucky)?"
The answer is a POSTERIOR DISTRIBUTION: a best-guess (posterior mean) wrapped in an error bar
(credible interval), from which we read P(skilled) = P(true skill > θ* | data). The machinery is
SHRINKAGE: pull each fund's noisy estimate toward a PRIOR built from its peers, by an amount set by
how trustworthy that fund's own number is. A brand-new fund is mostly prior with wide bars; a fund
with a long record is mostly its own record with tight bars. Nothing is invented — the wide bars on
a young fund are the honest "we don't know yet," not a hidden bet.

THE MODEL (empirical-Bayes / James-Stein partial pooling):
  Prior hierarchy (universe → SEBI category → AMC×style), each level shrunk into the next so thin
  levels borrow strength (§1.2). Per-fund prior mean is a precision-weighted ladder
      μ_f = α_amc·m_amc + α_cat·m_cat + α_uni·μ0,   Σα=1,  α_level ∝ peers_level/spread_level²
  Prior variance τ_f² = the BETWEEN-fund TRUE-skill variance at the tightest level with enough peers,
  estimated by method-of-moments: τ² = Var_xsec(x̂) − E[s²]  (the §358/L763 estimator).
  Normal-normal conjugate posterior (§1.3, L271-279):
      w_f = (1/s²)/(1/s² + 1/τ²) = τ²/(τ²+s²)        # shrinkage weight = own-record fraction
      posterior_mean = w_f·x̂ + (1−w_f)·μ_f
      posterior_var  = 1/(1/s² + 1/τ²) = (s²τ²)/(s²+τ²)   # always smaller than both
      95% CI = posterior_mean ± 1.96·√posterior_var  (90%: ±1.645)
      P(skilled) = Φ( (posterior_mean − θ*) / √posterior_var )

IC→%/yr MAPPING (Fundamental-Law atom, §D.1 L766): turn a per-bet IC into an annual active return so
the posterior is on a NET-%/YR axis an allocator can read:
      data_est (net %/yr) = TC·IC·√BR_eff·ω_active − TER
  TC = transfer coefficient (long-only leak, ~0.3-0.6), ω_active = active-risk size, BR_eff from
  skill_signals.effective_breadth. The constants are ESTIMATED and category-dependent → a MODEL, not
  an identity; the OOS test (skill_validate) is what validates the end-to-end mapping. Report the
  constants (prior_mean, shrinkage, …) so it is auditable; run ±50% sensitivity on TC/ω.

★ SELF-SKEPTICISM (§3.5, L326). Our LIVE decomposition is sobering and we report it openly: at the
NAV-IR level true-skill var ≈ 0.002 (SD≈0.05); today's holding-rank IC has split-half persistence
only 0.097 / universe reliability ~0.7%. This is NOT a flaw in B — it is its JUSTIFICATION: until
Component A delivers a higher-reliability bet-level signal, τ MUST be small so the layer correctly
REFUSES to crown young funds. As A's reliability rises (same split-half test), τ is re-estimated
UPWARD and the crossover to "the manager's own record" comes sooner — data-driven, not hoped-for.
The honest ceiling: signals are weak → honesty + ranking, not magic.

MEASURED UNIVERSE PARAMS (live, §4 L330-348 — defaults baked as DATA so JS can read the same numbers):
  μ0 (prior mean IR) +0.43; Var(IR) 0.160; E[1/years] 0.158 → true-skill var ≈0.002 (SD≈0.05);
  category means: Multi 0.76, Value 0.57, Large 0.49, Flexi 0.47, Small 0.45, ELSS 0.43, Focused 0.40,
  Mid 0.29; between-cat var 0.015; monthly IC noise SD 0.186.

DISPLAY-PLANE only — additive, no analytics.py touch. The closed-form shrinkage + _norm_cdf are the
ONLY logic a later human ports to JS; the prior table + mapping constants ship as DATA (like
VISTAS_MACRO) so Python and JS read identical numbers.

--------------------------------------------------------------------------------------------------
SHARED DATA CONTRACT (locked in SKILL_ENGINE_BUILD.md)
--------------------------------------------------------------------------------------------------
category prior table `prior_table` (the output of build_category_prior; baked as DATA for JS parity):
    {
      "<sebi_category>": {
          "prior_mean": float,   # the category's pooled skill mean on the chosen axis (IR or net %/yr)
          "prior_sd":   float,   # √τ² = between-fund TRUE-skill SD within the category (method-of-moments)
          "n_peers":    int,     # # funds in the category (peer count that sets the ladder weight)
          "n0":         float    # prior pseudo-count = implied prior_sd^-2 strength (shrinkage n_eff/(n_eff+n0))
      }, ...,
      "_universe": {"prior_mean":μ0, "prior_sd":τ0, "n_peers":N, "n0":..},   # the grand backstop
    }

posterior() return dict (the §D.1 "posterior" sub-block, mirrored into the D.1 skill dict by skill_engine):
    {
      "metric":     "net_active_cagr",   # what the estimate is OF (net-of-fee, factor-adjusted ann. active)
      "best":       float,   # posterior MEAN (point estimate), decimal/yr
      "lo90": float, "hi90": float,      # 90% credible interval (best ∓ 1.645·SD)
      "lo50": float, "hi50": float,      # 50% interval (the "likely" band)
      "sd":         float,   # posterior std dev (error-bar half-width proxy)
      "p_skilled":  float,   # P(true metric > θ*_skilled)   ← the calibrated probability of skill
      "p_strong":   float,   # P(true metric > θ*_strong)    (a higher bar, e.g. +2%/yr)
      "prior_mean": float, "prior_sd": float,   # the prior shrunk toward (auditable provenance)
      "shrinkage":  float,   # w_f = own-record fraction (0=all prior, 1=all data)
      "basis":      str,     # human description of the axis + which rails were applied
    }
"""
from __future__ import annotations

import math

# Skill-axis thresholds + mapping constants (locked; baked as DATA for JS parity)
THETA_SKILLED = 0.0          # θ* for P(skilled): true net active > 0  (axis-dependent; see basis)
THETA_STRONG = 0.02          # θ* for P(strong): "materially skilled", true net active > +2%/yr
CI90_Z = 1.645               # z for the 90% credible interval
CI50_Z = 0.674               # z for the 50% "likely" band
# Fundamental-Law IC→%/yr mapping constants (ESTIMATED, category-dependent — a MODEL not an identity)
TC_DEFAULT = 0.45            # transfer coefficient (long-only leak, ~0.3-0.6) — run ±50% sensitivity
OMEGA_ACTIVE = 0.06          # active-risk size ω (tracking-error scale) — category-tunable
# LIVE universe defaults (§4) — used when a fresh empirical-Bayes fit isn't supplied
UNIVERSE_PRIOR_MEAN_IR = 0.43
UNIVERSE_TRUE_SKILL_VAR = 0.002      # var(IR) − E[1/years] = 0.160 − 0.158
MONTHLY_IC_NOISE_SD = 0.186          # median per-fund SD of monthly IC → SE(mean IC) = 0.186/√months
_CATEGORY_PRIOR_IR = {               # §340 (baked DATA; re-estimated walk-forward in the validator)
    "Multi Cap Fund": 0.76, "Value Fund": 0.57, "Large Cap Fund": 0.49, "Flexi Cap Fund": 0.47,
    "Small Cap Fund": 0.45, "ELSS": 0.43, "Focused Fund": 0.40, "Mid Cap Fund": 0.29,
}


def _norm_cdf(z: float) -> float:
    """Standard-normal CDF Φ(z) (no scipy). The ONLY non-trivial bit of logic a later human ports to
    JS (a ~5-line erf-based _normCdf), so the convention MUST match exactly. Returns Φ(z)∈[0,1].

    Φ(z) = ½·(1 + erf(z/√2)). math.erf is the C library erf (≈1e-16 accurate); the JS port must use an
    Abramowitz-Stegun erf with the SAME ½(1+erf(z/√2)) wrapping so the two agree to ≤1e-6 (the parity
    bar in spec D.1.4). Guards: non-finite z → 0.5 (no information), so a missing-SE fund never crashes.
    """
    if z is None or not math.isfinite(z):
        return 0.5
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def ic_to_annual(ic: float, br_eff: float, tc: float = TC_DEFAULT, omega: float = OMEGA_ACTIVE,
                 ter: float = 0.0) -> float:
    """IC→annual-active mapping (Fundamental-Law atom, §D.1 L766):
        net %/yr = TC·IC·√BR_eff·ω_active − TER.
    Turns a per-bet skill correlation into an annual NET active return. A MODEL (assumes the law's
    5 assumptions), validated end-to-end by skill_validate, not asserted. Returns decimal/yr.

    data_est (net %/yr) = TC · IC · √BR_eff · ω_active − TER   (spec §D.1 L766).
      IC      = per-bet information coefficient (forecast↔outcome corr; ~0.05 is good).
      BR_eff  = EFFECTIVE breadth = N/(1+(N−1)·ρ̄) × months (independent bets), from
                skill_signals.effective_breadth — NOT N×T (correlated bets shrink breadth).
      TC      = transfer coefficient (long-only / constraint leak, ~0.3-0.6) — the hidden reason a
                skilled manager looks mediocre (Clarke-de Silva-Thorley 2002).
      ω       = active-risk size (tracking-error scale).
      TER     = annual fee, subtracted LAST so the axis is NET (rail 2; only ever lowers).

    HONESTY: every term ≥0 except −TER, so the fee can ONLY lower the estimate; a non-finite or
    non-positive br_eff yields the bare prior-friendly 0.0 (no fabricated alpha). The constants
    (TC, ω) are ESTIMATED and category-dependent → a MODEL not an identity; skill_validate is what
    validates the end-to-end mapping. Returns decimal/yr.
    """
    if ic is None or br_eff is None or not math.isfinite(ic) or not math.isfinite(br_eff) or br_eff <= 0:
        return -float(ter or 0.0)
    return tc * ic * math.sqrt(br_eff) * omega - float(ter or 0.0)


def build_category_prior(scheme_recs: "object", axis: str = "info_ratio",
                         min_peers: int = 8, se_mode: str = "sampling") -> dict:
    """Empirical-Bayes prior, estimated ONCE from the cross-section of all funds with NO look-ahead
    (§3.2). Per SEBI category: prior_mean = pooled mean of the axis; prior_sd = √τ² where
    τ² = Var_xsec(x̂) − E[s²] (method-of-moments between-fund TRUE-skill variance, floored at a small
    positive); n_peers; n0 = prior pseudo-count. Plus a "_universe" grand backstop (μ0, τ0). Thin
    categories (n_peers<min_peers) borrow the universe level. Returns the locked `prior_table`.
    (For the walk-forward OOS test the prior MUST be re-fit on data ≤ t — never full-sample.)

    ---------------------------------------------------------------------------------------------
    METHOD (method-of-moments empirical Bayes, no look-ahead, closed-form):
      For each level (universe, then per SEBI category):
        prior_mean = mean of the axis x_hat across the peers (equal-weight; the pooled location).
        tau^2 (BETWEEN-fund TRUE-skill variance) = Var_xsec(x_hat) - E[s^2]   (spec L358 / L763).
            i.e. the cross-sectional spread of the estimates MINUS the average estimation noise,
            because Var(x_hat) = Var(true theta) + E[Var(noise)] for independent noise. Floored at a
            small positive (1% of the universe between-fund var, >=1e-6) so shrinkage is always defined.
        prior_sd = sqrt(tau^2);  n0 = prior pseudo-count = (universe-noise s^2)/tau^2 (a wide prior
            counts as little data; a tight prior dominates a thin record) for the n_eff/(n_eff+n0) read.

      Thin categories (n_peers < min_peers) are NOT emitted as their own level; prior_for_fund then
      falls back up the ladder to _universe (partial pooling: thin levels borrow strength).

    INPUTS (robust to both call sites):
      - a pandas.DataFrame from scheme_metrics (cols sebi_category/category, info_ratio, ...), OR
      - an iterable of dict records (the funds_attribution/*.json schema).
      Each record contributes (category, x_hat on `axis`, s) where s = the per-fund SE on the axis.

    ★ se_mode — which SE feeds the method-of-moments E[s^2] (the noise term subtracted to get tau^2):
      "sampling" (DEFAULT): use the IR's THEORETICAL sampling SE 1/sqrt(years) for axis=='info_ratio'.
          This is the spec-section-4 estimator E[s^2] = E[1/years] (under the null IR~N(theta, 1/years)),
          which reproduces the published true-skill var ~0.002 / SD ~0.05. It is the right term for a
          UNIVERSE-LEVEL between-fund variance because it is unbiased and not inflated by a handful of
          short-history funds' noisy bootstrap SEs (which, averaged, can exceed Var(IR) and drive tau^2
          negative — exactly the self-skepticism trap in spec L326 if used here).
      "bootstrap": use the autocorr-honest bootstrap-derived IR SE (se_IR from the boot CI on mean-A).
          More honest PER FUND, but at the universe level it slightly over-states E[s^2] (bootstrap
          E[s^2]=0.162 > Var(IR)=0.160 on this universe) → tau^2 floors to ~0. Available for audit /
          when Component A supplies higher-reliability bet-level SEs (then tau^2 is re-estimated upward).
      NOTE the asymmetry the spec intends: tau^2 (the universe shape) uses the SAMPLING term, but each
      fund's POSTERIOR uses its OWN bootstrap SE (passed separately to posterior()) — section-1.1's
      autocorr-honest s, NOT 1/sqrt(T). Two different SEs for two different jobs, both honest.
    Pure/closed-form, deterministic, JS-portable as DATA.
    """
    recs = _as_records(scheme_recs)
    rows = []
    for r in recs:
        x = _axis_value(r, axis)
        if x is None or not math.isfinite(x):
            continue
        s = _se_for_prior(r, axis, se_mode)
        cat = (r.get("sebi_category") or r.get("category") or "Uncategorized")
        amc = r.get("amc")
        rows.append((cat, amc, float(x), (float(s) if (s is not None and math.isfinite(s)) else None)))

    if not rows:
        return {"_universe": {"prior_mean": UNIVERSE_PRIOR_MEAN_IR,
                              "prior_sd": math.sqrt(UNIVERSE_TRUE_SKILL_VAR),
                              "n_peers": 0, "n0": 1.0}}

    def _level(level_rows):
        xs = [x for (_, _, x, _) in level_rows]
        ss = [s for (_, _, _, s) in level_rows if s is not None and math.isfinite(s)]
        mean = sum(xs) / len(xs)
        var_x = (sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0
        e_s2 = (sum(s * s for s in ss) / len(ss)) if ss else 0.0
        tau2 = var_x - e_s2
        return mean, var_x, e_s2, tau2, len(xs)

    u_mean, u_var_x, u_e_s2, u_tau2, u_n = _level(rows)
    # universe true-skill var: floor only at a tiny positive so shrinkage is defined (never zero-div).
    abs_floor = 1e-6
    u_tau2 = max(u_tau2, abs_floor)
    table = {"_universe": {"prior_mean": round(u_mean, 6), "prior_sd": round(math.sqrt(u_tau2), 6),
                           "n_peers": u_n,
                           "n0": round((u_e_s2 / u_tau2) if u_tau2 > 0 else 1.0, 4)}}

    # A category's true-skill spread should not be claimed TIGHTER than the universe's just because a
    # noisy MoM estimate went negative — that would let a category over-shrink its funds. So when a
    # category's MoM tau^2 falls below the universe tau^2, it BORROWS the universe spread (partial
    # pooling, spec L249-269: thin/noisy levels lean on the fat level). It may exceed it only when the
    # category genuinely shows more between-fund spread than the universe (e.g. Small/Value/Focused).
    by_cat: dict = {}
    for row in rows:
        by_cat.setdefault(row[0], []).append(row)
    for cat, crows in by_cat.items():
        if len(crows) < min_peers:
            continue
        mean, var_x, e_s2, tau2, n = _level(crows)
        tau2 = max(tau2, u_tau2)                 # borrow the universe spread when MoM underflows
        table[cat] = {"prior_mean": round(mean, 6), "prior_sd": round(math.sqrt(tau2), 6),
                      "n_peers": n, "n0": round((e_s2 / tau2) if tau2 > 0 else 1.0, 4)}
    return table


# ---------------------------------------------------------------------------------------------------
# small input-adapter helpers (robust to DataFrame OR list-of-dict; no pandas import required at module
# level so JS-portability is preserved and the module imports cheaply)
# ---------------------------------------------------------------------------------------------------
def _as_records(scheme_recs) -> list:
    """Coerce the prior input into a list of dict records. Accepts a pandas.DataFrame (→ to_dict rows),
    a list/tuple of dicts, or a dict-of-dicts (manifest style)."""
    if scheme_recs is None:
        return []
    if hasattr(scheme_recs, "to_dict") and hasattr(scheme_recs, "columns"):   # DataFrame, no hard import
        return scheme_recs.to_dict(orient="records")
    if isinstance(scheme_recs, dict):
        return [v for v in scheme_recs.values() if isinstance(v, dict)]
    return [r for r in scheme_recs if isinstance(r, dict)]


def _axis_value(r: dict, axis: str):
    """The fund's x_hat on the chosen skill axis. Default axis = 'info_ratio' (the NAV-IR today);
    any numeric field name is accepted so the same machine serves a future net-%/yr axis."""
    v = r.get(axis)
    if v is None and axis == "info_ratio":
        v = r.get("ir")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _se_for(r: dict, axis: str):
    """Per-fund SE on the axis, autocorr-honest first (spec section 1.1). Priority:
       (1) explicit r['se'] / r['x_se'];
       (2) for axis=='info_ratio': the block-bootstrap CI on mean active A mapped to the IR axis;
       (3) naive 1/sqrt(years) fallback.
    Returns None if nothing is derivable (the fund then contributes only to the mean, not to E[s^2])."""
    for k in ("se", "x_se", "se_axis"):
        if r.get(k) is not None:
            try:
                return float(r[k])
            except (TypeError, ValueError):
                pass
    if axis == "info_ratio":
        lo, hi = r.get("boot_meanA_lo"), r.get("boot_meanA_hi")
        ts = r.get("ts") or []
        if lo is not None and hi is not None and ts:
            A = [t.get("A") for t in ts if isinstance(t, dict) and t.get("A") is not None]
            if len(A) > 1:
                m = sum(A) / len(A)
                sd_A = math.sqrt(sum((a - m) ** 2 for a in A) / (len(A) - 1))
                if sd_A > 0:
                    se_meanA = (hi - lo) / (2.0 * 1.96)
                    return se_meanA * math.sqrt(12.0) / sd_A           # ppy=12, monthly panel
        yrs = r.get("years")
        if yrs and yrs > 0:
            return 1.0 / math.sqrt(yrs)
    return None


def _se_for_prior(r: dict, axis: str, se_mode: str):
    """The SE used ONLY for the method-of-moments E[s^2] noise term in build_category_prior.
    se_mode=='sampling' → the theoretical IR sampling SE 1/sqrt(years) (spec-section-4 E[1/years]),
    which reproduces the published universe true-skill var ~0.002 / SD ~0.05. se_mode=='bootstrap' →
    the autocorr-honest bootstrap SE (= _se_for). Any explicit per-fund 'se' field always wins."""
    if r.get("se") is not None or r.get("x_se") is not None or r.get("se_axis") is not None:
        return _se_for(r, axis)
    if se_mode == "bootstrap":
        return _se_for(r, axis)
    # "sampling" (default): theoretical IR sampling SE
    if axis == "info_ratio":
        yrs = r.get("years")
        if yrs and yrs > 0:
            return 1.0 / math.sqrt(yrs)
    return _se_for(r, axis)


def prior_for_fund(category: str, amc: str | None, prior_table: dict) -> tuple:
    """The fund's PRIOR (μ_f, τ_f) via the precision-weighted ladder (§1.2):
        μ_f = α_amc·m_amc + α_cat·m_cat + α_uni·μ0,  α_level ∝ peers_level/spread_level²,  Σα=1.
    Thin AMCs (too few peers) get ~all prior from the category; the category borrows from the universe.
    Today the AMC level degrades to a coarse AMC-name grouping (no tenure/style DB). Returns (mu_f, tau_f).

    ---------------------------------------------------------------------------------------------
    METHOD (precision-weighted partial-pooling ladder, spec §1.2 L249-269):
      mu_f = Σ_level α_level · m_level,    Σα = 1,    α_level ∝ peers_level / spread_level²
    The weight a level earns is proportional to how many independent peers it has and how TIGHT they
    are (small spread² → high precision → more weight). So a category with many peers and a small
    true-skill spread pulls hard; a thin/noisy level barely tilts the prior. Concretely:
      - universe level: always present (the grand backstop _universe), weight ∝ N_uni/τ_uni².
      - category level: present iff build_category_prior emitted it (n_peers ≥ min_peers); else the
        fund borrows the universe directly (partial pooling — thin category leans on the universe).
      - AMC×style level: NOT in today's prior_table (no tenure/style DB) → degrades to category-only,
        EXACTLY as the spec mandates; if a future build adds an "amc::<name>" key the same ladder
        picks it up automatically with no code change.
    τ_f (the prior SD the fund is allowed to leave by) = the tightest level that actually contributed
      (category if present, else universe) — that level's prior_sd. This is the room-to-move number;
      using the tightest available level keeps the bar honest (a fund can't claim a wider prior than
      its peer group genuinely shows). Returns (mu_f, tau_f).
    """
    uni = prior_table.get("_universe") or {"prior_mean": UNIVERSE_PRIOR_MEAN_IR,
                                            "prior_sd": math.sqrt(UNIVERSE_TRUE_SKILL_VAR),
                                            "n_peers": 1, "n0": 1.0}
    levels = []   # (mean, peers, spread2)
    # universe (always)
    u_sd = max(uni.get("prior_sd") or 0.0, 1e-4)
    levels.append((uni["prior_mean"], max(uni.get("n_peers") or 1, 1), u_sd * u_sd))
    tau_f = u_sd                                                  # tightest contributing level's SD
    # category (if the table has it)
    cat_rec = prior_table.get(category) if category else None
    if cat_rec:
        c_sd = max(cat_rec.get("prior_sd") or 0.0, 1e-4)
        levels.append((cat_rec["prior_mean"], max(cat_rec.get("n_peers") or 1, 1), c_sd * c_sd))
        tau_f = c_sd
    # amc×style (only if a future build added it; key convention "amc::<name>")
    if amc:
        amc_rec = prior_table.get("amc::" + str(amc))
        if amc_rec:
            a_sd = max(amc_rec.get("prior_sd") or 0.0, 1e-4)
            levels.append((amc_rec["prior_mean"], max(amc_rec.get("n_peers") or 1, 1), a_sd * a_sd))
            tau_f = a_sd
    # precision-weighted blend  α ∝ peers/spread²
    wts = [peers / spread2 for (_, peers, spread2) in levels]
    W = sum(wts)
    mu_f = sum(w * m for w, (m, _, _) in zip(wts, levels)) / W if W > 0 else uni["prior_mean"]
    return (float(mu_f), float(tau_f))


def posterior(x_hat: float, s: float, mu_f: float, tau_f: float,
              theta_skilled: float = THETA_SKILLED, theta_strong: float = THETA_STRONG,
              basis: str | None = None) -> dict:
    """★ The normal-normal conjugate posterior (§1.3) — the headline.
        w = τ²/(τ²+s²);  best = w·x̂ + (1−w)·μ_f;  var = (s²τ²)/(s²+τ²)
        lo90/hi90 = best ∓ 1.645·√var;  lo50/hi50 = best ∓ 0.674·√var
        p_skilled = Φ((best−θ_skilled)/√var);  p_strong = Φ((best−θ_strong)/√var)
    s MUST be Component A's bootstrap/Newey-West SE (NOT 1/√T — that would understate s and make us
    over-confident). The wide young-fund bar IS the feature. Returns the locked posterior() dict.

    Closed-form normal-normal (conjugate) — the ONLY logic a human later ports to JS:
      tau2 = tau_f²;  s2 = s²
      w     = tau2 / (tau2 + s2)              # shrinkage weight = own-record fraction (0=prior,1=data)
      best  = w·x_hat + (1−w)·mu_f            # posterior mean
      var   = (s2·tau2) / (s2 + tau2)         # posterior variance (always < both s² and tau²)
      sd    = sqrt(var)
      lo/hi(90) = best ∓ 1.645·sd ;  lo/hi(50) = best ∓ 0.674·sd
      p_skilled = Φ((best − theta_skilled)/sd) ;  p_strong = Φ((best − theta_strong)/sd)
    Guards: a non-finite/zero sd → p collapses to 0/1 cleanly via _norm_cdf's ±inf handling; a missing
    x_hat (None) leaves the fund AT its prior (w=0) — the honest "we have no own-record signal".
    """
    tau2 = float(tau_f) ** 2 if (tau_f is not None and math.isfinite(tau_f)) else 0.0
    # missing own-record estimate → sit at the prior with the prior's width (w=0)
    if x_hat is None or not math.isfinite(x_hat) or s is None or not math.isfinite(s) or s <= 0:
        w = 0.0
        best = float(mu_f)
        var = tau2
    else:
        s2 = float(s) ** 2
        if tau2 <= 0:                       # degenerate prior: trust the data fully
            w, best, var = 1.0, float(x_hat), s2
        else:
            w = tau2 / (tau2 + s2)
            best = w * float(x_hat) + (1.0 - w) * float(mu_f)
            var = (s2 * tau2) / (s2 + tau2)
    sd = math.sqrt(var) if var > 0 else 0.0

    def _ci(z):
        return (best - z * sd, best + z * sd)
    lo90, hi90 = _ci(CI90_Z)
    lo50, hi50 = _ci(CI50_Z)
    if sd > 0:
        p_skilled = _norm_cdf((best - theta_skilled) / sd)
        p_strong = _norm_cdf((best - theta_strong) / sd)
    else:                                   # no uncertainty → step function at the point estimate
        p_skilled = 1.0 if best > theta_skilled else 0.0
        p_strong = 1.0 if best > theta_strong else 0.0

    return {
        "metric": "net_active_cagr",
        "best": round(best, 6), "lo90": round(lo90, 6), "hi90": round(hi90, 6),
        "lo50": round(lo50, 6), "hi50": round(hi50, 6), "sd": round(sd, 6),
        "p_skilled": round(p_skilled, 4), "p_strong": round(p_strong, 4),
        "prior_mean": round(float(mu_f), 6), "prior_sd": round(math.sqrt(tau2) if tau2 > 0 else 0.0, 6),
        "shrinkage": round(w, 4),
        "basis": basis or ("normal-normal empirical-Bayes posterior on the info-ratio axis "
                           "(NAV-IR today; net-of-fee + factor-deflated once Component A/C feed it)"),
    }


def skill_tag(p_skilled: float, lo90: float, hi90: float, passes_fdr: bool,
              n_months: int, tracking_error: float) -> tuple:
    """The 5-STATE TAG (§D.1.3, L630-644) — replaces the binary verdict with a posterior-probability
    ladder. Returns (tag, tag_label, tag_why). Rules (most-specific first):
        index-like           : tracking_error < 0.02
        insufficient_history : n_months < 12   (was 24 — the high-breadth posterior works from ~1y)
        skilled              : p_skilled ≥ 0.90  AND lo90 > 0  AND passes_fdr   (FDR+fee gated → far fewer)
        likely_skilled       : 0.70 ≤ p_skilled < 0.90
        unproven             : 0.40 ≤ p_skilled < 0.70   (CI straddles 0 — the HONEST "we can't tell yet")
        likely_unskilled     : 0.10 < p_skilled ≤ 0.40
        lagging              : p_skilled ≤ 0.10  AND hi90 < 0
    The old 50% limbo is no longer a dead bucket — it spreads across likely_skilled/unproven/
    likely_unskilled by the posterior, each carrying a wide-but-quantified bar + a decile rank.

    Order matters (most-specific gates first): index-like and insufficient_history pre-empt the
    posterior ladder because their data simply isn't fit for the skill question (an index hugger has
    no active bets; a <1y fund has no posterior worth a verdict). Then the p_skilled ladder.
    `skilled` is the ONLY tag that requires the FDR + CI corroboration (lo90>0 AND passes_fdr) — the
    honesty asymmetry: it is HARD to be crowned skilled, easy to be flagged unproven. `lagging`
    additionally requires hi90<0 (the whole bar below zero) so a merely-low p isn't called lagging on
    a wide bar. Returns (tag, tag_label, tag_why).
    """
    def _f(x):
        return (x is not None) and isinstance(x, (int, float)) and math.isfinite(x)

    if _f(tracking_error) and tracking_error < 0.02:
        return ("index-like", "Index-like",
                f"tracking error {tracking_error*100:.1f}% < 2% — effectively a passive hugger, "
                "no active bets to judge")
    if (n_months is None) or (n_months < 12):
        return ("insufficient_history", "Insufficient history",
                f"only {n_months if n_months is not None else 0} months — below the 12-month floor the "
                "high-breadth posterior needs; verdict withheld")

    p = p_skilled if _f(p_skilled) else 0.5
    lo = lo90 if _f(lo90) else float("-inf")
    hi = hi90 if _f(hi90) else float("inf")

    if p >= 0.90 and lo > 0 and passes_fdr:
        return ("skilled", "Skilled",
                f"P(skilled)={p*100:.0f}%, 90% CI above 0, and survives multiple-testing (FDR) — "
                "FDR- & fee-gated, so far fewer than the old gross count")
    if p >= 0.70:
        why = f"P(skilled)={p*100:.0f}% (0.70-0.90)"
        if p >= 0.90 and not passes_fdr:
            why += " — held back from 'skilled' by the FDR multiple-testing gate"
        elif p >= 0.90 and lo <= 0:
            why += " — held back from 'skilled': 90% CI still straddles 0"
        return ("likely_skilled", "Likely skilled", why + ", wide-but-quantified bar")
    if p >= 0.40:
        return ("unproven", "Unproven",
                f"P(skilled)={p*100:.0f}% — the credible interval straddles 0; statistically "
                "indistinguishable from the prior yet (the honest 'we can't tell'), but still ranked")
    if p > 0.10:
        return ("likely_unskilled", "Likely unskilled",
                f"P(skilled)={p*100:.0f}% (0.10-0.40) — leaning below its peers, wide bar")
    # p <= 0.10
    if hi < 0:
        return ("lagging", "Lagging",
                f"P(skilled)={p*100:.0f}% and the 90% CI is entirely below 0 — lagging its benchmark")
    return ("likely_unskilled", "Likely unskilled",
            f"P(skilled)={p*100:.0f}% but the 90% CI still touches 0 — likely unskilled, not yet "
            "conclusively lagging")
