"""
_equity_research_validate.py — validation harness for vistas/equity_research.py (task #64/#65).

Runs research() on THREE archetypes — RELIANCE (conglomerate), INFY (IT), HDFCBANK (bank) —
prints the full dossier + thesis for each, and ASSERTS the acceptance criteria:

  A. every section resolves OR degrades to a clean {"ok": False, "na": ...} — never crashes;
  B. the dossier is json-serializable with allow_nan=False (no NaN/Inf escapes);
  C. EVERY percentile (own-history, peer, peer-table, favourability) is within [0, 100];
  D. the three theses read as GENUINELY DIFFERENT (distinct bull/bear/stance);
  E. the Fundamental-Law read is present and names an IC source per name.

Run: python _equity_research_validate.py
"""
import json
import math
import sys

from vistas import equity_research as er

ARCHETYPES = [("RELIANCE", "conglomerate"), ("INFY", "IT services"), ("HDFCBANK", "bank")]


def _walk_percentiles(obj, path=""):
    """Yield (path, value) for every key that looks like a percentile/favourability score, so we can
    range-check ALL of them in [0,100]."""
    PCT_KEYS = ("pctile", "percentile", "favourability", "global_pctile")
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = f"{path}.{k}"
            if any(tok in k.lower() for tok in PCT_KEYS) and isinstance(v, (int, float)):
                yield kp, v
            yield from _walk_percentiles(v, kp)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_percentiles(v, f"{path}[{i}]")


