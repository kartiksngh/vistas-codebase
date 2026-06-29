#!/usr/bin/env python
"""cio_firmtest.py — the FROZEN CIO firm-replay evaluator (autoresearch tag: cio-jun30).

THE SACRED HARNESS (read-only inside the loop). Analog of Karpathy's evaluate_bpb and the OWED
signal_navtest.py. It scores the CIO arithmetic (vistas/cio.py) by the Fundamental Law at FIRM scale:

    IR_firm  must exceed the average manager ONLY if the desks are DE-CORRELATED  (team-IR = s·√M_eff).

So the CIO is graded on BREADTH cultivated and CROWDING killed — exactly the √BR effective-breadth
collapse the contract names (the live CIO already caught ONGC top in 14/27 books → ~5-6 independent bets,
not 27).

★ THE FIRM = the 4 CROSS-AMC CONTRACT PILOTS (operator-fixed 2026-06-30), NOT the 28-desk ABSL firm:
  - ICICI Prudential — Large Cap Fund            (bench NIFTY 100)
  - SBI — Equity Hybrid / Aggressive Hybrid      (bench NIFTY 500)
  - Aditya Birla Sun Life — Flexi Cap Fund       (bench NIFTY 500)
  - Quant Mutual — Small Cap Fund                (bench NIFTY SMALLCAP 250)
  M = 4 desks. HONEST CAVEAT (carried in every verdict): with only 4 desks the √BR breadth HEADROOM is
  small, so the de-correlation lift demonstrable here is BOUNDED — report the bound, never over-claim.
  On the VALIDATION era (2015-2020) these 4 have mean pairwise active-ret ρ̄≈0.32 → M_eff≈2.0 (the
  large-cap trio ICICI/SBI/ABSL is one crowded bet at ρ 0.55-0.76; Quant SmallCap is the de-correlated
  diversifier at ρ≈0). Per-pilot is tracked so one book's tilt cannot masquerade as firm skill.

WHAT IT READS (no fabrication, no network, point-in-time, total return, delisted retained):
  amc_book/<AMC>/<scheme>/replay/
      nav.csv            daily book NAV (rules-FM, survivorship-clean, look-ahead-free; make_*_books.py)
      benchmark_nav.csv  daily NSE TR benchmark NAV for THAT desk's mandate (its OWN benchmark)
      scorecard.json     per-desk IR / IC / TC / benchmark (the Fundamental-Law decomposition)
  → desk active-return θ_d,t = r_nav,d,t − r_bench,d,t  (excess over the desk's OWN benchmark = the skill
    unit; a desk's excess over its OWN mandate index, so benchmark beta never masquerades as alpha).

THE CIO ARITHMETIC IT SCORES (mutable = vistas/cio.py):
  cio.firm_weights(desks, params) → a dict {desk_name: weight≥0, Σ=1}. The firm active return is
  θ_firm,t = Σ_d w_d θ_d,t. The evaluator turns that into IR_firm + the s·√M_eff decomposition + the
  full gauntlet. The loop edits ONLY vistas/cio.py; this file never changes.

DISCIPLINE: paper-only; no look-ahead (weights from an in-sample window, scored on the same validation
era — walk-forward variant in gate `wf`); total return; FDR/luck bar; tilt guard; net-of-fee GATED.

SEALED HOLDOUT (operator-set 2026-06-30 = last ~5y, harsher: includes the 2022 drawdown):
  era='val'     = 2015-01-30 → 2020-12-31  (all the loop ever sees)
  era='holdout' = 2021-01-01 → 2026-06-25  (one-shot at the very end; the loop must NOT touch it)

Run:  python cio_firmtest.py            # baseline (equal weight) on validation era, prints OBJECTIVE + GATE
      python cio_firmtest.py --fast     # cheap inner gate only (screening; never promotes)
      python cio_firmtest.py --era holdout   # the sealed one-shot (END ONLY)
"""
from __future__ import annotations
import os, sys, json, glob, argparse, time
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
BOOK_DIR = os.path.join(ROOT, "amc_book")

# ★ THE FIRM = the 4 cross-AMC contract pilots (operator-fixed 2026-06-30). Each entry = the replay dir.
PILOTS = {
    "ICICI_LargeCap": "ICICI Prudential Mutual Fund/ICICI Pru Large Cap Fund _G_",
    "SBI_AggHybrid":  "SBI Mutual Fund/SBI Equity Hybrid Fund _G_",
    "ABSL_FlexiCap":  "Aditya Birla Sun Life Mutual Fund/Aditya Birla SL Flexi Cap Fund _G_",
    "Quant_SmallCap": "Quant Mutual Fund/Quant Small Cap Fund - _G_",
}

