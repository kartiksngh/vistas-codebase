"""vistas/amc_live.py — the LIVE-FORWARD engine for the digital-AMC.

The historical track was built by the deterministic rules-FM (`amc_replay.py`). From the seam (the latest
data date) ON, the **LLM FM agents** take the decision seat: they read a desk context this module assembles,
return target weights, and this module's deterministic GUARDRAIL + EXECUTE turn that into an auditable book.
Design = `LIVE_FORWARD.md`. The LLM proposes; the mandate (here) disposes.

Pipeline (one scheme, one `asof`):
  prepare_desk(reg_entry, asof)  -> writes a desk JSON the FM agent Reads (inherited book + candidate universe
                                    with multi-force signals + quant baseline + mandate + scorecard); no look-ahead.
  [ LLM FM agent decides -> {sym: target_weight} + rationales ]   (run by the `amc_rebalance` Workflow)
  apply_decision(reg_entry, asof, proposed, rationales) -> enforce_guardrails -> execute -> mark -> compare.

Discipline: paper-only; no look-ahead (every price/signal <= asof); synthesize validated signals, never
manufacture alpha; raw per-stock ARM is NEVER persisted to amc_book/ (scrubbed from rationales — the LLM may
SEE it to reason, the committed audit trail must not carry it).
"""
import os
import re
import json
import datetime

import pandas as pd

from . import amc_firm as af
from . import amc_replay as ar

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_DIR = os.path.join(_ROOT, "output", "_amc", "live")
DESK_DIR = os.path.join(LIVE_DIR, "desks")
COST_BPS = ar.COST_BPS                      # 15 bps/side, same as the replay


def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(s)).strip("_")


# ───────────────────────────────────────────────────────── licensing scrub
# Strip any raw LSEG StarMine analyst-revision (ARM) value, however phrased, BEFORE it is persisted to the
# git-tracked amc_book/ audit trail. The agent MAY see "ARM 78" in its desk to reason; the committed text must
# carry it only QUALITATIVELY. Defence-in-depth: the rebalance prompt also tells agents not to quote a numeric
# revision score in persisted fields (belt) — this scrub is the suspenders.
# Conservative on the NUMBER side: a 0-100 number is only scrubbed when it sits next to a metric NAME
# (ARM / StarMine / analyst-revision / revision score|momentum|rank), so weights/counts/years are untouched.
_ARM_NAME = (r"(?:ARM|StarMine(?:\s+ARM)?|analyst[\s-]*revisions?(?:\s+(?:score|momentum|rank))?"
             r"|revision[\s-]*(?:score|momentum|rank))")
_ARM_CONN = r"(?:\s+(?:of|score|rank|reading|value|is|at|near|around)|\s*[:=])*"   # 0+ score-connector tokens
_ARM_RE = re.compile(
    _ARM_NAME + _ARM_CONN + r"\s*\d{1,3}(?:\.\d+)?\b"                  # NAME [connectors] NUMBER
    r"|\b\d{1,3}(?:\.\d+)?\s*(?:on\s+(?:the|its)\s+)?" + _ARM_NAME,    # NUMBER [on the] NAME
    re.IGNORECASE,
)


def scrub_arm(text):
    """Strip any raw per-stock ARM value from text BEFORE it is persisted to amc_book/ (the committed audit
    trail must not carry licensed LSEG values; the LLM may quote 'ARM 78' in-flight, we store it qualitatively)."""
    if not isinstance(text, str):
        return text
    return _ARM_RE.sub("strong analyst-revision momentum", text).strip()


# ───────────────────────────────────────────────────────── helpers
def _asof_ts(asof_str):
    panel = ar._panel()
    idx = panel.index[panel.index <= pd.Timestamp(asof_str)]
    if not len(idx):
        raise SystemExit(f"no price data on/before {asof_str}")
    return idx[-1]


def _book_path(reg_entry):
    return os.path.join(af.scheme_dir(reg_entry["amc"], reg_entry["scheme"]), "book.json")


def load_book(reg_entry):
    p = _book_path(reg_entry)
    if os.path.exists(p):
        return af._read_json(p)
    return af.new_book(reg_entry)


