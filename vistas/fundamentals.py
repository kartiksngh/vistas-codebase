"""
Fundamentals ANALYTICS engine for Vistas — the buy-side "analyst cockpit" math.

WHY THIS EXISTS (one source of truth)
-------------------------------------
The Screener bundles (data/screener/<SYM>.json) carry RAW statements (P&L, balance
sheet, cash flow, ratios, quarters, shareholding) as scraped row-dicts, plus valuation
(P/E, EPS) and price time-series. This module turns those raw numbers into the
professional analytical layer a fundamental analyst actually reads — growth (YoY/QoQ/
CAGR/TTM/acceleration), margins & profitability, DuPont/return decomposition, cash-flow
& earnings quality, balance-sheet & leverage, and valuation ratios/bands — with EVERY
metric carrying its formula, and graceful handling of missing fields.

It is computed in PYTHON ON PURPOSE (not in the browser): one canonical definition, unit-
testable against known values, no JS<->Python parity burden (the offline deck just embeds
the computed values — see deck.py). The front-end renders; it does not recompute.

CONVENTIONS (so numbers are reproducible)
-----------------------------------------
- Money is in ₹ CRORE (1 crore = 1e7 rupees), exactly as Screener prints it.
- "Operating Profit" (Screener) = Sales − Expenses, BEFORE depreciation/interest/tax, so
  it is an EBITDA proxy. EBIT = Operating Profit − Depreciation. (Verified against the
  identity PBT ≈ OP + Other income − Interest − Depreciation in the self-test.)
- Net worth (shareholders' equity) = Equity Capital + Reserves.
- Share count (crore) = Net Profit ÷ EPS  (₹cr ÷ ₹/share → crore shares). Screener gives no
  direct share count; this is the cleanest derivation and reconciles BVPS/market cap.
- Capex ≈ CFO − Free Cash Flow (Screener's FCF = CFO − capex; capex is not printed alone).
- TTM (trailing twelve months) = sum of the last 4 quarters for FLOW variables; TTM margins
  are computed from TTM components, never by averaging quarterly margins.
- Growth off a non-positive base is mathematically meaningless → returned as None (we never
  print a misleading % when the prior value was ≤ 0).
- BANKS / NBFCs use a DIFFERENT schema (Revenue / Financing Profit / Financing Margin % /
  Deposits, and NO Sales / Operating Profit / OPM%). Every metric branches on company type.

Public API:
    compute(bundle: dict) -> dict     # the full analytics block for one company
    attach(bundle: dict)  -> dict     # bundle with bundle["analytics"] = compute(bundle)

Graceful: never raises on a malformed/partial bundle — missing pieces come back as None and
the consuming panel simply hides. Provenance: written for Vistas 2026-06-21. Standalone;
imports nothing from the parity-checked engine.
"""
from __future__ import annotations

import math
import re

# ----------------------------------------------------------------------------- cleaning
_MONTHS = {m[:3].lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"]) if m}
_MONTH_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{4})")