# ── eras (the SEAL). Validation = strictly before the holdout. Holdout = last ~5y (incl. 2022 DD). ──
HOLDOUT_START = "2021-01-01"
ERA_BOUNDS = {
    "val":     (None,            HOLDOUT_START),    # (start_inclusive, end_exclusive) — 2015..2020-12-31
    "holdout": (HOLDOUT_START,   None),             # 2021-01-01 .. end (ONE-SHOT, end only)
    "full":    (None,            None),
}
# era-stability sub-eras WITHIN validation (distinct regimes; none touch the holdout)
SUB_ERAS = [("2015-2016", "2015-01-01", "2016-12-31"),
            ("2017-2018", "2017-01-01", "2018-12-31"),
            ("2019-2020", "2019-01-01", "2020-12-31")]

TRADING_DAYS = 252
RNG_SEED = 20260630            # fixed — reproducible random baselines, no Math.random


# ════════════════════════════════════════════════════ data load (cached across calls in one process)
_PANEL_CACHE = None

def load_desk_panel(log=lambda *a, **k: None):
    """Load the 4 pilot desks → a daily active-return panel A (date × desk) + per-desk meta.
    Returns dict: A (DataFrame), meta (per-desk IR/IC/TC/bench/cat), axis. Cached."""
    global _PANEL_CACHE
    if _PANEL_CACHE is not None:
        return _PANEL_CACHE
    desks = []
    for nm, rel in PILOTS.items():
        rd = os.path.join(BOOK_DIR, rel, "replay")
        try:
            nav = pd.read_csv(os.path.join(rd, "nav.csv"), parse_dates=["date"]).set_index("date")["nav"]
            bnav = pd.read_csv(os.path.join(rd, "benchmark_nav.csv"), parse_dates=["date"]).set_index("date")["nav"]
            d = json.load(open(os.path.join(rd, "scorecard.json"), encoding="utf-8"))
        except Exception as e:
            raise SystemExit(f"[load] pilot {nm} missing/broken at {rd}: {e}")
        s = d.get("scorecard", {})
        desks.append({
            "name": nm,
            "nav": nav, "bnav": bnav,
            "cat": d.get("category"),
            "bench": s.get("benchmark_name"),
            "ir": (s.get("benchmark", {}) or {}).get("info_ratio"),
            "ic": (s.get("fundamental_law", {}) or {}).get("ic_mean"),
            "tc": (s.get("fundamental_law", {}) or {}).get("transfer_coefficient"),
            "brain": (d.get("diag", {}) or {}).get("brain"),
        })
    if not desks:
        raise SystemExit("no pilot desks found under " + BOOK_DIR)
    # common daily axis
    axis = None
    for x in desks:
        axis = x["nav"].index if axis is None else axis.intersection(x["nav"].index)
    axis = axis.sort_values()
    # daily active-return panel θ = r_nav − r_bench (excess over the desk's OWN benchmark)
    A = {}
    pol = {}    # policy-benchmark daily return per desk (for the tilt guard)
    for x in desks:
        r = x["nav"].reindex(axis).pct_change()
        rb = x["bnav"].reindex(axis).pct_change()
        A[x["name"]] = (r - rb)
        pol[x["name"]] = rb
    A = pd.DataFrame(A).iloc[1:]                 # drop the first NaN row
    POL = pd.DataFrame(pol).iloc[1:]
    meta = {x["name"]: {k: x[k] for k in ("cat", "bench", "ir", "ic", "tc", "brain")} for x in desks}
    _PANEL_CACHE = {"A": A, "POL": POL, "meta": meta, "axis": A.index, "names": list(A.columns)}
    log(f"[load] {len(desks)} desks · axis {A.index.min().date()}..{A.index.max().date()} ({len(A)} days)")
    return _PANEL_CACHE


def era_slice(df, era):
    """Slice a date-indexed frame to an era (validation or holdout). The SEAL lives here."""
    if era in ERA_BOUNDS:
        lo, hi = ERA_BOUNDS[era]
        m = pd.Series(True, index=df.index)
        if lo is not None:
            m &= df.index >= pd.Timestamp(lo)
        if hi is not None:
            m &= df.index < pd.Timestamp(hi)       # end EXCLUSIVE → holdout never overlaps val
        return df[m]
    raise ValueError(f"unknown era {era}")