def _flow_snapshot():
    """Optional net-active-flow enrichment {sym: flow_cr} from the baked smart-money-vs-street screen
    (current snapshot). Best-effort — the desk is multi-force without it (ARM+mom+value), this just adds
    the smart-money lens where present."""
    for cand in (os.path.join(_ROOT, "output", "terminal_site", "data", "_screens", "smart_vs_street.json"),
                 os.path.join(_ROOT, "data", "_screens", "smart_vs_street.json")):
        try:
            d = json.load(open(cand, encoding="utf-8"))
            rows = d.get("rows") or d.get("stocks") or []
            out = {}
            for r in rows:
                s = r.get("symbol") or r.get("sym")
                f = r.get("net_active_cr", r.get("flow", r.get("flow_cr")))
                if s is not None and f is not None:
                    out[s] = round(float(f), 1)
            return out
        except Exception:
            continue
    return {}


def _book_value(book, asof_str):
    """Current marked value of the book (₹cr) = Σ qty·price_asof + cash."""
    eq = 0.0
    for sym, pos in book.get("positions", {}).items():
        px = af.price_asof(sym, asof_str)
        if px:
            eq += pos.get("qty", 0) * px / 1e7
    return eq + af._f(book.get("cash_cr"))


# ───────────────────────────────────────────────────────── 1) ASSEMBLE the FM desk (no look-ahead)
def prepare_desk(reg_entry, asof_str, top_n=70, write=True):
    """Build the FM's decision desk as-of `asof_str` and (optionally) write it to a file the LLM FM agent
    Reads. Returns the context dict. No look-ahead: universe/prices/ARM/forces are all ≤ asof."""
    m = reg_entry["mandate"]
    asof_ts = _asof_ts(asof_str)
    bid = reg_entry.get("brain") or af.brain_for_mandate(reg_entry.get("category"), m)

    uni, umeta = ar.point_in_time_universe(asof_ts)
    buckets = set(m.get("buckets", ["large", "mid", "small"]))
    cand = [u for u in uni if u.get("bucket") in buckets] or list(uni)
    bid, _bdiag = af.score_universe(cand, asof_str, bid)          # attaches z_arm/z_mom/z_val + brain score
    cand.sort(key=lambda u: -u["score"])
    cand_by_sym = {u["sym"]: u for u in cand}

    book = load_book(reg_entry)
    aum_now = _book_value(book, asof_str)
    held = {}
    for sym, pos in book.get("positions", {}).items():
        px = af.price_asof(sym, asof_str)
        w = (pos.get("qty", 0) * px / 1e7 / aum_now) if (px and aum_now) else 0.0
        cost = pos.get("avg_cost")
        pnl = round(100.0 * (px / cost - 1.0), 1) if (px and cost) else None
        held[sym] = {"sym": sym, "name": pos.get("name"), "sector": pos.get("sector"),
                     "weight_pct": round(100.0 * w, 2), "play_type": pos.get("play_type"),
                     "pnl_since_entry_pct": pnl, "px": round(px, 2) if px else None}

    flows = _flow_snapshot()
    # candidate desk = top_n by brain score ∪ everything currently held (so the FM can see/trim its book)
    keep = {u["sym"] for u in cand[:top_n]} | set(held)
    desk = []
    for u in cand:
        if u["sym"] not in keep:
            continue
        desk.append({
            "sym": u["sym"], "name": u["name"], "sector": u["sector"], "bucket": u.get("bucket"),
            "arm": (round(u["arm"], 0) if u.get("arm") is not None else None),
            "z_mom": round(u.get("z_mom", 0.0), 2), "z_val": round(u.get("z_val", 0.0), 2),
            "z_arm": round(u.get("z_arm", 0.0), 2), "brain_score": round(u["score"], 3),
            "net_flow_cr": flows.get(u["sym"]),
            "held_pct": held.get(u["sym"], {}).get("weight_pct", 0.0),
            "px": round(u["px"], 2),
        })

    # the rules-FM (quant) baseline target — the reference the FM may deviate from
    q_targets, _qcand, _qinfo = ar.construct_targets(reg_entry, [dict(u) for u in cand], asof_ts, aum_now, bid)
    quant_baseline = sorted(
        ({"sym": s, "name": cand_by_sym.get(s, {}).get("name"), "target_pct": round(100.0 * t["w"], 2)}
         for s, t in q_targets.items()), key=lambda x: -x["target_pct"])[:40]

    sc_path = os.path.join(af.scheme_dir(reg_entry["amc"], reg_entry["scheme"]), "replay", "scorecard.json")
    scorecard = af._read_json(sc_path) if os.path.exists(sc_path) else {}
    sc = (scorecard.get("scorecard") or {}) if scorecard else {}

    ctx = {
        "scheme": reg_entry["scheme"], "amc": reg_entry["amc"], "category": reg_entry["category"],
        "benchmark": reg_entry.get("benchmark"), "brain": bid, "asof": asof_str,
        "aum_cr": round(aum_now, 1),
        "mandate": {"equity_min": m.get("equity_min"), "equity_max": m.get("equity_max"),
                    "max_pos_pct": round(100 * m["max_pos"], 1), "max_sector_pct": round(100 * m["max_sector"], 1),
                    "n_band": [m.get("n_lo"), m.get("n_hi")], "buckets": sorted(buckets)},
        "current_book": {"n_holdings": len(held), "cash_pct": round(100.0 * af._f(book.get("cash_cr")) / aum_now, 1) if aum_now else None,
                         "holdings": sorted(held.values(), key=lambda h: -(h["weight_pct"] or 0))},
        "track_record": {k: sc.get("benchmark", {}).get(k) for k in ("cagr_pct", "excess_cagr_pct", "info_ratio", "beta")} if sc else {},
        "quant_baseline_top": quant_baseline,
        "candidates": desk,
        "universe_meta": {"n": umeta.get("n"), "arm_coverage_pct": umeta.get("arm_cov")},
        "signal_legend": {
            "arm": "LSEG StarMine analyst-revision momentum 0-100 (>=50 = analysts upgrading; a SMALL ~1-6mo tilt, IC~0.05, NOT a guarantee)",
            "z_mom": "cross-sectional z of 6m-1m price momentum (>0 = stronger than peers)",
            "z_val": "cross-sectional z of cheapness (E/P+B/P+S/P; >0 = cheaper than peers)",
            "brain_score": f"this desk's multi-force blend ({bid}) used by the quant baseline",
            "net_flow_cr": "net-active mutual-fund flow (₹cr; >0 = smart money accumulating)",
            "held_pct": "your current weight in the name (0 = not held)",
        },
    }
    # the LEARNING LOOP: embed the FM's most recent graded round (hit rate / conviction-IC / best+worst
    # calls vs its benchmark) so the agent can learn from its own track record. Best-effort; None on the
    # first round or before any horizon has elapsed. No licensed data (price-outcome + scrubbed thesis only).
    try:
        from . import amc_grade as _grade
        ctx["last_round_review"] = _grade.latest_review(reg_entry)
    except Exception:
        ctx["last_round_review"] = None
    if write:
        os.makedirs(DESK_DIR, exist_ok=True)
        path = os.path.join(DESK_DIR, f"{slug(reg_entry['scheme'])}.json")
        json.dump(ctx, open(path, "w", encoding="utf-8"), indent=1, default=str)
        ctx["_path"] = path
        ctx["_cand_by_sym"] = None        # not serialised; callers that need it call prepare again or keep cand
    ctx["_cand"] = cand                   # in-memory only (for guardrail/execute in the same process)
    ctx["_aum_now"] = aum_now
    ctx["_book"] = book
    ctx["_quant_targets"] = {s: t["w"] for s, t in q_targets.items()}
    return ctx


