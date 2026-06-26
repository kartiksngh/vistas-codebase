"""vistas/amc_firm.py — the Digital-AMC FIRM OS (multi-AMC paper-trading books).

Turns the Digital-AMC *reasoning demo* into a firm that actually HOLDS BOOKS, trades within
liquidity, marks to market every day, and keeps an auditable record it learns from. It recreates
real Indian fund houses as VIRTUAL firms: each real scheme (at its real latest AUM) gets a paper
book, run by our analyst/FM/CIO agents, and is scored against BOTH its benchmark AND the real
scheme's actual track (we hold the real NAV + attribution — a built-in scorecard).

Design (AGENTIC_AMC.md, Phase 1) — the engine/judgment split keeps it honest + tractable:
  • This module = the DETERMINISTIC firm OS: registry, books, blotter, mark-to-market daily
    fact sheet, liquidity caps, scorecards. Every NUMBER is produced here, reproducibly.
  • The historical REPLAY is driven by a rules-based FM constructor (build_rules in #68) — NOT
    the LLM committee (running LLMs over years x dozens of schemes is cost-prohibitive). The
    LLM analyst/FM/CIO agents take over LIVE-FORWARD, adding judgment on top of the rules.

STATE (git-tracked = the audit trail) under amc_book/ :
  _registry.json                          AMCs - schemes - real AUM - mandate - benchmark
  <AMC>/<SCHEME>/book.json                live positions + cash + NAV + play-type tags
                /blotter.jsonl            every trade, append-only (rationale + pitch/decision link)
                /prereg.jsonl             every thesis + its falsifier, before the outcome
                /daily/<YYYY-MM>.json      daily mark-to-market fact sheets (the CITI column schema)
                /scorecard.json           IC - IR=IC*sqrt(BR)*TC vs benchmark AND vs the real scheme
  _firm/{pitches,decisions,rulings}.jsonl + lessons/<agent>.md

DAILY FACT-SHEET schema (mirrors ABSL's CITI_BIRLA_DAILY_EQUITY_FACT_SHEET.xlsx):
  sr, isin, name, sector, bbg, qty, avg_cost, book_cost, mkt_price, mkt_value,
  prev_price, prev_value, pct_change, wtd_contribution, pct_assets   (+ sector subtotals,
  + footer: equity, cash, nav, day_return, since_inception)  — also rendered to .xlsx for audit.

CONVENTIONS (so every number is reproducible — KV reporting rule):
  AUM        = Σ mktval_lakh / 100 over the scheme's latest disclosed holdings (₹cr). The fund's
               real latest size; seeds the book as 100% CASH (per decision: agents deploy it).
  price      = the terminal's clean adjusted total-return close (vistas.stocks.load), so the paper
               NAV is a true total-return series. Marked daily even on no-trade days.
  liquidity  = a position is capped at min(mandate cap, LIQ_DAYS x trailing median daily turnover);
               a single day's trade ≤ TRADE_ADV_FRAC of that day's turnover (a big book takes days
               to build). Turnover from bhav OHLCV (as-of date); current median as a fallback.
  play_type  = structural (core long-term compounder) / cyclical (medium-term rotation tilt) /
               tactical (short-term catalyst). Tags every holding so the book is legible by horizon.
"""

import os
import json
import datetime as _dt

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
BOOK_DIR = os.path.join(_ROOT, "amc_book")

LIQ_DAYS = 20            # max position ≤ this many days of median daily turnover
TRADE_ADV_FRAC = 0.15   # a single day's trade ≤ this fraction of the day's turnover


# ───────────────────────────────────────────────────────── funds data location
def _funds_dirs():
    """Prefer the freshly-built site; fall back to the stable published copy. Returns
    (attribution_dir, portfolio_dir) or (None, None)."""
    for base in (os.path.join(_ROOT, "output", "terminal_site", "data"),
                 os.path.join(_ROOT, "_pages", "terminal", "data")):
        a = os.path.join(base, "funds_attribution")
        p = os.path.join(base, "funds_portfolio")
        if os.path.isdir(a) and os.path.isdir(p):
            return a, p
    return None, None


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d
    except Exception:
        return d


