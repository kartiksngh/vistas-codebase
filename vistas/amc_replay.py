"""vistas/amc_replay.py — the deterministic rules-FM HISTORICAL REPLAY engine (#68).

Walks a virtual scheme book FORWARD through history. At each monthly rebalance it reconstructs a
POINT-IN-TIME, survivorship-clean universe (no look-ahead), scores it by our validated ARM edge
(analyst-revision momentum), water-fills target weights under the scheme's SEBI mandate + a hard
liquidity cap (the SAME water-fill as the live constructor — amc_firm.waterfill), trades into the
target charging a realistic cost, then marks the book to market every trading day to produce a true
total-return NAV series. It scores the resulting track vs the REAL NSE benchmark TR index AND the
REAL scheme's actual AMFI NAV, and decomposes realized skill via the Fundamental Law of Active
Management (IR = IC·√BR·TC).

★ WHY THE TRACK IS SURVIVORSHIP-CLEAN AT THE UNIVERSE LEVEL (audited 2026-06-26):
  • The PRICE panel (vistas.stocks.load, bhavcopy-sourced) RETAINS every delisted name's history —
    a stock alive in 2015 and dead today still carries its 2015 prices (NaN only AFTER it delists).
    So we anchor the UNIVERSE on the price panel: at each asof the universe = symbols ACTIVELY
    TRADING as-of that date (a fresh price within ACTIVE_DAYS trading rows). This INCLUDES
    alive-then-dead-now names → no universe-level survivor bias. We COUNT those names each rebalance
    (`n_dead_included`) so the cleanliness is visible, not asserted.
  • ARM is attached as a SCORE where available (neutral 50.0 where not). The one known leak is that
    idmap.resolve(isin) is static-today (its `asof` is a no-op), so a RENAMED name's ARM can be
    smeared onto its successor — a minor SCORING imperfection on a clean universe, NOT a selection
    bias. We report ARM coverage each rebalance so the residual is measurable.

★ NO LOOK-AHEAD: every input is filtered to ≤ asof (prices, ARM revisions, trailing turnover). The
  only forward data used is the NEXT-rebalance return, and ONLY to MEASURE realized IC after the
  fact — never to select or weight.

★ LICENSING (LSEG StarMine ARM is licensed IP): NO raw per-stock ARM value is ever persisted. The
  saved artifacts hold only OUR derived decisions (weights, coarse play-type tags) and AGGREGATE
  statistics (mean IC, sector mixes, coverage %). Exact ARM lives only in-memory during the run.

CONVENTIONS (so every number is reproducible — KV reporting rule):
  NAV         seeded at 100 on the inception rebalance; NAV(t) = 100 · portfolio_value(t)/AUM0,
              portfolio_value = Σ qty·price(t) + cash. Total-return (prices are adjusted TR closes).
  returns     simple daily; ppy = 252. CAGR = (end/start)^(365/calendar_days) − 1.
  vol         std(daily r)·√252 ; Sharpe = (mean(r)·252 − rf)/vol, rf = 0 ; MaxDD = min(level/cummax − 1).
  IR          = excess CAGR (book − benchmark) / tracking error, TE = std(r_book − r_bench)·√252.
  cost        per-SIDE transaction cost on traded notional, COST_BPS bps; charged to cash at each
              rebalance (a real drag on NAV). Report gross is NOT separately tracked — NAV is net.
  size bucket trailing-21d median traded value (₹cr) RANK as-of date: top LARGE_TOP = large,
              next to MID_TOP = mid, rest = small. A point-in-time liquidity/size proxy (true
              point-in-time market cap isn't available; current-mcap would be look-ahead).
  liquidity   per-name cap = min(mandate single-name cap, LIQ_DAYS × trailing median turnover / AUM).
"""

import os
import re
import json
import math
import datetime as _dt

import numpy as np
import pandas as pd

from . import amc_firm as af
from . import data as _data
from . import idmap as _idm

DEFAULT_START = "2015-01-01"   # broad ARM coverage + direct-growth NAV exists; ~11y track to 2026
ACTIVE_DAYS = 15               # a symbol is "trading as-of asof" if it has a price in the last N rows ≤ asof
COST_BPS = 15.0                # per-side transaction cost on traded notional (bps); realistic IN large/mid
LARGE_TOP = 100                # turnover-rank ≤ this = large-cap bucket
MID_TOP = 250                  #            ≤ this = mid-cap bucket; rest = small-cap
TURN_WINDOW = 21               # trailing window (rows) for median turnover
SPIKE_RET = 0.60               # a >60% single-day price move is below no real NSE circuit → an
                               # adjustment/data glitch; despike it (mask + carry prior price forward)
