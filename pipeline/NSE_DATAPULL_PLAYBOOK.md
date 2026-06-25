# NSE Data-Pull Playbook — the antifragile framework

**Purpose.** NSE / niftyindices is a hostile, flaky source (anti-bot WAF, cookie handshakes,
silent throttling). Every failure we hit must make this framework *stronger*, not just get
patched and forgotten. This doc is the **single source of truth** for how we pull NSE data
robustly, the **failure modes we've actually seen**, the **diagnostics** that tell them apart,
and a **dated experiment log**. Read this BEFORE debugging a "the pull is stuck" report.

Code: `vistas/fetch.py` (index TR/PR/VAL), `vistas/bhav_*.py` (bhavcopy/mcap). Engine: `vistas/pipeline.py`.

---

## 0. The first law: OBSERVE before you conclude (don't assume IP-block)

When the pull "hangs", the instinct is "NSE is blocking our datacenter IP". **Verify that first
with a 3-request timed probe** — it takes 10 seconds and has repeatedly proven the instinct wrong:

```python
import requests, time
H={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
s=requests.Session(); s.headers.update(H)
for name,fn in [
    ('page', lambda: s.get('https://www.niftyindices.com/reports/historical-data', timeout=20)),
    ('map',  lambda: s.get('https://iislliveblob.niftyindices.com/assets/json/IndexMapping.json', timeout=20)),
    ('post', lambda: s.post('https://www.niftyindices.com/Backpage.aspx/getHistoricaldatatabletoString',
                            data=str({'cinfo':"{'name':'NIFTY 50','startDate':'01-Jun-2026','endDate':'19-Jun-2026','indexName':'NIFTY 50'}"}),
                            headers={**H,'Content-Type':'application/json; charset=UTF-8',
                                     'Referer':'https://www.niftyindices.com/reports/historical-data',
                                     'Origin':'https://www.niftyindices.com','X-Requested-With':'XMLHttpRequest'}, timeout=30)),
]:
    t=time.time()
    try: r=fn(); print(f'{name:6} OK  {time.time()-t:.1f}s status={r.status_code} bytes={len(r.content)}')
    except Exception as e: print(f'{name:6} FAIL {time.time()-t:.1f}s {type(e).__name__}: {e}')
```

**Decision tree from the probe:**
- All 3 return 200 in <1s  → **NOT blocked.** A "hang" is OUR pacing (see F1) or a downstream bug.
- Connection refused / timeout on `page` → genuine network/IP block → degrade gracefully, run from a residential IP.
- 200 on `page`+`map` but 403/418/empty on `post` → **WAF on the data endpoint** → see F3 (bare host + matching Referer/Origin).

---

## 1. Failure modes seen (with the fix and the diagnostic that fingerprints it)