# ───────────────────────────────────────────────────────── mandates (SEBI category → rules)
# cap_floor: which AMFI market-cap buckets the mandate must live in (large=top100, mid=101-250,
# small=251+). equity_min: floor on equity %. max_pos / max_sector: single-name / sector caps.
# n_lo/n_hi: target holdings count band. turn: annual turnover budget (a soft style guide).
_M = lambda **k: k
MANDATES = {
    "Large Cap Fund":        _M(buckets=["large"], equity_min=0.80, max_pos=0.10, max_sector=0.35, n_lo=35, n_hi=55, turn=0.4),
    "Large & Mid Cap Fund":  _M(buckets=["large", "mid"], equity_min=0.70, max_pos=0.08, max_sector=0.35, n_lo=45, n_hi=70, turn=0.5),
    "Mid Cap Fund":          _M(buckets=["mid"], equity_min=0.65, max_pos=0.07, max_sector=0.35, n_lo=45, n_hi=70, turn=0.6),
    "Small Cap Fund":        _M(buckets=["small"], equity_min=0.65, max_pos=0.05, max_sector=0.30, n_lo=55, n_hi=90, turn=0.5),
    "Multi Cap Fund":        _M(buckets=["large", "mid", "small"], equity_min=0.75, max_pos=0.08, max_sector=0.35, n_lo=50, n_hi=80, turn=0.5),
    "Flexi Cap Fund":        _M(buckets=["large", "mid", "small"], equity_min=0.65, max_pos=0.09, max_sector=0.35, n_lo=40, n_hi=70, turn=0.5),
    "Focused Fund":          _M(buckets=["large", "mid", "small"], equity_min=0.65, max_pos=0.10, max_sector=0.40, n_lo=20, n_hi=30, turn=0.4),
    "ELSS":                  _M(buckets=["large", "mid", "small"], equity_min=0.80, max_pos=0.09, max_sector=0.35, n_lo=40, n_hi=65, turn=0.4),
    "Value Fund":            _M(buckets=["large", "mid", "small"], equity_min=0.65, max_pos=0.09, max_sector=0.35, n_lo=40, n_hi=70, turn=0.4, tilt="value"),
    "Contra Fund":           _M(buckets=["large", "mid", "small"], equity_min=0.65, max_pos=0.09, max_sector=0.35, n_lo=40, n_hi=70, turn=0.4, tilt="value"),
    "Dividend Yield Fund":   _M(buckets=["large", "mid", "small"], equity_min=0.65, max_pos=0.09, max_sector=0.35, n_lo=35, n_hi=60, turn=0.4, tilt="yield"),
    "Sectoral / Thematic":   _M(buckets=["large", "mid", "small"], equity_min=0.80, max_pos=0.12, max_sector=1.00, n_lo=25, n_hi=50, turn=0.5, thematic=True),
    "Aggressive Hybrid Fund": _M(buckets=["large", "mid"], equity_min=0.65, equity_max=0.80, max_pos=0.09, max_sector=0.35, n_lo=35, n_hi=60, turn=0.4),
    "Dynamic Asset Allocation or Balanced Advantage": _M(buckets=["large", "mid"], equity_min=0.30, equity_max=0.90, max_pos=0.08, max_sector=0.35, n_lo=35, n_hi=60, turn=0.6, daa=True),
    "Multi Asset Allocation": _M(buckets=["large", "mid"], equity_min=0.40, equity_max=0.80, max_pos=0.08, max_sector=0.35, n_lo=30, n_hi=55, turn=0.5),
    "Equity Savings":        _M(buckets=["large", "mid"], equity_min=0.20, equity_max=0.50, max_pos=0.07, max_sector=0.35, n_lo=30, n_hi=55, turn=0.6),
}
_DEFAULT_MANDATE = _M(buckets=["large", "mid", "small"], equity_min=0.65, max_pos=0.09, max_sector=0.35, n_lo=40, n_hi=70, turn=0.5)


def mandate_for(category):
    return dict(MANDATES.get(str(category or "").strip(), _DEFAULT_MANDATE))


# ───────────────────────────────────────────────────────── registry (real AMCs → schemes)
def _read_json(p):
    try:
        return json.loads(open(p, encoding="utf-8").read())
    except Exception:
        return None


def _vset(holds):
    return frozenset(h.get("vst_id") for h in (holds or []) if h.get("vst_id"))


