"""
equity_research.py — the DEEP EQUITY-ANALYST engine (Vistas task #64/#65, W5).

WHAT THIS IS (one sentence)
---------------------------
A DETERMINISTIC, NO-LLM, pure-statistics engine that assembles a **defensible, reproducible
equity case study** for any NSE symbol — `research(symbol) -> dossier` — by READING the
terminal's already-computed substrate (it fetches nothing, computes nothing new from raw data
that the substrate engines don't already expose). It is the "analyst's desk tool" of the
Agentic-AMC stack ([[ANALYST_GOLDMINE]] charter): it SYNTHESISES validated signals into a
structured thesis; it NEVER manufactures a forecast.

WHY DETERMINISTIC (the house discipline)
----------------------------------------
Every number is traceable to a source field, every percentile carries its definition + method
+ why, and the SYNTHESIS is a TEMPLATE filled from the computed flags — not free-form opinion.
This is the charter's non-negotiable: agents synthesise validated signals, they never invent
alpha; no curve-fit; no fabricated estimate levels (our ARM dump carries NO forward estimate
LEVELS, so this engine never pretends to). The thesis is reproducible bit-for-bit from the
dossier dict.

THE SUBSTRATE IT READS (reuses existing loaders; no new fetch)
--------------------------------------------------------------
  - fundamentals.compute(bundle)   3-statement analytics + valuation-multiple SERIES, each with
                                   its own-history `now / median / percentile`
                                   (pe / ps / pb / ev_ebitda / ev_sales / dy / fcfy).
  - stock_intel._market_behaviour  per-stock momentum/technical (returns ladder, DMA, 52w, RS).
  - starmine.cards_by_symbol       LSEG StarMine ARM card (score + 30/90d trend + 4 components).
  - funds_flows.build_stock_series net-active CONVICTION flow (`net_active[]` + `decomp` snapshot)
                                   and its size-neutral rank.
  - funds_flows.build_stock_holders  MF "who owns it" (n_funds, top holders) — optional.
  - the per-stock Screener `shareholding` table (FII/DII/promoter mix) via stock_intel._ownership.

THE SEVEN SECTIONS OF A DOSSIER (organised as an analyst would)
---------------------------------------------------------------
  1. valuation   — each multiple with OWN-history pctile (cheap/dear vs its past) AND a
                   peer-relative pctile (vs sector cohort).
  2. quality     — 3-statement trends (revenue/EPS/margin/ROE trajectory, leverage, growth-stability).
  3. momentum    — trailing-return ladder, distance to 200-DMA, new-high status, trend.
  4. revisions   — ARM score + direction (shippable per ABSL sign-off; score+trend, not vendor internals).
  5. smart_money — net-active conviction flow + rank + ownership mix (FII/DII/promoter/MF).
  6. peers       — where the stock sits vs its sector cohort on each axis (percentile table).
  7. synthesis   — a mechanical bull/bear/what-would-change-my-mind thesis + a FUNDAMENTAL-LAW read
                   (which axis is the would-be IC source; breadth/independence; the long-only
                   transfer-coefficient caveat).

PERCENTILE CONVENTION (used everywhere, stated once)
----------------------------------------------------
`pctile(x, sample)` = 100 * (# of sample values <= x) / N, on the NON-null sample (the same
"<=" convention as fundamentals._pct_rank and stock_intel). For a VALUATION multiple a LOW
percentile = CHEAP (the stock trades below most of the comparison set); for a "higher-is-better"
quantity (a return, ROE, the ARM score) a HIGH percentile = STRONG. Each percentile field in the
dossier states its own polarity in its `.method`. All percentiles are clamped to [0, 100].

LICENSING
---------
Raw ARM is OK to surface in the deck (ABSL sign-off, attributed "LSEG StarMine"); we surface the
0-100 SCORE + its 30/90d trend + the 4 component scores — NOT raw vendor internals (no per-broker
estimate rows, no ISIN-keyed raw revision counts). This engine PERSISTS nothing to any committed
book/blotter file; it returns an in-memory dict for display. The dossier is json-serializable and
`allow_nan=False`-clean (no NaN/Inf ever leaves the engine).

Provenance: written for Vistas 2026-06-26 (task #64/#65). Standalone; degrades gracefully — any
missing substrate piece becomes a clean "n/a", never a crash.
"""
from __future__ import annotations

import math


# ============================================================================ helpers
def _fin(x):
    """Coerce to a finite float or None (NaN/Inf/None -> None). The single gate that keeps the
    dossier allow_nan=False clean — every number passes through here before it is stored."""
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x, d=2):
    """Round to d decimals, NaN-safe -> None."""
    v = _fin(x)
    return None if v is None else round(v, d)


def _pctile(x, sample):
    """Percentile (0-100) of x within the non-null `sample`, '<=' convention, clamped to [0,100].

    Definition: 100 * (count of sample values <= x) / N. A LOW value means x sits below most of
    the sample. Returns None if x or the sample is empty. This is THE percentile primitive of the
    engine; every percentile in the dossier is computed here so the convention is identical
    everywhere (and matches fundamentals._pct_rank / stock_intel)."""
    xv = _fin(x)
    if xv is None:
        return None
    pts = [v for v in (_fin(s) for s in sample) if v is not None]
    if not pts:
        return None
    p = 100.0 * sum(1 for v in pts if v <= xv) / len(pts)
    return round(max(0.0, min(100.0, p)), 1)


def _trend_word(delta, up=2.0, down=-2.0):
    """Map a numeric change to a plain word (rising / falling / stable). `delta` in the same units
    as the quantity; the ±2 default suits ARM points and percentage-point moves."""
    d = _fin(delta)
    if d is None:
        return None
    if d >= up:
        return "rising"
    if d <= down:
        return "falling"
    return "stable"


def _last_non_null(seq):
    if not seq:
        return None
    for v in reversed(seq):
        fv = _fin(v)
        if fv is not None:
            return fv
    return None


