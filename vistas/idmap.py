"""
idmap.py — the PUBLIC identifier bridge: ISIN  <->  NSE trading symbol.

This is public data (it reads only the committed data/stock_security_master.json), so it lives in the
public `vistas/` tree and is importable by both public modules (shares, starmine) and the gated layer.

WHY: external data (StarMine ARM, the Bloomberg mcap panel, NSE500 sheets) names a stock by ISIN; we name
it by NSE symbol. Joining on ISIN resolves ~100% of the StarMine universe (it is natively ISIN-keyed, and
the master carries the WHOLE lineage of ISINs per company, so renamed/old ISINs resolve to the live symbol).

The Bloomberg cap/price panels are keyed by Bloomberg's own ABBREVIATED house tickers — a DIFFERENT
identifier namespace from the NSE trading symbol (HUVR=HINDUNILVR, ICICIBC=ICICIBANK, ADSEZ=ADANIPORTS,
INFO=INFY, MM=M&M). The two strings coincide only ~39% of the time (verified 386/994 on the Apr-2026 cap
panel) — that is a namespace mismatch, NOT a deficiency of Bloomberg tickers, which are perfectly valid
within Bloomberg. So we never string-join on the ticker: we bridge Bloomberg-ticker -> ISIN via the NSE500
crosswalk sheets (which carry both), then ISIN -> symbol here. ISIN is the join key, always. (Better still,
re-export the BBG panel WITH its ID_ISIN field and join ISIN->ISIN directly, removing the crosswalk's
NSE500 coverage limit.)
"""
from __future__ import annotations

import os
import re
import json
import difflib
from functools import lru_cache

# vistas/ is the package; data/ sits at the project root (sibling of vistas/).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_MASTER_PATH = os.path.join(_ROOT, "data", "stock_security_master.json")

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")   # 2-letter country + 9 alnum + 1 check digit


# --------------------------------------------------------------------------- normalisation
def normalize_isin(s) -> str:
    """Canonical form of an ISIN: trimmed, upper-cased, inner spaces removed. Returns '' for junk."""
    if s is None:
        return ""
    return re.sub(r"\s+", "", str(s)).upper()