MIN_PRICE = 2.0                # skip names priced below ₹2 (penny/glitch tail — uninvestable + unstable)
MIN_TURN_CR = 0.25             # skip names whose trailing-median traded value < ₹0.25 cr/day (no liquidity
                               # to assess/trade; also where adjustment glitches concentrate)
RET_CLIP = 0.40                # winsorize each name's daily return at ±40% when marking the book — a
                               # persistent bad-adjustment LEVEL step (which single-day despiking only
                               # delays) can't then inflate NAV. Real NSE daily moves rarely exceed this.


# ───────────────────────────────────────────────────────── cached substrate
_C = {"panel": None, "ff": None, "sym2isin": None, "sym2name": None, "sector": None,
      "turn_med": None, "last_valid": None}


def _panel():
    if _C["panel"] is None:
        _C["panel"] = af._prices()
    return _C["panel"]


def _last_valid():
    """{symbol: last date it ever has a price} — precomputed once, so the 'delisted-but-included'
    diagnostic is an O(1) lookup per name per rebalance instead of a full-column scan."""
    if _C["last_valid"] is None:
        panel = _panel()
        _C["last_valid"] = {c: panel[c].last_valid_index() for c in panel.columns}
    return _C["last_valid"]


def _panel_ff():
    """Price panel DESPIKED then forward-filled, used ONLY for pricing (marking held positions,
    pv, deploy). A single-day move > SPIKE_RET is treated as an adjustment/data glitch — the spike
    day (and its mirror revert) are masked and the prior good price carried forward, so one bad tick
    at a month-end can't inflate the book and compound. Forward-fill then lets a held name keep its
    last good price until the next rebalance force-sells it. NB: 'actively trading' detection uses the
    RAW panel (un-ffilled), never this — so despiking/ffill can't resurrect a delisted name."""
    if _C["ff"] is None:
        raw = _panel()
        bad = raw.pct_change().abs() > SPIKE_RET
        _C["ff"] = raw.mask(bad).ffill()
    return _C["ff"]


def _identity_maps():
    """{symbol_upper: isin} and {symbol_upper: name} from the idmap reverse index (whole lineage,
    so a historical symbol still resolves to its ISIN/name)."""
    if _C["sym2isin"] is None:
        idx = _idm.symbol_index()      # {symbol_upper: {isin, isins, name, vst_id}}
        _C["sym2isin"] = {s: r.get("isin") for s, r in idx.items()}
        _C["sym2name"] = {s: r.get("name") for s, r in idx.items()}
    return _C["sym2isin"], _C["sym2name"]


def _sector_map():
    """{symbol_upper: desk_sector} = the 11 analyst-desk macro-sectors (current snapshot applied
    through history — the same convention arm_sectors uses). Best-effort: {} if unavailable, in
    which case sector caps are NOT enforced (flagged in the scorecard)."""
    if _C["sector"] is None:
        try:
            from . import arm_sectors as _asec
            rows = _asec._load_rows(None)
            sym_desk, _mcap = _asec._sector_index(rows)
            _C["sector"] = {str(s).upper(): d for s, d in sym_desk.items()}
        except Exception:
            _C["sector"] = {}
    return _C["sector"]


def _turn_med():
    """DataFrame[date × symbol] of trailing-TURN_WINDOW MEDIAN daily traded value (₹cr), built once
    from amc_firm's bhav turnover panel and ALIGNED (reindexed + forward-filled) onto the price
    calendar — so `Tmed.loc[asof]` returns every symbol's trailing-median turnover as-of `asof` in
    one row lookup (NOT DataFrame.asof, which requires ALL columns non-NaN and silently returns
    all-NaN with a wide panel). Used for size-bucket ranking + the universe liquidity floor. {} → empty."""
    if _C["turn_med"] is None:
        g = af._turnover_panel()       # {sym: (dates_list, turnover_cr_list)}
        if not g:
            _C["turn_med"] = pd.DataFrame()
            return _C["turn_med"]
        cols = {}
        for sym, (dates, vals) in g.items():
            try:
                s = pd.Series(vals, index=pd.to_datetime(dates))
                s = s[~s.index.duplicated(keep="last")].sort_index()
                cols[sym] = s
            except Exception:
                continue
        if not cols:
            _C["turn_med"] = pd.DataFrame()
            return _C["turn_med"]
        T = pd.DataFrame(cols).sort_index()
        med = T.rolling(TURN_WINDOW, min_periods=5).median()
        _C["turn_med"] = med.reindex(_panel().index, method="ffill")   # align to price calendar
    return _C["turn_med"]


# ───────────────────────────────────────────────────────── small stats (documented formulas)
def _cagr(level):
    level = level.dropna()
    if len(level) < 2:
        return float("nan")
    days = (level.index[-1] - level.index[0]).days
    if days <= 0 or level.iloc[0] <= 0:
        return float("nan")
    return (level.iloc[-1] / level.iloc[0]) ** (365.0 / days) - 1.0


