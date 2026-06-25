"""
mesh_backtest.py — does the CONVICTION_ADD cross-force signal actually predict forward
returns on the NSE universe? (MESH design §2 S1 validation gate)

This VALIDATES signal S1 (`CONVICTION_ADD`) on KV's own data BEFORE any UI is built on it
("no score for error"). It mirrors the rigor and conventions of `vistas/arm_backtest.py`
(monthly cross-sectional Spearman IC + decile spread + calendar-era stability) and adds the
two gates the MESH design demands for a flow-driven cross-force signal:

  GATE A (does it BEAT the single-force baseline?) — the composite MUST out-IC and out-spread
          a plain high-ARM-only signal AND a plain delta-ARM-only signal, else it is just ARM
          repackaged and earns no place in the UI.
  GATE B (does flow LEAD return, or merely co-move?) — Granger-lite: compare the IC of flow
          vs the FORWARD return against the IC of flow vs the CONTEMPORANEOUS return, and
          regress forward return on flow controlling for contemporaneous return. If flow only
          co-moves with the same-month return (trend-chasing mirage), S1 fails.

THE SIGNAL S1 CONVICTION_ADD (per stock, per month)
---------------------------------------------------
"Analysts + breadth-of-fund-owners + fund money-flow all pointing UP together."
Construction (a cross-sectional z-composite, computed fresh each month over that month's
universe — so it is universe-relative, not absolute-threshold-fragile):

    CONVICTION_ADD = z(flow_3m_intensity) + z(dbreadth) + z(dARM_3m)

where, per stock per month-end t:
  * flow_3m_intensity = (trailing-3M cumulative net active flow, Rs cr) / (end fund market value, Rs cr)
        — a SIZE-NEUTRAL flow read. Net active flow = funds_flows.stock_flows(ym).net_flow_cr,
          the corp-action- and price-drift-immune metric `end - start*(1+TR)`. We sum the latest
          3 monthly net flows and divide by the latest end market value so a small stock with a big
          relative inflow is not buried by a mega-cap's larger rupee number.
  * dbreadth = funds_flows.stock_flows(ym).dbreadth = (# funds holding now) - (# funds holding last
          month) = buyers - sellers of positions (the Chen-Hong-Stein breadth signal).
  * dARM_3m = ARM_100_REG(t) - ARM_100_REG(t-3M), from arm_backtest._arm_symbol_series() ffilled
          to month-ends (the analyst-revision TREND, the validated direction-is-the-edge mechanic).

  z(x) = (x - cross-sectional mean of x this month) / (cross-sectional std of x this month).
  The composite sums the three z-scores; a stock missing any one component is dropped from
  that month's cross-section (so every scored name has all three forces).

BASELINES it must beat:
  * ARM_LEVEL  = ARM_100_REG(t)            (the plain high-ARM-only signal)
  * dARM_3m    = ARM_100_REG(t)-ARM(t-3M)  (the plain delta-ARM-only signal — the trend component alone)

METHOD (reproducible, mirrors arm_backtest.run)
-----------------------------------------------
- Universe/window: the overlap of (a) the monthly holdings panel (2013-04 -> 2026-05) and (b) the
  per-stock TR panel (stocks.latest_csv(), Date x SYMBOL, month-end resampled). ~2013 -> 2026.
- DEAD names RETAINED: each month we use whatever symbols are present in the panels AT THAT DATE
  (no filter to today's survivors) — else flow/breadth signals look better than they are.
- Each month-end t: forward h-month return = px[t+h]/px[t]-1 (needs both present); h in {1,3,6,12}.
- IC = Spearman rank corr ACROSS stocks that month between the score and forward return. One IC per
  month. Report mean IC, t-stat (mean/std*sqrt(n)), %months IC>0, by calendar era.
- Decile spread = each month sort into 10 score deciles, mean(D10)-mean(D1) forward return, averaged
  across months, annualised *(12/h).
- MIN_XS = 30 stocks per cross-section.

Diagnostics only; never trades a NAV. Set PYTHONIOENCODING=utf-8 (ASCII prints, Windows cp1252).
"""
from __future__ import annotations

