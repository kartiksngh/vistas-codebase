"""vistas/amc_round.py — the monthly LIVE-FORWARD ROUND orchestration (the deterministic glue).

A monthly round = ONE LLM step (the FM+CIO `_amc_rebalance.js` Workflow) wrapped in deterministic
prep / apply / publish. The LLM step can only run inside the Claude harness (a Workflow needs agents),
so this module gives the THREE deterministic commands around it, turning a round into a clean,
idempotent runbook:

  1. python -m vistas.amc_round start  [--asof YYYY-MM-DD]
        → prepare_round: write each pilot's decision desk + round_manifest.json and PRINT the manifest
          JSON on stdout (this is the `args` payload the FM Workflow consumes). asof defaults to the
          latest price date.
  2. [ Claude runs the Workflow `_amc_rebalance.js` with that manifest → {asof, decisions, cio};
       save the returned object to output/_amc/live/round_decisions_<asof>.json ]
  3. python -m vistas.amc_round finish --asof YYYY-MM-DD --decisions <path>
        → apply_round: guardrail + execute each FM book, mark, compare-to-quant, write round_<asof>.json
          (+ round_latest.json). Use --dry-run to validate the decisions file WITHOUT trading.
  4. python -m vistas.amc_round publish  [--no-push] [--no-backup]
        → daily-mark + rebuild the digital-AMC site + copy to _pages/digital-amc + git push + backups.

WHY NOT one fully-autonomous bat? The FM decisions are an LLM judgment call — by design a human (KV via
Claude) triggers and REVIEWS each monthly round before it goes live, and a Workflow can't be driven by a
plain .bat or by Windows Task Scheduler anyway. The part that IS autonomous is the no-LLM DAILY MARK
(`amc_daily_mark` / Run AMC Daily Mark.bat), schedulable nightly. See LIVE_FORWARD.md.

DISCIPLINE (unchanged): paper-only; no look-ahead; LLM proposes, the deterministic mandate guardrail
disposes; raw per-stock LSEG ARM is NEVER persisted (scrubbed in apply); the publish is gated by the
same single-flight build lock as the terminal (never two builds/publishes at once).
"""
import os
import sys
import json
import shutil
import argparse
import datetime

from . import amc_live as al
from . import amc_daily_mark as dm

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
LIVE_DIR = al.LIVE_DIR
PAGES_URL = "https://kartiksngh.github.io/vistas/digital-amc/"


def latest_asof():
    """The latest trading day in the price panel (the natural `asof` for a round)."""
    from . import amc_firm as af
    return str(af._prices().index[-1].date())


# ───────────────────────────────────────────────────────── 1) START (deterministic prep)
def cmd_start(asof=None, log=print):
    """Assemble + write the four FM decision desks and the round manifest; print the manifest JSON
    (the Workflow `args`). No book changes, no look-ahead. Returns the manifest doc."""
    asof = asof or latest_asof()
    log(f"[round/start] preparing the FM desks as-of {asof} …")
    manifest = al.prepare_round(asof, log=log)
    doc = {"asof": asof, "schemes": manifest}
    log("")
    log(f"[round/start] {len(manifest)} desks written under {al.DESK_DIR}")
    log("Next: run the Workflow `_amc_rebalance.js` with the args printed below, save its result to")
    log(f"      {os.path.join(LIVE_DIR, f'round_decisions_{asof}.json')}, then run `amc_round finish`.")
    print(json.dumps(doc))                      # machine-readable manifest on stdout (the Workflow args)
    return doc


# ───────────────────────────────────────────────────────── 2) FINISH (deterministic apply)
def _load_decisions(path):
    """Read the Workflow output {asof, decisions, cio}; tolerate a bare {decisions, cio} too."""
    d = json.load(open(path, encoding="utf-8"))
    return d.get("asof"), (d.get("decisions") or {}), d.get("cio")