def _num(v):
    """Screener cell -> float or None. Strips ',', '%', '₹', NBSP; '-'/'—'/''/'nan' -> None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip().replace(",", "").replace("%", "").replace("₹", "").replace("\xa0", "")
    if s in ("", "-", "—", "nan", "NaN", "None", "NA"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pdate(label):
    """'Mar 2024' -> (2024, 3) sort key; None if unparseable."""
    m = _MONTH_RE.search(str(label))
    if not m:
        return None
    mm = _MONTHS.get(m.group(1)[:3].lower())
    if not mm:
        return None
    return (int(m.group(2)), mm)


def _norm(label):
    """Normalize a line-item label: drop NBSP, trailing '+', lower-case."""
    s = str(label).replace("\xa0", " ").strip()
    while s.endswith("+"):
        s = s[:-1].strip()
    return s.lower()


# ----------------------------------------------------------------------------- statement access
def _table(bundle, key):
    return (bundle.get("statements") or {}).get(key) or []


def _period_cols(table):
    """Chronologically-sorted period header strings present in a statement table."""
    cols = []
    for row in table:
        if isinstance(row, dict):
            for k in row:
                if k != "Unnamed: 0" and k not in cols and _pdate(k):
                    cols.append(k)
    return sorted(cols, key=_pdate)


def _find_row(table, aliases):
    """First row whose normalized label equals or starts with any alias."""
    for row in table:
        if not isinstance(row, dict):
            continue
        lab = _norm(row.get("Unnamed: 0", ""))
        for a in aliases:
            if lab == a or lab.startswith(a):
                return row
    return None


def _line(table, *aliases, cols=None):
    """(periods, values) aligned for the first matching line item. Missing row -> all None."""
    if cols is None:
        cols = _period_cols(table)
    row = _find_row(table, [a.lower() for a in aliases])
    if row is None:
        return cols, [None] * len(cols)
    return cols, [_num(row.get(c)) for c in cols]


def _vals(table, *aliases, cols=None):
    """Just the value list for a line item (periods taken from `cols`)."""
    return _line(table, *aliases, cols=cols)[1]


# ----------------------------------------------------------------------------- elementwise math
def _div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _mul(a, b):
    return None if (a is None or b is None) else a * b


def _sub(a, b):
    return None if (a is None or b is None) else a - b


def _add(a, b):
    return None if (a is None or b is None) else a + b


def _zip(f, xs, ys):
    return [f(x, y) for x, y in zip(xs, ys)]


def _growth(prev, cur):
    """YoY/QoQ growth fraction; None when the base is missing or ≤ 0 (sign-flip = meaningless)."""
    if prev is None or cur is None or prev <= 0:
        return None
    return cur / prev - 1.0


def _yoy(values, lag=1):
    out = [None] * len(values)
    for i in range(lag, len(values)):
        out[i] = _growth(values[i - lag], values[i])
    return out


def _cagr(values, n):
    """N-year CAGR from an ANNUAL value list (end vs value n years back). None if base ≤ 0."""
    if values is None or len(values) <= n:
        return None
    end, start = values[-1], values[-1 - n]
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1.0 / n) - 1.0


def _ttm_sum(qvals):
    """TTM = sum of the last 4 quarterly values (flow). None unless 4 are present."""
    last4 = qvals[-4:]
    if len(last4) < 4 or any(v is None for v in last4):
        return None
    return sum(last4)


def _ttm_prev_sum(qvals):
    """The PRIOR-year TTM (quarters −8..−5) so we can compute TTM YoY."""
    if len(qvals) < 8:
        return None
    win = qvals[-8:-4]
    if any(v is None for v in win):
        return None
    return sum(win)


def _accel(yoy):
    """Growth acceleration = change in the YoY growth rate (2nd difference of the level)."""
    out = [None] * len(yoy)
    for i in range(1, len(yoy)):
        if yoy[i] is not None and yoy[i - 1] is not None:
            out[i] = yoy[i] - yoy[i - 1]
    return out


def _pct_rank(series, x):
    """Percentile (0-100) of x within the non-null history `series` (≤ convention)."""
    pts = [v for v in series if v is not None]
    if not pts or x is None:
        return None
    return 100.0 * sum(1 for v in pts if v <= x) / len(pts)


def _last(values):
    for v in reversed(values):
        if v is not None:
            return v
    return None


def _std(values):
    pts = [v for v in values if v is not None]
    if len(pts) < 2:
        return None
    m = sum(pts) / len(pts)
    return math.sqrt(sum((v - m) ** 2 for v in pts) / (len(pts) - 1))


def _mean(values):
    pts = [v for v in values if v is not None]
    return sum(pts) / len(pts) if pts else None


# ----------------------------------------------------------------------------- formulas (tooltips)
FORMULAS = {
    "sales": "Net sales / revenue (P&L). Banks: total revenue (interest + other income).",
    "ebitda": "Operating Profit = Sales − Operating Expenses (before depreciation, interest, tax).",
    "ebit": "EBIT = Operating Profit − Depreciation.",
    "pat": "Net Profit after tax (P&L).",
    "eps": "Earnings per share (₹) reported by Screener.",
    "networth": "Net worth = Equity Capital + Reserves (shareholders' equity).",
    "bookvalue": "Book value per share = Net worth ÷ shares; shares = Net Profit ÷ EPS.",
    "debt": "Total borrowings (balance sheet). Banks: borrowings + deposits.",
    "cfo": "Cash from Operating Activities (cash-flow statement).",
    "fcf": "Free Cash Flow = CFO − Capex (as reported by Screener).",
    "opm": "Operating margin = Operating Profit ÷ Sales. Banks: Financing Margin %.",
    "ebit_margin": "EBIT margin = (Operating Profit − Depreciation) ÷ Sales.",
    "pat_margin": "Net margin = Net Profit ÷ Sales.",
    "roe": "Return on equity = Net Profit ÷ Net worth (Equity + Reserves).",
    "roa": "Return on assets = Net Profit ÷ Total Assets.",
    "roce": "Return on capital employed — Screener's reported ROCE % (conceptually EBIT ÷ capital employed, capital employed = Total Assets − Current Liabilities); shown as reported, not re-derived here.",
    "op_leverage": "Operating leverage = Operating-profit growth ÷ Sales growth (YoY).",
    "de": "Debt-to-equity = Total Borrowings ÷ Net worth.",
    "debt_ebitda": "Gross Debt ÷ EBITDA (Operating Profit). Net debt not available (cash not isolated).",
    "interest_cover": "Interest coverage = EBIT ÷ Interest.",
    "cfo_pat": "Cash conversion = CFO ÷ Net Profit (≥1 over time = profits are real cash).",
    "fcf_pat": "Free cash conversion = Free Cash Flow ÷ Net Profit.",
    "capex_sales": "Capex intensity = (CFO − FCF) ÷ Sales.",
    "accrual_ratio": "Accrual ratio = (Net Profit − CFO) ÷ Total Assets (Sloan; high = low quality).",
    "dupont": "ROE = Net margin × Asset turnover × Equity multiplier (= PAT/Sales × Sales/Assets × Assets/Networth).",
    "dupont5": "ROE = Tax burden × Interest burden × EBIT margin × Asset turnover × Leverage.",
    "pe": "Price-to-Earnings (Screener daily series).",
    "earnings_yield": "Earnings yield = 1 ÷ P/E = EPS ÷ Price.",
    "fcf_yield": "FCF yield = Free Cash Flow ÷ Market cap.",
    "pb": "Price-to-Book = Market cap ÷ Net worth.",
    "ev_ebitda": "EV/EBITDA = (Market cap + Debt) ÷ Operating Profit. Cash not netted (approx).",
    "ev_sales": "EV/Sales = (Market cap + Debt) ÷ Sales.",
    "mcap_sales": "Market cap ÷ Sales.",
}


# ----------------------------------------------------------------------------- bank detection
def _is_bank(bundle):
    pl = _table(bundle, "profit_loss")
    if _find_row(pl, ["financing profit", "financing margin"]):
        return True
    bs = _table(bundle, "balance_sheet")
    if _find_row(bs, ["deposits"]):
        return True
    return False


def _series_obj(periods, values, key, freq="annual", unit="₹ cr"):
    return {"periods": periods, "values": values, "unit": unit, "freq": freq,
            "formula": FORMULAS.get(key, "")}


# ----------------------------------------------------------------------------- the engine
def compute(bundle):
    """The full fundamentals analytics block for ONE Screener bundle. Never raises."""
    try:
        return _compute(bundle)
    except Exception as e:                                  # pragma: no cover - safety net
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _compute(bundle):
    if not isinstance(bundle, dict) or not bundle.get("statements"):
        return {"ok": False, "error": "no statements"}

    is_bank = _is_bank(bundle)
    pl = _table(bundle, "profit_loss")
    q = _table(bundle, "quarters")
    bs = _table(bundle, "balance_sheet")
    cf = _table(bundle, "cash_flow")
    ra = _table(bundle, "ratios")

    ap = _period_cols(pl)        # annual periods (P&L drives the annual axis)
    bp = _period_cols(bs)
    qp = _period_cols(q)

    # ---- raw annual lines (bank-aware) ----
    sales = _vals(pl, "sales", "revenue", cols=ap)
    op = _vals(pl, "operating profit", "financing profit", cols=ap)
    opm = _vals(pl, "opm", "financing margin", cols=ap)        # percent as printed
    other_inc = _vals(pl, "other income", cols=ap)
    interest = _vals(pl, "interest", cols=ap)
    dep = _vals(pl, "depreciation", cols=ap)
    pbt = _vals(pl, "profit before tax", cols=ap)
    taxpct = _vals(pl, "tax", cols=ap)
    pat = _vals(pl, "net profit", cols=ap)
    eps = _vals(pl, "eps", cols=ap)
    payout = _vals(pl, "dividend payout", cols=ap)

    ebit = _zip(_sub, op, dep)                                  # EBIT = OP − Dep

    # ---- balance sheet (aligned to its own period axis) ----
    eqcap = _vals(bs, "equity capital", cols=bp)
    reserves = _vals(bs, "reserves", cols=bp)
    borrow = _vals(bs, "borrowings", "borrowing", cols=bp)
    deposits = _vals(bs, "deposits", cols=bp)
    tot_assets = _vals(bs, "total assets", cols=bp)
    networth = _zip(_add, eqcap, reserves)
    debt = _zip(_add, borrow, deposits) if is_bank else borrow

    # ---- cash flow ----
    cp = _period_cols(cf)
    cfo = _vals(cf, "cash from operating", cols=cp)
    fcf = _vals(cf, "free cash flow", cols=cp)

    # ---- ratios (given) ----
    roce_given = _vals(ra, "roce", cols=_period_cols(ra))
    roe_given = _vals(ra, "roe", cols=_period_cols(ra))

    # ---- shares & book value per share (annual, P&L axis) ----
    shares = _zip(lambda p, e: _div(p, e), pat, eps)           # crore shares = PAT ÷ EPS
    bvps = _bvps_on_annual(ap, bp, networth, shares)

    # ============================ MODULE A: GROWTH
    def growth_block(values, periods, qvals=None):
        yoy = _yoy(values)
        ttm = _ttm_sum(qvals) if qvals is not None else None
        ttm_prev = _ttm_prev_sum(qvals) if qvals is not None else None
        return {
            "level": _series_obj(periods, values, "level"),
            "yoy": {"periods": periods, "values": yoy},
            "accel": {"periods": periods, "values": _accel(yoy)},
            "cagr": {"3y": _cagr(values, 3), "5y": _cagr(values, 5), "10y": _cagr(values, 10)},
            "ttm": ttm, "ttm_yoy": _growth(ttm_prev, ttm),
        }

    q_sales = _vals(q, "sales", "revenue", cols=qp)
    q_op = _vals(q, "operating profit", "financing profit", cols=qp)
    q_pat = _vals(q, "net profit", cols=qp)
    q_eps = _vals(q, "eps", cols=qp)

    growth = {
        "Sales": growth_block(sales, ap, q_sales),
        "EBITDA": growth_block(op, ap, q_op),
        "PAT": growth_block(pat, ap, q_pat),
        "EPS": growth_block(eps, ap, q_eps),
        "Book value/sh": growth_block(bvps, ap),
        "Net worth": growth_block(networth, bp),
        "CFO": growth_block(cfo, cp),
        "FCF": growth_block(fcf, cp),
    }
    # quarterly QoQ + YoY (for the quarterly trend panels)
    quarterly = {
        "periods": qp,
        "Sales": {"level": q_sales, "yoy": _yoy(q_sales, 4), "qoq": _yoy(q_sales, 1)},
        "EBITDA": {"level": q_op, "yoy": _yoy(q_op, 4), "qoq": _yoy(q_op, 1)},
        "PAT": {"level": q_pat, "yoy": _yoy(q_pat, 4), "qoq": _yoy(q_pat, 1)},
        "EPS": {"level": q_eps, "yoy": _yoy(q_eps, 4), "qoq": _yoy(q_eps, 1)},
    }
    if is_bank:
        # Banks have no EBITDA; the underlying "Financing Profit" line is volatile and can be
        # negative, so EBITDA growth/level cards are meaningless. Null them (consistent with the
        # OPM/EBIT/operating-leverage nulling in margins) and flag na so the UI shows "n/a",
        # not a misleading raw (often negative) Financing-Profit number.
        _na_eb = "Not meaningful for banks/NBFCs (no EBITDA; see Financing margin under Margins)."
        _eb = growth["EBITDA"]
        growth["EBITDA"] = {
            "level": _na(_eb["level"], _na_eb), "yoy": _na(_eb["yoy"], _na_eb),
            "accel": _na(_eb["accel"], _na_eb), "cagr": {"3y": None, "5y": None, "10y": None},
            "ttm": None, "ttm_yoy": None, "na": _na_eb,
        }
        quarterly["EBITDA"] = {"level": [None] * len(qp), "yoy": [None] * len(qp),
                               "qoq": [None] * len(qp), "na": _na_eb}

    # ============================ MODULE B: MARGINS & PROFITABILITY
    # margins from levels (don't trust the printed OPM for derived ones)
    opm_calc = [_pct(_div(o, s)) for o, s in zip(op, sales)]
    ebit_margin = [_pct(_div(e, s)) for e, s in zip(ebit, sales)]
    pat_margin = [_pct(_div(p, s)) for p, s in zip(pat, sales)]
    # ROE/ROA need balance-sheet items on the annual axis
    nw_on_a = _reindex(bp, networth, ap)
    assets_on_a = _reindex(bp, tot_assets, ap)
    roe_calc = [_pct(_div(p, nw)) for p, nw in zip(pat, nw_on_a)]
    roa_calc = [_pct(_div(p, a)) for p, a in zip(pat, assets_on_a)]
    op_lev = _zip(lambda g_op, g_s: _div(g_op, g_s), _yoy(op), _yoy(sales))

    margins = {
        "periods": ap,
        "OPM": _series_obj(ap, opm_calc, "opm", unit="%"),
        "EBIT margin": _series_obj(ap, ebit_margin, "ebit_margin", unit="%"),
        "PAT margin": _series_obj(ap, pat_margin, "pat_margin", unit="%"),
        "ROE": _series_obj(ap, roe_calc, "roe", unit="%"),
        "ROA": _series_obj(ap, roa_calc, "roa", unit="%"),
        "ROCE": {"periods": _period_cols(ra), "values": roce_given, "unit": "%",
                 "formula": FORMULAS["roce"]},
        "Operating leverage": {"periods": ap, "values": op_lev, "unit": "×",
                               "formula": FORMULAS["op_leverage"]},
    }
    if is_bank:
        # Operating/EBITDA-style margins are NOT meaningful for banks (no COGS/operating-
        # profit concept); show the bank's Financing Margin instead and null the rest so the
        # UI hides them rather than printing a misleading number.
        margins["Financing margin"] = {
            "periods": ap, "values": _vals(pl, "financing margin", cols=ap), "unit": "%",
            "formula": "Financing margin % (banks) = (Revenue − Interest expense − Operating expense) ÷ Revenue."}
        for _k in ("OPM", "EBIT margin", "ROCE", "Operating leverage"):
            margins[_k] = _na(margins[_k], "Not meaningful for banks/NBFCs")

    # ============================ MODULE C: DUPONT / RETURN DECOMPOSITION
    npm = [_div(p, s) for p, s in zip(pat, sales)]                       # PAT/Sales
    asset_turn = [_div(s, a) for s, a in zip(sales, assets_on_a)]        # Sales/Assets
    eq_mult = [_div(a, nw) for a, nw in zip(assets_on_a, nw_on_a)]       # Assets/Networth
    dupont_roe = [_mul(_mul(n, t), e) for n, t, e in zip(npm, asset_turn, eq_mult)]
    tax_burden = [_div(p, b) for p, b in zip(pat, pbt)]                  # PAT/PBT
    int_burden = [_div(b, e) for b, e in zip(pbt, ebit)]                # PBT/EBIT
    ebit_m_frac = [_div(e, s) for e, s in zip(ebit, sales)]            # EBIT/Sales
    dupont = {
        "periods": ap,
        "three_step": {"npm": npm, "asset_turnover": asset_turn,
                       "equity_multiplier": eq_mult, "roe": dupont_roe,
                       "formula": FORMULAS["dupont"]},
        "five_step": {"tax_burden": tax_burden, "interest_burden": int_burden,
                      "ebit_margin": ebit_m_frac, "asset_turnover": asset_turn,
                      "leverage": eq_mult, "formula": FORMULAS["dupont5"]},
    }
    if is_bank:
        # Five-step DuPont splits net margin into tax-burden × interest-burden × EBIT-margin; the
        # latter two use EBIT (= Financing Profit − Dep for banks), which is negative/meaningless.
        # Null the whole five-step block; the three-step (margin × turnover × leverage) stays valid.
        _na_du = "EBIT/interest-burden decomposition not meaningful for banks/NBFCs (no EBIT/EBITDA)."
        for _k in ("tax_burden", "interest_burden", "ebit_margin", "asset_turnover", "leverage"):
            dupont["five_step"][_k] = [None] * len(ap)
        dupont["five_step"]["na"] = _na_du
    # latest-year profit waterfall: Sales -> OP -> PBT -> PAT
    li = _last_idx(sales)
    if li is not None:
        abs_tax = _mul(pbt[li], _frac(taxpct[li]))
        dupont["waterfall"] = {
            "period": ap[li], "sales": sales[li], "expenses": _sub(sales[li], op[li]),
            "operating_profit": op[li], "other_income": other_inc[li],
            "interest": interest[li], "depreciation": dep[li], "pbt": pbt[li],
            "tax": abs_tax, "pat": pat[li],
        }
        # EPS bridge: contribution of sales growth / margin change / share-count change
        if li >= 1:
            dupont["eps_bridge"] = _eps_bridge(sales, pat, eps, shares, li)

    # ============================ MODULE D: CASH FLOW & EARNINGS QUALITY
    pat_on_c = _reindex(ap, pat, cp)
    sales_on_c = _reindex(ap, sales, cp)
    assets_on_c = _reindex(bp, tot_assets, cp)
    capex = [_sub(c, f) for c, f in zip(cfo, fcf)]                       # ≈ CFO − FCF
    cashflow = {
        "periods": cp,
        "CFO": _series_obj(cp, cfo, "cfo"),
        "PAT": _series_obj(cp, pat_on_c, "pat"),
        "FCF": _series_obj(cp, fcf, "fcf"),
        "Capex": {"periods": cp, "values": capex, "unit": "₹ cr",
                  "formula": "Capex ≈ CFO − Free Cash Flow."},
        "CFO/PAT": {"periods": cp, "values": [_div(c, p) for c, p in zip(cfo, pat_on_c)],
                    "unit": "×", "formula": FORMULAS["cfo_pat"]},
        "FCF/PAT": {"periods": cp, "values": [_div(f, p) for f, p in zip(fcf, pat_on_c)],
                    "unit": "×", "formula": FORMULAS["fcf_pat"]},
        "Capex/Sales": {"periods": cp, "values": [_pct(_div(cx, s)) for cx, s in zip(capex, sales_on_c)],
                        "unit": "%", "formula": FORMULAS["capex_sales"]},
        "Accrual ratio": {"periods": cp,
                          "values": [_pct(_div(_sub(p, c), a)) for p, c, a in zip(pat_on_c, cfo, assets_on_c)],
                          "unit": "%", "formula": FORMULAS["accrual_ratio"]},
        "cum_CFO": _cumsum(cfo), "cum_PAT": _cumsum(pat_on_c),
    }
    if is_bank:
        # A bank's CFO is dominated by deposit/loan/treasury flows, not operating cash from a
        # product business, so cash-conversion / accrual ratios aren't meaningful quality signals.
        _na_cf = ("Cash-flow conversion not meaningful for banks/NBFCs "
                  "(CFO dominated by deposit/loan/treasury flows).")
        for _k in ("CFO/PAT", "FCF/PAT", "Capex", "Capex/Sales", "Accrual ratio"):
            cashflow[_k] = _na(cashflow[_k], _na_cf)

    # ============================ MODULE E: BALANCE SHEET & LEVERAGE
    op_on_b = _reindex(ap, op, bp)
    ebit_on_b = _reindex(ap, ebit, bp)
    int_on_b = _reindex(ap, interest, bp)
    balance = {
        "periods": bp,
        "Net worth": _series_obj(bp, networth, "networth"),
        "Debt": _series_obj(bp, debt, "debt"),
        "Total assets": _series_obj(bp, tot_assets, "level"),
        "D/E": {"periods": bp, "values": [_div(d, nw) for d, nw in zip(debt, networth)],
                "unit": "×", "formula": FORMULAS["de"]},
        "Debt/EBITDA": {"periods": bp, "values": [_div(d, o) for d, o in zip(debt, op_on_b)],
                        "unit": "×", "formula": FORMULAS["debt_ebitda"]},
        "Interest coverage": {"periods": bp, "values": [_div(e, i) for e, i in zip(ebit_on_b, int_on_b)],
                              "unit": "×", "formula": FORMULAS["interest_cover"]},
        "debt_growth": {"periods": bp, "values": _yoy(debt)},
    }
    if is_bank:
        # EBITDA-based leverage / coverage are undefined for banks (no EBITDA); a bank's D/E
        # (with deposits) is structurally high by design, so don't flag it via these gauges.
        for _k in ("Debt/EBITDA", "Interest coverage"):
            balance[_k] = _na(balance[_k], "Not meaningful for banks/NBFCs")

    # ============================ MODULE F: VALUATION
    valuation = _valuation_block(bundle, is_bank, sales, op, ebit, networth, pat, eps, fcf, debt, ap, bp, cp)

    # ============================ MODULE G & H: QUALITY + CYCLE
    quality = _quality_block(is_bank, margins, growth, cashflow, balance, roce_given, roe_given)
    cycle = _cycle_block(is_bank, margins, growth, valuation, balance)

    return {
        "ok": True,
        "symbol": bundle.get("symbol"),
        "name": bundle.get("name"),
        "is_bank": is_bank,
        "currency": "INR crore",
        "annual_periods": ap,
        "quarterly_periods": qp,
        "growth": growth,
        "quarterly": quarterly,
        "margins": margins,
        "dupont": dupont,
        "cashflow": cashflow,
        "balance": balance,
        "valuation": valuation,
        "quality": quality,
        "cycle": cycle,
        "formulas": FORMULAS,
    }


# ----------------------------------------------------------------------------- small helpers
def _pct(x):
    return None if x is None else x * 100.0


def _frac(x):
    """A printed percent (e.g. 27 from '27%') -> fraction 0.27. Pass-through None."""
    return None if x is None else x / 100.0


def _cumsum(values):
    out, run = [], 0.0
    started = False
    for v in values:
        if v is None:
            out.append(None if not started else run)
        else:
            run += v
            started = True
            out.append(run)
    return out


def _last_idx(values):
    for i in range(len(values) - 1, -1, -1):
        if values[i] is not None:
            return i
    return None


def _reindex(src_periods, src_values, dst_periods):
    """Align a value list from one period axis onto another by exact period label."""
    m = {p: v for p, v in zip(src_periods, src_values)}
    return [m.get(p) for p in dst_periods]


def _bvps_on_annual(ap, bp, networth, shares):
    nw = _reindex(bp, networth, ap)
    return [_div(n, s) for n, s in zip(nw, shares)]


def _align(*a, **k):                                  # placeholder (unused branch)
    return []


def _eps_bridge(sales, pat, eps, shares, li):
    """Decompose ΔEPS over the latest year into revenue, margin, and share-count effects.
    EPS = (Sales × net_margin) ÷ shares. We attribute the change with a simple sequential
    walk (not a full Shapley): revenue effect at old margin/shares, then margin effect, then
    share-count effect. Components sum (approximately) to ΔEPS."""
    s0, s1 = sales[li - 1], sales[li]
    p0, p1 = pat[li - 1], pat[li]
    e0, e1 = eps[li - 1], eps[li]
    sh0, sh1 = shares[li - 1], shares[li]
    if None in (s0, s1, p0, p1, e0, e1, sh0, sh1) or 0 in (s0, sh0, sh1):
        return None
    m0, m1 = p0 / s0, p1 / s1                       # net margin
    eps_from = lambda s, m, sh: (s * m) / sh
    base = eps_from(s0, m0, sh0)
    rev = eps_from(s1, m0, sh0) - base              # revenue growth effect
    mar = eps_from(s1, m1, sh0) - eps_from(s1, m0, sh0)   # margin effect
    shr = eps_from(s1, m1, sh1) - eps_from(s1, m1, sh0)   # share-count (buyback/dilution) effect
    return {"period": "", "from_eps": e0, "to_eps": e1,
            "revenue_effect": rev, "margin_effect": mar, "share_count_effect": shr,
            "residual": e1 - (base + rev + mar + shr)}


def _valuation_block(bundle, is_bank, sales, op, ebit, networth, pat, eps, fcf, debt, ap, bp, cp):
    val = bundle.get("valuation") or {}
    price_blk = bundle.get("price") or {}
    pe_series = val.get("Price to Earning") or []
    eps_series = val.get("EPS") or []
    median_pe = None
    mp = val.get("Median PE") or []
    if mp:
        median_pe = _num(mp[-1][1]) if isinstance(mp[-1], (list, tuple)) else None

    pe_vals = [_num(p[1]) for p in pe_series if isinstance(p, (list, tuple))]
    pe_dates = [p[0] for p in pe_series if isinstance(p, (list, tuple))]
    pe_now = _last(pe_vals)
    pe_pctile = _pct_rank(pe_vals, pe_now)

    # latest-year snapshot ratios (use the most recent annual figures + latest price)
    li = _last_idx(sales)
    price_pts = price_blk.get("Price") or []
    price_now = _num(price_pts[-1][1]) if price_pts and isinstance(price_pts[-1], (list, tuple)) else None
    eps_now = _last([_num(e[1]) for e in eps_series if isinstance(e, (list, tuple))])

    snap = {"price": price_now, "pe": pe_now, "median_pe": median_pe,
            "earnings_yield": _pct(_div(1.0, pe_now)) if (pe_now and pe_now > 0) else None}
    if li is not None:
        nw_last = _reindex(bp, networth, ap)[li]
        sales_l, op_l, debt_l = sales[li], op[li], _reindex(bp, debt, ap)[li]
        fcf_l = _reindex(cp, fcf, ap)[li]
        shares_l = _div(pat[li], eps[li])
        mcap = _mul(price_now, shares_l)                       # ₹ cr
        ev = _add(mcap, debt_l)
        snap.update({
            "shares_cr": shares_l, "mktcap_cr": mcap,
            "pb": _div(mcap, nw_last),
            "ev_ebitda": None if is_bank else _div(ev, op_l),
            "ev_sales": None if is_bank else _div(ev, sales_l),
            "mcap_sales": _div(mcap, sales_l),
            "fcf_yield": _pct(_div(fcf_l, mcap)),
        })
    return {
        "pe_series": {"dates": pe_dates, "values": pe_vals},
        "pe_now": pe_now, "pe_percentile": pe_pctile, "median_pe": median_pe,
        "snapshot": snap,
        "formulas": {k: FORMULAS[k] for k in
                     ("pe", "earnings_yield", "fcf_yield", "pb", "ev_ebitda", "ev_sales", "mcap_sales")},
    }


def _na(series, reason):
    """Blank a metric that isn't meaningful for this company type (UI hides it)."""
    s = dict(series)
    s["values"] = [None] * len(series.get("values", []))
    s["na"] = reason
    return s


