"""
skill_engine.py — the integrator (spec Component D.1, L526-616): compute_skill(...) wires
A → C.deflate → B → C.FDR → D into the single per-fund `skill` block, and exposes a build_all hook
helper for the additive funds_attribution.build_all(posterior=True) integration.

THE PIPELINE compute_skill assembles (spec §"How the rails compose", L474-483 + §D.1):
    raw active A(t)
      ──RAIL 2 (skill_rails.net_of_fee)──►  A_net(t) = A − TER/12
      ──RAIL 1 (skill_factors.deflate)──►   A_net = α + Σβ_k F_k (+β_S·S thematic) + ε
                                            skill_t = α̂ / SE_NW(α̂)
    ──Component A (skill_signals)──► bet-level IC + trade-alpha + batting signal tuples
    ──Component B (skill_posterior)──► shrink each signal's (x̂,s) toward the category prior → posterior,
                                       map IC→%/yr, P(skilled), the 5-state tag
    ──RAIL 3 (skill_rails.fdr, BOOK-LEVEL, run in the build hook over ALL funds)──► passes_fdr survivor flag
    ──Component D──► the `skill` JSON block (D.1), the decile/percentile rank (cross-sectional, in the hook)

THE SLOW NAV-IR is DEMOTED from GATE to CHECK (D.1 block 6): kept clean as an independent slow
corroborator (breadth≈1-3/yr, can't DECIDE before ~years_needed); a sign-agreement flag, not a gate.

★ GUARDRAILS (every phase, non-negotiable):
  - ADDITIVE only. compute_skill READS funds_attribution's existing scheme record + panel + flows; it
    does NOT mutate them. The ONLY edit to an existing file is the additive build_all(posterior=...)
    hook (apply_to_record below shows the shape; the actual one-line wiring is the Integrate phase).
  - Legacy keys stay intact; schema_version:2 ADDS the "skill" block, removes nothing.
  - Rails may only LOWER skill. Every gap is stamped in rails.caveats.
  - Do NOT flip any consumer default (fsScorecardHTML/fsLeaderboardHTML/amc_context.packf stay legacy).
  - Smoke-test on a HANDFUL of funds directly — never the full 740-fund build_all, never take the build lock.

--------------------------------------------------------------------------------------------------
WHAT FEEDS THE POSTERIOR (the axis decision, grounded honestly):
  The headline posterior runs on the NET-%/yr axis (an allocator-readable annual active return), so:
    x_hat = ic_to_annual(IC_cons, BR_eff, TC, ω, TER)   — the cleaned peer-consensus holding IC mapped
            to a NET annual active via the Fundamental Law atom (Rail-2 fee already inside the map).
    s     = the SAME linear map applied to the IC's bootstrap/NW SE: s = TC·se_IC·√BR_eff·ω  (so the
            error bar is on the same axis; |·| because an SE is a magnitude).
  The factor-deflation rail (RAIL 1) runs on the NAV active series A_net and produces an INDEPENDENT
  residual-alpha t-stat (skill_t) + the per-fund net p-value that the BOOK-LEVEL FDR (RAIL 3) consumes.
  The factor α is also surfaced (rails.residual_alpha_ann / factor_alpha_share) — it can only LOWER the
  read. The slow NAV-IR is the demoted CHECK. Every gap (W-HIST cap-tilt, category-median TER proxy,
  factor-deflation conventions, scheme-level manager blending) is stamped in rails.caveats.

DISPLAY-PLANE only — additive, no analytics.py touch. The posterior closed-form + _norm_cdf are the
only bits a later human ports to JS (under the parity harness); the prior table + mapping constants
ship as DATA. NO JS work here.
"""
from __future__ import annotations

import math

from . import skill_factors as sf
from . import skill_rails as sr
from . import skill_signals as ssig
from . import skill_posterior as sp

