"""Per-stock Quant & Market-Intelligence compute (DISPLAY-PLANE).

Like `fundamentals.py` / `macro.py`, this is computed once in Python and embedded as
values into the terminal (per-symbol `data/quant/<SYM>.json`); the browser only renders
it. There is therefore **NO JS parity port** and no parity burden — but it MUST be
runtime-tested in the deck shell.

It is a per-stock COCKPIT that REUSES the existing engines/substrate, it does not invent a
new analytics stack:
  - prices       : the adjusted total-return panel `stocks.load()` (2000->present, ~4300 syms)
  - liquidity    : per-stock traded value from `bhav_prices.load_ohlcv()` (turnover)
  - benchmarks   : NSE index total-return levels from `data.py` (NIFTY 50/500 + 19 sector indices)
  - fundamentals : the already-computed `fundamentals.compute()` analytics (summary cards only)
  - ownership    : the Screener bundle's `shareholding` table + `data/_corpactions/*.json`

MVP-1 sections built here:
  1. Market Behaviour        (price/returns/DMA/52w/drawdown/liquidity/relative-strength)
  2. Business Confirmation   (4 flags reusing the Fundamentals quality/cash/leverage/growth)
  3. Valuation Context       (P/E percentile vs own history, vs quality & growth, cycle risk)
  4. Ownership & Governance  (promoter/FII/DII holding trend + corporate-action timeline)
  5. Data Quality + Research Snapshot (rule-based per-dimension verdict — diagnostics, NO buy/sell)

Every metric carries plain-English meaning + formula + frequency inline so the UI can show
Definition/Method/Why without guessing. NO buy/sell calls — diagnostics only.
"""
from __future__ import annotations

import os
import json
import math

try:
    import numpy as np
    import pandas as pd
except Exception:                                            # pragma: no cover
    np = None
    pd = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))
INDUSTRY_CACHE = os.path.join(DATA_DIR, "stock_industry.json")
CORPACTION_DIR = os.path.join(DATA_DIR, "_corpactions")

# Trading-day horizons (the panel is daily, ~252/yr).
HZ = {"1M": 21, "3M": 63, "6M": 126, "12M": 252}
DMA = {"50DMA": 50, "200DMA": 200}
HIGH_WINDOW = 252            # "52-week" high/low
LIQ_WINDOW = 21             # ~1 month for the median traded-value read
ILLIQ_TURNOVER_CR = 1.0     # median daily traded value below this (₹ cr) => liquidity warning
CORPACTION_YEARS = 3        # how many recent calendar years of corporate actions to surface

# Broad benchmarks for relative strength (must exist in data.py's index panel).
BROAD_BENCHMARKS = ["NIFTY 50", "NIFTY 500"]

# NSE macro-"Industry" -> the Nifty SECTOR index whose TR we already serve (for sector RS).
# Approximate but useful; unmapped industries simply get no sector RS (labelled n/a).
INDUSTRY_TO_SECTOR = {
    "information technology": "NIFTY IT",
    "financial services": "NIFTY FINANCIAL SERVICES",
    "automobile and auto components": "NIFTY AUTO",
    "healthcare": "NIFTY HEALTHCARE INDEX",
    "fast moving consumer goods": "NIFTY FMCG",
    "oil gas & consumable fuels": "NIFTY OIL & GAS",
    "metals & mining": "NIFTY METAL",
    "power": "NIFTY ENERGY",
    "consumer durables": "NIFTY CONSUMER DURABLES",
    "realty": "NIFTY REALTY",
    "construction": "NIFTY INFRASTRUCTURE",
    "media entertainment & publication": "NIFTY MEDIA",
    "consumer services": "NIFTY CONSUMER SERVICES",
}

# Corporate-action materiality buckets (structural events move share count / control).
_CA_HIGH = ("bonus", "split", "face value", "buyback", "buy back", "rights", "demerg",
            "amalgamat", "merger", "scheme of arrangement", "spin", "delisting", "open offer")
_CA_LOW = ("dividend", "agm", "book closure", "egm", "interest payment", "income distribution")


# ------------------------------------------------------------------ small helpers
def _r(x, d=2):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), d)