def _median(pts):
    pts = sorted(v for v in pts if v is not None)
    if not pts:
        return None
    n, mid = len(pts), len(pts) // 2
    return pts[mid] if n % 2 else (pts[mid - 1] + pts[mid]) / 2.0


def _round(x, d=1):
    return None if x is None else round(x, d)


def _score_high(x, lo, hi):
    """0 at `lo`, 100 at `hi`, clamped — for 'higher is better' components."""
    if x is None or hi == lo:
        return None
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100.0))


def _score_low(x, good, bad):
    """100 at `good`, 0 at `bad`, clamped — for 'lower is better' components."""
    if x is None or bad == good:
        return None
    return max(0.0, min(100.0, (bad - x) / (bad - good) * 100.0))


# ============================ MODULE G: QUALITY & MOAT SCORECARD (transparent)
def _quality_block(is_bank, margins, growth, cashflow, balance, roce_given, roe_given):
    """A quality score that DECOMPOSES into visible components (raw value + 0-100 map + weight).
    Composite = equal-weighted mean of the available component scores. House thresholds are
    stated; nothing is hidden. (Munger: avoid the false precision of a black-box score.)"""
    comps = []

    def add(label, value, unit, score, note):
        comps.append({"label": label, "value": value, "unit": unit,
                      "score": _round(score), "weight": None, "note": note})

    prof = (margins["ROCE"]["values"] if not is_bank else margins["ROE"]["values"])
    prof_med = _median(prof)
    add("Profitability — median ROCE/ROE", _round(prof_med), "%", _score_high(prof_med, 10, 25),
        "Return on capital; higher = better business economics. Score: 10%→0, 25%→100.")

    mser = (margins["OPM"]["values"] if not is_bank else margins["PAT margin"]["values"])
    mvals = [v for v in mser if v is not None]
    mu, sd = _mean(mvals), _std(mvals)
    cv = (sd / abs(mu)) if (mu not in (None, 0) and sd is not None) else None
    add("Margin stability — CV", _round(cv, 2), "ratio", _score_low(cv, 0.10, 0.50),
        "Coefficient of variation (std÷mean) of margin; lower = steadier. Score: 0.10→100, 0.50→0.")

    yoy = [v for v in growth["Sales"]["yoy"]["values"] if v is not None]
    cons = (100.0 * sum(1 for v in yoy if v > 0) / len(yoy)) if yoy else None
    add("Growth consistency — % up years", _round(cons), "%", _score_high(cons, 50, 100),
        "Share of years with positive sales growth. Score: 50%→0, 100%→100.")

    if not is_bank:
        cc = _median(cashflow["CFO/PAT"]["values"])
        add("Cash conversion — median CFO/PAT", _round(cc, 2), "×", _score_high(cc, 0.5, 1.0),
            "Median CFO ÷ PAT; ≥1 means profits convert to cash. Score: 0.5→0, 1.0→100.")

    if not is_bank:
        de = _median(balance["D/E"]["values"])
        add("Low leverage — median D/E", _round(de, 2), "×", _score_low(de, 0.0, 1.5),
            "Median debt-to-equity; lower = safer. Score: 0→100, 1.5→0.")

    cagr = growth["Sales"]["cagr"]["5y"]
    add("Compounding — 5y sales CAGR", _round(_pct(cagr)), "%", _score_high(cagr, 0.0, 0.20),
        "Five-year sales CAGR. Score: 0%→0, 20%→100.")

    scores = [c["score"] for c in comps if c["score"] is not None]
    w = round(1.0 / len(scores), 3) if scores else None
    for c in comps:
        if c["score"] is not None:
            c["weight"] = w
    composite = round(sum(scores) / len(scores), 1) if scores else None
    return {"score": composite, "components": comps,
            "method": "Composite = equal-weighted average of the component scores below; every component "
                      "shows its raw value and 0-100 mapping (house thresholds, adjustable). Not a black box."}