def _vol(r, ppy=252):
    return float(r.std() * math.sqrt(ppy)) if len(r) > 1 else float("nan")


def _sharpe(r, ppy=252, rf=0.0):
    v = _vol(r, ppy)
    return float((r.mean() * ppy - rf) / v) if v and v == v and v != 0 else float("nan")


def _maxdd(level):
    level = level.dropna()
    if len(level) < 2:
        return float("nan")
    return float((level / level.cummax() - 1.0).min())


def _spearman(x, y):
    """Spearman rank correlation (manual, no scipy). Returns nan if < 3 paired points."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 3:
        return float("nan")
    rx = pd.Series(x).rank().values
    ry = pd.Series(y).rank().values
    return _pearson(rx, ry)


def _pearson(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


# ───────────────────────────────────────────────────────── benchmark selection
def _norm_idx(s):
    return re.sub(r"\b(TRI|TR|PRI|INDEX|TOTAL RETURN)\b", "", str(s or "").upper()).strip()


def _bench_for(reg_entry):
    """Pick the best real NSE TR index name (present in data.available()) for this scheme: match the
    scheme's stated benchmark string, else fall back by the mandate's market-cap buckets."""
    avail = list(_data.available())
    target = _norm_idx(reg_entry.get("benchmark"))
    best, blen = None, -1
    for a in avail:
        na = _norm_idx(a)
        if na and (na == target or (target and (na in target or target in na))):
            if len(na) > blen:
                best, blen = a, len(na)
    if best:
        return best
    buckets = reg_entry.get("mandate", {}).get("buckets", [])
    fb = ("NIFTY 100" if buckets == ["large"] else
          "NIFTY SMALLCAP 250" if buckets == ["small"] else
          "NIFTY MIDCAP 150" if buckets == ["mid"] else "NIFTY 500")
    if fb in avail:
        return fb
    return "NIFTY 500" if "NIFTY 500" in avail else (avail[0] if avail else None)


# ───────────────────────────────────────────────────────── point-in-time universe
def point_in_time_universe(asof_ts):
    """The survivorship-clean investable universe AS-OF `asof_ts` (a Timestamp in the panel index):
    every symbol actively trading on/near asof, tagged with last price, trailing-median turnover,
    size bucket (turnover rank), ARM score (or neutral 50), sector, and whether it later delists.
    Returns (list[dict], meta)."""
    panel = _panel(); pf = _panel_ff()
    sym2isin, sym2name = _identity_maps()
    sector_map = _sector_map()
    Tmed = _turn_med()
    lastv = _last_valid()
    panel_last = panel.index[-1]

    sub = panel.loc[:asof_ts]
    if not len(sub):
        return [], {"n": 0}
    recent = sub.tail(ACTIVE_DAYS)
    active = [c for c in recent.columns if recent[c].notna().any()]
    if not active:
        return [], {"n": 0}
    row_ff = pf.loc[asof_ts]                       # last known price ≤ asof for every symbol
    asof_str = asof_ts.strftime("%Y-%m-%d")

    # trailing-median turnover as-of asof for all symbols (single aligned row lookup)
    turn_asof = Tmed.loc[asof_ts] if (len(Tmed) and asof_ts in Tmed.index) else None

    uni = []
    for sym in active:
        px = row_ff.get(sym)
        if px is None or not (px == px) or px < MIN_PRICE:
            continue
        turn = float(turn_asof.get(sym)) if (turn_asof is not None and sym in turn_asof.index
                                             and turn_asof.get(sym) == turn_asof.get(sym)) else None
        # require enough liquidity to size/trade the name (also drops the dead/glitch-prone micro tail)
        if turn is None or turn < MIN_TURN_CR:
            continue
        isin = sym2isin.get(sym)
        arm = af.current_arm(isin, asof_str) if isin else None
        # delisted-but-included diagnostic: this name's last EVER price is well before the panel end
        last_px_date = lastv.get(sym)
        dead_later = bool(last_px_date is not None and last_px_date < (panel_last - pd.Timedelta(days=30)))
        uni.append({
            "sym": sym, "isin": isin, "name": sym2name.get(sym) or sym,
            "px": float(px), "turn": turn, "arm": arm,
            "score": float(arm) if arm is not None else 50.0,
            "sector": sector_map.get(sym, "Unclassified"),
            "dead_later": dead_later,
        })
    if not uni:
        return [], {"n": 0}

    # size buckets by trailing-turnover rank (no turnover → ranked last = small)
    have_turn = any(u["turn"] is not None for u in uni)
    if have_turn:
        order = sorted(uni, key=lambda u: (-(u["turn"] if u["turn"] is not None else -1.0)))
        for i, u in enumerate(order):
            u["bucket"] = "large" if i < LARGE_TOP else ("mid" if i < MID_TOP else "small")
    else:
        for u in uni:
            u["bucket"] = "large"     # no turnover data → don't bucket-filter (flagged)

    n_arm = sum(1 for u in uni if u["arm"] is not None)
    meta = {"n": len(uni), "n_arm": n_arm, "arm_cov": round(100.0 * n_arm / len(uni), 1),
            "n_dead_included": sum(1 for u in uni if u["dead_later"]),
            "have_turnover": have_turn,
            "buckets": {b: sum(1 for u in uni if u["bucket"] == b) for b in ("large", "mid", "small")}}
    return uni, meta


# ───────────────────────────────────────────────────────── target construction (rules-FM, no LLM)
def construct_targets(reg_entry, universe, asof_ts, aum_now, brain_id=None):
    """Deploy `aum_now` (₹cr, the book's current value) into mandate+liquidity-constrained target
    WEIGHTS as-of asof, scored by the desk's MULTI-FORCE FM brain (NOT a single ARM clone). Returns
    (targets {sym: {w, px, sector, isin, name, arm}}, cand [the ranked candidate set, for IC/TC
    measurement], info). No look-ahead: the brain reads only prices/ARM/EPS ≤ asof.

    LICENSING: raw per-stock ARM is used IN MEMORY by the brain to score; only derived weights /
    play-types / brain-id / aggregate IC·TC are persisted downstream (save_replay is unchanged)."""
    m = reg_entry["mandate"]
    asof_str = asof_ts.strftime("%Y-%m-%d")
    bid = brain_id or reg_entry.get("brain") or af.brain_for_mandate(reg_entry.get("category"), m)
    equity_target = 0.95
    emax = m.get("equity_max")
    if emax:                                       # hybrid/DAA: target the middle of the equity band
        equity_target = min(equity_target, round((m["equity_min"] + emax) / 2.0, 4))

    buckets = set(m.get("buckets", ["large", "mid", "small"]))
    cand = [u for u in universe if u["bucket"] in buckets]
    if not cand:                                   # mandate bucket empty as-of → don't starve the book
        cand = list(universe)

    # ── SCORE the candidate set with the desk's FM brain (overwrites the neutral 50 default with a
    # multi-force score: ARM + momentum + value, combined per the brain). Mutates u["score"] in place.
    bid, _bdiag = af.score_universe(cand, asof_str, bid)

    cand.sort(key=lambda u: (-u["score"], -(u["turn"] or 0.0)))
    sel = cand[:min(m["n_hi"], len(cand))]

    SECTOR_FREE = "Unclassified"
    for u in sel:
        cap = m["max_pos"]
        lc = af.liquidity_cap_cr(u["sym"], aum_now, asof_str)   # LIQ_DAYS × trailing median turnover
        if lc is not None and aum_now > 0:
            cap = min(cap, lc / aum_now)
        u["cap"] = max(0.0, cap)
    af.waterfill(sel, equity_target, m["max_sector"], SECTOR_FREE)

    targets = {}
    for u in sel:
        if u["w"] > 1e-6:
            targets[u["sym"]] = {"w": u["w"], "px": u["px"], "sector": u["sector"],
                                 "isin": u["isin"], "name": u["name"], "arm": u["arm"]}
    info = {"n_cand": len(cand), "n_sel": len(sel), "n_held": len(targets),
            "equity_target": equity_target, "brain": bid,
            "deployed": round(sum(t["w"] for t in targets.values()), 4)}
    return targets, cand, info


def _tc_sample(cand, targets, equity_target):
    """Transfer coefficient proxy at one rebalance = correlation between the ACTUAL target weights
    and the UNCONSTRAINED ideal weights (score-proportional, scaled to equity_target), across the
    candidate set. Measures how much of the signal survives the long-only + cap constraints."""
    if not cand:
        return float("nan")
    tot = sum(u["score"] for u in cand) or 1.0
    ideal = [equity_target * u["score"] / tot for u in cand]
    actual = [targets.get(u["sym"], {}).get("w", 0.0) for u in cand]
    return _pearson(actual, ideal)


# ───────────────────────────────────────────────────────── the walk-forward replay
def _month_end_trading_days(cal):
    """Last trading day of each calendar month present in `cal` (a sorted DatetimeIndex)."""
    s = pd.Series(cal, index=cal)
    return list(s.groupby([cal.year, cal.month]).last().values)


def replay(reg_entry, start=DEFAULT_START, end=None, cost_bps=COST_BPS, brain_id=None, log=print):
    """Walk the virtual book forward; return (nav, monthly, scorecard, diag). `nav` is a daily NAV
    Series (base 100 at inception). `monthly` is a compact per-rebalance summary list (NO raw ARM).

    `brain_id` selects the desk's MULTI-FORCE FM brain (default = brain_for_mandate by the scheme's
    SEBI category — so each pilot scheme runs a DISTINCT philosophy, not one ARM clone).

    RETURN-SPACE marking: the book is a set of WEIGHTS that compound by each name's winsorized daily
    return (±RET_CLIP). This never divides by a price level, so a persistent bad-adjustment level step
    can't inflate NAV. Each trading day: apply the day's clipped returns to the current (drifting)
    weights — including the rebalance day's own return BEFORE re-weighting — then rebalance at the
    close if scheduled, charging a per-side cost as a NAV haircut on the traded fraction."""
    panel = _panel(); pf = _panel_ff()
    aum0 = af._f(reg_entry["aum_cr"])
    if aum0 <= 0:
        raise ValueError("scheme AUM is zero — cannot seed a book")
    brain_id = brain_id or reg_entry.get("brain") or af.brain_for_mandate(reg_entry.get("category"), reg_entry.get("mandate"))
    cal = panel.index
    cal = cal[(cal >= pd.Timestamp(start)) & (cal <= pd.Timestamp(end))] if end else cal[cal >= pd.Timestamp(start)]
    if len(cal) < 60:
        raise ValueError("not enough trading days in the requested window")
    rebset = set(pd.Timestamp(d) for d in _month_end_trading_days(cal))
    if not rebset:
        raise ValueError("no rebalance dates")
    R = pf.pct_change().clip(-RET_CLIP, RET_CLIP)   # winsorized daily returns (level-glitch-proof)

    nav = 100.0
    w = {}                               # {sym: weight fraction of NAV}; cash = 1 − Σw
    pos_meta = {}
    live = False                         # series starts at the first rebalance (no flat-cash prefix)
    nav_idx, nav_val = [], []
    monthly = []
    ic_samples, tc_samples, turn_samples = [], [], []
    pending_ic = None                    # {scores, px} from the prior rebalance, to realize IC here
    cov_arm, cov_dead, cov_uni = [], [], []
    prev_day = None

    for t in cal:
        # ---- 1) mark the day: apply each held name's clipped return to its (drifting) weight
        if w and prev_day is not None:
            rt = R.loc[t].reindex(list(w.keys())).fillna(0.0)
            rb = float(sum(w[k] * rt[k] for k in w))
            nav *= (1.0 + rb)
            if (1.0 + rb) > 0:
                w = {k: w[k] * (1.0 + rt[k]) / (1.0 + rb) for k in w}
        prev_day = t

        # ---- 2) rebalance at this close if scheduled (no look-ahead — only data ≤ t)
        if t in rebset:
            pv_cr = aum0 * nav / 100.0            # current book ₹cr (drives liquidity caps + summary)
            if pending_ic is not None:           # realize the PRIOR rebalance's IC with the now-known fwd return
                scores, px0 = pending_ic["scores"], pending_ic["px"]
                xs, ys = [], []
                for sym, sc in scores.items():
                    p1 = pf.loc[t, sym] if sym in pf.columns else None
                    p0 = px0.get(sym)
                    if p0 and p1 == p1 and p1 and p0 > 0:
                        xs.append(sc); ys.append(p1 / p0 - 1.0)
                ic_samples.append(_spearman(xs, ys))
            uni, umeta = point_in_time_universe(t)
            if not uni:
                pending_ic = None
                monthly.append({"date": t.strftime("%Y-%m-%d"), "nav": round(nav, 4),
                                "n_holdings": len(w), "note": "no investable universe"})
            else:
                targets, cand, info = construct_targets(reg_entry, uni, t, pv_cr, brain_id=brain_id)
                tc_samples.append(_tc_sample(cand, targets, info["equity_target"]))
                cov_arm.append(umeta["arm_cov"]); cov_dead.append(umeta["n_dead_included"]); cov_uni.append(umeta["n"])
                w_new = {sym: tt["w"] for sym, tt in targets.items()}
                dturn = sum(abs(w_new.get(k, 0.0) - w.get(k, 0.0)) for k in (set(w) | set(w_new)))
                nav *= (1.0 - dturn * cost_bps / 1e4)   # per-side cost on the traded fraction
                turn_samples.append(dturn / 2.0)
                w = w_new
                pos_meta = {sym: {"name": tt["name"], "sector": tt["sector"],
                                  "play_type": af._play_type(tt["arm"])} for sym, tt in targets.items()}
                pending_ic = {"scores": {u["sym"]: u["score"] for u in cand},
                              "px": {u["sym"]: u["px"] for u in cand}}
                live = True
                monthly.append(_monthly_summary(t, nav, w, pos_meta, umeta, info, dturn / 2.0))

        if live:
            nav_idx.append(t); nav_val.append(nav)

    nav = pd.Series(nav_val, index=pd.DatetimeIndex(nav_idx)).sort_index()
    nav = nav[~nav.index.duplicated(keep="last")]

    diag = {"aum0_cr": round(aum0, 1), "start": str(nav.index[0].date()), "end": str(nav.index[-1].date()),
            "n_rebalances": len(rebset), "cost_bps": cost_bps, "brain": brain_id,
            "brain_thesis": af.BRAINS.get(brain_id, {}).get("thesis"),
            "avg_universe": round(float(np.nanmean(cov_uni)), 0) if cov_uni else None,
            "avg_arm_cov_pct": round(float(np.nanmean(cov_arm)), 1) if cov_arm else None,
            "avg_dead_included": round(float(np.nanmean(cov_dead)), 1) if cov_dead else None,
            "avg_holdings": round(float(np.nanmean([m.get("n_holdings", 0) for m in monthly])), 1),
            "avg_oneway_turnover_pct": round(100.0 * float(np.nanmean(turn_samples)), 2) if turn_samples else None}
    score = scorecard(nav, reg_entry, ic_samples, tc_samples, turn_samples, monthly, log=log)
    return nav, monthly, score, diag


def _monthly_summary(rd, nav, w, pos_meta, umeta, info, turn_oneway):
    """Compact per-rebalance record from WEIGHTS (NO raw per-stock ARM — only derived weights/tags +
    aggregates). pct_assets is the weight; cash% = 100 − Σweights."""
    eqw = sum(w.values())
    top = [{"name": pos_meta[s]["name"], "sym": s, "pct": round(100.0 * wt, 2)}
           for s, wt in sorted(w.items(), key=lambda kv: -kv[1])[:8]]
    sect, plays = {}, {}
    for s, wt in w.items():
        sect[pos_meta[s]["sector"]] = sect.get(pos_meta[s]["sector"], 0.0) + wt
        plays[pos_meta[s]["play_type"]] = plays.get(pos_meta[s]["play_type"], 0.0) + wt
    sectors = sorted(sect.items(), key=lambda kv: -kv[1])
    return {
        "date": rd.strftime("%Y-%m-%d"), "nav": round(nav, 4), "n_holdings": int(len(w)),
        "equity_pct": round(100.0 * eqw, 2), "cash_pct": round(100.0 * (1.0 - eqw), 2),
        "oneway_turnover_pct": round(100.0 * turn_oneway, 2),
        "universe_n": umeta["n"], "arm_coverage_pct": umeta["arm_cov"],
        "n_dead_included": umeta["n_dead_included"], "deployed_pct": round(100.0 * info["deployed"], 2),
        "top_holdings": top,
        "top_sectors": [{"sector": s, "pct": round(100.0 * wt, 2)} for s, wt in sectors[:6]],
        "play_mix": {k: round(100.0 * v, 2) for k, v in plays.items()},
    }


# ───────────────────────────────────────────────────────── benchmark NAV (a full series, for the chart)
def benchmark_nav_series(nav, reg_entry):
    """Build the scheme's benchmark as a NAV *series* on the BOOK's own date axis, rebased to 100 at
    the book's start — so the site can overlay a benchmark LINE on the NAV chart (today only bench
    SCALARS — CAGR/IR — exist). Returns a pandas Series (date-indexed) or None if no benchmark resolves.

    Conventions (reproducible — KV reporting rule), identical to the scorecard's benchmark leg so the
    line and the scalars agree:
      • SOURCE   the SAME real NSE TR index the scorecard scores against (`_bench_for(reg_entry)` →
                 data.get_level_frame([name], measure='TR')). A total-return index → dividends reinvested.
      • AXIS     intersect the benchmark's trading days with the book's NAV index, so both series share
                 one date axis (the chart overlays cleanly; no forward-filling a non-trading day).
      • REBASE   level → 100 · level(t)/level(t0), where t0 = the FIRST common date (the book's
                 inception). The book NAV is base-100 at the same t0, so the two lines start together.
      • SURVIVORSHIP / LOOK-AHEAD  an NSE published index has no survivor bias of its own, and rebasing
                 to the book's start uses only the index's own past levels — no forward data enters.
    """
    bname = _bench_for(reg_entry)
    if not bname:
        return None
    try:
        bf = _data.get_level_frame([bname], measure="TR")
        bser = bf[bname].dropna() if (bf is not None and bname in bf.columns) else None
    except Exception:
        bser = None
    if bser is None or len(bser) < 10:
        return None
    bser.index = pd.to_datetime(bser.index)
    common = nav.index.intersection(bser.index)
    if len(common) < 2:
        return None
    b2 = bser.reindex(common).dropna()
    if len(b2) < 2 or b2.iloc[0] <= 0:
        return None
    return (100.0 * b2 / float(b2.iloc[0])).rename("nav")


# ───────────────────────────────────────────────────────── scorecard (vs benchmark + real scheme)
def _real_scheme_nav(reg_entry):
    """The real scheme's actual AMFI NAV series, matched by name, or None. Survivorship-free panel."""
    try:
        from . import funds_nav as _fn
        wide = _fn.load_named()
    except Exception:
        return None, None
    if wide is None or not len(wide.columns):
        return None, None
    want = _norm_name(reg_entry.get("scheme"))
    if not want:
        return None, None
    best, bscore = None, 0
    wt = set(want.split())
    for col in wide.columns:
        ct = set(_norm_name(col).split())
        if not ct:
            continue
        inter = len(wt & ct)
        if inter > bscore and inter >= max(2, len(wt) - 2):
            bscore, best = inter, col
    if best is None:
        return None, None
    s = wide[best].dropna()
    s.index = pd.to_datetime(s.index)
    return s.sort_index(), best


