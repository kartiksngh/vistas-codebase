"""
_gauntlet.py — the ANALYST autoresearch gauntlet driver (tag analyst-jun30).

Reads the FROZEN evaluator (vistas.mesh_research.evaluate / _ic_block convention) and the MUTABLE
vistas.mesh_research.desk_signal() / DESK_PARAMS. Runs the discipline gauntlet on the VALIDATION era
ONLY (scoring dates <= 2023-06-30). The sealed holdout (>= 2023-07-01) is NEVER touched here.

This file is a driver, not part of the evaluator: it only CALLS the frozen IC convention. It does not
alter any formula. Usage:
    python _gauntlet.py '<json params>'     -> prints a one-line OBJ/GATE summary + a JSON blob
    python _gauntlet.py holdout '<json>'    -> the one-shot SEALED-HOLDOUT test (scoring >= 2023-07-01)

Conventions (so numbers are reproducible):
  IC = monthly cross-sectional Spearman rank corr between desk_signal(t) and ret_fwd_6m(t->t+6),
       averaged across scored months (>=30 names/month). Exactly mesh_research._ic_block.
  Fama-MacBeth t = mean_IC / (std_IC / sqrt(n_months)).
  VALIDATION = scoring month_end in [2013-01-01, 2023-06-30]. HOLDOUT = [2023-07-01, 2026-12-31].
"""
import sys, json, time
import numpy as np
import pandas as pd
from vistas import mesh_research as mr

VAL_END = pd.Timestamp("2020-12-31")    # operator update 2026-06-30: seal last ~5y
HOLD_START = pd.Timestamp("2021-01-01")  # HOLDOUT = 2021-01 .. 2026-06 (harsher, incl 2022 rate-shock)
H = 6                       # headline horizon (ARM's natural peak)
BLOCK = 3                   # bootstrap block length (months)
NBOOT = 2000

_PANEL = None
def panel():
    global _PANEL
    if _PANEL is None:
        _PANEL = pd.read_parquet(mr.PANEL_PATH)
    return _PANEL


def _ic_series_masked(score_wide, fwd_wide, date_mask):
    """Per-month Spearman IC of score vs fwd, ONLY for scoring months in date_mask. Returns
    (months list, ics list, counts list) — the raw FM inputs."""
    months, ics, counts = [], [], []
    for t in score_wide.index:
        if not date_mask(t) or t not in fwd_wide.index:
            continue
        a = score_wide.loc[t]; r = fwd_wide.loc[t]
        common = a.index.intersection(r.index)
        a, r = a[common], r[common]
        ok = a.notna() & r.notna()
        if ok.sum() < mr.MIN_XS:
            continue
        ic = a[ok].corr(r[ok], method="spearman")
        if pd.isna(ic):
            continue
        months.append(t); ics.append(float(ic)); counts.append(int(ok.sum()))
    return months, ics, counts


def _fm(ics):
    ics = np.asarray(ics, float)
    n = len(ics)
    m = float(np.mean(ics)) if n else float("nan")
    sd = float(np.std(ics, ddof=1)) if n > 2 else 0.0
    t = float(m / (sd / np.sqrt(n))) if (n > 2 and sd > 0) else float("nan")
    return m, t, n, sd