def cmd_finish(asof, decisions_path, dry_run=False, log=print):
    """Guardrail + execute the FM decisions onto the books, mark, compare-to-quant, write the round
    docs. `--dry-run` validates the decisions file (counts/structure) WITHOUT trading or writing."""
    if not os.path.exists(decisions_path):
        raise SystemExit(f"decisions file not found: {decisions_path}")
    a2, decisions, cio = _load_decisions(decisions_path)
    asof = asof or a2
    if not asof:
        raise SystemExit("no asof (pass --asof or include it in the decisions file)")
    if not decisions:
        raise SystemExit(f"no `decisions` in {decisions_path}")
    n_tickets = sum(len((v or {}).get("tickets") or []) for v in decisions.values())
    log(f"[round/finish] asof {asof}: {len(decisions)} FM books, {n_tickets} tickets total; "
        f"CIO {'present' if cio else 'absent'}")
    if dry_run:
        for sl, v in decisions.items():
            longs = [t for t in (v.get("tickets") or []) if (t.get("target_pct") or 0) > 0]
            log(f"   {sl}: {len(longs)} long / {len(v.get('tickets') or [])} tickets — \"{(v.get('stance') or '')[:60]}\"")
        log("[round/finish] DRY-RUN — parsed OK; nothing applied.")
        return None
    doc = al.apply_round(asof, decisions, cio, log=log)
    log("")
    for s in doc["schemes"]:
        vq = s.get("vs_quant") or {}
        log(f"   {s['scheme']}: {s['n_holdings']} names, {s.get('deployed_pct')}% deployed, "
            f"turnover {s.get('turnover_pct')}%, active-vs-quant {vq.get('active_share_pct')}%")
    log(f"[round/finish] wrote {os.path.join(LIVE_DIR, f'round_{asof}.json')} (+ round_latest.json)")
    return doc


# ───────────────────────────────────────────────────────── 3) PUBLISH (mark → rebuild → push → backup)
def cmd_publish(no_push=False, no_backup=False, log=print):
    """Daily-mark the books, rebuild the digital-AMC site, and (unless --no-push) mirror it into
    _pages/digital-amc + push, then run the standing off-machine backups. Push is gated by the SAME
    single-flight build lock as the terminal publish (never two builds/publishes at once)."""
    log("[round/publish] (1/3) marking the books to the latest close…")
    dm.run(log=log)

    log("[round/publish] (2/3) rebuilding the digital-AMC site…")
    from . import amc_site
    amc_site.build()
    src = os.path.join(ROOT, "output", "_amc", "site", "index.html")
    if not os.path.exists(src):
        raise SystemExit("amc_site.build() produced no index.html")
    log(f"   built {os.path.getsize(src)//1024} KB")

    if no_push:
        log("[round/publish] (3/3) --no-push: site rebuilt + marked, NOT pushed.")
        return
    import publish_terminal as pt
    import publish_passive as pp
    if not pt.acquire_lock():
        raise SystemExit("another build/publish holds the lock — wait and retry (never two at once)")
    try:
        log("[round/publish] (3/3) publishing to _pages/digital-amc …")
        pp.ensure_repo()
        dst_dir = os.path.join(pp.PUB_DIR, "digital-amc")
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copyfile(src, os.path.join(dst_dir, "index.html"))
        pp.run(["git", "add", "-A"], cwd=pp.PUB_DIR)
        msg = "digital-amc live round " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        c = pp.run(["git", "commit", "-m", msg], cwd=pp.PUB_DIR)
        if "nothing to commit" in (c.stdout + c.stderr):
            log("   (digital-amc unchanged — nothing to push)")
        else:
            p = pp.run(["git", "push", "-u", "origin", "main"], cwd=pp.PUB_DIR)
            if p.returncode != 0:
                log("   PUSH FAILED:\n   " + (p.stdout + p.stderr).strip()[-800:])
                return
            log(f"   published OK → live in ~1 min at {PAGES_URL}")
        if not no_backup:
            log("[round/publish] off-machine backups (best-effort)…")
            pt.backup_codebase()        # source + amc_book audit trail → vistas-codebase
            pt.backup_arm()             # licensed ARM → encrypted off-machine mirror
    finally:
        pt.release_lock()


# ───────────────────────────────────────────────────────── CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="Live-forward AMC round orchestration")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("start", help="prepare FM desks + print the Workflow args")
    ps.add_argument("--asof", default=None)
    pf = sub.add_parser("finish", help="apply the Workflow's FM/CIO decisions to the books")
    pf.add_argument("--asof", default=None)
    pf.add_argument("--decisions", required=True, help="path to the Workflow's {asof,decisions,cio} json")
    pf.add_argument("--dry-run", action="store_true", help="validate the decisions file without trading")
    pb = sub.add_parser("publish", help="mark + rebuild + push the digital-AMC site")
    pb.add_argument("--no-push", action="store_true")
    pb.add_argument("--no-backup", action="store_true")
    sub.add_parser("mark", help="no-LLM daily mark only (alias of amc_daily_mark)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if args.cmd == "start":
        cmd_start(args.asof)
    elif args.cmd == "finish":
        cmd_finish(args.asof, args.decisions, dry_run=args.dry_run)
    elif args.cmd == "publish":
        cmd_publish(args.no_push, args.no_backup)
    elif args.cmd == "mark":
        dm.run()


if __name__ == "__main__":
    main()
