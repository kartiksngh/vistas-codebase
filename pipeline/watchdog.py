"""
watchdog.py — the DETERMINISTIC backstop for the Vistas daily-refresh agent.

The agent (headless Claude) is adaptive but can silently die — auth lapses, the box was off, a crash.
This watchdog is the dumb, reliable opposite: a tiny no-network freshness check that runs INDEPENDENTLY
(its own schedule, ~2h after the agent) and shouts if the terminal's prices went stale or the agent
stopped running. It can't die the way the agent can, so a silently-dead agent can't go unnoticed.

It does NOT fix anything (that's the agent's job) — it only DETECTS + ALERTS:
  * writes data/_refresh/watchdog_status.json   (always — the machine-readable state)
  * writes data/_refresh/WATCHDOG_ALERT.txt     (ONLY when something's wrong — the loud human flag)
  * best-effort Windows pop-up (msg) so a logged-in KV sees it immediately
When healthy it deletes any stale ALERT file, so the presence of WATCHDOG_ALERT.txt always means "act now".

Run: python pipeline/watchdog.py        (schedule it daily ~10:30pm, after the 8pm agent)
"""
import os, sys, json, glob, re, subprocess
import datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
REFRESH = os.path.join(DATA, "_refresh")
STATUS = os.path.join(REFRESH, "watchdog_status.json")
ALERT = os.path.join(REFRESH, "WATCHDOG_ALERT.txt")
AGENT_LOG = os.path.join(REFRESH, "agent_run.log")

_STALE_DAYS = 3          # tolerate a weekend + one holiday before crying "stale"
_AGENT_MAX_HRS = 26      # the agent runs daily; >26h since its last run = it stopped


def _newest_asof(measure="TR"):
    """Latest date covered by the newest 'Indices Data <measure> till <date>.csv' (from the filename)."""
    best = None
    for p in glob.glob(os.path.join(DATA, f"Indices Data {measure} till *.csv")):
        m = re.search(r"till (.+)\.csv$", os.path.basename(p))
        if not m:
            continue
        try:
            d = dt.datetime.strptime(m.group(1).strip(), "%b %d, %Y").date()
        except ValueError:
            try:
                d = dt.datetime.strptime(m.group(1).strip(), "%b %d %Y").date()
            except ValueError:
                continue
        if best is None or d > best:
            best = d
    return best


def _agent_last_run_hours():
    """Hours since the agent last started (mtime of its run log). None if it has never run."""
    if not os.path.exists(AGENT_LOG):
        return None
    age = dt.datetime.now() - dt.datetime.fromtimestamp(os.path.getmtime(AGENT_LOG))
    return age.total_seconds() / 3600.0


def main():
    today = dt.date.today()
    is_weekday = today.weekday() < 5                      # Mon-Fri (no holiday calendar; threshold absorbs holidays)
    asof = _newest_asof("TR")
    stale_days = (today - asof).days if asof else 9999
    agent_hrs = _agent_last_run_hours()

    problems = []
    if is_weekday and stale_days > _STALE_DAYS:
        problems.append(f"PRICE DATA STALE: NSE-TR indices are as of {asof} ({stale_days} days behind today) "
                        f"— the daily refresh did not land fresh prices.")
    if agent_hrs is not None and agent_hrs > _AGENT_MAX_HRS:
        problems.append(f"AGENT SILENT: the refresh agent last ran {agent_hrs:.0f}h ago (>{_AGENT_MAX_HRS}h) "
                        f"— it has stopped running (auto-task off, box was asleep, auth lapsed, or it crashed).")
    agent_note = ("never (not scheduled yet)" if agent_hrs is None else f"{agent_hrs:.0f}h ago")

    status = {
        "checked_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "today": today.isoformat(), "is_weekday": is_weekday,
        "price_asof": asof.isoformat() if asof else None, "price_stale_days": stale_days,
        "agent_last_run": agent_note, "ok": not problems, "problems": problems,
    }
    os.makedirs(REFRESH, exist_ok=True)
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=1)

    if problems:
        msg = ("VISTAS WATCHDOG ALERT — " + dt.datetime.now().strftime("%Y-%m-%d %H:%M") + "\n\n"
               + "\n".join("  * " + p for p in problems)
               + "\n\nWhat to do: run the refresh from a clean network if NSE is blocking "
                 "(mobile hotspot), or re-trigger:  pipeline\\Daily Refresh Agent.bat  /  "
                 "python -m vistas.pipeline . Diary: data\\_refresh\\agent_journal.md")
        with open(ALERT, "w", encoding="utf-8") as f:
            f.write(msg + "\n")
        # best-effort visible pop-up to a logged-in user (never fatal if it isn't available)
        try:
            subprocess.run(["msg", "*", "/TIME:600",
                            "Vistas watchdog: terminal data is stale / the refresh agent is not running. "
                            "See data\\_refresh\\WATCHDOG_ALERT.txt"], timeout=10)
        except Exception:
            pass
        print("WATCHDOG: ALERT\n" + msg)
        return 1
    else:
        try:
            if os.path.exists(ALERT):
                os.remove(ALERT)                          # healthy again -> clear any old alarm
        except OSError:
            pass
        print(f"WATCHDOG: OK — prices asof {asof} ({stale_days}d), agent {agent_note}.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
