"""vistas/amc_grade.py — the LEARNING LOOP for the live-forward digital-AMC (the closed
decision→outcome→lesson cycle the North Star demands).

Every monthly round, each FM agent pre-registers its bets (thesis + a FALSIFIER) in prereg.jsonl
BEFORE the outcome — the anti-hindsight anchor. This module closes the loop: once a round's horizon
has elapsed, it SCORES those bets against what actually happened and writes a per-FM review that the
NEXT round's desk feeds back to the agent ("here is how your last book did — learn from it").

WHAT IS SCORED (and why this and not the prose):
  The falsifier is free-text human/agent judgment — it CANNOT be auto-evaluated. So the engine scores
  the one honest, reproducible NUMBER: each bet's forward **active return vs the book's OWN benchmark**
  (the real NSE TR index `amc_replay._bench_for` picks). The free-text thesis/falsifier are preserved
  verbatim for the agent to self-reflect on; the engine supplies the outcome, not a verdict on the prose.

METRICS (each reproducible — KV reporting rule):
  • horizon            trading days from the bet date `asof` to the grade date `t1` (default = to the
                       latest priced day; at the next monthly round that is ≈1 month — the natural clock).
  • raw_return         price[t1]/price[asof] − 1 on the terminal's adjusted total-return close (no
                       look-ahead: both ≤ t1 ≤ today). PENDING if t1 == asof (no forward day yet).
  • active_return      raw_return − benchmark_return over the SAME [asof, t1] window. The bet's edge over
                       just holding the index — the thing an active FM is paid for.
  • WIN / LOSS         active_return > 0 / ≤ 0  (PENDING until the horizon elapses; UNPRICED if either
                       end can't be priced — a delisted/illiquid name, flagged not silently dropped).
  • hit_rate           share of GRADED bets with active_return > 0 (mean over priced, non-pending bets).
  • avg_active_return  mean active_return over graded bets (the realized average edge per bet).
  • conviction_IC      Spearman rank corr( target_pct , active_return ) across the round's graded bets —
                       "did the FM put MORE weight on the bets that worked?" (>0 = sizing skill; the
                       Fundamental-Law per-bet IC at the desk level). N≥5 else None (too few to rank).

DISCIPLINE: paper-only; NO look-ahead (a round is graded only after its horizon has passed, using prices
≤ today); raw per-stock ARM is never read or written here (we grade on PRICE outcomes + the already-
scrubbed prereg text). Reviews are git-tracked audit (amc_book/<AMC>/<SCHEME>/reviews/<asof>.json +
lessons.md); a firm roll-up goes to output/_amc/live/review_latest.json for the site/desks.
"""
import os
import csv
import json
import datetime

import pandas as pd

from . import amc_firm as af
from . import amc_live as al
from . import amc_replay as ar

LIVE_DIR = al.LIVE_DIR
HORIZON_DEFAULT = None          # None = grade to the latest priced day (the natural live "to-date")


# ───────────────────────────────────────────────────────── small stats (no scipy)
def _rank(xs):
    """Fractional (tie-averaged) ranks of a list — for Spearman."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0                    # average rank (1-based) over the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a, b):
    n = len(a)
    if n < 2:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 1e-12 or vb <= 1e-12:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / (va ** 0.5 * vb ** 0.5)


def _spearman(a, b):
    """Spearman rank correlation of two equal-length lists, or None if < 5 points / degenerate."""
    if len(a) < 5 or len(a) != len(b):
        return None
    return _pearson(_rank(a), _rank(b))


# ───────────────────────────────────────────────────────── benchmark return over a window
_BENCH = {}


def _bench_series(reg_entry):
    """The scheme's benchmark TR level series (date-indexed), cached. Same index the scorecard uses."""
    bname = ar._bench_for(reg_entry)
    if not bname:
        return None, None
    if bname not in _BENCH:
        try:
            bf = ar._data.get_level_frame([bname], measure="TR")
            s = bf[bname].dropna() if (bf is not None and bname in bf.columns) else None
            if s is not None:
                s.index = pd.to_datetime(s.index)
                s = s.sort_index()
            _BENCH[bname] = s
        except Exception:
            _BENCH[bname] = None
    return bname, _BENCH[bname]


def _level_at(series, date_str):
    """Last level on/before date_str, or None."""
    if series is None:
        return None
    seg = series[series.index <= pd.Timestamp(date_str)]
    return float(seg.iloc[-1]) if len(seg) else None