def _portfolio_list(pdir):
    """Every disclosed scheme portfolio as {vs(vst_id set of equity holdings), aum_cr, asof,
    n_equity, holdings:[...], name, amc}. Joined to attribution by HOLDINGS FINGERPRINT (vst_id
    set overlap) — robust to name/plan/format differences (the funds_bridge method), since the
    portfolio files carry no navindia_code (only a slug key)."""
    import glob
    out = []
    for fp in glob.glob(os.path.join(pdir, "*.json")):
        if os.path.basename(fp).startswith("_"):
            continue
        d = _read_json(fp)
        if not d:
            continue
        hold = d.get("holdings") or []
        if not isinstance(hold, list):
            continue
        eq = [h for h in hold if str(h.get("asset_class")) == "equity"]
        vs = _vset(eq)
        if not vs:
            continue
        aum = sum(_f(h.get("mktval_lakh")) for h in hold) / 100.0     # lakh → ₹cr
        out.append({"vs": vs, "aum_cr": round(aum, 1), "asof": d.get("asof"),
                    "n_equity": len(eq), "holdings": eq, "name": d.get("name"), "amc": d.get("amc")})
    return out


def _best_portfolio(av, plist, thresh=0.5):
    """Best holdings-fingerprint match (Jaccard of vst_id sets) for an attribution scheme's
    equity holdings `av`, or None below `thresh`."""
    best, bs = None, thresh
    for p in plist:
        u = len(av | p["vs"])
        if not u:
            continue
        j = len(av & p["vs"]) / u
        if j >= bs:
            bs, best = j, p
    return (best, round(bs, 3)) if best else (None, 0.0)


def registry(amcs=None, categories=None, min_aum_cr=0.0):
    """Build the AMC→scheme registry from the real funds data.

    amcs/categories: optional allow-lists (substring match on AMC, exact on SEBI category).
    Returns {amc: {scheme_code: {scheme, amc, category, benchmark, aum_cr, asof, mandate,
    real_holdings, real_cagr, real_bench_cagr, real_ir, real_verdict}}}.
    """
    import glob
    adir, pdir = _funds_dirs()
    if not adir:
        return {}
    plist = _portfolio_list(pdir)
    out = {}
    for fp in glob.glob(os.path.join(adir, "*.json")):
        if os.path.basename(fp).startswith("_"):
            continue
        a = _read_json(fp)
        if not a:
            continue
        amc = (a.get("amc") or "").strip()
        cat = a.get("sebi_category")
        if amcs and not any(s.lower() in amc.lower() for s in amcs):
            continue
        if categories and cat not in categories:
            continue
        code = str(a.get("navindia_code"))
        # holdings fingerprint = the scheme's own equity vst_id set (full book, from crowd_flow)
        av = _vset(((a.get("crowd_flow") or {}).get("equity_holdings")) or
                   ((a.get("portfolio") or {}).get("top_holdings")))
        pf, score = (_best_portfolio(av, plist) if av else (None, 0.0))
        aum = _f((pf or {}).get("aum_cr"))
        if aum < min_aum_cr:
            continue
        out.setdefault(amc, {})[code] = {
            "scheme": a.get("scheme_name"), "amc": amc, "category": cat,
            "benchmark": a.get("benchmark"), "code": code,
            "aum_cr": aum, "asof": (pf or {}).get("asof"), "n_equity": (pf or {}).get("n_equity"),
            "match_score": score, "equity_pct_fund": (a.get("portfolio") or {}).get("equity_pct_fund"),
            "mandate": mandate_for(cat),
            "real_holdings": (pf or {}).get("holdings") or [],
            "real_cagr": a.get("cagr_paper"), "real_bench_cagr": a.get("cagr_bench"),
            "real_ir": a.get("info_ratio"), "real_verdict": a.get("verdict"),
        }
    return out


# ───────────────────────────────────────────────────────── prices / liquidity
_PX = {"df": None}


def _prices():
    """Cached Date×Symbol adjusted total-return price panel."""
    if _PX["df"] is None:
        from . import stocks as _st
        _PX["df"] = _st.load()
    return _PX["df"]