# ============================================================================ context (load-once)
def build_context(symbols=None, flow_months_back=12, log=print):
    """Load the SHARED, reusable substrate ONCE so per-symbol `research()` is cheap when run on
    many names. Every piece degrades to None/empty if unavailable. Returns a dict the section
    builders read.

    - prices/index/turnover/industry/corpactions : the stock_intel context (reused as-is).
    - arm_cards   : {SYM: ARM card} from starmine.cards_by_symbol (the full StarMine India cache).
    - flows       : {SYM: net-active-flow series} from funds_flows.build_stock_series (the
                    conviction signal). SLOW to build — pass flow_months_back small for speed, or
                    None to skip (smart-money degrades to n/a).
    - holders     : {SYM: MF ownership} from funds_flows.build_stock_holders (optional).
    - fund_cache  : {SYM: fundamentals-analytics} — lazily filled by research() and reused for the
                    peer cohort (so peers are computed from the SAME analytics, not re-fetched).
    """
    ctx = {"prices": None, "index": None, "turnover": None, "industry": {}, "corpactions": {},
           "arm_cards": {}, "flows": {}, "holders": {}, "fund_cache": {}, "_notes": []}

    # 1) stock_intel shared panels (prices / index / turnover / industry map / corp actions)
    try:
        from vistas import stock_intel as _si
        sctx = _si.build_context()
        ctx.update({k: sctx.get(k) for k in ("prices", "index", "turnover", "industry", "corpactions")})
        ctx["_si"] = _si
    except Exception as e:                                   # pragma: no cover
        ctx["_notes"].append(f"stock_intel context unavailable: {e}")
        ctx["_si"] = None

    # 2) ARM cards (LSEG StarMine) — score + components only (no raw vendor internals)
    try:
        from vistas import starmine as _sm
        ctx["arm_cards"] = _sm.cards_by_symbol(log=lambda *a, **k: None) or {}
    except Exception as e:                                   # pragma: no cover
        ctx["_notes"].append(f"ARM cards unavailable: {e}")

    # 3) cross-AMC net-active conviction flow (optional — slow; skip with flow_months_back=None)
    if flow_months_back:
        try:
            from vistas import funds_flows as _ff
            ctx["flows"] = _ff.build_stock_series(months_back=flow_months_back) or {}
            ctx["holders"] = _ff.build_stock_holders() or {}
        except Exception as e:                               # pragma: no cover
            ctx["_notes"].append(f"smart-money flow unavailable: {e}")

    return ctx


# ============================================================================ fundamentals access
def _load_fundamentals(symbol, ctx):
    """Return the fundamentals-analytics dict for `symbol` (cached in ctx so peers reuse it).
    Reads the local Screener bundle + fundamentals.compute — no network. None if uncached."""
    SY = str(symbol).upper()
    cache = ctx.setdefault("fund_cache", {})
    if SY in cache:
        return cache[SY]
    fa = None
    try:
        from vistas import screener as _scr, fundamentals as _fund
        b = _scr.load(symbol) or _scr.load(SY)
        if b:
            fa = _fund.compute(b)
            ctx.setdefault("_bundles", {})[SY] = b      # keep the bundle for the shareholding table
    except Exception:
        fa = None
    cache[SY] = fa
    return fa


# ============================================================================ peer cohort
def _peer_symbols(symbol, ctx, cap=60):
    """The sector-peer cohort for `symbol`: every OTHER cached-fundamentals symbol carrying the
    SAME NSE 'Industry' tag (stock_intel's industry map). Capped at `cap` for compute cost.

    Definition: peers = {s : industry(s) == industry(symbol), s has a cached Screener bundle, s != symbol}.
    Method: industry tag from data/stock_industry.json (the NIFTY-500 constituent 'Industry' column);
    membership in the cached set from the screener cache directory. Why: a valuation/momentum read is
    only meaningful RELATIVE to like businesses; the cohort is the comparison set for every
    'peer-relative percentile' in the dossier. Returns (industry_label, [peer_syms])."""
    industry = (ctx.get("industry") or {}).get(str(symbol).upper())
    if not industry:
        return None, []
    indU = {k.upper(): v for k, v in (ctx.get("industry") or {}).items()}
    try:
        from vistas import screener as _scr
        avail = {s.upper() for s in (_scr.available() or [])}
    except Exception:
        avail = set()
    SY = str(symbol).upper()
    peers = [s for s in avail
             if s != SY and indU.get(s) == industry]
    peers.sort()
    return industry, peers[:cap]


def _peer_metric_samples(symbol, ctx):
    """Collect the PEER cohort's values for every cross-sectional axis, so each axis gets a
    peer-relative percentile. Lazily computes each peer's fundamentals/market once and caches.

    Returns (industry, n_peers, samples) where samples = {axis: [peer values...]}. Axes:
      pe, pb, ps, ev_ebitda, ev_sales, dy, fcfy   (valuation multiples — current snapshot value)
      quality, roe, pat_growth_5y                  (quality/growth)
      ret_12m, dist_200dma                          (momentum)
      arm                                           (analyst revisions score)
      na_intensity                                  (smart-money conviction intensity)
    The SELF value is NOT included in its own peer sample (a clean cross-section)."""
    industry, peers = _peer_symbols(symbol, ctx)
    axes = ("pe", "pb", "ps", "ev_ebitda", "ev_sales", "dy", "fcfy",
            "quality", "roe", "pat_growth_5y", "ret_12m", "dist_200dma", "arm", "na_intensity")
    samples = {a: [] for a in axes}
    if not peers:
        return industry, 0, samples
    n = 0
    for p in peers:
        try:
            row = _axis_values(p, ctx)
        except Exception:
            continue
        if row is None:
            continue
        n += 1
        for a in axes:
            v = row.get(a)
            if v is not None:
                samples[a].append(v)
    return industry, n, samples


