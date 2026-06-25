#!/usr/bin/env python
"""Refresh the Vistas passive offline deck and publish it to GitHub Pages.

Run via "Refresh Vistas Passive.bat" (double-click). One run:
  1. pulls the latest NSE data (best-effort; skip with --no-fetch),
  2. rebuilds the self-contained offline deck,
  3. VALIDATES it (Node runtime render smoke-test; structural fallback if Node is
     absent). **If the new deck is faulty it is NOT published** — the live link
     keeps showing the last good deck, and the failure is printed for debugging,
  4. on success, copies it into the `vistas` repo (passive/index.html), commits,
     and pushes -> GitHub Pages updates the shareable link in ~1 minute.

Flags:  --no-fetch  (rebuild from current data, no NSE pull)
        --no-push   (build + validate only; don't touch the repo)

Live link (once the repo exists + Pages is enabled):
    https://kartiksngh.github.io/vistas/passive/
"""
from __future__ import annotations

import os
import sys
import shutil
import argparse
import subprocess
import datetime
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# GitHub-Pages publish repo — a SEPARATE git repo from this dev app (its own .git +
# remote), holding passive/index.html + terminal/ (the live site). It now lives INSIDE
# the Vistas folder as `_pages/` (git-ignored, so the dev repo never tracks it), so
# everything Vistas-related stays self-contained. Override with the VISTAS_PUBLISH_DIR
# env var if you keep the clone elsewhere. See README "Publish to GitHub Pages".
DEFAULT_PUB_DIR = os.path.join(APP_DIR, "_pages")
PUB_DIR = os.path.abspath(os.environ.get("VISTAS_PUBLISH_DIR", DEFAULT_PUB_DIR))
PASSIVE_DIR = os.path.join(PUB_DIR, "passive")
DECK_LATEST = os.path.join(APP_DIR, "output", "Vistas_Passive_Deck_latest.html")
TESTER = os.path.join(APP_DIR, "_deck_runtime_test.js")
REMOTE = os.environ.get("VISTAS_REMOTE", "git@github.com:kartiksngh/vistas.git")
PAGES_URL = "https://kartiksngh.github.io/vistas/passive/"

sys.path.insert(0, APP_DIR)


def hr():
    print("-" * 72, flush=True)


def say(m=""):
    print(m, flush=True)


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


# --------------------------------------------------------------------- steps
def refresh_data():
    """Best-effort NSE pull. Never fatal — locally it normally succeeds; if the
    API is unreachable we just rebuild from the data already on disk."""
    from vistas import fetch, data
    try:
        res = fetch.update()
        if res.get("ok") and res.get("updated"):
            say(f"  data refreshed -> {res.get('new_asof')} (+{res.get('rows_added')} rows)")
        else:
            say(f"  no new data ({res.get('message') or res.get('error') or 'already current'})")
    except Exception as e:
        say(f"  WARNING: data refresh failed ({e}); rebuilding from existing data")
    data.reload()


def build_deck():
    from vistas import deck
    info = deck.save_deck(reason="publish")
    say(f"  built {info['file']} ({info['size_mb']} MB), data as of {info['asof']}")
    return info


def validate(path):
    """Return (ok, detail). Prefers the Node runtime render smoke-test (proves
    every panel actually draws with no thrown errors); falls back to structural
    checks if Node is unavailable."""
    if not os.path.exists(path):
        return False, "deck file was not created"
    sz = os.path.getsize(path)
    if sz < 3_000_000:
        return False, f"deck suspiciously small ({sz:,} bytes) — likely truncated/empty"

    node = shutil.which("node")
    if node and os.path.exists(TESTER):
        r = run([node, TESTER, path], cwd=APP_DIR)
        out = (r.stdout + r.stderr).strip()
        if r.returncode == 0 and "PASS" in out:
            return True, "Node runtime smoke-test PASSED (all 9 panels render, 0 errors; re-selection OK)"
        return False, "Node runtime smoke-test FAILED:\n" + out[-2500:]

    # structural fallback (Node not installed)
    html = open(path, encoding="utf-8").read()
    need = ["window.VISTAS_DATA", "window.VISTAS_CATALOG", "VistasAnalytics",
            'id="plot-gp"', 'id="plot-corrmat"', 'id="distret"']
    missing = [m for m in need if m not in html]
    if missing:
        return False, "structural check failed; missing: " + ", ".join(missing)
    return True, f"structural check passed ({sz:,} bytes; Node not found, full render check skipped)"


