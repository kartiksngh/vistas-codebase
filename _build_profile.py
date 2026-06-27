"""_build_profile.py — measure WHERE the terminal build spends time, so #98/#99 optimize the real
bottleneck (not an assumed one). Read-only on project data; writes throwaway JSON to a temp dir.

Splits the per-stock quant cost into: market-behaviour compute (DAILY block) vs the rest of compute
(slow blocks) vs JSON serialize+disk write. Also times the one-time context build and a sample of the
fundamentals stage (the QUARTERLY-cadence cost #99 would skip on daily builds).

Run:  python -u _build_profile.py [N_SAMPLE]
"""
import os, sys, json, time, tempfile, random

random.seed(7)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 150

from vistas import stock_intel as si

def t():
    return time.perf_counter()

print(f"[profile] sampling N={N} stocks\n")

# ---- one-time: build the shared context (price panel, turnover, industry, corpactions) ----
t0 = t()
ctx = si.build_context()
ctx_s = t() - t0
sp = ctx.get("prices")
cols = list(sp.columns) if sp is not None else []
print(f"[1] build_context(): {ctx_s:7.2f}s  (one-time; price panel {None if sp is None else sp.shape})")

# pick N symbols that actually have price history
pool = [c for c in cols if sp[c].notna().sum() >= 60]
random.shuffle(pool)
sample = pool[:N]

# ---- per-stock: market-behaviour (DAILY block) ----
mb_ms = []
for sym in sample:
    a = t(); _ = si._market_behaviour(sym, ctx); mb_ms.append((t() - a) * 1e3)

# ---- fundamentals stage cost (QUARTERLY) + full compute + serialize, on the subset with a bundle ----
from vistas import screener, fundamentals
fa_ms, full_ms, ser_ms = [], [], []
tmp = tempfile.mkdtemp(prefix="vbuildprof_")
n_bundle = 0
for sym in sample:
    try:
        a = t(); b = screener.load(sym); b["analytics"] = fundamentals.compute(b); fa_ms.append((t() - a) * 1e3)
    except Exception:
        continue
    n_bundle += 1
    fa = b.get("analytics")
    a = t(); q = si.compute(sym, ctx, fund_analytics=fa, bundle=b); full_ms.append((t() - a) * 1e3)
    a = t()
    with open(os.path.join(tmp, si._safe_name(sym) + ".json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(q, allow_nan=False, separators=(",", ":")))
    ser_ms.append((t() - a) * 1e3)

def stat(xs):
    if not xs:
        return (0.0, 0.0)
    s = sorted(xs)
    return (sum(xs) / len(xs), s[len(s) // 2])

UNIV = 2875
for label, xs in [("market-behaviour (DAILY block)", mb_ms),
                  ("fundamentals load+compute (QUARTERLY)", fa_ms),
                  ("full quant compute (all blocks)", full_ms),
                  ("JSON serialize + disk write", ser_ms)]:
    mean, med = stat(xs)
    print(f"[per-stock] {label:42s} mean {mean:7.2f} ms  median {med:7.2f} ms  "
          f"-> x{UNIV} = {mean * UNIV / 1000:6.1f}s")

# the quant stage proper = full compute + serialize (NOT fundamentals — that's a separate stage)
qmean = (stat(full_ms)[0] + stat(ser_ms)[0])
mbmean = stat(mb_ms)[0]
print(f"\n[extrapolated to {UNIV} stocks, single-thread]")
print(f"  quant stage (full compute + serialize) : {qmean * UNIV / 1000:6.1f}s")
print(f"    of which DAILY market-behaviour       : {mbmean * UNIV / 1000:6.1f}s  "
      f"({100*mbmean/max(qmean,1e-9):.0f}% of the quant per-stock cost)")
print(f"    of which slow blocks + serialize      : {(qmean-mbmean) * UNIV / 1000:6.1f}s")
print(f"  fundamentals stage (QUARTERLY)          : {stat(fa_ms)[0] * UNIV / 1000:6.1f}s  "
      f"(skippable on daily builds per #99)")
print(f"\n  n_with_bundle={n_bundle}/{N}")
print("\n[read] DAILY parallel job (#98) = market-behaviour x2875; the rest is slow-cadence (#99).")
