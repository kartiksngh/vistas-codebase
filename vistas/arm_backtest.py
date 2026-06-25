"""
arm_backtest.py — does ARM actually predict forward returns on the NSE universe? (research, #36)

A cross-sectional signal-backtest of LSEG StarMine ARM (the regional analyst-revision percentile, 0-100)
against our own per-stock total-return panel. This VALIDATES the signal on KV's data before we build
features on it ("no score for error") — and is an independent cross-check of the ISIN->symbol mapping
(if the mapping were wrong, IC would be ~0).

METHOD (stated so it's reproducible)
------------------------------------
- Universe/data: per-stock daily TR levels from data/Stocks Data TR till *.csv (split/bonus-adjusted,
  2000-2026), resampled to MONTH-END. Signal: ARM_100_REG step-series per ISIN (arm_repo compiled cache),
  resolved ISIN->NSE symbol via idmap, reindexed onto month-ends with forward-fill (the score in force).
- Each month-end t and horizon h (months): forward return = level[t+h]/level[t] - 1 (needs both present).
- IC (information coefficient) = Spearman rank correlation, ACROSS stocks in that month, between ARM(t)
  and forward return(t->t+h). One IC per month. We report mean IC, its t-stat (mean/std*sqrt(n_months)),
  and % of months with IC>0.
- Decile spread = each month sort stocks into 10 ARM deciles, mean forward return of D10 (highest ARM)
  minus D1 (lowest); average across months, annualised by *(12/h). This is the long-top/short-bottom edge.
- Stability: the same, split into ~equal calendar eras, to check the edge isn't one regime.
Min 30 stocks per cross-section. Diagnostics only.
"""
from __future__ import annotations

import os
import json
import pandas as pd
import numpy as np

from . import arm, idmap, stocks

HEADLINE = "ARM_100_REG"
HORIZONS = [1, 3, 6, 12]
MIN_XS = 30                      # min stocks in a monthly cross-section to count it


def _arm_symbol_series(log=print) -> dict:
    """{NSE symbol -> pandas Series of ARM_100_REG indexed by date (step change-points)}."""
    raw = arm.load_raw()
    if not raw:
        log("[arm_backtest] no ARM cache — run vistas.arm.compile_india first.")
        return {}
    out, dup = {}, 0
    for isin, rec in raw.items():
        head = (rec.get("mnem") or {}).get(HEADLINE)
        if not head:
            continue
        sym = idmap.resolve(isin)
        if not sym:
            continue
        s = pd.Series({pd.Timestamp(d): v for d, v in head}).sort_index()
        if sym in out:                                  # multiple lineage ISINs -> keep the longer series
            dup += 1
            if len(s) <= len(out[sym]):
                continue
        out[sym] = s
    log(f"[arm_backtest] ARM symbol-series: {len(out)} symbols ({dup} lineage dups collapsed).")
    return out


def run(start="2005-01-01", log=print) -> dict:
    panel = pd.read_csv(stocks.latest_csv(), index_col=0)
    panel.index = pd.to_datetime(panel.index, errors="coerce")
    panel = panel[~panel.index.isna()].sort_index()
    panel = panel[panel.index >= pd.Timestamp(start)]
    me = panel.resample("ME").last()                     # month-end TR level per stock
    log(f"[arm_backtest] panel {panel.shape[1]} stocks, {me.shape[0]} month-ends {me.index.min().date()}..{me.index.max().date()}")

    arm_ser = _arm_symbol_series(log)
    syms = [s for s in me.columns if s in arm_ser]
    log(f"[arm_backtest] stocks with BOTH price and ARM: {len(syms)}")
    if len(syms) < MIN_XS:
        return {"error": "too few overlapping stocks", "n": len(syms)}

    # ARM aligned to month-ends (score in force at each month-end), forward-filled
    arm_me = pd.DataFrame(index=me.index)
    for s in syms:
        arm_me[s] = arm_ser[s].reindex(me.index, method="ffill")
    px = me[syms]

    eras = [("2005-2012", "2005-01-01", "2012-12-31"),
            ("2013-2019", "2013-01-01", "2019-12-31"),
            ("2020-2026", "2020-01-01", "2026-12-31")]
    results = {"method": "Spearman IC + decile spread, monthly cross-sections, ARM_100_REG vs forward TR",
               "n_stocks_overlap": len(syms), "horizons": {}}

    for h in HORIZONS:
        fwd = px.shift(-h) / px - 1.0                    # forward h-month return per stock at each month-end
        ics, spreads, xs_counts = [], [], []
        ic_by_era = {e[0]: [] for e in eras}
        for t in me.index:
            a = arm_me.loc[t]; r = fwd.loc[t]
            ok = a.notna() & r.notna()
            if ok.sum() < MIN_XS:
                continue
            a2, r2 = a[ok], r[ok]
            ic = a2.corr(r2, method="spearman")
            if pd.isna(ic):
                continue
            ics.append(ic); xs_counts.append(int(ok.sum()))
            for nm, s0, s1 in eras:
                if pd.Timestamp(s0) <= t <= pd.Timestamp(s1):
                    ic_by_era[nm].append(ic)
            # decile spread (need >=10 names/decile-ish; qcut into 10)
            try:
                d = pd.qcut(a2.rank(method="first"), 10, labels=False)
                grp = r2.groupby(d).mean()
                if 9 in grp.index and 0 in grp.index:
                    spreads.append(grp.loc[9] - grp.loc[0])
            except Exception:
                pass
        ics = np.array(ics, float); spreads = np.array(spreads, float)
        n = len(ics)
        ic_mean = float(np.mean(ics)) if n else float("nan")
        ic_t = float(ic_mean / (np.std(ics, ddof=1) / np.sqrt(n))) if n > 2 and np.std(ics, ddof=1) > 0 else float("nan")
        spread_ann = float(np.mean(spreads) * (12.0 / h)) if len(spreads) else float("nan")
        results["horizons"][f"{h}m"] = {
            "n_months": n,
            "ic_mean": round(ic_mean, 4),
            "ic_t_stat": round(ic_t, 2),
            "pct_months_ic_pos": round(float(np.mean(ics > 0)), 3) if n else None,
            "decile_spread_ann_pct": round(spread_ann * 100, 2) if not np.isnan(spread_ann) else None,
            "avg_stocks_per_xs": int(np.mean(xs_counts)) if xs_counts else 0,
            "ic_by_era": {nm: (round(float(np.mean(v)), 4) if v else None) for nm, v in ic_by_era.items()},
        }
        log(f"[arm_backtest] {h}m: IC={ic_mean:.4f} t={ic_t:.2f} declSpread={spread_ann*100:.2f}%/yr "
            f"posMonths={np.mean(ics > 0):.0%} n={n}")

    return results


if __name__ == "__main__":   # python -m vistas.arm_backtest
    import sys
    res = run()
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "arm_backtest_result.json")
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    except Exception:
        pass
    print(json.dumps(res, indent=1))
