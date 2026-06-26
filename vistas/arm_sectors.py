"""vistas/arm_sectors.py — Analyst Consensus Flow (task #46).

Roll per-stock LSEG StarMine **ARM** (analyst-revision momentum, a 0-100 regional
percentile — high = analysts are revising estimates/recommendations UP) UP to the
sector/desk level as a TIME SERIES, so the terminal can read the *street's* changing
conviction by sector. Produces one self-contained dataset (embedded as
``window.VISTAS_CONSENSUS``) with, per sector:

  • **EW ARM history**     — equal-weight mean of the sector's stock ARMs, monthly.
  • **FF ARM snapshot**    — float/market-cap-weighted ARM for the CURRENT cross-section
                             only (we hold no mcap history), flagged "as of latest".
  • **4 ARM components**   — Revenue / EPS(pref-earnings) / EBITDA(sec-earnings) /
                             Recommendation, each rolled up EW (which driver is moving).
  • **Net fund-flow history** — trailing-3M sum of per-stock net-active mutual-fund flow
                             (₹ cr), summed across the sector (the *smart-money* lens).

────────────────────────────────────────────────────────────────────────────────────
CONVENTIONS (so every number is reproducible without this code) — KV reporting rule:

  Universe   the MF-held NSE cohort that carries a sector tag (the rows of the
             smart_vs_street screen), intersected with the ARM-mapped universe
             (idmap ISIN→symbol). ~900-1000 stocks.
  Sector     the 11 ANALYST_DESKS macro-sectors (amc_context) — the SAME desks the
             Digital AMC analysts cover — plus a "Market (all)" aggregate. Sector
             membership is the CURRENT snapshot applied through history (sectors
             rarely change; this is a mild, disclosed survivorship/look-through caveat).
  EW(g,t)    simple mean of ARM_100_REG over the stocks in sector g whose latest ARM
             change-point is on/before month-end t AND fresh (≤ STALE_D days old).
             50 = neutral; >50 = sector net-upgraded by the street; <50 = net-downgraded.
  comp(g,t)  identical EW rollup on each component mnemonic, each with its OWN freshness
             guard (components go stale independently — a headline can be current while
             a component is years old, so they are filtered separately).
  FF(g)      Σ wᵢ·ARMᵢ / Σ wᵢ over the CURRENT stocks-with-ARM in g, wᵢ = mcap_cr
             (latest AMFI market-cap snapshot). Single value per sector — NOT history.
  flow(g,t)  trailing-3-month sum of per-stock net-active fund flow (₹ cr), summed over
             g's stocks. Source = funds_flows.build_stock_series (≤36 months).
  min-N      a sector-month EW cell needs ≥ MIN_N fresh stocks, else it is null (so a
             one-name month can't masquerade as a sector reading).

ARM is LSEG proprietary IP — this module only emits SECTOR AGGREGATES (means), never
the per-stock licensed values, so the published dataset carries no raw vendor data.
"""

import os
import json
import datetime as _dt

from . import arm as _arm
from . import idmap as _idm
from .amc_context import ANALYST_DESKS

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

HEADLINE = "ARM_100_REG"
COMPONENTS = [          # (short key, cache mnemonic, friendly label)
    ("REVENUE",   "ARM_REVENUE_COMP_100",   "Revenue"),
    ("PREF_EARN", "ARM_PREF_EARN_COMP_100", "EPS / earnings"),
    ("SEC_EARN",  "ARM_SEC_EARN_COMP_100",  "EBITDA"),
    ("REC",       "ARM_REC_COMP_100",       "Recommendation"),
]
STALE_D = 400          # an ARM value counts at month t only if its change-point is ≤ this many days before t
MIN_N = 4              # a sector-month needs ≥ this many fresh stocks, else null
HIST_START = "2008-01"  # ARM coverage of the investable cohort is thin before this
MARKET_KEY = "_MARKET"


