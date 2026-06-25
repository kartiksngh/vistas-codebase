"""
starmine.py — LSEG StarMine Analyst Revision Model (ARM): per-stock card for the Fundamentals tab.

WHAT ARM IS (so the card is read correctly — no mistakes)
---------------------------------------------------------
ARM = Analyst Revision Model, a LSEG/Refinitiv StarMine signal. It is a REGION-RELATIVE 0–100 PERCENTILE
of analyst estimate-REVISION momentum: 100 = analysts are raising estimates the most in the region, 1 = cutting
the most. It is a SHORT-HORIZON (~1-month) TIMING signal (the revision-drift effect) that MEAN-REVERTS — NOT a
valuation verdict and NOT a buy-and-hold thesis. Read it ALONGSIDE valuation (high ARM + cheap = the constructive
combo), and watch DIRECTION (a score falling from a high is a warning even while still high).

THE SUM OF PARTS (what KV asked to plot)
----------------------------------------
The headline ARM (ITEM 44, `ARM_100_REG`) is a blend of component sub-scores — its DRIVERS:
  * `ARM_PREF_EARN_COMP_100`  preferred-earnings (usually EPS) revisions
  * `ARM_SEC_EARN_COMP_100`   secondary-earnings (usually EBITDA) revisions
  * `ARM_REVENUE_COMP_100`    revenue revisions
  * `ARM_REC_COMP_100`        analyst recommendation changes
Plotting the parts shows WHAT is driving the headline (earnings vs revenue vs recommendations). IMPORTANT &
HONEST: the headline is a coverage/profitability-weighted, NON-LINEAR blend — NOT a literal arithmetic sum of
the four parts — so we present the components as the decomposition/drivers, never claim "headline = sum(parts)".
Also carried: `ARM_100_GLOBAL` (global, not regional, rank) and `ARM_5_REG` (the coarse 1–5 bucket).

DATA
----
Long-form CSV (one row per ISIN × MNEMONIC × step-compressed date range): columns
ISIN, CMPNAME, STARTDATE, ENDDATE, MNEMONIC, ITEM, ITEMNAME, VALUE_, ESTCURR. ~458k rows, 472 ISINs, daily
point-in-time, 2024-07 → 2026-04. We key by ISIN and resolve to our NSE symbol via vistas.idmap (100%).

LICENSING: this is ABSL's licensed LSEG data, published on the terminal with ABSL's explicit sign-off (KV,
2026-06-22). The card is attributed "LSEG StarMine". Architected so a future gating is a single flag.
Provenance: runs at DECK-BUILD time on the local machine (reads the local licensed CSV) and bakes the cards
into the per-company fundamentals JSON; the hosted site never needs the raw CSV.
"""
from __future__ import annotations

import os
import csv
import glob

from . import idmap

# env override, else the newest "ARM scores extracted *.csv" under the ABSL tree
_ARM_ENV = "VISTAS_ARM_CSV"
_ARM_GLOB = os.environ.get(
    "VISTAS_ARM_GLOB",
    r"C:\Users\Administrator\Documents\ABSL Quant\**\ARM scores extracted *.csv",
)

HEADLINE = "ARM_100_REG"
GLOBAL = "ARM_100_GLOBAL"
BUCKET5 = "ARM_5_REG"
# component mnemonic -> (display label, short note) in the order we want them plotted
COMPONENTS = [
    ("ARM_PREF_EARN_COMP_100", "Preferred earnings (EPS)", "revisions to the main earnings line"),
    ("ARM_SEC_EARN_COMP_100", "Secondary earnings (EBITDA)", "revisions to the secondary earnings line"),
    ("ARM_REVENUE_COMP_100", "Revenue", "revisions to the top line"),
    ("ARM_REC_COMP_100", "Recommendations", "changes in analyst buy/hold/sell ratings"),
]
ATTRIBUTION = "LSEG StarMine — Analyst Revision Model (ARM)"


