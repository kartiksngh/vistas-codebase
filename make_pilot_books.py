#!/usr/bin/env python
"""Build the 4 PILOT virtual books + their first daily fact sheets in one pass.

For each pilot (AMC, SEBI-category) the flagship scheme (largest AUM in that category for that
AMC) is seeded 100% cash at its real AUM, deployed by the deterministic rules-FM under its own
mandate + liquidity caps, marked to the latest trading day, and written to amc_book/ (book +
blotter + daily + .xlsx/.json). Prints a side-by-side comparison.

    python make_pilot_books.py
"""
import os
import sys
import json

from vistas import amc_firm as af

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# (AMC substring, SEBI category) → the flagship in that bucket is the largest-AUM match.
PILOT = [
    ("ICICI Prudential",       "Large Cap Fund"),
    ("SBI Mutual",             "Aggressive Hybrid Fund"),
    ("Aditya Birla Sun Life",  "Flexi Cap Fund"),
    ("Quant Mutual",           "Small Cap Fund"),
]


def main():
    print("=" * 78)
    print("PILOT VIRTUAL BOOKS — 4 flagship schemes across top AMCs")
    print("=" * 78)

    reg = af.registry(amcs=[a for a, _c in PILOT], min_aum_cr=500)
    df = af._prices()
    dates = list(df.index.astype(str))
    asof, prev_asof = dates[-1], dates[-2]
    print(f"as of {asof} (prev {prev_asof})\n")

    summary = []
    for amc_sub, cat in PILOT:
        # flagship = largest-AUM scheme of this category whose AMC contains amc_sub
        cands = [s for schemes in reg.values() for s in schemes.values()
                 if s["category"] == cat and amc_sub.lower() in (s["amc"] or "").lower()]
        if not cands:
            print(f"  [skip] no {cat} for {amc_sub}")
            continue
        entry = max(cands, key=lambda s: af._f(s["aum_cr"]))

        book, trades, diag = af.build_rules_v0(entry, asof)
        af.save_book(book)
        # fresh blotter each build (idempotent inception); then record the buys
        bdir = af.scheme_dir(book["amc"], book["scheme"])
        open(os.path.join(bdir, "blotter.jsonl"), "w").close()
        for t in trades:
            af.append_blotter(book["amc"], book["scheme"], t)
        sheet = af.fact_sheet(book, asof, prev_asof)
        af.save_daily(book["amc"], book["scheme"], sheet)
        xlsx = os.path.join(bdir, f"FACT_SHEET_{asof}.xlsx")
        af.to_xlsx(sheet, xlsx)
        with open(os.path.join(bdir, f"FACT_SHEET_{asof}.json"), "w", encoding="utf-8") as f:
            json.dump(sheet, f, indent=1, default=str)

        fo = sheet["footer"]
        play = {}
        for r in sheet["rows"]:
            play[r["play_type"]] = play.get(r["play_type"], 0) + (r["pct_assets"] or 0)
        summary.append({
            "amc": book["amc"], "scheme": book["scheme"], "cat": cat,
            "aum": af._f(entry["aum_cr"]), "bench": entry["benchmark"], "real_ir": entry["real_ir"],
            "n": fo["n_holdings"], "deployed": diag["deployed_pct"], "cash_pct": fo["cash_pct"],
            "day_ret": fo["day_return_pct"], "n_arm": diag["n_arm_scored"], "n_priced": diag["n_priced"],
            "top_sectors": ", ".join(f"{s['sector'][:14]} {s['pct_assets']:.0f}%" for s in sheet["sectors"][:3]),
            "play": ", ".join(f"{k} {v:.0f}%" for k, v in sorted(play.items(), key=lambda x: -x[1])),
            "xlsx": xlsx,
        })
        print(f"  built  {book['scheme'][:44]:<44} {fo['n_holdings']:>3} names  "
              f"deployed {diag['deployed_pct']:>5.1f}%  day {fo['day_return_pct']:+.2f}%")

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for s in summary:
        print(f"\n{s['amc']}  —  {s['scheme']}")
        print(f"  {s['cat']} · AUM ₹{s['aum']:,.0f} cr · bench {s['bench']} · real IR {s['real_ir']}")
        print(f"  book: {s['n']} names · deployed {s['deployed']}% · cash {s['cash_pct']}% · "
              f"day return {s['day_ret']:+.2f}% · ARM-scored {s['n_arm']}/{s['n_priced']}")
        print(f"  play-type: {s['play']}")
        print(f"  top sectors: {s['top_sectors']}")
    print("\nfact sheets:")
    for s in summary:
        print("   " + s["xlsx"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
