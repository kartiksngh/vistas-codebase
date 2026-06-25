"""
merge_amc_specs.py — merge the deterministic + agent-authored AMC specs into ONE verified registry.

Two independent sources proposed a fetch spec per AMC:
  - the deterministic pass (build_amc_specs.py)  -> data/funds/amc_feed_patterns.json
  - the agent workflow (author-verify-amc-specs) -> the workflow .output file (result.specs)
Neither is trusted on its claim. For each AMC we RE-VERIFY every candidate through the real engine
(vistas/funds_portfolio.build_amc, which now has the hardened recency scorer) and keep the BEST by:
   latest as-on month  ->  most schemes  ->  sane coverage (drop a broken >130% parse).
If a candidate fails re-verify (transient network) but was previously verified, we keep it as a
flagged fallback rather than dropping a good feed. WAF/render houses + genuinely feedless AMCs are
recorded honestly (needs_harness / no_feed). Writes the merged amc_feed_patterns.json + a report.
"""
import os, sys, json, re, statistics, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vistas import funds_portfolio as fp

ROOT = os.path.dirname(os.path.abspath(__file__))
DET = os.path.join(ROOT, "data", "funds", "amc_feed_patterns.json")
OUT = DET
REPORT = os.path.join(ROOT, "data", "funds", "_merge_report.json")
AGENT_OUT = sys.argv[1] if len(sys.argv) > 1 else None

_MON = {m.lower(): i for i, m in enumerate(
    ["January","February","March","April","May","June","July","August","September","October","November","December"], 1)}
_MON.update({k[:3]: v for k, v in list(_MON.items())})


def asof_rank(s):
    """Parse a messy as-on string -> (year, month, day) for newest-wins ranking. Missing year is
    assumed to be the current year (these feeds are all recent), so 'May 31' ranks correctly."""
    if not s:
        return (0, 0, 0)
    s = str(s); low = s.lower()
    yr = 0
    y4 = re.findall(r"20\d{2}", s)
    if y4:
        yr = max(int(y) for y in y4 if 2015 <= int(y) <= dt.date.today().year + 1) if \
             any(2015 <= int(y) <= dt.date.today().year + 1 for y in y4) else 0
    mo = 0
    for name, num in _MON.items():
        if re.search(rf"\b{name}\b", low) or name in low:
            mo = max(mo, num)
    # numeric dd/mm/yy or dd-mm-yyyy
    m = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", s)
    if m:
        d2, mth, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mth <= 12:
            mo = mo or mth
        if not yr:
            yr = y2 + 2000 if y2 < 100 else y2
    day = 0
    md = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", low)
    if md and 1 <= int(md.group(1)) <= 31:
        day = int(md.group(1))
    if mo and not yr:
        yr = dt.date.today().year
    return (yr, mo, day)


def verify(key, spec):
    """Run the real engine on a spec; return (n_schemes, median_cov, asof, rank) or (0,..)."""
    if not spec:
        return 0, None, None, (0, 0, 0)
    off = (spec.get("method") or "").lower() in ("waf-blocked", "render-needed")
    try:
        recs = fp.build_amc(key, isin_map={}, registry={key: {**spec, "offline_only": off}}, log=lambda m: None)
    except Exception:
        recs = []
    if not recs:
        return 0, None, None, (0, 0, 0)
    covs = [r["agg"]["coverage"]["pct_sum"] for r in recs]
    asof = recs[0].get("asof")
    return len(recs), round(statistics.median(covs), 1), asof, asof_rank(asof)


def clean(spec):
    """Drop bookkeeping keys so the registry holds only what the engine consumes + light provenance."""
    keep = ("amc", "file_type", "method", "url_pattern", "resolve", "verified_url", "multi",
            "max_files", "landing", "needs_harness")
    return {k: spec[k] for k in keep if k in spec and spec[k] is not None}