def _axis_values(symbol, ctx):
    """The CURRENT cross-sectional value of every comparable axis for one symbol (used for BOTH the
    subject and each peer). Pulls from fundamentals snapshot + market behaviour + ARM + flow. All
    values are finite-or-None. This is the single definition of 'what a peer contributes', so the
    subject and the cohort are measured identically."""
    fa = _load_fundamentals(symbol, ctx)
    out = {}
    if fa and fa.get("ok"):
        val = fa.get("valuation") or {}
        snap = val.get("snapshot") or {}
        out["pe"] = _fin(val.get("pe_now"))
        out["pb"] = _fin(snap.get("pb"))
        out["ps"] = _fin((val.get("ps_series") or {}).get("now"))
        out["ev_ebitda"] = _fin(snap.get("ev_ebitda"))
        out["ev_sales"] = _fin(snap.get("ev_sales"))
        out["dy"] = _fin((val.get("dy_series") or {}).get("now"))
        out["fcfy"] = _fin((val.get("fcfy_series") or {}).get("now"))
        out["quality"] = _fin((fa.get("quality") or {}).get("score"))
        out["roe"] = _last_non_null(((fa.get("margins") or {}).get("ROE") or {}).get("values"))
        cagr = (((fa.get("growth") or {}).get("PAT") or {}).get("cagr") or {})
        g5 = _fin(cagr.get("5y"))
        out["pat_growth_5y"] = (g5 * 100.0) if g5 is not None else None
    # market behaviour (returns ladder + DMA) — reuse stock_intel
    si = ctx.get("_si")
    if si is not None:
        try:
            mb = si._market_behaviour(symbol, ctx)
            if mb and mb.get("ok"):
                out["ret_12m"] = _fin((mb.get("returns") or {}).get("12M"))
                out["dist_200dma"] = _fin(((mb.get("dma") or {}).get("200DMA") or {}).get("px_vs"))
        except Exception:
            pass
    # ARM score
    card = (ctx.get("arm_cards") or {}).get(str(symbol).upper())
    if card:
        out["arm"] = _fin((card.get("headline") or {}).get("score"))
    # smart-money net-active intensity (size-neutral conviction)
    fl = (ctx.get("flows") or {}).get(str(symbol).upper())
    if fl:
        out["na_intensity"] = _fin((fl.get("decomp") or {}).get("na_intensity"))
    return out


# ============================================================================ SECTION 1: valuation
def _section_valuation(symbol, fa, peer_samples):
    """Each multiple with (a) its OWN-history percentile (cheap/dear vs its own past, from the dense
    weekly multiple series fundamentals already emits) and (b) a PEER-relative percentile (vs the
    sector cohort's current values). For a multiple, LOW pctile = cheap; for a YIELD (dy/fcfy) HIGH
    = cheap-ish (more yield). Each row states its own polarity."""
    if not fa or not fa.get("ok"):
        return {"ok": False, "na": "no fundamentals for this symbol"}
    val = fa.get("valuation") or {}
    snap = val.get("snapshot") or {}
    is_bank = bool(fa.get("is_bank"))

    # (multiple key in dossier, own-history series key, snapshot 'now' fallback, lower_is_cheaper)
    SPEC = [
        ("pe",        None,             val.get("pe_now"),      True,  "Price / earnings"),
        ("pb",        "pb_series",      snap.get("pb"),         True,  "Price / book"),
        ("ps",        "ps_series",      snap.get("mcap_sales"), True,  "Price / sales"),
        ("ev_ebitda", "ev_ebitda_series", snap.get("ev_ebitda"), True, "Enterprise value / EBITDA"),
        ("ev_sales",  "ev_sales_series", snap.get("ev_sales"),  True,  "Enterprise value / sales"),
        ("dy",        "dy_series",      None,                   False, "Dividend yield (%)"),
        ("fcfy",      "fcfy_series",    snap.get("fcf_yield"),  False, "Free-cash-flow yield (%)"),
    ]
    rows = {}
    for key, series_key, now_fallback, lower_cheap, label in SPEC:
        if key == "pe":
            now = _fin(val.get("pe_now"))
            own_pct = _fin(val.get("pe_percentile"))
            median = _fin(val.get("median_pe"))
        else:
            ser = val.get(series_key) or {}
            now = _fin(ser.get("now"))
            if now is None:
                now = _fin(now_fallback)
            own_pct = _fin(ser.get("percentile"))
            median = _fin(ser.get("median"))
        # EV multiples are n/a for banks (no EBITDA / enterprise-value concept)
        if is_bank and key in ("ev_ebitda", "ev_sales"):
            rows[key] = {"label": label, "value": None, "na": "not meaningful for banks/NBFCs"}
            continue
        peer_pct = _pctile(now, peer_samples.get(key, []))
        n_peers = sum(1 for v in peer_samples.get(key, []) if _fin(v) is not None)
        # plain reads (own-history is the primary cheap/dear read)
        read = None
        if own_pct is not None and lower_cheap:
            read = ("cheap vs its own history" if own_pct < 30 else
                    "expensive vs its own history" if own_pct > 70 else "mid-range vs its own history")
        elif own_pct is not None and not lower_cheap:   # a yield: high own-pctile = high yield now
            read = ("high yield vs its own history" if own_pct > 70 else
                    "low yield vs its own history" if own_pct < 30 else "mid-range vs its own history")
        rows[key] = {
            "label": label, "value": _r(now, 2), "median": _r(median, 2),
            "own_pctile": _r(own_pct, 1), "peer_pctile": _r(peer_pct, 1), "n_peers": n_peers,
            "lower_is_cheaper": lower_cheap, "read": read,
        }
    # a single composite "cheapness" read from the headline P/E own-history percentile
    pe_pct = rows.get("pe", {}).get("own_pctile")
    cheap_status = (None if pe_pct is None else
                    "cheap" if pe_pct < 30 else "expensive" if pe_pct > 70 else "fair")
    return {
        "ok": True, "is_bank": is_bank, "multiples": rows,
        "headline_pe_own_pctile": pe_pct, "cheapness": cheap_status,
        "method": ("Each multiple's OWN-HISTORY percentile = where today's value sits in the stock's own "
                   "dense weekly multiple series (fundamentals._multiple_series); '<=' convention, so a LOW "
                   "percentile on a P/E-type multiple means CHEAP vs its past (HIGH on a yield = more yield). "
                   "The PEER percentile ranks today's value against the current values of the sector cohort "
                   "(same NSE Industry tag). Banks omit EV multiples (no EBITDA)."),
        "why": "Cheapness is only information relative to a benchmark — the stock's own past AND its peers.",
    }


