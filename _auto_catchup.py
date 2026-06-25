"""
_auto_catchup.py - wait out NSE's rate-limit cooldown GENTLY, then pull fresh TR prices and publish.

KV's Airtel residential IP got soft-blocked by a retry-storm; it self-releases in a few hours. This
script probes ONCE every 20 min (trivial volume - won't re-trip the block), and the moment NSE answers
it runs the real pull (curl_cffi Chrome fingerprint) -> rebuild -> publish. 6-hour cap; the 8pm scheduled
job is the backstop. Run in the background; it logs to data/_refresh/auto_catchup.log.
"""
import os, sys, time, datetime as dt, subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from vistas import fetch  # noqa: E402

LOG = os.path.join(ROOT, "data", "_refresh", "auto_catchup.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)


def log(m):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {m}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:                 # Windows cp1252 console can't encode some glyphs
        print(line.encode("ascii", "replace").decode(), flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


PROBE_EVERY = 20 * 60          # 20 min between gentle probes
DEADLINE = time.time() + 6 * 3600
log(f"=== auto-catchup START (curl_cffi={fetch._cffi is not None}, impersonate={fetch._IMPERSONATE}) ===")
log("waiting for NSE to release the cooldown; gentle single-shot probe every 20 min")

reachable = False
probe_n = 0
while time.time() < DEADLINE:
    probe_n += 1
    try:
        s = fetch._session()
        end = dt.date.today()
        ok = fetch._reachable(s, "NIFTY 50", end - dt.timedelta(days=10), end, timeout=10, tries=1)
    except Exception as e:
        ok = False
        log(f"probe {probe_n}: error {type(e).__name__}")
    log(f"probe {probe_n}: NSE reachable = {ok}")
    if ok:
        reachable = True
        break
    time.sleep(PROBE_EVERY)

if not reachable:
    log("gave up after 6h - NSE still cold. The 8pm scheduled job will try again. EXIT.")
    sys.exit(0)

log("NSE is answering - running the real TR pull now (gentle, fingerprinted)...")
res = fetch.update(dry=False)
log(f"update(): ok={res.get('ok')} updated={res.get('updated')} rows_added={res.get('rows_added')} "
    f"new_asof={res.get('new_asof')} n_live={res.get('n_live')} n_failed={res.get('n_failed')} "
    f"msg={res.get('message') or res.get('error')}")

if not res.get("updated"):
    log("no fresh rows written (no new trading days, or blocked mid-pull) - not republishing. EXIT.")
    sys.exit(0)

log(f"fresh prices written through {res.get('new_asof')} -> rebuilding + publishing the terminal...")
r = subprocess.run([sys.executable, "publish_terminal.py", "--no-fetch"],
                   capture_output=True, text=True, cwd=ROOT)
tail = "\n".join((r.stdout or "").splitlines()[-10:])
log("publish_terminal.py --no-fetch output tail:\n" + tail)
ok_pub = "published OK" in (r.stdout or "") or "DONE" in (r.stdout or "")
log(f"=== auto-catchup DONE: prices through {res.get('new_asof')}; published={ok_pub} ===")