import os
import json
import numpy as np
import pandas as pd

from . import arm_backtest, stocks, funds_flows

HORIZONS = [1, 3, 6, 12]
MIN_XS = 30                       # min stocks in a monthly cross-section to count it
FLOW_WINDOW = 3                   # months in the trailing flow accumulation


# ------------------------------------------------------------------ data assembly
def _month_end(ym: str) -> pd.Timestamp:
    """'2020-06' -> the month-END Timestamp 2020-06-30 (matches a pandas 'ME' resample index)."""
    return (pd.Timestamp(ym + "-01") + pd.offsets.MonthEnd(0))


def _build_flow_breadth_panel(log=print):
    """Per-stock, per-month flow + breadth, from funds_flows.stock_flows over every store month.

    Returns three wide DataFrames indexed by month-END Timestamp x SYMBOL:
      net_flow  (Rs cr net active flow that month),
      mv_end    (Rs cr fund market value at month end — the size denominator),
      dbreadth  (buyers - sellers of positions that month).
    Built ONCE (stock_flows is the expensive call); symbol is the join key downstream.
    Dead names are naturally retained — we take whatever symbols stock_flows returns each month.
    """
    h, ret = funds_flows._load()
    ca = funds_flows._load_ca_events(h)
    months = sorted(h["ym"].unique())
    nf, mv, db = {}, {}, {}
    n_ok = 0
    for ym in months:
        try:
            g = funds_flows.stock_flows(ym, h=h, ret=ret, ca=ca)
        except ValueError:
            continue
        # drop corp-action-quarantined names (their flow is not a clean discretionary signal)
        g = funds_flows.clean_flows(g)
        g = g[g["sym"].notna()]
        if g.empty:
            continue
        t = _month_end(ym)
        # collapse any rare duplicate symbol to its summed flow / max breadth (keep it honest)
        gg = g.reset_index()
        nf[t] = gg.groupby("sym")["net_flow_cr"].sum()
        mv[t] = gg.groupby("sym")["mv_end_cr"].sum()
        db[t] = gg.groupby("sym")["dbreadth"].sum()
        n_ok += 1
    net_flow = pd.DataFrame(nf).T.sort_index()
    mv_end = pd.DataFrame(mv).T.sort_index()
    dbreadth = pd.DataFrame(db).T.sort_index()
    log(f"[mesh_backtest] flow/breadth panel: {n_ok} months "
        f"{net_flow.index.min().date()}..{net_flow.index.max().date()}, "
        f"{net_flow.shape[1]} symbols")
    return net_flow, mv_end, dbreadth


def _arm_month_end(me_index, syms, log=print) -> pd.DataFrame:
    """ARM_100_REG aligned to month-ends (score in force at each month-end, ffilled), symbols x time.
    REUSES arm_backtest._arm_symbol_series() — the same step-series the ARM backtest uses."""
    arm_ser = arm_backtest._arm_symbol_series(log)
    cols = {s: arm_ser[s].reindex(me_index, method="ffill") for s in syms if s in arm_ser}
    arm_me = pd.concat(cols, axis=1) if cols else pd.DataFrame(index=me_index)
    log(f"[mesh_backtest] ARM aligned: {len(cols)}/{len(syms)} symbols have an ARM series")
    return arm_me


# ------------------------------------------------------------------ stats helpers
def _zscore_xs(s: pd.Series) -> pd.Series:
    """Cross-sectional z-score over the non-NaN values of s (mean 0, std 1). NaN if std==0/empty."""
    v = s.dropna()
    if len(v) < 2:
        return pd.Series(np.nan, index=s.index)
    mu, sd = v.mean(), v.std(ddof=0)
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(np.nan, index=s.index)
    return (s - mu) / sd