def _has_nan(obj):
    """True if any float NaN/Inf is anywhere in the structure (allow_nan=False would reject it)."""
    if isinstance(obj, float):
        return not math.isfinite(obj)
    if isinstance(obj, dict):
        return any(_has_nan(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_nan(v) for v in obj)
    return False


def main():
    print("Building shared context (prices + ARM cards + 12m smart-money flow) ... this takes a minute.\n")
    ctx = er.build_context(flow_months_back=12)
    if ctx.get("_notes"):
        print("context notes:", ctx["_notes"], "\n")

    dossiers = {}
    all_ok = True

    for sym, archetype in ARCHETYPES:
        print("#" * 100)
        print(f"# {sym}  ({archetype})")
        print("#" * 100)
        try:
            d = er.research(sym, ctx=ctx)
        except Exception as e:
            print(f"  *** research() CRASHED on {sym}: {type(e).__name__}: {e}")
            all_ok = False
            continue
        dossiers[sym] = d

        # --- A: sections resolve or clean-degrade ---
        for name, sec in d["sections"].items():
            status = "ok" if sec.get("ok") else f"n/a ({sec.get('na')})"
            print(f"  [section] {name:12s}: {status}")

        # --- B: allow_nan=False round-trip ---
        try:
            payload = json.dumps(d, allow_nan=False)
            nan_free = not _has_nan(d)
            print(f"  [json   ] allow_nan=False OK ({len(payload):,} bytes); NaN/Inf-free: {nan_free}")
            if not nan_free:
                all_ok = False
        except (ValueError, TypeError) as e:
            print(f"  *** JSON allow_nan=False FAILED: {e}")
            all_ok = False

        # --- C: percentile range check ---
        bad = [(p, v) for p, v in _walk_percentiles(d) if not (0.0 <= v <= 100.0)]
        n_pct = sum(1 for _ in _walk_percentiles(d))
        if bad:
            print(f"  *** {len(bad)} percentile(s) OUT OF [0,100]: {bad[:5]}")
            all_ok = False
        else:
            print(f"  [pctile ] all {n_pct} percentile fields in [0,100]: OK")

        # --- E: fundamental-law present ---
        fl = d["synthesis"]["fundamental_law"]
        has_fl = bool(fl.get("ic_source")) and bool(fl.get("transfer_coefficient"))
        print(f"  [law    ] IC-source named + TC caveat present: {has_fl}")
        if not has_fl:
            all_ok = False

        # --- dump the structured dossier (compact) + the full thesis ---
        print("\n  --- VALUATION (own-history + peer percentiles) ---")
        v = d["sections"]["valuation"]
        if v.get("ok"):
            for k, row in v["multiples"].items():
                if row.get("value") is None:
                    print(f"    {row['label']:28s}: n/a ({row.get('na')})")
                    continue
                print(f"    {row['label']:28s}: {row['value']:>10}  own%ile={row.get('own_pctile')}  "
                      f"peer%ile={row.get('peer_pctile')} (n={row.get('n_peers')})  {row.get('read') or ''}")
            print(f"    => headline cheapness: {v.get('cheapness')} (P/E own-pctile {v.get('headline_pe_own_pctile')})")

        print("\n  --- QUALITY / GROWTH ---")
        qg = d["sections"]["quality"]
        if qg.get("ok"):
            print(f"    quality score={qg['quality_score']} | rev 5y CAGR={qg['revenue_cagr_5y_pct']}% | "
                  f"EPS 5y CAGR={qg['eps_cagr_5y_pct']}% | PAT TTM YoY={qg['pat_ttm_yoy_pct']}%")
            print(f"    ROE={qg['roe_now_pct']}% ({qg['roe_dir']}) | margin={qg['margin_now_pct']}% ({qg['margin_dir']}) | "
                  f"up-years={qg['growth_stability_up_years_pct']}% | D/E={qg['de_now']} | int-cover={qg['interest_cover']}")

        print("\n  --- MOMENTUM / TECHNICAL ---")
        m = d["sections"]["momentum"]
        if m.get("ok"):
            print(f"    px={m['price']} asof {m['asof']} | returns={m['returns_pct']}")
            print(f"    200-DMA dist={m['dist_200dma_pct']}% (above={m['above_200dma']}) | golden={m['golden_cross']} | "
                  f"52w-high dist={m['dist_52w_high_pct']}% (new-high={m['new_52w_high']}) | trend={m['trend']} | "
                  f"RS vs N500={m['rs_vs_nifty500_12m_pp']}pp")

        print("\n  --- ANALYST REVISIONS (ARM) ---")
        rv = d["sections"]["revisions"]
        if rv.get("ok"):
            print(f"    ARM={rv['score']} ({rv['level_band']}) | 30d={rv['trend_30d']} 90d={rv['trend_90d']} "
                  f"({rv['direction']}) | bucket5={rv['bucket5']} | dominant={rv['dominant_component']} | stale={rv['stale']}")
            print(f"    components: {[(c['label'], c['score']) for c in rv['components']]}")
        else:
            print(f"    n/a ({rv.get('na')})")

        print("\n  --- SMART MONEY (net-active conviction flow + ownership) ---")
        sm = d["sections"]["smart_money"]
        if sm.get("flow"):
            fl2 = sm["flow"]
            print(f"    net-active={fl2['net_active_cr']} Rs cr ({fl2['ym']}) | intensity={fl2['na_intensity_pct']}% | "
                  f"rank={fl2['na_rank']}/{fl2['na_nclean']} | 3m stance={fl2['stance']} ({fl2['recent_3m_net_active_cr']} Rs cr)")
        if sm.get("ownership"):
            print(f"    ownership: {sm['ownership']}")
        if not sm.get("ok"):
            print(f"    n/a ({sm.get('na')})")

        print("\n  --- PEER CONTEXT ---")
        p = d["sections"]["peers"]
        if p.get("ok"):
            print(f"    industry='{p['industry']}'  n_peers={p['n_peers']}")
            for axis, row in p["table"].items():
                print(f"    {row['label']:22s}: value={row['value']:>10}  peer%ile={row['peer_pctile']:>5}  "
                      f"favourability={row['favourability']:>5}  [{row['band']}]")
        else:
            print(f"    n/a ({p.get('na')})")

        print("\n  --- SYNTHESIS ---")
        syn = d["synthesis"]
        print(f"    stance: {syn['stance']}")
        print(f"    bull : {syn['bull']}")
        print(f"    bear : {syn['bear']}")
        print(f"    cmm  : {syn['what_would_change_my_mind']}")
        print(f"    LAW  : ic_source = {syn['fundamental_law']['ic_source']}")
        print(f"           breadth   = {syn['fundamental_law']['breadth_independence']}")

        print("\n  --- THESIS (plain English) ---")
        print("   ", d["thesis"])
        print()

    # --- D: the three theses must be genuinely different ---
    print("#" * 100)
    print("# CROSS-CHECK: are the three theses genuinely different?")
    print("#" * 100)
    syms = [s for s, _ in ARCHETYPES if s in dossiers]
    theses = {s: dossiers[s]["thesis"] for s in syms}
    stances = {s: dossiers[s]["synthesis"]["stance"] for s in syms}
    bulls = {s: tuple(dossiers[s]["synthesis"]["bull"]) for s in syms}
    bears = {s: tuple(dossiers[s]["synthesis"]["bear"]) for s in syms}
    print("  stances:", stances)
    distinct_text = len(set(theses.values())) == len(theses)
    distinct_profile = len(set((bulls[s], bears[s]) for s in syms)) == len(syms)
    print(f"  distinct thesis TEXT     : {distinct_text}")
    print(f"  distinct bull/bear PROFILE: {distinct_profile}")
    if not (distinct_text and distinct_profile):
        print("  *** theses are NOT all distinct")
        all_ok = False

    print("\n" + ("=" * 40))
    print("VALIDATION RESULT:", "ALL CHECKS PASS" if all_ok else "FAILURES ABOVE")
    print("=" * 40)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