# ════════════════════════════════════════════════════ the Fundamental-Law firm metrics
def _ann_ir(theta: pd.Series):
    """Annualised IR of a daily active-return series: mean·252 / (std·√252)."""
    t = theta.dropna()
    if len(t) < 30:
        return float("nan")
    mu, sd = t.mean(), t.std(ddof=1)
    if not np.isfinite(sd) or sd == 0:
        return float("nan")
    return (mu * TRADING_DAYS) / (sd * np.sqrt(TRADING_DAYS))


def _rho_bar(A: pd.DataFrame, w: np.ndarray):
    """Weight-emphasised mean pairwise correlation of desk active returns. We weight each pair by
    w_i·w_j so the crowding number reflects the desks the firm actually leans on (a desk at weight 0
    cannot crowd the firm). Returns ρ̄ in [−1,1]."""
    C = A.corr().values
    n = len(w)
    num = den = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            cij = C[i, j]
            if not np.isfinite(cij):
                continue
            ww = w[i] * w[j]
            num += ww * cij
            den += ww
    return (num / den) if den > 0 else float("nan")


def _m_eff(w: np.ndarray, rho: float):
    """Effective breadth from the weight concentration AND the crowding ρ̄.
    Two haircuts compose: (1) weight concentration — a firm that puts all weight on one desk has
    breadth 1 regardless of how many desks exist: N_w = 1/Σ w² (the participation ratio / inverse-HHI);
    (2) correlation — Qian-Hua/Buckle: M_eff = N / (1+(N−1)ρ̄). Compose on N_w."""
    nw = 1.0 / np.sum(w ** 2)                      # effective # of desks the firm actually rides
    if not np.isfinite(rho):
        return nw
    rho = max(rho, 0.0)                            # negative ρ̄ would inflate breadth — cap at 0 (honest)
    return nw / (1.0 + (nw - 1.0) * rho)


def firm_metrics(A: pd.DataFrame, meta: dict, w_map: dict):
    """Core: given desk weights, compute the firm active-return series, IR_firm, and the s·√M_eff
    decomposition. w_map = {desk_name: weight}. Returns a dict."""
    names = list(A.columns)
    w = np.array([max(0.0, float(w_map.get(nm, 0.0))) for nm in names])
    sw = w.sum()
    if sw <= 0:
        return {"ir_firm": float("nan"), "err": "zero weight"}
    w = w / sw
    theta_firm = A.values @ w                      # daily firm active return
    theta_firm = pd.Series(theta_firm, index=A.index)
    ir_firm = _ann_ir(theta_firm)
    # decomposition: s = weighted mean desk IR; M_eff from concentration + crowding
    desk_ir = np.array([meta[nm].get("ir") if meta[nm].get("ir") is not None else np.nan for nm in names])
    s = float(np.nansum(w * desk_ir) / np.nansum(w[np.isfinite(desk_ir)])) if np.isfinite(desk_ir).any() else float("nan")
    rho = _rho_bar(A, w)
    meff = _m_eff(w, rho)
    implied = s * np.sqrt(meff) if np.isfinite(s) else float("nan")
    return {
        "ir_firm": ir_firm, "theta_firm": theta_firm,
        "s": s, "rho_bar": rho, "m_eff": meff, "implied_team_ir": implied,
        "n_w": 1.0 / np.sum(w ** 2), "w": dict(zip(names, w)),
    }


# ════════════════════════════════════════════════════ the GAUNTLET
def _random_weightings_ir(A, meta, n_random, n_active, rng):
    """≥10k random desk-weightings of the SAME SHAPE (n_active desks, Dirichlet on the simplex):
    the firm IR distribution under random allocation. Returns the array of IRs."""
    names = list(A.columns)
    M = len(names)
    Av = A.values
    out = np.empty(n_random)
    for k in range(n_random):
        idx = rng.choice(M, size=min(n_active, M), replace=False)
        ww = rng.dirichlet(np.ones(len(idx)))
        w = np.zeros(M); w[idx] = ww
        theta = Av @ w
        mu, sd = theta.mean(), theta.std(ddof=1)
        out[k] = (mu * TRADING_DAYS) / (sd * np.sqrt(TRADING_DAYS)) if sd > 0 else np.nan
    return out


def _block_bootstrap_pval(theta: pd.Series, block=3, n_boot=2000, rng=None):
    """Tilt-matched circular block bootstrap of the firm active-return MEAN: p-value that the mean
    is > 0 by luck. block≈3 days. Returns p (share of resamples with mean ≤ 0)."""
    if rng is None:
        rng = np.random.default_rng(RNG_SEED)
    x = theta.dropna().values
    n = len(x)
    if n < 30:
        return float("nan")
    nb = int(np.ceil(n / block))
    means = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n, size=nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % n
        means[b] = x[idx][:n].mean()
    return float(np.mean(means <= 0.0))