def _ic_series(score_me: pd.DataFrame, fwd: pd.DataFrame, eras):
    """For each month, Spearman IC across stocks of score(t) vs forward return(t->t+h).
    Returns (ics list, xs_counts list, ic_by_era dict, spreads list)."""
    ics, xs_counts, spreads = [], [], []
    ic_by_era = {e[0]: [] for e in eras}
    for t in score_me.index:
        if t not in fwd.index:
            continue
        a = score_me.loc[t]
        r = fwd.loc[t]
        common = a.index.intersection(r.index)
        a = a[common]
        r = r[common]
        ok = a.notna() & r.notna()
        if ok.sum() < MIN_XS:
            continue
        a2, r2 = a[ok], r[ok]
        ic = a2.corr(r2, method="spearman")
        if pd.isna(ic):
            continue
        ics.append(ic)
        xs_counts.append(int(ok.sum()))
        for nm, s0, s1 in eras:
            if pd.Timestamp(s0) <= t <= pd.Timestamp(s1):
                ic_by_era[nm].append(ic)
        try:
            d = pd.qcut(a2.rank(method="first"), 10, labels=False)
            grp = r2.groupby(d).mean()
            if 9 in grp.index and 0 in grp.index:
                spreads.append(grp.loc[9] - grp.loc[0])
        except Exception:
            pass
    return ics, xs_counts, ic_by_era, spreads


def _summ(ics, xs_counts, ic_by_era, spreads, h):
    ics = np.array(ics, float)
    spreads = np.array(spreads, float)
    n = len(ics)
    ic_mean = float(np.mean(ics)) if n else float("nan")
    ic_t = (float(ic_mean / (np.std(ics, ddof=1) / np.sqrt(n)))
            if n > 2 and np.std(ics, ddof=1) > 0 else float("nan"))
    spread_ann = float(np.mean(spreads) * (12.0 / h)) if len(spreads) else float("nan")
    return {
        "n_months": n,
        "ic_mean": round(ic_mean, 4),
        "ic_t_stat": round(ic_t, 2) if np.isfinite(ic_t) else None,
        "pct_months_ic_pos": round(float(np.mean(ics > 0)), 3) if n else None,
        "decile_spread_ann_pct": round(spread_ann * 100, 2) if np.isfinite(spread_ann) else None,
        "avg_stocks_per_xs": int(np.mean(xs_counts)) if xs_counts else 0,
        "ic_by_era": {nm: (round(float(np.mean(v)), 4) if v else None) for nm, v in ic_by_era.items()},
    }


def _signal_block(score_me, px, eras, label, log=print):
    """Run the IC + spread backtest for ONE score across all horizons. score_me = symbols-x-time
    DataFrame of the (already cross-sectionally computed) signal; px = month-end TR levels."""
    block = {}
    for h in HORIZONS:
        fwd = px.shift(-h) / px - 1.0
        ics, xs_counts, ic_by_era, spreads = _ic_series(score_me, fwd, eras)
        block[f"{h}m"] = _summ(ics, xs_counts, ic_by_era, spreads, h)
        s = block[f"{h}m"]
        log(f"[mesh_backtest] {label:14s} {h:>2d}m: IC={s['ic_mean']:+.4f} "
            f"t={s['ic_t_stat']} spread={s['decile_spread_ann_pct']}%/yr "
            f"pos={s['pct_months_ic_pos']} n={s['n_months']}")
    return block


