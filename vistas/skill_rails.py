"""
skill_rails.py — Component C honesty rails 2 & 3: net-of-fee + FDR/multiple-testing
(spec RAIL 2, L426-441; RAIL 3, L444-471). (RAIL 1 factor/sector deflation lives in skill_factors.py.)

WHY (one paragraph)
-------------------
The early/high-breadth detector (A/B) fires from ~1 year — and that sensitivity is exactly what makes
it dangerous: a fund can look "skilled" because it (1) rode a factor/sector the market rewarded
[RAIL 1 -> skill_factors], (2) charged fees that quietly ate the edge [RAIL 2 here], or (3) was simply
one of the LUCKY tails among 740 simultaneous tests [RAIL 3 here]. Each rail converts a raw optimistic
estimate into a defensible one and may ONLY ever LOWER the headline skill count, never raise it — that
asymmetry is the point ("no score for error").

RAIL 2 — NET-OF-FEE (L426-441).
  A_net(t) = A(t) - annual_TER/12 (a constant monthly drag), applied to the SAME active series before
  IR/t/bootstrap, and fed as the dependent variable into RAIL 1's regression when a TER exists (so the
  net alpha is also factor-deflated — the rails compose). Report BOTH gross and net; the verdict keys off
  NET where a TER exists. Re-ranks correctly: a high-gross/high-fee fund loses to a modest-gross/low-fee
  fund. * THE DATA GAP, stated plainly: we DO NOT have expense ratios — scheme_master.json and
  _amfi_census.json carry name/isin/category/fund_house but NO TER field (verified). So RAIL 2 runs on
  a flagged CATEGORY-MEDIAN TER PROXY (this module) until a real SEBI/AMFI TER feed (vistas/funds_ter.py)
  lands. Every net number is stamped net_basis="category-median-proxy" so no one mistakes it for the
  fund's real TER. Plan type (direct vs regular) is DETECTED from the scheme name.

RAIL 3 — FDR / MULTIPLE-TESTING (L444-471). Running 740 simultaneous one-tailed tests, ~2.5%*740 ~=
  18-19 funds clear t>=2 by pure luck even with zero true skill. Two complementary corrections on the
  per-fund NET, FACTOR-DEFLATED p-values:
    3a. BSW (Barras-Scaillet-Wermers 2010) — population view. Storey pi0 estimator from the FLAT middle
        of the p-value histogram (true nulls are Uniform): pi0 = #{p_i > lambda}/((1-lambda)*M), lambda~=0.5-0.6;
        the significant tails beyond pi0 are the genuinely skilled (pi+) / unskilled (pi-).
        Reframes "152 gross-significant" into truly-skilled vs lucky-good (+ lucky-bad).
    3b. Benjamini-Hochberg — controls the PUBLISHED shortlist. Sort the M one-tailed p-values ascending;
        k* = max{k : p_(k) <= (k/M)*q}, q=0.10; call funds 1..k* skilled -> <=10% of the published list is
        luck (adaptive: strict when few clear, lenient when many do). Optionally Storey-sharpen with pi0.
  Honesty on the honesty rail: BH/BSW need valid per-fund p-values (so RAIL-1 Newey-West SE + the >=24mo
  minimum are prerequisites); overlapping benchmarks induce POSITIVE dependence (BH is robust to it;
  Benjamini-Yekutieli is the strict fallback) — flag it; FDR controls the LIST, not any single fund
  (a fund "in the list" still carries its own t + CI, never a bare label).

DISPLAY-PLANE only — additive, no analytics.py touch, NO JS-parity port here.

--------------------------------------------------------------------------------------------------
SHARED DATA CONTRACT (locked in SKILL_ENGINE_BUILD.md)
--------------------------------------------------------------------------------------------------
TER proxy table `TER_PROXY` (category-median TER haircut; a FLAGGED approximation, baked as DATA):
    {
      "<sebi_category>": {"regular": float, "direct": float},   # annual TER, decimal/yr (e.g. 0.019, 0.008)
      ...,
      "_default": {"regular": 0.019, "direct": 0.008},
    }
net_of_fee() return dict:
    {"a_net_monthly": <series>, "ter_annual": float, "plan": "regular"|"direct",
     "fee_drag_pct": float, "net_basis": "category-median-proxy", "fee_adjusted": True}
fdr() return dict (RAIL 3 book-level pass over the whole panel of p-values):
    {
      "passes_fdr": {navindia_code: bool, ...},   # BH survivor flag per fund (the published-list membership)
      "fdr_q": 0.10, "k_star": int, "p_cutoff": float, "M": int,
      "bsw": {"pi0": float, "pi_plus": float, "pi_minus": float, "lambda": float,
              "n_truly_skilled_est": float, "n_lucky_good_est": float},
      "method": "benjamini-hochberg", "dependence_note": "positive dep (overlapping benches); BY is the strict fallback",
    }
"""
from __future__ import annotations