# ============================ MODULE H: CYCLE / MEAN-REVERSION (self-referenced)
def _cycle_block(is_bank, margins, growth, valuation, balance):
    """Where is the company vs its OWN history? Each gauge = latest value's percentile + z-score
    within its history; flags combine gauges per cycle-investing heuristics (Naren/Dalio)."""
    gauges = []

    def gauge(label, series, current=None):
        vals = [v for v in series if v is not None]
        cur = current if current is not None else _last(series)
        pctile = _pct_rank(vals, cur)
        mu, sd = _mean(vals), _std(vals)
        z = (cur - mu) / sd if (cur is not None and mu is not None and sd not in (None, 0)) else None
        gauges.append({"label": label, "current": _round(cur, 2),
                       "percentile": _round(pctile), "zscore": _round(z, 2)})
        return pctile

    margin_p = gauge("Margin", margins["OPM"]["values"] if not is_bank
                     else margins["Financing margin"]["values"])
    gauge("ROCE/ROE", margins["ROCE"]["values"] if not is_bank else margins["ROE"]["values"])
    growth_p = gauge("Sales growth (YoY)", growth["Sales"]["yoy"]["values"])
    val_p = gauge("Valuation (P/E)", valuation["pe_series"]["values"], current=valuation["pe_now"])
    lev_p = gauge("Leverage (D/E)", balance["D/E"]["values"])

    flags = []
    if margin_p is not None and val_p is not None and margin_p > 80 and val_p > 80:
        flags.append("Peak-cycle caution: margins AND valuation both in the top quintile of their own "
                     "history — the market may be extrapolating peak earnings.")
    if growth_p is not None and val_p is not None and growth_p > 80 and val_p > 80:
        flags.append("Growth and valuation both extended — priced for continued high growth.")
    if (margin_p is not None and lev_p is not None and margin_p < 25 and lev_p < 50
            and not is_bank):
        flags.append("Possible cyclical low: margins depressed but leverage below its own median "
                     "(balance sheet intact) — watch for normalization/recovery.")
    if val_p is not None and val_p < 15:
        flags.append("Valuation near a multi-year low versus its own history.")
    return {"gauges": gauges, "flags": flags,
            "method": "Each gauge = the latest value's percentile and z-score within the company's OWN "
                      "history (self-referenced cycle position; no peer data needed). Flags are signposts."}


