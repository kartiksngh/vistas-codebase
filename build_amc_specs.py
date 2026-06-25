"""
build_amc_specs.py — author + VERIFY the all-AMC funds registry, deterministically (no LLM).

Reads the one-time discovery (data/funds/_discovery_raw.json: each AMC's real download URL,
landing page, method) and, for each AMC, tries a few fetch STRATEGIES through the REAL engine
(vistas/funds_portfolio.build_amc) and keeps the one that actually parses the most schemes. The
gate is ground truth — a workbook that downloads + parses — not anyone's claim.

Strategies tried, cheapest first (network is bounded: per-scheme 'multi' is only tried when the
combined strategies parse < 3 schemes, so combined houses don't needlessly fetch 50 files):
  1. curated url_pattern   (the handful of clean deterministic templates I verified by hand)
  2. resolve (landing scrape, newest link)  +  verified_url fallback     -> COMBINED workbook
  3. verified_url only
  4. resolve (all links) + multi             -> PER-SCHEME house (one file per scheme)

Writes data/funds/amc_feed_patterns.json (the registry the engine reads) + a human report
data/funds/_spec_build_report.json. WAF/render houses are recorded with needs_harness=true for
the local puppeteer harness (discover_amc_feeds.js). Re-run anytime discovery is refreshed.
"""
import os, sys, json, statistics, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vistas import funds_portfolio as fp

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "data", "funds", "_discovery_raw.json")
OUT = os.path.join(ROOT, "data", "funds", "amc_feed_patterns.json")
REPORT = os.path.join(ROOT, "data", "funds", "_spec_build_report.json")

# The few clean, hand-verified deterministic templates (combined workbooks). Everything else is
# driven by the generic landing-scrape resolver + the discovered direct URL as fallback.
CURATED = {
    "groww": {"file_type": "xlsx", "method": "cdn",
              "url_pattern": "https://assets-netstorage.growwmf.in/compliance_docs/Statutory Disclosure/Portfolio/{FYS} -{FYE}/Monthly Portfolio- {Month} {DD}, {YYYY}.xlsx"},
    "nippon": {"file_type": "xls", "method": "cdn",
               "url_pattern": "https://mf.nipponindiaim.com/InvestorServices/FactsheetsDocuments/NIMF-MONTHLY-PORTFOLIO-{DD}-{Mon}-{YY}.xls"},
    "icici-prudential": {"file_type": "zip", "method": "cdn",
                         "url_pattern": "https://www.icicipruamc.com/blob/downloads/Files/Monthly%20Portfolio%20Disclosures/{YYYY}/{Mon}/Monthly-Portfolio-Disclosure-{Month}-{YYYY}.zip"},
    "ppfas": {"file_type": "xls", "method": "cdn",
              "url_pattern": "https://amc.ppfas.com/downloads/portfolio-disclosure/{YYYY}/PPFAS_Monthly_Portfolio_Report_{Month}_{DD}_{YYYY}.xls"},
    "il-fs-infra": {"file_type": "xlsx", "method": "cdn",
                    "url_pattern": "https://www.ilfsinfrafund.com/otherfile/ILFS_Portfolio_TransactionReports_{Month}_{YYYY}.xlsx"},
}


def _verify(key, spec, method):
    """Run the real engine with this spec and return (n_schemes, median_coverage, asof) or (0,..)."""
    off = method in ("waf-blocked", "render-needed")
    try:
        recs = fp.build_amc(key, isin_map={}, registry={key: {**spec, "offline_only": off}},
                            log=lambda m: None)
    except Exception:
        recs = []
    if not recs:
        return 0, None, None
    covs = [r["agg"]["coverage"]["pct_sum"] for r in recs]
    return len(recs), round(statistics.median(covs), 1), recs[0].get("asof")


def main():
    raw = json.load(open(RAW, encoding="utf-8"))
    amcs, report = {}, []
    for key, d in raw.items():
        amc = d["amc"]; method = (d.get("method") or "").lower()
        landing = d.get("landing") or ""
        verified = d.get("direct_url") or ""
        ft = d.get("file_type") or "xlsx"
        base = {"amc": amc, "file_type": ft, "method": method, "landing": landing}

        # WAF / render-needed: datacenter can't fetch — record for the local harness, don't probe.
        if method in ("waf-blocked", "render-needed"):
            amcs[key] = {**base, "needs_harness": True,
                         "resolve": {"landing": landing} if landing else None,
                         "verified_url": verified or None, "n_schemes": 0, "confidence": "low"}
            report.append({"key": key, "amc": amc, "ok": False, "strategy": "harness",
                           "needs_harness": True, "n": 0})
            print(f"{key:22s} HARNESS (method={method})", flush=True)
            continue

        best = None
        def consider(strategy, spec):
            nonlocal best
            n, cov, asof = _verify(key, spec, method)
            if n > 0 and (best is None or n > best["n"]):
                best = {"strategy": strategy, "spec": spec, "n": n, "cov": cov, "asof": asof}
            return n

        # 1) curated deterministic pattern (if any)
        if key in CURATED:
            consider("pattern", {**base, **CURATED[key],
                                 "verified_url": verified or None})
        # 2) generic landing-scrape (combined) + verified fallback
        if landing:
            consider("resolve", {**base, "resolve": {"landing": landing, "pick": "newest"},
                                 "verified_url": verified or None})
        # 3) the discovered direct URL alone
        if best is None and verified:
            consider("verified", {**base, "verified_url": verified})
        # 4) per-scheme house: only if combined gave few schemes (bounds network)
        if (best is None or best["n"] < 3) and landing:
            consider("resolve_multi", {**base, "resolve": {"landing": landing, "pick": "all"},
                                       "multi": True, "verified_url": verified or None})

        if best:
            spec = {k: v for k, v in best["spec"].items() if v is not None}
            spec["n_schemes"] = best["n"]; spec["median_coverage"] = best["cov"]
            spec["asof"] = best["asof"]; spec["strategy"] = best["strategy"]
            spec["confidence"] = "high" if best["n"] >= 3 and (best["cov"] or 0) >= 85 else "medium"
            amcs[key] = spec
            report.append({"key": key, "amc": amc, "ok": True, "strategy": best["strategy"],
                           "n": best["n"], "cov": best["cov"], "asof": best["asof"]})
            print(f"{key:22s} OK {best['strategy']:13s} n={best['n']:<3} cov={best['cov']} asof={best['asof']}", flush=True)
        else:
            amcs[key] = {**base, "needs_harness": bool(landing), "verified_url": verified or None,
                         "resolve": {"landing": landing} if landing else None,
                         "n_schemes": 0, "confidence": "low"}
            report.append({"key": key, "amc": amc, "ok": False, "strategy": "none",
                           "needs_harness": bool(landing), "n": 0})
            print(f"{key:22s} FAIL (no strategy parsed; {'harness' if landing else 'no feed'})", flush=True)

    out = {"version": 1, "updated": dt.date.today().isoformat(),
           "note": "AMC monthly-portfolio fetch registry; built+verified by build_amc_specs.py",
           "amcs": amcs}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = [r for r in report if r["ok"]]
    harness = [r for r in report if r.get("needs_harness")]
    print("\n========================================================")
    print(f"VERIFIED {len(ok)}/{len(report)} AMCs parse from datacenter; {len(harness)} need the local harness.")
    print(f"total schemes across verified AMCs: {sum(r['n'] for r in ok)}")
    print(f"wrote {OUT}")
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    main()