import math

# FDR / BSW constants (locked; mirrored in SKILL_ENGINE_BUILD.md)
FDR_Q = 0.10                 # Benjamini-Hochberg target false-discovery rate for the published "skilled" list
BSW_LAMBDA = 0.5             # Storey pi0 cut: pi0 = #{p>lambda}/((1-lambda)*M)

# --------------------------------------------------------------------------------------------------
# Category-median TER proxy (FLAGGED approximation; baked DATA; replaced by a real SEBI/AMFI feed later)
# --------------------------------------------------------------------------------------------------
# Source: SEBI Mutual Fund Regulations 1996, Reg. 52 tiered-TER ceilings + the published industry
# medians for open-ended EQUITY schemes (AMFI monthly TER aggregates). SEBI caps total TER for an
# equity scheme by AUM slab (2.25% up to Rs.500cr, sliding to 1.05% above Rs.50,000cr); direct plans
# strip the ~0.5-1.1%/yr distributor commission. We bake a CATEGORY-MEDIAN regular-plan TER and the
# matching direct-plan TER (regular minus the typical commission), per SEBI-category. The category
# ORDERING is the economically-real one: smaller/less-liquid mandates (small-cap, sectoral) sit at
# higher TER tiers than large-cap; hybrid/allocation funds sit lower (larger, simpler books). These
# are PROXIES, not any fund's real TER — every net number is stamped net_basis="category-median-proxy".
TER_PROXY = {
    # equity (pure)         regular  direct
    "Large Cap Fund":                    {"regular": 0.0175, "direct": 0.0075},
    "Large & Mid Cap Fund":              {"regular": 0.0190, "direct": 0.0085},
    "Flexi Cap Fund":                    {"regular": 0.0185, "direct": 0.0080},
    "Multi Cap Fund":                    {"regular": 0.0190, "direct": 0.0085},
    "Mid Cap Fund":                      {"regular": 0.0200, "direct": 0.0090},
    "Small Cap Fund":                    {"regular": 0.0210, "direct": 0.0095},
    "Focused Fund":                      {"regular": 0.0190, "direct": 0.0085},
    "Value Fund":                        {"regular": 0.0190, "direct": 0.0085},
    "Contra Fund":                       {"regular": 0.0190, "direct": 0.0085},
    "Dividend Yield Fund":               {"regular": 0.0195, "direct": 0.0090},
    "ELSS":                              {"regular": 0.0185, "direct": 0.0090},
    "Sectoral / Thematic":               {"regular": 0.0205, "direct": 0.0095},
    "Retirement Fund":                   {"regular": 0.0190, "direct": 0.0090},
    "Childrens Fund":                    {"regular": 0.0195, "direct": 0.0095},
    # hybrid / allocation (larger, simpler books -> lower median TER)
    "Aggressive Hybrid Fund":            {"regular": 0.0175, "direct": 0.0075},
    "Conservative Hybrid Fund":          {"regular": 0.0150, "direct": 0.0070},
    "Balanced Hybrid Fund":              {"regular": 0.0170, "direct": 0.0075},
    "Equity Savings":                    {"regular": 0.0140, "direct": 0.0065},
    "Dynamic Asset Allocation or Balanced Advantage": {"regular": 0.0165, "direct": 0.0075},
    "Multi Asset Allocation":            {"regular": 0.0175, "direct": 0.0080},
    # grand backstop (SEBI tiered-TER midpoints used in the spec: regular ~1.9%, direct ~0.8%)
    "_default":                          {"regular": 0.0190, "direct": 0.0080},
}

NET_BASIS = "category-median-proxy"   # stamped until vistas/funds_ter.py (real SEBI/AMFI feed) lands