def _num(x):
    """Parse a Screener-style cell ('50.39%', '1,234', '-', None) to a float or None."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return None if (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else float(x)
    s = str(x).strip().replace(",", "").replace("%", "").replace("₹", "").replace(" ", "")
    if s in ("", "-", "—", "NA", "N/A", "n/a", "nan"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def _ret(s, n):
    """Trailing simple return over n trading days, in %: s[-1]/s[-1-n] - 1. None if short."""
    if s is None or len(s) <= n:
        return None
    a, b = s.iloc[-1], s.iloc[-1 - n]
    if b is None or b == 0 or pd.isna(a) or pd.isna(b):
        return None
    return (a / b - 1.0) * 100.0


def _vals(series):
    """Clean numeric value list from a fundamentals {'values':[...]} series object."""
    if not isinstance(series, dict):
        return []
    out = []
    for v in (series.get("values") or []):
        out.append(_num(v))
    return out


def _latest(series):
    """Last non-null numeric value of a fundamentals series object."""
    for v in reversed(_vals(series)):
        if v is not None:
            return v
    return None


def _at_back(series, n):
    """The value n positions before the last *non-null* value (for YoY-style change)."""
    nn = [v for v in _vals(series) if v is not None]
    if len(nn) <= n:
        return None
    return nn[-1 - n]


def _median_tail(series, n=5):
    """Median of the last n non-null values of a fundamentals series object."""
    nn = [v for v in _vals(series) if v is not None]
    if not nn:
        return None
    tail = sorted(nn[-n:])
    m = len(tail)
    return tail[m // 2] if m % 2 else (tail[m // 2 - 1] + tail[m // 2]) / 2.0


def _flag(label, value, unit, status, read, meaning, why, why_fail=None):
    """One compact diagnostic flag. status in {good, ok, weak, na}. NO buy/sell wording."""
    return {"label": label, "value": _r(value, 2) if isinstance(value, (int, float)) else value,
            "unit": unit, "status": status, "read": read,
            "meaning": meaning, "why_useful": why, "why_it_can_fail": why_fail}


# ------------------------------------------------------------------ industry map
def load_industry_map() -> dict:
    """{SYMBOL: "Industry"} from the cached NIFTY-500 constituents (the Industry column
    `stocks.py` otherwise discards). Best-effort: returns {} if not cached / unfetchable —
    sector RS then degrades to n/a. Refresh with fetch_industry_map()."""
    try:
        if os.path.exists(INDUSTRY_CACHE):
            with open(INDUSTRY_CACHE, encoding="utf-8") as f:
                d = json.load(f)
            return {str(k).upper(): v for k, v in (d.get("industry") or d).items()}
    except Exception:
        pass
    return {}


def fetch_industry_map(log=print) -> dict:
    """Pull Symbol->Industry from the static NSE NIFTY-500 constituents CSV (same hosts
    `stocks.py` already uses) and cache it. Network, best-effort; never raises."""
    try:
        import requests
        from vistas import stocks as _stocks
    except Exception as e:                                   # pragma: no cover
        log(f"[stock_intel] industry fetch unavailable: {e}")
        return {}
    ua = {"User-Agent": "Mozilla/5.0"}
    for url in getattr(_stocks, "CONSTITUENTS_URLS", []):
        try:
            r = requests.get(url, headers=ua, timeout=30)
            if r.status_code == 200 and "Symbol" in r.text and "Industry" in r.text:
                df = pd.read_csv(pd.io.common.StringIO(r.text))
                scol = next((c for c in df.columns if c.strip().lower() == "symbol"), None)
                icol = next((c for c in df.columns if c.strip().lower() == "industry"), None)
                if scol and icol:
                    m = {str(s).strip().upper(): str(v).strip()
                         for s, v in zip(df[scol], df[icol]) if pd.notna(s) and pd.notna(v)}
                    try:
                        with open(INDUSTRY_CACHE, "w", encoding="utf-8") as f:
                            json.dump({"industry": m, "source": url}, f, ensure_ascii=False)
                    except Exception:
                        pass
                    log(f"[stock_intel] industry map: {len(m)} symbols <- {url}")
                    return m
        except Exception:
            continue
    log("[stock_intel] industry map: fetch failed (sector RS will be n/a)")
    return {}


# ------------------------------------------------------------------ corporate actions
def load_corpactions(years=CORPACTION_YEARS) -> dict:
    """{SYMBOL: [ {date, subject, materiality}, ... ]} from the most recent `years`
    `data/_corpactions/<year>.json` files (NSE corporate-actions dumps). Best-effort."""
    out = {}
    if not os.path.isdir(CORPACTION_DIR):
        return out
    try:
        files = sorted(f for f in os.listdir(CORPACTION_DIR) if f.endswith(".json"))
    except Exception:
        return out
    for fn in files[-max(1, years):]:
        try:
            with open(os.path.join(CORPACTION_DIR, fn), encoding="utf-8") as f:
                rows = json.load(f)
        except Exception:
            continue
        for rec in (rows or []):
            sym = str(rec.get("symbol") or "").strip().upper()
            if not sym:
                continue
            subj = str(rec.get("subject") or "").strip()
            date = str(rec.get("exDate") or rec.get("recDate") or "").strip()
            if not subj or date in ("", "-"):
                continue
            low = subj.lower()
            mat = "Low"
            if any(k in low for k in _CA_HIGH):
                mat = "High"
            elif not any(k in low for k in _CA_LOW):
                mat = "Med"
            out.setdefault(sym, []).append({"date": date, "subject": subj, "materiality": mat})
    return out


# ------------------------------------------------------------------ context (load once)
def build_context(industry_map=None) -> dict:
    """Load the shared panels ONCE so per-symbol compute is cheap. Returns a dict the
    compute functions read. Degrades gracefully — any missing piece just disables the
    metrics that need it."""
    from vistas import stocks as _stocks
    ctx = {"prices": None, "index": None, "turnover": None,
           "industry": industry_map if industry_map is not None else load_industry_map(),
           "corpactions": {}}
    try:
        ctx["prices"] = _stocks.load()                       # Date x SYMBOL, adjusted TR
    except Exception:
        pass
    try:
        from vistas import data as _data
        idx = None
        for attr in ("wide", "frame", "levels_frame"):
            if hasattr(_data, attr):
                try:
                    idx = getattr(_data, attr)()
                    break
                except Exception:
                    idx = None
        if idx is None and hasattr(_data, "load"):
            try:
                idx = _data.load()
            except Exception:
                idx = None
        ctx["index"] = idx
    except Exception:
        pass
    try:
        from vistas import bhav_prices as _bp
        tv = _bp.load_ohlcv(adjusted=False, columns=["date", "sym", "turnover"])
        ctx["turnover"] = tv
    except Exception:
        ctx["turnover"] = None
    try:
        ctx["corpactions"] = load_corpactions()
    except Exception:
        ctx["corpactions"] = {}
    return ctx


def _index_series(ctx, name):
    idx = ctx.get("index")
    if idx is None or name not in getattr(idx, "columns", []):
        return None
    return idx[name].dropna()


# ------------------------------------------------------------------ MODULE 1: Market Behaviour
def _market_behaviour(sym, ctx):
    sp = ctx.get("prices")
    if sp is None or sym not in getattr(sp, "columns", []):
        return {"ok": False, "na": "no price history for this symbol"}
    s = sp[sym].dropna()
    if len(s) < 30:
        return {"ok": False, "na": f"price history too short ({len(s)} days)"}
    px = float(s.iloc[-1])
    asof = s.index[-1].strftime("%Y-%m-%d")

    # --- trailing point returns (simple, %): px[t]/px[t-n]-1 ------------------
    returns = {k: _r(_ret(s, n), 1) for k, n in HZ.items()}

    # --- distance from the 52-week high / low (%): px/max(252) - 1 ------------
    win = s.tail(HIGH_WINDOW)
    hi, lo = float(win.max()), float(win.min())
    dist_high = _r((px / hi - 1.0) * 100.0, 1) if hi else None
    dist_low = _r((px / lo - 1.0) * 100.0, 1) if lo else None

    # --- price vs 50/200-day moving average (%): px/mean(N) - 1 --------------
    dma = {}
    for label, n in DMA.items():
        if len(s) >= n:
            m = float(s.tail(n).mean())
            dma[label] = {"value": _r(m, 1), "px_vs": _r((px / m - 1.0) * 100.0, 1),
                          "above": bool(px >= m)}
        else:
            dma[label] = {"value": None, "px_vs": None, "above": None, "na": f"<{n}d history"}
    golden = None
    if dma["50DMA"]["value"] and dma["200DMA"]["value"]:
        golden = "above" if dma["50DMA"]["value"] >= dma["200DMA"]["value"] else "below"

    # --- drawdown: current underwater from the running peak, + worst over 1Y/3Y -
    def maxdd(series):
        if series is None or len(series) < 2:
            return None
        dd = (series / series.cummax() - 1.0).min()
        return _r(dd * 100.0, 1)
    cur_dd = _r((px / float(s.cummax().iloc[-1]) - 1.0) * 100.0, 1)
    drawdown = {"current_from_peak": cur_dd, "from_52w_high": dist_high,
                "maxdd_1y": maxdd(s.tail(252)), "maxdd_3y": maxdd(s.tail(756))}

    # --- liquidity: median daily traded value over ~1M (₹ cr) + a warning -----
    liquidity = {"median_turnover_cr": None, "warning": None, "na": None}
    tv = ctx.get("turnover")
    try:
        if tv is not None and len(tv):
            sub = tv[tv["sym"].astype(str).str.upper() == str(sym).upper()]
            if len(sub):
                t = sub.sort_values("date")["turnover"].dropna().tail(LIQ_WINDOW)
                if len(t):
                    med_cr = float(t.median()) / 1e7        # rupees -> ₹ crore
                    liquidity["median_turnover_cr"] = _r(med_cr, 2)
                    if med_cr < ILLIQ_TURNOVER_CR:
                        liquidity["warning"] = (f"Thinly traded — median ~₹{med_cr:.2f} cr/day "
                                                f"(< ₹{ILLIQ_TURNOVER_CR:.0f} cr); signals/exits may slip.")
            else:
                liquidity["na"] = "no turnover rows for this symbol"
        else:
            liquidity["na"] = "turnover panel unavailable"
    except Exception:
        liquidity["na"] = "turnover read failed"

    # --- relative strength vs broad + sector benchmarks ----------------------
    def rel(bench_name):
        b = _index_series(ctx, bench_name)
        if b is None or len(b) < 60:
            return None
        j = pd.concat([s.rename("s"), b.rename("b")], axis=1).dropna()
        if len(j) < 30:
            return None
        rels = {}
        for k, n in HZ.items():
            sr, br = _ret(j["s"], n), _ret(j["b"], n)
            rels[k] = _r((sr - br), 1) if (sr is not None and br is not None) else None
        tail = j.tail(HIGH_WINDOW)
        ratio = (tail["s"] / tail["b"])
        base = ratio.iloc[0]
        line = {"dates": [d.strftime("%Y-%m-%d") for d in tail.index],
                "values": [_r(v / base * 100.0, 2) for v in ratio.to_numpy()]} if base else None
        return {"benchmark": bench_name, "rel_return": rels, "rs_line": line}

    rs_broad = {bn: rel(bn) for bn in BROAD_BENCHMARKS}
    industry = ctx.get("industry", {}).get(str(sym).upper())
    sector_index = INDUSTRY_TO_SECTOR.get((industry or "").strip().lower())
    rs_sector = rel(sector_index) if sector_index else None

    return {
        "ok": True, "asof": asof, "price": _r(px, 2),
        "returns": returns,
        "high_low": {"dist_from_52w_high_pct": dist_high, "dist_from_52w_low_pct": dist_low,
                     "high_52w": _r(hi, 2), "low_52w": _r(lo, 2)},
        "dma": dma, "golden_cross": golden,
        "drawdown": drawdown, "liquidity": liquidity,
        "rs_broad": rs_broad, "industry": industry, "sector_index": sector_index, "rs_sector": rs_sector,
        "formulas": {
            "returns": "Trailing point return = price[t] / price[t-N] - 1 (N = 21/63/126/252 trading days); adjusted total-return prices.",
            "dist_from_52w_high": "price[t] / max(price, last 252d) - 1 (how far below the 1-year peak).",
            "dma": "price[t] / mean(price, last N days) - 1; 'above' = price >= the N-day average.",
            "drawdown": "current = price[t]/running-peak - 1; maxDD = min(price/cummax - 1) over the window.",
            "liquidity": "median daily traded value (turnover ÷ 1e7 = ₹ cr) over the last ~21 trading days.",
            "relative_strength": "horizon: stock return - benchmark return (pp); line: (stock/benchmark) rebased to 100 over the last year.",
        },
        "notes": {
            "source": "adjusted total-return price panel (bhavcopy-reconstructed) + NSE index TR + bhavcopy turnover.",
            "frequency": "daily", "confidence": "high where history is long; flagged low if thin/short.",
            "why_useful": "what the market is saying about the stock — trend, leadership vs the index/sector, and tradeability.",
            "why_it_can_fail": "corporate-action gaps, illiquid names, or a short listing history can distort returns/DMA/RS.",
        },
    }


# ------------------------------------------------------------------ MODULE 2: Business Confirmation
def _business_confirmation(fa):
    """4 compact flags reusing the Fundamentals analytics — NO new charts, NO buy/sell.
    Banks: cash-conversion & leverage are not meaningful in the non-financial schema -> n/a."""
    if not fa or not fa.get("ok"):
        return {"ok": False, "na": "no fundamentals for this symbol"}
    bank = bool(fa.get("is_bank"))
    flags = []

    # 1) Business quality (the Fundamentals composite, 0-100)
    q = (fa.get("quality") or {}).get("score")
    flags.append(_flag(
        "Business quality", q, "/100",
        "na" if q is None else ("good" if q >= 70 else "ok" if q >= 45 else "weak"),
        None if q is None else (f"High-quality business ({q:.0f}/100)." if q >= 70
                                else f"Average quality ({q:.0f}/100)." if q >= 45
                                else f"Low-quality ({q:.0f}/100)."),
        "Composite of profitability, return on capital, balance-sheet safety and cash conversion (the Fundamentals-tab Quality score).",
        "A durable, high-return business compounds value; a low-quality one needs a bigger margin of safety.",
        "A single composite can mask a weak component; open the Fundamentals tab for the breakdown."))

    # 2) Cash conversion — does reported profit turn into cash? (CFO / PAT, median 5y)
    if bank:
        flags.append(_flag("Cash conversion", None, "×", "na", None,
            "Operating cash flow is not a meaningful concept for a lender (deposits & loans dominate the cash statement).",
            "For banks, earnings quality is read from asset quality / provisioning instead (not in MVP-1).",
            None))
    else:
        cfo_pat = _median_tail((fa.get("cashflow") or {}).get("CFO/PAT"), 5)
        flags.append(_flag(
            "Cash conversion", cfo_pat, "×",
            "na" if cfo_pat is None else ("good" if cfo_pat >= 0.8 else "ok" if cfo_pat >= 0.6 else "weak"),
            None if cfo_pat is None else (f"{cfo_pat:.2f}× of profit came in as operating cash (5-yr median) — well backed." if cfo_pat >= 0.8
                                          else f"{cfo_pat:.2f}× — partly cash-backed." if cfo_pat >= 0.6
                                          else f"Only {cfo_pat:.2f}× — profit not turning into cash (watch receivables/inventory)."),
            "Median of (cash from operations ÷ net profit) over the last 5 years.",
            "Profit you can't collect as cash is fragile; >0.8× means earnings are real cash, <0.6× is an accrual red flag.",
            "One-off working-capital swings or a capex year can distort a single ratio; the 5-yr median smooths it."))

    # 3) Balance-sheet safety — debt load & ability to service it
    if bank:
        flags.append(_flag("Balance-sheet safety", None, "", "na", None,
            "A bank is structurally levered (deposits are its raw material); D/E & interest-cover don't apply.",
            "Read bank safety from capital adequacy (CAR) and NPAs instead (not in MVP-1).",
            None))
    else:
        lev = fa.get("balance") or {}        # leverage lives under the "balance" block in fundamentals.py
        de = _latest(lev.get("D/E"))
        ic = _latest(lev.get("Interest coverage"))
        if de is None:
            st, read = "na", None
        elif de > 2 or (ic is not None and ic < 2):
            st = "weak"
            read = f"Leveraged (D/E {de:.2f}×" + (f", interest cover {ic:.1f}×)." if ic is not None else ").")
        elif de <= 0.5 or (de <= 1 and (ic is None or ic >= 4)):
            st = "good"
            read = f"Conservatively financed (D/E {de:.2f}×" + (f", interest cover {ic:.1f}×)." if ic is not None else ").")
        else:
            st = "ok"
            read = f"Moderate leverage (D/E {de:.2f}×" + (f", interest cover {ic:.1f}×)." if ic is not None else ").")
        flags.append(_flag(
            "Balance-sheet safety", de, "× D/E", st, read,
            "Latest debt-to-equity and interest coverage (EBIT ÷ interest) from the balance sheet.",
            "Low debt and high interest cover let a business survive a downturn; high debt amplifies trouble.",
            "Off-balance-sheet leases/guarantees aren't captured; a cash-rich firm can show low D/E yet still be risky elsewhere."))

    # 4) Earnings momentum — is the latest TTM profit growing, and accelerating?
    #    NB: fundamentals.py ttm_yoy/accel are FRACTIONS (0.08 = 8%) -> ×100 for a % read.
    pat = (fa.get("growth") or {}).get("PAT") or {}
    yoyf = _num(pat.get("ttm_yoy"))
    yoy = yoyf * 100.0 if yoyf is not None else None
    accel = _latest(pat.get("accel"))            # fraction; only its sign is used below
    if yoy is None:
        st, read = "na", None
    elif yoy >= 8 and (accel is None or accel >= 0):
        st, read = "good", f"Profit up {yoy:.0f}% YoY (latest TTM)" + (" and accelerating." if (accel or 0) > 0 else ".")
    elif yoy >= 0:
        st, read = "ok", f"Profit up {yoy:.0f}% YoY — modest." if yoy > 0 else "Profit roughly flat YoY."
    else:
        st, read = "weak", f"Profit down {abs(yoy):.0f}% YoY (latest TTM)."
    flags.append(_flag(
        "Earnings momentum", yoy, "% YoY", st, read,
        "Trailing-twelve-month net profit vs the year-ago TTM (and whether the YoY rate is rising = 'accelerating').",
        "Improving, accelerating earnings tend to drive re-rating; falling profit warns of a deteriorating business.",
        "TTM can be lumpy for cyclicals/one-offs; cross-check the multi-year trend on the Fundamentals tab."))

    return {"ok": True, "is_bank": bank, "flags": flags}


# ------------------------------------------------------------------ MODULE 3: Valuation Context
def _valuation_context(fa):
    """Where the stock trades vs its OWN history, paired with quality & growth, plus the
    cyclical-trap caveat. Context only — NOT a buy/sell call."""
    if not fa or not fa.get("ok"):
        return {"ok": False, "na": "no fundamentals for this symbol"}
    val = fa.get("valuation") or {}
    snap = val.get("snapshot") or {}
    pe = _num(val.get("pe_now"))
    pct = _num(val.get("pe_percentile"))
    med = _num(val.get("median_pe"))
    ey = _num(snap.get("earnings_yield"))
    pb = _num(snap.get("pb"))
    eve = _num(snap.get("ev_ebitda"))
    bank = bool(fa.get("is_bank"))

    # cheapness vs own 10y P/E history (percentile: low = cheap vs its past)
    if pct is None:
        cheap_status, cheap_read = "na", None
    elif pct < 30:
        cheap_status = "good"
        cheap_read = f"P/E at the {pct:.0f}-percentile of its 10-yr range — cheap vs its own history."
    elif pct > 70:
        cheap_status = "weak"
        cheap_read = f"P/E at the {pct:.0f}-percentile of its 10-yr range — expensive vs its own history."
    else:
        cheap_status = "ok"
        cheap_read = f"P/E at the {pct:.0f}-percentile of its 10-yr range — mid-range vs its own history."

    # quality & growth to pair against the multiple (PAT 5y CAGR is a FRACTION -> ×100 for %)
    q = (fa.get("quality") or {}).get("score")
    _cagr = ((fa.get("growth") or {}).get("PAT") or {}).get("cagr")
    g5f = _num(_cagr.get("5y")) if isinstance(_cagr, dict) else None
    g5 = g5f * 100.0 if g5f is not None else None
    peg = (pe / g5) if (pe and g5 and g5 > 0) else None

    # cyclical-trap flag: a low multiple on peak earnings can be a value trap
    cyc_flags = [f for f in ((fa.get("cycle") or {}).get("flags") or [])
                 if any(k in f.lower() for k in ("peak", "elevated margin", "high margin", "cycle", "above-trend"))]

    expectation = None
    if peg is not None:
        expectation = (f"At {pe:.1f}× earnings for ~{g5:.0f}% 5-yr profit CAGR, the market is paying "
                       f"≈{peg:.2f}× growth (PEG). >2 = a lot already priced in; <1 = cheap if growth holds. "
                       f"This PEG divides TTM P/E by TRAILING 5-year annual PAT CAGR (endpoint-to-endpoint) — "
                       f"a time-axis mismatch vs the textbook FORWARD-EPS PEG, and one abnormal base/terminal "
                       f"year distorts the CAGR. A rough trailing valuation-vs-growth gauge, not a precise forward PEG.")
    elif pe and (g5 is None):
        expectation = f"Trades at {pe:.1f}× earnings; multi-year growth not estimable, so 'priced-in' growth is unclear."

    return {
        "ok": True, "is_bank": bank,
        "pe_now": _r(pe, 2), "pe_percentile": _r(pct, 0), "median_pe": _r(med, 2),
        "earnings_yield_pct": _r(ey, 2), "pb": _r(pb, 2), "ev_ebitda": _r(eve, 2),
        "quality_score": _r(q, 0), "pat_cagr_5y_pct": _r(g5, 1), "peg": _r(peg, 2),
        "cheapness": {"status": cheap_status, "read": cheap_read},
        "expectations": expectation,
        "cyclical_caveat": (cyc_flags[0] if cyc_flags else None),
        "meaning": ("P/E percentile = where today's price-to-earnings sits within the stock's own last-10-yr range "
                    "(low = cheap vs its past). Paired with the Quality score and 5-yr profit growth so a cheap "
                    "multiple is judged against business strength, not in isolation."),
        "why_useful": "Cheapness only matters relative to quality and growth — a low P/E on a declining or peak-cyclical business is a trap, not a bargain.",
        "why_it_can_fail": ("P/E breaks for loss-makers / one-offs; for banks use P/B not EV/EBITDA; a multiple cheap vs "
                            "history can stay cheap if the business has structurally de-rated."),
    }


# ------------------------------------------------------------------ MODULE 4: Ownership & Governance
_OWN_ROWS = [("Promoter", ("promoter",), ("pledge",)),
             ("FII", ("fii", "foreign"), ()),
             ("DII", ("dii", "domestic"), ()),
             ("Government", ("government",), ()),
             ("Public", ("public",), ())]


def _ownership(sym, bundle, ctx):
    """Promoter/FII/DII holding trend from the Screener `shareholding` table + a 3-yr
    corporate-action timeline. Labelled placeholders for pledge-detail / bulk-deals /
    announcements / results-calendar (MVP-2)."""
    out = {"ok": False, "na": None, "holders": {}, "pledge": None, "events": [],
           "placeholders": [
               {"label": "Promoter pledge (detail)", "note": "share-level pledge history — fetchable via NSE, deferred to MVP-2."},
               {"label": "Bulk / block deals", "note": "large on-market trades — NSE feed, deferred to MVP-2."},
               {"label": "Insider / SAST disclosures", "note": "promoter & designated-person dealings — deferred to MVP-2."},
               {"label": "Exchange announcements", "note": "NSE/BSE filings stream — deferred to MVP-2."},
               {"label": "Results calendar", "note": "next board-meeting / results date — deferred to MVP-2."},
               {"label": "Credit ratings", "note": "rating-agency actions — deferred to MVP-2."},
           ]}

    # --- corporate-action timeline (always available, symbol-keyed) -----------
    evs = (ctx.get("corpactions") or {}).get(str(sym).upper(), [])
    out["events"] = evs[:40]

    # --- shareholding table from the Screener bundle --------------------------
    sh = (((bundle or {}).get("statements") or {}).get("shareholding")) if bundle else None
    if not sh or not isinstance(sh, list):
        out["na"] = "no shareholding table cached for this symbol"
        out["ok"] = bool(evs)            # still useful if only the event timeline exists
        return out

    # quarter columns are every key except the row-label ("Unnamed: 0")
    cols = [k for k in (sh[0].keys()) if str(k).lower().startswith("unnamed") is False]
    # keep them in the bundle's own order (Screener gives oldest->newest left to right)
    def series_for(name_keys, exclude_keys):
        for row in sh:
            lbl = str(row.get("Unnamed: 0") or "").strip().lower()
            if any(k in lbl for k in name_keys) and not any(x in lbl for x in exclude_keys):
                vals = [_num(row.get(c)) for c in cols]
                if any(v is not None for v in vals):
                    return vals
        return None

    holders = {}
    for label, keys, excl in _OWN_ROWS:
        v = series_for(keys, excl)
        if v is None:
            continue
        latest = next((x for x in reversed(v) if x is not None), None)
        yago = v[-5] if len(v) >= 5 else (v[0] if v else None)   # ~4 quarters back
        chg = (latest - yago) if (latest is not None and yago is not None) else None
        holders[label] = {"periods": cols, "values": [_r(x, 2) for x in v],
                          "latest_pct": _r(latest, 2), "chg_1y_pp": _r(chg, 2)}
    out["holders"] = holders

    # promoter pledge row (if Screener exposes it)
    pl = series_for(("pledge",), ())
    if pl is not None:
        latest_pl = next((x for x in reversed(pl) if x is not None), None)
        out["pledge"] = {"periods": cols, "values": [_r(x, 2) for x in pl], "latest_pct": _r(latest_pl, 2)}

    # trend reads (no buy/sell)
    reads = {}
    prom = holders.get("Promoter")
    if prom and prom.get("latest_pct") is not None and prom["latest_pct"] < 1:
        reads["Promoter"] = "No identifiable promoter (professionally managed / widely held)."
    elif prom and prom.get("chg_1y_pp") is not None:
        d = prom["chg_1y_pp"]
        reads["Promoter"] = (f"Promoter stake {('up' if d>0 else 'down' if d<0 else 'flat')} "
                             f"{abs(d):.2f}pp over ~1yr → {prom['latest_pct']:.2f}%."
                             if abs(d) >= 0.01 else f"Promoter stake stable at {prom['latest_pct']:.2f}%.")
    for k in ("FII", "DII"):
        h = holders.get(k)
        if h and h.get("chg_1y_pp") is not None:
            d = h["chg_1y_pp"]
            reads[k] = (f"{k}s {('adding' if d>0 else 'reducing' if d<0 else 'steady')} "
                        f"({d:+.2f}pp YoY → {h['latest_pct']:.2f}%).")
    out["reads"] = reads
    out["ok"] = bool(holders or evs)
    out["meaning"] = ("Quarterly shareholding (% held by Promoters / Foreign (FII) / Domestic (DII) institutions / "
                      "Public) from Screener, plus exchange corporate actions tagged by materiality "
                      "(High = structural: bonus/split/buyback/merger; Low = routine: dividend/AGM).")
    out["why_useful"] = ("Rising promoter & institutional ownership and clean corporate actions are confirming signals; "
                         "falling promoter stake or pledging is a governance/skin-in-the-game warning.")
    out["why_it_can_fail"] = ("Shareholding is a lagged quarterly snapshot; promoter % can fall for benign reasons "
                              "(QIP, ESOP dilution). Pledge & insider detail are deferred (placeholders below).")
    return out


# ------------------------------------------------------------------ MODULE 5: Research Snapshot
def _verdict(score, lo=-2, hi=2):
    if score is None:
        return "insufficient"
    if score >= hi:
        return "positive"
    if score <= lo:
        return "negative"
    return "neutral"


# the EXACT rule for each dimension, shown in the UI so the verdict is auditable (not a black box)
_DIM_RULES = {
    "Market": ("Points: +1 price above its 200-day average · +1 a golden cross (50-day ≥ 200-day) · "
               "+1 within 10% of the 52-week high · +1 beating the NIFTY 500 over 12 months; −1 for each "
               "opposite, and −1 if more than 25% below its peak. Verdict: positive at +2 or more, "
               "negative at −2 or less, otherwise neutral. The Market signals (above 200-DMA, golden "
               "cross, near 52-week high, beating the index) are CORRELATED facets of ONE trend, not "
               "independent confirmations — read the Market verdict as trend-strength, not a tally of "
               "independent votes."),
    "Business": ("Points across the four business flags: +1 for each that is 'good' (quality, cash "
                 "conversion, balance-sheet safety, earnings momentum), −1 for each that is 'weak'. "
                 "Verdict: positive at +2 or more, negative at −2 or less."),
    "Valuation": ("Cheap — P/E below the 30th percentile of its own 10-year range — scores +2 (supportive); "
                  "expensive (above the 70th) scores −2; mid-range is 0. A peak/cyclical-earnings flag "
                  "discounts a cheap read to +1. NOTE: 'positive' means the multiple looks supportive vs the "
                  "stock's OWN history — it is NOT a buy call."),
    "Ownership": ("Points: +1 promoter stake rising · −1 promoter stake falling more than 1pp in a year · "
                  "−1 promoter pledge above 10% · +1 for each of FII / DII accumulating (>0.5pp in a year). "
                  "Verdict: positive at +1 or more, negative at −1 or less."),
}


def _research_snapshot(market, business, valuation, ownership, fa):
    """Rule-based per-dimension verdict. Each dimension carries the EXACT signals that drove it
    (`drivers`) + its `rule`, so the verdict is auditable, not a black box. The cross-dimension
    synthesis lists roll the strongest signals up. DIAGNOSTIC ONLY — explicitly NOT advice."""
    positives, risks, monitor, caveats = [], [], [], []
    dd_map = {"Market": [], "Business": [], "Valuation": [], "Ownership": []}

    def drive(dim, sign, text, to_global=True):
        """Record one signal under its dimension (+ / − / ·) and optionally roll it up."""
        dd_map[dim].append(("+ " if sign > 0 else "− " if sign < 0 else "· ") + text)
        if to_global and sign > 0:
            positives.append(text)
        elif to_global and sign < 0:
            risks.append(text)

    # ---- Market dimension ----------------------------------------------------
    msc = None
    if market and market.get("ok"):
        msc = 0
        d200 = (market.get("dma") or {}).get("200DMA") or {}
        if d200.get("above") is True:
            msc += 1; drive("Market", +1, f"Trades above its 200-day average ({d200.get('px_vs'):+.0f}%).")
        elif d200.get("above") is False:
            msc -= 1; drive("Market", -1, f"Below its 200-day average ({d200.get('px_vs'):+.0f}%) — downtrend.")
        if market.get("golden_cross") == "above":
            msc += 1; drive("Market", +1, "50-day average above the 200-day (golden cross).")
        elif market.get("golden_cross") == "below":
            drive("Market", 0, "50-day average still below the 200-day (no golden cross).", to_global=False)
        dh = (market.get("high_low") or {}).get("dist_from_52w_high_pct")
        if dh is not None and dh > -10:
            msc += 1; drive("Market", +1, f"Within {abs(dh):.0f}% of its 52-week high.")
        elif dh is not None and dh < -25:
            msc -= 1; drive("Market", -1, f"{abs(dh):.0f}% below its 52-week high.")
        rs500 = ((market.get("rs_broad") or {}).get("NIFTY 500") or {}).get("rel_return") or {}
        r12 = rs500.get("12M")
        if r12 is not None and r12 > 0:
            msc += 1; drive("Market", +1, f"Outpacing the NIFTY 500 by {r12:+.0f}pp over 12 months.")
        elif r12 is not None and r12 < 0:
            msc -= 1; drive("Market", -1, f"Lagging the NIFTY 500 by {r12:+.0f}pp over 12 months.")
        cur_dd = (market.get("drawdown") or {}).get("current_from_peak")
        if cur_dd is not None and cur_dd < -25:
            msc -= 1; drive("Market", -1, f"In a deep drawdown ({cur_dd:.0f}% from its peak).")
        liq = (market.get("liquidity") or {}).get("warning")
        if liq:
            caveats.append(liq)
    market_v = _verdict(msc)

    # ---- Business dimension --------------------------------------------------
    bsc = None
    if business and business.get("ok"):
        bsc = 0
        for fl in business.get("flags", []):
            st, rd = fl.get("status"), (fl.get("read") or fl.get("label"))
            if st == "good":
                bsc += 1; drive("Business", +1, rd)
            elif st == "weak":
                bsc -= 1; drive("Business", -1, rd)
            elif st == "ok" and rd:
                drive("Business", 0, rd, to_global=False)
    business_v = _verdict(bsc, lo=-2, hi=2)

    # ---- Valuation dimension (positive == supportive/cheap; context, not a call)
    vsc = None
    if valuation and valuation.get("ok"):
        cs = (valuation.get("cheapness") or {}).get("status")
        cr = (valuation.get("cheapness") or {}).get("read")
        vsc = 0
        if cs == "good":
            vsc = 2; drive("Valuation", +1, cr)
        elif cs == "weak":
            vsc = -2; drive("Valuation", -1, cr)
        elif cr:
            drive("Valuation", 0, cr, to_global=False)
        if valuation.get("cyclical_caveat"):
            caveats.append("Cheap multiple may sit on peak/cyclical earnings — " + valuation["cyclical_caveat"])
            if vsc > 0:
                vsc = 1; drive("Valuation", 0, "discounted: earnings may be at a cyclical peak (value-trap risk).", to_global=False)
    valuation_v = _verdict(vsc, lo=-2, hi=2)

    # ---- Ownership dimension -------------------------------------------------
    osc = None
    if ownership and ownership.get("ok") and ownership.get("holders"):
        osc = 0
        prom = ownership["holders"].get("Promoter")
        if prom and prom.get("chg_1y_pp") is not None:
            if prom["chg_1y_pp"] >= 0.01:
                osc += 1; drive("Ownership", +1, f"Promoter stake rising ({prom['chg_1y_pp']:+.2f}pp YoY).")
            elif prom["chg_1y_pp"] <= -1.0:
                osc -= 1; drive("Ownership", -1, f"Promoter stake falling ({prom['chg_1y_pp']:+.2f}pp YoY).")
        pledge = ownership.get("pledge") or {}
        if pledge.get("latest_pct") and pledge["latest_pct"] > 10:
            osc -= 1; drive("Ownership", -1, f"Promoter pledge elevated ({pledge['latest_pct']:.0f}%).")
        for k in ("FII", "DII"):
            h = ownership["holders"].get(k)
            if h and h.get("chg_1y_pp") is not None and h["chg_1y_pp"] > 0.5:
                osc += 1; drive("Ownership", +1, f"{k}s accumulating ({h['chg_1y_pp']:+.2f}pp YoY).")
    ownership_v = _verdict(osc, lo=-1, hi=1)

    # ---- monitor-next & caveats ---------------------------------------------
    monitor.append("Next quarterly results (board-meeting date is an MVP-2 placeholder).")
    if valuation and valuation.get("ok") and valuation.get("median_pe"):
        monitor.append(f"Re-rating toward / away from its median P/E ({valuation['median_pe']}×).")
    if market and market.get("ok"):
        monitor.append("Relative-strength inflection vs its sector index.")
    if ownership and ownership.get("ok"):
        monitor.append("Promoter pledge / institutional-holding changes next quarter.")
    if fa and fa.get("is_bank"):
        caveats.append("Bank — cash-flow & EV/leverage metrics use the bank schema (assessed via P/B, ROE, deposits).")
    if market and market.get("ok"):
        hist_note = (market.get("notes") or {}).get("why_it_can_fail")
        if hist_note:
            caveats.append(hist_note)

    # ---- overall confidence --------------------------------------------------
    have = sum(1 for v in (market_v, business_v, valuation_v, ownership_v) if v != "insufficient")
    thin = bool(market and market.get("ok") and (market.get("liquidity") or {}).get("warning"))
    confidence = "high" if (have >= 4 and not thin) else "medium" if have >= 2 else "low"

    def _dim(verdict, score, name):
        return {"verdict": verdict, "score": score, "drivers": dd_map[name], "rule": _DIM_RULES[name]}

    return {
        "dimensions": {
            "Market": _dim(market_v, msc, "Market"),
            "Business": _dim(business_v, bsc, "Business"),
            "Valuation": _dim(valuation_v, vsc, "Valuation"),
            "Ownership": _dim(ownership_v, osc, "Ownership"),
        },
        "top_positives": positives[:6],
        "key_risks": risks[:6],
        "monitor_next": monitor[:5],
        "caveats": caveats[:5],
        "confidence": confidence,
        "disclaimer": ("Diagnostics only — a structured reading of price behaviour, business fundamentals, valuation "
                       "and ownership for your OWN judgement. This is NOT a buy/sell/hold recommendation."),
        "method": ("Each dimension is a transparent points tally of the named signals listed beneath it — open a "
                   "dimension to see the exact signals that fired and its rule. Verdict = positive at +2 or more / "
                   "negative at −2 or less for Market, Business and Valuation; ±1 for Ownership. A 'positive' "
                   "valuation means the multiple looks supportive vs the stock's OWN history — never a buy call."),
    }


# ------------------------------------------------------------------ public compute
def compute(sym, ctx=None, fund_analytics=None, bundle=None) -> dict:
    """Per-stock Quant & MI block (MVP-1, 5 sections). REUSES `fund_analytics`
    (= fundamentals.compute output) and the Screener `bundle` (for shareholding); both are
    optional and degrade gracefully. Never raises — returns ok:False on a bad symbol."""
    if ctx is None:
        ctx = build_context()
    try:
        market = _market_behaviour(sym, ctx)
    except Exception as e:                                   # pragma: no cover
        market = {"ok": False, "na": f"market-behaviour error: {e}"}
    try:
        business = _business_confirmation(fund_analytics)
    except Exception as e:                                   # pragma: no cover
        business = {"ok": False, "na": f"business error: {e}"}
    try:
        valuation = _valuation_context(fund_analytics)
    except Exception as e:                                   # pragma: no cover
        valuation = {"ok": False, "na": f"valuation error: {e}"}
    try:
        ownership = _ownership(sym, bundle, ctx)
    except Exception as e:                                   # pragma: no cover
        ownership = {"ok": False, "na": f"ownership error: {e}"}
    try:
        snapshot = _research_snapshot(market, business, valuation, ownership, fund_analytics or {})
    except Exception as e:                                   # pragma: no cover
        snapshot = {"na": f"snapshot error: {e}"}

    name = (fund_analytics or {}).get("name") or (bundle or {}).get("name") or sym
    return {
        "ok": bool(market.get("ok")), "symbol": sym, "name": name,
        "is_bank": bool((fund_analytics or {}).get("is_bank")),
        "market": market, "business": business, "valuation": valuation,
        "ownership": ownership, "snapshot": snapshot,
        "data_quality": {
            "price_asof": market.get("asof") if market.get("ok") else None,
            "fundamentals_fetched": (bundle or {}).get("fetched"),
            "has_fundamentals": bool(fund_analytics and fund_analytics.get("ok")),
            "has_shareholding": bool(ownership.get("holders")),
            "liquidity_warning": (market.get("liquidity") or {}).get("warning") if market.get("ok") else None,
            "note": ("Market data is daily and current; fundamentals & shareholding are as of the last cached "
                     "Screener fetch (quarterly/annual). Confidence is lower for thin or short-history names."),
        },
    }


# ------------------------------------------------------------------ per-symbol writer (for the deck)
def build_all(fund_all, out_dir, ctx=None, log=print, flows_by_sym=None, holders_by_sym=None) -> dict:
    """Write per-symbol `data/quant/<SYM>.json` for every symbol in `fund_all`
    ({sym: screener-bundle-with-['analytics']}). Returns {sym: name} manifest. Reuses the
    already-loaded bundles + a single shared context, so it adds no new fetch.
    `flows_by_sym` (optional, from funds_flows.build_stock_series) attaches the cross-AMC
    net-active-flow / crowding series for the symbol as `smart_money_flow`."""
    if ctx is None:
        ctx = build_context()
    os.makedirs(out_dir, exist_ok=True)
    flows_by_sym = flows_by_sym or {}
    holders_by_sym = holders_by_sym or {}
    manifest, n_ok, n_flow = {}, 0, 0
    for sym, bundle in (fund_all or {}).items():
        try:
            fa = (bundle or {}).get("analytics")
            q = compute(sym, ctx, fund_analytics=fa, bundle=bundle)
            smf = flows_by_sym.get(sym) or flows_by_sym.get(str(sym).upper())
            if smf:
                q["smart_money_flow"] = smf
                n_flow += 1
            hold = holders_by_sym.get(sym) or holders_by_sym.get(str(sym).upper())
            if hold:
                q["fund_holders"] = hold
            with open(os.path.join(out_dir, _safe_name(sym) + ".json"), "w", encoding="utf-8") as f:
                f.write(json.dumps(q, allow_nan=False, separators=(",", ":")))
            manifest[sym] = q.get("name") or sym
            n_ok += 1
        except Exception as e:                               # pragma: no cover
            log(f"[stock_intel] {sym}: build failed: {e}")
    log(f"[stock_intel] wrote {n_ok} quant files ({n_flow} with smart-money flow) -> {out_dir}")
    return manifest


def _safe_name(sym):
    # MUST match deck._safe_name (urllib.parse.quote(sym, safe="")) and the JS lazyURL(), so a
    # fetch of data/quant/<encoded>.json hits the right file even for names like "M&M".
    from urllib.parse import quote
    return quote(str(sym), safe="")


# ------------------------------------------------------------------ self-test
def _selftest():
    from vistas import screener, fundamentals
    ctx = build_context()
    sp = ctx.get("prices")
    print("context: prices", None if sp is None else sp.shape,
          "| index", None if ctx.get("index") is None else len(getattr(ctx["index"], "columns", [])),
          "| turnover", "yes" if ctx.get("turnover") is not None else "no",
          "| industry", len(ctx.get("industry") or {}),
          "| corpactions", len(ctx.get("corpactions") or {}))
    for sym in ("TCS", "HDFCBANK", "RELIANCE", "INFY"):
        try:
            b = screener.load(sym)
            b["analytics"] = fundamentals.compute(b)
        except Exception as e:
            b = None
            print(f"\n=== {sym}: screener/fundamentals load failed: {e}")
        q = compute(sym, ctx, fund_analytics=(b or {}).get("analytics"), bundle=b)
        m = q["market"]
        if not m.get("ok"):
            print(f"\n=== {sym}: {m.get('na')}")
            continue
        print(f"\n=== {sym}  px={m['price']} asof {m['asof']}  bank={q['is_bank']}  industry={m['industry']} sector={m['sector_index']}")
        print("  returns:", m["returns"], "| 200DMA px_vs", m["dma"]["200DMA"]["px_vs"], "| golden", m["golden_cross"])
        print("  drawdown cur:", m["drawdown"]["current_from_peak"], "| liq ₹cr", m["liquidity"]["median_turnover_cr"])
        print("  BUSINESS flags:")
        for fl in (q["business"].get("flags") or []):
            print(f"    - {fl['label']}: {fl['status']} ({fl['value']}{fl['unit']}) — {fl['read']}")
        v = q["valuation"]
        if v.get("ok"):
            print(f"  VALUATION: P/E {v['pe_now']} ({v['pe_percentile']}%ile) median {v['median_pe']} | "
                  f"PEG {v['peg']} | cheapness={v['cheapness']['status']}")
            if v.get("cyclical_caveat"):
                print("    cyclical caveat:", v["cyclical_caveat"])
        own = q["ownership"]
        print("  OWNERSHIP:", {k: own["holders"][k].get("latest_pct") for k in own.get("holders", {})},
              "| events", len(own.get("events", [])), "| pledge", (own.get("pledge") or {}).get("latest_pct"))
        for k, rd in (own.get("reads") or {}).items():
            print("    -", rd)
        sn = q["snapshot"]
        print("  SNAPSHOT verdicts:", {d: sn["dimensions"][d]["verdict"] for d in sn["dimensions"]},
              "| confidence", sn["confidence"])
        print("    positives:", sn["top_positives"][:3])
        print("    risks:", sn["key_risks"][:3])


if __name__ == "__main__":
    _selftest()
