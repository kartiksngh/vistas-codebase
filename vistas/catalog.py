"""
Index universe catalog for the picker.

Two tiers:
  * IN-SNAPSHOT  -> indices with local daily history (instant; from the CSV).
  * FETCHABLE    -> every index in IndexMapping.json not yet in the snapshot
                    (selectable; pulled on demand via /api/add_index, locally).

If IndexMapping is unavailable (offline/hosted with no cache), the catalog
gracefully falls back to the in-snapshot indices only.
"""
from __future__ import annotations

from . import data
from . import fetch

# Sensible terminal defaults for a passive NSE desk. No default tickers (user picks) — only a
# benchmark is pre-set so the first paint has a reference line.
DEFAULT_BENCHMARK = "NIFTY 500"
DEFAULT_TICKERS = []

# Lightweight grouping for the picker (substring rules; purely cosmetic).
# NOTE: NSE's IndexMapping mixes equity AND fixed-income/REIT indices; detect those
# FIRST so a debt index like "NIFTY 50 BLENDED 10 YR BENCHMARK G-SEC" isn't filed
# under "Broad market" just because it contains "NIFTY 50".
_DEBT = ("G-SEC", "GSEC", "SDL", "GILT", " BOND", "BHARAT BOND", "AAA", " AA ", "PSU BOND",
         "CD INDEX", "CP INDEX", "MONEY MARKET", "LIQUID", "OVERNIGHT", "TREASURY", "T-BILL",
         "TBILL", "DEBT", "1D RATE", "10 YEAR", "10 YR", "5 YR", "5YR", "15 YR", "BENCHMARK G-SEC",
         "TARGET MATURITY", "BANEX")
_REIT = ("REIT", "INVIT")
_FACTOR = ("MOMENTUM", "QUALITY", "ALPHA", "LOW VOL", "LOW-VOL", "VALUE", "HIGH BETA",
           "DIVIDEND", "ENHANCED", "QUALITY LOW", "FACTOR", "EQUAL WEIGHT")
_BROAD = ("NIFTY 50", "NIFTY 100", "NIFTY 200", "NIFTY 500", "MIDCAP", "SMALLCAP",
          "LARGEMIDCAP", "MIDSMALL", "MULTICAP", "MICROCAP", "TOTAL MARKET")


def _group(name: str) -> str:
    u = name.upper()
    if any(k in u for k in _DEBT):
        return "Fixed income / debt"
    if any(k in u for k in _REIT):
        return "REIT / InvIT"
    if any(k in u for k in _FACTOR):
        return "Factor"
    if any(k in u for k in _BROAD):
        return "Broad market"
    return "Sector / thematic"


def list_indices() -> dict:
    """The full catalog for the front-end picker."""
    cov = data.coverage()
    have = set(cov)
    have_upper = {c.upper() for c in cov}          # NSE casing is inconsistent
    items = []
    for name in sorted(have):
        c = cov[name]
        items.append({"name": name, "group": _group(name), "has_history": True,
                      "start": c["start"], "end": c["end"], "n_obs": c["n_obs"]})
    # fetchable-but-not-yet-local (de-dup case-insensitively so 'Nifty Auto' from
    # IndexMapping doesn't double-list the local 'NIFTY AUTO')
    try:
        fetchable = fetch.catalog_names()
    except Exception:
        fetchable = []
    seen_upper = set(have_upper)
    for name in fetchable:
        u = name.upper()
        if u in seen_upper:
            continue
        seen_upper.add(u)
        items.append({"name": name, "group": _group(name), "has_history": False,
                      "start": None, "end": None, "n_obs": 0})
    # individual stocks (yfinance snapshot) — selectable alongside indices, grouped "Stocks".
    # Each carries a `label` = company name so the picker is searchable by part of the name
    # or an acronym, not only the bare NSE ticker (e.g. "hindustan" / "HUL" -> HINDUNILVR).
    try:
        from . import stocks as _stocks
        scov = _stocks.coverage()
        try:
            snames = _stocks.company_names()
        except Exception:
            snames = {}
        try:
            salias = _stocks.aliases()         # {current symbol: [former NSE tickers]}
        except Exception:
            salias = {}
        for name in sorted(scov):
            c = scov[name]
            it = {"name": name, "group": "Stocks", "has_history": True,
                  "label": snames.get(name.upper(), ""),
                  "start": c["start"], "end": c["end"], "n_obs": c["n_obs"]}
            if salias.get(name):
                it["aliases"] = salias[name]   # so the picker finds the OLD ticker too (TATAMOTORS->TMPV)
            items.append(it)
    except Exception:
        pass

    # world / cross-asset instruments (Yahoo snapshot) — global indices, commodities, FX,
    # bond yields, credit, volatility, crypto. Keyed by FRIENDLY name; `label`=Yahoo ticker
    # so the picker is searchable by either. Grouped by asset class.
    try:
        from . import world as _world
        wcov = _world.coverage()                 # friendly-name -> coverage
        wsym = _world.names()                     # friendly-name -> Yahoo ticker
        for name in sorted(wcov):
            c = wcov[name]
            items.append({"name": name, "group": _world.GROUP_BY_NAME.get(name, "World / cross-asset"),
                          "has_history": True, "label": wsym.get(name, ""),
                          "start": c["start"], "end": c["end"], "n_obs": c["n_obs"]})
    except Exception:
        pass

    # active mutual-fund schemes (AMFI/mfapi NAV) — selectable next to indices/stocks. NAV is a
    # TOTAL-RETURN level so it charts like-for-like through the existing engine. Keyed by scheme
    # NAME; label = scheme code; grouped by SEBI category ("Mutual Funds · Large Cap", …).
    try:
        from . import funds_nav as _funds
        fcov = _funds.coverage()                 # scheme name -> coverage
        fcode = _funds.names()                    # scheme name -> AMFI code
        fcat = _funds.categories()                # scheme name -> SEBI category

        def _mfgroup(c):
            c = (c or "").replace("Equity Scheme - ", "").replace(" Fund", "").strip()
            return "Mutual Funds · " + (c or "Equity")

        for name in sorted(fcov):
            c = fcov[name]
            items.append({"name": name, "group": _mfgroup(fcat.get(name, "")),
                          "has_history": True, "label": str(fcode.get(name, "")),
                          "start": c["start"], "end": c["end"], "n_obs": c["n_obs"]})
    except Exception:
        pass

    items.sort(key=lambda x: (not x["has_history"], x["group"], x["name"]))
    dr = data.date_range()
    return {
        "indices": items,
        "n_local": len(have),
        "n_total": len(items),
        "default_benchmark": DEFAULT_BENCHMARK if DEFAULT_BENCHMARK in have else (sorted(have)[0] if have else None),
        "default_tickers": [t for t in DEFAULT_TICKERS if t in have][:3],
        "data_asof": data.asof(),
        "source_file": data.source_filename(),
        "data_start": dr["start"],
        "data_end": dr["end"],
    }