def _norm_name(s):
    s = str(s or "").upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    drop = {"FUND", "DIRECT", "REGULAR", "GROWTH", "PLAN", "OPTION", "IDCW", "DIVIDEND",
            "SCHEME", "OPEN", "ENDED", "EQUITY", "THE", "AN", "OF"}
    return " ".join(w for w in s.split() if w not in drop)


def scorecard(nav, reg_entry, ic_samples, tc_samples, turn_samples, monthly, log=print):
    """Score the replay NAV vs the real benchmark TR index AND the real scheme NAV, and decompose
    realized skill via the Fundamental Law (IR = IC·√BR·TC). All AGGREGATE stats (no per-stock ARM).
    Every metric here is defined in the module docstring CONVENTIONS block."""
    out = {"window": {"start": str(nav.index[0].date()), "end": str(nav.index[-1].date()),
                      "years": round((nav.index[-1] - nav.index[0]).days / 365.25, 2)}}
    rn = nav.pct_change().dropna()
    out["book"] = {"cagr_pct": round(100 * _cagr(nav), 2), "vol_pct": round(100 * _vol(rn), 2),
                   "sharpe": round(_sharpe(rn), 2), "maxdd_pct": round(100 * _maxdd(nav), 2),
                   "final_nav": round(float(nav.iloc[-1]), 2)}

    # ---- vs the real NSE benchmark TR index
    bname = _bench_for(reg_entry)
    out["benchmark_name"] = bname
    if bname:
        try:
            bf = _data.get_level_frame([bname], measure="TR")
            bser = bf[bname].dropna() if (bf is not None and bname in bf.columns) else None
        except Exception:
            bser = None
        if bser is not None and len(bser) > 10:
            bser.index = pd.to_datetime(bser.index)
            common = nav.index.intersection(bser.index)
            if len(common) > 30:
                n2 = nav.reindex(common); b2 = bser.reindex(common)
                rb = b2.pct_change().dropna(); rn2 = n2.pct_change().reindex(rb.index)
                te = float((rn2 - rb).std() * math.sqrt(252))
                ex_cagr = _cagr(n2) - _cagr(b2)
                beta = float(np.cov(rn2.dropna(), rb.reindex(rn2.dropna().index))[0, 1] / rb.var()) if rb.var() else float("nan")
                up = float(rn2[rb > 0].sum() / rb[rb > 0].sum()) if rb[rb > 0].sum() else float("nan")
                dn = float(rn2[rb < 0].sum() / rb[rb < 0].sum()) if rb[rb < 0].sum() else float("nan")
                out["benchmark"] = {
                    "cagr_pct": round(100 * _cagr(b2), 2), "vol_pct": round(100 * _vol(rb), 2),
                    "maxdd_pct": round(100 * _maxdd(b2), 2),
                    "excess_cagr_pct": round(100 * ex_cagr, 2),
                    "tracking_error_pct": round(100 * te, 2),
                    "info_ratio": round(ex_cagr / te, 2) if te else None,
                    "beta": round(beta, 2), "up_capture": round(up, 2), "down_capture": round(dn, 2),
                }

    # ---- vs the real scheme's actual NAV (did our virtual FM beat the real FM?)
    rs, matched = _real_scheme_nav(reg_entry)
    if rs is not None:
        common = nav.index.intersection(rs.index)
        if len(common) > 30:
            n2 = nav.reindex(common); r2 = rs.reindex(common)
            out["real_scheme"] = {
                "matched_name": matched,
                "real_cagr_pct": round(100 * _cagr(r2), 2),
                "book_cagr_pct": round(100 * _cagr(n2), 2),
                "book_minus_real_cagr_pct": round(100 * (_cagr(n2) - _cagr(r2)), 2),
                "overlap_years": round((common[-1] - common[0]).days / 365.25, 2),
            }
    else:
        out["real_scheme"] = {"matched_name": None, "note": "no AMFI NAV match for this scheme name"}

    # ---- Fundamental Law of Active Management: IR = IC·√BR·TC  (the evaluative lens KV wants)
    ic = float(np.nanmean(ic_samples)) if ic_samples else float("nan")
    ic_arr = np.array([x for x in ic_samples if x == x])
    ic_t = float(ic / (ic_arr.std() / math.sqrt(len(ic_arr)))) if len(ic_arr) > 2 and ic_arr.std() else float("nan")
    tc = float(np.nanmean(tc_samples)) if tc_samples else float("nan")
    rebals_per_year = 12.0
    avg_n = float(np.nanmean([m.get("n_holdings", 0) for m in monthly if m.get("n_holdings")])) or 0.0
    BR_upper = avg_n * rebals_per_year      # UPPER bound: treats each name each month as independent
    implied_ir = ic * math.sqrt(BR_upper) * tc if (ic == ic and tc == tc and BR_upper > 0) else float("nan")
    out["fundamental_law"] = {
        "ic_mean": round(ic, 4), "ic_tstat": round(ic_t, 2) if ic_t == ic_t else None,
        "transfer_coefficient": round(tc, 3),
        "breadth_per_year_UPPER": round(BR_upper, 0),
        "implied_IR_UPPER": round(implied_ir, 2) if implied_ir == implied_ir else None,
        "realized_IR_vs_bench": out.get("benchmark", {}).get("info_ratio"),
        "note": ("IC = mean cross-sectional Spearman(ARM score, next-rebalance return) over the "
                 "candidate universe; TC = mean corr(actual target weights, unconstrained "
                 "score-weights); BR is an UPPER bound (monthly holdings are NOT independent — true "
                 "breadth is far lower), so implied_IR is an upper bound on what the signal could "
                 "deliver, to be read against the realized IR."),
    }
    return out


