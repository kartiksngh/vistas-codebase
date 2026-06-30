"""Self-check for skill_rails.py — NOT part of the build, a scratch harness.
(a) synthetic uniform-null p-vector -> pi0 should be ~1, BH survivors ~0.
(b) the REAL 740 p-values, derived crudely (one-tailed normal) from the live _manifest.json t-stats.
Run: python -m vistas._skill_rails_selfcheck
"""
from __future__ import annotations
import json, os, math, random

from vistas import skill_rails as R


def section(s): print("\n" + "=" * 78 + "\n" + s + "\n" + "=" * 78)


def _net_t(v, R):
    """First-order net-of-fee t for a manifest entry v (shrink t in proportion to fee-eaten excess)."""
    t = v.get("t_stat"); n = v.get("n_months"); exc = v.get("excess_cagr")
    if t is None or not (isinstance(t, (int, float)) and math.isfinite(t)):
        return None
    ter, _, _ = R.ter_for(v.get("category"), v.get("name") or "")
    if exc is not None and math.isfinite(exc) and exc > 1e-6:
        return t * (exc - ter) / exc
    nt = t - abs(ter) * 4.0 * (math.sqrt(n) if n else 1.0) / 100.0
    return min(nt, t)


# ---------- (a) synthetic uniform null ----------
section("(a) SYNTHETIC UNIFORM-NULL p-vector (no skill anywhere) — expect pi0~=1, BH survivors=0")
random.seed(0)
M = 740
pu = [random.random() for _ in range(M)]
fdr_u = R.fdr({i: p for i, p in enumerate(pu)})
print(f"  M={fdr_u['M']}  pi0={fdr_u['bsw']['pi0']:.3f}  pi+={fdr_u['bsw']['pi_plus']:.4f} "
      f" pi-={fdr_u['bsw']['pi_minus']:.4f}")
print(f"  BH k*={fdr_u['k_star']}  survivors={sum(fdr_u['passes_fdr'].values())}  "
      f"(n_sig_good@t>=2 bar={fdr_u['bsw']['n_sig_good']}, n_truly_skilled_est="
      f"{fdr_u['bsw']['n_truly_skilled_est']:.1f})")


# ---------- (b) the REAL 740 ----------
section("(b) REAL 740 funds — one-tailed p derived from live _manifest.json t-stats")
mpath = os.path.join(os.path.dirname(__file__), "..", "data", "funds_attribution", "_manifest.json")
mpath = os.path.abspath(mpath)
man = json.load(open(mpath))
print(f"  manifest: {mpath}  ({len(man)} funds)")

pvals = {}
n_no_t = 0
for code, v in man.items():
    t = v.get("t_stat")
    n = v.get("n_months")
    if t is None or not (isinstance(t, (int, float)) and math.isfinite(t)):
        pvals[code] = None      # "insufficient history" funds -> untestable, excluded from M
        n_no_t += 1
        continue
    pvals[code] = R.t_to_one_tailed_p(float(t), int(n) if n else None)

n_t2 = sum(1 for v in man.values()
           if isinstance(v.get("t_stat"), (int, float)) and math.isfinite(v["t_stat"]) and v["t_stat"] >= 2)
print(f"  GROSS baseline: funds with t>=2 = {n_t2}  (the legacy 'skilled' tally)")
print(f"  funds with NO t_stat (insufficient history, excluded from M) = {n_no_t}")

fdr_r = R.fdr(pvals)
bsw = fdr_r["bsw"]
print(f"\n  M (testable funds)          = {fdr_r['M']}")
print(f"  Storey pi0  (no-skill frac) = {bsw['pi0']:.3f}")
print(f"  pi+ (truly-skilled frac)    = {bsw['pi_plus']:.4f}  -> ~{bsw['pi_plus']*fdr_r['M']:.1f} funds")
print(f"  pi- (truly-unskilled frac)  = {bsw['pi_minus']:.4f}  -> ~{bsw['pi_minus']*fdr_r['M']:.1f} funds")
print(f"  n_sig_good (cleared t>=2 bar) = {bsw['n_sig_good']}")
print(f"  n_lucky_good_est            = {bsw['n_lucky_good_est']:.1f}")
print(f"  n_truly_skilled_est (excess)= {bsw['n_truly_skilled_est']:.1f}")
print(f"\n  BH q=0.10  k* = {fdr_r['k_star']}  p_cutoff={fdr_r['p_cutoff']:.5f}")
print(f"  BH survivors on GROSS p (published 'skilled') = {sum(fdr_r['passes_fdr'].values())}")
print("  NOTE: BH on the GROSS, pre-deflation, pre-fee p-vector barely moves the 152 (cutoff lands")
print("  at t~=1.96, marginally LENIENT) — because these gross t-stats are INFLATED (no factor")
print("  deflation, no fees). That is exactly why the spec composes the rails IN ORDER: RAIL 1+2")
print("  push the p-values right FIRST, then RAIL 3/BH culls the residual luck tail. We show the")
print("  RAIL-2 leg of that composition below (RAIL 1 deflation lives in skill_factors.py).")