def price_asof(symbol, asof=None):
    """Latest adjusted close for `symbol` on/before `asof` ('YYYY-MM-DD' or None=latest), or None."""
    df = _prices()
    if df is None or symbol not in df.columns:
        return None
    s = df[symbol].dropna()
    if asof:
        s = s[s.index <= asof]
    if not len(s):
        return None
    return float(s.iloc[-1])


_TURN = {"df": None}


def _turnover_panel():
    """Cached {symbol: (sorted dates, turnover ₹cr array)} from bhav OHLCV. Heavy cold-read;
    guarded — returns {} on failure (then liquidity falls back to the quant snapshot)."""
    if _TURN["df"] is None:
        try:
            from . import bhav_prices as _bp
            d = _bp.load_ohlcv(adjusted=False)
            g = {}
            for sym, sub in d.groupby("sym"):
                sub = sub.sort_values("date")
                g[str(sym)] = (list(sub["date"].astype(str)), [(_f(t) / 1e7) for t in sub["turnover"]])  # ₹ → ₹cr
            _TURN["df"] = g
        except Exception:
            _TURN["df"] = {}
    return _TURN["df"]


def median_turnover_cr(symbol, asof=None, window=21):
    """Trailing-`window` median daily traded value (₹cr) for `symbol` on/before `asof`."""
    g = _turnover_panel().get(str(symbol))
    if not g:
        return None
    dates, vals = g
    if asof:
        n = sum(1 for d in dates if d <= str(asof))
    else:
        n = len(dates)
    seg = [v for v in vals[max(0, n - window):n] if v and v > 0]
    if not seg:
        return None
    seg.sort()
    return seg[len(seg) // 2]


def liquidity_cap_cr(symbol, aum_cr, asof=None):
    """Max ₹cr a book may hold in `symbol` = LIQ_DAYS × trailing median daily turnover.
    Returns None if turnover unknown (caller treats as 'no extra cap beyond the mandate')."""
    mt = median_turnover_cr(symbol, asof)
    if mt is None:
        return None
    return LIQ_DAYS * mt


# ───────────────────────────────────────────────────────── book + daily fact sheet
def new_book(reg_entry):
    """A fresh 100%-cash book seeded with the scheme's real latest AUM."""
    return {
        "scheme": reg_entry["scheme"], "amc": reg_entry["amc"], "code": reg_entry["code"],
        "category": reg_entry["category"], "benchmark": reg_entry["benchmark"],
        "aum0_cr": reg_entry["aum_cr"], "cash_cr": reg_entry["aum_cr"],
        "inception": None, "asof": None,
        "positions": {},   # symbol -> {isin, name, sector, qty, avg_cost, play_type, entry_date, thesis_ref}
        "nav0": 100.0,
    }


def _sector_of(pos):
    return pos.get("sector") or "Unclassified"


def fact_sheet(book, asof, prev_asof=None):
    """Mark the book to market on `asof` → the daily fact sheet (CITI schema). Pure: reads only
    the price panel. Returns {header, rows, sectors, footer}. qty is in SHARES; ₹ values in CRORE.

    avg_cost / mkt_price are PER-SHARE (₹); book_cost / mkt_value are ₹cr (= qty·price/1e7).
    wtd_contribution = pct_assets · pct_change/100 (the holding's points-contribution to the day).
    """
    rows = []
    eq_val = 0.0
    pos = book.get("positions", {})
    for sym, p in pos.items():
        qty = _f(p.get("qty"))
        if qty <= 0:
            continue
        mp = price_asof(sym, asof)
        pp = price_asof(sym, prev_asof) if prev_asof else None
        if mp is None:
            continue
        mv = qty * mp / 1e7        # ₹ → ₹cr
        bc = qty * _f(p.get("avg_cost")) / 1e7
        pv = (qty * pp / 1e7) if pp is not None else None
        pct_chg = ((mp / pp - 1) * 100.0) if (pp and pp > 0) else None
        eq_val += mv
        rows.append({"isin": p.get("isin"), "name": p.get("name") or sym, "sym": sym,
                     "sector": _sector_of(p), "play_type": p.get("play_type"),
                     "qty": round(qty), "avg_cost": round(_f(p.get("avg_cost")), 4),
                     "book_cost": round(bc, 4), "mkt_price": round(mp, 4), "mkt_value": round(mv, 4),
                     "prev_price": (round(pp, 4) if pp is not None else None),
                     "prev_value": (round(pv, 4) if pv is not None else None),
                     "pct_change": (round(pct_chg, 4) if pct_chg is not None else None)})
    cash = _f(book.get("cash_cr"))
    total = eq_val + cash
    # % assets + weighted contribution (needs total)
    for r in rows:
        r["pct_assets"] = round(100.0 * r["mkt_value"] / total, 4) if total > 0 else None
        r["wtd_contribution"] = (round(r["pct_assets"] * r["pct_change"] / 100.0, 5)
                                 if (r["pct_assets"] is not None and r["pct_change"] is not None) else None)
    rows.sort(key=lambda r: (r["sector"], -r["mkt_value"]))
    for i, r in enumerate(rows, 1):
        r["sr"] = i
    # sector subtotals
    sect = {}
    for r in rows:
        s = sect.setdefault(r["sector"], {"sector": r["sector"], "mkt_value": 0.0, "pct_assets": 0.0})
        s["mkt_value"] += r["mkt_value"]
        s["pct_assets"] += (r["pct_assets"] or 0.0)
    sectors = sorted(sect.values(), key=lambda s: -s["mkt_value"])
    day_ret = None
    prev_total = sum((r["prev_value"] or 0.0) for r in rows) + cash
    if prev_asof and prev_total > 0:
        day_ret = round((total / prev_total - 1) * 100.0, 4)
    return {
        "header": {"scheme": book.get("scheme"), "amc": book.get("amc"),
                   "asof": str(asof), "aum_cr": round(total, 2)},
        "rows": rows, "sectors": [{"sector": s["sector"], "pct_assets": round(s["pct_assets"], 3),
                                   "mkt_value": round(s["mkt_value"], 3)} for s in sectors],
        "footer": {"equity_cr": round(eq_val, 3), "cash_cr": round(cash, 3),
                   "total_cr": round(total, 3), "cash_pct": round(100.0 * cash / total, 2) if total > 0 else None,
                   "n_holdings": len(rows), "day_return_pct": day_ret},
    }


# ───────────────────────────────────────────────────────── state IO (git-tracked audit trail)
def _safe(s):
    return "".join(c if (c.isalnum() or c in " -_") else "_" for c in str(s)).strip().replace("  ", " ")


def scheme_dir(amc, scheme):
    d = os.path.join(BOOK_DIR, _safe(amc), _safe(scheme))
    os.makedirs(d, exist_ok=True)
    return d


def save_book(book):
    d = scheme_dir(book["amc"], book["scheme"])
    with open(os.path.join(d, "book.json"), "w", encoding="utf-8") as f:
        json.dump(book, f, indent=1, default=str)
    return d


def append_blotter(amc, scheme, trade):
    d = scheme_dir(amc, scheme)
    with open(os.path.join(d, "blotter.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(trade, default=str) + "\n")


def save_daily(amc, scheme, sheet):
    """Bucket daily fact sheets by month so the audit trail stays navigable."""
    d = os.path.join(scheme_dir(amc, scheme), "daily")
    os.makedirs(d, exist_ok=True)
    ym = str(sheet["header"]["asof"])[:7]
    path = os.path.join(d, f"{ym}.json")
    book = _read_json(path) or {}
    book[str(sheet["header"]["asof"])] = sheet
    with open(path, "w", encoding="utf-8") as f:
        json.dump(book, f, default=str)


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    PILOT_AMCS = ["ICICI Prudential", "SBI Mutual", "Aditya Birla Sun Life", "Quant Mutual"]
    reg = registry(amcs=PILOT_AMCS, min_aum_cr=1.0)
    print(f"registry: {len(reg)} AMCs, {sum(len(v) for v in reg.values())} schemes")
    for amc, schemes in reg.items():
        big = sorted(schemes.values(), key=lambda s: -_f(s["aum_cr"]))[:4]
        print(f"\n{amc}: {len(schemes)} schemes")
        for s in big:
            print(f"   {(s['scheme'] or '?')[:46]:<46} {s['category'][:26]:<26} "
                  f"AUM ₹{s['aum_cr']:>10,.0f} cr  bench={s['benchmark']}  realIR={s['real_ir']}")
