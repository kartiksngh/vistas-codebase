"""One-off diagnostic: run the funds_portfolio engine for the 23 needs_harness AMCs from KV's
RESIDENTIAL IP (forced online), counting real schemes + coverage per house. Proves which houses
the existing resolver already cracks here vs which need browser/API work. Side effect: warms the
byte-cache (data/funds/portfolio_cache) so a confirmed house is instantly buildable offline.

Run:  python _funds_residential_probe.py            # all needs_harness houses
      python _funds_residential_probe.py uti hsbc   # just these keys
"""
import sys, json, time, statistics, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vistas import funds_portfolio as fp

# skip symbol resolution — we only care about ACQUISITION (scheme count + % coverage), not idmap
fp._resolve_symbols = lambda rec, m: None

reg = fp._registry()
patt = fp._load_patterns()
harness = [k for k, s in patt.items() if s.get("needs_harness")]
keys = sys.argv[1:] or harness
print(f"probing {len(keys)} house(s): {', '.join(keys)}\n", flush=True)

sess = fp._session()
results = []
for k in keys:
    if k not in reg:
        # render-needed/no-feed houses can be absent from _registry() if they lack a fetch path
        s = dict(patt.get(k, {}))
        s.setdefault("amc", k)
        s["offline_only"] = False
        reg[k] = s
    reg[k]["offline_only"] = False          # force NETWORK even for render-needed
    t0 = time.time()
    logs = []
    try:
        recs = fp.build_amc(k, session=sess, isin_map={}, log=lambda x: logs.append(x),
                            offline=False, registry=reg)
    except Exception as e:
        recs = []
        logs.append(f"EXC {e}")
    dt = time.time() - t0
    covs = [r["agg"]["coverage"]["pct_sum"] for r in recs if r.get("agg")]
    asofs = sorted({(r.get("asof") or "?") for r in recs})
    cov_med = round(statistics.median(covs), 1) if covs else None
    line = (f"{k:22s} n={len(recs):3d}  cov_med={cov_med}  asof={asofs[:2]}  "
            f"{dt:4.0f}s  | {logs[-1] if logs else ''}")
    print(line, flush=True)
    results.append({"key": k, "n": len(recs), "cov_med": cov_med, "asof": asofs[:3],
                    "secs": round(dt, 1), "log": logs[-1] if logs else "",
                    "registry_n_claim": patt.get(k, {}).get("n_schemes")})

ok = [r for r in results if r["n"] > 0]
print(f"\n=== {len(ok)}/{len(results)} houses returned >=1 scheme; "
      f"{sum(r['n'] for r in results)} schemes total ===", flush=True)
with open("data/funds/_residential_probe.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=1)
print("wrote data/funds/_residential_probe.json", flush=True)