# ============================================================================ SECTION 2: quality / growth
def _section_quality(fa):
    """3-statement trajectory: the Fundamentals quality composite (with its components), revenue/EPS
    growth, margin & ROE level + direction, leverage, and growth-stability (% up-years). All read
    from fundamentals.compute — no recompute."""
    if not fa or not fa.get("ok"):
        return {"ok": False, "na": "no fundamentals for this symbol"}
    is_bank = bool(fa.get("is_bank"))
    growth = fa.get("growth") or {}
    margins = fa.get("margins") or {}
    quality = fa.get("quality") or {}

    def _cagr(metric, horizon="5y"):
        c = ((growth.get(metric) or {}).get("cagr") or {}).get(horizon)
        cf = _fin(c)
        return (cf * 100.0) if cf is not None else None

    def _ttm_yoy(metric):
        y = _fin((growth.get(metric) or {}).get("ttm_yoy"))
        return (y * 100.0) if y is not None else None

    def _level_dir(block_key, sub_key):
        vals = ((margins.get(block_key) or {}).get("values")) or []
        cur = _last_non_null(vals)
        # direction = latest minus the value 4 periods back (rough YoY for an annual axis)
        nn = [v for v in (_fin(v) for v in vals) if v is not None]
        prior = nn[-5] if len(nn) >= 5 else (nn[0] if nn else None)
        delta = (cur - prior) if (cur is not None and prior is not None) else None
        return cur, delta

    roe_now, roe_dir = _level_dir("ROE", "ROE")
    opm_now, opm_dir = _level_dir("OPM" if not is_bank else "Financing margin", None)

    # growth-stability = share of years with positive sales growth (a quality-composite component too)
    sales_yoy = [v for v in (_fin(v) for v in ((growth.get("Sales") or {}).get("yoy") or {}).get("values", [])) if v is not None]
    up_years = (100.0 * sum(1 for v in sales_yoy if v > 0) / len(sales_yoy)) if sales_yoy else None

    # leverage (non-bank): latest D/E + interest cover
    bal = fa.get("balance") or {}
    de = _last_non_null((bal.get("D/E") or {}).get("values")) if not is_bank else None
    ic = _last_non_null((bal.get("Interest coverage") or {}).get("values")) if not is_bank else None

    return {
        "ok": True, "is_bank": is_bank,
        "quality_score": _r(quality.get("score"), 1),
        "quality_components": [
            {"label": c.get("label"), "value": _r(c.get("value"), 2), "score": _r(c.get("score"), 1)}
            for c in (quality.get("components") or [])
        ],
        "revenue_cagr_5y_pct": _r(_cagr("Sales"), 1),
        "eps_cagr_5y_pct": _r(_cagr("EPS"), 1),
        "pat_ttm_yoy_pct": _r(_ttm_yoy("PAT"), 1),
        "roe_now_pct": _r(roe_now, 1), "roe_trend_pp": _r(roe_dir, 1), "roe_dir": _trend_word(roe_dir, 1, -1),
        "margin_now_pct": _r(opm_now, 1), "margin_trend_pp": _r(opm_dir, 1), "margin_dir": _trend_word(opm_dir, 1, -1),
        "growth_stability_up_years_pct": _r(up_years, 0),
        "de_now": _r(de, 2), "interest_cover": _r(ic, 1),
        "method": ("Read straight from fundamentals.compute: quality = the equal-weighted 0-100 composite "
                   "(profitability, margin stability, growth consistency, cash conversion, low leverage, "
                   "compounding); CAGRs are endpoint-to-endpoint annual (PAT/EPS over 5y); ROE/margin level = "
                   "latest annual value, direction = latest minus ~5 years prior; growth-stability = share of "
                   "years with positive sales growth; leverage = latest D/E and EBIT/interest cover. Banks "
                   "use ROE & financing margin (no OPM/leverage)."),
        "why": "A cheap multiple is a trap on a declining business — quality/growth is the other half of the read.",
    }


# ============================================================================ SECTION 3: momentum / technical
def _section_momentum(symbol, ctx):
    """Trailing-return ladder, distance to the 200-DMA, 52-week-high status, golden cross, drawdown,
    and relative strength vs the broad index — all from stock_intel._market_behaviour (no recompute)."""
    si = ctx.get("_si")
    if si is None:
        return {"ok": False, "na": "market-behaviour engine unavailable"}
    try:
        mb = si._market_behaviour(symbol, ctx)
    except Exception as e:                                   # pragma: no cover
        return {"ok": False, "na": f"market-behaviour error: {e}"}
    if not mb or not mb.get("ok"):
        return {"ok": False, "na": (mb or {}).get("na", "no price history")}
    returns = mb.get("returns") or {}
    dma200 = (mb.get("dma") or {}).get("200DMA") or {}
    hl = mb.get("high_low") or {}
    dd = mb.get("drawdown") or {}
    rs500 = ((mb.get("rs_broad") or {}).get("NIFTY 500") or {}).get("rel_return") or {}
    dist_high = _fin(hl.get("dist_from_52w_high_pct"))
    new_high = (dist_high is not None and dist_high > -2.0)
    above200 = dma200.get("above")
    r12 = _fin(returns.get("12M"))
    # a compact trend label from the price structure (correlated facets of ONE trend — labelled, not a tally)
    if above200 is True and mb.get("golden_cross") == "above" and (dist_high is not None and dist_high > -10):
        trend = "strong uptrend"
    elif above200 is False or (dd.get("current_from_peak") is not None and _fin(dd.get("current_from_peak")) < -25):
        trend = "downtrend"
    else:
        trend = "sideways / mixed"
    return {
        "ok": True, "asof": mb.get("asof"), "price": mb.get("price"),
        "returns_pct": {k: _r(returns.get(k), 1) for k in ("1M", "3M", "6M", "12M")},
        "dist_200dma_pct": _r(dma200.get("px_vs"), 1), "above_200dma": above200,
        "golden_cross": mb.get("golden_cross"),
        "dist_52w_high_pct": _r(dist_high, 1), "new_52w_high": bool(new_high),
        "current_drawdown_pct": _r(dd.get("current_from_peak"), 1),
        "rs_vs_nifty500_12m_pp": _r(rs500.get("12M"), 1),
        "trend": trend,
        "method": ("From stock_intel._market_behaviour on the adjusted total-return price panel: trailing "
                   "return = price[t]/price[t-N]-1 (N=21/63/126/252 trading days); 200-DMA distance = "
                   "price/mean(last 200d)-1; 52w-high distance = price/max(last 252d)-1 (new high = within 2%); "
                   "golden cross = 50-DMA >= 200-DMA; RS = stock 12m return minus NIFTY-500 12m return (pp). "
                   "Trend is a LABEL of correlated facets of one move, not a vote tally."),
        "why": "What the market is already saying — trend, leadership, and whether revisions/flow have started to price in.",
    }