# ------------------------------------------------------------------ the build
def run(start="2013-01-01", log=print) -> dict:
    # ---- 1. price panel, month-end ----
    panel = pd.read_csv(stocks.latest_csv(), index_col=0)
    panel.index = pd.to_datetime(panel.index, errors="coerce")
    panel = panel[~panel.index.isna()].sort_index()
    panel = panel[panel.index >= pd.Timestamp(start)]
    me = panel.resample("ME").last()
    log(f"[mesh_backtest] price panel {panel.shape[1]} stocks, {me.shape[0]} month-ends "
        f"{me.index.min().date()}..{me.index.max().date()}")

    # ---- 2. flow / breadth panel (month-end x symbol) ----
    net_flow, mv_end, dbreadth = _build_flow_breadth_panel(log)

    # restrict everything to the COMMON month-end axis & symbols (the overlap window)
    me_axis = me.index.intersection(net_flow.index)
    me = me.reindex(me_axis)
    net_flow = net_flow.reindex(me_axis)
    mv_end = mv_end.reindex(me_axis)
    dbreadth = dbreadth.reindex(me_axis)
    log(f"[mesh_backtest] overlap window {me_axis.min().date()}..{me_axis.max().date()} "
        f"({len(me_axis)} months)")

    # symbols that exist somewhere in BOTH the price panel and the flow panel
    flow_syms = set(net_flow.columns)
    syms = [s for s in me.columns if s in flow_syms]
    log(f"[mesh_backtest] symbols in BOTH price and flow panels: {len(syms)}")
    me = me[syms]
    net_flow = net_flow.reindex(columns=syms)
    mv_end = mv_end.reindex(columns=syms)
    dbreadth = dbreadth.reindex(columns=syms)

    # ---- 3. ARM aligned to the same month-end axis ----
    arm_me = _arm_month_end(me_axis, syms, log).reindex(columns=syms)

    # ---- 4. raw force fields ----
    # trailing 3M cumulative net active flow (sum of last FLOW_WINDOW monthly flows)
    flow_3m = net_flow.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum()
    # size-neutral flow intensity = 3M cumulative flow / end fund market value
    flow_3m_intensity = flow_3m / mv_end.replace(0.0, np.nan)
    # analyst revision trend over trailing 3 month-ends
    dARM_3m = arm_me - arm_me.shift(3)
    # ARM level (the plain baseline)
    arm_level = arm_me

    # ---- 5. cross-sectional z-scores, month by month ----
    def _xs_z(df):
        return df.apply(lambda row: _zscore_xs(row), axis=1)

    z_flow = _xs_z(flow_3m_intensity)
    z_db = _xs_z(dbreadth)
    z_darm = _xs_z(dARM_3m)

    # the composite: only where ALL THREE forces are present (so every scored name is a true confluence)
    have_all = z_flow.notna() & z_db.notna() & z_darm.notna()
    conviction = (z_flow + z_db + z_darm).where(have_all)

    avg_stocks = int(have_all.sum(axis=1)[have_all.sum(axis=1) >= MIN_XS].mean()) \
        if (have_all.sum(axis=1) >= MIN_XS).any() else 0
    log(f"[mesh_backtest] CONVICTION_ADD scored names/month (all 3 forces present): "
        f"avg ~{avg_stocks}")

    eras = [("2013-2016", "2013-01-01", "2016-12-31"),
            ("2017-2020", "2017-01-01", "2020-12-31"),
            ("2021-2023", "2021-01-01", "2023-12-31"),
            ("2024-2026", "2024-01-01", "2026-12-31")]

    # ---- 6. backtest the composite + the two baselines ----
    log("\n[mesh_backtest] === CONVICTION_ADD (composite) ===")
    comp = _signal_block(conviction, me, eras, "CONVICTION_ADD", log)
    # FAIR head-to-head: every baseline is scored on the SAME universe as the composite (have_all),
    # else ARM_LEVEL's bigger universe (~960 vs ~680) confounds the gate (KV/own-review catch 2026-06-25).
    log("\n[mesh_backtest] === ARM_LEVEL baseline (same universe as composite) ===")
    base_arm = _signal_block(arm_level.where(have_all), me, eras, "ARM_LEVEL", log)
    log("\n[mesh_backtest] === dARM_3m baseline (same universe) ===")
    base_darm = _signal_block(dARM_3m.where(have_all), me, eras, "dARM_3m", log)
    # single forces on the same universe, for the component picture
    log("\n[mesh_backtest] === flow_3m_intensity (single force, same universe) ===")
    base_flow = _signal_block(flow_3m_intensity.where(have_all), me, eras, "FLOW_3M", log)
    log("\n[mesh_backtest] === dbreadth (single force, same universe) ===")
    base_breadth = _signal_block(dbreadth.where(have_all), me, eras, "DBREADTH", log)
    # ARM on its FULL universe — context only, NOT used to judge the gate (the broader set ARM also covers)
    log("\n[mesh_backtest] === ARM_LEVEL (full ARM universe, context) ===")
    base_arm_full = _signal_block(arm_level, me, eras, "ARM_LEVEL_full", log)

    # ---- 7. GATE A: does the composite beat the ARM baselines? ----
    def _beats(a, b, h="6m"):
        """Composite IC/spread minus baseline at horizon h (positive = composite wins)."""
        ic_d = (a[h]["ic_mean"] or 0) - (b[h]["ic_mean"] or 0)
        sp_a = a[h]["decile_spread_ann_pct"]
        sp_b = b[h]["decile_spread_ann_pct"]
        sp_d = (sp_a - sp_b) if (sp_a is not None and sp_b is not None) else None
        return round(ic_d, 4), (round(sp_d, 2) if sp_d is not None else None)

    gate_a = {}
    for h in ["3m", "6m", "12m"]:
        ic_vs_lvl, sp_vs_lvl = _beats(comp, base_arm, h)
        ic_vs_darm, sp_vs_darm = _beats(comp, base_darm, h)
        gate_a[h] = {
            "ic_uplift_vs_arm_level": ic_vs_lvl,
            "spread_uplift_vs_arm_level_pct": sp_vs_lvl,
            "ic_uplift_vs_dARM_3m": ic_vs_darm,
            "spread_uplift_vs_dARM_3m_pct": sp_vs_darm,
        }
    # judge at the 6M horizon (ARM's natural peak per arm_backtest): must out-IC BOTH baselines
    h6 = gate_a["6m"]
    beats_arm_baseline = bool(h6["ic_uplift_vs_arm_level"] > 0 and h6["ic_uplift_vs_dARM_3m"] > 0)

    # ---- 8. GATE B: lead-lag / Granger-lite — does flow LEAD return or co-move? ----
    # (i) IC of flow vs FORWARD return vs IC of flow vs CONTEMPORANEOUS return.
    # Contemporaneous return = the SAME month's stock TR return (level[t]/level[t-1]-1).
    contemp = me / me.shift(1) - 1.0
    z_flow_clean = z_flow  # the flow-intensity z (cross-sectional)

    def _ic_one(score_me, target_me):
        out = []
        for t in score_me.index:
            if t not in target_me.index:
                continue
            a = score_me.loc[t]
            r = target_me.loc[t]
            common = a.index.intersection(r.index)
            a, r = a[common], r[common]
            ok = a.notna() & r.notna()
            if ok.sum() < MIN_XS:
                continue
            ic = a[ok].corr(r[ok], method="spearman")
            if pd.notna(ic):
                out.append(ic)
        arr = np.array(out, float)
        n = len(arr)
        m = float(np.mean(arr)) if n else float("nan")
        t = float(m / (np.std(arr, ddof=1) / np.sqrt(n))) if n > 2 and np.std(arr, ddof=1) > 0 else float("nan")
        return round(m, 4), (round(t, 2) if np.isfinite(t) else None), n

    fwd6 = me.shift(-6) / me - 1.0
    ic_flow_fwd6, t_flow_fwd6, _ = _ic_one(z_flow_clean, fwd6)
    ic_flow_contemp, t_flow_contemp, _ = _ic_one(z_flow_clean, contemp)

    # (ii) panel regression: forward 6M return ~ flow(t) + contemporaneous return(t).
    # If flow's coefficient stays positive & significant AFTER controlling for the same-month return,
    # flow carries forward information beyond co-movement (it LEADS). Pooled OLS with month effects
    # absorbed by cross-sectional de-meaning each month (so it is a within-month effect).
    rows = []
    for t in z_flow_clean.index:
        if t not in fwd6.index or t not in contemp.index:
            continue
        f = z_flow_clean.loc[t]
        cr = contemp.loc[t]
        fr = fwd6.loc[t]
        common = f.index.intersection(cr.index).intersection(fr.index)
        f, cr, fr = f[common], cr[common], fr[common]
        ok = f.notna() & cr.notna() & fr.notna()
        if ok.sum() < MIN_XS:
            continue
        # de-mean within the month (remove the month fixed effect)
        sub = pd.DataFrame({"f": f[ok], "cr": cr[ok], "fr": fr[ok]})
        sub = sub - sub.mean()
        rows.append(sub)
    reg = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["f", "cr", "fr"])
    flow_beta_controlling_contemp = None
    flow_beta_t = None
    if len(reg) > 50:
        X = np.column_stack([np.ones(len(reg)), reg["f"].values, reg["cr"].values])
        y = reg["fr"].values
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        dof = len(reg) - X.shape[1]
        sigma2 = (resid @ resid) / dof
        try:
            cov = sigma2 * np.linalg.inv(X.T @ X)
            se = np.sqrt(np.diag(cov))
            flow_beta_controlling_contemp = round(float(beta[1]), 5)
            flow_beta_t = round(float(beta[1] / se[1]), 2) if se[1] > 0 else None
        except Exception:
            pass

    # flow LEADS if: forward IC is materially positive AND survives the contemporaneous control.
    flow_leads_return = bool(
        (ic_flow_fwd6 is not None and ic_flow_fwd6 > 0.005)
        and (flow_beta_controlling_contemp is not None and flow_beta_controlling_contemp > 0
             and (flow_beta_t is not None and flow_beta_t > 1.96))
    )

    # ---- 9. era stability read ----
    def _era_stable(block, h="6m"):
        vals = [v for v in block[h]["ic_by_era"].values() if v is not None]
        if not vals:
            return None
        pos = sum(1 for v in vals if v > 0)
        return {"eras_positive": f"{pos}/{len(vals)}", "by_era": block[h]["ic_by_era"]}

    # ---- 10. assemble verdict ----
    def _verdict_for(block, name):
        return {
            "ic_by_horizon": {h: block[h]["ic_mean"] for h in ["1m", "3m", "6m", "12m"]},
            "t_stat_by_horizon": {h: block[h]["ic_t_stat"] for h in ["1m", "3m", "6m", "12m"]},
            "decile_spread_ann_pct_by_horizon": {h: block[h]["decile_spread_ann_pct"]
                                                 for h in ["1m", "3m", "6m", "12m"]},
            "pct_months_ic_pos": {h: block[h]["pct_months_ic_pos"] for h in ["1m", "3m", "6m", "12m"]},
            "era_stability_6m": _era_stable(block, "6m"),
            "n_months_6m": block["6m"]["n_months"],
            "avg_stocks_per_xs_6m": block["6m"]["avg_stocks_per_xs"],
        }

    result = {
        "signal": "S1 CONVICTION_ADD = z(flow_3m_intensity) + z(dbreadth) + z(dARM_3m)",
        "method": ("monthly cross-sectional Spearman IC + decile spread vs forward TR; "
                   "dead names retained; corp-action-quarantined flow excluded; MIN_XS=30; "
                   "z-scored cross-sectionally each month; composite requires all 3 forces present."),
        "window": f"{me_axis.min().date()}..{me_axis.max().date()}",
        "n_symbols_universe": len(syms),
        "avg_stocks_per_month_composite": avg_stocks,

        "CONVICTION_ADD": _verdict_for(comp, "CONVICTION_ADD"),
        "baseline_ARM_LEVEL": _verdict_for(base_arm, "ARM_LEVEL"),
        "baseline_ARM_LEVEL_full_universe": _verdict_for(base_arm_full, "ARM_LEVEL_full"),
        "baseline_dARM_3m": _verdict_for(base_darm, "dARM_3m"),
        "single_force_FLOW_3M": _verdict_for(base_flow, "FLOW_3M"),
        "single_force_DBREADTH": _verdict_for(base_breadth, "DBREADTH"),

        "GATE_A_beats_arm_baseline": {
            "verdict": beats_arm_baseline,
            "judged_at": "6m",
            "detail_by_horizon": gate_a,
            "plain": ("composite out-ICs BOTH plain ARM-level and plain dARM_3m at 6M"
                      if beats_arm_baseline else
                      "composite does NOT out-IC both ARM baselines at 6M -> likely ARM repackaged"),
        },

        "GATE_B_flow_leads_return": {
            "verdict": flow_leads_return,
            "ic_flow_vs_forward_6m": ic_flow_fwd6,
            "ic_flow_vs_forward_6m_t": t_flow_fwd6,
            "ic_flow_vs_contemporaneous_ret": ic_flow_contemp,
            "ic_flow_vs_contemporaneous_ret_t": t_flow_contemp,
            "flow_beta_on_forward6m_controlling_contemp": flow_beta_controlling_contemp,
            "flow_beta_t": flow_beta_t,
            "n_obs_regression": int(len(reg)),
            "plain": ("flow PRECEDES forward return (positive forward IC AND a positive, "
                      "significant flow coefficient after controlling for the same-month return)"
                      if flow_leads_return else
                      "flow does NOT robustly lead forward return after controlling for "
                      "contemporaneous co-movement -> trend-chasing risk"),
        },
    }
    return result


