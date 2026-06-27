#!/usr/bin/env python
"""Build the FULL digital-ABSL firm — every distinct ABSL equity/hybrid scheme as a deterministic
rules-FM paper book, in ONE pass (one process → the big price panel loads once; memory-safe).

This generalises `make_pilot_books.py` (4 cross-AMC flagships) to the per-AMC FIRM the North Star
targets: one DESK PER DISTINCT FUND across all of Aditya Birla Sun Life's equity/hybrid mandates.
Per scheme it writes the SAME two artifact sets the 4 pilots already have, so the firm view is a
real, like-for-like product (not 28 flat NAV=100 stubs):

  (A) the SEAM BOOK  — build_rules_v0 deploys a fresh 100%-cash book as-of the latest data date under
      the scheme's mandate + liquidity caps → book.json + blotter.jsonl + first CITI fact sheet
      (.xlsx/.json + daily/). This is the current book the daily-mark re-prices forward + the LLM
      round will later inherit.
  (B) the HISTORICAL TRACK — replay() walks the same rules-FM forward from 2015 (survivorship-clean,
      look-ahead-free) → replay/{nav.csv, benchmark_nav.csv, monthly_summary.json, scorecard.json}.
      This is what gives each scheme a real NAV path + scorecard (CAGR / excess / IR·IC·TC vs its
      benchmark TR + the real AMFI scheme), exactly like the pilots.

DISCIPLINE (unchanged): deterministic + FREE (no LLM); paper-money only; no look-ahead; and NO raw
per-stock LSEG StarMine ARM is ever persisted — only OUR derived weights / play-types / brain-id and
AGGREGATE coverage stats reach disk (the build_rules_v0 + replay guards already enforce this).

Run (slow — ~1 min/scheme historical replay; kick off in the background):
    python make_absl_firm.py
    python make_absl_firm.py --no-replay     # seam books only (fast; thin firm view until replays run)
    python make_absl_firm.py --amc "SBI Mutual"   # clone the firm build to another AMC later
"""
import os
import sys
import json
import time
import argparse

from vistas import amc_firm as af
from vistas import amc_live as al
from vistas import amc_replay as ar

try:
    sys.stdout.reconfigure(encoding="utf-8")   # ₹ glyph + some scheme names
except Exception:
    pass


def build_seam_book(entry, asof, prev_asof):
    """(A) the current seam book + blotter + first daily fact sheet (mirrors make_pilot_books)."""
    book, trades, diag = af.build_rules_v0(entry, asof, log=lambda *a: None)
    af.save_book(book)
    bdir = af.scheme_dir(book["amc"], book["scheme"])
    open(os.path.join(bdir, "blotter.jsonl"), "w").close()      # fresh blotter at inception (idempotent)
    for t in trades:
        af.append_blotter(book["amc"], book["scheme"], t)
    sheet = af.fact_sheet(book, asof, prev_asof)
    af.save_daily(book["amc"], book["scheme"], sheet)
    af.to_xlsx(sheet, os.path.join(bdir, f"FACT_SHEET_{asof}.xlsx"))
    with open(os.path.join(bdir, f"FACT_SHEET_{asof}.json"), "w", encoding="utf-8") as f:
        json.dump(sheet, f, indent=1, default=str)
    return book, diag, sheet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--amc", default="Aditya Birla Sun Life", help="AMC name substring (the firm to build)")
    ap.add_argument("--no-replay", action="store_true", help="seam books only — skip the ~1min/scheme history")
    ap.add_argument("--min-aum-cr", type=float, default=200.0)
    args = ap.parse_args()

    print("=" * 84)
    print(f"DIGITAL FIRM BUILD — {args.amc}  (deterministic rules-FM; paper-only; no LLM)")
    print("=" * 84)

    entries = al.amc_reg_entries(args.amc, equity_only=True, min_aum_cr=args.min_aum_cr)
    if not entries:
        print(f"  no equity/hybrid schemes for '{args.amc}' (≥₹{args.min_aum_cr}cr) — nothing to build")
        return 1
    df = af._prices()
    dates = list(df.index.astype(str))
    asof, prev_asof = dates[-1], dates[-2]
    firm_aum = sum(af._f(e["aum_cr"]) for e in entries)
    print(f"firm: {len(entries)} distinct equity/hybrid funds · firm AUM ₹{firm_aum:,.0f} cr · "
          f"as of {asof} (prev {prev_asof}) · replay={'OFF' if args.no_replay else 'ON'}\n")

    ok, failed, summary = 0, [], []
    t0 = time.time()
    for i, entry in enumerate(entries, 1):
        scheme = entry["scheme"]
        tag = f"[{i:>2}/{len(entries)}] {scheme[:46]:<46}"
        try:
            book, diag, sheet = build_seam_book(entry, asof, prev_asof)
        except (Exception, SystemExit) as e:
            failed.append((scheme, f"seam-book: {e}"))
            print(f"{tag}  SEAM FAIL — {e}")
            continue

        fo = sheet["footer"]
        row = {"scheme": scheme, "category": entry.get("category"), "aum": af._f(entry["aum_cr"]),
               "bench": entry.get("benchmark"), "n": fo["n_holdings"],
               "deployed": diag["deployed_pct"], "cash_pct": fo["cash_pct"], "brain": diag.get("brain")}

        if not args.no_replay:
            try:
                ts = time.time()
                nav, monthly, score, diag2 = ar.replay(entry, log=lambda *a: None)
                ar.save_replay(entry, nav, monthly, score, diag2)
                sc = (score or {}).get("benchmark", {}) if isinstance(score, dict) else {}
                row["cagr"] = (score or {}).get("book", {}).get("cagr_pct")
                row["excess"] = sc.get("excess_cagr_pct")
                row["ir"] = sc.get("info_ratio")
                row["replay_s"] = round(time.time() - ts, 1)
            except (Exception, SystemExit) as e:
                row["replay_err"] = str(e)[:80]
                failed.append((scheme, f"replay: {e}"))

        ok += 1
        summary.append(row)
        ex = (f"CAGR {row.get('cagr')!s:>6} · excess {row.get('excess')!s:>6} · IR {row.get('ir')!s:>5} "
              f"({row.get('replay_s','-')}s)") if not args.no_replay else ""
        print(f"{tag}  {row['n']:>3} names · deployed {row['deployed']:>5.1f}% · {row['brain']:<10} {ex}")

    dt = time.time() - t0
    print("\n" + "=" * 84)
    print(f"DONE — {ok}/{len(entries)} schemes built in {dt/60:.1f} min · {len(failed)} issue(s)")
    print("=" * 84)
    for scheme, why in failed:
        print(f"  ⚠ {scheme[:46]:<46} {why}")
    # machine-readable firm summary (for a quick sanity read; the site reads amc_book/ directly)
    out = {"amc": args.amc, "asof": asof, "n_schemes": ok, "firm_aum_cr": round(firm_aum),
           "schemes": summary, "failed": [{"scheme": s, "why": w} for s, w in failed]}
    op = os.path.join(af.BOOK_DIR, "_firm_build_last.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    print(f"\nsummary → {op}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