# ============================================================================ SECTION 4: analyst revisions (ARM)
def _section_revisions(symbol, ctx):
    """LSEG StarMine ARM: the 0-100 regional revision-momentum SCORE + its 30/90-day direction + the
    four component scores (revenue / EPS / EBITDA / recommendations). Score + trend only — NO raw
    vendor internals. Stale (>90d) is flagged not-recommending per the charter."""
    card = (ctx.get("arm_cards") or {}).get(str(symbol).upper())
    if not card:
        return {"ok": False, "na": "no ARM coverage for this symbol"}
    head = card.get("headline") or {}
    score = _fin(head.get("score"))
    t30 = _fin(head.get("trend_30d"))
    t90 = _fin(head.get("trend_90d"))
    # staleness: ARM is a fast (~1-month) signal; flag if the latest change-point is old
    stale = False
    asof = card.get("asof")
    try:
        import datetime as _dt
        if asof:
            age = (_dt.date.today() - _dt.date.fromisoformat(asof)).days
            stale = age > 90
    except Exception:
        age = None
    # dominant component = the highest of the four (the apparent catalyst — labelled, not claimed)
    comps = [(c.get("label"), _fin(c.get("score"))) for c in (card.get("components") or [])]
    comps_clean = [(l, s) for l, s in comps if s is not None]
    dominant = max(comps_clean, key=lambda t: t[1])[0] if comps_clean else None
    return {
        "ok": True, "asof": asof, "score": _r(score, 1),
        "global_pctile": _r(head.get("global"), 1), "bucket5": head.get("bucket5"),
        "trend_30d": _r(t30, 1), "trend_90d": _r(t90, 1),
        "direction": _trend_word(t30, 5, -5),
        "level_band": (None if score is None else
                       "high (analysts raising)" if score >= 60 else
                       "low (analysts cutting)" if score < 40 else "neutral"),
        "components": [{"label": l, "score": _r(s, 1)} for l, s in comps],
        "dominant_component": dominant,
        "stale": bool(stale),
        "method": ("LSEG StarMine Analyst Revision Model: a 0-100 REGIONAL percentile of analyst "
                   "estimate-revision momentum (100 = raising estimates the most). LEVEL = where consensus "
                   "already is (context); 30/90-day CHANGE = consensus turning now (the edge). Dominant "
                   "component (the highest of revenue/EPS/EBITDA/recommendation) is labelled as the apparent "
                   "catalyst, not a claimed cause. Surfaced with ABSL sign-off; score+trend only, no vendor "
                   "internals. Our ARM IC is ~0.03-0.045 @1M — a portfolio tilt, NOT a per-name guarantee. "
                   "Stale (>90d) is flagged not-recommending."),
        "why": "A revision is information arriving — it is the freshest of the fundamental signals (a ~1-6 month clock).",
    }


# ============================================================================ SECTION 5: smart money
def _section_smart_money(symbol, ctx, fa):
    """The NEW net-active CONVICTION flow (`net_active`/`decomp.net_active_cr`, inflow-immune,
    weight-space) + its size-neutral rank, plus the ownership mix (FII/DII/promoter from the Screener
    shareholding table; MF ownership from build_stock_holders). Flow is the rupees managers actually
    MOVED net of price drift AND scheme inflows."""
    out = {"ok": False, "na": None, "flow": None, "ownership": None}
    SY = str(symbol).upper()

    # --- conviction flow (net-active) ---
    fl = (ctx.get("flows") or {}).get(SY)
    if fl:
        dc = fl.get("decomp") or {}
        na_series = [v for v in (_fin(v) for v in (fl.get("net_active") or [])) if v is not None]
        # recent direction: sum of the last 3 monthly net-active figures (accumulating vs distributing)
        recent = sum(na_series[-3:]) if na_series else None
        out["flow"] = {
            "ym": dc.get("ym"),
            "net_active_cr": _r(dc.get("net_active_cr"), 1),
            "na_intensity_pct": _r(dc.get("na_intensity"), 1),
            "na_rank": dc.get("na_rank"), "na_nclean": dc.get("na_nclean"),
            "recent_3m_net_active_cr": _r(recent, 1),
            "stance": (None if recent is None else
                       "accumulating" if recent > 0 else "distributing" if recent < 0 else "flat"),
            "price_adj_cr": _r(dc.get("price_adj_cr"), 1),
            "gross_cr": _r(dc.get("gross_cr"), 1),
        }
        out["ok"] = True

    # --- ownership mix: FII / DII / promoter (Screener shareholding) ---
    bundle = (ctx.get("_bundles") or {}).get(SY)
    holders = {}
    si = ctx.get("_si")
    if si is not None and bundle is not None:
        try:
            own = si._ownership(symbol, bundle, ctx)
            for k, h in (own.get("holders") or {}).items():
                holders[k] = {"latest_pct": _r(h.get("latest_pct"), 2), "chg_1y_pp": _r(h.get("chg_1y_pp"), 2)}
            pledge = own.get("pledge") or {}
            if pledge.get("latest_pct") is not None:
                holders["_pledge_pct"] = _r(pledge.get("latest_pct"), 2)
        except Exception:
            pass
    # --- MF ownership (who owns it, count) ---
    mf = (ctx.get("holders") or {}).get(SY)
    mf_summary = None
    if mf:
        mf_summary = {"n_funds": mf.get("n_funds"), "total_cr": _r(mf.get("total_cr"), 1), "ym": mf.get("ym")}
    if holders or mf_summary:
        out["ownership"] = {"shareholding": holders or None, "mutual_funds": mf_summary}
        out["ok"] = True

    if not out["ok"]:
        out["na"] = "no smart-money flow or ownership data for this symbol"
    out["method"] = (
        "net-active flow (funds_flows): per fund, the rupees REWEIGHTED in weight-space — "
        "AUM_eq*(1+R_p)*Δw_active — summed across active MFs. It strips BOTH price drift AND "
        "pro-rata scheme inflows (a pure-inflow deployment leaves weights unchanged -> zero net-active), "
        "and is corporate-action-immune via the merger-bridge + CA quarantine. na_rank is the "
        "size-neutral intensity rank over the clean cross-section (1 = strongest accumulation). "
        "Ownership mix = latest Screener shareholding % (promoter/FII/DII) + the count of MFs holding it.")
    out["why"] = "Where informed money is actually moving (net of price and inflows) confirms or contradicts the thesis."
    return out