def _beta_on_policy(theta_firm, A, POL, w_map):
    """Tilt guard: beta of the firm active return on the firm POLICY-benchmark return (the
    weight-blended desk benchmarks). If the 'excess' is really a benchmark-beta tilt this is large.
    Active return SHOULD be ~orthogonal to the policy benchmark (beta≈0)."""
    names = list(A.columns)
    w = np.array([max(0.0, float(w_map.get(nm, 0.0))) for nm in names]); w = w / w.sum()
    pol_firm = pd.Series(POL.values @ w, index=POL.index).reindex(theta_firm.index)
    df = pd.concat([theta_firm.rename("a"), pol_firm.rename("b")], axis=1).dropna()
    if len(df) < 30 or df["b"].std() == 0:
        return float("nan")
    beta = np.cov(df["a"], df["b"])[0, 1] / np.var(df["b"])
    return float(beta)


def run_gauntlet(weights_fn, params, era="val", n_random=10000, fast=False, log=lambda *a, **k: None):
    """The full gauntlet on the CIO arithmetic `weights_fn(desks_meta, params) -> {name: w}`.
    era MUST be 'val' inside the loop. Returns (objective, gate_dict, decomp_dict, detail)."""
    P = load_desk_panel(log)
    meta = P["meta"]
    A_full = P["A"]; POL_full = P["POL"]
    A = era_slice(A_full, era); POL = era_slice(POL_full, era)
    rng = np.random.default_rng(RNG_SEED)

    # the CIO arithmetic chooses weights (provenance: from desk meta = validated IR/IC/TC + crowding)
    w_map = weights_fn(meta, A, params)
    n_active = sum(1 for v in w_map.values() if v > 1e-9)
    fm = firm_metrics(A, meta, w_map)
    ir_firm = fm["ir_firm"]
    objective = -ir_firm                          # MINIMISE −IR_firm

    gate = {}
    decomp = {}

    # ── decomposition (always reported) ──
    decomp["s"] = round(fm["s"], 3) if np.isfinite(fm["s"]) else None
    decomp["rho_bar"] = round(fm["rho_bar"], 3) if np.isfinite(fm["rho_bar"]) else None
    decomp["m_eff"] = round(fm["m_eff"], 2) if np.isfinite(fm["m_eff"]) else None
    decomp["n_active"] = n_active
    decomp["implied_team_ir"] = round(fm["implied_team_ir"], 3) if np.isfinite(fm["implied_team_ir"]) else None
    # ── per-pilot tracking: standalone IR + firm weight, so one book's tilt can't masquerade as
    #    firm skill (operator requirement; M=4 means a single book dominates easily) ──
    decomp["per_pilot"] = {nm: {"ir": round(_ann_ir(A[nm]), 3), "w": round(fm["w"].get(nm, 0.0), 3)}
                           for nm in A.columns}

    # ── 1. random baseline (≥10k Dirichlet weightings of the same shape) ──
    nr = 1000 if fast else n_random
    rand_irs = _random_weightings_ir(A, meta, nr, max(n_active, 2), rng)
    pct = float(np.mean(rand_irs[np.isfinite(rand_irs)] < ir_firm)) if np.isfinite(ir_firm) else 0.0
    gate["rand"] = "PASS" if pct >= 0.95 else "FAIL"
    decomp["rand_pct"] = round(pct, 3)

    # ── 3. beats the single best desk standalone IR (else firm adds no breadth) ──
    best_desk_ir = max((_ann_ir(A[nm]) for nm in A.columns), default=float("nan"))
    gate["single"] = "PASS" if (np.isfinite(ir_firm) and np.isfinite(best_desk_ir) and ir_firm > best_desk_ir) else "FAIL"
    decomp["best_desk_ir"] = round(best_desk_ir, 3) if np.isfinite(best_desk_ir) else None

    if fast:
        # cheap inner gate: rand + single + objective only (NEVER promotes; just screens)
        gate["wf"] = gate["era"] = gate["plateau"] = gate["luck"] = gate["fdr"] = gate["tilt"] = "SKIP"
        gate["fee"] = "GATED"
        return objective, gate, decomp, {"fm": fm, "fast": True}

    # ── 2. walk-forward: weights from data strictly BEFORE the scored window ──
    # split the era in half by time; choose weights on the FIRST half, score IR on the SECOND half.
    n = len(A); mid = n // 2
    A1, A2 = A.iloc[:mid], A.iloc[mid:]
    w_wf = weights_fn(meta, A1, params)            # weights learned on the past only
    fm_wf = firm_metrics(A2, meta, w_wf)           # scored on the future-only slice
    # compare to equal-weight on the same future slice (the no-skill allocator)
    eq = {nm: 1.0 for nm in A2.columns}
    ir_wf_eq = firm_metrics(A2, meta, eq)["ir_firm"]
    gate["wf"] = "PASS" if (np.isfinite(fm_wf["ir_firm"]) and np.isfinite(ir_wf_eq)
                            and fm_wf["ir_firm"] >= ir_wf_eq - 1e-9) else "FAIL"
    decomp["ir_wf_oos"] = round(fm_wf["ir_firm"], 3) if np.isfinite(fm_wf["ir_firm"]) else None
    decomp["ir_wf_eq"] = round(ir_wf_eq, 3) if np.isfinite(ir_wf_eq) else None

    # ── 4. era stability: firm IR sign holds across the 3 sub-eras (distinct regimes) ──
    era_irs = {}
    for nm, s0, s1 in SUB_ERAS:
        sub = A[(A.index >= pd.Timestamp(s0)) & (A.index <= pd.Timestamp(s1))]
        if len(sub) < 60:
            era_irs[nm] = None; continue
        era_irs[nm] = round(firm_metrics(sub, meta, w_map)["ir_firm"], 3)
    vals = [v for v in era_irs.values() if v is not None]
    gate["era"] = "PASS" if (len(vals) >= 2 and all(v > 0 for v in vals)) else "FAIL"
    decomp["era_irs"] = era_irs

    # ── 7. tilt guard: firm active return ~orthogonal to the policy benchmark (beta≈0) ──
    beta = _beta_on_policy(fm["theta_firm"], A, POL, w_map)
    gate["tilt"] = "PASS" if (np.isfinite(beta) and abs(beta) <= 0.25) else "FAIL"
    decomp["beta_on_policy"] = round(beta, 3) if np.isfinite(beta) else None

    # ── 6. luck bar (block bootstrap) ──
    p_luck = _block_bootstrap_pval(fm["theta_firm"], block=3, n_boot=2000, rng=rng)
    gate["luck"] = "PASS" if (np.isfinite(p_luck) and p_luck < 0.05) else "FAIL"
    decomp["p_luck"] = round(p_luck, 4) if np.isfinite(p_luck) else None
    gate["fdr"] = "NA"        # session-level (applied across trials in the summary)

    # ── 8. net-of-fee — GATED (no TER data) ──
    gate["fee"] = "GATED"

    # plateau is evaluated by the LOOP (needs neighbour params) → reported NA here, filled by caller
    gate["plateau"] = "NA"

    return objective, gate, decomp, {"fm": fm, "fm_wf": fm_wf, "best_desk_ir": best_desk_ir,
                                     "rand_irs_summary": {"mean": float(np.nanmean(rand_irs)),
                                                          "p95": float(np.nanpercentile(rand_irs, 95))}}