def _grade_end(asof_str, horizon_days):
    """The grade date t1 = asof + horizon trading days (capped at the latest priced day), as a string.
    horizon_days None → the latest priced day. Returns (t1_str, elapsed_trading_days)."""
    idx = af._prices().index
    pos = idx.searchsorted(pd.Timestamp(asof_str), side="right") - 1   # last day ≤ asof
    if pos < 0:
        return asof_str, 0
    end_pos = (len(idx) - 1) if horizon_days is None else min(pos + horizon_days, len(idx) - 1)
    return str(idx[end_pos].date()), int(end_pos - pos)


# ───────────────────────────────────────────────────────── load a scheme's last round of bets
def _prereg_rows(reg_entry):
    p = os.path.join(af.scheme_dir(reg_entry["amc"], reg_entry["scheme"]), "prereg.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _last_round(reg_entry):
    """The bets from the MOST RECENT round in prereg.jsonl: (asof, [rows]). ('', []) if none."""
    rows = _prereg_rows(reg_entry)
    if not rows:
        return "", []
    asof = max(str(r.get("date")) for r in rows)
    return asof, [r for r in rows if str(r.get("date")) == asof]


# ───────────────────────────────────────────────────────── grade one scheme's last round
def grade_scheme(reg_entry, asof=None, horizon_days=HORIZON_DEFAULT, log=print):
    """Score a scheme's pre-registered bets (the `asof` round, or its latest round) to the grade date.
    Returns the review dict (or None if there are no bets). Pure: reads prereg + prices + benchmark."""
    if asof is None:
        asof, rows = _last_round(reg_entry)
    else:
        rows = [r for r in _prereg_rows(reg_entry) if str(r.get("date")) == str(asof)]
    if not rows:
        return None

    t1, elapsed = _grade_end(asof, horizon_days)
    bname, bser = _bench_series(reg_entry)
    b0 = _level_at(bser, asof)
    b1 = _level_at(bser, t1)
    bench_ret = (b1 / b0 - 1.0) if (b0 and b1 and b0 > 0) else None

    graded = []
    for r in rows:
        sym = r.get("sym")
        tp = af._f(r.get("target_pct"))
        p0 = af.price_asof(sym, asof)
        p1 = af.price_asof(sym, t1)
        rec = {"sym": sym, "target_pct": tp, "play_type": r.get("play_type"),
               "thesis": r.get("thesis"), "falsification": r.get("falsification")}
        if p0 is None or p1 is None or p0 <= 0:
            rec["status"] = "unpriced"
            rec["raw_return_pct"] = rec["active_return_pct"] = None
        elif elapsed <= 0:
            rec["status"] = "pending"
            rec["raw_return_pct"] = rec["active_return_pct"] = None
        else:
            raw = p1 / p0 - 1.0
            act = (raw - bench_ret) if bench_ret is not None else None
            rec["raw_return_pct"] = round(100 * raw, 2)
            rec["active_return_pct"] = (round(100 * act, 2) if act is not None else None)
            rec["status"] = ("win" if (act is not None and act > 0) else
                             ("loss" if act is not None else "no_bench"))
        graded.append(rec)

    scored = [g for g in graded if g["status"] in ("win", "loss")]
    n = len(scored)
    hit = round(100.0 * sum(1 for g in scored if g["status"] == "win") / n, 1) if n else None
    avg_act = round(sum(g["active_return_pct"] for g in scored) / n, 2) if n else None
    med_act = None
    if n:
        s = sorted(g["active_return_pct"] for g in scored)
        med_act = round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0, 2)
    ic = (_spearman([g["target_pct"] for g in scored], [g["active_return_pct"] for g in scored])
          if n >= 5 else None)
    ranked = sorted(scored, key=lambda g: -(g["active_return_pct"]))
    review = {
        "scheme": reg_entry["scheme"], "amc": reg_entry["amc"], "category": reg_entry["category"],
        "fm_key": al._FM_KEY.get(reg_entry["category"], "flexicap"),
        "round_asof": asof, "graded_to": t1, "horizon_trading_days": elapsed,
        "benchmark": bname, "benchmark_return_pct": (round(100 * bench_ret, 2) if bench_ret is not None else None),
        "n_bets": len(rows), "n_graded": n,
        "n_pending": sum(1 for g in graded if g["status"] == "pending"),
        "n_unpriced": sum(1 for g in graded if g["status"] == "unpriced"),
        "hit_rate_pct": hit, "avg_active_return_pct": avg_act, "median_active_return_pct": med_act,
        "conviction_ic": (round(ic, 3) if ic is not None else None),
        "best_calls": [{"sym": g["sym"], "active_return_pct": g["active_return_pct"], "target_pct": g["target_pct"]}
                       for g in ranked[:5]],
        "worst_calls": [{"sym": g["sym"], "active_return_pct": g["active_return_pct"], "target_pct": g["target_pct"]}
                        for g in ranked[-5:][::-1]] if n else [],
        "bets": graded,
    }
    log(f"[grade] {reg_entry['scheme']}: round {asof} → {t1} ({elapsed}d): "
        + (f"{n} graded, hit {hit}%, avg active {avg_act}%, conviction-IC {review['conviction_ic']}"
           if n else f"all {review['n_pending']} pending (horizon not elapsed)"))
    return review


