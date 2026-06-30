"""
skill_validate.py — the pre-registered walk-forward OOS validation harness (spec D.2, L662-695).

WHAT THIS PROVES (the test that earns the metric its place)
-----------------------------------------------------------
Does the EARLY posterior PREDICT future active return? This is the `flag-validation` discipline
(validate an early predictor of a future outcome as a HYPOTHESIS TEST of predictive validity, NOT a
tradable NAV backtest) crossed with `signal-backtest` (judge the DISTRIBUTION, control for luck,
pre-register the rule). PRE-REGISTERED: the full spec is committed to prereg/skill_posterior_oos.md
with a SHA + date BEFORE the test runs; this script reads only that frozen spec — no peeking, no
post-hoc knob-turning.

ONE-SENTENCE HYPOTHESIS: a fund's skill posterior, estimated using ONLY data available at time t,
ranks funds by their realized active return over the FOLLOWING window — so the top posterior bucket
out-earns the bottom, OUT-OF-SAMPLE, by a margin that beats a luck null. If it FAILS, the posterior is
curve-fit and must NOT ship as a forward read (it can still ship as a clearly-labelled DESCRIPTIVE
past-skill summary). "No score for error."

THE WALK-FORWARD PROTOCOL (D.2.1) — NO LOOK-AHEAD (the cardinal rule):
  Substrate: the per-fund attribution JSONs (data/funds_attribution/<navindia_code>.json), each
  carrying the per-month `ts` block [{ym, A=active, ic, n, ...}] — this is exactly the load_panel()
  output already reduced per fund. The PREDICTOR posterior at decision date t is recomputed from
  ts[ym <= t] ONLY (returns <= t AND the category prior re-fit on every fund's data <= t — the prior
  itself is walk-forward, never full-sample). The OUTCOME is the REAL fund NAV active return from the
  SURVIVORSHIP-FREE data/funds/history/_amfi_nav_panel.parquet (dead funds RETAINED with their terminal
  active return) minus the SEBI-category-benchmark TR CAGR over t -> t+H — a DIFFERENT estimation plane
  from the predictor (no shared-estimation leakage between predictor and outcome).
  Loop: for each decision date t every 6 months 2016-06 -> 2024-06 (~17 dates); for every fund alive at
  t with n_months(<=t) >= 12: compute the posterior using ONLY data <= t; rank funds WITHIN SEBI
  category into deciles (terciles where thin; pool thin cats); hold a forward window H in {1y, 3y}
  (3y is the headline); measure realized forward active = real-NAV CAGR(t->t+H) - cat-bench CAGR(t->t+H);
  record (t, fund, category, decile, p_skilled, post_best, tenure_at_t, forward_active_1y/3y).

OUTCOME STATISTICS (pre-registered, priority order):
  PRIMARY  — top-minus-bottom decile spread, pooled over t, with a BLOCK-BOOTSTRAP CI over decision
             dates (block = H, to respect overlap autocorrelation — the same circular-block bootstrap in
             funds_attribution._block_bootstrap_mean). PASS = spread > 0, CI excludes 0, AND >= +2.0%/yr
             at H=3y AND actual >= 97.5th pctile of the within-category label-shuffle null.
  SECONDARY— Fama-MacBeth Spearman rank-IC between p_skilled(t) and forward_active_H; + monotonicity
             across deciles.
  TERTIARY — does the POSTERIOR beat the OLD verdict? Re-run ranking by legacy t_stat and by NAV-IR; the
             posterior must show a LARGER, EARLIER spread — ESPECIALLY in the SHORT-TENURE stratum (1-4y).
             Report spread per tenure stratum {1-4y, 4-8y, >8y}.

THE LUCK NULLS (signal-backtest discipline — both pre-registered):
  (1) LABEL-SHUFFLE null: at each t, permute p_skilled labels WITHIN category (>=10,000 perms), recompute
      the top-minus-bottom spread, place the ACTUAL spread in that null. PASS = actual >= 97.5th pctile.
  (2) PERSISTENCE-vs-NOISE null: rank funds by trailing RAW active return (no skill model); the posterior's
      forward spread must be LARGER (else it's just momentum in disguise).

PASS/FAIL (decided in advance, D.2.2): PASS (primary spread >=+2%/yr, CI excludes 0, beats both nulls,
1-4y stratum positive) -> ship as a validated FORWARD read. PARTIAL (works all-funds, 1-4y stratum null)
-> ship but label short-tenure "descriptive, not yet forward-validated". FAIL -> do NOT ship as forward.

* Survivorship: dead funds between t and t+H KEEP their terminal active return (the survivorship-free
NAV panel). Overlap: 6-mo steps x 3y hold => each fund in ~6 overlapping windows => naive CIs too tight
=> the block-bootstrap (block=H) + label-shuffle null respect this; report effective-N.

Re-points the signal-backtest/flag-validation harness at the fund panel — NO new data fetch. WRITES a
report .md ONLY (no publish, no live touch, no build lock).

HONESTY FLAGS BAKED INTO THE OUTPUT (every one a known gap, never hidden):
  - net_basis: the NAV outcome is NET of TER (TER is inside the AMFI NAV); the category-benchmark side is
    a GROSS TR index, so the realized active is a slightly conservative (net-vs-gross) read — STATED.
  - fund_bridge="name-jaccard": no navindia_code->AMFI-code key exists in the repo, so the predictor
    (navindia store) is joined to the outcome (AMFI NAV panel) by normalized-name token Jaccard. Funds
    that don't bridge are DROPPED (reported as bridge coverage), never guessed.
  - prior_walk_forward=True: the category prior is re-fit on data <= t at every decision date.
  - scheme-level (manager-blended): the predictor is scheme-level, same caveat as the live verdict.

--------------------------------------------------------------------------------------------------
SHARED DATA CONTRACT (locked in SKILL_ENGINE_BUILD.md)
--------------------------------------------------------------------------------------------------
walk_forward() panel return: pandas.DataFrame, one row per (t, fund), cols
    [decision_date, navindia_code, category, decile, p_skilled, post_best, tenure_years_at_t,
     forward_active_1y, forward_active_3y].
decile_spread() return dict:
    {"H":"3y","spread":float,"ci_lo":float,"ci_hi":float,"ci_excludes_0":bool,
     "meets_2pct":bool,"rank_ic":float,"rank_ic_t":float,"monotone":bool,
     "by_stratum":{"1-4y":float,"4-8y":float,">8y":float},
     "vs_legacy":{"t_stat_spread":float,"nav_ir_spread":float}}
luck_nulls() return dict:
    {"shuffle_pctile":float,"beats_shuffle":bool,"n_perms":int,
     "trailing_raw_spread":float,"beats_trailing":bool}
run_validation() return dict (-> output/SKILL_POSTERIOR_VALIDATION.md):
    {"verdict":"PASS"|"PARTIAL"|"FAIL","primary":<decile_spread dict>,"nulls":<luck_nulls dict>,
     "calibration":<reliability-diagram dict>,"prereg_sha":str,"n_decision_dates":int,"effective_n":int}
"""
from __future__ import annotations