| # | Symptom | Root cause | Fix | Fingerprint |
|---|---------|-----------|-----|-------------|
| **F1** | Pull "hangs" 5–50 min, no error, stuck on `[1/4]` | Default **`slow` pacing profile** sleeps **4–9 s per index** → ~130 idx × 3 measures × ~8 s ≈ **50 min of pure `sleep()`**, while NSE answers in 0.1 s | `fetch._profile()` default `slow`→**`normal`** (0.7–1.4 s/idx ≈ 6–7 min). `VISTAS_FETCH_PROFILE=slow` for max stealth | Probe is instant but the full run takes forever; CPU near 0, process alive, sleeping |
| **F2** | Mid-run the session goes dead (every request 401/empty) | Cookie/session expired or identity flagged | `_rehandshake(s)` — rotate UA identity + re-seed cookies on the SAME session; refresh every N requests | Early requests OK, later ones uniformly fail |
| **F3** | PR / PE-PB endpoints 403 while TR works | WAF wants a self-consistent same-origin request on the **bare** host | `prefer_bare=True` + `_headers_for_url` (Referer/Origin match the host POSTed to) + patient 75 s timeout | Only certain measures fail; TR table fine |
| **F4** | Endpoint returns garbage / parse error | `cinfo` payload must be **single-quoted** JSON-in-a-string (NSE's quirk), strict-CSV embedded commas | Build cinfo with single quotes; parse defensively | 200 but body unparseable |
| **F5** | Whole pull fails in a cron/headless box | Datacenter IP genuinely blocked **in that environment** (not ours today) | Graceful degrade: `ok:False`, keep serving the last snapshot; never crash the pipeline | `page` probe times out/refused |
| **F6** ★ | Pull dead-stops at 0% CPU after a few requests; each later call hangs to its 30s timeout | **Keep-alive socket poisoning** — NSE's WAF silently RESETS long-lived sockets; `requests` reuses the dead pooled socket → blocks. THE real "TR pull hangs" cause | `Connection: close` in the session headers (fresh socket per request, immune). A 25-request burst with `close` is flawless; with `keep-alive` it stalls | Single requests fine, the LOOP hangs; process alive at 0% CPU |
| **F7** | `_session()` eats the full 30s every run | The **HTML-page cookie handshake** (`GET /reports/historical-data`) intermittently `ReadTimeout`s — and is **unnecessary** (the Backpage data endpoints serve fine with **0 cookies**) | Short handshake timeout (8s) so it never stalls; never block on it | `_session` ~30s but the data endpoints respond in 0.1s |

## 2. Antifragile design principles (already in the engine; keep them)

1. **Timeouts on EVERY request** (30 s TR, 75 s PR/VAL). A missing timeout is the only thing that
   truly hangs forever — F1 was pacing, not a missing timeout, but never remove these.
2. **Graceful degradation is the contract.** A feed failing returns `ok:False` and the pipeline
   keeps the prior snapshot + flags the feed degraded. **Nothing but a faulty *shell* blocks publish.**
3. **Profiles by environment, not hardcoded.** `slow` (paranoid) / `normal` (default, polite ~1 req/s)
   / add `fast` only with evidence the IP is safe. The probe tells you which is safe TODAY.
4. **Retry + backoff + session refresh**, randomized index order, periodic breathers, daily cap.
5. **Single-flight lock** (`data/_refresh/.build.lock`, 90-min staleness) so two pulls never collide.
6. **Diagnose with the probe first** (§0). Most "NSE is down" reports are our own pacing or a stale lock.

## 3. Operational checklist when "the pull is stuck"

1. Run the §0 probe. <1 s 200s ⇒ it's us, not NSE.
2. `ls data/_refresh/.build.lock` — stale lock from a killed run? remove it.
3. Check the profile: `echo $VISTAS_FETCH_PROFILE` / the `_profile()` default. On `slow`? that's F1.
4. Is a python `publish_terminal`/`pipeline` already running and just slow-sleeping? (CPU≈0, alive) → F1.
5. Only after the above point to NSE itself.

---

## 4. Experiment log (append-only — every failure + learning)

- **2026-06-24** — Daily pipeline "hung" twice on the incremental NSE pull (stuck at `[1/4]` >5 min,
  killed). **Hypothesis:** datacenter IP blocked by NSE. **Experiment:** §0 timed probe from this exact
  runtime. **Result:** page 200/0.3 s, map 200/0.2 s, POST 200/0.1 s — **NSE fully reachable, fast, NOT
  blocked.** **Real root cause:** the default `slow` profile (4–9 s/index) → a full pull is ~50 min of
  `sleep()`. **Fix:** default profile `slow`→`normal` in `fetch._profile()`. **Learning:** a "hang" with
  a process that's alive but using ~0 CPU is *sleeping*, not blocked — always probe before blaming the
  source. This is the F1 pattern; codified above.
- **2026-06-24 (same day, deeper)** — the pull STILL hung after the F1 pacing fix, even TR-only. Took it
  apart step by step: (1) single TR request → 0.1s; (2) **25-request burst → all 0.3s, 0 fails** (so NSE
  does NOT rate-limit this IP); (3) the *module's* loop hung at 0% CPU. The only difference vs the working
  burst: the module set **`Connection: keep-alive`**. **Root cause = F6 keep-alive socket poisoning** —
  NSE's WAF resets the long-lived socket, `requests` reuses the dead one, every later call blocks to the
  30s timeout. Fix: `Connection: close`. THEN found `_session()` still ate 30s on the **HTML handshake**
  (F7) — which returns **0 cookies and isn't needed** (data endpoints work cookie-less); fix: 8s timeout.
  Result: full TR loop 8/8 indices in **4.0s**. **Correction to the earlier same-day note:** the `slow`
  profile was *a* slowness but NOT the hang — the hang was F6+F7. **Meta-learning: don't stop at the first
  plausible cause; the burst test (isolate volume) + step-isolation (session vs loop vs endpoint) is what
  separated three stacked causes (pacing, keep-alive, handshake) that all looked like "NSE is slow".**
  Also (KV): dropped PR/VAL from the terminal entirely (never worked, unused) — pulls are TR-only now.
- **2026-06-24 (evening) — "ran the update 10×, prices still stuck at Jun 17".** *Diagnosis chain:* (1)
  `update()` canary `NIFTY 50` → ReadTimeout; (2) page+POST to www.niftyindices.com → ReadTimeout on BOTH
  hosts, 3/3 retries, even at a patient **60s** timeout (TCP connects, server never replies = silent WAF
  drop, not slowness); (3) **curl_cffi Chrome impersonation ALSO timed out** → it's an **IP-level** block,
  not a TLS-fingerprint block *at this moment*; (4) **the CDN `iislliveblob` still answers** (different
  infra) — so niftyindices www is selectively dropping us. (5) **`ipinfo` → 182.70.119.104 = AS24560 Bharti
  Airtel Telemedia, `abts-mum-static-…airtelbroadband.in` = a STATIC RESIDENTIAL/business broadband IP, NOT
  a datacenter.** **Root cause: a temporary rate-limit/soft-block from the ~10 rapid retries + debugging
  probes machine-gunning NSE in a short window** — a residential IP that suddenly bursts gets parked for a
  few hours, then released. **NOT a permanent block.** *Cures (shipped):* (a) **switch the HTTP client to
  curl_cffi with a real Chrome TLS/HTTP2 fingerprint** (`_session()` uses `curl_cffi.requests.Session(
  impersonate='chrome124')`, falls back to `requests`) — the durable evasion, since NSE weights JA3 heavily;
  (b) **canary early-abort** in `update()` — probe one liquid index first; if it times out twice, return
  `unreachable:True` with a plain-English reason in ~30-40s instead of grinding 130 idx × timeouts (~87 min)
  and silently writing nothing (THE reason "10 runs changed nothing"); (c) **dead-column prune** (skip
  indices whose own data ended >30d before the snapshot's last day); (d) STOP retrying — each retry resets
  the cooldown. **META: don't VPN a residential IP.** A consumer VPN exits via a DATACENTER IP, which NSE
  blocks *harder* — KV's real Airtel line is the GOOD path. The fix is to look less like a bot (browser
  fingerprint + gentle once-daily + no retry-storm), not to flee the IP. **LEARNING (new failure mode F8):
  residential IP soft-blocked by burst/retry-storm — confirm IP is residential via ipinfo BEFORE assuming a
  datacenter block; cure = curl_cffi fingerprint + fast-fail + cooldown, never a datacenter VPN.**
  *CONFIRMED SAME DAY (14:15):* once the cooldown lapsed, the **curl_cffi pull fetched ALL 131 indices,
  0 failures, in ~90s**, filling Jun 18→23 → snapshot now "till Jun 23, 2026". The fingerprint cure works;
  the only thing that had to pass was NSE's own cooldown clock. (Lone gotcha: the catch-up script crashed
  on a `print('…→…')` UnicodeEncodeError — Windows cp1252 console — AFTER the CSV was safely written; the
  data was fine, just the logging. Fixed: ascii-safe log(). Lesson: keep stdout ASCII on Windows cron.)
