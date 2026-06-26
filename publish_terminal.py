#!/usr/bin/env python
"""Refresh + build + validate + publish the Vistas TERMINAL v2 HOSTED site.

Publishes the HYBRID LAZY-LOAD site (small shell + per-symbol JSON fetched on demand)
to a NEW path in the same Pages repo — `terminal/` — so the live v1 Passive deck
(`passive/index.html`) is left completely untouched:
    v1 (unchanged):  https://kartiksngh.github.io/vistas/passive/
    v2 (this):       https://kartiksngh.github.io/vistas/terminal/

Why the site, not the single-file deck: the all-in-one offline deck is ~216 MB, which
exceeds GitHub's 100 MB-per-file limit and would be rejected. The hosted site keeps the
shell at ~20 MB and serves stock/fundamentals data on demand from data/<…>.json — so it
both fits Pages and loads fast. The single-file deck stays for offline/email use.

One run: (1) optional incremental NSE refresh, (2) rebuild the hosted site
(output/terminal_site/), (3) VALIDATE the shell via the Node runtime smoke-test (a
faulty shell is NEVER published), (4) mirror the site into terminal/ and push, (5)
back up off-machine so a local disk crash can't lose anything: the SOURCE (code +
project .md docs) -> the private vistas-codebase git repo, and the LICENSED ARM dump
(arm_repo/, which can't go to GitHub) -> an AES-256-GCM encrypted cloud mirror.

Flags:  --no-fetch    rebuild from current data, no NSE pull
        --no-rebuild  publish the site already on disk (skip the rebuild; fastest)
        --no-push     build + validate only
        --no-backup   skip the off-machine backups in step 5 (source + ARM)
        --email       also build + email the single-file offline deck (needs VISTAS_SMTP_*)
"""
from __future__ import annotations

import os
import sys
import time
import argparse
import datetime
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# reuse the passive publisher's helpers (validate / run / repo paths / remote)
import publish_passive as pp

TERMINAL_DIR = os.path.join(pp.PUB_DIR, "terminal")            # publish target  (…/vistas/terminal)
SITE_DIR = os.path.join(APP_DIR, "output", "terminal_site")    # source artifact (built by deck.save_terminal_site)
SHELL = os.path.join(SITE_DIR, "index.html")
DECK_LATEST = os.path.join(APP_DIR, "output", "Vistas_Terminal_Deck_v2_latest.html")  # single-file (email only)
PAGES_URL = "https://kartiksngh.github.io/vistas/terminal/"

# ----------------------------------------------------------------- single-flight build/publish lock
# A build and a publish both write output/terminal_site/ and _pages/. Two at once (e.g. a Daily
# Refresh + a manual Publish Last Build, or a background rebuild) corrupt each other — that is the
# ONLY way this pipeline "breaks". This lock makes a second run refuse cleanly instead of clobbering
# the first. Held only for the build/validate/publish critical section; auto-released at the end.
LOCK_PATH = os.path.join(APP_DIR, "data", "_refresh", ".build.lock")
_LOCK_MAX_AGE = 90 * 60                                        # a lock older than 90 min = a crashed run


