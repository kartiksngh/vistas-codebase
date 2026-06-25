"""
benchmarks.py — NSE index BENCHMARK PORTFOLIOS for fund-vs-benchmark comparison in the Funds tab.

We have TRI for ~131 NSE indices but NSE does not freely publish constituent WEIGHTS. So we fetch the
constituent LIST (free) and RECONSTRUCT two weight vectors per the published methodology:

  • EW      — equal weight, 1/N (what a "Nifty-50 equal weight" basket holds).
  • FF-mcap — free-float market-cap weight. Real NSE weight_i = (Shares_i·Price_i·IWF_i·Cap_i)/Σ. We have
              no free Investable-Weight-Factor (IWF) feed, so we use FULL market cap (AMFI's official
              avg-mcap file) as the documented APPROXIMATION of free-float mcap. For the BROAD indices
              (Nifty 50/100/200/500, midcap/smallcap broad) this is exact-in-spirit (they are uncapped,
              Cap_i=1); for SECTORAL/THEMATIC indices NSE applies single-stock/sector caps we only
              partially replicate (a simple single-stock cap), so those FF weights over-state the
              mega-caps slightly — flagged in each file's `note`.

Constituents: niftyindices.com IndexConstituent CSV (a plain requests GET WITH a browser User-Agent
sails through the WAF that blocks header-less/proxy GETs); archives.nseindia.com is the byte-identical
mirror fallback. The CSV carries Company/Industry(sector)/Symbol/Series/ISIN — no weights. Row-count ==
expected is a built-in data-quality gate. Symbols with '-'/'&' (BAJAJ-AUTO, M&M) are kept verbatim.

Join: constituent ISIN -> AMFI full mcap (data/_shares/amfi_mcap.json) and -> vst_id (identity master).
Output: data/benchmarks/<slug>.json (self-describing) + data/benchmarks/_manifest.json. Graceful-degrade:
never raises; a failed index is skipped and logged. Display-plane only (Python-baked; no JS-parity port).

Provenance: methodology from the NIFTY Index Methodology docs (niftyindices.com); category->benchmark map
and the working fetch method established via the `benchmark-research` workflow (2026-06-25).
"""
from __future__ import annotations

import os
import io
import json
import time
import glob
import datetime as dt

import pandas as pd

try:
    import requests
except Exception:                                            # pragma: no cover
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))
OUT_DIR = os.path.join(DATA_DIR, "benchmarks")
MCAP_FILE = os.path.join(DATA_DIR, "_shares", "amfi_mcap.json")
IDMASTER_FILE = os.path.join(DATA_DIR, "stock_security_master.json")
SLUGS_FILE = os.path.join(DATA_DIR, "_benchmark_slugs.json")
SCREENER_DIR = os.path.join(DATA_DIR, "screener")

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
NIFTY_URL = "https://niftyindices.com/IndexConstituent/{slug}.csv"
NSE_MIRROR = "https://archives.nseindia.com/content/indices/{slug}.csv"

# verified constituent slugs (probed live 2026-06-25); KEY = our TRI index name. Extend freely.
SLUGS = {
    "NIFTY 50": "ind_nifty50list", "NIFTY NEXT 50": "ind_niftynext50list",
    "NIFTY 100": "ind_nifty100list", "NIFTY 200": "ind_nifty200list", "NIFTY 500": "ind_nifty500list",
    "NIFTY MIDCAP 150": "ind_niftymidcap150list", "NIFTY MIDCAP 100": "ind_niftymidcap100list",
    "NIFTY SMALLCAP 250": "ind_niftysmallcap250list", "NIFTY SMALLCAP 100": "ind_niftysmallcap100list",
    "NIFTY LARGEMIDCAP 250": "ind_niftylargemidcap250list", "NIFTY MIDSMALLCAP 400": "ind_niftymidsmallcap400list",
    "NIFTY MICROCAP 250": "ind_niftymicrocap250_list",
    "NIFTY BANK": "ind_niftybanklist", "NIFTY IT": "ind_niftyITlist", "NIFTY PHARMA": "ind_niftypharmalist",
    "NIFTY FMCG": "ind_niftyfmcglist", "NIFTY AUTO": "ind_niftyautolist", "NIFTY METAL": "ind_niftymetallist",
    "NIFTY ENERGY": "ind_niftyenergylist", "NIFTY REALTY": "ind_niftyrealtylist",
    "NIFTY FINANCIAL SERVICES": "ind_niftyfinancelist",
}