# ───────────────────────────────────────────────────────── 2) GUARDRAIL (deterministic)
def enforce_guardrails(proposed, ctx):
    """`proposed` = {sym: target_weight_fraction} from the LLM FM. Returns (final {sym: w}, notes[]).
    Enforces: only priceable as-of names (drops look-ahead / fabricated tickers); per-name cap =
    min(mandate max_pos, liquidity cap); per-sector cap; total ≤ equity_target; and if the result is below
    the MANDATE EQUITY FLOOR, top up from the quant baseline (deploy_with_floor) so the book stays compliant.
    Reuses the SAME water-fill the rules-FM uses, with the LLM's target weight as the conviction 'score'."""
    cand = ctx["_cand"]
    cand_by_sym = {u["sym"]: u for u in cand}
    reg_m = _mandate_obj(ctx)                     # real mandate object (ctx['mandate'] is display-only)
    aum = ctx["_aum_now"]
    asof = ctx["asof"]
    equity_target = 0.95
    emax = reg_m.get("equity_max")
    if emax:
        equity_target = min(equity_target, round((reg_m["equity_min"] + emax) / 2.0, 4))

    notes = []
    sel = []
    dropped = []
    for sym, w in proposed.items():
        u = cand_by_sym.get(sym)
        if u is None or w is None or w <= 0:
            if u is None and sym:
                dropped.append(sym)
            continue
        cap = reg_m["max_pos"]
        lc = af.liquidity_cap_cr(sym, aum, asof)
        if lc is not None and aum > 0:
            cap = min(cap, lc / aum)
        sel.append({"sym": sym, "score": float(w), "sector": u["sector"], "px": u["px"],
                    "isin": u.get("isin"), "name": u.get("name"), "arm": u.get("arm"),
                    "cap": max(0.0, cap), "w": 0.0})
    if dropped:
        notes.append(f"dropped {len(dropped)} non-priceable/unknown ticker(s): {', '.join(dropped[:8])}")
    if not sel:
        notes.append("LLM proposed no valid names — falling back to the quant baseline")
        return dict(ctx["_quant_targets"]), notes

    SECTOR_FREE = "Unclassified"
    final_sel, deployed, relaxed = af.deploy_with_floor(sel, cand, reg_m, aum, asof, equity_target, SECTOR_FREE)
    if relaxed:
        notes.append(f"below mandate equity floor on the LLM names alone → widened/relaxed to {round(100*deployed,1)}% (compliance top-up)")
    final = {u["sym"]: u["w"] for u in final_sel if u["w"] > 1e-6}
    notes.append(f"deployed {round(100*sum(final.values()),1)}% across {len(final)} names (cap {reg_m['max_pos']*100:.0f}%/name, {reg_m['max_sector']*100:.0f}%/sector)")
    return final, notes


