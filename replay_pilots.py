#!/usr/bin/env python
"""Walk the 4 PILOT virtual books forward through history with the deterministic rules-FM (#68),
then print + save each scorecard — vs the real NSE benchmark TR index AND the real scheme's actual
AMFI NAV — plus the Fundamental-Law decomposition (IR = IC·√BR·TC).

    python replay_pilots.py            # all 4 pilots
    python replay_pilots.py 0          # just pilot index 0 (ICICI Large Cap) — fast smoke test
    REPLAY_START=2014-01-01 python replay_pilots.py
"""
import os
import sys

from vistas import amc_replay as ar, amc_firm as af

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PILOT = [
    ("ICICI Prudential",       "Large Cap Fund"),
    ("SBI Mutual",             "Aggressive Hybrid Fund"),
    ("Aditya Birla Sun Life",  "Flexi Cap Fund"),
    ("Quant Mutual",           "Small Cap Fund"),
]
START = os.environ.get("REPLAY_START", "2015-01-01")


def main():
    sel = PILOT
    if len(sys.argv) > 1:
        sel = [PILOT[int(sys.argv[1])]]
    print("=" * 84)
    print(f"PILOT HISTORICAL REPLAYS — rules-FM, monthly rebalance, daily NAV, from {START}")
    print("=" * 84)
    for amc, cat in sel:
        try:
            reg, nav, monthly, score, diag = ar.run(amc, cat, start=START)
        except SystemExit as e:
            print(f"  [skip] {amc} / {cat}: {e}")
            continue
        except Exception as e:
            print(f"  [ERROR] {amc} / {cat}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            continue
        b = score.get("benchmark", {}) or {}
        rs = score.get("real_scheme", {}) or {}
        fl = score.get("fundamental_law", {}) or {}
        bk = score["book"]
        print(f"\n{reg['amc']} — {reg['scheme']}")
        print(f"  window {score['window']['start']}..{score['window']['end']} ({score['window']['years']}y) · "
              f"{diag['n_rebalances']} rebals · avg {diag['avg_holdings']} names")
        print(f"  universe: avg {diag['avg_universe']} priced+active · ARM coverage {diag['avg_arm_cov_pct']}% · "
              f"delisted-but-included {diag['avg_dead_included']}/reb (survivorship-clean check) · "
              f"turnover {diag['avg_oneway_turnover_pct']}%/reb · cost {diag['cost_bps']}bps/side")
        print(f"  BOOK : CAGR {bk['cagr_pct']}% · vol {bk['vol_pct']}% · Sharpe {bk['sharpe']} · "
              f"maxDD {bk['maxdd_pct']}% · final NAV {bk['final_nav']}")
        if b:
            print(f"  vs {score.get('benchmark_name')}: bench CAGR {b['cagr_pct']}% → "
                  f"excess {b['excess_cagr_pct']}%/yr · IR {b['info_ratio']} · beta {b['beta']} · "
                  f"up/dn capture {b['up_capture']}/{b['down_capture']}")
        if rs.get("matched_name"):
            print(f"  vs REAL «{rs['matched_name']}»: real CAGR {rs['real_cagr_pct']}% · book {rs['book_cagr_pct']}% · "
                  f"book−real {rs['book_minus_real_cagr_pct']}%/yr over {rs['overlap_years']}y")
        else:
            print(f"  vs REAL scheme: (no AMFI NAV name match)")
        print(f"  FUND.LAW: IC {fl.get('ic_mean')} (t={fl.get('ic_tstat')}) · TC {fl.get('transfer_coefficient')} · "
              f"BR≤{fl.get('breadth_per_year_UPPER')}/yr → implied IR≤{fl.get('implied_IR_UPPER')}  "
              f"vs realized IR {fl.get('realized_IR_vs_bench')}")
    print("\n" + "=" * 84)
    print("saved per scheme → amc_book/<AMC>/<SCHEME>/replay/ : nav.csv · scorecard.json · monthly_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