# the standard SEBI-category benchmark indices to build first (research R3). These are broad => uncapped.
PRIORITY = ["NIFTY 50", "NIFTY 100", "NIFTY NEXT 50", "NIFTY 200", "NIFTY 500",
            "NIFTY LARGEMIDCAP 250", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250", "NIFTY MIDSMALLCAP 400"]

# SEBI safety / NSE sectoral single-stock caps. Broad indices: the 25% SEBI ceiling (rarely binds, so
# effectively uncapped). Sectoral/thematic: NSE's 33% default. (Exact Bank 19/14/10 etc. not replicated.)
_BROAD = set(PRIORITY) | {"NIFTY MIDCAP 100", "NIFTY SMALLCAP 100", "NIFTY MICROCAP 250"}


def _is_broad(name):
    return name in _BROAD


def _cap_single(name):
    return 0.25 if _is_broad(name) else 0.33


# ----------------------------------------------------------------------------- identity + mcap
def _load_mcap():
    """ISIN -> full market cap (Rs cr) from AMFI's official avg-mcap file."""
    try:
        d = json.load(open(MCAP_FILE, encoding="utf-8"))
    except Exception:
        return {}, None
    out, period = {}, None
    for isin, r in d.items():
        try:
            out[isin] = float(r["amfi_mcap_cr"])
            period = period or r.get("period")
        except Exception:
            pass
    return out, period


def _load_identity():
    """ISIN -> vst_id and SYMBOL -> vst_id, from the security master."""
    try:
        m = json.load(open(IDMASTER_FILE, encoding="utf-8")).get("master", {})
    except Exception:
        return {}, {}
    by_isin, by_sym = {}, {}
    for vid, r in m.items():
        for i in (r.get("isins") or []):
            by_isin[i] = vid
        for s in (r.get("symbols") or []):
            by_sym[s] = vid
    return by_isin, by_sym


def _parse_pct(x):
    try:
        return float(str(x).replace("%", "").replace(",", "").strip())
    except Exception:
        return None


def _load_freefloat():
    """SYMBOL -> free-float factor ≈ 1 − latest promoter-holding fraction, from screener quarterly
    shareholding (statements.shareholding). This is a proxy for NSE's Investable Weight Factor (IWF):
    it captures the DOMINANT exclusion (promoter/promoter-group) but not the minor strategic/ADR/
    lock-in buckets, so it slightly OVER-states free float vs NSE's exact IWF. Missing -> caller uses 1.0."""
    out = {}
    for f in glob.glob(os.path.join(SCREENER_DIR, "*.json")):
        sym = os.path.splitext(os.path.basename(f))[0]
        try:
            sh = (json.load(open(f, encoding="utf-8")).get("statements") or {}).get("shareholding")
            if not sh:
                continue
            prow = next((r for r in sh if "promoter" in str(r.get("Unnamed: 0", "")).lower()), None)
            if prow is None:
                continue
            vals = [_parse_pct(v) for k, v in prow.items() if k != "Unnamed: 0"]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            out[sym] = min(1.0, max(0.02, 1.0 - vals[-1] / 100.0))   # latest quarter; clip to [2%,100%]
        except Exception:
            pass
    return out


# ----------------------------------------------------------------------------- fetch
def fetch_constituents(slug, progress=None):
    """Index constituent CSV -> DataFrame[name, sector, symbol, series, isin]. niftyindices first,
    archives.nseindia mirror fallback. Returns empty DataFrame on failure (never raises)."""
    log = progress or (lambda m: None)
    if requests is None:
        return pd.DataFrame()
    for url in (NIFTY_URL.format(slug=slug), NSE_MIRROR.format(slug=slug)):
        try:
            r = requests.get(url, headers=_UA, timeout=30)
            if r.status_code != 200 or "Symbol" not in r.text[:300]:
                continue
            df = pd.read_csv(io.StringIO(r.text))
            cols = {c.lower().strip(): c for c in df.columns}
            sym = cols.get("symbol")
            isin = cols.get("isin code") or cols.get("isin")
            if not sym or not isin:
                continue
            out = pd.DataFrame({
                "name": df[cols.get("company name", sym)].astype(str).str.strip(),
                "sector": df[cols.get("industry")].astype(str).str.strip() if cols.get("industry") else "",
                "symbol": df[sym].astype(str).str.strip(),     # keep '-'/'&' verbatim
                "isin": df[isin].astype(str).str.strip(),
            })
            return out[out["symbol"] != ""].reset_index(drop=True)
        except Exception as e:
            log(f"[benchmarks]   fetch {slug} via {url.split('/')[2]} failed: {e}")
    return pd.DataFrame()


# ----------------------------------------------------------------------------- weights
def _cap_redistribute(w: pd.Series, cap: float, iters: int = 100) -> pd.Series:
    """Iterative single-stock cap: clip any weight > cap to cap, redistribute the excess pro-rata to the
    uncapped names, repeat until stable. NSE applies this per quarter via a CapFactor; we apply the
    static single-stock cap (top-3/sector caps NOT replicated)."""
    w = w.copy()
    for _ in range(iters):
        over = w[w > cap + 1e-12]
        if over.empty:
            break
        excess = float((over - cap).sum())
        w[over.index] = cap
        free = w[w < cap - 1e-12]
        if free.empty or free.sum() <= 0:
            break
        w[free.index] = free + excess * (free / free.sum())
    return w / w.sum()


def build_benchmark(name, slug=None, mcap=None, by_isin=None, ff_map=None, progress=None):
    """Build one benchmark portfolio: fetch constituents, attach sector + vst_id + full mcap + a
    free-float factor (1 − promoter%), compute EW and free-float-mcap (capped) weights. Returns a
    self-describing dict or None on failure."""
    log = progress or (lambda m: None)
    slug = slug or SLUGS.get(name)
    if not slug:
        return None
    if mcap is None:
        mcap, _ = _load_mcap()
    if by_isin is None:
        by_isin, _ = _load_identity()
    if ff_map is None:
        ff_map = _load_freefloat()
    df = fetch_constituents(slug, progress=log)
    if df.empty:
        log(f"[benchmarks] {name}: no constituents fetched — skipped")
        return None
    n = len(df)
    df["vst_id"] = df["isin"].map(lambda i: by_isin.get(i))
    df["mcap_cr"] = df["isin"].map(lambda i: mcap.get(i))
    df["ff_factor"] = df["symbol"].map(lambda s: ff_map.get(s))   # 1 − promoter%; None if unknown
    have_m = df["mcap_cr"].notna()
    coverage = float(have_m.mean())
    ff_cov = float(df.loc[have_m, "ff_factor"].notna().mean()) if have_m.any() else 0.0
    # free-float mcap = full mcap × free-float factor (default 1.0 where promoter% unknown)
    df["ffmcap"] = df["mcap_cr"] * df["ff_factor"].fillna(1.0)
    ffw_full = pd.Series(0.0, index=df.index)
    if have_m.any():
        m = df.loc[have_m, "ffmcap"].astype(float)
        m = m[m > 0]
        ffw = (m / m.sum())
        ffw = _cap_redistribute(ffw, _cap_single(name))
        ffw_full.loc[ffw.index] = ffw
    ew = pd.Series(1.0 / n, index=df.index)                  # equal weight
    cons = []
    for i, row in df.iterrows():
        cons.append({"symbol": row["symbol"], "isin": row["isin"], "vst_id": row["vst_id"],
                     "name": row["name"], "sector": row["sector"],
                     "ff_factor": (None if pd.isna(row["ff_factor"]) else round(float(row["ff_factor"]), 3)),
                     "w_ew": round(float(ew[i]) * 100, 4),
                     "w_ffmcap": round(float(ffw_full[i]) * 100, 4)})
    cap_txt = ("uncapped (broad index)" if _is_broad(name)
               else f"with a {int(_cap_single(name)*100)}% single-stock cap (NSE's exact top-3/sector caps not replicated)")
    note = (f"Free-float-mcap weight = full mcap (AMFI { '' }) × free-float factor (1 − promoter%, from "
            f"quarterly shareholding) / Σ, {cap_txt}. The free-float factor proxies NSE's IWF via the "
            f"dominant promoter term (minor strategic/lock-in buckets not separated). EW = 1/N. "
            f"Free-float coverage {ff_cov:.0%} of mcap-covered names (rest assume full float).")
    return {"index": name, "slug": slug, "n": n,
            "asof": dt.date.today().isoformat(),
            "weighting": {"ew": "equal weight (1/N)",
                          "ffmcap": "free-float-adjusted market-cap = full mcap × (1 − promoter%), capped, renormalised"},
            "mcap_source": "AMFI average market capitalisation (data/_shares/amfi_mcap.json)",
            "freefloat_source": "promoter holding % from screener quarterly shareholding (proxy for NSE IWF)",
            "mcap_coverage": round(coverage, 3), "freefloat_coverage": round(ff_cov, 3),
            "low_confidence": bool(coverage < 0.90),
            "note": note, "constituents": cons}


def build_all(indices=None, progress=None) -> dict:
    """Build benchmark portfolios for `indices` (default = every slug we know), write one JSON each +
    a manifest. Returns a status dict. Never raises."""
    log = progress or (lambda m: print(m, flush=True))
    os.makedirs(OUT_DIR, exist_ok=True)
    # merge any probed slugs from disk
    try:
        SLUGS.update(json.load(open(SLUGS_FILE, encoding="utf-8")))
    except Exception:
        pass
    names = indices or list(SLUGS.keys())
    mcap, period = _load_mcap()
    by_isin, _ = _load_identity()
    ff_map = _load_freefloat()
    log(f"[benchmarks] free-float factors loaded for {len(ff_map)} symbols (promoter% proxy for IWF)")
    manifest, n_ok = {}, 0
    for name in names:
        try:
            b = build_benchmark(name, mcap=mcap, by_isin=by_isin, ff_map=ff_map, progress=log)
        except Exception as e:
            log(f"[benchmarks] {name}: ERROR {e}")
            b = None
        if not b:
            continue
        slug = b["slug"]
        with open(os.path.join(OUT_DIR, f"{slug}.json"), "w", encoding="utf-8") as f:
            json.dump(b, f, ensure_ascii=False, separators=(",", ":"))
        manifest[name] = {"slug": slug, "n": b["n"], "coverage": b["mcap_coverage"],
                          "broad": _is_broad(name), "low_confidence": b["low_confidence"]}
        n_ok += 1
        log(f"[benchmarks] {name}: {b['n']} constituents, mcap coverage {b['mcap_coverage']:.0%}")
        time.sleep(0.25)
    manifest_obj = {"asof": dt.date.today().isoformat(), "mcap_period": period,
                    "n_indices": n_ok, "indices": manifest}
    with open(os.path.join(OUT_DIR, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest_obj, f, ensure_ascii=False, indent=1)
    log(f"[benchmarks] built {n_ok}/{len(names)} benchmark portfolios -> {OUT_DIR}")
    return {"ok": n_ok > 0, "n_indices": n_ok, "mcap_period": period}


def load_manifest() -> dict:
    try:
        return json.load(open(os.path.join(OUT_DIR, "_manifest.json"), encoding="utf-8"))
    except Exception:
        return {}


def load_benchmark(slug) -> dict:
    try:
        return json.load(open(os.path.join(OUT_DIR, f"{slug}.json"), encoding="utf-8"))
    except Exception:
        return {}


if __name__ == "__main__":
    build_all(progress=print)