def acquire_lock() -> bool:
    """Take the build/publish lock. Returns False (and explains) if another run holds a fresh lock."""
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    if os.path.exists(LOCK_PATH):
        try:
            age = time.time() - os.path.getmtime(LOCK_PATH)
            who = open(LOCK_PATH, encoding="utf-8").read().strip()
        except Exception:
            age, who = 0, "?"
        if age < _LOCK_MAX_AGE:
            pp.say(f"  ANOTHER BUILD/PUBLISH IS ALREADY RUNNING ({who}; {int(age)}s ago).")
            pp.say("  Refusing to run two at once — they overwrite the same files and corrupt the build.")
            pp.say("  Wait for it to finish. If you are SURE nothing is running, delete this file and retry:")
            pp.say(f"    {LOCK_PATH}")
            return False
        pp.say(f"  (overriding a stale lock, {int(age)}s old — the previous run must have crashed)")
    try:
        with open(LOCK_PATH, "w", encoding="utf-8") as f:
            f.write(f"pid {os.getpid()} @ {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    except Exception:
        pass
    return True


def release_lock():
    try:
        os.remove(LOCK_PATH)
    except Exception:
        pass


def refresh_all():
    """Incremental TR tail pull, then reload. TR-ONLY — PR (price-return) + VAL (PE/PB/DY) are dropped
    from the terminal (the WAF-gated legs never worked and the terminal doesn't use them). Best-effort;
    never fatal."""
    from vistas import fetch, data
    try:
        res = fetch.update()                       # TR snapshot tail (the only NSE leg we pull)
        pp.say(f"  TR: {res.get('message') or res.get('new_asof') or res.get('error')}")
    except Exception as e:
        pp.say(f"  TR refresh failed ({e})")
    data.reload()


def build_site():
    from vistas import deck
    info = deck.save_terminal_site(reason="publish")
    pp.say(f"  built shell {info['shell_mb']} MB · {info.get('n_index_files', 0)} index + "
           f"{info.get('n_world_files', 0)} world + {info['n_stock_files']} stock + "
           f"{info['n_fundamental_files']} fundamental files")
    return info


def _site_summary():
    """(file_count, total_mb) of the on-disk site — for the publish log."""
    n, b = 0, 0
    for root, _dirs, files in os.walk(SITE_DIR):
        for f in files:
            n += 1
            b += os.path.getsize(os.path.join(root, f))
    return n, round(b / 1e6, 1)


def publish_site():
    """Mirror output/terminal_site/ -> <PUB_DIR>/terminal/ and push.

    robocopy /MIR makes terminal/ an exact mirror of the freshly built site (adds new
    per-symbol files, updates changed ones, removes any that no longer exist), so a
    delisted stock's stale JSON can't linger. robocopy exit codes 0-7 are success
    (8+ are real errors)."""
    pp.ensure_repo()
    os.makedirs(TERMINAL_DIR, exist_ok=True)
    n, mb = _site_summary()
    pp.say(f"  mirroring {n} files ({mb} MB) into terminal/ …")
    r = pp.run(["robocopy", SITE_DIR, TERMINAL_DIR, "/MIR", "/NFL", "/NDL", "/NP",
                "/NJH", "/NJS", "/R:1", "/W:1"])
    if r.returncode >= 8:
        pp.say("  MIRROR FAILED:\n" + (r.stdout + r.stderr).strip()[-1500:])
        return False

    pp.run(["git", "add", "-A"], cwd=pp.PUB_DIR)
    msg = "terminal v2 site refresh " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    c = pp.run(["git", "commit", "-m", msg], cwd=pp.PUB_DIR)
    if "nothing to commit" in (c.stdout + c.stderr):
        pp.say("  (site unchanged since last publish — nothing to push)")
        return True
    pp.say("  pushing to GitHub (first publish of the site can take a few minutes)…")
    p = pp.run(["git", "push", "-u", "origin", "main"], cwd=pp.PUB_DIR)
    if p.returncode != 0:
        pp.say("  PUSH FAILED:\n" + (p.stdout + p.stderr).strip()[-1500:])
        return False
    pp.say(f"  published OK -> live in ~1 min at {PAGES_URL}")
    return True


def backup_codebase():
    """Commit + push the SOURCE (code + project .md docs) to the private vistas-codebase
    repo (this folder's own `origin`), so a local disk crash can't lose the source — the
    live site AND its source are both safe after every publish.

    NON-FATAL by design: the site is already live by the time this runs, so a backup
    hiccup (offline, auth) only prints a warning and never blocks publishing. The dev-repo
    .gitignore already excludes everything heavy/licensed (arm_repo, the 130-185 MB stock
    CSVs, _pages/, the gated layers, node_modules), so only code + the .md docs + the small
    seed data + the git-tracked amc_book/ audit trail go up.

    Safety net: a staged file >95 MB would be hard-rejected by GitHub and is almost always
    something that belongs in .gitignore — so we name it and SKIP the backup rather than
    push a doomed commit."""
    if not os.path.isdir(os.path.join(APP_DIR, ".git")):
        pp.say("  (no source git repo in this folder — skipping codebase backup)")
        return True
    pp.run(["git", "add", "-A"], cwd=APP_DIR)
    staged = [s.strip() for s in
              pp.run(["git", "diff", "--cached", "--name-only"], cwd=APP_DIR).stdout.split("\n")
              if s.strip()]
    big = []
    for rel in staged:
        try:
            mb = os.path.getsize(os.path.join(APP_DIR, rel)) / 1e6
            if mb > 95:
                big.append(f"{rel} ({mb:.0f} MB)")
        except OSError:
            pass
    if big:
        pp.say("  CODEBASE BACKUP SKIPPED — these staged files exceed GitHub's 100 MB limit:")
        for b in big:
            pp.say(f"    {b}")
        pp.say("  Add them to .gitignore (they're almost certainly regenerable data), then retry.")
        pp.run(["git", "reset"], cwd=APP_DIR)        # unstage so the working tree is left clean
        return False
    msg = "codebase + docs backup " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    c = pp.run(["git", "commit", "-m", msg], cwd=APP_DIR)
    if "nothing to commit" in (c.stdout + c.stderr):
        pp.say("  (source unchanged since last backup — nothing to push)")
        return True
    p = pp.run(["git", "push", "origin", "HEAD"], cwd=APP_DIR)
    if p.returncode != 0:
        pp.say("  CODEBASE BACKUP PUSH FAILED (the live site IS published; only the source backup didn't go up):")
        pp.say("  " + (p.stdout + p.stderr).strip()[-800:])
        return False
    short = pp.run(["git", "rev-parse", "--short", "HEAD"], cwd=APP_DIR).stdout.strip()
    pp.say(f"  source backed up to vistas-codebase ({short})")
    return True


def backup_arm():
    """Best-effort encrypted, off-machine backup of arm_repo/ (the LICENSED LSEG StarMine ARM
    dump — the one piece that can NEVER go to GitHub). Incremental, so after the first ~1.1 GB
    run each call only re-encrypts the new weekly drop; near-instant when nothing changed.

    NON-FATAL and OPT-IN-by-environment: runs only when a backup target is configured (OneDrive
    signed in, or VISTAS_ARM_BACKUP_DIR set) and arm_repo/ exists locally — otherwise it quietly
    skips. The cloud only ever receives ciphertext (AES-256-GCM). See vistas/arm_backup.py."""
    try:
        from vistas import arm_backup
    except Exception as e:
        pp.say(f"  (ARM backup module unavailable: {e}) — skipping"); return False
    if not arm_backup._target_dir():
        pp.say("  (no ARM backup target — sign in to OneDrive or set VISTAS_ARM_BACKUP_DIR; skipping)")
        return False
    if not os.path.isdir(arm_backup.ARM_DIR):
        pp.say("  (no arm_repo/ on disk — skipping ARM backup)"); return False
    try:
        return arm_backup.backup() == 0
    except Exception as e:
        pp.say(f"  ARM BACKUP FAILED (non-fatal; the live site IS published): {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--no-rebuild", action="store_true", help="publish the site already on disk")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-backup", action="store_true", help="skip the source backup to vistas-codebase")
    ap.add_argument("--email", action="store_true", help="also build + email the single-file deck")
    args = ap.parse_args()

    pp.hr(); pp.say("VISTAS TERMINAL v2 — refresh & publish (hosted lazy-load site)"); pp.hr()
    if not acquire_lock():                          # refuse to clash with another build/publish
        return 1
    try:
        return _run(args)
    finally:
        release_lock()


def _run(args):
    if args.no_rebuild:
        pp.say("[1-2/5] using the site already on disk (--no-rebuild)")
        if not os.path.exists(SHELL):
            pp.say("  ERROR: no site on disk — run once without --no-rebuild first."); return 1
    else:
        if not args.no_fetch:
            pp.say("[1/5] refreshing NSE TR data (incremental tail)…"); refresh_all()
        else:
            pp.say("[1/5] skipping NSE pull (--no-fetch)")
            from vistas import data; data.reload()
        pp.say("[2/5] rebuilding the hosted site…"); build_site()

    pp.say("[3/5] validating the shell…")
    ok, detail = pp.validate(SHELL)
    pp.say("  " + detail.replace("\n", "\n  "))
    if not ok:
        pp.hr(); pp.say("FAULTY SHELL — NOT PUBLISHING (the live link is unchanged)."); pp.hr()
        return 1

    if args.email:
        from vistas import deck, notify
        deck.save_terminal_deck(reason="publish-email")
        e = notify.email_deck(deck_path=DECK_LATEST, subject="Vistas Terminal v2 — latest deck")
        pp.say("  email: " + ("sent to " + e["to"] if e.get("ok") else e.get("error", "failed")))

    if args.no_push:
        pp.hr(); pp.say("Validation passed. (--no-push: not publishing.)"); pp.hr(); return 0

    pp.say("[4/5] publishing terminal/ to GitHub…")
    pushed = publish_site()

    if args.no_backup:
        pp.say("[5/5] skipping off-machine backups (--no-backup)")
    else:
        pp.say("[5/5] backing up off-machine (source -> vistas-codebase; licensed ARM -> encrypted cloud)…")
        backup_codebase()
        backup_arm()

    pp.hr(); pp.say("DONE." if pushed else "Built & validated, NOT published (see above)."); pp.hr()
    return 0 if pushed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