# ════════════════════════════════════════════════════ CLI (baseline / fast / holdout)
def _fmt_gate(g):
    return " ".join(f"{k}:{v}" for k, v in g.items())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--era", default="val", choices=list(ERA_BOUNDS))
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--params", default=None, help="JSON dict of CIO params (else cio.DEFAULT_PARAMS)")
    args = ap.parse_args()

    import importlib
    cio = importlib.import_module("vistas.cio")
    importlib.reload(cio)
    params = json.loads(args.params) if args.params else dict(cio.DEFAULT_PARAMS)

    t0 = time.time()
    obj, gate, decomp, detail = run_gauntlet(cio.firm_weights, params, era=args.era,
                                             fast=args.fast, log=print)
    dt = time.time() - t0
    print("=" * 78)
    print(f"CIO FIRM-REPLAY — era={args.era}  params={params}")
    print(f"OBJECTIVE  -IR_firm = {obj:.6f}   (IR_firm = {-obj:.4f})")
    print(f"DECOMP     s={decomp.get('s')}  rho_bar={decomp.get('rho_bar')}  M_eff={decomp.get('m_eff')}"
          f"  n_active={decomp.get('n_active')}  implied s*sqrt(Meff)={decomp.get('implied_team_ir')}"
          f"  best_desk_IR={decomp.get('best_desk_ir')}")
    print(f"GATE       {_fmt_gate(gate)}")
    print(f"DETAIL     wf_oos={decomp.get('ir_wf_oos')} vs eq {decomp.get('ir_wf_eq')} | "
          f"era_irs={decomp.get('era_irs')} | beta={decomp.get('beta_on_policy')} | p_luck={decomp.get('p_luck')}")
    print(f"WALLCLOCK  {dt:.1f}s")
    print("=" * 78)

if __name__ == "__main__":
    main()
