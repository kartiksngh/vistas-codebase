"""vistas/amc_daily_mark.py — the NO-LLM daily MARK for the live-forward digital-AMC books.

Between the monthly LLM rebalance rounds (`amc_live.apply_round`), a paper book DOESN'T change its
holdings — but it must be MARKED TO MARKET on every trading day so the paper NAV is a true daily
total-return track and each day has its CITI daily fact sheet. This module is that heartbeat: a pure,
deterministic, PAPER-ONLY re-pricing, run over EVERY book on disk (the full digital-ABSL firm + any
future AMC, discovered from amc_book/ — not a hard-coded pilot list).

  • NO trades, NO LLM, NO look-ahead (prices ≤ the mark date only).
  • Idempotent + gap-filling: it re-marks the WHOLE window [book inception → latest price date] every
    run, so a missed/late cron day self-heals and re-running the same day changes nothing.
  • book.json is NEVER modified here (marking ≠ trading) — the post-round book stays the canonical,
    git-stable audit state between rounds. Only the daily fact sheets + the forward NAV series are written.

Per book, for each trading day d in (inception … latest):
  fact_sheet(book, d, prev_trading_day) → save_daily  (the audit fact sheet, CITI schema)
and we maintain a forward NAV series   live/nav/<slug>.csv   (base 100 at the live-track start, so the
track is chartable / scoreable later) plus a single  live/daily_mark_status.json  health summary.

CONVENTIONS (reproducible — KV reporting rule):
  price      = the terminal's clean adjusted total-return close (amc_firm.price_asof) ≤ the mark date.
  book value = Σ qty·price/1e7 + cash_cr  (₹cr; the fact_sheet footer total_cr).
  NAV(d)     = 100 · book_value(d) / book_value(inception)   — a base-100 total-return index of the
               live book from its inception (round) date onward. NAV(inception)=100 by construction.
  day return = footer.day_return_pct from fact_sheet (total/prev_total − 1), prev = prior trading day.

Run nightly AFTER the data refresh (so the latest close is in the panel). One-click: Run AMC Daily Mark.bat.
"""
import os
import csv
import json

import pandas as pd

from . import amc_firm as af
from . import amc_live as al

LIVE_DIR = al.LIVE_DIR
NAV_DIR = os.path.join(LIVE_DIR, "nav")
STATUS_PATH = os.path.join(LIVE_DIR, "daily_mark_status.json")


def _trading_index():
    """The price panel's sorted trading-day DatetimeIndex (the calendar we mark on)."""
    df = af._prices()
    if df is None or not len(df.index):
        raise SystemExit("amc_daily_mark: empty price panel — cannot mark")
    return df.index


def _window(idx, inception_str):
    """Trading days in [inception, latest] as a list of Timestamps, with a {day: prev_day} map
    (prev = the immediately-preceding trading day; None for the first day in the panel)."""
    inc = pd.Timestamp(str(inception_str)[:10])
    days = [d for d in idx if d >= inc]
    prev = {}
    pos = {d: i for i, d in enumerate(idx)}
    for d in days:
        i = pos[d]
        prev[d] = idx[i - 1] if i > 0 else None
    return days, prev


def _all_book_reg_entries():
    """Discover EVERY paper book on disk (data-driven) → a minimal reg-entry {amc, scheme} per book,
    by walking amc_book/<amc>/<scheme>/book.json. This is what makes the daily mark ADAPTABLE rather
    than fragile: any new AMC or fund (e.g. the full digital-ABSL roster, not just the 4 cross-AMC
    pilots) is marked automatically — there is no pilot list to keep in sync. `mark_book` needs only
    the amc+scheme strings (it loads book.json + the price panel); the mandate/benchmark/AUM aren't
    used to MARK, so a minimal entry is sufficient and exactly mirrors how the book was written."""
    out, seen = [], set()
    root = af.BOOK_DIR
    if not os.path.isdir(root):
        return out
    for amc_name in sorted(os.listdir(root)):
        amc_dir = os.path.join(root, amc_name)
        if not os.path.isdir(amc_dir):
            continue
        for sch_name in sorted(os.listdir(amc_dir)):
            bj = os.path.join(amc_dir, sch_name, "book.json")
            if not os.path.isfile(bj):
                continue
            try:
                bk = af._read_json(bj) or {}
            except Exception:
                continue
            amc = bk.get("amc") or amc_name
            scheme = bk.get("scheme") or sch_name
            key = (amc, scheme)
            if key in seen:
                continue
            seen.add(key)
            out.append({"amc": amc, "scheme": scheme})
    return out


