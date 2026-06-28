"""
vistas/fm_shortlist.py — the FM ACTION SHORTLIST (task #39), reference / agent-path twin.

WHAT IT IS
  A per-fund EVIDENCE shortlist that ranks, from forces ALREADY validated and published on this
  terminal, two candidate lists for a fund manager (human OR the FM agent in the Agentic AMC):
    • TRIM candidates = names the fund HOLDS whose forces have WEAKENED (the weak Smart-vs-Street
      quadrant: analysts not revising up AND funds net-selling), weakest-ARM first.
    • ADD  candidates = in-mandate names the fund does NOT hold (or is underweight vs its benchmark)
      whose forces have STRENGTHENED (recommending AND buying), best-ARM first.

  It is DECISION-SUPPORT, never an instruction. It SIZES nothing, FORECASTS no return, and shows no
  composite score. The FM decides + sizes under the mandate; it is then scored by IR = IC·√BR·TC.

DISCIPLINE / LICENSING
  • Synthesize validated signals, never manufacture alpha: ADDs are ranked by analyst-revision
    momentum (ARM) ALONE — the one forward-validated axis (IC ~0.03-0.045, short-horizon). Net flow
    enters only as a SIGN FLAG (the EW blend failed its backtest gate; flow is contrarian-if-anything).
  • This module READS only already-built artifacts (smart_vs_street.json + a fund's holdings book) and
    WRITES NOTHING — no new committed file, no new raw-ARM surface. The browser deck mirrors this exact
    logic in static/vistas.js (renderFMShortlist); the two share the threshold literals by convention,
    but there is no formula to keep in lock-step, so no JS↔Python parity harness gate applies.
  • If the FM-agent/paper-trading loop ingests the output, it must drop the raw `arm` number before
    persisting (keep only the quadrant label / flag), per the standing amc_book/ rule.

Provenance: re-implements the Vistas Smart-vs-Street quadrant convention (vistas/screens.py): a stock is
"recommending" if StarMine ARM >= 50, "buying" if corp-action-adjusted net active 3m-flow > 0; quadrant
1 = recommending+buying (strong), 4 = neither (weak).
"""

from __future__ import annotations
from typing import Optional, Iterable

# ---- thresholds (single source of truth; the JS twin mirrors these literals) ----
ARM_RECO        = 50.0      # ARM >= 50 = "recommending" (matches screens.ARM_REC)
TRIM_QUADS      = (4,)      # WEAKENING: held + "Neither" (not recommending AND not buying)
ADD_QUAD        = 1         # STRENGTHENING: recommending AND buying
ADD_MAX         = 15        # cap each list — decision-support, not a universe dump
TRIM_MAX        = 15
UNDERWEIGHT_EPS = 0.0       # "underweight vs benchmark" = held_wt < bench_wt - eps (eps=0 -> strictly under)


def _held_map(book: Iterable[dict]) -> dict:
    """Map a fund's equity book to held weights, keyed on vst_id FIRST (as "vid:<id>") and symbol as a
    fallback. The baked crowd_flow.equity_holdings is vst_id-keyed (symbol is dropped in the vst_id
    groupby), so a symbol-only join silently came up empty — held names showed as not-held and the
    weakening (TRIM) list was always 0. vst_id is the canonical identity, so join on it."""
    held = {}
    for h in (book or []):
        wt = float(h.get("pct", h.get("weight", 0)) or 0)
        vid = h.get("vst_id")
        if vid is not None:
            held["vid:" + str(vid)] = wt
        s = h.get("symbol") or h.get("sym")
        if s:
            held[s] = wt
    return held


def _held_wt(r: dict, held: dict):
    """The fund's held weight for a screen row — vst_id join first, then symbol. None if not held."""
    vid = r.get("vst_id")
    if vid is not None and ("vid:" + str(vid)) in held:
        return held["vid:" + str(vid)]
    s = r.get("symbol")
    if s is not None and s in held:
        return held[s]
    return None


def _usable(r: dict) -> bool:
    # ARM must be present AND not stale to judge the analyst axis (else we cannot call it weak/strong)
    return (r.get("arm") is not None) and (not r.get("arm_stale", False))