SCHEMA_VERSION = 2
FACTORS = ("MKT", "SMB", "HML", "WML", "QMJ")
DEFINITION = ("posterior over net-of-fee factor-adjusted annual active return; estimated from the "
              "holdings cross-section IC and inferred-trade alpha (high breadth), shrunk to the "
              "SEBI-category prior, mapped to %/yr via the Fundamental Law (α≈TC·IC·√BR_eff·ω); "
              "NAV-IR kept as a slow independent corroborator.")

# the cross-sectional axis the category prior is built on (gross annual active, decimal/yr — the
# closest available proxy to the net-%/yr posterior axis; flagged in the prior basis below).
PRIOR_AXIS = "net_excess"    # net-of-fee active axis (excess_cagr − category-median TER), consistent with the posterior x_hat


def _finite(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _f(x):
    """Clean one value to a JSON-safe float-or-None (mirrors funds_attribution._clean_nan)."""
    try:
        if x is None:
            return None
        xf = float(x)
        return xf if math.isfinite(xf) else None
    except (TypeError, ValueError):
        return None


# ==================================================================================================
# PRIOR TABLE (build-hook helper) — built ONCE over all funds, on the NET-annual-active axis
# ==================================================================================================
def build_prior_table(records, ter_table: dict | None = None) -> dict:
    """Build the empirical-Bayes category prior ONCE from the legacy scheme records (the output of
    funds_attribution.scheme_metrics) — on the NET-annual-active axis (`excess_cagr − category-median
    TER`, decimal/yr), the cross-sectional proxy CONSISTENT with the net-%/yr posterior x_hat (a gross
    +2.84%/yr prior would crown a no-information fund; the net axis + the θ=μ recentre fix that).

    We inject each record's per-fund SE on that axis = tracking_error/√years (the SE of an annualised
    mean active return) so the method-of-moments τ² = Var_xsec(excess) − E[s²] is NOT inflated by
    estimation noise (otherwise a few short-history funds blow τ up and the prior under-shrinks).
    Returns the locked prior_table {category:{prior_mean,prior_sd,n_peers,n0}, "_universe":{…}}."""
    recs = []
    for r in records:
        rr = dict(r)
        # SE of the annualised mean active return ≈ tracking_error / √years (iid approx; honest enough
        # for a universe-level noise term — the same role 1/√years plays for the IR axis in the spec).
        te = rr.get("tracking_error"); yrs = rr.get("years")
        if _finite(te) and _finite(yrs) and float(yrs) > 0:
            rr["se"] = float(te) / math.sqrt(float(yrs))
        # ── put the prior on the SAME net-of-fee axis as the posterior x_hat ──
        # x_hat is net-%/yr (TER already subtracted); a GROSS excess prior (+2.84%/yr) would crown a
        # no-information fund. Subtract the category-median TER proxy so μ_f is net-%/yr too.
        gx = rr.get("excess_cagr")
        if _finite(gx):
            ter_a, _plan, _nb = sr.ter_for(rr.get("sebi_category") or "", rr.get("scheme_name") or "", ter_table)
            rr["net_excess"] = float(gx) - float(ter_a)
        recs.append(rr)
    return sp.build_category_prior(recs, axis=PRIOR_AXIS, min_peers=8, se_mode="sampling")


# ==================================================================================================
# THE INTEGRATOR
# ==================================================================================================
def compute_skill(record: dict, panel_fund: "object", flows_fund: "object | None",
                  legs: "object", prior_table: dict, consensus_by_ym: "object | None" = None,
                  universe_by_ym: "object | None" = None, ter_table: dict | None = None,
                  build_id: str | None = None,
                  bench_fwd_by_ym: "object | None" = None,
                  shared: dict | None = None) -> dict:
    """★ THE INTEGRATOR — assemble one fund's full D.1 `skill` block (everything EXCEPT the two
    panel-relative fields decided in the build hook: rails.passes_fdr and rank.*).

    Parameters
    ----------
    record      : the EXISTING funds_attribution scheme dict (read-only) — supplies info_ratio, t_stat,
                  years, excess_cagr, ic_mean, sebi_category, amc, scheme_name, tracking_error, n_months,
                  ts[] (the per-month series incl. slug). NOT mutated.
    panel_fund  : the per-fund slice of funds_attribution.load_panel() (cols incl. ym, A) — the NAV
                  active series A(t) for the net-of-fee + factor-deflation rails. May be None (then A is
                  reconstructed from record['ts']).
    flows_fund  : the fund_trade_panel DataFrame (Component A trade signals); may be None.
    legs        : the shared factor-leg frame from skill_factors.get_factor_legs() (built once).
    prior_table : the shared category prior from build_prior_table() (built once).
    consensus_by_ym / universe_by_ym / bench_fwd_by_ym / shared : Component-A substrate, passed once.

    Returns the locked `skill` dict with rails.passes_fdr=None and rank={} (filled in fdr_and_rank).
    """
    code = str(record.get("navindia_code"))
    category = record.get("sebi_category") or "Uncategorized"
    amc = record.get("amc")
    scheme_name = record.get("scheme_name") or ""
    n_months = int(record.get("n_months") or 0)
    years = record.get("years")
    te = record.get("tracking_error")
    gross_excess = record.get("excess_cagr")
    as_of = None

    # ── build the monthly active series A(t) from the panel (or from record['ts'] as a fallback) ──
    A = _active_series(panel_fund, record)
    if A is not None and len(A) > 0:
        as_of = str(A.index[-1])[:7] if hasattr(A.index[-1], "__str__") else None

    # ── RAIL 2: net-of-fee (category-median TER proxy) — subtract TER/12 BEFORE deflation ──
    net = sr.net_of_fee(A, category, scheme_name, gross_excess_ann=gross_excess, ter_table=ter_table)
    A_net = net["a_net_monthly"]
    ter_annual = net["ter_annual"]
    net_basis = net["net_basis"]

    # ── RAIL 1: factor (+sector) deflation on the NET active series ──
    sector_S = None
    try:
        sector_S = sf.build_sector_leg({"scheme_name": scheme_name, "sebi_category": category},
                                       log=lambda *a, **k: None)
    except Exception:
        sector_S = None
    defl = sf.deflate(A_net, legs=legs, sector_S=sector_S,
                      gross_excess_ann=(net.get("net_excess_ann") if net.get("net_excess_ann") is not None
                                        else gross_excess))
    # one-tailed p-value of the residual-alpha t (book-level FDR consumes this; None when n_obs<24)
    deflated_p = sr.t_to_one_tailed_p(defl.get("t"), n_months=defl.get("n_obs")) if defl.get("ok") else None

    # ── Component A: bet-level signals (cleaned holding IC = the SHIP default that feeds the posterior)
    sig = _component_a(code, category, panel_fund, flows_fund, record,
                       consensus_by_ym, universe_by_ym, bench_fwd_by_ym, shared)
    ic_cons = sig.get("holding_ic_cons") or {}
    ic_raw = sig.get("holding_ic_raw") or {}
    trade_ic = sig.get("trade_ic") or {}
    avt = sig.get("add_minus_trim") or {}
    batting = sig.get("batting") or {}
    slug = (sig.get("_diag") or {}).get("slug") or {}

    # ── Component B: map the cleaned IC → NET %/yr, shrink toward the category prior ──
    ic = ic_cons.get("x_hat")
    se_ic = ic_cons.get("se")
    br_eff = ic_cons.get("n_bets_eff")
    # x_hat on the net-%/yr axis (Fundamental-Law atom; TER subtracted LAST → already net)
    x_hat = sp.ic_to_annual(ic, br_eff, tc=sp.TC_DEFAULT, omega=sp.OMEGA_ACTIVE, ter=ter_annual) \
        if (_finite(ic) and _finite(br_eff)) else None
    # the SE on the same axis = the same linear scaling of the IC's own (bootstrap/NW) SE
    s_axis = None
    if _finite(se_ic) and _finite(br_eff) and float(br_eff) > 0:
        s_axis = abs(sp.TC_DEFAULT * float(se_ic) * math.sqrt(float(br_eff)) * sp.OMEGA_ACTIVE)

    mu_f, tau_f = sp.prior_for_fund(category, amc, prior_table)
    basis = (f"net-of-fee ({net_basis}, TER≈{ter_annual*100:.2f}%/yr, {net['plan']} plan) factor-deflated "
             f"annual active, from the peer-consensus holding IC mapped via the Fundamental Law "
             f"(TC={sp.TC_DEFAULT}, ω={sp.OMEGA_ACTIVE}); shrunk to the {category} prior on the NET-active "
             f"axis (excess−TER); θ_skill = the category prior mean (relative: P(true skill > peers))")
    # θ recentred to the fund's own prior mean → a no-information fund (best→μ_f) reads p_skilled=0.5
    # (relative skill: P(true skill > category peer average)). 'strong' keeps the +2%/yr margin above it.
    theta_sk = mu_f
    theta_st = mu_f + (sp.THETA_STRONG - sp.THETA_SKILLED)
    post = sp.posterior(x_hat, s_axis, mu_f, tau_f,
                        theta_skilled=theta_sk, theta_strong=theta_st, basis=basis)

    # ── the 5-state tag (passes_fdr decided book-level → start False; re-derived in fdr_and_rank) ──
    tag, tag_label, tag_why = sp.skill_tag(post["p_skilled"], post["lo90"], post["hi90"],
                                           passes_fdr=False, n_months=n_months,
                                           tracking_error=(te if _finite(te) else float("nan")))

    # ── NAV-IR demoted to a slow CHECK (sign-agreement, never a gate) ──
    nav_ir = record.get("info_ratio"); nav_t = record.get("t_stat")
    years_needed = record.get("years_needed")
    nav_corr = _nav_corroborator(nav_ir, nav_t, years, years_needed, post["best"])

    # ── assemble the locked blocks ───────────────────────────────────────────────────────────────
    bet_level = {
        "ic": _f(ic_cons.get("x_hat")), "ic_sd": _f(ic_cons.get("se")), "ic_t": _f(ic_cons.get("fm_t")),
        "n_bets_eff": int(round(ic_cons.get("n_bets_eff"))) if _finite(ic_cons.get("n_bets_eff")) else None,
        "n_bets_naive": int(ic_cons.get("n_bets_naive")) if _finite(ic_cons.get("n_bets_naive")) else None,
        "rho_bar": _f(ic_cons.get("rho_bar")),
        "ic_raw": _f(ic_raw.get("x_hat")), "ic_raw_t": _f(ic_raw.get("fm_t")),
        "route": ic_cons.get("route"),
        "ic_source": "holdings-cross-section (peer-consensus active-weight vs fwd residual return)",
        "caveats": list(ic_cons.get("caveats") or []),
    }
    trade_alpha = {
        "ic": _f(trade_ic.get("x_hat")), "ic_t": _f(trade_ic.get("fm_t")),
        "add_minus_trim_ann": _f(avt.get("x_hat")), "add_minus_trim_t": _f(avt.get("fm_t")),
        "n_trades_eff": int(round(trade_ic.get("n_bets_eff"))) if _finite(trade_ic.get("n_bets_eff")) else None,
        "batting_excess": _f(batting.get("x_hat")), "batting_p_beats_null": _f(batting.get("p_beats_null")),
        "source": "funds_flows.net_active (dw_active), inflow-immune, corp-action-bridged",
        "caveats": list(trade_ic.get("caveats") or []),
    }

    # collate every honesty caveat (rails may ONLY lower skill — flag each gap inline)
    rail_caveats = []
    rail_caveats += list(defl.get("caveats") or [])
    rail_caveats.append(f"net-of-fee uses {net_basis} (no real per-scheme TER feed yet)")
    rail_caveats.append("posterior x_hat = peer-consensus holding-IC mapped via the Fundamental Law "
                        "(TC/ω are estimated category constants — a MODEL, not an identity)")
    rail_caveats.append("category prior built on the NET-active proxy axis (excess_cagr − category TER); "
                        "θ recentred to the category prior mean (relative skill: P > peer average); the "
                        "residual IC-implied-vs-NAV-excess estimator offset is a known approximation "
                        "(proper future fix = a two-pass prior built from the x_hat cross-section itself)")
    if ic_cons.get("caveats"):
        for c in ic_cons["caveats"]:
            if c not in rail_caveats:
                rail_caveats.append(c)
    if trade_ic.get("caveats"):
        for c in trade_ic["caveats"]:
            if c not in rail_caveats:
                rail_caveats.append(c)

    bench_sens = "high" if (_finite(te) and float(te) < 0.03) else ("med" if (_finite(te) and float(te) < 0.06) else "low")

    rails = {
        "fee_adjusted": True, "ter_annual": _f(ter_annual), "plan": net.get("plan"),
        "net_basis": net_basis, "fee_drag_pct": _f(net.get("fee_drag_pct")),
        "net_excess_ann": _f(net.get("net_excess_ann")),
        "factor_deflated": bool(defl.get("ok") and defl.get("factor_deflated")),
        "factors": list(FACTORS) + (["SECTOR"] if defl.get("sector_deflated") else []),
        "sector_deflated": bool(defl.get("sector_deflated")),
        "residual_alpha_ann": _f(defl.get("alpha_ann")), "residual_alpha_t": _f(defl.get("t")),
        "residual_alpha_se": _f(defl.get("se_nw")), "factor_alpha_share": _f(defl.get("factor_alpha_share")),
        "factor_betas": {k: _f(v) for k, v in (defl.get("betas") or {}).items()},
        "factor_r2": _f(defl.get("r2")), "deflate_ok": bool(defl.get("ok")), "deflate_n_obs": int(defl.get("n_obs") or 0),
        "deflated_p": _f(deflated_p),
        "fdr_q": sr.FDR_Q, "passes_fdr": None, "fdr_note": "book-level FDR decided in the build hook over all funds",
        "benchmark_sensitivity": bench_sens,
        "manager_tenure_contaminated": True,   # scheme-level → may blend managers across tenure (always flagged)
        "caveats": rail_caveats,
    }

    skill = {
        "schema_version": SCHEMA_VERSION,
        "posterior": post,
        "tag": tag, "tag_label": tag_label, "tag_why": tag_why,
        "rank": {},                                # filled in fdr_and_rank
        "bet_level": bet_level,
        "trade_alpha": trade_alpha,
        "nav_corroborator": nav_corr,
        "rails": rails,
        "slug_diagnostic": {"slug_cnt": _f(slug.get("slug_cnt")), "slug_aum": _f(slug.get("slug_aum")),
                            "diagnostic_only": True, "caveat": slug.get("caveat")},
        "as_of": as_of, "n_months": n_months, "build_id": build_id,
        "definition": DEFINITION,
    }
    return skill


# ──────────────────────────────────────────────────────────── internals
def _active_series(panel_fund, record):
    """Build the monthly active series A(t) (pandas Series indexed by 'YYYY-MM') from the per-fund
    panel slice if available, else reconstruct from record['ts'] (the per-month A already baked there)."""
    import pandas as pd
    if panel_fund is not None:
        try:
            if hasattr(panel_fund, "columns") and "A" in getattr(panel_fund, "columns", []):
                d = panel_fund.sort_values("ym")
                return pd.Series(d["A"].astype(float).values, index=list(d["ym"]))
        except Exception:
            pass
    ts = record.get("ts") or []
    ym = [p.get("ym") for p in ts if p.get("A") is not None]
    a = [p.get("A") for p in ts if p.get("A") is not None]
    if not ym:
        return pd.Series([], dtype=float)
    return pd.Series([float(x) for x in a], index=ym)


def _component_a(code, category, panel_fund, flows_fund, record,
                 consensus_by_ym, universe_by_ym, bench_fwd_by_ym, shared):
    """Run Component A's signals for ONE fund. Reuses pre-built substrate (shared/consensus/universe/
    bench) so the heavy tables are built once; degrades gracefully to empty tuples on any failure (the
    posterior then sits at the prior — the honest 'no own-record signal')."""
    shared = dict(shared or {})
    if consensus_by_ym is not None:
        shared.setdefault("consensus", consensus_by_ym)
    if universe_by_ym is not None:
        shared.setdefault("universe", universe_by_ym)
    if bench_fwd_by_ym is not None:
        shared.setdefault("bench_fwd", bench_fwd_by_ym)
    try:
        out = ssig.all_signals_for_fund(code, category, shared=shared)
    except Exception:
        out = {}
    # attach the live slug series from the record's ts[] (a windowed average, NOT a recompute)
    try:
        ts = record.get("ts") or []
        slug_series = {"ym": [p.get("ym") for p in ts],
                       "sc": [p.get("sc") for p in ts], "sa": [p.get("sa2") for p in ts]}
        out["_diag"] = {"slug": ssig.slug_diagnostic(None, slug_series=slug_series)}
    except Exception:
        pass
    return out


def _nav_corroborator(nav_ir, nav_t, years, years_needed, post_best):
    """The slow NAV-IR demoted from GATE to CHECK (D.1 block 6): a sign-agreement flag, never a gate.
    'confirms' when the NAV-IR sign agrees with the posterior best AND the NAV-IR is significant
    (|t|≥2); 'contradicts' when significant but the signs disagree; 'uninformative_yet' otherwise (the
    NAV breadth is ~1-3/yr so it usually can't decide before ~years_needed)."""
    agrees = None
    status = "uninformative_yet"
    if _finite(nav_ir) and _finite(post_best):
        agrees = (float(nav_ir) > 0) == (float(post_best) > 0)
    if _finite(nav_t) and abs(float(nav_t)) >= 2.0 and agrees is not None:
        status = "confirms" if agrees else "contradicts"
    return {
        "info_ratio": _f(nav_ir), "t_stat": _f(nav_t), "years": _f(years),
        "years_needed": (int(round(years_needed)) if _finite(years_needed) else None),
        "agrees_with_posterior": agrees, "status": status,
        "role": "slow independent NAV-level corroborator (breadth ~1-3/yr → can't decide before "
                "~years_needed); a sign-check, NOT a gate",
    }


# ==================================================================================================
# BUILD-HOOK helpers (panel-relative, run ONCE over ALL funds)
# ==================================================================================================
def fdr_and_rank(skill_by_fund: dict, q: float = 0.10) -> None:
    """BUILD-HOOK helper (panel-relative, runs once over ALL funds after compute_skill per fund):
      (a) RAIL 3: skill_rails.fdr over every fund's per-fund NET-DEFLATED one-tailed p-value →
          fill rails.passes_fdr (and re-derive the `skilled` tag, which requires passes_fdr).
      (b) RANK: within each SEBI category, rank funds by posterior.p_skilled (ties → posterior.best) →
          fill rank = {basis, within, n_peers, decile(1-10), pctile(0-100)}.
    Mutates `skill_by_fund` IN PLACE ({navindia_code: skill_dict}). Recomputed every build."""
    # ── (a) BOOK-LEVEL FDR over the per-fund net-deflated p-values ──
    pvals = {code: (sk.get("rails", {}).get("deflated_p")) for code, sk in skill_by_fund.items()}
    fdr_res = sr.fdr(pvals, q=q)
    passes = fdr_res.get("passes_fdr", {})
    for code, sk in skill_by_fund.items():
        rails = sk.setdefault("rails", {})
        rails["passes_fdr"] = bool(passes.get(code, False))
        rails["fdr_note"] = (f"Benjamini-Hochberg q={fdr_res.get('fdr_q')} over M={fdr_res.get('M')} "
                             f"testable funds; k*={fdr_res.get('k_star')}; "
                             f"{fdr_res.get('dependence_note')}")
        # re-derive the tag now that passes_fdr is known (only 'skilled' depends on it)
        post = sk.get("posterior", {})
        nav = sk.get("nav_corroborator", {})
        te = None  # tracking_error not stored on the skill block; the original tag already gated index-like
        # only the 'skilled' upgrade is affected; recompute from p_skilled/lo90/hi90 with the real flag
        p = post.get("p_skilled"); lo = post.get("lo90"); hi = post.get("hi90")
        n_months = sk.get("n_months")
        # preserve the original index-like / insufficient_history pre-empts by re-running skill_tag with
        # a benign tracking_error that won't re-trigger index-like (the first pass already set those).
        prev_tag = sk.get("tag")
        if prev_tag in ("index-like", "insufficient_history"):
            continue   # those pre-empts don't depend on FDR — leave untouched
        tag, tag_label, tag_why = sp.skill_tag(p, lo, hi, passes_fdr=rails["passes_fdr"],
                                               n_months=n_months, tracking_error=float("nan"))
        sk["tag"], sk["tag_label"], sk["tag_why"] = tag, tag_label, tag_why

    # ── (b) within-category percentile / decile rank by posterior.p_skilled (ties → best) ──
    by_cat: dict = {}
    for code, sk in skill_by_fund.items():
        cat = (sk.get("posterior", {}).get("basis") and None)  # placeholder; use stored category below
        by_cat.setdefault(_cat_of(sk), []).append(code)
    for cat, codes in by_cat.items():
        def _key(c):
            post = skill_by_fund[c].get("posterior", {})
            p = post.get("p_skilled"); b = post.get("best")
            return (p if _finite(p) else -1.0, b if _finite(b) else -1e9)
        ordered = sorted(codes, key=_key)            # ascending (worst first)
        n = len(ordered)
        for rank0, c in enumerate(ordered):          # rank0=0 is the worst
            pctile = int(round(100.0 * (rank0 + 0.5) / n)) if n > 0 else 0
            decile = min(10, max(1, int(pctile / 10) + 1))
            skill_by_fund[c]["rank"] = {"basis": "p_skilled", "within": cat, "n_peers": n,
                                        "decile": decile, "pctile": pctile}


def _cat_of(sk: dict) -> str:
    """The SEBI category a skill block belongs to (stamped on the nav_corroborator/rank build)."""
    return sk.get("_category") or "Uncategorized"


def manifest_fields(skill: dict) -> dict:
    """The headline posterior fields to merge into _manifest.json[<code>] so the leaderboard sorts
    without fetching every file (D.1 L621-625): {tag, p_skilled, post_best, post_lo90, post_hi90,
    decile, ic_t, nav_ir, n_months, verdict(=tag mirror so old code won't crash)}."""
    post = skill.get("posterior", {})
    nav = skill.get("nav_corroborator", {})
    bet = skill.get("bet_level", {})
    rank = skill.get("rank", {})
    return {
        "tag": skill.get("tag"),
        "verdict": skill.get("tag"),                 # mirror so any legacy reader of 'verdict' won't crash
        "p_skilled": _f(post.get("p_skilled")),
        "post_best": _f(post.get("best")),
        "post_lo90": _f(post.get("lo90")),
        "post_hi90": _f(post.get("hi90")),
        "decile": rank.get("decile"),
        "ic_t": _f(bet.get("ic_t")),
        "nav_ir": _f(nav.get("info_ratio")),
        "n_months": skill.get("n_months"),
    }


def apply_to_record(record: dict, skill: dict) -> dict:
    """Attach the computed `skill` block to a scheme record ADDITIVELY: record["skill"] = skill; the
    legacy flat keys (excess_cagr/info_ratio/t_stat/ic_t/verdict/verdict_why) are RETAINED untouched.
    Stamps the fund's category onto the skill block (_category) so fdr_and_rank can group without the
    record. Returns the augmented record."""
    skill = dict(skill)
    skill["_category"] = record.get("sebi_category") or "Uncategorized"
    record["skill"] = skill
    return record