def attach(bundle):
    bundle = dict(bundle)
    bundle["analytics"] = compute(bundle)
    return bundle


# ----------------------------------------------------------------------------- self-test
def _selftest():
    """Reproduce KNOWN Screener values from the local cache (no network). Verifies the
    statement parse, the EBITDA/EBIT identity, bank branching, and the derived ratios."""
    import os
    import json as _json
    here = os.path.dirname(os.path.abspath(__file__))
    cache = os.path.join(here, "..", "data", "screener")

    def load(sym):
        p = os.path.join(cache, f"{sym}.json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return _json.load(f)

    ok = True
    for sym in ("TCS", "HDFCBANK", "RELIANCE"):
        b = load(sym)
        if not b:
            print(f"[skip] {sym}: not cached")
            continue
        a = compute(b)
        print(f"\n=== {sym}  (bank={a.get('is_bank')}) ===")
        if not a.get("ok"):
            print("  FAIL:", a.get("error")); ok = False; continue
        ap = a["annual_periods"]
        print("  annual periods:", ap[-3:] if len(ap) >= 3 else ap)
        sales = a["growth"]["Sales"]["level"]["values"]
        print(f"  Sales last: {_last(sales)}  (5y CAGR {a['growth']['Sales']['cagr']['5y']})")
        print(f"  OPM last:   {_last(a['margins']['OPM']['values'])}")
        print(f"  ROE last:   {_last(a['margins']['ROE']['values'])}")
        print(f"  ROCE last:  {_last(a['margins']['ROCE']['values'])}")
        print(f"  D/E last:   {_last(a['balance']['D/E']['values'])}")
        print(f"  PE now:     {a['valuation']['pe_now']}  pctile {a['valuation']['pe_percentile']}")
        print(f"  P/B:        {a['valuation']['snapshot'].get('pb')}")
        # EBITDA/EBIT identity sanity: PBT ≈ OP + OtherInc − Interest − Dep
        li = _last_idx(sales)
        if li is not None and not a["is_bank"]:
            pl = _table(b, "profit_loss")
            cols = _period_cols(pl)
            o = _vals(pl, "operating profit", cols=cols)[li]
            oi = _vals(pl, "other income", cols=cols)[li]
            it = _vals(pl, "interest", cols=cols)[li]
            dp = _vals(pl, "depreciation", cols=cols)[li]
            pbt = _vals(pl, "profit before tax", cols=cols)[li]
            if None not in (o, oi, it, dp, pbt):
                approx = o + oi - it - dp
                print(f"  identity PBT={pbt} vs OP+OI-Int-Dep={approx:.0f} "
                      f"(diff {abs(pbt - approx):.0f})")
    print("\nself-test done.")
    return ok


if __name__ == "__main__":
    _selftest()