def _block_bootstrap_t(ics, nboot=NBOOT, block=BLOCK, seed=0):
    """Circular block bootstrap of the mean IC: resample blocks of consecutive months with
    replacement, rebuild a series of the same length, recompute mean. Report the share of
    resamples with mean>0 (a one-sided luck bar) and the bootstrap t = mean/se_boot."""
    ics = np.asarray(ics, float)
    n = len(ics)
    if n < block + 1:
        return None
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    means = np.empty(nboot)
    for b in range(nboot):
        starts = rng.integers(0, n, size=nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % n
        means[b] = ics[idx[:n]].mean()
    frac_pos = float(np.mean(means > 0))
    se = float(np.std(means, ddof=1))
    tb = float(np.mean(ics) / se) if se > 0 else float("nan")
    return {"frac_pos": round(frac_pos, 4), "boot_t": round(tb, 2), "se": round(se, 5)}


def _size_neutralise(score_wide):
    """Cross-sectionally orthogonalise the score vs log-mcap each month (residualise on size),
    so the tilt guard can ask: does IC survive after removing any size tilt?"""
    mcap = mr._wide_from_panel(panel(), "mcap_cr")
    lm = np.log(mcap.replace(0.0, np.nan))
    return mr._orth_vs(score_wide, lm)


def _single_force_ic(col, mask_wide, val_mask):
    """IC@6m of a single force restricted to the same (month,symbol) cells as the composite mask."""
    z = mr._xs_z_wide(mr._wide_from_panel(panel(), col)).where(mask_wide)
    fwd = mr._wide_from_panel(panel(), f"ret_fwd_{H}m")
    _, ics, _ = _ic_series_masked(z, fwd, val_mask)
    m, t, n, sd = _fm(ics)
    return round(m, 4) if n else None


def run(params, holdout=False):
    # apply params to the mutable DESK_PARAMS
    mr.DESK_PARAMS.update(params)
    p = panel()
    score = mr.desk_signal(p)
    fwd = mr._wide_from_panel(p, f"ret_fwd_{H}m")

    if holdout:
        date_mask = lambda t: t >= HOLD_START
        era_label = "HOLDOUT"
    else:
        date_mask = lambda t: t <= VAL_END
        era_label = "VALIDATION"

    months, ics, counts = _ic_series_masked(score, fwd, date_mask)
    m, t, n, sd = _fm(ics)
    obj = -m  # minimise -IC

    # mask of where the composite is present (for same-rows single-force comparison)
    mask_wide = score.notna()

    # ---- single-beat: vs ARM on same rows + vs each single force ----
    arm_same = _single_force_ic("arm_level", mask_wide, date_mask)
    forces = ["arm_trend_3m", "flow_intensity_3m", "dbreadth", "mom_6m", "mom_12m", "value_z", "quality_score"]
    single = {c: _single_force_ic(c, mask_wide, date_mask) for c in forces}
    single["arm_level"] = arm_same
    best_single = max([v for v in single.values() if v is not None], default=None)
    beats_single = bool(m is not None and best_single is not None and m > best_single + 1e-9)

    # ---- era stability (within the evaluated era) ----
    # validation eras must lie INSIDE the validation window (<=2020-12); holdout split into 2 sub-eras
    VAL_ERAS = [("2013-2016", "2013-01-01", "2016-12-31"),
                ("2017-2018", "2017-01-01", "2018-12-31"),
                ("2019-2020", "2019-01-01", "2020-12-31")]
    HOLD_ERAS = [("2021-2022", "2021-01-01", "2022-12-31"),
                 ("2023-2024", "2023-01-01", "2024-12-31"),
                 ("2025-2026", "2025-01-01", "2026-12-31")]
    eras = HOLD_ERAS if holdout else VAL_ERAS
    ic_by_era = {}
    for nm, s0, s1 in eras:
        sub = [ic for mo, ic in zip(months, ics) if pd.Timestamp(s0) <= mo <= pd.Timestamp(s1)]
        ic_by_era[nm] = round(float(np.mean(sub)), 4) if sub else None
    era_vals = [v for v in ic_by_era.values() if v is not None]
    eras_pos = sum(1 for v in era_vals if v > 0)
    era_pass = bool(era_vals and eras_pos == len(era_vals))

    # ---- tilt guard: IC after size-neutralising ----
    score_sn = _size_neutralise(score)
    _, ics_sn, _ = _ic_series_masked(score_sn, fwd, date_mask)
    m_sn, t_sn, n_sn, _ = _fm(ics_sn)
    tilt_pass = bool(m_sn is not None and best_single is not None and m_sn > 0 and
                     (m is None or m_sn > 0.6 * m))   # most of the IC survives size-neutralisation

    # ---- luck bar (block bootstrap) ----
    boot = _block_bootstrap_t(ics)
    luck_pass = bool(boot and boot["frac_pos"] >= 0.975 and (t is not None and t > 1.96))

    out = {
        "era": era_label, "params": params,
        "ic_6m": round(m, 4) if n else None, "t_6m": round(t, 2) if np.isfinite(t) else None,
        "n_months": n, "obj": round(obj, 6) if n else None,
        "arm_same_rows": arm_same, "best_single": best_single, "single": single,
        "beats_single": beats_single,
        "ic_by_era": ic_by_era, "eras_pos": f"{eras_pos}/{len(era_vals)}", "era_pass": era_pass,
        "ic_size_neutral": round(m_sn, 4) if n_sn else None, "tilt_pass": tilt_pass,
        "boot": boot, "luck_pass": luck_pass,
    }
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    holdout = False
    if args and args[0] == "holdout":
        holdout = True; args = args[1:]
    params = json.loads(args[0]) if args else {}
    t0 = time.time()
    res = run(params, holdout=holdout)
    res["secs"] = round(time.time() - t0, 1)
    # one-line gate string
    def g(b): return "PASS" if b else "FAIL"
    gate = (f"single:{g(res['beats_single'])} era:{g(res['era_pass'])} "
            f"luck:{g(res['luck_pass'])} tilt:{g(res['tilt_pass'])}")
    print(f"OBJ={res['obj']} IC6m={res['ic_6m']} t={res['t_6m']} n={res['n_months']} "
          f"armSame={res['arm_same_rows']} bestSingle={res['best_single']} | {gate} | {res['secs']}s")
    print("JSON " + json.dumps(res, default=str))