# --------------------------------------------------------------------------- discovery
def arm_csv_path() -> str | None:
    """The ARM extract to use: $VISTAS_ARM_CSV if set, else the newest 'ARM scores extracted *.csv' found."""
    p = os.environ.get(_ARM_ENV)
    if p and os.path.exists(p):
        return p
    cands = [f for f in glob.glob(_ARM_GLOB, recursive=True) if "~$" not in f]
    if not cands:
        return None
    # newest by the date embedded in the filename, falling back to mtime
    import re
    _MON = {m: i for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"], 1)}

    def datekey(f):
        m = re.search(r"extracted\s+([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", os.path.basename(f))
        if m:
            return (int(m.group(3)), _MON.get(m.group(1).lower(), 0), int(m.group(2)))
        return (0, 0, int(os.path.getmtime(f)))

    return max(cands, key=datekey)


# --------------------------------------------------------------------------- parse
def load_raw(path: str | None = None) -> dict:
    """Parse the ARM source -> {ISIN: {"name": str, "mnem": {MNEMONIC: [(date, value), ...] sorted}}}.

    PREFERENCE: the full-history compiled India cache (vistas/arm.py, from the big weekly parquet dump,
    1998 → current, ~1,900 mapped stocks). The small legacy CSV (472 ISINs) is the FALLBACK, used only
    when no ARM repo is present or an explicit `path` is passed. Each row = one (date, value) change-point."""
    if path is None:
        try:
            from . import arm as _arm
            if _arm.has_compiled() or _arm.source_parquets():
                raw = _arm.load_raw()
                if raw:
                    return raw
        except Exception as e:                       # never let ARM-repo issues break the build
            print(f"[starmine] compiled ARM cache unavailable ({e}); falling back to legacy CSV.")
    path = path or arm_csv_path()
    if not path or not os.path.exists(path):
        return {}
    out: dict = {}
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        rd = csv.DictReader(f)
        for row in rd:
            isin = (row.get("ISIN") or "").strip().upper()
            mnem = (row.get("MNEMONIC") or "").strip()
            if not isin or not mnem:
                continue
            v = row.get("VALUE_")
            try:
                val = float(v)
            except (TypeError, ValueError):
                continue
            date = (row.get("STARTDATE") or "").strip()
            if not date:
                continue
            rec = out.setdefault(isin, {"name": (row.get("CMPNAME") or "").strip(), "mnem": {}})
            if "secid" not in rec:                       # LSEG/Refinitiv permanent security id (for the crosswalk)
                sid = (str(row.get("SECID") or "").strip()
                       or str(row.get("SECCODE") or "").strip())
                if sid and sid.lower() != "nan":
                    rec["secid"] = sid
            rec["mnem"].setdefault(mnem, []).append((date, val))
    # sort each series by date and dedup exact repeats
    for rec in out.values():
        for mnem, series in rec["mnem"].items():
            series.sort(key=lambda t: t[0])
            rec["mnem"][mnem] = series
    return out


# --------------------------------------------------------------------------- card
def _latest(series):
    return series[-1] if series else (None, None)


def _value_on_or_before(series, cutoff_date):
    """The value of the most recent change-point at/before `cutoff_date` (the step value in force then)."""
    v = None
    for d, val in series:
        if d <= cutoff_date:
            v = val
        else:
            break
    return v


def _shift_date(date_str, days):
    """date_str (YYYY-MM-DD) minus `days` calendar days -> YYYY-MM-DD. Pure stdlib, no Date.now."""
    import datetime as dt
    try:
        d = dt.date.fromisoformat(date_str) - dt.timedelta(days=days)
        return d.isoformat()
    except Exception:
        return date_str


def _round_series(series, dp=1):
    return [[d, round(v, dp)] for d, v in series]


DISPLAY_TRIM_DAYS = 760   # bake ~25 months of plotted history per series; full history still drives trends


def _trim_series(series, asof, days=DISPLAY_TRIM_DAYS):
    """Keep only change-points within `days` before asof (always retain >= the latest point), so a
    27-year series doesn't bloat every per-stock JSON. Trends/levels are computed from the FULL series."""
    if not series or not asof:
        return series
    cutoff = _shift_date(asof, days)
    out = [pt for pt in series if pt[0] >= cutoff]
    return out or series[-1:]


def card_from_raw(rec: dict) -> dict | None:
    """Build the per-stock ARM card from one ISIN's parsed record. None if there is no headline score."""
    mnem = rec.get("mnem", {})
    head = mnem.get(HEADLINE) or []
    if not head:
        return None
    asof, score = _latest(head)
    prev30 = _value_on_or_before(head, _shift_date(asof, 30))
    prev90 = _value_on_or_before(head, _shift_date(asof, 90))
    glob = _latest(mnem.get(GLOBAL) or [])[1]
    bucket = _latest(mnem.get(BUCKET5) or [])[1]

    components = []
    for m, label, note in COMPONENTS:
        s = mnem.get(m) or []
        if not s:
            continue
        cd, cv = _latest(s)
        components.append({"key": m, "label": label, "note": note,
                           "score": round(cv, 1), "series": _round_series(_trim_series(s, asof))})

    card = {
        "source": ATTRIBUTION,
        "asof": asof,
        "headline": {
            "score": round(score, 1),
            "label": "Analyst Revisions Score (regional, 0–100)",
            "global": (round(glob, 1) if glob is not None else None),
            "bucket5": (int(bucket) if bucket is not None else None),
            "trend_30d": (round(score - prev30, 1) if prev30 is not None else None),
            "trend_90d": (round(score - prev90, 1) if prev90 is not None else None),
            "series": _round_series(_trim_series(head, asof)),
        },
        "components": components,
        "read": _interpretation(score, score - prev30 if prev30 is not None else 0.0),
        "usage": ("Short-horizon (~1-month) analyst-revision momentum, region-relative percentile. "
                  "It mean-reverts — read direction, not just level — and is best paired with valuation "
                  "(high ARM + cheap = constructive). Not a valuation or buy-and-hold signal."),
    }
    return card


def _interpretation(score: float, trend30: float) -> str:
    """A short, honest, computed read of the headline (no fabricated precision)."""
    if score >= 80:
        band = "very strong upward revision momentum"
    elif score >= 60:
        band = "above-average upward revision momentum"
    elif score >= 40:
        band = "neutral revision momentum"
    elif score >= 20:
        band = "below-average (downward) revision momentum"
    else:
        band = "very weak (analysts cutting estimates)"
    if trend30 >= 8:
        d = "and rising"
    elif trend30 <= -8:
        d = "but falling — momentum fading"
    else:
        d = "and roughly stable"
    return f"{score:.0f}/100 — {band}, {d} over the last month."


# --------------------------------------------------------------------------- batch
def cards_by_symbol(path: str | None = None, log=print) -> dict:
    """{NSE symbol -> ARM card} for every ISIN we can resolve. Logs coverage."""
    raw = load_raw(path)
    if not raw:
        log("[starmine] no ARM CSV found — set $VISTAS_ARM_CSV (skipping ARM cards).")
        return {}
    out, unresolved = {}, 0
    for isin, rec in raw.items():
        sym = idmap.resolve(isin)
        if not sym:
            unresolved += 1
            continue
        card = card_from_raw(rec)
        if not card:
            continue
        card["isin"] = isin
        prev = out.get(sym)                     # multiple lineage ISINs can map to one live symbol —
        if prev and (prev.get("asof") or "") >= (card.get("asof") or ""):
            continue                            # keep the most recently-updated card for that symbol
        out[sym] = card
    log(f"[starmine] ARM cards: {len(out)} symbols (of {len(raw)} ISINs; {unresolved} unresolved).")
    return out


def attach_to_fundamentals(fund_all: dict, path: str | None = None, log=print) -> int:
    """Attach `bundle['starmine'] = card` in place for every fundamentals bundle that has an ARM card.
    Returns the number attached. Used at deck-build time so the card ships inside the per-company JSON."""
    cards = cards_by_symbol(path, log=log)
    n = 0
    for sym, bundle in fund_all.items():
        c = cards.get(sym)
        if c:
            bundle["starmine"] = c
            n += 1
    return n