if __name__ == "__main__":   # python -m vistas.mesh_backtest
    res = run()
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "mesh_backtest_result.json")
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    except Exception:
        pass
    # ASCII-only verdict print (Windows cp1252 console)
    print("\n" + "=" * 72)
    print("S1 CONVICTION_ADD -- VALIDATION VERDICT")
    print("=" * 72)
    c = res["CONVICTION_ADD"]
    print(f"window {res['window']}  universe {res['n_symbols_universe']} syms  "
          f"avg ~{res['avg_stocks_per_month_composite']} scored/month")
    print(f"IC by horizon  : {c['ic_by_horizon']}")
    print(f"t-stat         : {c['t_stat_by_horizon']}")
    print(f"decile spread  : {c['decile_spread_ann_pct_by_horizon']} (%/yr)")
    print(f"%months IC>0   : {c['pct_months_ic_pos']}")
    print(f"era stability  : {c['era_stability_6m']}")
    ga = res["GATE_A_beats_arm_baseline"]
    gb = res["GATE_B_flow_leads_return"]
    print(f"\nGATE A beats ARM baseline : {ga['verdict']}  -> {ga['plain']}")
    print(f"   detail 6m: {ga['detail_by_horizon']['6m']}")
    print(f"GATE B flow leads return  : {gb['verdict']}  -> {gb['plain']}")
    print(f"   flow IC fwd6={gb['ic_flow_vs_forward_6m']} (t={gb['ic_flow_vs_forward_6m_t']}) "
          f"vs contemp={gb['ic_flow_vs_contemporaneous_ret']} (t={gb['ic_flow_vs_contemporaneous_ret_t']})")
    print(f"   flow beta | contemp = {gb['flow_beta_on_forward6m_controlling_contemp']} "
          f"(t={gb['flow_beta_t']}, n={gb['n_obs_regression']})")
    print("\nsaved -> mesh_backtest_result.json")