def isin_check_digit_ok(s) -> bool:
    """Verify the ISIN's trailing Luhn (mod-10) check digit per ISO 6166 — catches typo/transposition
    corruption that a shape check passes. Expects a 12-char shape-valid ISIN. Method: map letters A..Z
    -> 10..35 (each expands to TWO digits), concatenate all digits, then from the RIGHTMOST digit double
    every second one (rightmost included), sum the digit-sums, and require (10 - total % 10) % 10 ==
    the final digit. Verified against INE002A01018 (Reliance, check 8) and US0378331005 (Apple, check 5)."""
    t = normalize_isin(s)
    if not _ISIN_RE.match(t):
        return False
    digits = []
    for ch in t[:-1]:                       # body = first 11 chars (exclude the check digit)
        if ch.isdigit():
            digits.append(ord(ch) - 48)
        else:
            v = ord(ch) - 55                # 'A'(65) -> 10 ... 'Z'(90) -> 35
            digits.append(v // 10)
            digits.append(v % 10)
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:                      # rightmost (i=0) is doubled, then every second
            d *= 2
            if d > 9:
                d -= 9                      # digit-sum of a doubled digit (10..18) = d-9
        total += d
    return (10 - total % 10) % 10 == int(t[-1])


def is_valid_isin(s) -> bool:
    """True if `s` is a syntactically valid ISIN AND its ISO-6166 check digit verifies. The check digit
    closes the hole where a corrupted-but-shaped ISIN (a transposed/typo'd code) would silently mis-join."""
    return bool(_ISIN_RE.match(normalize_isin(s))) and isin_check_digit_ok(s)


def is_india_equity_isin(s) -> bool:
    """True for Indian *equity* ISINs (listed equity 'INE...'; DVR/special 'IN9...'). Excludes 'INF'
    (mutual-fund units) and ADR/foreign lines so a mixed external file can't false-match the stock master."""
    t = normalize_isin(s)
    return t.startswith("INE") or t.startswith("IN9")


# --------------------------------------------------------------------------- index build
@lru_cache(maxsize=1)
def _load_master() -> dict:
    """Load the public security master. Cached. Raises if missing."""
    if not os.path.exists(_MASTER_PATH):
        raise FileNotFoundError(
            f"security master not found at {_MASTER_PATH} — idmap needs the public "
            "data/stock_security_master.json to bridge ISIN <-> NSE symbol."
        )
    with open(_MASTER_PATH, encoding="utf-8") as f:
        sm = json.load(f)
    return sm.get("master", sm)   # file is {master:{...}, links:[...], flagged:[...]}


@lru_cache(maxsize=1)
def isin_index() -> dict:
    """Forward map ISIN -> {symbol, name, vst_id, symbols}. `symbol` = current NSE symbol; every lineage
    ISIN points at it so old/renamed ISINs resolve to the live symbol. Synthetic 'SYM:<symbol>' placeholders
    in the master are dropped (kept only real, valid ISINs)."""
    master = _load_master()
    idx: dict = {}
    collisions: list = []
    for vst_id, rec in master.items():
        sym = rec.get("latest_symbol") or (rec.get("symbols") or [None])[0]
        if not sym:
            continue
        name = rec.get("name") or sym
        syms = list(rec.get("symbols") or [sym])
        for raw in (rec.get("isins") or []):
            isin = normalize_isin(raw)
            if not is_valid_isin(isin):
                continue
            if isin in idx and idx[isin]["symbol"] != sym:
                collisions.append((isin, idx[isin]["symbol"], sym))
                continue
            idx[isin] = {"symbol": sym, "name": name, "vst_id": vst_id, "symbols": syms}
    if collisions:
        idx["__collisions__"] = collisions
    return idx


@lru_cache(maxsize=1)
def symbol_index() -> dict:
    """Reverse map NSE symbol (upper) -> {isin (primary live), isins[], name, vst_id}."""
    master = _load_master()
    out: dict = {}
    for vst_id, rec in master.items():
        sym = rec.get("latest_symbol") or (rec.get("symbols") or [None])[0]
        if not sym:
            continue
        isins = [normalize_isin(x) for x in (rec.get("isins") or []) if is_valid_isin(x)]
        out[str(sym).upper()] = {"isin": (isins[-1] if isins else None), "isins": isins,
                                 "name": rec.get("name") or sym, "vst_id": vst_id}
    return out


# --------------------------------------------------------------------------- resolution API
def resolve(isin, asof=None) -> str | None:
    """ISIN -> current NSE symbol, or None.

    `asof` (a 'YYYY-MM-DD' string) is accepted for forward compatibility with point-in-time
    resolution. The master does NOT yet carry dated identifier windows, so resolution currently
    returns the LATEST symbol regardless of `asof` — this is documented, not silently wrong. Full
    as-of resolution (needed for symbol REUSE and historical backtests) requires a dated identifier
    history; see crosswalk() and the identity-layer notes. Until then `asof` is a no-op."""
    rec = isin_index().get(normalize_isin(isin))
    return rec["symbol"] if rec else None


def resolve_record(isin) -> dict | None:
    """ISIN -> full record {symbol, name, vst_id, symbols}, or None."""
    return isin_index().get(normalize_isin(isin))


def symbol_to_isin(symbol) -> str | None:
    """NSE symbol -> primary (live) ISIN, or None."""
    rec = symbol_index().get(str(symbol).upper())
    return rec["isin"] if rec else None


def resolve_many(isins, india_only: bool = True) -> tuple[dict, dict]:
    """Resolve a list/iterable of ISINs at once -> (mapping {isin: symbol|None}, stats).
    india_only drops non-INE/IN9 (ADRs/fund units) first, reporting them separately."""
    idx = isin_index()
    mapping, unmatched, dropped, considered = {}, [], [], 0
    for raw in isins:
        isin = normalize_isin(raw)
        if not isin:
            continue
        if india_only and not is_india_equity_isin(isin):
            dropped.append(isin)
            mapping[isin] = None
            continue
        considered += 1
        rec = idx.get(isin)
        mapping[isin] = rec["symbol"] if rec else None
        if not rec:
            unmatched.append(isin)
    matched = considered - len(unmatched)
    stats = {"n": len(mapping), "considered": considered, "matched": matched,
             "coverage": round(matched / considered, 4) if considered else 0.0,
             "unmatched": unmatched, "dropped_non_india": dropped}
    return mapping, stats


def stage_fuzzy_candidates(name: str, top: int = 3, cutoff: float = 0.92) -> list:
    """For an unresolved ISIN that carries a company NAME, suggest closest master names for HUMAN confirm
    (token-set similarity, never auto-accepted). Returns [{symbol, name, score}] desc, score>=cutoff."""
    def toks(s):
        return " ".join(sorted(re.findall(r"[A-Z0-9]+", str(s).upper())))
    target = toks(name)
    if not target:
        return []
    out, seen = [], set()
    for rec in isin_index().values():
        if not isinstance(rec, dict) or rec["symbol"] in seen:
            continue
        seen.add(rec["symbol"])
        score = difflib.SequenceMatcher(None, target, toks(rec["name"])).ratio()
        if score >= cutoff:
            out.append({"symbol": rec["symbol"], "name": rec["name"], "score": round(score, 3)})
    out.sort(key=lambda d: d["score"], reverse=True)
    return out[:top]


# --------------------------------------------------------------------------- vst_id: our UNCHANGING id
# vst_id is Vistas's permanent surrogate key — it never changes across an ISIN re-issue or a symbol
# rename. ISIN / NSE-symbol / Bloomberg-ticker / LSEG-id are time-varying ATTRIBUTES that point AT it.
# Join on vst_id; display the current symbol. This is the single spine every source resolves through.
@lru_cache(maxsize=1)
def _vid_index() -> dict:
    """{ISIN -> vst_id} and {symbol_upper -> vst_id} over the whole lineage, so any historical ISIN or
    old symbol still resolves to the one permanent vst_id."""
    master = _load_master()
    by_isin, by_sym = {}, {}
    for vst_id, rec in master.items():
        sym = rec.get("latest_symbol") or (rec.get("symbols") or [None])[0]
        for s in (rec.get("symbols") or ([sym] if sym else [])):
            if s:
                by_sym.setdefault(str(s).strip().upper(), vst_id)
        for raw in (rec.get("isins") or []):
            isin = normalize_isin(raw)
            if is_valid_isin(isin):
                by_isin.setdefault(isin, vst_id)
    return {"by_isin": by_isin, "by_sym": by_sym}


def resolve_to_vid(identifier, kind: str = "auto") -> str | None:
    """Any external identifier -> our UNCHANGING vst_id, or None. `kind`: 'isin' | 'symbol' | 'auto'
    (auto tries ISIN-by-shape first, then NSE symbol). This is the join key to prefer everywhere."""
    idx = _vid_index()
    s_isin = normalize_isin(identifier)
    if kind in ("isin", "auto") and is_valid_isin(s_isin):
        v = idx["by_isin"].get(s_isin)
        if v or kind == "isin":
            return v
    return idx["by_sym"].get(str(identifier).strip().upper())


def symbol_to_vid(symbol) -> str | None:
    """NSE symbol (current or historical) -> our unchanging vst_id, or None."""
    return _vid_index()["by_sym"].get(str(symbol).strip().upper())


def vid_record(vst_id) -> dict | None:
    """vst_id -> {vst_id, nse_symbol (current), isin (latest), name, isins[], symbols[]}, or None."""
    rec = _load_master().get(vst_id)
    if not rec:
        return None
    isins = [normalize_isin(x) for x in (rec.get("isins") or []) if is_valid_isin(x)]
    return {"vst_id": vst_id,
            "nse_symbol": rec.get("latest_symbol") or (rec.get("symbols") or [None])[0],
            "isin": (isins[-1] if isins else None),           # latest = most recent in the lineage
            "name": rec.get("name"),
            "isins": isins, "symbols": list(rec.get("symbols") or [])}


@lru_cache(maxsize=1)
def crosswalk() -> dict:
    """The PUBLIC identity backbone: {vst_id -> {nse_symbol, isin, name, isins[], symbols[]}}.
    vst_id is our unchanging key; nse_symbol + isin are the CURRENT (latest) PUBLIC identifiers. The
    licensed vendor columns — latest Bloomberg ticker and latest LSEG id — are assembled ONTO this in
    the gated layer (vistas_gated.crosswalk), never here, so the public file carries no licensed IP."""
    out = {}
    for vst_id in _load_master():
        r = vid_record(vst_id)
        if r and r["nse_symbol"]:
            out[vst_id] = r
    return out