# ============================================================================ SECTION 6: peer context
def _section_peers(symbol, ctx, self_axes, industry, n_peers, peer_samples):
    """Where the stock sits vs its sector cohort on each axis — a percentile TABLE. For each axis the
    subject's value is ranked against the peer sample. Polarity is stated per row (for a multiple LOW
    = cheap; for a return/ROE/ARM HIGH = strong)."""
    POLARITY = {  # axis -> (label, higher_is_better)
        "pe": ("P/E", False), "pb": ("P/B", False), "ps": ("P/S", False),
        "ev_ebitda": ("EV/EBITDA", False), "ev_sales": ("EV/Sales", False),
        "dy": ("Dividend yield", True), "fcfy": ("FCF yield", True),
        "quality": ("Quality score", True), "roe": ("ROE", True),
        "pat_growth_5y": ("PAT 5y CAGR", True), "ret_12m": ("12m return", True),
        "dist_200dma": ("Dist. to 200-DMA", True), "arm": ("ARM score", True),
        "na_intensity": ("Smart-money intensity", True),
    }
    if not n_peers:
        return {"ok": False, "na": "no sector-peer cohort (no Industry tag or no cached peers)",
                "industry": industry, "n_peers": 0}
    table = {}
    for axis, (label, hib) in POLARITY.items():
        val = self_axes.get(axis)
        pct = _pctile(val, peer_samples.get(axis, []))
        nn = sum(1 for v in peer_samples.get(axis, []) if _fin(v) is not None)
        if val is None or pct is None or nn < 3:
            continue
        # plain rank read in the polarity's own direction
        rank_pct = pct if hib else (100.0 - pct)   # "how favourably it ranks" in [0,100]
        band = ("top-tercile" if rank_pct >= 67 else "bottom-tercile" if rank_pct <= 33 else "mid-tercile")
        table[axis] = {"label": label, "value": _r(val, 2), "peer_pctile": _r(pct, 1),
                       "higher_is_better": hib, "favourability": _r(rank_pct, 1), "band": band,
                       "n_peers": nn}
    return {
        "ok": bool(table), "industry": industry, "n_peers": n_peers, "table": table,
        "method": ("Peer cohort = cached-fundamentals symbols with the SAME NSE 'Industry' tag. For each "
                   "axis the subject's current value is percentiled ('<=' convention) against the cohort's "
                   "current values (self excluded). 'favourability' re-orients the percentile to the axis "
                   "polarity (for a multiple, cheap = favourable), so top-tercile always means 'looks good "
                   "vs peers'. Requires >=3 peer values on an axis."),
        "why": "A stock's read is only as meaningful as its comparison set — peers calibrate every axis.",
    }


# ============================================================================ SECTION 7: synthesis (mechanical)
def _classify_axes(valuation, quality, momentum, revisions, smart_money, peers):
    """Reduce the six analytic sections to a small set of BOOLEAN/ordinal flags. The synthesis is
    then a deterministic template over THESE flags — no free-form opinion, fully reproducible from
    the dossier. Returns a flags dict."""
    f = {}
    # valuation flags
    pe_pct = valuation.get("headline_pe_own_pctile") if valuation.get("ok") else None
    f["cheap_own"] = (pe_pct is not None and pe_pct < 30)
    f["dear_own"] = (pe_pct is not None and pe_pct > 70)
    pe_peer = ((peers.get("table") or {}).get("pe") or {}).get("peer_pctile") if peers.get("ok") else None
    f["cheap_vs_peers"] = (pe_peer is not None and pe_peer < 33)
    f["dear_vs_peers"] = (pe_peer is not None and pe_peer > 67)
    # quality flags
    q = quality.get("quality_score") if quality.get("ok") else None
    f["high_quality"] = (q is not None and q >= 70)
    f["low_quality"] = (q is not None and q < 45)
    g5 = quality.get("pat_ttm_yoy_pct") if quality.get("ok") else None
    f["growing"] = (g5 is not None and g5 >= 8)
    f["shrinking"] = (g5 is not None and g5 < 0)
    f["roe_rising"] = (quality.get("roe_dir") == "rising") if quality.get("ok") else False
    de = quality.get("de_now") if quality.get("ok") else None
    f["levered"] = (de is not None and de > 2)
    # momentum flags
    f["uptrend"] = (momentum.get("trend") == "strong uptrend") if momentum.get("ok") else False
    f["downtrend"] = (momentum.get("trend") == "downtrend") if momentum.get("ok") else False
    f["new_high"] = bool(momentum.get("new_52w_high")) if momentum.get("ok") else False
    r12 = momentum.get("rs_vs_nifty500_12m_pp") if momentum.get("ok") else None
    f["leads_index"] = (r12 is not None and r12 > 0)
    # revisions flags
    arm = revisions.get("score") if revisions.get("ok") else None
    f["arm_high"] = (arm is not None and arm >= 60 and not revisions.get("stale"))
    f["arm_low"] = (arm is not None and arm < 40 and not revisions.get("stale"))
    f["arm_rising"] = (revisions.get("direction") == "rising") if revisions.get("ok") else False
    f["arm_falling"] = (revisions.get("direction") == "falling") if revisions.get("ok") else False
    f["arm_stale"] = bool(revisions.get("stale")) if revisions.get("ok") else False
    # smart-money flags
    sm = (smart_money.get("flow") or {}) if smart_money.get("ok") else {}
    f["accumulating"] = (sm.get("stance") == "accumulating")
    f["distributing"] = (sm.get("stance") == "distributing")
    rank, nclean = sm.get("na_rank"), sm.get("na_nclean")
    f["top_flow"] = (rank is not None and nclean and rank <= max(1, int(0.1 * nclean)))
    return f