def build_shortlist(screen: dict, book, *, sebi_category: Optional[str] = None,
                    benchmark_constituents: Optional[dict] = None,
                    holdings_asof: Optional[str] = None) -> dict:
    """
    screen : parsed smart_vs_street.json (carries ['rows'], ['holdings_asof']).
    book   : the fund's equity holdings = f['crowd_flow']['equity_holdings'] (symbol + pct), SAME
             as-of month as the screen -> like-for-like.
    benchmark_constituents : {symbol -> bench_weight_pct} for the fund's benchmark (optional; if None
             the underweight test is skipped and Add eligibility = "in screen universe").
    Returns {'trim':[...], 'add_more':[...], 'add':[...], 'meta':{...}}. No file written.
    Held names split by force: weakening quadrant -> 'trim'; strengthening quadrant -> 'add_more'.
    Not-held strengthening names -> 'add'.
    """
    rows = {r["symbol"]: r for r in screen.get("rows", []) if r.get("symbol")}
    rows_by_vid = {str(r["vst_id"]): r for r in screen.get("rows", []) if r.get("vst_id") is not None}
    held = _held_map(book)

    # ---- HELD-SIDE: TRIM (held + weakening) and ADD-MORE (held + strengthening). Iterate the BOOK,
    #   resolve each row to its screen row by vst_id (then symbol), dedupe so a name can't repeat. ----
    trim, add_more, _seen = [], [], set()
    for h in (book or []):
        vid = h.get("vst_id")
        sym = h.get("symbol") or h.get("sym")
        r = (rows_by_vid.get(str(vid)) if vid is not None else None) or (rows.get(sym) if sym else None)
        if not r or not _usable(r):
            continue
        key = r.get("symbol") or r.get("vst_id")
        if key in _seen:
            continue
        _seen.add(key)
        q = r.get("quadrant_3m")
        bw = (benchmark_constituents or {}).get(r.get("symbol"))
        if q in TRIM_QUADS:                                       # held + Neither (weak) -> trim/sell candidate
            trim.append(_emit(r, held_wt=_held_wt(r, held), bench_wt=bw))
        elif q == ADD_QUAD:                                       # held + recommending&buying -> ADD-MORE candidate
            add_more.append(_emit(r, held_wt=_held_wt(r, held), bench_wt=bw))
    trim.sort(key=lambda e: (e["arm"], -abs(e["flow_3m_cr"] or 0)))    # weakest ARM first
    add_more.sort(key=lambda e: -e["arm"])                            # best ARM first

    # ---- ADD: NOT held, strong quadrant, in-mandate, best ARM first (held strong names go to ADD-MORE) ----
    add = []
    for s, r in rows.items():
        if not _usable(r) or r.get("quadrant_3m") != ADD_QUAD:
            continue
        if _held_wt(r, held) is not None:                        # held -> it's an ADD-MORE, not a new ADD
            continue
        bw = (benchmark_constituents or {}).get(s)
        if benchmark_constituents is not None and bw is None:    # bench provided but name out of universe
            continue
        add.append(_emit(r, held_wt=None, bench_wt=bw, not_held=True))
    add.sort(key=lambda e: -e["arm"])

    return {
        "trim": trim[:TRIM_MAX],
        "add_more": add_more[:ADD_MAX],
        "add":  add[:ADD_MAX],
        "meta": {
            "n_trim": len(trim), "n_add_more": len(add_more), "n_add": len(add), "n_held": len(held),
            "holdings_asof": holdings_asof or screen.get("holdings_asof"),
            "sebi_category": sebi_category,
            "ranked_by": "ARM (analyst-revision momentum); flow shown as sign-flag only",
            "discipline": "decision-support, not instructions; no size/target/expected-return/score",
        },
    }


def _emit(r: dict, *, held_wt=None, bench_wt=None, not_held=None) -> dict:
    return {
        "symbol": r["symbol"], "name": r.get("name"), "sector": r.get("sector"),
        "vst_id": r.get("vst_id"),
        "current_weight_pct": (round(held_wt, 3) if held_wt is not None else None),
        "bench_weight_pct":   (round(bench_wt, 3) if bench_wt is not None else None),
        "not_held": not_held,
        "arm": r.get("arm"), "arm_asof": r.get("arm_asof"), "arm_stale": r.get("arm_stale", False),
        "recommending": r.get("recommending", False),
        "flow_3m_cr": r.get("flow_3m"), "flow_1m_cr": r.get("flow_1m"),
        "buying_3m": r.get("buying_3m"), "net_breadth": r.get("net_breadth"),
        "quadrant_3m": r.get("quadrant_3m"), "mf_nfunds": r.get("mf_nfunds"),
        "rationale": _rationale(r),     # QUOTES the signal — never a verdict ("weak"/"trim")
    }


def _rationale(r: dict) -> str:
    arm = r.get("arm")
    fl = abs(r.get("flow_3m") or 0)
    a = "analysts %s (ARM %s)" % ("revising up" if r.get("recommending") else "not revising up",
                                  ("%.0f" % arm) if arm is not None else "—")
    f = "funds net-%s 3M (₹%s cr)" % ("buying" if r.get("buying_3m") else "selling", "{:,.0f}".format(fl))
    return "%s; %s" % (a, f)


if __name__ == "__main__":  # tiny self-test against the live screen + one fund's book, prints only
    import json, os, glob, sys
    try: sys.stdout.reconfigure(encoding="utf-8")   # Windows cp1252 console can't print the ₹ glyph
    except Exception: pass
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    scr_paths = glob.glob(os.path.join(base, "_pages", "terminal", "data", "_screens", "smart_vs_street.json")) \
        or glob.glob(os.path.join(base, "output", "*", "data", "_screens", "smart_vs_street.json"))
    if not scr_paths:
        print("no smart_vs_street.json found (build the terminal first)"); raise SystemExit
    screen = json.load(open(scr_paths[0], encoding="utf-8"))
    # demo book = the 10 highest-ARM recommending+buying names (so trim/add both exercise)
    demo = [{"symbol": r["symbol"], "pct": 2.0} for r in screen["rows"][:25] if r.get("symbol")]
    out = build_shortlist(screen, demo, sebi_category="Flexi Cap")
    print(json.dumps({"meta": out["meta"], "n_trim": len(out["trim"]), "n_add": len(out["add"]),
                      "trim_top": out["trim"][:3], "add_top": out["add"][:3]}, indent=2, ensure_ascii=False))