def ensure_repo():
    """Make sure the local publish repo exists with the right remote (idempotent)."""
    os.makedirs(PASSIVE_DIR, exist_ok=True)
    if not os.path.isdir(os.path.join(PUB_DIR, ".git")):
        run(["git", "init"], cwd=PUB_DIR)
        run(["git", "branch", "-M", "main"], cwd=PUB_DIR)
    # ensure the SSH remote is set
    have = run(["git", "remote"], cwd=PUB_DIR).stdout.split()
    if "origin" not in have:
        run(["git", "remote", "add", "origin", REMOTE], cwd=PUB_DIR)


def publish():
    ensure_repo()
    shutil.copyfile(DECK_LATEST, os.path.join(PASSIVE_DIR, "index.html"))
    run(["git", "add", "-A"], cwd=PUB_DIR)
    msg = "deck refresh " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    c = run(["git", "commit", "-m", msg], cwd=PUB_DIR)
    if "nothing to commit" in (c.stdout + c.stderr):
        say("  (deck unchanged since last publish — nothing to push)")
        return True
    p = run(["git", "push", "-u", "origin", "main"], cwd=PUB_DIR)
    if p.returncode != 0:
        err = (p.stdout + p.stderr)
        el = err.lower()
        if "not found" in el or "repository not found" in el:
            say("  PUSH FAILED — the GitHub repo doesn't exist yet.")
            say("  ONE-TIME: create an EMPTY public repo named 'vistas' at https://github.com/new")
            say("  (no README/gitignore/license), then double-click this again.")
        elif "permission denied" in el or "publickey" in el or "could not read from remote" in el:
            say("  PUSH FAILED — git couldn't authenticate to GitHub.")
            say("  Register an SSH key with your GitHub account, OR publish over HTTPS by")
            say("  setting the env var  VISTAS_REMOTE=https://github.com/<you>/vistas.git")
        else:
            say("  PUSH FAILED:\n" + err.strip()[-1500:])
        return False
    say(f"  published OK -> live in ~1 min at {PAGES_URL}")
    return True


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="rebuild from current data, skip the NSE pull")
    ap.add_argument("--no-push", action="store_true", help="build + validate only, don't publish")
    args = ap.parse_args()

    hr(); say("VISTAS PASSIVE — refresh & publish"); hr()

    if not args.no_fetch:
        say("[1/4] refreshing NSE data…"); refresh_data()
    else:
        say("[1/4] skipping NSE pull (--no-fetch)")
        from vistas import data; data.reload()

    say("[2/4] rebuilding offline deck…"); build_deck()

    say("[3/4] validating the new deck…")
    ok, detail = validate(DECK_LATEST)
    say("  " + detail.replace("\n", "\n  "))
    if not ok:
        hr()
        say("FAULTY DECK — NOT PUBLISHING.")
        say("The live link on GitHub is unchanged (it still shows the last good deck).")
        say(f"The faulty build is in: {os.path.join(APP_DIR, 'output')} (timestamped) — for debugging.")
        hr()
        return 1

    if args.no_push:
        hr(); say("Validation passed. (--no-push: not publishing.)"); hr(); return 0

    say("[4/4] publishing to GitHub…")
    pushed = publish()
    hr()
    say("DONE." if pushed else "Built & validated, but NOT published (see message above).")
    hr()
    return 0 if pushed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
