"""
pipeline.py — Vistas's ONE daily data-refresh + publish engine.

WHAT THIS IS (first principles)
-------------------------------
Vistas pulls from ~10 independent public feeds (NSE indices, NSE bhavcopy, Yahoo stocks/world,
MOSPI/FBIL/BIS macro, AMFI fund NAVs + market cap, AMC fund portfolios, Screener fundamentals).
Each already has its own fetcher. Historically each was a separate hand-run .bat. This module
turns that into a SINGLE process that, every evening:

    refresh every source  ->  reload  ->  rebuild the terminal site  ->  validate  ->  publish

…robustly: one feed failing never aborts the run; every step is timed and audited; a dated
health report is written; and (per KV) NOTHING blocks the publish except a genuinely faulty
shell (the build-integrity gate, which must stay). Auto-publish is the primary path; a
build-only "--no-push" run + the one-click  pipeline/Publish Last Build.bat  is the failsafe.

ADAPTABLE BY DESIGN (the #1 requirement)
----------------------------------------
The terminal's tabs/sources keep growing, so the engine must NOT be edited every time. The list
of feeds is a DECLARATIVE REGISTRY (`build_sources()`): adding a new data source = append ONE
`Source(...)` line; the generic runner/auditor/reporter handle it unchanged. The build step calls
`deck.save_terminal_site()`, which already auto-discovers every tab's data (fundamentals, quant,
funds, macro, …), so a NEW TAB needs no pipeline change at all — only a new *data feed* does, and
that is one registry line. Each source is also independently skippable (`--skip`, `--only`).

GRACEFUL-DEGRADE CONTRACT
-------------------------
Every source returns a native status dict and never raises out of the runner (we catch all). A
source that fails or returns "no new rows" is recorded as degraded / no-op (a market holiday is a
no-op, not a failure) and the last-good data on disk is kept. The run continues regardless.

Run it:   python -m vistas.pipeline            # refresh all -> build -> validate -> PUBLISH if green
          python -m vistas.pipeline --no-push  # refresh all -> build -> validate   (failsafe: no push)
          python -m vistas.pipeline --dry-run  # refresh sources only (no build/publish)
          python -m vistas.pipeline --skip bhav,screener      # skip the heavy feeds
          python -m vistas.pipeline --only macro,world        # run just these
          python -m vistas.pipeline --light    # skip the heavy feeds (bhav, screener, issued shares)
"""
from __future__ import annotations

import os
import sys
import json
import time
import argparse
import datetime as dt
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)                       # so `import publish_terminal` (a root script) works
_REPORT_DIR = os.path.join(_ROOT, "data", "_refresh")


# --------------------------------------------------------------------------- the source contract
class Source:
    """One refreshable data feed. `run` is a zero-arg callable that performs the INCREMENTAL pull
    and returns the fetcher's native status dict (or anything). Imports happen INSIDE `run` so a
    missing/broken module degrades to a per-source error, never a global import crash.

    `heavy` feeds (full bhavcopy rebuild, whole-universe screener) are the slow ones — tagged so a
    `--light` run can skip them and so the report can show where time went. `reads` is a short note
    on what the source updates (for the report + the README); purely documentation."""

    def __init__(self, key, title, run, heavy=False, reads="", timeout=None, cadence="daily"):
        self.key = key
        self.title = title
        self.run = run
        self.heavy = heavy
        self.reads = reads
        # CADENCE — how often this feed actually changes, so the daily job doesn't waste time (and WAF
        # exposure) re-pulling slow-moving data: "daily" (prices/NAVs), "weekly" (fundamentals — quarterly
        # results land on scattered dates, so a weekly sweep catches them), "monthly" (holdings, issued
        # shares — they move ~monthly). A feed runs only when due (≥7d / ≥28d since its last good pull);
        # `--all`/`--only` override the gate for a forced manual run.
        self.cadence = cadence
        # Hard wall-clock budget per feed. A feed whose network call HANGS (a server that accepts
        # the socket but never replies — NSE does this when it throttles an IP) would otherwise stall
        # the whole nightly run forever. Light feeds get 4 min, heavy crawls 20 min; overrun = the
        # feed is abandoned and marked degraded, and the run moves on with the last-good data.
        self.timeout = timeout if timeout is not None else (1200 if heavy else 240)


