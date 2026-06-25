"""
Offline deck builder.

Bundles the live terminal into ONE self-contained .html file: the page shell
(static/index.html) with the stylesheet, vendored Plotly, the verified JS
analytics port (static/vistas_analytics.js) and the full dataset + catalog all
INLINED. Opened in any browser it is fully interactive (re-pick indices / window /
frequency) with NO server and NO internet — every metric is recomputed
client-side by vistas_analytics.js, which is numerically identical to the
server-side analytics.py (see _parity_check.*).

Files land in ../output:
  * Vistas_Passive_Deck_<YYYY-MM-DD>_<HHMMSS>.html  (timestamped; one per save)
  * Vistas_Passive_Deck_latest.html                 (always the newest)

Auto-saved by app.py on every data update (refresh / fetch-all / add-index) and
on startup; also on demand via the 'Save offline deck' button (/api/save_deck).
"""
from __future__ import annotations

import os
import json
import math
import urllib.parse
from datetime import datetime

import pandas as pd

from . import data, catalog

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.abspath(os.path.join(HERE, "..", "static"))
OUTPUT_DIR = os.path.abspath(os.path.join(HERE, "..", "output"))

LATEST_NAME = "Vistas_Passive_Deck_latest.html"               # v1 (TR-only) — unchanged
TERMINAL_LATEST = "Vistas_Terminal_Deck_v2_latest.html"        # v2 (TR/PR + valuation)
EMBED_DECIMALS = 2     # NSE publishes TR/PR/PE/PB at 2dp and DY at 2dp -> rounding to
                       # 2dp is LOSSLESS vs source and shrinks the embedded JSON.

# --------------------------------------------------------------------------- licensing guardrail
# Bloomberg price/volume/market-cap is our AUDIT-ONLY ground truth and was NOT authorised for publication;
# it is paid third-party IP and must NEVER reach a published artifact. deck.py inlines the whole dataset as
# plaintext, so a single slip would leak it irreversibly. This is a BUILD-TIME hard stop — not a UI toggle —
# so an accidental publish becomes IMPOSSIBLE. The Bloomberg loaders live under vistas_gated/ (git-ignored).
# NOTE (2026-06-22): LSEG StarMine ARM is now PUBLISHED with ABSL's explicit sign-off, so its markers were
# REMOVED from this list — it is allowed in the deck. Bloomberg stays blocked. See VISTAS_DATA_INTEGRATION_PLAN.md §0.
_GATED_MARKERS = (
    "VISTAS_BBG", "VISTAS_GATED",                     # embed-key prefixes a gated layer would use
    "BBG_MCAP", "BLOOMBERG_PX", "BLOOMBERG_MCAP",     # Bloomberg ground-truth markers (audit-only)
)


def _assert_publishable(text: str, where: str) -> None:
    """Refuse to emit a published artifact that carries the Bloomberg audit data. Scans `text` for any
    gated marker and raises RuntimeError if found, aborting the build before anything is written. Keeps the
    (un-authorised) Bloomberg ground-truth out of a published deck."""
    hits = [m for m in _GATED_MARKERS if m in text]
    if hits:
        raise RuntimeError(
            f"BLOCKED PUBLISH: Bloomberg/gated data markers {hits} found in {where}. "
            "Bloomberg audit data must never be embedded in a published deck/site "
            "(see VISTAS_DATA_INTEGRATION_PLAN.md §0). Build aborted to prevent an irreversible leak."
        )


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _safe_js(s: str) -> str:
    """Neutralise any literal </script> so inlining can't close the block early."""
    return s.replace("</script", "<\\/script").replace("</SCRIPT", "<\\/SCRIPT")


