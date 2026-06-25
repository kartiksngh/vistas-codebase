"""
FAST diagnostic for the PR (Historical Index / Price-Return) and VAL (P/E · P/B ·
Dividend-Yield) niftyindices.com endpoints, with a TR control.

WHY A TR CONTROL: the TR endpoint is proven to work. A *malformed* NSE request
HANGS (~read-timeout) instead of erroring — so a PR/VAL stall is ambiguous: it
could be the request contract OR the network. By firing TR, PR and VAL from the
SAME session, same machine, same moment, we disambiguate:
  * TR ok, PR/VAL hang   -> PR/VAL request CONTRACT is wrong (fix in code).
  * TR also hangs         -> network / IP throttle (wait or change network).
  * PR/VAL return rows    -> endpoints good; ready to backfill.

Tiny + fast: one index, ~60 days, ONE request per group, 15s timeout, no retries.
Prints the RAW columns NSE returns and writes output/_measures_probe.json.

Run:  double-click "Validate NSE Measures.bat"   (or:  python _validate_measures.py)
"""
from __future__ import annotations

import os
import sys
import json
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("VISTAS_FETCH_PROFILE", "normal")

import pandas as pd
from vistas import fetch

INDEX = "NIFTY 50"
A = dt.date.today() - dt.timedelta(days=60)
B = dt.date.today()
OUT = os.path.join(HERE, "output", "_measures_probe.json")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

res = {"index": INDEX, "window": [A.isoformat(), B.isoformat()], "groups": {}}
print(f"=== Validate TR(control) + PR + VAL | {INDEX} | {A} -> {B} ===\n", flush=True)

try:
    s = fetch._session()
    print("NSE session established.\n", flush=True)
except Exception as e:
    print("SESSION FAILED:", e, flush=True)
    res["error"] = f"session: {e}"
    json.dump(res, open(OUT, "w"), indent=2, default=str)
    sys.exit(1)

nm = fetch.api_name(INDEX)


def probe(grp):
    """One patient POST per host (bare-first for PR/VAL) using the SAME timeout, host
    order and Referer/Origin the fixed fetcher now uses. Returns (status, raw, detail)."""
    spec = fetch.ENDPOINTS[grp]
    timeout = spec.get("timeout", 30)
    urls = fetch._endpoint_urls(spec["method"], prefer_bare=spec.get("prefer_bare", False))
    cinfo = "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}" % (
        nm, fetch._fmt(A), fetch._fmt(B), nm)
    body = json.dumps({"cinfo": cinfo})
    detail = None
    for url in urls:
        try:
            r = s.post(url, data=body, timeout=timeout, headers=fetch._headers_for_url(url))
            if r.status_code == 200 and r.content:
                try:
                    return "OK", fetch._parse(r.json()), fetch._host_of(url)
                except Exception as e:
                    return "BAD_JSON", None, f"{type(e).__name__}: {str(r.text)[:120]}"
            detail = f"HTTP {r.status_code} @ {fetch._host_of(url)}"
        except Exception as e:
            detail = f"{type(e).__name__} @ {fetch._host_of(url)}: {str(e)[:90]}"
    return "HANG/TIMEOUT", None, detail


for grp in ("TR", "PR", "VAL"):
    spec = fetch.ENDPOINTS[grp]
    status, raw, detail = probe(grp)
    entry = {"endpoint": spec["method"], "status": status}
    tag = "(control)" if grp == "TR" else ""
    if status == "OK" and raw is not None:
        rawcols = [str(c) for c in raw.columns]
        entry["raw_cols"] = rawcols
        if len(raw):
            entry["raw_first"] = {k: str(v) for k, v in raw.iloc[0].to_dict().items()}
        # map via the same logic the real fetcher uses
        f = fetch.fetch_frame(s, INDEX, A, B, grp)
        mapped = [str(c) for c in f.columns] if f is not None and not f.empty else []
        entry["mapped"] = mapped
        entry["rows"] = 0 if f is None else int(len(f))
        if mapped:
            entry["last"] = {c: round(float(f[c].iloc[-1]), 4)
                             for c in f.columns if pd.notna(f[c].iloc[-1])}
        print(f"[{grp}] {tag} {spec['method']}: OK", flush=True)
        print(f"      RAW cols : {rawcols}", flush=True)
        print(f"      MAPPED   : {mapped}  ({entry['rows']} rows)" +
              (f"  last={entry.get('last')}" if mapped else "  <-- raw came back but NOTHING mapped"),
              flush=True)
    else:
        print(f"[{grp}] {tag} {spec['method']}: {status}" + (f"  {detail}" if detail else ""), flush=True)
        if detail:
            entry["detail"] = detail
    res["groups"][grp] = entry
    print(flush=True)

json.dump(res, open(OUT, "w"), indent=2, default=str)
print("Result written to:", OUT, "\n", flush=True)

g = res["groups"]
tr = g["TR"]["status"]
pr = g["PR"]["status"]
val = g["VAL"]["status"]
print("VERDICT:", flush=True)
if tr == "OK" and pr == "OK" and val == "OK":
    print("  All three endpoints return data -> PR + valuation are GOOD. Ready to backfill.", flush=True)
elif tr == "OK" and (pr != "OK" or val != "OK"):
    print("  TR works but PR/VAL do NOT -> it's the PR/VAL request CONTRACT, not the network.", flush=True)
    print("  The RAW columns above (if any) show the fix; I'll patch fetch.py.", flush=True)
elif tr != "OK":
    print("  Even the TR control failed -> this is the network / IP throttle, not the code.", flush=True)
    print("  Wait for it to clear or switch network (phone hotspot), then re-run.", flush=True)