def _mandate_obj(ctx):
    """Recover the real mandate object for the scheme's category (ctx['mandate'] is a display-only dict)."""
    return af.mandate_for(ctx["category"])


# ───────────────────────────────────────────────────────── 3) EXECUTE (deterministic) + record
def apply_decision(reg_entry, asof_str, proposed, rationales=None, stance=None, experience_note=None,
                   ctx=None, log=print):
    """Guardrail the LLM's proposed targets, diff vs the inherited book → trades, charge cost, write the
    blotter + pre-registered theses (raw ARM scrubbed) + the new book, mark to market, and compare to the
    quant baseline. `rationales` = {sym: {rationale, thesis, falsification, play_type, action}}. Returns a
    summary dict."""
    if ctx is None:
        ctx = prepare_desk(reg_entry, asof_str, write=False)
    rationales = rationales or {}
    cand_by_sym = {u["sym"]: u for u in ctx["_cand"]}
    final, notes = enforce_guardrails(proposed, ctx)
    aum = ctx["_aum_now"]
    book = ctx["_book"]
    asof = asof_str
    cur = dict(book.get("positions", {}))

    # build the new positions from target weights; diff vs current → trades
    trades, cost_cr, new_positions = [], 0.0, {}
    syms = set(final) | set(cur)
    for sym in sorted(syms, key=lambda s: -final.get(s, 0.0)):
        u = cand_by_sym.get(sym)
        px = (u["px"] if u else af.price_asof(sym, asof))
        if not px:
            # can't price → if held, keep as-is (no trade); if proposed, skip
            if sym in cur:
                new_positions[sym] = cur[sym]
            continue
        tgt_w = final.get(sym, 0.0)
        tgt_qty = round(tgt_w * aum * 1e7 / px)
        cur_qty = cur.get(sym, {}).get("qty", 0)
        d_qty = tgt_qty - cur_qty
        if d_qty != 0 and abs(d_qty * px / 1e7) > 0.01:        # ignore sub-₹0.01cr dust
            side = "BUY" if d_qty > 0 else "SELL"
            notional = abs(d_qty) * px / 1e7
            cost_cr += notional * COST_BPS / 1e4
            info = rationales.get(sym, {})
            action = info.get("action") or ("NEW" if cur_qty == 0 and d_qty > 0 else ("EXIT" if tgt_qty == 0 else side))
            trades.append({"date": asof, "sym": sym, "isin": (u.get("isin") if u else cur.get(sym, {}).get("isin")),
                           "name": (u.get("name") if u else cur.get(sym, {}).get("name")),
                           "side": side, "action": action, "qty": abs(d_qty), "price": round(px, 4),
                           "value_cr": round(notional, 4),
                           "play_type": info.get("play_type") or af._play_type(u.get("arm") if u else None),
                           "decided_by": "LLM-FM", "brain": ctx.get("brain"),
                           "rationale": scrub_arm(info.get("rationale") or f"rebalance to {round(tgt_w*100,2)}%")})
        if tgt_qty > 0:
            prev = cur.get(sym, {})
            info = rationales.get(sym, {})
            new_positions[sym] = {
                "isin": (u.get("isin") if u else prev.get("isin")), "name": (u.get("name") if u else prev.get("name")),
                "sector": (u.get("sector") if u else prev.get("sector")),
                "qty": tgt_qty, "avg_cost": prev.get("avg_cost") if (cur.get(sym, {}).get("qty", 0) >= tgt_qty) else round(px, 4),
                "play_type": info.get("play_type") or prev.get("play_type") or af._play_type(u.get("arm") if u else None),
                "entry_date": prev.get("entry_date", asof) if sym in cur else asof,
                "thesis_ref": None, "sel_rank": None, "brain": ctx.get("brain"), "decided_by": "LLM-FM",
            }

    # update the book
    book["positions"] = new_positions
    deployed_cr = sum(p["qty"] * (cand_by_sym.get(s, {}).get("px") or af.price_asof(s, asof) or 0) / 1e7
                      for s, p in new_positions.items())
    book["cash_cr"] = round(max(0.0, aum - deployed_cr - cost_cr), 4)
    book["asof"] = asof
    book["managed_by"] = "LLM-FM (live-forward)"
    book["stance"] = stance
    af.save_book(book)

    # record blotter + pre-registered theses (the honest-scoring anchor) — ARM scrubbed
    for t in trades:
        af.append_blotter(reg_entry["amc"], reg_entry["scheme"], t)
    prereg = []
    for sym, info in rationales.items():
        if sym not in final:
            continue
        prereg.append({"date": asof, "sym": sym, "decided_by": "LLM-FM",
                       "target_pct": round(100 * final.get(sym, 0), 2),
                       "play_type": info.get("play_type"),
                       "thesis": scrub_arm(info.get("thesis") or info.get("rationale") or ""),
                       "falsification": scrub_arm(info.get("falsification") or "")})
    _append_jsonl(reg_entry, "prereg.jsonl", prereg)
    _write_jsonl_one(reg_entry, "decisions.jsonl",
                     {"date": asof, "stance": stance, "experience_note": scrub_arm(experience_note or ""),
                      "n_holdings": len(new_positions), "n_trades": len(trades),
                      "turnover_pct": round(100.0 * sum(t["value_cr"] for t in trades) / aum, 1) if aum else None,
                      "guardrail_notes": notes})

    # mark to market (the CITI daily fact sheet)
    sheet = af.fact_sheet(book, asof)
    af.save_daily(reg_entry["amc"], reg_entry["scheme"], sheet)

    cmp = compare_to_quant(final, ctx["_quant_targets"], cand_by_sym)
    summary = {"scheme": reg_entry["scheme"], "asof": asof, "n_holdings": len(new_positions),
               "n_trades": len(trades), "deployed_pct": round(100 * deployed_cr / aum, 1) if aum else None,
               "cash_pct": round(100 * book["cash_cr"] / aum, 1) if aum else None,
               "turnover_pct": round(100.0 * sum(t["value_cr"] for t in trades) / aum, 1) if aum else None,
               "cost_cr": round(cost_cr, 2), "vs_quant": cmp, "guardrail_notes": notes, "stance": stance}
    log(f"[live] {reg_entry['scheme']}: {len(new_positions)} names, {len(trades)} trades, "
        f"{summary['deployed_pct']}% deployed, active-share-vs-quant {cmp['active_share_pct']}%")
    return summary