def _json_for_script(obj) -> str:
    """JSON for embedding inside an inline <script>. Escapes < and > so a literal
    '</script>' / '<!--' / '<script' inside ANY string (e.g. an index name added via
    /api/add_index, which becomes a data key) can't break out of the block. The JS
    engine restores \\u003c/\\u003e to </> when it parses the object literal."""
    return (json.dumps(obj, allow_nan=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e"))


def _dataset() -> dict:
    """The full local TR dataset, exactly as analytics expects it: {dates, series}.
    NaN -> None so JSON is valid; floats keep full precision (parity with the app)."""
    df = data.load()
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    series = {c: [None if pd.isna(v) else float(v) for v in df[c].to_numpy()] for c in df.columns}
    return {"dates": dates, "series": series}


def _measure_dataset(measure: str) -> dict:
    """One measure's frame as {dates, series}, rounded to EMBED_DECIMALS (lossless vs
    NSE's 2dp source) to keep the embedded JSON small."""
    df = data.load(measure)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    series = {c: [None if pd.isna(v) else round(float(v), EMBED_DECIMALS) for v in df[c].to_numpy()]
              for c in df.columns}
    return {"dates": dates, "series": series}


def _measures_dataset() -> dict:
    """All measures that have a snapshot on disk: {measure: {dates, series}}.
    Always includes TR; PR/PE/PB/DY appear once their backfill has run."""
    out = {}
    for m in data.measures_present():
        try:
            out[m] = _measure_dataset(m)
        except Exception:
            pass
    return out


def _measure_subset(measure: str, names) -> dict:
    """One measure's frame restricted to `names` columns (all dates kept), rounded — the
    SLIM inline embed for the hosted shell: only the default selection ships inline so the
    first paint is instant; every other index is fetched on demand (data/indices/<M>/<n>.json)."""
    df = data.load(measure)
    keep = [c for c in names if c in df.columns]
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    series = {c: [None if pd.isna(v) else round(float(v), EMBED_DECIMALS) for v in df[c].to_numpy()]
              for c in keep}
    return {"dates": dates, "series": series}


def _stocks_dataset():
    """The yfinance stock-price snapshot as {dates, series} (rounded), or None if no
    stock snapshot exists yet. Embedded so the offline deck can chart stocks too."""
    try:
        from . import stocks as _stocks
        df = _stocks.load()
        if df is None or not len(df):
            return None
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        series = {c: [None if pd.isna(v) else round(float(v), EMBED_DECIMALS) for v in df[c].to_numpy()]
                  for c in df.columns}
        return {"dates": dates, "series": series}
    except Exception:
        return None


def _compact_price(price, target=260):
    """Downsample a Screener price block ({Price/DMA50/DMA200/Volume: [[date,val],...]})
    to ~`target` points so the Fundamentals price chart works offline without bloating the
    deck. Keeps the same nested [[date,val]] shape (the JS reads it directly), rounded."""
    if not isinstance(price, dict):
        return None
    pr = price.get("Price") or []
    if not pr:
        return None
    n = len(pr)
    step = max(1, n // target)
    idx = list(range(0, n, step))
    if idx and idx[-1] != n - 1:
        idx.append(n - 1)
    out = {}
    for key in ("Price", "DMA50", "DMA200", "Volume"):
        arr = price.get(key) or []
        ds = []
        for i in idx:
            if i < len(arr) and arr[i] and len(arr[i]) > 1:
                v = arr[i][1]
                ds.append([arr[i][0], None if v is None else round(float(v), 2)])
        if ds:
            out[key] = ds
    return out or None


def _world_dataset():
    """The world / cross-asset snapshot as {dates, series} (friendly-named columns,
    rounded), or None if no snapshot yet. Embedded inline (it's small + universal) so the
    offline deck can chart NSE indices vs S&P 500 / Gold / USD/INR / US 10Y / BTC."""
    try:
        from . import world as _world
        df = _world.load_named()
        if df is None or not len(df):
            return None
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        series = {c: [None if pd.isna(v) else round(float(v), EMBED_DECIMALS) for v in df[c].to_numpy()]
                  for c in df.columns}
        return {"dates": dates, "series": series}
    except Exception:
        return None


def _macro_dataset():
    """India-native macro snapshot as {dates, series, meta} (friendly-named, rounded), or
    None if no snapshot yet. Embedded inline (monthly/daily, small + universal) so the
    Macro tab reads India-first — CPI/WPI inflation, policy & market rates, money & credit,
    the external sector, real activity, and FII/DII flows — fully offline. `meta` carries
    each series' group/unit/freq/source for the Definition·Method·Why blocks."""
    try:
        from . import macro as _macro
        df = _macro.load()
        if df is None or not len(df):
            return None
        meta = dict(_macro.meta())

        # The canonical macro date index (DatetimeIndex, sorted). We accumulate every
        # series as a pandas Series indexed by Timestamp, then materialize onto the UNION
        # index at the end so dates/series stay aligned even after the merges below.
        idx = pd.DatetimeIndex(df.index)
        series_obj = {c: df[c] for c in df.columns}            # {name: Series(Timestamp)}

        # ------------------------------------------------------------------ TASK B:
        # merge Market-Internals (breadth / range-vol / liquidity) into the SAME frame.
        # bhav_derived.fetch_series() cold-reads a large parquet (~60-90s) — one-time
        # build cost. Guard it: on ANY failure skip internals silently (graceful-degrade).
        try:
            from . import bhav_derived as _bd
            bser, bmeta = _bd.fetch_series()
            for nm, s in (bser or {}).items():
                s = pd.Series(s)
                s.index = pd.DatetimeIndex(s.index)
                series_obj[nm] = s.sort_index()
                idx = idx.union(s.index)                       # outer-join the dates
            meta.update(bmeta or {})
        except Exception:
            pass

        # ------------------------------------------------------------------ TASK C:
        # two DERIVED signals computed from macro.py series already present (above).
        # If an input series is missing, skip that signal silently. (ERP and the
        # Buffett indicator are PENDING — they need NIFTY P/E + nominal GDP, not yet wired.)
        def _get(name):
            s = series_obj.get(name)
            return s if (s is not None and len(s)) else None

        # Real policy rate = RBI repo rate − CPI Combined YoY, aligned on dates. The repo
        # is a slow step series (changes only on policy dates) so forward-fill it onto each
        # CPI date before subtracting; keep only dates where BOTH are then defined.
        repo = _get("RBI repo rate")
        cpi = _get("CPI inflation — Combined (YoY)")
        if repo is not None and cpi is not None:
            union = repo.index.union(cpi.index)
            repo_ff = repo.reindex(union).ffill()
            cpi_al = cpi.reindex(union)
            real = (repo_ff - cpi_al).dropna()
            if len(real):
                nm = "Real policy rate (repo − CPI)"
                series_obj[nm] = real
                idx = idx.union(real.index)
                meta[nm] = {"group": "Policy & rates", "unit": "%", "freq": "monthly",
                            "source": "derived (repo − CPI YoY)"}

        # Credit-to-deposit ratio = 100 * SCB Bank Credit / SCB Aggregate Deposits, aligned
        # on dates (both present). Both are fortnightly SCB levels on the same axis.
        credit = _get("SCB — Bank Credit (Rs crore)")
        deposits = _get("SCB — Aggregate Deposits (Rs crore)")
        if credit is not None and deposits is not None:
            union = credit.index.union(deposits.index)
            import numpy as _np
            cd = (100.0 * credit.reindex(union) / deposits.reindex(union))
            cd = cd.replace([_np.inf, -_np.inf], _np.nan).dropna()
            if len(cd):
                nm = "Credit-to-deposit ratio (%)"
                series_obj[nm] = cd
                idx = idx.union(cd.index)
                meta[nm] = {"group": "Money & credit", "unit": "%", "freq": "fortnightly",
                            "source": "derived (SCB credit ÷ deposits)"}

        # ------------------------------------------------------------------ materialize
        idx = pd.DatetimeIndex(sorted(set(idx)))
        dates = [d.strftime("%Y-%m-%d") for d in idx]
        series = {}
        for nm, s in series_obj.items():
            arr = s.reindex(idx).to_numpy()
            series[nm] = [None if pd.isna(v) else round(float(v), 4) for v in arr]
        return {"dates": dates, "series": series, "meta": meta}
    except Exception:
        return None


def _fundamentals_dataset():
    """Per-company Screener fundamentals from data/screener/*.json as {SYM: bundle}, or
    None if none cached. Keeps valuation (PE/EPS/MedianPE), the statement tables, AND a
    downsampled `price` block (Price + DMA50/DMA200 + Volume) so the Fundamentals tab —
    including its price chart — works fully offline."""
    try:
        from . import screener as _sc
    except Exception:
        return None
    syms = _sc.available()
    if not syms:
        return None

    def _clean(o):       # read_html leaves NaN in empty cells -> None (valid JSON, allow_nan=False)
        if isinstance(o, float):
            return None if math.isnan(o) else o
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        return o

    try:
        from . import fundamentals as _fund
    except Exception:
        _fund = None

    # COLLECTED market cap (AMFI published / exact NSE issuedSize) keyed by symbol — overlaid
    # onto each valuation snapshot below. Never estimated from earnings ratios (KV directive).
    try:
        from . import shares as _shares
        _mcap_resolved = _shares.mcap_resolved()
    except Exception:
        _mcap_resolved = {}

    out = {}
    for sym in syms:
        b = _sc.load(sym)
        if not b or not b.get("ok"):
            continue
        obj = _clean({"symbol": b.get("symbol"), "name": b.get("name"),
                      "company_id": b.get("company_id"), "consolidated": b.get("consolidated"),
                      "valuation": b.get("valuation", {}), "statements": b.get("statements", {}),
                      "price": _compact_price(b.get("price")), "fetched": b.get("fetched")})
        # embed the computed analytics block (compute never raises). _clean it too so any
        # NaN slips out as None (allow_nan=False JSON). Skip silently on a malformed bundle.
        if _fund is not None:
            try:
                obj["analytics"] = _clean(_fund.compute(b))
            except Exception:
                pass
        # overlay the COLLECTED market cap + size cohort onto the valuation snapshot (the
        # derived price×PAT/EPS mktcap_cr stays alongside, clearly labelled an approximation)
        mc = _mcap_resolved.get(sym)
        if mc and isinstance(obj.get("analytics"), dict):
            val = obj["analytics"].get("valuation")
            snap = val.get("snapshot") if isinstance(val, dict) else None
            if isinstance(snap, dict):
                cr = mc.get("mcap_cr")
                snap["mcap_collected_cr"] = round(float(cr), 1) if cr is not None else None
                snap["mcap_cohort"] = mc.get("cohort")
                snap["mcap_source"] = mc.get("source")
        # stamp the STABLE identity onto the published bundle so exported data is joinable by ISIN /
        # our unchanging vst_id (not just the mutable display symbol)
        try:
            from . import idmap as _idm
            _vid = _idm.symbol_to_vid(sym)
            obj["vst_id"] = _vid
            obj["isin"] = (_idm.vid_record(_vid) or {}).get("isin") if _vid else None
        except Exception:
            pass
        out[sym] = obj

    # attach the LSEG StarMine ARM card per company (published with ABSL's explicit sign-off). Build-time
    # only: reads the local licensed CSV and bakes the card into the per-company JSON; the hosted site
    # never needs the raw CSV. Absent file / unresolved name -> simply no card for that stock.
    try:
        from . import starmine as _sm
        n_arm = _sm.attach_to_fundamentals(out, log=lambda m: print(m, flush=True))
        if n_arm:
            print(f"[deck] StarMine ARM cards attached to {n_arm} companies")
    except Exception as e:
        print(f"[deck] StarMine attach skipped: {e}")
    return out or None


def _offline_catalog(built_str: str) -> dict:
    """Catalog restricted to LOCAL indices only (everything embedded), so the
    offline picker never offers an index with no data."""
    cat = catalog.list_indices()
    local = [it for it in cat.get("indices", []) if it.get("has_history")]
    cat["indices"] = local
    cat["n_total"] = len(local)
    cat["n_local"] = len(local)
    cat["deck_built"] = built_str
    return cat


def build_deck_html(built_str: str, reason: str = "manual", terminal: bool = False, site_embed: dict = None) -> str:
    """Assemble the self-contained offline HTML string.

    terminal=True  -> Terminal Deck v2: embeds EVERY measure present (TR/PR/PE/PB/DY)
                      as window.VISTAS_MEASURES + a version-2 lineage stamp, so the
                      Performance (TR/PR) and Valuation tabs both light up.
    terminal=False -> legacy Passive v1 embed (TR only, full precision) — unchanged.
    site_embed     -> when given (hosted HYBRID build), embed only a small watchlist of
                      stocks+fundamentals inline + a `fund_manifest` of every company, and
                      set window.VISTAS_LAZY so the page fetches the rest (per-symbol files)
                      on demand. Keeps the published shell small. Ignored unless terminal."""
    html = _read(os.path.join(STATIC, "index.html"))
    css = _read(os.path.join(STATIC, "vistas.css"))
    plotly = _read(os.path.join(STATIC, "vendor", "plotly.min.js"))
    analytics_js = _read(os.path.join(STATIC, "vistas_analytics.js"))
    app_js = _read(os.path.join(STATIC, "vistas.js"))

    cat = _offline_catalog(built_str)

    if terminal:
        lazy = site_embed is not None
        macro_ds = _macro_dataset()                       # always inline (small, universal)
        index_names = {}
        world_names = []
        funds_names = []
        if lazy:
            # HOSTED shell: embed only the DEFAULT selection per measure inline (instant first
            # paint); every other index + all world series + all stocks/fundamentals are fetched
            # on demand from per-symbol files. This keeps the shell tiny so the page loads fast.
            present = data.measures_present()
            defaults = list(dict.fromkeys(
                (cat.get("default_tickers") or []) +
                ([cat.get("default_benchmark")] if cat.get("default_benchmark") else [])))
            measures = {m: _measure_subset(m, defaults) for m in present}
            index_names = {m: list(data.load(m).columns) for m in present}
            try:
                from . import world as _world
                wdf = _world.load_named()
                world_names = list(wdf.columns) if (wdf is not None and len(wdf)) else []
            except Exception:
                world_names = []
            try:
                from . import funds_nav as _funds
                fdf = _funds.load_named()
                funds_names = list(fdf.columns) if (fdf is not None and len(fdf)) else []
            except Exception:
                funds_names = []
            world_ds = stocks_ds = fund_ds = None          # not embedded — fetched on demand
        else:
            measures = _measures_dataset()                  # single-file deck: everything inline
            world_ds = _world_dataset()
            stocks_ds = _stocks_dataset()
            fund_ds = _fundamentals_dataset()
        cat["measures"] = list(measures.keys())
        adds = [m for m in ("PR", "PE", "PB", "DY") if m in measures]
        n_stk = len(site_embed.get("all_stocks", [])) if lazy else (len(stocks_ds["series"]) if stocks_ds else 0)
        n_world = len(world_names) if lazy else (len(world_ds["series"]) if world_ds else 0)
        n_fund = len(site_embed.get("fund_manifest", {})) if lazy else (len(fund_ds) if fund_ds else 0)
        if n_stk:
            adds.append(f"{n_stk} stocks")
        if n_world:
            adds.append(f"{n_world} world")
        if macro_ds:
            adds.append(f"{len(macro_ds['series'])} macro")
        if n_fund:
            adds.append(f"{n_fund} fundamentals")
        version = {"version": 2, "name": "Vistas Terminal Deck v2",
                   "built_on": "Passive v1 (TR-only)", "adds": adds, "built": built_str,
                   "hosting": ("hybrid lazy-load" if lazy else "single-file")}
        stocks_line = (f"window.VISTAS_STOCKS={_json_for_script(stocks_ds)};\n" if stocks_ds else "")
        world_line = (f"window.VISTAS_WORLD={_json_for_script(world_ds)};\n" if world_ds else "")
        macro_line = (f"window.VISTAS_MACRO={_json_for_script(macro_ds)};\n" if macro_ds else "")
        fund_line = (f"window.VISTAS_FUNDAMENTALS={_json_for_script(fund_ds)};\n" if fund_ds else "")
        manifest_line = (f"window.VISTAS_FUND_MANIFEST={_json_for_script(site_embed.get('fund_manifest', {}))};\n" if lazy else "")
        quant_manifest_line = (f"window.VISTAS_QUANT_MANIFEST={_json_for_script(site_embed.get('quant_manifest', {}))};\n" if lazy else "")
        funds_hold_manifest_line = (f"window.VISTAS_FUNDS_HOLDINGS_MANIFEST={_json_for_script(site_embed.get('funds_holdings_manifest', {}))};\n" if lazy else "")
        funds_attr_manifest_line = (f"window.VISTAS_FUNDS_ATTR_MANIFEST={_json_for_script(site_embed.get('funds_attribution_manifest', {}))};\n" if lazy else "")
        funds_holdonly_manifest_line = (f"window.VISTAS_FUNDS_HOLDONLY_MANIFEST={_json_for_script(site_embed.get('funds_holdonly_manifest', {}))};\n" if lazy else "")
        benchmark_manifest_line = (f"window.VISTAS_BENCHMARK_MANIFEST={_json_for_script(site_embed.get('benchmark_manifest', {}))};\n" if (lazy and site_embed.get('benchmark_manifest')) else "")
        # the screen is now ~1.5MB (all MF-held stocks) — inline only a tiny META marker; the JS lazy-fetches
        # the full rows from data/_screens/smart_vs_street.json (which screens.py already writes into the site).
        _ss = site_embed.get('screen_svs') or {}
        _ss_marker = ({k: v for k, v in _ss.items() if k != "rows"} if _ss else {})
        if _ss_marker:
            _ss_marker["lazy"] = True
        screen_svs_line = (f"window.VISTAS_SCREEN_SVS={_json_for_script(_ss_marker)};\n" if (lazy and _ss) else "")
        market_flows_line = (f"window.VISTAS_MARKET_FLOWS={_json_for_script(site_embed.get('market_flows', {}))};\n" if (lazy and site_embed.get('market_flows')) else "")
        survivorship_line = (f"window.VISTAS_SURVIVORSHIP={_json_for_script(site_embed.get('survivorship', {}))};\n" if (lazy and site_embed.get('survivorship')) else "")
        lazy_cfg = {"base": "data/", "stocks": True, "fundamentals": True, "quant": True,
                    "funds_holdings": True, "funds_attribution": True, "benchmarks": True,
                    "indices": index_names, "world": world_names, "funds": funds_names}
        lazy_line = (f"window.VISTAS_LAZY={_json_for_script(lazy_cfg)};\n" if lazy else "")
        # VISTAS_DATA is a REFERENCE to the TR measure (no JSON duplication).
        embed = (
            f"<script>{_safe_js(analytics_js)}</script>\n"
            f"<script>window.VISTAS_MEASURES={_json_for_script(measures)};\n"
            f"window.VISTAS_DATA=window.VISTAS_MEASURES.TR||null;\n"
            f"{stocks_line}"
            f"{world_line}"
            f"{macro_line}"
            f"{fund_line}"
            f"{manifest_line}"
            f"{quant_manifest_line}"
            f"{funds_hold_manifest_line}"
            f"{funds_attr_manifest_line}"
            f"{funds_holdonly_manifest_line}"
            f"{benchmark_manifest_line}"
            f"{screen_svs_line}"
            f"{market_flows_line}"
            f"{survivorship_line}"
            f"{lazy_line}"
            f"window.VISTAS_CATALOG={_json_for_script(cat)};\n"
            f"window.VISTAS_VERSION={_json_for_script(version)};</script>\n"
            f"<script>{_safe_js(app_js)}</script>"
        )
        title = f"Vistas · Terminal v2 ({'hosted' if lazy else 'offline'} deck · {built_str})"
    else:
        cat["measures"] = ["TR"]
        embed = (
            f"<script>{_safe_js(analytics_js)}</script>\n"
            f"<script>window.VISTAS_DATA={_json_for_script(_dataset())};\n"
            f"window.VISTAS_CATALOG={_json_for_script(cat)};</script>\n"
            f"<script>{_safe_js(app_js)}</script>"
        )
        title = f"Vistas · Passive (offline deck · {built_str})"

    html = html.replace('<link rel="stylesheet" href="/static/vistas.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="/static/vendor/plotly.min.js"></script>', f"<script>{_safe_js(plotly)}</script>")
    html = html.replace('<script src="/static/vistas.js"></script>', embed)
    html = html.replace("<title>Vistas · Passive NSE Index Terminal</title>", f"<title>{title}</title>")
    _assert_publishable(html, f"deck shell HTML ({title})")   # licensing hard stop — see _GATED_MARKERS
    return html


def save_deck(reason: str = "manual", timestamped: bool = True) -> dict:
    """Build + write the offline deck. Always refreshes ..._latest.html; when
    `timestamped` also writes a dated/timed copy (so multiple saves a day don't
    clobber each other). Returns a small summary dict for the API/CLI."""
    built = datetime.now()
    built_str = built.strftime("%Y-%m-%d %H:%M")
    html = build_deck_html(built_str, reason)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    latest = os.path.join(OUTPUT_DIR, LATEST_NAME)
    with open(latest, "w", encoding="utf-8") as f:
        f.write(html)

    out = {"ok": True, "reason": reason, "asof": data.asof(), "built": built_str,
           "latest": LATEST_NAME, "output_dir": OUTPUT_DIR}
    if timestamped:
        fn = f"Vistas_Passive_Deck_{built.strftime('%Y-%m-%d_%H%M%S')}.html"
        p = os.path.join(OUTPUT_DIR, fn)
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        out.update({"file": fn, "path": p, "size_mb": round(os.path.getsize(p) / 1e6, 1)})
    else:
        out.update({"file": LATEST_NAME, "path": latest,
                    "size_mb": round(os.path.getsize(latest) / 1e6, 1)})
    return out


def save_terminal_deck(reason: str = "manual", timestamped: bool = True) -> dict:
    """Build + write the Terminal Deck v2 (TR/PR + valuation). Writes
    Vistas_Terminal_Deck_v2_latest.html and (by default) a timestamped copy. The v1
    Passive deck + its live link are NOT touched."""
    built = datetime.now()
    built_str = built.strftime("%Y-%m-%d %H:%M")
    html = build_deck_html(built_str, reason, terminal=True)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    latest = os.path.join(OUTPUT_DIR, TERMINAL_LATEST)
    with open(latest, "w", encoding="utf-8") as f:
        f.write(html)

    out = {"ok": True, "deck": "terminal_v2", "reason": reason, "built": built_str,
           "measures": data.measures_present(), "latest": TERMINAL_LATEST, "output_dir": OUTPUT_DIR}
    if timestamped:
        fn = f"Vistas_Terminal_Deck_v2_{built.strftime('%Y-%m-%d_%H%M%S')}.html"
        p = os.path.join(OUTPUT_DIR, fn)
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        out.update({"file": fn, "path": p, "size_mb": round(os.path.getsize(p) / 1e6, 1)})
    else:
        out.update({"file": TERMINAL_LATEST, "path": latest,
                    "size_mb": round(os.path.getsize(latest) / 1e6, 1)})
    return out


# ----------------------------------------------------------------------------- hosted hybrid site
SITE_DIRNAME = "terminal_site"


def _safe_name(sym: str) -> str:
    """Filename/URL-safe encoding of a symbol (matches JS encodeURIComponent), so a fetch
    of data/stocks/<encoded>.json hits the right static file even for names like M&M."""
    return urllib.parse.quote(str(sym), safe="")


def _stocks_subset(syms) -> dict:
    """{dates, series} for just `syms` (the embedded watchlist), rounded. None if empty."""
    try:
        from . import stocks as _stocks
        df = _stocks.load()
        if df is None or not len(df):
            return None
        cols = [c for c in syms if c in df.columns]
        if not cols:
            return None
        df = df[cols]
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        series = {c: [None if pd.isna(v) else round(float(v), EMBED_DECIMALS) for v in df[c].to_numpy()] for c in cols}
        return {"dates": dates, "series": series}
    except Exception:
        return None


def save_terminal_site(reason: str = "manual", watchlist=None) -> dict:
    """Build the HOSTED, hybrid lazy-load terminal under output/terminal_site/:
        index.html                      — light shell (indices+world+catalog+manifest+
                                          watchlist embedded) + window.VISTAS_LAZY
        data/stocks/<SYM>.json          — per-stock adjusted-price series (fetched on select)
        data/fundamentals/<SYM>.json    — per-company bundle (fetched on open)
    The published page stays small; heavy data loads on demand. The single-file offline
    deck (save_terminal_deck) is untouched for email/curated use."""
    from . import stocks as _stocks
    built = datetime.now()
    built_str = built.strftime("%Y-%m-%d %H:%M")
    site = os.path.join(OUTPUT_DIR, SITE_DIRNAME)
    sdir = os.path.join(site, "data", "stocks")
    fdir = os.path.join(site, "data", "fundamentals")
    os.makedirs(sdir, exist_ok=True)
    os.makedirs(fdir, exist_ok=True)

    # 1) per-stock price files
    sdf = _stocks.load()
    all_stocks = list(sdf.columns) if (sdf is not None and len(sdf)) else []
    n_stk = 0
    # stable identity per stock (joinable by ISIN / unchanging vst_id) — stamped into every export file
    try:
        from . import idmap as _idm
        _id_by_sym = {r["nse_symbol"]: (r["isin"], vid) for vid, r in _idm.crosswalk().items()
                      if r.get("nse_symbol")}
    except Exception:
        _id_by_sym = {}
    if all_stocks:
        dts = [d.strftime("%Y-%m-%d") for d in sdf.index]
        for c in all_stocks:
            vals = [None if pd.isna(v) else round(float(v), EMBED_DECIMALS) for v in sdf[c].to_numpy()]
            rec = {"dates": dts, "series": {c: vals}}
            ident = _id_by_sym.get(c)
            if ident:
                rec["isin"], rec["vst_id"] = ident[0], ident[1]
            with open(os.path.join(sdir, _safe_name(c) + ".json"), "w", encoding="utf-8") as f:
                f.write(json.dumps(rec, allow_nan=False, separators=(",", ":")))
            n_stk += 1

    # 2) per-company fundamentals files + a manifest of every company (for instant search)
    fund_all = _fundamentals_dataset() or {}
    fund_manifest = {}
    for sym, bundle in fund_all.items():
        fund_manifest[sym] = (bundle.get("name") or sym)
        payload = json.dumps(bundle, allow_nan=False, separators=(",", ":"))
        _assert_publishable(payload, f"fundamentals/{sym}.json")   # licensing hard stop
        with open(os.path.join(fdir, _safe_name(sym) + ".json"), "w", encoding="utf-8") as f:
            f.write(payload)
    n_fund = len(fund_all)

    # 2c) per-symbol Quant & MI files — reuse the already-loaded bundles + analytics (no new fetch),
    # one shared context. Mirrors the fundamentals lazy layout: data/quant/<SYM>.json + a manifest.
    quant_manifest = {}
    n_quant = 0
    # cross-AMC net-active-flow / crowding series (MoneyBall Layer D#1), baked per symbol into
    # the quant bundle so the Quant&MI cockpit shows "smart-money flow" with no extra fetch.
    flows_by_sym, holders_by_sym = {}, {}
    try:
        from . import funds_flows as _ff
        flows_by_sym = _ff.build_stock_series(months_back=36)
        holders_by_sym = _ff.build_stock_holders()
        print(f"[deck] smart-money flow series: {len(flows_by_sym)} symbols; fund-holders: {len(holders_by_sym)} symbols", flush=True)
    except Exception as e:
        print(f"[deck] smart-money flow skipped: {e}")
    try:
        from . import stock_intel as _qi
        quant_manifest = _qi.build_all(fund_all, os.path.join(site, "data", "quant"),
                                       flows_by_sym=flows_by_sym, holders_by_sym=holders_by_sym)
        n_quant = len(quant_manifest)
    except Exception as e:
        print(f"[deck] quant build skipped: {e}")

    # 2b) per-INDEX files (one per measure) + per-WORLD files — so the shell embeds only the
    # default selection and fetches the rest on demand (this is what keeps the shell small).
    n_idx = 0
    for m in data.measures_present():
        df = data.load(m)
        mdir = os.path.join(site, "data", "indices", m)
        os.makedirs(mdir, exist_ok=True)
        dts = [d.strftime("%Y-%m-%d") for d in df.index]
        for c in df.columns:
            vals = [None if pd.isna(v) else round(float(v), EMBED_DECIMALS) for v in df[c].to_numpy()]
            with open(os.path.join(mdir, _safe_name(c) + ".json"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"dates": dts, "series": {c: vals}}, allow_nan=False, separators=(",", ":")))
            n_idx += 1
    n_world = 0
    try:
        from . import world as _world
        wdf = _world.load_named()
        if wdf is not None and len(wdf):
            wdir = os.path.join(site, "data", "world")
            os.makedirs(wdir, exist_ok=True)
            wdts = [d.strftime("%Y-%m-%d") for d in wdf.index]
            for c in wdf.columns:
                vals = [None if pd.isna(v) else round(float(v), EMBED_DECIMALS) for v in wdf[c].to_numpy()]
                with open(os.path.join(wdir, _safe_name(c) + ".json"), "w", encoding="utf-8") as f:
                    f.write(json.dumps({"dates": wdts, "series": {c: vals}}, allow_nan=False, separators=(",", ":")))
                n_world += 1
    except Exception:
        pass

    # 2d) per-fund NAV files (one per scheme name) — like world; lazy-fetched into the Prices view
    n_funds = 0
    try:
        from . import funds_nav as _funds
        fdf = _funds.load_named()
        if fdf is not None and len(fdf):
            nvdir = os.path.join(site, "data", "funds_nav")
            os.makedirs(nvdir, exist_ok=True)
            fdts = [d.strftime("%Y-%m-%d") for d in fdf.index]
            for c in fdf.columns:
                vals = [None if pd.isna(v) else round(float(v), EMBED_DECIMALS) for v in fdf[c].to_numpy()]
                with open(os.path.join(nvdir, _safe_name(c) + ".json"), "w", encoding="utf-8") as f:
                    f.write(json.dumps({"dates": fdts, "series": {c: vals}}, allow_nan=False, separators=(",", ":")))
                n_funds += 1
    except Exception as e:
        print(f"[deck] funds NAV files skipped: {e}")

    # 2e) per-scheme FUND HOLDINGS (look-through) — AMC monthly portfolio disclosures, parsed +
    # aggregated locally (display-plane, like quant). Writes data/funds_portfolio/<key>.json + a
    # manifest. Fetches at build time (plain CDN GETs, no WAF); graceful-degrade if unreachable.
    funds_hold_manifest = {}
    n_fund_hold = 0
    try:
        from . import funds_portfolio as _fph
        funds_hold_manifest = _fph.build_all(os.path.join(site, "data", "funds_portfolio"),
                                             log=lambda m: print(m, flush=True))
        n_fund_hold = len(funds_hold_manifest)
    except Exception as e:
        print(f"[deck] funds holdings skipped: {e}")

    # 2f) per-scheme FUND-SKILL attribution — holdings-based manager skill (excess vs category
    # benchmark, IR/t-stat, selection IC, sizing, concentration) over the 13-yr history store,
    # on a total-return basis. Writes data/funds_attribution/<navindia_code>.json + a manifest.
    funds_attr_manifest = {}
    n_fund_attr = 0
    # per-fund crowd-alignment / herding + latest trades (MoneyBall D#1, fund side) — attached to
    # each scheme's attribution JSON; and a small market-wide flow summary embedded inline.
    fund_flows_by_code, market_flows = {}, {}
    try:
        from . import funds_flows as _ff2
        fund_flows_by_code = _ff2.build_fund_series(months_back=18)
        market_flows = _ff2.build_market_summary()
        # peer-relative Active Share (guarded, 2026-06-25) merged into the same per-fund dict so it
        # rides the existing crowd_flow plumbing into the cockpit's fsCrowdHTML.
        try:
            _as = _ff2.build_active_share()
            n_as = 0
            for _code, _v in _as.items():
                if _code in fund_flows_by_code:
                    fund_flows_by_code[_code]["active_share"] = _v
                    n_as += 1
            print(f"[deck] active share merged into {n_as} schemes", flush=True)
        except Exception as _ase:
            print(f"[deck] active share skipped: {_ase}")
        # per-fund EQUITY book (vst_id+pct+sector) for the cockpit's benchmark-relative active share —
        # rides into each attribution JSON via crowd_flow. Sector from the benchmark constituents.
        try:
            import glob as _glob
            _bdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "benchmarks")
            _secmap = {}
            for _bf in _glob.glob(os.path.join(_bdir, "ind_*.json")):
                try:
                    for _c in json.load(open(_bf, encoding="utf-8")).get("constituents", []):
                        if _c.get("vst_id") and _c.get("sector"):
                            _secmap.setdefault(_c["vst_id"], _c["sector"])
                except Exception:
                    pass
            _books = _ff2.build_equity_books(sector_map=_secmap)
            n_bk = 0
            for _code, _bk in _books.items():
                if _code in fund_flows_by_code:
                    fund_flows_by_code[_code]["equity_holdings"] = _bk
                    n_bk += 1
            print(f"[deck] equity books merged into {n_bk} schemes (sector map {len(_secmap)})", flush=True)
        except Exception as _bke:
            print(f"[deck] equity books skipped: {_bke}")
        print(f"[deck] fund crowd-alignment: {len(fund_flows_by_code)} schemes; market-flow summary {market_flows.get('ym')}", flush=True)
    except Exception as e:
        print(f"[deck] fund crowd-alignment skipped: {e}")
    # survivorship coverage of the holdings panel (Data-Quality, CIO/quant honesty) — precomputed
    # from the AMFI survivorship-free census + NAV-panel premium; embedded inline if present.
    survivorship = {}
    try:
        import json as _json
        _dd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "funds")
        _rep = _json.load(open(os.path.join(_dd, "_survivorship_report.json"), encoding="utf-8"))
        survivorship = {"funds_ever": _rep.get("census_equity_funds_ever"),
                        "funds_live": _rep.get("census_alive"), "funds_dead": _rep.get("census_dead"),
                        "missing_dead": _rep.get("missing_dead_count"),
                        "missing_dead_ge24mo": _rep.get("missing_dead_lived_ge24mo"),
                        "as_of": _rep.get("as_of")}
        try:
            _prem = _json.load(open(os.path.join(_dd, "_survivorship_premium.json"), encoding="utf-8"))
            survivorship.update({"premium_annual_pct": _prem.get("premium_annual_pct"),
                                 "all_cagr": _prem.get("all_cagr"), "surv_cagr": _prem.get("surv_cagr"),
                                 "premium_years": _prem.get("years"), "premium_n_codes": _prem.get("n_codes")})
        except Exception:
            pass
        print(f"[deck] survivorship coverage: {survivorship.get('funds_dead')} dead / {survivorship.get('funds_ever')} ever; premium {survivorship.get('premium_annual_pct')}%/yr", flush=True)
    except Exception as e:
        print(f"[deck] survivorship coverage skipped: {e}")
    try:
        from . import funds_attribution as _fattr
        _far = _fattr.build_all(os.path.join(site, "data", "funds_attribution"), flows_by_fund=fund_flows_by_code)
        funds_attr_manifest = _far.get("manifest", {})
        n_fund_attr = _far.get("n_schemes", 0)
        print(f"[deck] fund-skill attribution: {n_fund_attr} schemes", flush=True)
    except Exception as e:
        print(f"[deck] funds attribution skipped: {e}")

    # 2f-bench) NSE index BENCHMARK PORTFOLIOS (EW + free-float-mcap reconstructed weights) — shipped
    # as lazy per-index files so the Funds cockpit can compare a fund to a chosen benchmark on demand.
    benchmark_manifest = {}
    try:
        import shutil as _shutil
        _bsrc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "benchmarks")
        _bdst = os.path.join(site, "data", "benchmarks")
        if os.path.isdir(_bsrc):
            os.makedirs(_bdst, exist_ok=True)
            for _bf in os.listdir(_bsrc):
                if _bf.endswith(".json"):
                    _shutil.copy2(os.path.join(_bsrc, _bf), os.path.join(_bdst, _bf))
            _bm = json.load(open(os.path.join(_bsrc, "_manifest.json"), encoding="utf-8"))
            benchmark_manifest = _bm.get("indices", {})
            print(f"[deck] benchmark portfolios: {len(benchmark_manifest)} indices (asof {_bm.get('asof')}, mcap {_bm.get('mcap_period')})", flush=True)
    except Exception as e:
        print(f"[deck] benchmark portfolios skipped: {e}")

    # 2f-screen) cross-sectional STOCK SCREEN: Smart-money vs the Street (Analyst ARM × corp-action-clean
    # FM flow, on the correction+deteriorating NSE-500 watchlist) — reads the just-built quant+fundamentals.
    screen_svs = {}
    try:
        from . import screens as _scr
        _root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        screen_svs = _scr.build_smart_vs_street(os.path.join(site, "data"), _root, progress=lambda m: print(m, flush=True))
    except Exception as e:
        print(f"[deck] smart-vs-street screen skipped: {e}")

    # 2g) BRIDGE live-AMC holdings -> store by HOLDINGS FINGERPRINT so the cockpit lists ALL funds
    # without duplicates: matched live funds collapse to their store scheme; unmatched (passive
    # index/ETF + debt/liquid) become HOLDINGS-ONLY cockpit entries (KV: full coverage, survivorship-safe).
    funds_holdonly_manifest = {}
    try:
        from . import funds_bridge as _fbr
        _fb = _fbr.build(os.path.join(site, "data", "funds_portfolio"), funds_attr_manifest)
        funds_holdonly_manifest = _fb.get("holdonly", {})
    except Exception as e:
        print(f"[deck] funds bridge skipped: {e}")

    # 3) the light shell — embeds ONLY the default index selection + macro inline; every
    # stock / world series / non-default index / company is fetched on demand. fund_manifest
    # (names only) stays inline so company search is instant.
    site_embed = {
        "stocks": None,
        "fundamentals": None,
        "fund_manifest": fund_manifest,
        "quant_manifest": quant_manifest,
        "funds_holdings_manifest": funds_hold_manifest,
        "funds_attribution_manifest": funds_attr_manifest,
        "funds_holdonly_manifest": funds_holdonly_manifest,
        "market_flows": market_flows,
        "survivorship": survivorship,
        "benchmark_manifest": benchmark_manifest,
        "screen_svs": screen_svs,
        "all_stocks": all_stocks,
    }
    html = build_deck_html(built_str, reason, terminal=True, site_embed=site_embed)
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    shell_mb = round(os.path.getsize(os.path.join(site, "index.html")) / 1e6, 2)
    return {"ok": True, "site": site, "index": os.path.join(site, "index.html"),
            "shell_mb": shell_mb, "n_stock_files": n_stk, "n_fundamental_files": n_fund,
            "n_quant_files": n_quant, "n_index_files": n_idx, "n_world_files": n_world,
            "n_funds_files": n_funds, "n_funds_holdings_files": n_fund_hold,
            "n_funds_attr_files": n_fund_attr, "built": built_str}


def rebuild_all(reason: str = "auto") -> dict:
    """Rebuild BOTH the single-file offline deck and the hosted hybrid site from current
    data — used by the auto-rebuild watcher when a pull finishes."""
    out = {}
    try:
        out["deck"] = save_terminal_deck(reason=reason)
    except Exception as e:
        out["deck"] = {"ok": False, "error": str(e)}
    try:
        out["site"] = save_terminal_site(reason=reason)
    except Exception as e:
        out["site"] = {"ok": False, "error": str(e)}
    return out


if __name__ == "__main__":
    print(save_deck(reason="cli"))