def mark_book(reg_entry, idx, log=print):
    """Mark one pilot book to market over its whole live window and (re)write its forward NAV series +
    daily fact sheets. Returns a status dict (or None if the book has no positions / no inception).
    Pure: reads the price panel + book.json, writes only daily fact sheets + the NAV csv (NOT book.json)."""
    book = al.load_book(reg_entry)
    if not book.get("positions"):
        log(f"[mark] {reg_entry['scheme']}: no positions — skipped (book not yet built/rebalanced)")
        return None
    inception = book.get("inception") or book.get("asof")
    if not inception:
        log(f"[mark] {reg_entry['scheme']}: no inception/asof on book — skipped")
        return None

    days, prev = _window(idx, inception)
    if not days:
        log(f"[mark] {reg_entry['scheme']}: inception {inception} is after the latest price date — skipped")
        return None

    base_total = None
    rows = []          # (date_str, total_cr, nav, day_return_pct)
    last_sheet = None
    for d in days:
        d_str = str(d.date())
        # prev day for the day-return: the prior trading day, but only once we're past inception
        p = prev.get(d)
        prev_str = (str(p.date()) if (p is not None and d > pd.Timestamp(str(inception)[:10])) else None)
        sheet = af.fact_sheet(book, d_str, prev_str)
        af.save_daily(reg_entry["amc"], reg_entry["scheme"], sheet)
        total = af._f(sheet["footer"]["total_cr"])
        if base_total is None:
            base_total = total or 1.0
        nav = round(100.0 * total / base_total, 4) if base_total else None
        rows.append((d_str, round(total, 4), nav, sheet["footer"].get("day_return_pct")))
        last_sheet = sheet

    # forward NAV series (base 100 at the live-track inception)
    os.makedirs(NAV_DIR, exist_ok=True)
    nav_path = os.path.join(NAV_DIR, f"{al.slug(reg_entry['scheme'])}.csv")
    with open(nav_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "total_cr", "nav", "day_return_pct"])
        w.writerows(rows)

    foot = last_sheet["footer"]
    st = {"scheme": reg_entry["scheme"], "amc": reg_entry["amc"], "slug": al.slug(reg_entry["scheme"]),
          "inception": str(inception)[:10], "last_marked": rows[-1][0], "n_days": len(rows),
          "nav": rows[-1][2], "total_cr": rows[-1][1], "day_return_pct": rows[-1][3],
          "n_holdings": foot.get("n_holdings"), "equity_cr": foot.get("equity_cr"),
          "cash_cr": foot.get("cash_cr"), "cash_pct": foot.get("cash_pct")}
    n_fwd = len(rows) - 1
    log(f"[mark] {reg_entry['scheme']}: marked {len(rows)} day(s) ({n_fwd} forward of inception), "
        f"NAV {st['nav']} on {st['last_marked']}, {st['n_holdings']} names, cash {st['cash_pct']}%")
    return st


def run(log=print):
    """Mark EVERY paper book on disk → daily fact sheets + forward NAV series + a health summary.
    Data-driven (no pilot list): the full digital-ABSL roster and any future AMC are covered the moment
    their book.json exists. Returns the status doc. Safe to run repeatedly (idempotent); does NOT rebuild
    or publish the site."""
    idx = _trading_index()
    latest = str(idx[-1].date())
    entries = _all_book_reg_entries()
    log(f"[daily-mark] latest price date = {latest}; marking {len(entries)} book(s) on disk (paper-only, no trades)…")
    schemes = []
    for re_ in entries:
        st = mark_book(re_, idx, log=log)
        if st:
            schemes.append(st)
    doc = {"latest_price_date": latest, "n_schemes": len(schemes), "schemes": schemes,
           "note": "no-LLM daily mark; holdings frozen between monthly LLM rounds; NAV base-100 at each "
                   "book's live-forward inception; paper-only; no look-ahead. Data-driven over every "
                   "amc_book/<amc>/<scheme>/book.json."}
    os.makedirs(LIVE_DIR, exist_ok=True)
    json.dump(doc, open(STATUS_PATH, "w", encoding="utf-8"), indent=1, default=str)
    log(f"[daily-mark] done: {len(schemes)} book(s) marked → {STATUS_PATH}")
    return doc


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # the ₹ glyph in some scheme names
    except Exception:
        pass
    run()