def _synthesize(symbol, name, flags, fundamental_law):
    """Build the structured thesis dict + the plain-English thesis STRING from the boolean flags.
    Pure template-fill: the same flags always yield the same thesis (reproducible, defensible, no
    opinion). bull/bear/what-would-change-my-mind are assembled from the flags that fired."""
    bull, bear, change_mind = [], [], []

    # --- BULL clauses ---
    if flags["cheap_own"]:
        bull.append("trades cheap versus its own valuation history")
    if flags["cheap_vs_peers"]:
        bull.append("cheaper than most sector peers")
    if flags["high_quality"]:
        bull.append("a high-quality business (top-band composite)")
    if flags["growing"]:
        bull.append("earnings are growing")
    if flags["roe_rising"]:
        bull.append("return on equity is improving")
    if flags["uptrend"] or flags["new_high"]:
        bull.append("price is in a confirmed uptrend" + (" at new highs" if flags["new_high"] else ""))
    if flags["leads_index"]:
        bull.append("outperforming the broad index over 12 months")
    if flags["arm_high"] or flags["arm_rising"]:
        bull.append("analysts are raising estimates" + (" and the revision is turning up" if flags["arm_rising"] else ""))
    if flags["accumulating"]:
        bull.append("active mutual funds are accumulating it (net of price and inflows)")
    if flags["top_flow"]:
        bull.append("among the strongest smart-money accumulation in its cross-section")

    # --- BEAR clauses ---
    if flags["dear_own"]:
        bear.append("expensive versus its own valuation history")
    if flags["dear_vs_peers"]:
        bear.append("richer than most sector peers")
    if flags["low_quality"]:
        bear.append("a lower-quality business (below-band composite)")
    if flags["shrinking"]:
        bear.append("earnings are contracting")
    if flags["levered"]:
        bear.append("carries elevated leverage")
    if flags["downtrend"]:
        bear.append("price is in a downtrend")
    if not flags["leads_index"]:
        bear.append("lagging the broad index over 12 months")
    if flags["arm_low"] or flags["arm_falling"]:
        bear.append("analysts are cutting estimates" + (" / revision momentum fading" if flags["arm_falling"] else ""))
    if flags["distributing"]:
        bear.append("active funds are reducing (distributing) the position")
    if flags["arm_stale"]:
        bear.append("analyst-revision data is stale (>90d) — not currently a usable signal")

    # --- WHAT WOULD CHANGE MY MIND ---
    if flags["cheap_own"] or flags["cheap_vs_peers"]:
        change_mind.append("a quality/earnings deterioration would turn the cheapness into a value trap")
    if flags["high_quality"] and (flags["dear_own"] or flags["dear_vs_peers"]):
        change_mind.append("a multiple de-rate toward its own median would remove the premium-quality cushion")
    if flags["uptrend"] or flags["arm_high"]:
        change_mind.append("a roll-over in revision momentum or relative strength would mark the inflection")
    if flags["accumulating"]:
        change_mind.append("a swing to net distribution by active funds would remove the flow confirmation")
    if not change_mind:
        change_mind.append("a change in the dominant axis (valuation, quality, momentum, revisions, or flow) would shift the read")

    # --- stance: how many bull vs bear clauses (descriptive, NOT a buy/sell) ---
    nb, nr = len(bull), len(bear)
    if nb >= nr + 2:
        stance = "constructive"
    elif nr >= nb + 2:
        stance = "cautious"
    else:
        stance = "balanced / mixed"

    nm = name or symbol
    parts = [f"{nm} ({symbol}) reads as {stance}."]
    if bull:
        parts.append("Bull case: " + "; ".join(bull) + ".")
    else:
        parts.append("Bull case: no positive axis currently fires.")
    if bear:
        parts.append("Bear case: " + "; ".join(bear) + ".")
    else:
        parts.append("Bear case: no negative axis currently fires.")
    parts.append("What would change the view: " + "; ".join(change_mind) + ".")
    parts.append("Fundamental-Law read: " + fundamental_law["summary"])
    parts.append("This is a diagnostic synthesis of validated signals for the reader's own judgement — "
                 "NOT a buy/sell/hold recommendation, and it manufactures no forecast.")
    thesis_text = " ".join(parts)

    return {
        "stance": stance,
        "bull": bull, "bear": bear, "what_would_change_my_mind": change_mind,
        "fundamental_law": fundamental_law,
        "thesis": thesis_text,
        "disclaimer": ("Mechanically template-filled from the computed section flags — the same inputs always "
                       "yield the same thesis. Diagnostics only; not advice; no fabricated forecasts."),
    }