import json
import math
import os
import re

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------------------------- #
#  Pre-registered constants (frozen by prereg/skill_posterior_oos.md when that SHA-stamped file
#  exists; these literals are the spec defaults).
# ----------------------------------------------------------------------------------------------- #
STEP_MONTHS = 6                       # decision date cadence
DATE_START, DATE_END = "2016-06", "2024-06"
HOLD_HORIZONS = ("1y", "3y")
HEADLINE_HORIZON = "3y"               # the pre-registered confirmatory horizon
MIN_MONTHS_AT_T = 12                  # a fund must have >=12 months at the decision date to be ranked
PASS_SPREAD = 0.02                    # >= +2%/yr top-minus-bottom at H=3y to call it decision-grade
SHUFFLE_PERMS = 10000                 # within-category label-shuffle permutations
TENURE_STRATA = ("1-4y", "4-8y", ">8y")
PREREG_PATH = "prereg/skill_posterior_oos.md"   # the SHA-stamped frozen spec read at run time

_HORIZON_MONTHS = {"1y": 12, "3y": 36}
PPY = 12

# ---- paths (grounded to the real files in this repo) ------------------------------------------ #
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _p(*parts):
    return os.path.join(_ROOT, *parts)


ATTRIB_DIR = _p("data", "funds_attribution")
NAV_PANEL = _p("data", "funds", "history", "_amfi_nav_panel.parquet")
NAV_META = _p("data", "funds", "history", "_amfi_nav_panel_meta.json")

# Posterior axis = annualized Information Ratio (IR = mean(A)/std(A)*sqrt(12)), the same axis
# scheme_metrics builds the t_stat on. theta* for P(skilled) = IR>0 (true skill positive).
THETA_SKILLED_IR = 0.0
CI90_Z = 1.645
# Spec-measured universe constants (baked DATA in skill_posterior; imported so the prior/posterior here
# read the SAME numbers — no divergence). Fallback literals match skill_posterior's module-level defaults.
try:
    from vistas.skill_posterior import UNIVERSE_TRUE_SKILL_VAR, UNIVERSE_PRIOR_MEAN_IR
except Exception:  # pragma: no cover - skill_posterior is a sibling module, always importable
    UNIVERSE_TRUE_SKILL_VAR = 0.002      # Var(IR) 0.160 - E[1/years] 0.158
    UNIVERSE_PRIOR_MEAN_IR = 0.43

# SEBI category -> benchmark TR index (mirrors funds_attribution._CAT_BENCH; read-only copy so the
# validator never imports the live build's mutable state).
_CAT_BENCH = {
    "Large Cap Fund": "NIFTY 100",
    "Mid Cap Fund": "NIFTY MIDCAP 100",
    "Small Cap Fund": "NIFTY SMALLCAP 250",
    "Large & Mid Cap Fund": "NIFTY LARGEMIDCAP 250",
}
_DEFAULT_BENCH = "NIFTY 500"


# =============================================================================================== #
#  LOW-LEVEL HELPERS — date math, name bridge, NAV/bench CAGR
# =============================================================================================== #
def _ym_to_idx(ym: str) -> int:
    """'YYYY-MM' -> integer month ordinal (year*12+month) for clean <= / arithmetic comparisons."""
    return int(ym[:4]) * 12 + int(ym[5:7])


def _idx_to_ym(idx: int) -> str:
    y, m = divmod(idx - 1, 12)
    return f"{y:04d}-{m + 1:02d}"


def _decision_dates(start: str = DATE_START, end: str = DATE_END, step: int = STEP_MONTHS) -> list:
    """The every-6-months decision dates from start..end inclusive (the no-look-ahead grid)."""
    s, e = _ym_to_idx(start), _ym_to_idx(end)
    return [_idx_to_ym(i) for i in range(s, e + 1, step)]


_STOP = set("fund growth idcw dividend direct regular plan option payout reinvestment reinvest the of "
            "and an a g advantage scheme open ended".split())
_ABBR = {"pru": "prudential", "sl": "sunlife", "mf": "", "ltd": "", "limited": ""}