# ─────────────────────────────────────────────────────────────── small date helpers
def _parse(s):
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _last_day(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    nxt = _dt.date(y + 1, 1, 1) if m == 12 else _dt.date(y, m + 1, 1)
    return nxt - _dt.timedelta(days=1)


def _month_list(start_ym, end_ym):
    sy, sm = int(start_ym[:4]), int(start_ym[5:7])
    ey, em = int(end_ym[:4]), int(end_ym[5:7])
    out, y, m = [], sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _f(x):
    try:
        v = float(x)
        return v if v == v else 0.0   # NaN -> 0
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────── inputs
def _load_rows(rows):
    """Screen rows (symbol, sector, mcap_cr). Use the passed list, else read the written file."""
    if rows:
        return rows
    p = os.path.join(_ROOT, "output", "terminal_site", "data", "_screens", "smart_vs_street.json")
    if os.path.exists(p):
        try:
            d = json.loads(open(p, encoding="utf-8").read())
            return d.get("rows") or d.get("stocks") or []
        except Exception:
            return []
    return []


def _sector_index(rows):
    """{symbol: desk_key}, {symbol: mcap_cr} for stocks that map to one of the 11 desks."""
    sec_to_desk = {}
    for dk, d in ANALYST_DESKS.items():
        for s in d["sectors"]:
            sec_to_desk[s] = dk
    sym_desk, sym_mcap = {}, {}
    for r in rows:
        sym = r.get("symbol")
        dk = sec_to_desk.get(r.get("sector"))
        if not sym or not dk:
            continue
        sym = str(sym).upper()
        sym_desk[sym] = dk
        sym_mcap[sym] = _f(r.get("mcap_cr"))
    return sym_desk, sym_mcap


def _ffill_monthly(steps, months_dt):
    """Forward-fill a sorted step-series [(date_str,val)] onto month-end dates.
    Returns [(value|None, asof_date|None)] aligned to months_dt (two-pointer walk)."""
    sd = [(_parse(s), v) for s, v in steps if _parse(s) is not None]
    out, j, n = [], 0, len(sd)
    last_v, last_d = None, None
    for me in months_dt:
        while j < n and sd[j][0] <= me:
            last_d, last_v = sd[j][0], sd[j][1]
            j += 1
        out.append((last_v, last_d) if last_v is not None else (None, None))
    return out


# ─────────────────────────────────────────────────────────────── the build
def build_consensus_dataset(rows=None, flows_by_sym=None, log=print):
    """Return the Analyst-Consensus-Flow dataset, or {} on any missing input (graceful)."""
    rows = _load_rows(rows)
    if not rows:
        log("[arm_sectors] no screen rows — consensus skipped")
        return {}
    sym_desk, sym_mcap = _sector_index(rows)
    if not sym_desk:
        log("[arm_sectors] no sector-mapped stocks — consensus skipped")
        return {}

    raw = _arm.load_raw()
    if not raw:
        log("[arm_sectors] no ARM cache — consensus skipped")
        return {}

    # ISIN -> symbol in one pass (cheap, cached index), keep only our universe.
    try:
        mapping, _stats = _idm.resolve_many(list(raw.keys()))
    except Exception:
        mapping = {isin: _idm.resolve(isin) for isin in raw.keys()}
    want = set(sym_desk)

    # per-symbol mnemonic step-series; on ISIN lineage (two ISINs → one symbol) keep the
    # longer headline history.
    sym_mnem = {}
    for isin, rec in raw.items():
        sym = mapping.get(isin)
        if not sym:
            continue
        sym = str(sym).upper()
        if sym not in want:
            continue
        mnem = rec.get("mnem") or {}
        if sym in sym_mnem:
            if len(mnem.get(HEADLINE) or []) <= len(sym_mnem[sym].get(HEADLINE) or []):
                continue
        sym_mnem[sym] = mnem
    if not sym_mnem:
        log("[arm_sectors] no ARM-mapped stocks in universe — consensus skipped")
        return {}

    # date grid: monthly month-ends from HIST_START to the latest ARM change-point.
    latest = None
    for mnem in sym_mnem.values():
        s = mnem.get(HEADLINE) or []
        if s:
            d = _parse(s[-1][0])
            if d and (latest is None or d > latest):
                latest = d
    if latest is None:
        return {}
    end_ym = f"{latest.year:04d}-{latest.month:02d}"
    months = _month_list(HIST_START, end_ym)
    months_dt = [_last_day(ym) for ym in months]
    nM = len(months)

    # desks present (in canonical order) + the Market aggregate.
    desks = [(MARKET_KEY, "Market (all sectors)")]
    for dk in ANALYST_DESKS:
        if any(sym_desk.get(s) == dk for s in sym_mnem):
            desks.append((dk, ANALYST_DESKS[dk]["name"]))
    desk_keys = [d[0] for d in desks]

    def _membership(dk, sym):
        return dk == MARKET_KEY or sym_desk.get(sym) == dk

    # ── EW rollup for a given mnemonic → {desk: [val|None per month]} ──────────────
    def _rollup(mnem_code):
        ssum = {dk: [0.0] * nM for dk in desk_keys}
        scnt = {dk: [0] * nM for dk in desk_keys}
        for sym, mnem in sym_mnem.items():
            ff = _ffill_monthly(mnem.get(mnem_code) or [], months_dt)
            mydesks = [dk for dk in desk_keys if _membership(dk, sym)]
            for i in range(nM):
                v, asof = ff[i]
                if v is None or asof is None:
                    continue
                if (months_dt[i] - asof).days > STALE_D:
                    continue
                for dk in mydesks:
                    ssum[dk][i] += v
                    scnt[dk][i] += 1
        out = {}
        for dk in desk_keys:
            out[dk] = [round(ssum[dk][i] / scnt[dk][i], 1) if scnt[dk][i] >= MIN_N else None
                       for i in range(nM)]
        return out

    ew = _rollup(HEADLINE)
    comp = {ck: _rollup(code) for ck, code, _lbl in COMPONENTS}   # {compkey: {desk: [..]}}
    # reshape comp -> {desk: {compkey: [..]}}
    comp_by_desk = {dk: {ck: comp[ck][dk] for ck, _c, _l in COMPONENTS} for dk in desk_keys}

    # ── FF (mcap-weighted) snapshot for the CURRENT month + coverage stats ─────────
    snap = {}
    for dk in desk_keys:
        syms = [s for s in sym_mnem if _membership(dk, s)]
        vals, wts, fresh_n, stale_n = [], [], 0, 0
        for s in syms:
            steps = sym_mnem[s].get(HEADLINE) or []
            if not steps:
                continue
            d = _parse(steps[-1][0])
            v = steps[-1][1]
            if d is None:
                continue
            if (latest - d).days > STALE_D:
                stale_n += 1
                continue
            fresh_n += 1
            vals.append(v)
            wts.append(max(sym_mcap.get(s, 0.0), 0.0))
        n_sec = len(syms)
        ew_now = round(sum(vals) / len(vals), 1) if vals else None
        wsum = sum(wts)
        ff_now = round(sum(v * w for v, w in zip(vals, wts)) / wsum, 1) if wsum > 0 else ew_now
        snap[dk] = {
            "ew": ew_now, "ff": ff_now, "n_sector": n_sec,
            "coverage_n": fresh_n,
            "coverage_pct": round(100.0 * fresh_n / n_sec, 1) if n_sec else None,
            "stale_n": stale_n,
        }

    # ── net-active fund-flow history (trailing-3M ₹cr), summed by sector ───────────
    flow, flow_months = {}, []
    if flows_by_sym is None:
        try:
            from . import funds_flows as _ff
            flows_by_sym = _ff.build_stock_series(months_back=36)
        except Exception as e:
            log(f"[arm_sectors] flow series unavailable ({e}) — flow omitted")
            flows_by_sym = {}
    if flows_by_sym:
        fm = set()
        for sym in sym_mnem:
            d = flows_by_sym.get(sym)
            if d:
                fm.update(d.get("months") or [])
        flow_months = sorted(fm)
        fidx = {ym: i for i, ym in enumerate(flow_months)}
        nF = len(flow_months)
        net = {dk: [0.0] * nF for dk in desk_keys}
        for sym in sym_mnem:
            d = flows_by_sym.get(sym)
            if not d:
                continue
            mydesks = [dk for dk in desk_keys if _membership(dk, sym)]
            for ym, fv in zip(d.get("months") or [], d.get("flow") or []):
                i = fidx.get(ym)
                if i is None:
                    continue
                for dk in mydesks:
                    net[dk][i] += _f(fv)
        # trailing-3-month rolling sum
        for dk in desk_keys:
            roll = []
            for i in range(nF):
                lo = max(0, i - 2)
                roll.append(round(sum(net[dk][lo:i + 1]), 1))
            flow[dk] = roll

    out = {
        "arm_asof": latest.isoformat(),
        "flow_asof": (flow_months[-1] if flow_months else None),
        "dates": months,
        "flow_dates": flow_months,
        "sectors": [{"key": k, "name": n} for k, n in desks],
        "comp_labels": [[ck, lbl] for ck, _code, lbl in COMPONENTS],
        "ew": ew,
        "comp": comp_by_desk,
        "flow": flow,
        "snap": snap,
        "stale_d": STALE_D, "min_n": MIN_N,
        "ff_note": ("FF (float/market-cap-weighted) ARM uses the LATEST market-cap snapshot held "
                    "constant — it is shown for the CURRENT cross-section only, not as history "
                    "(no market-cap history is stored)."),
        "method": ("Per-stock LSEG StarMine ARM (0-100 analyst-revision percentile) rolled up to the "
                   "11 analyst-desk sectors. EW(sector,month) = mean ARM over the sector's stocks whose "
                   f"latest revision is ≤{STALE_D}d old at that month-end (≥{MIN_N} fresh stocks required, "
                   "else blank). Components rolled up the same way, each freshness-guarded. FF = the "
                   "current mcap-weighted cross-section only. Flow = trailing-3M net-active mutual-fund "
                   "flow (₹cr) summed by sector. Sector membership = current snapshot applied through history."),
    }
    log(f"[arm_sectors] consensus: {len(desks)} sectors, {nM} months "
        f"({months[0]}..{months[-1]}), {len(sym_mnem)} stocks, flow {len(flow_months)}m")
    return out


if __name__ == "__main__":
    import sys
    ds = build_consensus_dataset()
    if not ds:
        print("no dataset"); sys.exit(1)
    print(f"sectors={len(ds['sectors'])} months={len(ds['dates'])} flow_months={len(ds['flow_dates'])}")
    for s in ds["sectors"]:
        k = s["key"]; sn = ds["snap"][k]
        ewv = next((v for v in reversed(ds["ew"][k]) if v is not None), None)
        print(f"  {s['name']:<28} EW={sn['ew']} FF={sn['ff']} cov={sn['coverage_pct']}% "
              f"(n={sn['coverage_n']}/{sn['n_sector']})  latest-hist={ewv}")