# ==================================================================================================
# RAIL 2 — NET-OF-FEE
# ==================================================================================================
def detect_plan(scheme_name: str) -> str:
    """Detect the plan type from the scheme name -> "direct" | "regular" (default "regular" when
    unmarked). NOTE: funds_attribution._norm_name STRIPS 'DIRECT/REGULAR'; here we instead DETECT it
    (the opposite operation) so the right TER tier is applied.

    Worked example: "Quantum ELSS Tax Saver Fund - Direct (G)" -> "direct";
                    "Aditya Birla SL Equity Hybrid '95 Fund (G)" -> "regular" (unmarked default).
    Rationale for the default: a regular-plan haircut is the LARGER drag, so defaulting unmarked
    names to "regular" keeps RAIL 2 conservative (it can only lower skill more, never less) — exactly
    the asymmetry the honesty doctrine wants.
    """
    if not scheme_name:
        return "regular"
    low = str(scheme_name).lower()
    # explicit DIRECT markers (word-boundary-ish; avoid matching 'indirect'/'director' nonsense)
    if "direct" in low and "indirect" not in low:
        return "direct"
    # a few AMCs abbreviate as "- dir -" / "(dir)"
    if " dir " in low or "(dir)" in low or "-dir-" in low.replace(" ", ""):
        return "direct"
    return "regular"


def ter_for(category: str, scheme_name: str, ter_table: dict | None = None) -> tuple:
    """Category-median TER proxy for a scheme -> (ter_annual, plan, net_basis). Looks up
    TER_PROXY[category][plan] (falling back to _default). net_basis is always "category-median-proxy"
    here — stamped so a real-TER feed can later override and flip the basis string.

    Returns
    -------
    (ter_annual: float decimal/yr, plan: "direct"|"regular", net_basis: str)
    """
    table = ter_table if ter_table is not None else TER_PROXY
    plan = detect_plan(scheme_name)
    row = table.get(category) or table.get("_default") or {"regular": 0.019, "direct": 0.008}
    ter_annual = row.get(plan)
    if ter_annual is None:
        # category row missing this plan -> fall back through _default, then a hard floor
        ter_annual = (table.get("_default") or {}).get(plan, 0.019 if plan == "regular" else 0.008)
    return float(ter_annual), plan, NET_BASIS


def net_of_fee(a_monthly, category: str, scheme_name: str, gross_excess_ann: float | None = None,
               ter_table: dict | None = None) -> dict:
    """RAIL 2 — subtract one-twelfth of the annual TER each month: A_net(t) = A(t) - annual_TER/12,
    BEFORE the IR/t/bootstrap (and as the dependent variable into RAIL 1). Returns the locked
    net_of_fee() dict with the fee-adjusted series, the TER used, the plan, fee_drag_pct, and the
    net_basis flag. Honesty: this can only LOWER skill (a constant negative drag).

    Parameters
    ----------
    a_monthly        : the GROSS monthly active series A(t)=rp-rb. Accepts a pandas Series, a numpy
                       array, or a plain list/tuple of floats. Whatever goes in, the same type
                       (best-effort) comes out for a_net_monthly.
    category         : sebi_category string (keys TER_PROXY).
    scheme_name      : raw scheme name (plan detected from it).
    gross_excess_ann : (optional) the fund's annualised gross excess (decimal/yr). If given,
                       fee_drag_pct = ter_annual / |gross_excess_ann| reports how much of the gross
                       edge the fee ate. If absent/zero, fee_drag_pct is None.

    Returns
    -------
    {"a_net_monthly", "ter_annual", "plan", "fee_drag_pct", "net_basis", "fee_adjusted",
     "monthly_drag", "gross_excess_ann", "net_excess_ann"}
    """
    ter_annual, plan, net_basis = ter_for(category, scheme_name, ter_table)
    monthly_drag = ter_annual / 12.0

    a_net = _subtract_const(a_monthly, monthly_drag)

    fee_drag_pct = None
    net_excess_ann = None
    if gross_excess_ann is not None and math.isfinite(gross_excess_ann):
        net_excess_ann = float(gross_excess_ann) - ter_annual
        if abs(gross_excess_ann) > 1e-12:
            fee_drag_pct = float(ter_annual) / abs(float(gross_excess_ann))

    return {
        "a_net_monthly": a_net,
        "ter_annual": float(ter_annual),
        "plan": plan,
        "monthly_drag": float(monthly_drag),
        "fee_drag_pct": fee_drag_pct,
        "gross_excess_ann": (float(gross_excess_ann) if gross_excess_ann is not None
                             and math.isfinite(gross_excess_ann) else None),
        "net_excess_ann": net_excess_ann,
        "net_basis": net_basis,
        "fee_adjusted": True,
    }


