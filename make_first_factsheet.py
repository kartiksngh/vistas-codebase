#!/usr/bin/env python
"""Produce the FIRST virtual daily equity fact sheet for a pilot scheme.

Builds the registry, seeds a 100%-cash book at the scheme's real AUM, deploys it with the
deterministic rules-FM (ARM-scored, mandate + liquidity capped, play-type tagged), marks it to
market on the latest trading day, and writes the audit trail (book.json + blotter.jsonl +
daily/<YYYY-MM>.json) plus an .xlsx in the ABSL CITI layout.

    python make_first_factsheet.py ["Aditya Birla Sun Life"] ["Flexi Cap Fund"]
"""
import os
import sys
import json

from vistas import amc_firm as af

try:                                  # the ₹ glyph crashes the cp1252 Windows console otherwise
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

AMC = sys.argv[1] if len(sys.argv) > 1 else "Aditya Birla Sun Life"
CATEGORY = sys.argv[2] if len(sys.argv) > 2 else "Flexi Cap Fund"


def main():
    print("-" * 72)
    print(f"FIRST VIRTUAL FACT SHEET — {AMC} / {CATEGORY}")
    print("-" * 72)

    reg = af.registry(amcs=[AMC], min_aum_cr=1.0)
    entry = None
    for schemes in reg.values():
        for s in schemes.values():
            if s["category"] == CATEGORY:
                if entry is None or af._f(s["aum_cr"]) > af._f(entry["aum_cr"]):
                    entry = s
    if not entry:
        print(f"  no {CATEGORY} scheme found for {AMC}. Available categories:")
        cats = sorted({s["category"] for sch in reg.values() for s in sch.values()})
        for c in cats:
            print("   -", c)
        return 1

    print(f"  scheme   : {entry['scheme']}")
    print(f"  AUM      : ₹{af._f(entry['aum_cr']):,.0f} cr   (real, from disclosed holdings {entry['asof']})")
    print(f"  benchmark: {entry['benchmark']}   |   real IR vs bench: {entry['real_ir']}")
    print(f"  mandate  : n {entry['mandate']['n_lo']}-{entry['mandate']['n_hi']}, "
          f"max name {entry['mandate']['max_pos']*100:.0f}%, max sector {entry['mandate']['max_sector']*100:.0f}%, "
          f"equity floor {entry['mandate']['equity_min']*100:.0f}%")

    # latest two trading days from the price panel
    df = af._prices()
    dates = list(df.index.astype(str))
    asof, prev_asof = dates[-1], dates[-2]
    print(f"  as of    : {asof}  (prev {prev_asof})")

    book, trades, diag = af.build_rules_v0(entry, asof)

    # persist the audit trail
    af.save_book(book)
    for t in trades:
        af.append_blotter(book["amc"], book["scheme"], t)
    sheet = af.fact_sheet(book, asof, prev_asof)
    af.save_daily(book["amc"], book["scheme"], sheet)

    d = af.scheme_dir(book["amc"], book["scheme"])
    xlsx = os.path.join(d, f"FACT_SHEET_{asof}.xlsx")
    af.to_xlsx(sheet, xlsx)
    json_path = os.path.join(d, f"FACT_SHEET_{asof}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(sheet, f, indent=1, default=str)

    # console digest
    fo = sheet["footer"]
    print("\n  === fact sheet (mark-to-market, inception day) ===")
    print(f"  holdings {fo['n_holdings']} · equity ₹{fo['equity_cr']:,.0f} cr · "
          f"cash ₹{fo['cash_cr']:,.0f} cr ({fo['cash_pct']}%) · NAV-base ₹{fo['total_cr']:,.0f} cr · "
          f"day return {fo['day_return_pct']}%")
    play = {}
    for r in sheet["rows"]:
        play[r.get("play_type")] = play.get(r.get("play_type"), 0) + (r.get("pct_assets") or 0)
    print("  play-type mix: " + ", ".join(f"{k} {v:.1f}%" for k, v in sorted(play.items(), key=lambda x: -x[1])))
    print(f"  top sectors  : " + ", ".join(f"{s['sector'][:18]} {s['pct_assets']:.1f}%"
                                            for s in sheet["sectors"][:5]))
    print("  top 8 holdings:")
    for r in sorted(sheet["rows"], key=lambda r: -r["mkt_value"])[:8]:
        chg = "n/a" if r["pct_change"] is None else f"{r['pct_change']:+.2f}%"
        print(f"    {r['name'][:34]:<34} {r['sector'][:16]:<16} {r['play_type']:<11} "
              f"{r['pct_assets']:>5.2f}%  day {chg}")

    print(f"\n  written:")
    print(f"    {xlsx}")
    print(f"    {json_path}")
    print(f"    {os.path.join(d, 'book.json')}  ·  blotter.jsonl ({len(trades)} trades)  ·  daily/{asof[:7]}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