def build_sources():
    """THE REGISTRY — the single place that lists every daily feed, newest-cheapest first so the
    core terminal is fresh fast and the heavy feeds trail. Append one line to add a feed."""
    S = []

    S.append(Source("nse_tr", "NSE TR indices",
                    lambda: __import__("vistas.fetch", fromlist=["x"]).update(dry=False),
                    reads="data/Indices Data TR till <date>.csv"))

    # NSE PR (price-return) + VAL (PE/PB/DY) are DROPPED from the terminal pipeline (2026-06-24, KV):
    # the WAF-gated endpoints never worked reliably and the terminal doesn't use them (the Valuation
    # tab was dropped). Removed so they can never stall the daily pull. The standalone manual backfill
    # (_backfill_measures.py / "Pull PR + Valuation.bat") still exists for ad-hoc use, off the pipeline.

    S.append(Source("stocks_px", "Stock prices (Yahoo adj-close)",
                    lambda: __import__("vistas.stocks", fromlist=["x"]).update_stocks(),
                    reads="data/Stocks Data PX till <date>.csv"))

    S.append(Source("world", "World / cross-asset (Yahoo)",
                    lambda: __import__("vistas.world", fromlist=["x"]).update_world(),
                    reads="data/World Data PX till <date>.csv"))

    S.append(Source("macro", "India macro (MOSPI/FBIL/BIS/RBI)",
                    lambda: __import__("vistas.macro", fromlist=["x"]).build_snapshot(),
                    reads="data/India Macro till <date>.csv", cadence="weekly"))

    S.append(Source("funds_nav", "Mutual-fund NAVs (AMFI hist-report primary, mfapi fallback)",
                    lambda: __import__("vistas.funds_nav", fromlist=["x"]).build_snapshot(),
                    reads="data/India Mutual Fund NAV till <date>.csv"))

    S.append(Source("mcap_amfi", "Market cap (AMFI bulk XLSX)",
                    lambda: __import__("vistas.shares", fromlist=["x"]).build_from_amfi(),
                    reads="data/shares.json (AMFI mcap + SEBI cohort)", cadence="weekly"))

    S.append(Source("benchmarks", "NSE index benchmark portfolios (EW + free-float-mcap weights)",
                    lambda: __import__("vistas.benchmarks", fromlist=["x"]).build_all(),
                    reads="data/benchmarks/<slug>.json + _manifest.json", cadence="weekly"))

    # ---- heavy feeds (slow; tagged so --light can skip them) -------------------------------------
    S.append(Source("bhav", "NSE bhavcopy stock TR + identity master", heavy=True,
                    run=_run_bhav,
                    reads="data/Stocks Data TR till <date>.csv + data/stock_security_master.json"))

    S.append(Source("screener", "Screener fundamentals (new, bounded)", heavy=True,
                    run=_run_screener,
                    reads="data/screener/<SYM>.json", cadence="weekly"))

    S.append(Source("issued_shares", "Exact issued shares (NSE, WAF-gated, bounded)", heavy=True,
                    run=_run_issued_shares,
                    reads="data/shares.json (exact issuedSize, supersedes AMFI)", cadence="monthly"))

    return S


# Per-run caps keep the nightly window BOUNDED: each feed pulls a slice of the not-yet-covered
# universe and resumes the rest the next night, so a first run can never become an hours-long crawl
# and a steady state is a fast no-op. Tune here as the universe grows.
_SCREENER_PER_RUN = 600
_ISSUED_PER_RUN = 300


def _stock_universe():
    st = __import__("vistas.stocks", fromlist=["x"])
    sdf = st.load()
    return list(sdf.columns) if sdf is not None and len(sdf) else []


def _run_screener():
    """Pull fundamentals for companies we DON'T yet have, capped per run (resumes nightly). New-only
    keeps it bounded; periodic full re-pulls (results season) stay a manual `screener.refresh(full=True)`."""
    sc = __import__("vistas.screener", fromlist=["x"])
    have = set(sc.available())
    todo = [s for s in _stock_universe() if s not in have][:_SCREENER_PER_RUN]
    if not todo:
        return {"ok": True, "updated": False, "message": "fundamentals universe already covered"}
    return sc.refresh(todo, full=False)


def _run_issued_shares():
    """Collect exact issued shares for symbols missing from the shares cache, capped per run. NSE's
    quote API is WAF-gated (403 from a datacenter IP) — degrades cleanly there, fills on KV's runtime."""
    sh = __import__("vistas.shares", fromlist=["x"])
    have = set(sh.shares_by_symbol().keys())
    todo = [s for s in _stock_universe() if s not in have][:_ISSUED_PER_RUN]
    if not todo:
        return {"ok": True, "updated": False, "message": "issued-shares universe already covered"}
    return sh.build(todo)