# ───────────────────────────────────────────────────────── persistence (git-tracked audit; no raw ARM)
def save_replay(reg_entry, nav, monthly, score, diag):
    d = os.path.join(af.scheme_dir(reg_entry["amc"], reg_entry["scheme"]), "replay")
    os.makedirs(d, exist_ok=True)
    nav.to_frame("nav").to_csv(os.path.join(d, "nav.csv"), index_label="date")
    # benchmark NAV series on the book's date axis (rebased to 100 at the book start) — lets the site
    # overlay a benchmark line on the NAV chart. Survivorship-clean (NSE TR index) + look-ahead-free.
    bnav = benchmark_nav_series(nav, reg_entry)
    if bnav is not None and len(bnav):
        bnav.to_frame("nav").to_csv(os.path.join(d, "benchmark_nav.csv"), index_label="date")
    with open(os.path.join(d, "scorecard.json"), "w", encoding="utf-8") as f:
        json.dump({"scheme": reg_entry["scheme"], "amc": reg_entry["amc"],
                   "category": reg_entry["category"], "diag": diag, "scorecard": score},
                  f, indent=1, default=str)
    with open(os.path.join(d, "monthly_summary.json"), "w", encoding="utf-8") as f:
        json.dump(monthly, f, indent=1, default=str)
    return d