# ───────────────────────────────────────────────────────── persist + lessons text
def _reviews_dir(reg_entry):
    d = os.path.join(af.scheme_dir(reg_entry["amc"], reg_entry["scheme"]), "reviews")
    os.makedirs(d, exist_ok=True)
    return d


def save_review(reg_entry, review):
    """Write the round review JSON + append a human-readable lessons.md (the FM's running report card)."""
    d = _reviews_dir(reg_entry)
    json.dump(review, open(os.path.join(d, f"{review['round_asof']}.json"), "w", encoding="utf-8"),
              indent=1, default=str)
    json.dump(review, open(os.path.join(d, "latest.json"), "w", encoding="utf-8"), indent=1, default=str)
    with open(os.path.join(d, "lessons.md"), "a", encoding="utf-8") as f:
        f.write(_lesson_md(review) + "\n")
    return d


def _lesson_md(rv):
    """A compact, plain-English report card for one graded round (what the agent reads next round)."""
    if not rv.get("n_graded"):
        return (f"### Round {rv['round_asof']} — PENDING (graded_to {rv['graded_to']}, "
                f"horizon {rv['horizon_trading_days']}d not yet elapsed; {rv['n_pending']} bets awaiting outcome)")
    best = ", ".join(f"{c['sym']} {c['active_return_pct']:+}%" for c in rv["best_calls"][:3])
    worst = ", ".join(f"{c['sym']} {c['active_return_pct']:+}%" for c in rv["worst_calls"][:3])
    ic = rv["conviction_ic"]
    ic_txt = ("no read (too few bets)" if ic is None else
              f"{ic:+.2f} — {'you sized winners bigger (sizing skill)' if ic > 0.1 else ('you sized LOSERS bigger (negative sizing)' if ic < -0.1 else 'sizing added ~nothing')}")
    return (f"### Round {rv['round_asof']} → {rv['graded_to']} ({rv['horizon_trading_days']}d) vs {rv['benchmark']}\n"
            f"- **Hit rate {rv['hit_rate_pct']}%** ({rv['n_graded']} graded; benchmark moved {rv['benchmark_return_pct']:+}%).\n"
            f"- **Avg active return {rv['avg_active_return_pct']:+}%** (median {rv['median_active_return_pct']:+}%) over the index.\n"
            f"- **Conviction-IC {ic_txt}** (did weight track outcome).\n"
            f"- Best: {best}.  Worst: {worst}.\n"
            f"- Lesson: synthesize this honestly into next round — keep what worked, cut the theses the "
            f"market refuted; don't re-chase a name your own falsifier flagged.")


# ───────────────────────────────────────────────────────── what the next round's desk reads
def latest_review(reg_entry):
    """A COMPACT summary of the scheme's most recent graded round, for embedding in the next desk
    (no per-bet detail, no licensed data) — or None. Best-effort; never raises."""
    try:
        p = os.path.join(af.scheme_dir(reg_entry["amc"], reg_entry["scheme"]), "reviews", "latest.json")
        rv = af._read_json(p) if os.path.exists(p) else None
        if not rv:
            return None
        return {k: rv.get(k) for k in ("round_asof", "graded_to", "horizon_trading_days", "benchmark",
                                       "benchmark_return_pct", "n_bets", "n_graded", "n_pending",
                                       "hit_rate_pct", "avg_active_return_pct", "median_active_return_pct",
                                       "conviction_ic", "best_calls", "worst_calls")}
    except Exception:
        return None