# ---- RAIL 2 composed onto the panel: recompute each fund's t NET of the category-median TER ----
# First-order: for a fixed tracking error, IR (hence t=IR*sqrt(yrs)) scales linearly with excess.
# net_t = t_stat * (excess_cagr - TER) / excess_cagr   (only where excess_cagr>0; else net_t<=t).
section("RAILS COMPOSE — RAIL 2 (net-of-fee proxy) applied to the panel, then RAIL 3/BH")
pvals_net = {}
for code, v in man.items():
    nt = _net_t(v, R)
    n = v.get("n_months")
    pvals_net[code] = None if nt is None else R.t_to_one_tailed_p(nt, int(n) if n else None)

n_t2_net = sum(1 for v in man.values()
               if (_net_t(v, R) or -9) >= 2)
fdr_net = R.fdr(pvals_net)
bsw_net = fdr_net["bsw"]
print(f"  after RAIL 2 (category-median TER, net_basis='{R.NET_BASIS}'):")
print(f"    funds still t>=2 NET = {n_t2_net}   (was {n_t2} gross)")
print(f"    Storey pi0 = {bsw_net['pi0']:.3f}   pi+ = {bsw_net['pi_plus']:.4f} "
      f"(~{bsw_net['pi_plus']*fdr_net['M']:.0f} funds)   pi- = {bsw_net['pi_minus']:.4f}")
print(f"    BH q=0.10  k* = {fdr_net['k_star']}  survivors = {sum(fdr_net['passes_fdr'].values())}")
print(f"    ===> gross t>=2: {n_t2}  -> net t>=2: {n_t2_net}  -> BH(net) survivors: "
      f"{sum(fdr_net['passes_fdr'].values())}")

# honesty guard on the COMPOSED pipeline: net survivors must be <= gross t>=2 (a rail only lowers)
assert sum(fdr_net["passes_fdr"].values()) <= n_t2, "RAIL VIOLATION: net BH raised the skilled count!"
assert n_t2_net <= n_t2, "RAIL VIOLATION: net-of-fee raised the t>=2 count!"
print("\n  HONESTY GUARD ok: RAIL 2 only LOWERED both the t>=2 count and the BH survivor count.")

# Storey-sharpened variant (recovers a little power) on the gross panel, for reference
fdr_s = R.fdr(pvals, use_storey=True)
print(f"\n  (ref) BH+Storey on GROSS panel survivors = {sum(fdr_s['passes_fdr'].values())} "
      f"(k*={fdr_s['k_star']}, lenient by design)")


# ---------- RAIL 2 spot check ----------
section("RAIL 2 net-of-fee — spot checks on real categories")
for cat, nm in [("Small Cap Fund", "Foo Small Cap Fund - Direct (G)"),
                ("Large Cap Fund", "Bar Large Cap Fund (G)"),
                ("Sectoral / Thematic", "Baz Pharma Fund (G)"),
                ("Equity Savings", "Qux Equity Savings Fund (G)")]:
    nf = R.net_of_fee([0.01, 0.00, -0.005], cat, nm, gross_excess_ann=0.025)
    print(f"  {cat:35s} plan={nf['plan']:7s} TER={nf['ter_annual']*100:.2f}%/yr "
          f"monthly_drag={nf['monthly_drag']*100:.3f}%  fee_drag_pct={nf['fee_drag_pct']:.2f} "
          f"net_excess={nf['net_excess_ann']*100:+.2f}%/yr  basis={nf['net_basis']}")
print("  detect_plan('X - Direct (G)') =", R.detect_plan("X - Direct (G)"),
      "| detect_plan('Y (G)') =", R.detect_plan("Y (G)"))