# ───────────────────────────────────────────────────────── driver
def run(amc_sub, category, start=DEFAULT_START, end=None, min_aum_cr=500.0, brain_id=None, save=True, log=print):
    """Build the registry, pick the flagship (largest-AUM) scheme of `category` whose AMC contains
    `amc_sub`, replay it (with the desk's multi-force FM brain, default by mandate), and (optionally)
    save the audit trail. Returns (reg_entry, nav, monthly, score, diag)."""
    reg = af.registry(amcs=[amc_sub], min_aum_cr=min_aum_cr)
    cands = [s for schemes in reg.values() for s in schemes.values()
             if s["category"] == category and amc_sub.lower() in (s["amc"] or "").lower()]
    if not cands:
        raise SystemExit(f"no {category} scheme for {amc_sub} (≥₹{min_aum_cr}cr)")
    reg_entry = max(cands, key=lambda s: af._f(s["aum_cr"]))
    bid = brain_id or af.brain_for_mandate(reg_entry.get("category"), reg_entry.get("mandate"))
    log(f"[replay] {reg_entry['amc']} — {reg_entry['scheme']}  "
        f"(AUM ₹{af._f(reg_entry['aum_cr']):,.0f} cr, {reg_entry['category']}, bench {reg_entry['benchmark']}, "
        f"brain={bid})")
    nav, monthly, score, diag = replay(reg_entry, start=start, end=end, brain_id=bid, log=log)
    if save:
        d = save_replay(reg_entry, nav, monthly, score, diag)
        log(f"[replay] saved → {d}")
    return reg_entry, nav, monthly, score, diag