def compare_to_quant(final, quant, cand_by_sym):
    """Active share + overlap + top deviations of the LLM book vs the rules-FM (quant) baseline."""
    syms = set(final) | set(quant)
    active = 0.5 * sum(abs(final.get(s, 0.0) - quant.get(s, 0.0)) for s in syms)
    overlap = sum(min(final.get(s, 0.0), quant.get(s, 0.0)) for s in syms)
    devs = sorted(((s, final.get(s, 0.0) - quant.get(s, 0.0)) for s in syms), key=lambda x: -abs(x[1]))[:8]
    return {
        "active_share_pct": round(100 * active, 1), "overlap_pct": round(100 * overlap, 1),
        "n_llm_only": sum(1 for s in final if s not in quant), "n_quant_only": sum(1 for s in quant if s not in final),
        "top_deviations": [{"sym": s, "name": cand_by_sym.get(s, {}).get("name"),
                            "llm_pct": round(100 * final.get(s, 0), 2), "quant_pct": round(100 * quant.get(s, 0), 2),
                            "delta_pct": round(100 * d, 2)} for s, d in devs],
    }


# ───────────────────────────────────────────────────────── small JSONL helpers (audit trail)
def _append_jsonl(reg_entry, fname, rows):
    if not rows:
        return
    d = af.scheme_dir(reg_entry["amc"], reg_entry["scheme"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, fname), "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _write_jsonl_one(reg_entry, fname, row):
    _append_jsonl(reg_entry, fname, [row])


# ───────────────────────────────────────────────────────── round driver (the 4 pilots)
PILOTS = [
    ("ICICI Prudential",       "Large Cap Fund"),
    ("SBI Mutual",             "Aggressive Hybrid Fund"),
    ("Aditya Birla Sun Life",  "Flexi Cap Fund"),
    ("Quant Mutual",           "Small Cap Fund"),
]
_FM_KEY = {"Large Cap Fund": "largecap", "Aggressive Hybrid Fund": "agghybrid",
           "Flexi Cap Fund": "flexicap", "Small Cap Fund": "smallcap"}


def amc_reg_entries(amc_substr, equity_only=True, min_aum_cr=200.0):
    """Every mandate-relevant scheme of ONE real AMC — the roster for a FULL digital-AMC firm (e.g.
    "Aditya Birla Sun Life" → all its equity/hybrid schemes, each becomes a paper book run by an FM
    desk under the AMC's CIO). This generalises the 4-scheme cross-AMC `pilot_reg_entries` to the
    per-AMC firm the North Star targets.

    equity_only=True keeps only schemes whose SEBI category has a real equity/hybrid mandate in
    `amc_firm.MANDATES` (so pure-debt / children / retirement schemes — which would wrongly inherit the
    default equity mandate — are excluded; they're named by the caller, not silently dropped).

    ONE DESK PER DISTINCT FUND (not per category): every distinct product is its own book, so the 14
    different thematic funds (PSU, Consumption, Pharma, …) each get a desk — but the Regular/Direct/
    Growth/IDCW PLANS of the SAME fund (identical holdings) collapse to one (largest-AUM kept), via a
    normalised scheme name. Returns the reg-entry list sorted by AUM desc."""
    import re as _re
    reg = af.registry(amcs=[amc_substr], min_aum_cr=min_aum_cr)

    def _norm(nm):                       # drop plan/option words so Reg vs Direct vs IDCW collapse
        s = _re.sub(r"[^A-Za-z0-9 ]", " ", str(nm or "").upper())
        drop = {"REGULAR", "DIRECT", "GROWTH", "IDCW", "DIVIDEND", "PLAN", "OPTION", "G", "R", "D",
                "REG", "PAYOUT", "REINVESTMENT", "FUND", "THE"}
        return " ".join(w for w in s.split() if w not in drop)

    best = {}
    for sch in reg.values():
        for s in sch.values():
            if equity_only and s.get("category") not in af.MANDATES:
                continue
            key = _norm(s.get("scheme"))
            if key and (key not in best or af._f(s["aum_cr"]) > af._f(best[key]["aum_cr"])):
                best[key] = s
    return sorted(best.values(), key=lambda s: -af._f(s["aum_cr"]))


def pilot_reg_entries(min_aum_cr=500.0):
    """The 4 pilot flagship schemes (largest-AUM match per (AMC, category)) — same set as replay_pilots."""
    reg = af.registry(amcs=[a for a, _c in PILOTS], min_aum_cr=min_aum_cr)
    out = []
    for amc_sub, cat in PILOTS:
        cands = [s for sch in reg.values() for s in sch.values()
                 if s["category"] == cat and amc_sub.lower() in (s["amc"] or "").lower()]
        if cands:
            out.append(max(cands, key=lambda s: af._f(s["aum_cr"])))
    return out


def prepare_round(asof_str, log=print):
    """Assemble + write the desk file for each pilot so the FM agents can Read them. Returns the manifest
    [{slug, scheme, amc, category, fm_key, path, n_candidates, n_held, aum_cr}] the Workflow consumes."""
    os.makedirs(DESK_DIR, exist_ok=True)
    manifest = []
    for re_ in pilot_reg_entries():
        ctx = prepare_desk(re_, asof_str, write=True)
        manifest.append({"slug": slug(re_["scheme"]), "scheme": re_["scheme"], "amc": re_["amc"],
                         "category": re_["category"], "fm_key": _FM_KEY.get(re_["category"], "flexicap"),
                         "path": ctx["_path"], "n_candidates": len(ctx["candidates"]),
                         "n_held": ctx["current_book"]["n_holdings"], "aum_cr": ctx["aum_cr"]})
        log(f"[prep] {re_['scheme']}: desk written ({len(ctx['candidates'])} candidates, {ctx['current_book']['n_holdings']} held)")
    json.dump({"asof": asof_str, "schemes": manifest},
              open(os.path.join(LIVE_DIR, "round_manifest.json"), "w", encoding="utf-8"), indent=1)
    return manifest


def apply_round(asof_str, decisions, cio=None, log=print):
    """`decisions` = {slug: {stance, experience_note, tickets:[{sym, action, target_pct, play_type,
    rationale, thesis, falsification}]}} from the Workflow. Re-assembles each desk, guardrails + executes
    each FM's targets onto the book, marks, and writes the round summary. Returns the summaries list."""
    by_slug = {slug(re_["scheme"]): re_ for re_ in pilot_reg_entries()}
    summaries = []
    for sl, re_ in by_slug.items():
        dec = decisions.get(sl) or {}
        tickets = dec.get("tickets") or []
        proposed, rationales = {}, {}
        for t in tickets:
            sym = t.get("sym")
            tp = af._f(t.get("target_pct"))
            if not sym or tp <= 0:
                continue
            proposed[sym] = tp / 100.0
            rationales[sym] = {"action": t.get("action"), "play_type": (t.get("play_type") or "").lower() or None,
                               "rationale": t.get("rationale"), "thesis": t.get("thesis"),
                               "falsification": t.get("falsification")}
        ctx = prepare_desk(re_, asof_str, write=False)
        if not proposed:
            log(f"[apply] {re_['scheme']}: no LLM tickets — keeping quant baseline")
            proposed = dict(ctx["_quant_targets"])
        s = apply_decision(re_, asof_str, proposed, rationales,
                           stance=dec.get("stance"), experience_note=dec.get("experience_note"),
                           ctx=ctx, log=log)
        s["book_thesis"] = scrub_arm(dec.get("book_thesis") or "")
        summaries.append(s)
    round_doc = {"asof": asof_str, "schemes": summaries, "cio": cio,
                 "discipline": "LLM FMs decide; deterministic guardrail enforces mandate/liquidity/floor; "
                               "paper-only; no look-ahead; raw ARM never persisted."}
    json.dump(round_doc, open(os.path.join(LIVE_DIR, f"round_{asof_str}.json"), "w", encoding="utf-8"),
              indent=1, default=str)
    json.dump(round_doc, open(os.path.join(LIVE_DIR, "round_latest.json"), "w", encoding="utf-8"),
              indent=1, default=str)
    log(f"[round] {len(summaries)} schemes rebalanced → {os.path.join(LIVE_DIR, f'round_{asof_str}.json')}")
    return round_doc