def _fundamental_law_read(flags, peers):
    """The IR = IC * sqrt(BR) * TC lens applied to THIS name as a would-be active bet
    ([[FUNDAMENTAL_LAW]]). We do NOT compute a fund's realized IR here; we identify, for a single
    name, which axis is the plausible IC SOURCE, how its breadth/independence looks, and the
    long-only transfer-coefficient caveat. All qualitative + honest (no fabricated magnitudes)."""
    # which axis is the would-be IC source (the freshest validated edge that fires)
    ic_source = None
    if flags["arm_high"] or flags["arm_rising"]:
        ic_source = ("analyst-revision momentum (ARM) — the freshest signal; our measured ARM IC is "
                     "~0.03-0.045 @1M, a real but MODEST per-bet edge and a portfolio tilt, not a per-name guarantee")
    elif flags["cheap_own"] or flags["cheap_vs_peers"]:
        ic_source = ("valuation cheapness — a slow-clock value edge; per-bet IC on value is small and pays over "
                     "a long horizon")
    elif flags["accumulating"] or flags["top_flow"]:
        ic_source = ("smart-money net-active flow — informed-flow confirmation; useful as a CONFIRMING cross, "
                     "weaker as a standalone forecast")
    elif flags["uptrend"] or flags["leads_index"]:
        ic_source = ("price/relative-strength momentum — a trend edge; beware it co-moves with the move it rides")
    else:
        ic_source = "no clean IC source fires — this name is, on the current read, a no-view (hold-the-index) candidate"

    # breadth / independence: do the axes agree (one bet wearing many coats) or diversify?
    agree = sum(1 for k in ("cheap_own", "high_quality", "uptrend", "arm_high", "accumulating") if flags.get(k))
    if agree >= 3:
        breadth = ("the bullish axes AGREE (cheap + quality + momentum + revisions + flow lining up) — high "
                   "conviction on ONE name, but correlated axes are ONE effective bet, not several "
                   "(BR_eff collapses when bets co-move), so this is a conviction add, not added breadth")
    elif agree == 0:
        breadth = "no bullish axis fires — contributes no independent active bet here"
    else:
        breadth = ("the axes only partly agree — a mixed signal contributes little independent breadth; real "
                   "breadth comes from many DE-CORRELATED names, not from stacking correlated reads on one")

    tc = ("Long-only transfer caveat: even a correct NEGATIVE read on this name can only be expressed by "
          "UNDERWEIGHTING to at most its index weight (no short), so a bearish edge here is largely "
          "amputated at the door (the transfer coefficient TC is typically 0.3-0.6 for long-only) — a "
          "skilled bearish view on a small-index-weight name transfers almost nothing.")
    summary = (f"the would-be edge is {ic_source.split(' — ')[0]}; "
               f"{'axes agree (a conviction add, not added breadth)' if agree >= 3 else 'thin independent breadth'}; "
               f"and any bearish leg is throttled by the long-only transfer coefficient.")
    return {"ic_source": ic_source, "breadth_independence": breadth, "transfer_coefficient": tc,
            "summary": summary,
            "lens": "IR = IC * sqrt(BR) * TC (Grinold-Kahn + Clarke-de Silva-Thorley): skill per bet x "
                    "sqrt(independent bets) x how much survives long-only implementation."}


# ============================================================================ PUBLIC API
def research(symbol, ctx=None, flow_months_back=12):
    """Assemble the full deterministic equity-analyst dossier for one NSE `symbol`.

    Returns a json-serializable dict (allow_nan=False clean) with seven sections + a plain-English
    `thesis` string. Never raises: a bad symbol / missing substrate degrades each section to a clean
    `{"ok": False, "na": ...}` (never a crash, never a NaN).

    `ctx` (optional) = build_context() output, reused across many symbols. If None, a context is
    built for this one call (pass flow_months_back=None to skip the slow smart-money build)."""
    symbol = str(symbol).strip().upper()
    if ctx is None:
        ctx = build_context(flow_months_back=flow_months_back)

    fa = _load_fundamentals(symbol, ctx)
    name = (fa or {}).get("name") or ((ctx.get("_bundles") or {}).get(symbol, {}) or {}).get("name") or symbol

    # peer cohort samples (one cohort pass shared by sections 1 & 6)
    try:
        industry, n_peers, peer_samples = _peer_metric_samples(symbol, ctx)
    except Exception:
        industry, n_peers, peer_samples = None, 0, {}
    # the subject's own axis values (for the peer table)
    try:
        self_axes = _axis_values(symbol, ctx)
    except Exception:
        self_axes = {}

    # the six analytic sections (each independently graceful)
    def _safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:                              # pragma: no cover
            return {"ok": False, "na": f"section error: {type(e).__name__}: {e}"}

    valuation = _safe(_section_valuation, symbol, fa, peer_samples)
    quality = _safe(_section_quality, fa)
    momentum = _safe(_section_momentum, symbol, ctx)
    revisions = _safe(_section_revisions, symbol, ctx)
    smart_money = _safe(_section_smart_money, symbol, ctx, fa)
    peers = _safe(_section_peers, symbol, ctx, self_axes, industry, n_peers, peer_samples)

    # section 7: mechanical synthesis over the flags
    flags = _classify_axes(valuation, quality, momentum, revisions, smart_money, peers)
    fundamental_law = _fundamental_law_read(flags, peers)
    synthesis = _synthesize(symbol, name, flags, fundamental_law)

    coverage = {
        "has_fundamentals": bool(fa and fa.get("ok")),
        "has_market": bool(momentum.get("ok")),
        "has_arm": bool(revisions.get("ok")),
        "has_flow": bool((smart_money.get("flow"))),
        "has_peers": bool(peers.get("ok")),
        "is_bank": bool(fa and fa.get("is_bank")),
        "industry": industry, "n_peers": n_peers,
    }

    return {
        "ok": bool(fa and fa.get("ok")) or bool(momentum.get("ok")),
        "symbol": symbol, "name": name,
        "sections": {
            "valuation": valuation,
            "quality": quality,
            "momentum": momentum,
            "revisions": revisions,
            "smart_money": smart_money,
            "peers": peers,
        },
        "synthesis": synthesis,
        "thesis": synthesis["thesis"],
        "flags": flags,
        "coverage": coverage,
        "disclaimer": ("Deterministic (no-LLM) synthesis of the terminal's validated signals — every number is "
                       "traceable to a source field and every percentile carries its definition/method/why. "
                       "Diagnostics only, NOT investment advice; manufactures no forecast."),
        "provenance": "vistas/equity_research.py — reads fundamentals/stock_intel/starmine/funds_flows; fetches nothing.",
    }


# ============================================================================ self-test (manual)
if __name__ == "__main__":
    import json
    c = build_context(flow_months_back=12)
    for sym in ("RELIANCE", "INFY", "HDFCBANK"):
        d = research(sym, ctx=c)
        print("=" * 90)
        print(sym, "->", d["name"], "| ok:", d["ok"], "| coverage:", d["coverage"])
        # json round-trip proves allow_nan=False cleanliness
        json.dumps(d, allow_nan=False)
        print(d["thesis"])