def _subtract_const(a_monthly, c: float):
    """Subtract a constant from a Series / ndarray / list, returning the same kind. Pure helper so
    net_of_fee never hard-depends on pandas/numpy being importable."""
    # pandas Series?
    try:
        import pandas as pd  # local import: module must work even if pandas isn't installed
        if isinstance(a_monthly, pd.Series):
            return a_monthly - c
    except Exception:
        pass
    # numpy array?
    try:
        import numpy as np
        if isinstance(a_monthly, np.ndarray):
            return a_monthly - c
    except Exception:
        pass
    # plain iterable
    try:
        return [float(x) - c for x in a_monthly]
    except TypeError:
        # scalar fallback
        return float(a_monthly) - c


# ==================================================================================================
# RAIL 3 — FDR / MULTIPLE-TESTING
# ==================================================================================================
def t_to_one_tailed_p(t: float, n_months: int | None = None) -> float:
    """Crude one-tailed (upper) p-value from a skill t-stat. Under H0 (no skill) the t-stat is ~N(0,1)
    for the sample sizes here (n_months typically >=24 -> the normal tail is within a few % of the
    Student-t tail). If scipy is present and n_months given, we use the exact Student-t with
    (n_months-1) df; otherwise the normal approximation. ONE-TAILED UPPER: a strongly NEGATIVE t maps
    to p near 1 (correctly NOT a positive-skill discovery).

    p = 1 - Phi(t)  (normal)  |  p = sf(t, df=n-1)  (Student-t if available)
    """
    if t is None or not math.isfinite(t):
        return float("nan")
    # exact Student-t when we can
    if n_months and n_months > 2:
        try:
            from scipy import stats  # optional
            return float(stats.t.sf(t, df=n_months - 1))
        except Exception:
            pass
    return float(_norm_sf(t))


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function (no scipy needed)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_sf(x: float) -> float:
    """Upper-tail survival function 1 - Phi(x)."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def bsw_pi0(pvals, lam: float = BSW_LAMBDA) -> dict:
    """RAIL 3a — Barras-Scaillet-Wermers population decomposition via the Storey pi0 estimator:
        pi0 = #{p_i > lambda}/((1-lambda)*M)  (fraction of TRUE-null / no-skill funds, from the flat
                                               middle/high of the p-value histogram);
        pi+ , pi- = the significant-tail mass beyond what pi0 predicts.

    Mechanics (one-tailed UPPER p-values feed in):
      - M = # finite p-values.
      - pi0 = min(1, #{p>lambda}/((1-lambda)*M))  — Storey's flat-tail estimator. Clamped to [0,1].
      - Tails: a fund is "significant-good" if p <= ALPHA_SIG (cleared the upside bar). The EXPECTED
        number of those that are pure luck is pi0 * ALPHA_SIG * M (uniform-null mass below the bar).
        So  n_lucky_good_est = pi0 * ALPHA_SIG * M ; the EXCESS truly-skilled count
            n_truly_skilled_est = max(0, n_sig_good - n_lucky_good_est).
        pi_plus = n_truly_skilled_est / M.
      - pi_minus is the symmetric LOWER tail excess: significant-bad funds (p >= 1-ALPHA_SIG, i.e.
        strongly negative t) beyond the uniform-null expectation -> truly unskilled.

    ALPHA_SIG is the one-tailed significance bar matching the legacy t>=2 verdict: 1-Phi(2)=0.0228.

    Returns {"pi0","pi_plus","pi_minus","lambda","n_truly_skilled_est","n_lucky_good_est",
             "M","n_sig_good","n_sig_bad","alpha_sig"}.
    Reframes "152 gross-significant" into truly-skilled vs lucky-good vs lucky-bad. Every count can
    only ever SHRINK the skilled population (pi_plus <= observed significant rate).
    """
    ALPHA_SIG = _norm_sf(2.0)  # 0.02275 — the one-tailed upside bar that t>=2 implies

    ps = [float(p) for p in pvals if p is not None and _isfinite(p)]
    M = len(ps)
    if M == 0:
        return {"pi0": float("nan"), "pi_plus": float("nan"), "pi_minus": float("nan"),
                "lambda": lam, "n_truly_skilled_est": float("nan"),
                "n_lucky_good_est": float("nan"), "M": 0, "n_sig_good": 0, "n_sig_bad": 0,
                "alpha_sig": ALPHA_SIG}

    n_above_lam = sum(1 for p in ps if p > lam)
    pi0 = n_above_lam / ((1.0 - lam) * M)
    pi0 = min(1.0, max(0.0, pi0))   # clamp to a valid proportion

    n_sig_good = sum(1 for p in ps if p <= ALPHA_SIG)            # cleared the upside bar
    n_sig_bad = sum(1 for p in ps if p >= 1.0 - ALPHA_SIG)      # strongly negative (lower tail)

    n_lucky_good = pi0 * ALPHA_SIG * M
    n_truly_skilled = max(0.0, n_sig_good - n_lucky_good)
    n_lucky_bad = pi0 * ALPHA_SIG * M
    n_truly_unskilled = max(0.0, n_sig_bad - n_lucky_bad)

    pi_plus = n_truly_skilled / M
    pi_minus = n_truly_unskilled / M

    return {
        "pi0": float(pi0),
        "pi_plus": float(pi_plus),
        "pi_minus": float(pi_minus),
        "lambda": float(lam),
        "n_truly_skilled_est": float(n_truly_skilled),
        "n_lucky_good_est": float(n_lucky_good),
        "n_truly_unskilled_est": float(n_truly_unskilled),
        "M": M,
        "n_sig_good": int(n_sig_good),
        "n_sig_bad": int(n_sig_bad),
        "alpha_sig": float(ALPHA_SIG),
    }


def fdr(pvals_by_fund, q: float = FDR_Q, lam: float = BSW_LAMBDA, use_storey: bool = False) -> dict:
    """RAIL 3b — Benjamini-Hochberg FDR over the WHOLE panel of per-fund (net, factor-deflated)
    one-tailed p-values: sort ascending, k* = max{k : p_(k) <= (k/M)*q}; funds 1..k* get
    passes_fdr=True. Optionally Storey-sharpen with pi0 (use_storey=True -> the bar becomes
    (k/M)*q/pi0, which is LESS strict / recovers power). Also runs bsw_pi0 for the population view.

    Parameters
    ----------
    pvals_by_fund : dict {navindia_code -> one_tailed_p}  OR  an iterable of (code, p) pairs.
                    Funds with a None/NaN p (e.g. "insufficient history") are EXCLUDED from M and
                    get passes_fdr=False (you cannot publish a fund you couldn't test).
    q             : target FDR for the published list (locked 0.10).
    lam           : Storey lambda for the BSW pi0 (locked 0.5).
    use_storey    : if True, divide the BH bar by pi0 (Storey q-value sharpening).

    Returns the locked fdr() dict:
      {"passes_fdr": {code: bool}, "fdr_q", "k_star", "p_cutoff", "M", "bsw": {...},
       "method", "dependence_note", "n_excluded", "use_storey"}.

    FDR controls the LIST, not any single fund — every fund still carries its own t + CI elsewhere.
    This rail can only LOWER the skilled count relative to the naive t>=2 tally.
    """
    # normalise input to a list of (code, p)
    if isinstance(pvals_by_fund, dict):
        items = list(pvals_by_fund.items())
    else:
        items = list(pvals_by_fund)

    valid = [(c, float(p)) for c, p in items if p is not None and _isfinite(p)]
    excluded = [c for c, p in items if p is None or not _isfinite(p)]
    M = len(valid)

    passes = {c: False for c, _ in items}   # default everyone False (incl. excluded)

    bsw = bsw_pi0([p for _, p in valid], lam=lam)

    if M == 0:
        return {
            "passes_fdr": passes, "fdr_q": q, "k_star": 0, "p_cutoff": 0.0, "M": 0,
            "bsw": bsw, "method": "benjamini-hochberg",
            "dependence_note": "positive dep (overlapping benches); BY is the strict fallback",
            "n_excluded": len(excluded), "use_storey": use_storey,
        }

    pi0 = bsw.get("pi0")
    storey_factor = (pi0 if (use_storey and pi0 and pi0 > 0) else 1.0)

    # sort ascending by p; ties keep stable order
    ordered = sorted(valid, key=lambda cp: cp[1])

    # k* = max{k : p_(k) <= (k/M)*q [ / pi0 ]}   (k is 1-based rank)
    k_star = 0
    p_cutoff = 0.0
    for k, (_, p) in enumerate(ordered, start=1):
        bar = (k / M) * q / storey_factor
        if p <= bar:
            k_star = k
            p_cutoff = p   # the p of the largest passing rank
    # mark the first k_star funds (by ascending p) as survivors
    for k, (c, _) in enumerate(ordered, start=1):
        if k <= k_star:
            passes[c] = True

    return {
        "passes_fdr": passes,
        "fdr_q": float(q),
        "k_star": int(k_star),
        "p_cutoff": float(p_cutoff),
        "M": int(M),
        "bsw": bsw,
        "method": ("benjamini-hochberg-storey" if use_storey else "benjamini-hochberg"),
        "dependence_note": "positive dep (overlapping benches); BY is the strict fallback",
        "n_excluded": len(excluded),
        "use_storey": use_storey,
    }


def _isfinite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False