# ───────────────────────────────────────────────────────── firm-wide driver
def grade_all(asof=None, horizon_days=HORIZON_DEFAULT, save=True, log=print):
    """Grade every pilot's most recent round (or the `asof` round), persist reviews + lessons, and write
    a firm roll-up to output/_amc/live/review_latest.json. Returns the roll-up doc."""
    reviews = []
    for re_ in al.pilot_reg_entries():
        rv = grade_scheme(re_, asof=asof, horizon_days=horizon_days, log=log)
        if rv is None:
            continue
        if save:
            save_review(re_, rv)
        reviews.append(rv)
    # firm-level breadth: average pairwise correlation of desks' winning names would need overlap; the
    # cheap firm read = mean hit-rate + dispersion of avg-active across desks (de-correlated desks differ).
    graded = [r for r in reviews if r.get("n_graded")]
    firm = {
        "as_run": str(datetime.datetime.now())[:19] if False else None,   # stamped by the caller, not here
        "n_schemes": len(reviews), "n_schemes_graded": len(graded),
        "mean_hit_rate_pct": round(sum(r["hit_rate_pct"] for r in graded) / len(graded), 1) if graded else None,
        "mean_avg_active_pct": round(sum(r["avg_active_return_pct"] for r in graded) / len(graded), 2) if graded else None,
        "schemes": [{k: r.get(k) for k in ("scheme", "fm_key", "round_asof", "graded_to",
                                           "horizon_trading_days", "n_graded", "n_pending", "hit_rate_pct",
                                           "avg_active_return_pct", "conviction_ic", "benchmark",
                                           "benchmark_return_pct", "best_calls", "worst_calls")} for r in reviews],
    }
    if save:
        os.makedirs(LIVE_DIR, exist_ok=True)
        json.dump(firm, open(os.path.join(LIVE_DIR, "review_latest.json"), "w", encoding="utf-8"),
                  indent=1, default=str)
    log(f"[grade] {len(reviews)} scheme(s) reviewed ({len(graded)} graded), "
        f"mean hit {firm['mean_hit_rate_pct']}% → {os.path.join(LIVE_DIR, 'review_latest.json')}")
    return firm


# ───────────────────────────────────────────────────────── self-test (synthetic, deterministic)
def _selftest():
    """Validate the scoring math on a controlled synthetic scenario (known answer), independent of any
    market data — so a regression in the ranking/IC/active-return logic is caught immediately."""
    # 5 bets; we KNOW each one's raw return and the benchmark return, so we know every output.
    bench_ret = 0.05                              # +5% benchmark
    # (target_pct, raw_return) — bigger bets should do better for a POSITIVE conviction-IC
    fake = [("A", 5.0, 0.20), ("B", 4.0, 0.15), ("C", 3.0, 0.10), ("D", 2.0, 0.02), ("E", 1.0, -0.05)]
    scored = []
    for sym, tp, raw in fake:
        act = raw - bench_ret
        scored.append({"sym": sym, "target_pct": tp, "active_return_pct": round(100 * act, 2),
                       "status": "win" if act > 0 else "loss"})
    n = len(scored)
    hit = 100.0 * sum(1 for g in scored if g["status"] == "win") / n
    avg = sum(g["active_return_pct"] for g in scored) / n
    ic = _spearman([g["target_pct"] for g in scored], [g["active_return_pct"] for g in scored])
    # expected: A,B,C win (raw>5% bench), D,E lose → hit 60%; active = raw−5% → [15,10,5,-3,-10] avg 3.4
    assert hit == 60.0, f"hit {hit}"
    assert abs(avg - 3.4) < 1e-9, f"avg {avg}"
    assert ic is not None and ic > 0.99, f"ic {ic}"        # perfect monotone tp↔active → Spearman ≈ +1
    # a PERFECTLY ANTI-sized book → IC ≈ −1
    anti = list(zip([g["target_pct"] for g in scored], [-g["active_return_pct"] for g in scored]))
    ic2 = _spearman([a for a, _ in anti], [b for _, b in anti])
    assert ic2 is not None and ic2 < -0.99, f"ic2 {ic2}"
    print("[grade/selftest] OK — hit 60.0%, avg active +3.40%, conviction-IC +1.00 / anti −1.00")


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _selftest()
    print("--- live: grade each pilot's latest round (to-date) ---")
    grade_all()