def _run_bhav():
    """Bhavcopy incremental tail: fetch any new trading-day zips (cached), then reassemble the TR
    panel + identity master and promote it live. Heavy but cache-backed (only new days download)."""
    bp = __import__("vistas.bhav_prices", fromlist=["x"])
    bp.build_cache()                                   # fetch only missing trading days (zip cache)
    return bp.build_panel(promote=True)                # assemble -> CA -> TR -> finalize -> promote


# --------------------------------------------------------------------------- run + normalise
def _first(d, keys):
    """First non-None value among `keys` in dict `d` (key-name sniffing across fetcher shapes)."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _normalise(key, title, heavy, raw, dur, err):
    """Fold any fetcher's native return into ONE shape the report understands. Best-effort key
    sniffing keeps it adaptable — a new source rarely needs a custom normaliser."""
    if err is not None:
        return {"source": key, "title": title, "heavy": heavy, "ok": False, "noop": False,
                "asof": None, "rows": None, "message": "", "error": str(err)[:300],
                "secs": round(dur, 1)}
    r = raw if isinstance(raw, dict) else {}
    ok = bool(r.get("ok", True))
    asof = _first(r, ("new_asof", "asof", "last", "period", "last_date"))
    rows = _first(r, ("rows_added", "n_pulled", "n_added", "n_days", "n_series", "n_schemes",
                      "n_rows", "n_symbols", "collected", "n"))
    # A no-op is EXPLICIT: the fetcher said nothing changed (updated=False) or 0 rows. An UNKNOWN
    # row count on an otherwise-OK call is NOT a no-op (we just don't have the count) — report it ok.
    noop = ok and (r.get("updated") is False or rows == 0)
    return {"source": key, "title": title, "heavy": heavy, "ok": ok, "noop": bool(noop),
            "asof": str(asof) if asof else None, "rows": rows,
            "message": str(r.get("message") or "")[:300], "error": str(r.get("error") or "")[:300],
            "secs": round(dur, 1)}


def run_one(src: Source, say=print):
    say(f"  -> {src.title} …")
    t0 = time.time()
    # Run the feed in a worker thread and wait at most src.timeout. A hung network call can't be
    # interrupted cleanly cross-platform (signal.alarm is POSIX-only), so on overrun we ABANDON the
    # thread (daemon — it dies with the process) and continue: the feed is reported degraded and the
    # last-good data on disk is kept. This is what makes "one feed failing never aborts" true even
    # for a feed that hangs rather than errors.
    import threading
    box = {}

    def _work():
        try:
            box["raw"] = src.run()
        except Exception as e:                          # a source NEVER aborts the run
            box["err"] = e
            traceback.print_exc()
    th = threading.Thread(target=_work, name=f"src-{src.key}", daemon=True)
    th.start()
    th.join(src.timeout)
    if th.is_alive():
        res = _normalise(src.key, src.title, src.heavy, None, time.time() - t0,
                         TimeoutError(f"hung — abandoned after {src.timeout}s (last-good data kept)"))
        say(f"     [TIMEOUT] {src.title}: no response in {src.timeout}s — abandoned, run continues")
        return res
    raw, err = box.get("raw"), box.get("err")
    res = _normalise(src.key, src.title, src.heavy, raw, time.time() - t0, err)
    tag = "ERROR" if not res["ok"] else ("no-op" if res["noop"] else "ok")
    say(f"     [{tag}] {res['title']}: asof={res['asof']} rows={res['rows']} "
        f"({res['secs']}s)" + (f"  err={res['error']}" if res["error"] else ""))
    return res


# ---- CADENCE GATE: prices daily, fundamentals weekly, holdings/issued-shares monthly. One nightly job;
# each feed runs only when due (≥ its interval since the last GOOD pull). State in _cadence_state.json.
_CADENCE_FILE = os.path.join(_REPORT_DIR, "_cadence_state.json")
_CADENCE_DAYS = {"daily": 0, "weekly": 7, "monthly": 28}


def _load_cadence_state():
    try:
        with open(_CADENCE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cadence_state(state):
    try:
        os.makedirs(_REPORT_DIR, exist_ok=True)
        with open(_CADENCE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
    except Exception:
        pass


def _cadence_due(src, state, today):
    """True if `src` should run today: daily always; weekly/monthly only if ≥ its interval since the
    last successful pull (or never pulled)."""
    days = _CADENCE_DAYS.get(src.cadence, 0)
    if days == 0:
        return True
    last = state.get(src.key)
    if not last:
        return True
    try:
        return (today - dt.date.fromisoformat(last)).days >= days
    except Exception:
        return True


def run_sources(skip=(), only=(), light=False, force_all=False, say=print):
    """Run the registry (minus skips), honouring each feed's CADENCE, returning the per-source results.
    A feed not due today is skipped with a note. `--only <keys>` force-runs those (ignores the gate);
    `--all` force-runs everything (e.g. a fresh box, or a manual full refresh)."""
    out = []
    state = _load_cadence_state()
    today = dt.date.today()
    for s in build_sources():
        if only and s.key not in only:
            continue
        if s.key in skip:
            say(f"  -> {s.title}: skipped"); continue
        if light and s.heavy:
            say(f"  -> {s.title}: skipped (--light)"); continue
        forced = force_all or (s.key in only)
        if not forced and not _cadence_due(s, state, today):
            nxt = state.get(s.key, "?")
            say(f"  -> {s.title}: not due ({s.cadence}; last {nxt}) — skipped"); continue
        r = run_one(s, say=say)
        out.append(r)
        if r.get("ok"):                         # advance the cadence clock only on a clean (non-degraded) run
            state[s.key] = today.isoformat()
    _save_cadence_state(state)
    return out


# --------------------------------------------------------------------------- audit + report
def audit(results):
    """Phase-1 audit: turn raw results into a health summary. Extensible — add per-source range /
    coverage / asof-advanced checks here as the terminal grows; today it classifies and counts."""
    degraded = [r for r in results if not r["ok"]]
    noop = [r for r in results if r["ok"] and r["noop"]]
    fresh = [r for r in results if r["ok"] and not r["noop"]]
    return {
        "n_sources": len(results),
        "n_fresh": len(fresh), "n_noop": len(noop), "n_degraded": len(degraded),
        "degraded": [r["source"] for r in degraded],
        "fresh": [r["source"] for r in fresh],
        "noop": [r["source"] for r in noop],
        "total_secs": round(sum(r["secs"] for r in results), 1),
    }


def write_report(run_iso, results, summary, build_info, validation, published, publish_note):
    """Write the dated machine report (data/_refresh/run-<date>.json) + a human last_run.md."""
    os.makedirs(_REPORT_DIR, exist_ok=True)
    report = {"run": run_iso, "summary": summary, "sources": results,
              "build": build_info, "validation": validation,
              "published": published, "publish_note": publish_note}
    day = run_iso[:10]
    with open(os.path.join(_REPORT_DIR, f"run-{day}.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    lines = [f"# Vistas daily refresh — {run_iso}", ""]
    lines.append(f"- **sources:** {summary['n_sources']}  ·  fresh {summary['n_fresh']}  ·  "
                 f"no-op {summary['n_noop']}  ·  **degraded {summary['n_degraded']}** "
                 f"({', '.join(summary['degraded']) or 'none'})")
    lines.append(f"- **total source time:** {summary['total_secs']}s")
    if build_info:
        lines.append(f"- **build:** shell {build_info.get('shell_mb','?')} MB · "
                     f"{build_info.get('n_stock_files','?')} stock + "
                     f"{build_info.get('n_fundamental_files','?')} fundamental files")
    _v = validation.get("ok")
    _vtxt = "SKIPPED" if _v is None else ("PASS" if _v else "FAIL")
    lines.append(f"- **validation:** {_vtxt} — {validation.get('detail','')}")
    lines.append(f"- **published:** {'YES' if published else 'NO'} — {publish_note}")
    lines.append("")
    lines.append("| source | status | asof | rows | secs | note |")
    lines.append("|--------|--------|------|------|------|------|")
    for r in results:
        st = "ERROR" if not r["ok"] else ("no-op" if r["noop"] else "ok")
        note = r["error"] or r["message"]
        lines.append(f"| {r['title']} | {st} | {r['asof'] or ''} | {r['rows'] if r['rows'] is not None else ''} "
                     f"| {r['secs']} | {note[:80]} |")
    with open(os.path.join(_REPORT_DIR, "last_run.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return report


# --------------------------------------------------------------------------- the daily run
def run_daily(publish=True, skip=(), only=(), light=False, dry_run=False, force_all=False, say=None):
    """Refresh every source -> reload -> rebuild terminal -> validate -> (auto)publish if green.
    Returns the report dict. NOTHING but a faulty shell blocks publish (KV policy)."""
    run_iso = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # reuse the publisher's logger + build/validate/publish helpers (no duplication)
    import publish_terminal as pt
    say = say or pt.pp.say
    pt.pp.hr(); say(f"VISTAS DAILY REFRESH — {run_iso}"); pt.pp.hr()

    say("[1/5] refreshing every data source (graceful-degrade; one failure never aborts)…")
    results = run_sources(skip=skip, only=only, light=light, force_all=force_all, say=say)
    summary = audit(results)
    say(f"  sources: fresh {summary['n_fresh']} · no-op {summary['n_noop']} · "
        f"degraded {summary['n_degraded']} ({', '.join(summary['degraded']) or 'none'})")

    if dry_run:
        say("[dry-run] sources refreshed; skipping build/validate/publish.")
        return write_report(run_iso, results, summary, {}, {"ok": None, "detail": "skipped (dry-run)"},
                            False, "dry-run")

    # the build + publish write output/terminal_site & _pages — take the single-flight lock so a
    # concurrent manual publish / second refresh can't corrupt them (the source refresh above writes
    # only data/, so it doesn't need the lock).
    if not pt.acquire_lock():
        return write_report(run_iso, results, summary, {},
                            {"ok": None, "detail": "aborted — another build/publish is running"},
                            False, "aborted: another build/publish holds the lock")
    try:
        say("[2/5] reloading data + rebuilding the hosted site…")
        from vistas import data
        data.reload()
        # mark the live-forward digital-AMC pilot books to the just-refreshed close (no-LLM,
        # paper-only; holdings frozen between monthly LLM rounds). Best-effort — a hiccup here must
        # NEVER block the terminal build/publish, so it is wrapped and non-fatal.
        try:
            from vistas import amc_daily_mark
            amc_daily_mark.run(log=say)
        except Exception as e:
            say(f"  (digital-AMC daily mark skipped, non-fatal: {e})")
        build_info = {}
        try:
            build_info = pt.build_site()
        except Exception as e:
            say(f"  BUILD FAILED: {e}")
            traceback.print_exc()
            return write_report(run_iso, results, summary, {}, {"ok": False, "detail": f"build crashed: {e}"},
                                False, "build crashed — last good site stays live")

        say("[3/5] validating the shell…")
        ok, detail = pt.pp.validate(pt.SHELL)
        say("  " + detail.replace("\n", "\n  "))
        validation = {"ok": bool(ok), "detail": detail.replace("\n", " ")[:500]}

        published, publish_note = False, ""
        if not ok:
            publish_note = "FAULTY SHELL — not published (last good stays live)"
            say("[4/5] " + publish_note)
        elif not publish:
            publish_note = "built + validated; --no-push (use pipeline/Publish Last Build.bat to push)"
            say("[4/5] " + publish_note)
        else:
            say("[4/5] publishing terminal/ to GitHub…")
            try:
                published = pt.publish_site()
                publish_note = ("published OK" if published else
                                "push FAILED — run pipeline/Publish Last Build.bat to retry (build is on disk)")
            except Exception as e:
                publish_note = f"push crashed ({e}) — run pipeline/Publish Last Build.bat (build is on disk)"
                say("  " + publish_note)

        # [5/5] off-machine backups — only after a real publish; best-effort, never gates the run.
        if published:
            say("[5/5] backing up off-machine (source -> vistas-codebase; licensed ARM -> encrypted cloud)…")
            try:
                pt.backup_codebase()
            except Exception as e:
                say(f"  codebase backup skipped (non-fatal): {e}")
            try:
                pt.backup_arm()
            except Exception as e:
                say(f"  ARM backup skipped (non-fatal): {e}")
        else:
            say("[5/5] no publish this run — skipping off-machine backups")

        report = write_report(run_iso, results, summary, build_info, validation, published, publish_note)
        pt.pp.hr()
        say(f"DAILY REFRESH DONE — fresh {summary['n_fresh']}/{summary['n_sources']}, "
            f"published={'YES' if published else 'NO'}. Report: data/_refresh/last_run.md")
        pt.pp.hr()
        return report
    finally:
        pt.release_lock()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Vistas daily data refresh + publish engine")
    ap.add_argument("--no-push", action="store_true", help="build + validate only (failsafe; no git push)")
    ap.add_argument("--dry-run", action="store_true", help="refresh sources only; no build/publish")
    ap.add_argument("--light", action="store_true", help="skip the heavy feeds (bhav, screener, issued shares)")
    ap.add_argument("--skip", default="", help="comma list of source keys to skip")
    ap.add_argument("--only", default="", help="comma list of source keys to run (exclusively)")
    ap.add_argument("--all", action="store_true", dest="force_all",
                    help="ignore the cadence gate and force every feed to run (fresh box / manual full refresh)")
    a = ap.parse_args(argv)
    skip = tuple(x.strip() for x in a.skip.split(",") if x.strip())
    only = tuple(x.strip() for x in a.only.split(",") if x.strip())
    try:
        rep = run_daily(publish=not a.no_push, skip=skip, only=only, light=a.light,
                        dry_run=a.dry_run, force_all=a.force_all)
        return 0 if (rep.get("validation", {}).get("ok") is not False) else 1
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