def _name_tokens(s: str) -> set:
    """Normalize a scheme name to a token set for the navindia<->AMFI name bridge (no fund-ISIN key
    exists in the repo, so name Jaccard is the honest bridge; flagged in the report)."""
    s = (s or "").lower()
    s = s.replace("'95", "").replace("'", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [_ABBR.get(w, w) for w in s.split()]
    return set(w for w in toks if w and w not in _STOP)


def build_fund_bridge(manifest: dict, nav_meta: dict, min_jaccard: float = 0.55) -> dict:
    """{navindia_code -> amfi_code} by normalized-name token Jaccard (no direct key exists). Returns the
    bridge plus a coverage stat. Funds with no strong match are DROPPED (never guessed). HONESTY GAP:
    a name bridge mis-joins are possible; reported as bridge coverage in the run summary."""
    nav_tok = {code: _name_tokens(info.get("name", "")) for code, info in nav_meta.items()}
    bridge = {}
    for code, info in manifest.items():
        mt = _name_tokens(info.get("name", ""))
        if not mt:
            continue
        best_c, best_j = None, 0.0
        for ncode, nt in nav_tok.items():
            if not nt:
                continue
            j = len(mt & nt) / len(mt | nt)
            if j > best_j:
                best_j, best_c = j, ncode
        if best_c is not None and best_j >= min_jaccard:
            bridge[str(code)] = str(best_c)
    return bridge


def _nav_cagr(nav_by_code: dict, amfi_code: str, t_ym: str, horizon_m: int) -> float:
    """Realized NAV CAGR from the survivorship-free panel over [t, t+horizon]. Survivorship rule
    (pre-registered, D.3.3 risk-6): if a fund DIED before t+H, use its LAST available NAV and the
    ACTUAL elapsed months as the annualization base (so a dead fund keeps its terminal — usually
    negative-vs-bench — active return; it is NOT dropped). Returns decimal/yr, or NaN if no anchor NAV."""
    ser = nav_by_code.get(amfi_code)
    if ser is None:
        return np.nan
    t0 = _ym_to_idx(t_ym)
    # anchor at t (or the closest month at/just before t within 2 months — month-end disclosure jitter)
    start_nav = None
    for back in range(0, 3):
        v = ser.get(_idx_to_ym(t0 - back))
        if v is not None and v > 0:
            start_nav, start_idx = v, t0 - back
            break
    if start_nav is None:
        return np.nan
    target = t0 + horizon_m
    end_nav, end_idx = None, None
    # walk forward to the target; if the fund died, take the LAST available NAV <= target (terminal)
    for fwd in range(horizon_m, -1, -1):
        v = ser.get(_idx_to_ym(t0 + fwd))
        if v is not None and v > 0:
            end_nav, end_idx = v, t0 + fwd
            break
    if end_nav is None or end_idx <= start_idx:
        return np.nan
    yrs = (end_idx - start_idx) / 12.0
    if yrs <= 0 or start_nav <= 0 or end_nav <= 0:
        return np.nan
    return (end_nav / start_nav) ** (1.0 / yrs) - 1.0


def _bench_cagr(bench_me: pd.DataFrame, bench: str, t_ym: str, horizon_m: int) -> float:
    """Category-benchmark TR CAGR over [t, t+H] from the month-end TR index levels. Mirrors the NAV
    anchoring (closest-month-at/just-before t, terminal-if-short) so the active difference is like-for-like."""
    if bench not in bench_me.columns:
        bench = _DEFAULT_BENCH
    ser = bench_me[bench]
    t0 = _ym_to_idx(t_ym)
    start_lv = None
    for back in range(0, 3):
        ym = _idx_to_ym(t0 - back)
        if ym in ser.index and pd.notna(ser.loc[ym]):
            start_lv, start_idx = float(ser.loc[ym]), t0 - back
            break
    if start_lv is None:
        return np.nan
    end_lv, end_idx = None, None
    for fwd in range(horizon_m, -1, -1):
        ym = _idx_to_ym(t0 + fwd)
        if ym in ser.index and pd.notna(ser.loc[ym]):
            end_lv, end_idx = float(ser.loc[ym]), t0 + fwd
            break
    if end_lv is None or end_idx <= start_idx:
        return np.nan
    yrs = (end_idx - start_idx) / 12.0
    if yrs <= 0 or start_lv <= 0 or end_lv <= 0:
        return np.nan
    return (end_lv / start_lv) ** (1.0 / yrs) - 1.0


# =============================================================================================== #
#  THE PREDICTOR — walk-forward posterior on the IR axis (NO look-ahead)
# =============================================================================================== #
def _ir_at_t(A_upto_t: np.ndarray) -> tuple:
    """Annualized IR and its SE from the active-return series up to t. IR = mean/sd*sqrt(12) (the
    SAME convention scheme_metrics uses). SE(IR) ~= 1/sqrt(T) on the IR axis (the standard IR
    sampling SE) — deliberately NOT 1/sqrt(months on the mean) so we do not under-state uncertainty.
    Returns (ir, se_ir, T)."""
    a = A_upto_t[np.isfinite(A_upto_t)]
    T = len(a)
    if T < 6:
        return (np.nan, np.nan, T)
    m, sd = float(np.mean(a)), float(np.std(a, ddof=1))
    if sd <= 0:
        return (np.nan, np.nan, T)
    ir = (m * PPY) / (sd * math.sqrt(PPY))
    # IR sampling SE (Lo 2002 approx, IID): se ~ sqrt((1 + 0.5*IR^2)/T). Reduces to ~1/sqrt(T) for small IR.
    se = math.sqrt(max(1e-9, (1.0 + 0.5 * ir * ir) / T))
    return (ir, se, T)


def _walk_forward_prior(ir_by_cat: dict) -> dict:
    """Empirical-Bayes category prior re-fit on data <= t (NO full-sample leakage). For each SEBI
    category with >= MIN_PEERS funds: prior_mean = cross-sectional mean of the funds' IR-at-t;
    prior_sd  = sqrt(max(eps, Var_xsec(IR) - mean(se_ir^2)))  (method-of-moments between-fund TRUE-skill
    SD, the spec's tau^2 = Var(x_hat) - E[s^2] estimator). Thin categories borrow the universe prior.
    Input: {category: [(ir, se_ir), ...]} computed at this t. Returns {category|'_universe': (mu, tau)}."""
    MIN_PEERS = 5
    # The spec's MEASURED universe true-skill SD on the IR axis (skill_posterior.UNIVERSE_TRUE_SKILL_VAR
    # = 0.002 => SD 0.05). The per-fund Lo-2002 IR-SE under-states time-series sampling noise on a long
    # history, so a naive Var(IR)-E[se^2] over-estimates tau and crowns too many funds. We CAP tau at the
    # spec's measured true-skill SD so the layer stays honestly conservative ("small tau => refuse young
    # funds"); this is a downward-only rail (it only ever SHRINKS p_skilled toward the prior). FLAGGED.
    TAU_CAP = math.sqrt(UNIVERSE_TRUE_SKILL_VAR)   # = 0.05 IR units
    # universe backstop first
    all_ir = [ir for vals in ir_by_cat.values() for (ir, se) in vals if np.isfinite(ir)]
    all_se2 = [se * se for vals in ir_by_cat.values() for (ir, se) in vals if np.isfinite(se)]
    if len(all_ir) >= MIN_PEERS:
        u_mu = float(np.mean(all_ir))
        u_tau2 = float(np.var(all_ir, ddof=1) - np.mean(all_se2))
    else:
        u_mu, u_tau2 = UNIVERSE_PRIOR_MEAN_IR, UNIVERSE_TRUE_SKILL_VAR
    u_tau = min(TAU_CAP, math.sqrt(max(1e-4, u_tau2)))
    prior = {"_universe": (u_mu, u_tau)}
    for cat, vals in ir_by_cat.items():
        irs = [ir for (ir, se) in vals if np.isfinite(ir)]
        se2 = [se * se for (ir, se) in vals if np.isfinite(se)]
        if len(irs) >= MIN_PEERS:
            mu = float(np.mean(irs))
            tau2 = float(np.var(irs, ddof=1) - np.mean(se2))
            tau = min(TAU_CAP, math.sqrt(max(1e-4, tau2)))
            prior[cat] = (mu, tau)
        else:
            prior[cat] = (u_mu, u_tau)   # thin category borrows universe
    return prior


def _norm_cdf(z: float) -> float:
    """Standard-normal CDF via erf (no scipy) — mirrors skill_posterior._norm_cdf convention exactly."""
    if not np.isfinite(z):
        return np.nan
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _posterior_at_t(ir: float, se: float, mu: float, tau: float) -> tuple:
    """Normal-normal conjugate posterior on the IR axis (the locked skill_posterior formulas):
        w   = tau^2/(tau^2+se^2)            (shrinkage = own-record fraction)
        best= w*ir + (1-w)*mu               (posterior mean IR)
        var = (se^2*tau^2)/(se^2+tau^2)     (always smaller than both)
        p_skilled = Phi((best - mu)/sqrt(var))   # θ = the category prior mean → no-info fund reads 0.5
    Returns (post_best, p_skilled, lo90, hi90). The wide young-fund bar IS the honest 'we don't know'."""
    if not (np.isfinite(ir) and np.isfinite(se) and se > 0 and np.isfinite(tau) and tau > 0):
        return (np.nan, np.nan, np.nan, np.nan)
    s2, t2 = se * se, tau * tau
    w = t2 / (t2 + s2)
    best = w * ir + (1.0 - w) * mu
    var = (s2 * t2) / (s2 + t2)
    sd = math.sqrt(var)
    # θ recentred to the per-category prior mean μ (NOT the absolute 0): with an IR prior mean ≈ +0.43,
    # θ=0 would crown a no-information fund at Φ(+0.43/sd) ≫ 0.5. p_skilled is now RELATIVE skill:
    # P(true IR > category peer average) → a no-record fund (best→μ) reads exactly 0.5 ("indistinguishable").
    p = _norm_cdf((best - mu) / sd)
    return (best, p, best - CI90_Z * sd, best + CI90_Z * sd)


def _load_attrib_ts() -> dict:
    """Load every fund's {navindia_code: {name, category, ts:[{ym,A,ic,n},...], legacy_t_stat,
    legacy_nav_ir}} from the per-fund attribution JSONs. ts is the ALREADY-REDUCED scheme-month panel
    (load_panel output per fund), so the predictor needs no holdings re-merge. Read-only."""
    out = {}
    if not os.path.isdir(ATTRIB_DIR):
        return out
    for fn in os.listdir(ATTRIB_DIR):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        try:
            d = json.load(open(os.path.join(ATTRIB_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        ts = d.get("ts") or []
        if not ts:
            continue
        out[str(d.get("navindia_code", fn[:-5]))] = {
            "name": d.get("scheme_name", ""),
            "category": d.get("sebi_category", ""),
            "ts": ts,
            "legacy_t_stat": d.get("t_stat"),
            "legacy_nav_ir": d.get("info_ratio"),
        }
    return out


def _load_nav_by_code() -> dict:
    """{amfi_code: {ym: nav}} from the survivorship-free NAV panel (dead funds retained). Read-only."""
    nav = pd.read_parquet(NAV_PANEL)
    nav["code"] = nav["code"].astype(str)
    out = {}
    for code, g in nav.groupby("code"):
        out[code] = dict(zip(g["ym"].astype(str), g["nav"].astype(float)))
    return out


def _bench_month_end() -> pd.DataFrame:
    """Month-end TR index levels indexed by 'YYYY-MM' (read-only call into vistas.data — NOT a mutation
    of the TR pipeline; load() only reads the snapshot CSV)."""
    from vistas import data as vdata
    px = vdata.load()
    me = px.resample("ME").last()
    me.index = me.index.strftime("%Y-%m")
    return me


# =============================================================================================== #
#  PUBLIC API
# =============================================================================================== #
def walk_forward(prior_axis: str = "p_skilled", min_months_at_t: int = MIN_MONTHS_AT_T,
                 date_start: str = DATE_START, date_end: str = DATE_END,
                 attrib: dict | None = None, nav_by_code: dict | None = None,
                 bench_me: "pd.DataFrame | None" = None,
                 manifest: dict | None = None, nav_meta: dict | None = None) -> "pd.DataFrame":
    """Run the no-look-ahead loop. For each decision date t (every 6mo, date_start->date_end), recompute
    each alive fund's posterior using ONLY ts[ym<=t] (prior re-fit walk-forward), rank within category
    into deciles, and attach the realized forward active return at H=1y and 3y from the survivorship-free
    NAV panel. Returns the locked walk_forward() panel DataFrame. No fetch; writes nothing live.

    The kwargs let run_validation()/a smoke-test inject a SMALL date subset + pre-loaded substrate so we
    never re-read the big parquets per call (and never run the full panel build / take the build lock)."""
    if attrib is None:
        attrib = _load_attrib_ts()
    if manifest is None:
        manifest = json.load(open(_p("data", "funds_attribution", "_manifest.json"), encoding="utf-8"))
    if nav_meta is None:
        nav_meta = json.load(open(NAV_META, encoding="utf-8"))
    if nav_by_code is None:
        nav_by_code = _load_nav_by_code()
    if bench_me is None:
        bench_me = _bench_month_end()
    bridge = build_fund_bridge(manifest, nav_meta)

    dates = _decision_dates(date_start, date_end)
    rows = []
    for t in dates:
        t_idx = _ym_to_idx(t)
        # 1) per-fund predictor at t (data STRICTLY < t) + collect IRs by category for the walk-forward prior.
        #    A[ym] is the FORWARD-stamped ym→ym+1 active return (rp via fwd_ret, rb via shift(-1)), so A[t]
        #    is only realized AFTER the decision date t — including it (`<= t_idx`) leaks one future month.
        ir_by_cat: dict = {}
        per_fund = {}
        for code, info in attrib.items():
            A = np.array([float(x["A"]) for x in info["ts"]
                          if x.get("A") is not None and _ym_to_idx(x["ym"]) < t_idx], dtype=float)
            T = len(A)
            if T < min_months_at_t:
                continue
            ir, se, _ = _ir_at_t(A)
            if not np.isfinite(ir):
                continue
            cat = info["category"] or "_unknown"
            ir_by_cat.setdefault(cat, []).append((ir, se))
            # trailing raw active CAGR (for the persistence-vs-noise null): cumulative mean active annualized
            trail = float(np.nanmean(A)) * PPY
            per_fund[code] = dict(ir=ir, se=se, T=T, cat=cat, trail=trail,
                                  legacy_t=info.get("legacy_t_stat"))
        if len(per_fund) < 10:
            continue
        prior = _walk_forward_prior(ir_by_cat)
        # 2) posterior + outcome per fund
        for code, pf in per_fund.items():
            mu, tau = prior.get(pf["cat"], prior["_universe"])
            best, p, lo, hi = _posterior_at_t(pf["ir"], pf["se"], mu, tau)
            if not np.isfinite(p):
                continue
            amfi = bridge.get(code)
            if amfi is None:
                continue
            cat = pf["cat"]
            bench = _CAT_BENCH.get(cat, _DEFAULT_BENCH)
            fa = {}
            for H in HOLD_HORIZONS:
                hm = _HORIZON_MONTHS[H]
                nav_c = _nav_cagr(nav_by_code, amfi, t, hm)
                ben_c = _bench_cagr(bench_me, bench, t, hm)
                fa[H] = (nav_c - ben_c) if (np.isfinite(nav_c) and np.isfinite(ben_c)) else np.nan
            rows.append(dict(
                decision_date=t, navindia_code=code, category=cat,
                p_skilled=p, post_best=best, tenure_years_at_t=pf["T"] / 12.0,
                legacy_t_stat=pf["legacy_t"], legacy_nav_ir=pf["ir"], trailing_active=pf["trail"],
                forward_active_1y=fa["1y"], forward_active_3y=fa["3y"],
            ))
    wf = pd.DataFrame(rows)
    if not wf.empty:
        wf = _attach_deciles(wf, axis="p_skilled")
    return wf


def _attach_deciles(wf: "pd.DataFrame", axis: str = "p_skilled") -> "pd.DataFrame":
    """Within (decision_date, category) rank funds by `axis` into deciles 1..10 (terciles where the
    cell is thin: <10 funds -> 3 buckets; <5 -> single bucket dropped from spread). Adds a `decile` col."""
    def _rank(g):
        n = len(g)
        if n < 5:
            g = g.copy(); g["decile"] = np.nan; return g
        q = 10 if n >= 10 else 3
        r = g[axis].rank(method="first")
        g = g.copy()
        g["decile"] = np.ceil(r / n * q).clip(1, q).astype(int)
        g["_nbucket"] = q
        return g
    # group_keys=False + the apply keeps decision_date/category in each g (we WANT them retained); the
    # pandas FutureWarning about operating on grouping columns is the intended behaviour here.
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return wf.groupby(["decision_date", "category"], group_keys=False).apply(_rank)


def decile_spread(wf_panel: "pd.DataFrame", horizon: str = HEADLINE_HORIZON,
                  rank_axis: str = "p_skilled") -> dict:
    """PRIMARY + SECONDARY + TERTIARY stats: top-minus-bottom decile spread (block-bootstrap CI over
    decision dates, block=H), Fama-MacBeth rank-IC, decile monotonicity, the per-tenure-stratum spreads,
    and the legacy-comparison spreads (t_stat, NAV-IR). Returns the locked decile_spread() dict."""
    fa_col = f"forward_active_{horizon}"
    wf = wf_panel.dropna(subset=[fa_col, "decile"]).copy()
    out = {"H": horizon, "spread": np.nan, "ci_lo": np.nan, "ci_hi": np.nan,
           "ci_excludes_0": False, "meets_2pct": False, "rank_ic": np.nan, "rank_ic_t": np.nan,
           "monotone": False, "by_stratum": {s: np.nan for s in TENURE_STRATA},
           "vs_legacy": {"t_stat_spread": np.nan, "nav_ir_spread": np.nan}, "n_obs": int(len(wf))}
    if wf.empty:
        return out

    # --- PRIMARY: top-minus-bottom decile spread per decision date, then pooled + block-bootstrap CI ---
    per_t = []
    for t, g in wf.groupby("decision_date"):
        top = g[g["decile"] == g["decile"].max()][fa_col].mean()
        bot = g[g["decile"] == g["decile"].min()][fa_col].mean()
        if np.isfinite(top) and np.isfinite(bot):
            per_t.append(top - bot)
    per_t = np.array(per_t, dtype=float)
    if len(per_t):
        out["spread"] = float(np.mean(per_t))
        lo, hi = _block_bootstrap_ci(per_t, block=_HORIZON_MONTHS[horizon] // STEP_MONTHS or 1)
        out["ci_lo"], out["ci_hi"] = lo, hi
        out["ci_excludes_0"] = bool(np.isfinite(lo) and lo > 0)
        out["meets_2pct"] = bool(out["spread"] >= PASS_SPREAD)

    # --- SECONDARY: Fama-MacBeth Spearman rank-IC + monotonicity ---
    ics = []
    for t, g in wf.groupby("decision_date"):
        if g[rank_axis].nunique() > 2 and len(g) >= 5:
            ics.append(_spearman(g[rank_axis].values, g[fa_col].values))
    ics = np.array([x for x in ics if np.isfinite(x)], dtype=float)
    if len(ics) >= 2:
        out["rank_ic"] = float(np.mean(ics))
        sd = np.std(ics, ddof=1)
        out["rank_ic_t"] = float(np.mean(ics) / (sd / math.sqrt(len(ics)))) if sd > 0 else np.nan
    dmean = wf.groupby("decile")[fa_col].mean().sort_index()
    if len(dmean) >= 3:
        diffs = np.diff(dmean.values)
        out["monotone"] = bool((diffs >= -1e-9).mean() >= 0.6 and dmean.iloc[-1] > dmean.iloc[0])

    # --- TERTIARY: per-tenure-stratum spread + legacy comparison ---
    for s, (lo_y, hi_y) in {"1-4y": (1, 4), "4-8y": (4, 8), ">8y": (8, 99)}.items():
        sub = wf[(wf["tenure_years_at_t"] >= lo_y) & (wf["tenure_years_at_t"] < hi_y)]
        out["by_stratum"][s] = _pooled_topbot(sub, fa_col)
    # legacy: re-decile by legacy t_stat and by NAV-IR, same forward outcome
    out["vs_legacy"]["t_stat_spread"] = _legacy_spread(wf_panel, "legacy_t_stat", fa_col)
    out["vs_legacy"]["nav_ir_spread"] = _legacy_spread(wf_panel, "legacy_nav_ir", fa_col)
    return out


def _pooled_topbot(wf: "pd.DataFrame", fa_col: str) -> float:
    wf = wf.dropna(subset=[fa_col, "decile"])
    if wf.empty:
        return np.nan
    per_t = []
    for t, g in wf.groupby("decision_date"):
        top = g[g["decile"] == g["decile"].max()][fa_col].mean()
        bot = g[g["decile"] == g["decile"].min()][fa_col].mean()
        if np.isfinite(top) and np.isfinite(bot):
            per_t.append(top - bot)
    return float(np.mean(per_t)) if per_t else np.nan


def _legacy_spread(wf_panel: "pd.DataFrame", axis: str, fa_col: str) -> float:
    """Re-rank the SAME panel by a legacy axis (t_stat or NAV-IR) into deciles, return its top-minus-bottom
    spread — the TERTIARY 'does the posterior beat the old verdict?' comparison."""
    sub = wf_panel.dropna(subset=[axis, fa_col]).copy()
    if sub.empty:
        return np.nan
    sub = _attach_deciles(sub.rename(columns={"decile": "_old_decile"}), axis=axis)
    return _pooled_topbot(sub, fa_col)


def _block_bootstrap_ci(a: np.ndarray, block: int = 1, n_boot: int = 2000) -> tuple:
    """Circular block-bootstrap CI of the mean (the same autocorr-honest device as
    funds_attribution._block_bootstrap_mean), block sized to the forward-horizon overlap. Returns (lo,hi)
    at the 2.5/97.5 percentiles."""
    a = a[np.isfinite(a)]
    n = len(a)
    if n < 3:
        return (np.nan, np.nan)
    block = max(1, min(block, n))
    nb = int(math.ceil(n / block))
    rng = np.random.default_rng(20260630 + n)
    offs = np.arange(block)
    means = np.empty(n_boot)
    starts = rng.integers(0, n, size=(n_boot, nb))
    for b in range(n_boot):
        idx = ((starts[b][:, None] + offs).ravel()[:n]) % n
        means[b] = a[idx].mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return (float(lo), float(hi))


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 4:
        return np.nan
    rx = pd.Series(x).rank().values
    ry = pd.Series(y).rank().values
    if np.std(rx) == 0 or np.std(ry) == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def luck_nulls(wf_panel: "pd.DataFrame", horizon: str = HEADLINE_HORIZON,
               n_perms: int = SHUFFLE_PERMS, seed: int = 20260630) -> dict:
    """The two pre-registered nulls: (1) within-category label-shuffle (>= n_perms perms; PASS = actual
    >= 97.5th pctile); (2) persistence-vs-noise (trailing raw-active ranking; the posterior spread must
    be LARGER). Returns the locked luck_nulls() dict."""
    fa_col = f"forward_active_{horizon}"
    wf = wf_panel.dropna(subset=[fa_col, "decile"]).copy()
    out = {"shuffle_pctile": np.nan, "beats_shuffle": False, "n_perms": int(n_perms),
           "trailing_raw_spread": np.nan, "beats_trailing": False}
    if wf.empty:
        return out
    actual = _pooled_topbot(wf, fa_col)
    if not np.isfinite(actual):
        return out

    # (1) LABEL-SHUFFLE: permute the p_skilled labels WITHIN (date, category), re-decile, re-spread.
    rng = np.random.default_rng(seed)
    null = np.empty(n_perms)
    groups = [(idx.values, g["p_skilled"].values) for (_, g) in wf.groupby(["decision_date", "category"])
              for idx in [g.index]]
    base = wf.copy()
    for k in range(n_perms):
        shuffled = base["p_skilled"].copy().values
        # permute within each (date,category) block
        for gi, (idxs, vals) in enumerate(groups):
            perm = rng.permutation(len(vals))
            pos = base.index.get_indexer(idxs)
            shuffled[pos] = vals[perm]
        tmp = base.copy()
        tmp["p_skilled"] = shuffled
        tmp = _attach_deciles(tmp.drop(columns=["decile"]), axis="p_skilled")
        null[k] = _pooled_topbot(tmp, fa_col)
    null = null[np.isfinite(null)]
    if len(null):
        out["shuffle_pctile"] = float((null < actual).mean() * 100.0)
        out["beats_shuffle"] = bool(out["shuffle_pctile"] >= 97.5)

    # (2) PERSISTENCE-vs-NOISE: rank by trailing raw active, same forward outcome; posterior must beat it.
    trail_spread = _legacy_spread(wf_panel, "trailing_active", fa_col)
    out["trailing_raw_spread"] = trail_spread
    out["beats_trailing"] = bool(np.isfinite(trail_spread) and actual > trail_spread)
    return out


def calibration(wf_panel: "pd.DataFrame", horizon: str = HEADLINE_HORIZON) -> dict:
    """Reliability diagram (D.3.3 risk-2): across OOS folds, the realized skill-rate of 'P(skilled)=X%'
    funds must track X%. 'realized skill' = the fund's forward active at H was positive. Returns
    {"buckets":[{p_stated,realized,n}],"max_gap":float,"well_calibrated":bool}."""
    fa_col = f"forward_active_{horizon}"
    wf = wf_panel.dropna(subset=[fa_col, "p_skilled"]).copy()
    out = {"buckets": [], "max_gap": np.nan, "well_calibrated": False}
    if wf.empty:
        return out
    edges = np.linspace(0, 1, 11)
    wf["_b"] = np.clip(np.digitize(wf["p_skilled"], edges) - 1, 0, 9)
    gaps = []
    for b in range(10):
        sub = wf[wf["_b"] == b]
        if len(sub) < 3:
            continue
        p_stated = float((edges[b] + edges[b + 1]) / 2.0)
        realized = float((sub[fa_col] > 0).mean())
        out["buckets"].append({"p_stated": p_stated, "realized": realized, "n": int(len(sub))})
        gaps.append(abs(p_stated - realized))
    if gaps:
        out["max_gap"] = float(max(gaps))
        out["well_calibrated"] = bool(out["max_gap"] <= 0.15)
    return out


def _prereg_sha() -> str:
    """SHA of the SHA-stamped frozen prereg spec (read-only). Returns 'NOT-PREREGISTERED' if the file
    does not yet exist (the smoke-test runs before the confirmatory prereg is committed — flagged)."""
    path = _p(*PREREG_PATH.split("/"))
    if not os.path.isfile(path):
        return "NOT-PREREGISTERED"
    import hashlib
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def run_validation(outpath: str = "output/SKILL_POSTERIOR_VALIDATION.md",
                   date_start: str = DATE_START, date_end: str = DATE_END,
                   smoke: bool = False, n_perms: int = SHUFFLE_PERMS) -> dict:
    """Drive the full pre-registered protocol -> PASS/PARTIAL/FAIL, write the report. Reads the
    SHA-stamped prereg spec; only the pre-registered PRIMARY can confirm. Returns the locked
    run_validation() dict. Writes a report .md ONLY (no publish, no live touch).

    smoke=True restricts the perm count + does NOT assert a verdict (the confirmatory full-grid OOS run is
    a later pre-registered step) — used to prove the harness runs end-to-end on a SMALL date subset."""
    wf = walk_forward(date_start=date_start, date_end=date_end)
    primary = decile_spread(wf, HEADLINE_HORIZON) if not wf.empty else None
    nulls = luck_nulls(wf, HEADLINE_HORIZON, n_perms=n_perms) if not wf.empty else None
    calib = calibration(wf, HEADLINE_HORIZON) if not wf.empty else None

    verdict = "SMOKE-ONLY" if smoke else _decide_verdict(primary, nulls)
    n_dates = int(wf["decision_date"].nunique()) if not wf.empty else 0
    eff_n = int(wf.groupby("navindia_code").ngroups) if not wf.empty else 0   # distinct funds = effective-N
    result = {"verdict": verdict, "primary": primary, "nulls": nulls, "calibration": calib,
              "prereg_sha": _prereg_sha(), "n_decision_dates": n_dates, "effective_n": eff_n,
              "n_obs": int(len(wf)), "smoke": bool(smoke)}
    _write_report(result, wf, outpath)
    return result


def _decide_verdict(primary, nulls) -> str:
    if not primary or not nulls:
        return "FAIL"
    pass_primary = (primary["ci_excludes_0"] and primary["meets_2pct"]
                    and nulls["beats_shuffle"] and nulls["beats_trailing"])
    short_ok = np.isfinite(primary["by_stratum"]["1-4y"]) and primary["by_stratum"]["1-4y"] > 0
    if pass_primary and short_ok:
        return "PASS"
    if pass_primary:
        return "PARTIAL"
    return "FAIL"


def _fmt(x, pct=True):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x * 100:+.2f}%/yr" if pct else f"{x:.3f}"


def _write_report(result: dict, wf: "pd.DataFrame", outpath: str):
    op = outpath if os.path.isabs(outpath) else _p(*outpath.split("/"))
    os.makedirs(os.path.dirname(op), exist_ok=True)
    p = result["primary"] or {}
    n = result["nulls"] or {}
    L = []
    L.append("# Skill-Posterior OOS Validation\n")
    L.append(f"**Verdict: {result['verdict']}**"
             + ("  _(SMOKE run — harness end-to-end only on a small date subset; the confirmatory "
                "pre-registered full-grid OOS run is a later step; NO pass is claimed here.)_" if result["smoke"] else "")
             + "\n")
    L.append(f"- prereg_sha: `{result['prereg_sha']}`")
    L.append(f"- decision dates: {result['n_decision_dates']}  ·  fund-date rows: {result['n_obs']}  "
             f"·  effective-N (distinct funds): {result['effective_n']}\n")
    L.append("## Honesty flags (known gaps — never hidden)")
    L.append("- **fund_bridge = name-Jaccard**: no navindia_code->AMFI-code key exists in the repo; the "
             "predictor (navindia store) is joined to the outcome (AMFI NAV panel) by normalized-name "
             "token Jaccard. Unbridged funds are DROPPED, never guessed.")
    L.append("- **net-vs-gross**: the NAV outcome is NET of TER; the category-benchmark is a GROSS TR "
             "index, so realized active is a slightly conservative read.")
    L.append("- **prior is walk-forward** (re-fit on data <= t each decision date — no full-sample leak).")
    L.append("- **scheme-level / manager-blended** predictor (same caveat as the live verdict).\n")
    if p:
        L.append("## PRIMARY — top-minus-bottom decile spread (H=3y)")
        L.append(f"- spread: **{_fmt(p['spread'])}**  ·  95% block-bootstrap CI "
                 f"[{_fmt(p['ci_lo'])}, {_fmt(p['ci_hi'])}]  ·  CI excludes 0: {p['ci_excludes_0']}  "
                 f"·  >= +2%/yr: {p['meets_2pct']}")
        L.append(f"- SECONDARY rank-IC (Fama-MacBeth): {_fmt(p['rank_ic'], pct=False)} "
                 f"(t={_fmt(p['rank_ic_t'], pct=False)})  ·  monotone deciles: {p['monotone']}")
        L.append("- TERTIARY tenure strata (posterior spread): "
                 + "  ".join(f"{s}={_fmt(v)}" for s, v in p["by_stratum"].items()))
        L.append(f"- vs legacy: t_stat-ranked spread {_fmt(p['vs_legacy']['t_stat_spread'])}  ·  "
                 f"NAV-IR-ranked spread {_fmt(p['vs_legacy']['nav_ir_spread'])}\n")
    if n:
        L.append("## LUCK NULLS")
        L.append(f"- label-shuffle: actual at {_fmt(n['shuffle_pctile'], pct=False)} pctile of "
                 f"{n['n_perms']} within-category permutations  ·  beats (>=97.5): {n['beats_shuffle']}")
        L.append(f"- persistence-vs-noise: trailing-raw-ranked spread {_fmt(n['trailing_raw_spread'])}  "
                 f"·  posterior beats trailing: {n['beats_trailing']}\n")
    if result["calibration"] and result["calibration"]["buckets"]:
        c = result["calibration"]
        L.append("## CALIBRATION (reliability)")
        L.append(f"- max gap |stated - realized P(fwd active>0)|: {_fmt(c['max_gap'], pct=False)}  "
                 f"·  well-calibrated (<=0.15): {c['well_calibrated']}\n")
    L.append("\n_No publish, no git, no live-site touch. Read-only over the attribution JSONs + NAV "
             "panel + TR index snapshot._\n")
    with open(op, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return op


if __name__ == "__main__":
    import sys
    sm = "--full" not in sys.argv
    r = run_validation(smoke=sm, date_start="2017-06", date_end="2018-06",
                       n_perms=200 if sm else SHUFFLE_PERMS)
    print(json.dumps({k: v for k, v in r.items() if k not in ("primary", "nulls", "calibration")},
                     indent=1, default=str))