def main():
    det = json.load(open(DET, encoding="utf-8")).get("amcs", {})
    agent = {}
    if AGENT_OUT and os.path.exists(AGENT_OUT):
        try:
            res = json.load(open(AGENT_OUT, encoding="utf-8"))
            for s in (res.get("result", res).get("specs", [])):
                if s and s.get("key"):
                    agent[s["key"]] = s
        except Exception as e:
            print("could not load agent specs:", e)
    keys = sorted(set(det) | set(agent))
    final, report = {}, []

    for key in keys:
        a = agent.get(key) or {}
        d = det.get(key) or {}
        cands = []                                            # (source, spec)
        if a.get("ok") and a.get("spec"):
            cands.append(("agent", a["spec"]))
        if d and (d.get("resolve") or d.get("url_pattern") or d.get("verified_url")) and not d.get("needs_harness"):
            cands.append(("det", d))

        scored = []
        for src, spec in cands:
            n, cov, asof, rank = verify(key, spec)
            scored.append({"src": src, "spec": spec, "n": n, "cov": cov, "asof": asof, "rank": rank})

        # valid = parsed >=1 scheme AND coverage not absurd (>130 = a broken/double-counted parse)
        valid = [s for s in scored if s["n"] > 0 and (s["cov"] is None or s["cov"] <= 130)]
        if valid:
            best = max(valid, key=lambda s: (s["rank"], s["n"]))
            entry = clean(best["spec"])
            entry.update({"amc": best["spec"].get("amc") or a.get("amc") or d.get("amc") or key,
                          "n_schemes": best["n"], "median_coverage": best["cov"], "asof": best["asof"],
                          "source": best["src"], "confidence": "high" if best["n"] >= 3 and (best["cov"] or 0) >= 80 else "medium"})
            final[key] = entry
            report.append({"key": key, "ok": True, "src": best["src"], "n": best["n"],
                           "cov": best["cov"], "asof": best["asof"],
                           "alt": [(s["src"], s["n"], s["asof"]) for s in scored if s is not best]})
            print(f"{key:22s} OK  {best['src']:5s} n={best['n']:<4} cov={best['cov']} asof={best['asof']}", flush=True)
            continue

        # nothing re-verified. fall back to an agent spec it CLAIMED to verify (transient net?),
        # else flag for the local harness / mark feedless.
        a_landing = (a.get("spec") or {}).get("landing") or ((a.get("spec") or {}).get("resolve") or {}).get("landing")
        if a.get("ok") and a.get("n_schemes", 0) > 0 and a.get("spec"):
            entry = clean(a["spec"]); entry.update({"amc": a.get("amc") or key, "n_schemes": a["n_schemes"],
                        "asof": a.get("asof"), "source": "agent-claimed", "confidence": "low"})
            final[key] = entry
            report.append({"key": key, "ok": True, "src": "agent-claimed", "n": a.get("n_schemes"), "asof": a.get("asof"), "note": "re-verify failed (transient?), kept claimed spec"})
            print(f"{key:22s} CLAIMED (re-verify failed) n={a.get('n_schemes')} asof={a.get('asof')}", flush=True)
        elif a.get("needs_harness") or d.get("needs_harness"):
            landing = a_landing or d.get("landing") or (d.get("resolve") or {}).get("landing") or ""
            vu = (a.get("spec") or {}).get("verified_url") or d.get("verified_url")
            final[key] = {"amc": a.get("amc") or d.get("amc") or key, "needs_harness": True,
                          "method": (a.get("spec") or d).get("method") or "render-needed",
                          "landing": landing or None, "resolve": {"landing": landing} if landing else None,
                          "verified_url": vu, "n_schemes": 0, "confidence": "low"}
            report.append({"key": key, "ok": False, "src": "harness", "needs_harness": True})
            print(f"{key:22s} HARNESS", flush=True)
        else:
            final[key] = {"amc": a.get("amc") or d.get("amc") or key, "no_feed": True, "n_schemes": 0, "confidence": "low"}
            report.append({"key": key, "ok": False, "src": "no_feed"})
            print(f"{key:22s} NO FEED", flush=True)

    out = {"version": 2, "updated": dt.date.today().isoformat(),
           "note": "AMC monthly-portfolio fetch registry; merged + re-verified (deterministic + agent) by merge_amc_specs.py",
           "amcs": final}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = [r for r in report if r["ok"]]
    harn = [r for r in report if r.get("needs_harness")]
    print("\n========================================================")
    print(f"MERGED: {len(ok)}/{len(report)} AMCs live; {len(harn)} need harness; "
          f"{sum((r.get('n') or 0) for r in ok)} total schemes")
    print(f"wrote {OUT}\nwrote {REPORT}")


if __name__ == "__main__":
    main()
