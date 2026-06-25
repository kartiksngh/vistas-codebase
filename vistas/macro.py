"""
India-native MACRO layer for Vistas (official public sources — no paid feed).

Pulls a curated set of India macro time-series — inflation (CPI/WPI), policy &
market rates, money & credit, the external sector, real activity, market flows —
into one wide monthly/daily snapshot, so the Macro tab reads India-first (and the
world/cross-asset layer in world.py supplies the US/global comparators).

Taxonomy is inspired by what a full macro data hub covers (prices, money &
banking, rates, external sector, output/activity, markets/flows, fiscal) — but
every series here is fetched from a FREE official source, never a paid vendor.

Sources, by reliability:
  • data.gov.in (OGD)  — the Govt of India open-data aggregator. Carries MOSPI's
                         CPI/IIP and the Commerce Ministry (Office of Economic
                         Adviser) WPI as proper monthly series. Generic resource
                         fetcher below; needs a free API key (env DATA_GOV_API_KEY,
                         falls back to OGD's public sample key — rate-limited).
  • NSE                — daily FII/FPI + DII cash-market net flows (latest day only
                         from the public endpoint, so we accumulate forward).
  • FBIL / RBI         — daily G-sec benchmark yields & policy rates (wired as
                         resource ids are confirmed; see MACRO_CATALOG).

Design mirrors world.py: a static catalog (series id/name/group/unit/freq/source),
a per-source fetch dispatcher, a dated wide snapshot CSV, and load/serve helpers.
NEVER raises on a network/parse failure — a missing source just doesn't appear in
the snapshot, and the terminal keeps serving whatever was last pulled.

Snapshot: data/India Macro till <date>.csv  (wide: Date x friendly series name).
Date convention: monthly series are stamped at the FIRST of their month
(YYYY-MM-01); daily/weekly series at their observation date. Values are the raw
published numbers (index level, %, ₹ crore, etc. — see each series' `unit`).

Provenance: written for Vistas 2026-06-20. Self-contained; imports nothing from
the research engine. Re-uses vistas.fetch's NSE session helper when present.
"""
from __future__ import annotations

import os
import re
import io
import glob
import json
import time
import random
import datetime as dt

import pandas as pd

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None

try:
    import openpyxl                      # only needed for the FBIL G-sec par-yield XLSX
except Exception:                       # pragma: no cover
    openpyxl = None

try:                                    # api.mospi.gov.in serves a Govt self-signed cert
    import urllib3                       # chain certifi rejects -> we verify=False that host
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:                       # pragma: no cover
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(HERE, "..", "data"))

# OGD public sample key works but is heavily rate-limited; KV can register a free
# key at data.gov.in and set DATA_GOV_API_KEY to lift the cap.
OGD_SAMPLE_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"
OGD_KEY = os.environ.get("DATA_GOV_API_KEY", "").strip() or OGD_SAMPLE_KEY
OGD_BASE = "https://api.data.gov.in/resource"

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}
_MONTHS.update({m[:3].lower(): i for m, i in list(_MONTHS.items()) if m})
for _i in range(1, 13):                 # also accept numeric "1".."12" / "01".."12"
    _MONTHS[str(_i)] = _i
    _MONTHS[f"{_i:02d}"] = _i


# ============================================================ THE CATALOG
# Each entry: (id, name, group, unit, freq, source, spec)
#   spec drives the fetcher:
#     {"kind":"ogd",  "resource":<id>, "value":<field>, "filters":{...},
#      "period":{"year":<field>,"month":<field>}  OR  {"date":<field>,"fmt":<strftime>},
#      "scale":<float>}                     -> data.gov.in resource as a monthly series
#     {"kind":"nse_fiidii", "field":"FII"|"DII"}   -> NSE daily flows (accumulate fwd)
#     {"kind":"fbil", ...}                          -> FBIL daily file (wired on confirm)
#     {"kind":"none"}                               -> placeholder (skipped until wired)
# Series whose spec is "none" or whose fetch yields nothing simply never appear in
# the snapshot — the picker/Macro tab degrade gracefully.
#
# The OGD resource ids / field names below are filled from a live discovery probe
# (see _refresh_macro.py notes); any left as "none" await a confirmed source.

def _m(id, name, group, unit, freq, source, spec):
    return {"id": id, "name": name, "group": group, "unit": unit,
            "freq": freq, "source": source, "spec": spec}

# CPI, IIP (all sub-indices) and the WPI All-commodities HEADLINE now come CURRENT from
# MOSPI eSankhyiki (see the _mospi_* fetchers). OGD now serves ONLY the WPI sub-component
# breakdown (Primary/Fuel/Manufactured) — eSankhyiki returns every descendant item for
# those (Manufactured = 119k rows), so the breakdown stays on OGD (lagged ~Oct-2023, same
# 2011-12 base as the eSankhyiki headline). FBIL rates & NSE flows are current (daily).
_WPI = "239ac3d0-f08d-40d0-b03c-9b7a426a62d5"     # WPI, WIDE (months = INDX<MMYYYY> columns)

MACRO_CATALOG = [
    # ---------------------------------------------------------------- Inflation (CPI/WPI)
    # CPI from MOSPI eSankhyiki (CURRENT to latest month, incl. Combined; 2024 base, 2013→).
    _m("cpi_comb", "CPI — Combined (index)", "Inflation", "index (2024=100)", "monthly",
       "MOSPI eSankhyiki", {"kind": "mospi_cpi", "sector": 3, "field": "index"}),
    _m("cpi_r", "CPI — Rural (index)", "Inflation", "index (2024=100)", "monthly",
       "MOSPI eSankhyiki", {"kind": "mospi_cpi", "sector": 1, "field": "index"}),
    _m("cpi_u", "CPI — Urban (index)", "Inflation", "index (2024=100)", "monthly",
       "MOSPI eSankhyiki", {"kind": "mospi_cpi", "sector": 2, "field": "index"}),
    _m("cpi_infl_comb", "CPI inflation — Combined (YoY)", "Inflation", "% YoY", "monthly",
       "MOSPI eSankhyiki (official)", {"kind": "mospi_cpi", "sector": 3, "field": "inflation"}),
    _m("cpi_infl_r", "CPI inflation — Rural (YoY)", "Inflation", "% YoY", "monthly",
       "MOSPI eSankhyiki (official)", {"kind": "mospi_cpi", "sector": 1, "field": "inflation"}),
    _m("cpi_infl_u", "CPI inflation — Urban (YoY)", "Inflation", "% YoY", "monthly",
       "MOSPI eSankhyiki (official)", {"kind": "mospi_cpi", "sector": 2, "field": "inflation"}),
    # Core CPI (ex food & fuel) — Combined index + YoY, from mospi_cpi_core. Catalog
    # NAME == the module's emitted name byte-for-byte (em-dash U+2014) so kind
    # "mospi_cpi_core" maps it. SHORT HISTORY: 2024-base legs start Jan-2025, so the
    # index begins Jan-2025 and the first real YoY is Jan-2026 (caveat in source str).
    _m("cpi_core_comb", "CPI Combined — Core ex food & fuel (index, official-weight est.)",
       "Inflation", "index (2024=100)", "monthly",
       "MOSPI eSankhyiki (derived core; 2024 base, legs from Jan-2025)",
       {"kind": "mospi_cpi_core"}),
    _m("cpi_core_infl_comb", "CPI Combined — Core ex food & fuel inflation (YoY, est.)",
       "Inflation", "% YoY", "monthly",
       "MOSPI eSankhyiki (derived core YoY; 2024 base, first YoY Jan-2026)",
       {"kind": "mospi_cpi_core"}),
    _m("wpi_all", "WPI — All commodities (index)", "Inflation", "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki", {"kind": "mospi_wpi"}),     # CURRENT headline (clean 169-mo series)
    _m("wpi_prim", "WPI — Primary articles (index)", "Inflation", "index (2011-12=100)", "monthly",
       "Commerce (OEA), via data.gov.in", {"kind": "ogd_wide", "resource": _WPI, "colfmt": "indx",
                                            "filters": {"COMM_CODE": "1100000000"}}),
    _m("wpi_fuel", "WPI — Fuel & power (index)", "Inflation", "index (2011-12=100)", "monthly",
       "Commerce (OEA), via data.gov.in", {"kind": "ogd_wide", "resource": _WPI, "colfmt": "indx",
                                            "filters": {"COMM_CODE": "1200000000"}}),
    _m("wpi_mfg", "WPI — Manufactured products (index)", "Inflation", "index (2011-12=100)", "monthly",
       "Commerce (OEA), via data.gov.in", {"kind": "ogd_wide", "resource": _WPI, "colfmt": "indx",
                                            "filters": {"COMM_CODE": "1300000000"}}),
    _m("wpi_infl", "WPI inflation (YoY)", "Inflation", "% YoY", "monthly",
       "derived from OEA WPI", {"kind": "yoy", "base": "WPI — All commodities (index)"}),
    # ---------------------------------------------------------------- Policy & market rates
    _m("tbill91", "91-day T-bill yield", "Policy & rates", "%", "daily",
       "FBIL", {"kind": "fbil", "path": "tbill", "value": "rate",
                "filterfield": "tenorName", "filterval": "3 Months"}),
    _m("gsec1", "India 1Y G-sec yield", "Policy & rates", "%", "monthly+",
       "FBIL (par-yield curve)", {"kind": "fbil_gsec", "tenor": 1.0}),
    _m("gsec5", "India 5Y G-sec yield", "Policy & rates", "%", "monthly+",
       "FBIL (par-yield curve)", {"kind": "fbil_gsec", "tenor": 5.0}),
    _m("gsec10", "India 10Y G-sec yield", "Policy & rates", "%", "monthly+",
       "FBIL (par-yield curve)", {"kind": "fbil_gsec", "tenor": 10.0}),
    _m("gsec30", "India 30Y G-sec yield", "Policy & rates", "%", "monthly+",
       "FBIL (par-yield curve)", {"kind": "fbil_gsec", "tenor": 30.0}),
    _m("repo", "RBI repo rate", "Policy & rates", "%", "policy-dates",
       "RBI policy repo rate · via BIS cbpol", {"kind": "bis_cbpol", "ref_area": "IN"}),
    # True overnight WACR (weighted-average call rate) must come from RBI later —
    # fbil_surface exposes only TERM MIBOR (14D/1M/3M), NOT an overnight call rate,
    # so wiring it here would misrepresent a 3-month term rate as the overnight WACR.
    # Left as a placeholder on purpose (honesty over filling the box).
    _m("call", "Call money rate (WACR)", "Policy & rates", "%", "daily",
       "RBI (DBIE)", {"kind": "none"}),
    # ---------------------------------------------------------------- Money & credit (RBI Weekly Statistical Supplement)
    # forex / m3_gr / credit_gr are now LIVE from rbi_wss (RBI WSS). The catalog row
    # NAME must match rbi_wss's emitted friendly name EXACTLY for kind "rbi_wss" so
    # META_BY_NAME (built from this catalog) auto-describes them in the picker.
    # forex Total: rbi_wss reports USD bn (US$ Mn / 1000) — unit fixed from "USD mn".
    _m("forex", "Forex reserves — Total (USD bn)", "External sector", "USD bn", "weekly",
       "RBI Weekly Statistical Supplement", {"kind": "rbi_wss"}),
    # M3 and SCB bank-credit LEVELS (₹ crore) — needed by derived growth + a future
    # credit-deposit-gap signal. rbi_wss emits these fortnightly.
    _m("m3_lvl", "Money supply M3 (Rs crore)", "Money & credit", "₹ crore", "fortnightly",
       "RBI Weekly Statistical Supplement", {"kind": "rbi_wss"}),
    _m("scb_credit_lvl", "SCB — Bank Credit (Rs crore)", "Money & credit", "₹ crore", "fortnightly",
       "RBI Weekly Statistical Supplement", {"kind": "rbi_wss"}),
    _m("scb_deposits_lvl", "SCB — Aggregate Deposits (Rs crore)", "Money & credit", "₹ crore", "fortnightly",
       "RBI Weekly Statistical Supplement", {"kind": "rbi_wss"}),
    # Growth (% YoY): DATE-BASED YoY of the levels above (NOT shift(12) — the data is
    # fortnightly ~26 obs/yr, so a 12-period shift is ~6 months, not a year). See
    # kind "rbi_wss_yoy" + _yoy_by_date() in build_snapshot.
    _m("credit_gr", "Bank credit growth (YoY)", "Money & credit", "% YoY", "fortnightly",
       "derived from RBI WSS SCB Bank Credit",
       {"kind": "rbi_wss_yoy", "base": "SCB — Bank Credit (Rs crore)"}),
    _m("m3_gr", "Broad money (M3) growth (YoY)", "Money & credit", "% YoY", "fortnightly",
       "derived from RBI WSS M3",
       {"kind": "rbi_wss_yoy", "base": "Money supply M3 (Rs crore)"}),
    # ---------------------------------------------------------------- National accounts (GDP)
    # 3 headline GDP series from MOSPI NAS via mospi_nas. Catalog NAME == the module's
    # emitted friendly name BYTE-FOR-BYTE (em-dash U+2014) so kind "mospi_nas" maps
    # each to its like-named series and META_BY_NAME auto-describes it. We fetch a
    # GDP-ONLY frame (indicators 5 & 22) — NOT the full ~230-page GVA pull — so the
    # import never hangs (see _mospi_nas_frame).
    _m("gdp_nom", "GDP — nominal (current prices, Rs cr, annual)", "National accounts",
       "Rs crore", "annual", "MOSPI eSankhyiki (National Accounts)", {"kind": "mospi_nas"}),
    _m("gdp_real", "GDP — real (constant prices, Rs cr, annual)", "National accounts",
       "Rs crore (2011-12 base)", "annual", "MOSPI eSankhyiki (National Accounts)",
       {"kind": "mospi_nas"}),
    _m("gdp_growth", "GDP growth — real (% YoY, official, annual)", "National accounts",
       "% YoY", "annual", "MOSPI eSankhyiki (National Accounts)", {"kind": "mospi_nas"}),
    # ---------------------------------------------------------------- External sector
    # Forex reserve COMPONENTS (USD bn) — cheap, same rbi_wss frame; enables a
    # reserves-composition view alongside the Total above.
    _m("forex_fca", "Forex reserves — Foreign Currency Assets (USD bn)", "External sector",
       "USD bn", "weekly", "RBI Weekly Statistical Supplement", {"kind": "rbi_wss"}),
    _m("forex_gold", "Forex reserves — Gold (USD bn)", "External sector",
       "USD bn", "weekly", "RBI Weekly Statistical Supplement", {"kind": "rbi_wss"}),
    _m("forex_sdr", "Forex reserves — SDRs (USD bn)", "External sector",
       "USD bn", "weekly", "RBI Weekly Statistical Supplement", {"kind": "rbi_wss"}),
    _m("forex_rtp", "Forex reserves — Reserve Position in IMF (USD bn)", "External sector",
       "USD bn", "weekly", "RBI Weekly Statistical Supplement", {"kind": "rbi_wss"}),
    _m("exports", "Merchandise exports", "External sector", "USD mn", "monthly",
       "Commerce", {"kind": "none"}),
    _m("imports", "Merchandise imports", "External sector", "USD mn", "monthly",
       "Commerce", {"kind": "none"}),
    _m("trade_bal", "Merchandise trade balance", "External sector", "USD mn", "monthly",
       "Commerce", {"kind": "none"}),
    # ---------------------------------------------------------------- Real activity (IIP)
    _m("iip_gen", "IIP — General", "Activity", "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki", {"kind": "mospi_iip", "category": "General"}),
    _m("iip_min", "IIP — Mining", "Activity", "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki", {"kind": "mospi_iip", "category": "Mining"}),
    _m("iip_mfg", "IIP — Manufacturing", "Activity", "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki", {"kind": "mospi_iip", "category": "Manufacturing"}),
    _m("iip_elec", "IIP — Electricity", "Activity", "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki", {"kind": "mospi_iip", "category": "Electricity"}),
    # IIP use-based classification — the 6 demand-side buckets, from mospi_iip_usebased.
    # Catalog NAME == the module's emitted "IIP <stem> (index)" byte-for-byte so kind
    # "mospi_iip_ub" maps each. Index only (we skip the per-bucket YoY to stay curated);
    # base 2011-12 = 100.
    _m("iip_ub_prim", "IIP Primary goods (index)", "Activity", "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki (IIP use-based)", {"kind": "mospi_iip_ub"}),
    _m("iip_ub_cap", "IIP Capital goods (index)", "Activity", "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki (IIP use-based)", {"kind": "mospi_iip_ub"}),
    _m("iip_ub_int", "IIP Intermediate goods (index)", "Activity", "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki (IIP use-based)", {"kind": "mospi_iip_ub"}),
    _m("iip_ub_infra", "IIP Infrastructure/Construction goods (index)", "Activity",
       "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki (IIP use-based)", {"kind": "mospi_iip_ub"}),
    _m("iip_ub_cdur", "IIP Consumer durables (index)", "Activity", "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki (IIP use-based)", {"kind": "mospi_iip_ub"}),
    _m("iip_ub_cndur", "IIP Consumer non-durables (index)", "Activity", "index (2011-12=100)", "monthly",
       "MOSPI eSankhyiki (IIP use-based)", {"kind": "mospi_iip_ub"}),
    # ---------------------------------------------------------------- Market flows
    _m("fii_net", "FII/FPI net (cash)", "Market flows", "₹ crore", "daily",
       "NSE", {"kind": "nse_fiidii", "field": "FII"}),
    _m("dii_net", "DII net (cash)", "Market flows", "₹ crore", "daily",
       "NSE", {"kind": "nse_fiidii", "field": "DII"}),
]
NAME_BY_ID = {c["id"]: c["name"] for c in MACRO_CATALOG}
META_BY_NAME = {c["name"]: {"group": c["group"], "unit": c["unit"],
                            "freq": c["freq"], "source": c["source"]} for c in MACRO_CATALOG}


# --- sibling data modules (already built + audited). Guarded so a bad/missing
#     import can never break the macro snapshot. rbi_wss supplies the RBI Weekly
#     Statistical Supplement levels (forex reserves, M3, SCB bank credit/deposits)
#     used to fill the forex / m3_gr / credit_gr placeholders below. fbil_surface
#     would supply a call-money proxy IF it exposed an overnight rate — it does not
#     (only TERM MIBOR 14D/1M/3M), so the `call` placeholder is left {kind:"none"}.
try:
    from . import rbi_wss as _rbi_wss
except Exception:                       # pragma: no cover
    _rbi_wss = None
try:
    from . import fbil_surface as _fbil_surface
except Exception:                       # pragma: no cover
    _fbil_surface = None
# MOSPI eSankhyiki sibling modules (already built + audited). Each is wired below
# as a frame-fetch-ONCE block (mirrors rbi_wss): the catalog NAME must be
# byte-identical to the module's emitted friendly name so META_BY_NAME auto-describes.
try:
    from . import mospi_nas as _mospi_nas
except Exception:                       # pragma: no cover
    _mospi_nas = None
try:
    from . import mospi_cpi_core as _mospi_cpi_core
except Exception:                       # pragma: no cover
    _mospi_cpi_core = None
try:
    from . import mospi_iip_usebased as _mospi_iip_ub
except Exception:                       # pragma: no cover
    _mospi_iip_ub = None


# ============================================================ fetchers
def _to_float(raw):
    try:
        return float(re.sub(r"[, ]", "", str(raw)))
    except Exception:
        return None


def _ogd_records(resource: str, filters: dict | None = None, page: int = 100,
                 max_records: int = 20000, log=lambda m: None) -> list:
    """All records of a data.gov.in resource (paged), with optional server-side
    field filters. The OGD public sample key hard-caps at 10 records/call regardless
    of `limit`, so we page by the ACTUAL returned count and stop at the resource's
    reported `total`. Returns [] on any failure (never raises)."""
    if requests is None or not resource:
        return []
    out, offset, total = [], 0, None
    params = {"api-key": OGD_KEY, "format": "json", "limit": page}
    for k, v in (filters or {}).items():
        params[f"filters[{k}]"] = v
    while offset < max_records:
        try:
            r = requests.get(f"{OGD_BASE}/{resource}", params=dict(params, offset=offset),
                             timeout=40, headers={"User-Agent": "Mozilla/5.0"})
            j = r.json()
        except Exception as e:
            log(f"    ogd {resource} offset {offset} failed: {e}")
            break
        recs = j.get("records", []) if isinstance(j, dict) else []
        if total is None:
            try:
                total = int(j.get("total") or 0)
            except Exception:
                total = 0
        if not recs:
            break
        out.extend(recs)
        offset += len(recs)
        if total and offset >= total:
            break
        time.sleep(random.uniform(0.25, 0.6))
    return out


def _ogd_tidy_series(spec: dict, log=lambda m: None) -> pd.Series:
    """A TIDY OGD resource (one row per period) -> date-indexed Series. period =
    {year,month} split fields (CPI) or {date,fmt}; value = a single field."""
    recs = _ogd_records(spec["resource"], spec.get("filters"), log=log)
    per, vf, scale = spec.get("period", {}), spec.get("value"), float(spec.get("scale", 1.0))
    rows = {}
    for rec in recs:
        ts = None
        if "year" in per:
            try:
                yy = int(re.sub(r"[^0-9]", "", str(rec.get(per["year"])))[:4])
                mo = rec.get(per["month"]) if per.get("month") else 1
                mm = _MONTHS.get(str(mo).strip().lower())
                if mm is None:
                    mm = int(re.sub(r"[^0-9]", "", str(mo)) or 1)
                ts = pd.Timestamp(yy, max(1, min(12, mm)), 1)
            except Exception:
                ts = None
        elif "date" in per:
            ts = pd.to_datetime(str(rec.get(per["date"])), format=per.get("fmt"),
                                errors="coerce", dayfirst=True)
        v = _to_float(rec.get(vf))
        if ts is not None and not pd.isna(ts) and v is not None:
            rows[pd.Timestamp(ts).normalize()] = v * scale
    return pd.Series(rows, dtype="float64").sort_index()


def _wide_col_date(col: str, fmt: str):
    """A pivoted month-column name -> Timestamp(month-start), or None.
       fmt 'indx' : INDX042012  (INDX + MM + YYYY)
       fmt 'usym' : _2012_apr   (_ + YYYY + _ + mon)"""
    if fmt == "indx":
        m = re.fullmatch(r"INDX(\d{2})(\d{4})", col)
        if m:
            try:
                return pd.Timestamp(int(m.group(2)), int(m.group(1)), 1)
            except Exception:
                return None
    elif fmt == "usym":
        m = re.fullmatch(r"_(\d{4})_([A-Za-z]{3,})", col)
        if m:
            mm = _MONTHS.get(m.group(2).strip().lower())
            if mm:
                try:
                    return pd.Timestamp(int(m.group(1)), mm, 1)
                except Exception:
                    return None
    return None


def _ogd_wide_series(spec: dict, log=lambda m: None) -> pd.Series:
    """A PIVOTED OGD resource (WPI/IIP: one row per commodity/sector, each month a
    column) filtered to a single row -> melted date-indexed Series."""
    recs = _ogd_records(spec["resource"], spec.get("filters"), log=log)
    if not recs:
        return pd.Series(dtype="float64")
    fmt = spec.get("colfmt", "indx")
    rows = {}
    for rec in recs:                    # server filter pins one row; melt all returned, last-wins
        for col, raw in rec.items():
            ts = _wide_col_date(col, fmt)
            if ts is None:
                continue
            v = _to_float(raw)
            if v is not None:
                rows[pd.Timestamp(ts).normalize()] = v
    return pd.Series(rows, dtype="float64").sort_index()


# ---- MOSPI eSankhyiki CPI (CURRENT, incl. Combined which OGD lacks) ----
# The portal SPA's prod API. CPI series come from /cpi/getCpiData on the 2024 base:
# series 'Back' = back-cast to 2013, 'Current' = latest — together a continuous
# All-India General index per sector, with MOSPI's OFFICIAL published YoY inflation.
# Host presents a Govt self-signed cert chain certifi rejects -> verify=False (public,
# read-only data). Paged 10 records/page (param `page`; recordPerPage is server-fixed).
MOSPI_API = "https://api.mospi.gov.in/api"
_MOSPI_VERIFY = False


def _mospi_cpi_frame(sector_code, base_year=2024, log=lambda m: None) -> pd.DataFrame:
    """All-India CPI (General) for one sector (1=Rural,2=Urban,3=Combined) from MOSPI
    eSankhyiki — 'Back'(→2013) + 'Current' on the 2024 base, paginated. Returns
    DataFrame[date x {index, inflation}]; `inflation` is MOSPI's official YoY %."""
    if requests is None:
        return pd.DataFrame()
    rows = {}
    for series in ("Back", "Current"):
        page, pages = 1, 1
        while page <= pages and page <= 60:
            try:
                r = requests.get(f"{MOSPI_API}/cpi/getCpiData", timeout=40, verify=_MOSPI_VERIFY,
                    params={"base_year": base_year, "series": series, "state_code": 1,
                            "sector_code": sector_code, "level": "Group", "division_code": 0, "page": page},
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://esankhyiki.mospi.gov.in/"})
                j = r.json()
            except Exception as e:
                log(f"    mospi cpi s{sector_code} {series} p{page} failed: {e}")
                break
            for x in j.get("data", []):
                mm = _MONTHS.get(str(x.get("month", "")).strip().lower())
                if not mm:
                    continue
                try:
                    ts = pd.Timestamp(int(x["year"]), mm, 1)
                except Exception:
                    continue
                rows[ts] = (_to_float(x.get("index")), _to_float(x.get("inflation")))
            pages = int(j.get("meta_data", {}).get("totalPages", 1) or 1)
            page += 1
            time.sleep(random.uniform(0.15, 0.35))
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame.from_dict(rows, orient="index", columns=["index", "inflation"]).sort_index()


def _mospi_iip_frame(base_year="2011-12", log=lambda m: None) -> dict:
    """IIP (base 2011-12, Monthly) from MOSPI eSankhyiki, CURRENT. Two one-shot calls
    (`limit=5000`): type=General + type=Sectoral. Returns {category: index Series} for
    General/Mining/Manufacturing/Electricity (the AGGREGATE rows — sub_category empty;
    the 23 NIC manufacturing divisions, which also carry category='Manufacturing', are
    dropped)."""
    if requests is None:
        return {}
    out = {}

    def _grab(type_, want):
        try:
            r = requests.get(f"{MOSPI_API}/iip/getIipData", timeout=90, verify=_MOSPI_VERIFY,
                params={"base_year": base_year, "frequency": "Monthly", "type": type_, "limit": 5000},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://esankhyiki.mospi.gov.in/"})
            recs = r.json().get("data", [])
        except Exception as e:
            log(f"    mospi iip {type_} failed: {e}")
            return
        for x in recs:
            cat = x.get("category")
            if cat not in want or (str(x.get("sub_category") or "").strip()):
                continue
            mm = _MONTHS.get(str(x.get("month", "")).strip().lower())
            if not mm:
                continue
            try:
                ts = pd.Timestamp(int(x["year"]), mm, 1)
            except Exception:
                continue
            out.setdefault(cat, {})[ts] = _to_float(x.get("index"))

    _grab("General", {"General"})
    time.sleep(random.uniform(0.3, 0.7))
    _grab("Sectoral", {"Mining", "Manufacturing", "Electricity"})
    return {c: pd.Series(v, dtype="float64").dropna().sort_index() for c, v in out.items()}


def _mospi_wpi_all_series(base_year="2011-12", log=lambda m: None) -> pd.Series:
    """WPI All-commodities index (base 2011-12) from MOSPI eSankhyiki, CURRENT — clean
    169-month headline series via getWpiRecords (one `limit` call)."""
    if requests is None:
        return pd.Series(dtype="float64")
    try:
        r = requests.get(f"{MOSPI_API}/wpi/getWpiRecords", timeout=90, verify=_MOSPI_VERIFY,
            params={"base_year": base_year, "major_group_code": "1000000000", "limit": 5000},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://esankhyiki.mospi.gov.in/"})
        recs = r.json().get("data", [])
    except Exception as e:
        log(f"    mospi wpi failed: {e}")
        return pd.Series(dtype="float64")
    rows = {}
    for x in recs:
        mm = _MONTHS.get(str(x.get("month", "")).strip().lower())
        if not mm:
            continue
        try:
            ts = pd.Timestamp(int(x["year"]), mm, 1)
        except Exception:
            continue
        rows[ts] = _to_float(x.get("index_value"))
    return pd.Series(rows, dtype="float64").dropna().sort_index()


# ---- FBIL (inline JSON, no auth) — full history via /<prod>/fetchfiltered ----
FBIL_BASE = "https://www.fbil.org.in/wasdm"


def _fbil_series(spec: dict, start="2014-01-01", log=lambda m: None) -> pd.Series:
    """An FBIL inline-JSON product (refrates/tbill) -> daily date-indexed Series.
    Filters to one sub-series (e.g. tenorName='3 Months') and reads `value`."""
    if requests is None:
        return pd.Series(dtype="float64")
    end = dt.date.today().isoformat()
    url = f"{FBIL_BASE}/{spec['path']}/fetchfiltered"
    try:
        r = requests.get(url, params={"fromDate": start, "toDate": end, "authenticated": "false"},
                         timeout=60, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                                              "Referer": "https://www.fbil.org.in/"})
        data = r.json()
    except Exception as e:
        log(f"    fbil {spec['path']} failed: {e}")
        return pd.Series(dtype="float64")
    ff, fv, vf = spec.get("filterfield"), spec.get("filterval"), spec.get("value", "rate")
    rows = {}
    for rec in (data or []):
        if ff and rec.get(ff) != fv:
            continue
        ts = pd.to_datetime(str(rec.get("processRunDate")), errors="coerce")
        v = _to_float(rec.get(vf))
        if ts is not None and not pd.isna(ts) and v is not None:
            rows[pd.Timestamp(ts).normalize()] = v
    return pd.Series(rows, dtype="float64").sort_index()


# ---- FBIL G-sec par-yield curve (one XLSX per business day) ----
def _fbil_gsec_dates(start="2018-01-01", log=lambda m: None) -> list:
    """Available G-sec publication dates (Timestamps), oldest→newest. [] on failure."""
    if requests is None:
        return []
    end = dt.date.today().isoformat()
    try:
        r = requests.get(f"{FBIL_BASE}/gsec/fetchfiltered",
                         params={"fromDate": start, "toDate": end, "authenticated": "false"},
                         timeout=60, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.fbil.org.in/"})
        j = r.json()
    except Exception as e:
        log(f"    fbil gsec date-list failed: {e}")
        return []
    ds = sorted({pd.to_datetime(str(x.get("processRunDate")), errors="coerce").normalize()
                 for x in (j or [])} - {pd.NaT})
    return [d for d in ds if pd.notna(d)]


def _parse_par_yield(content: bytes, tenors) -> dict:
    """{tenor: annualized YTM%} from one FBIL G-sec file's par-yield sheet. Handles BOTH
    formats by magic byte: the modern .xlsx (PK zip, ~Jan-2023→, sheet 'Par Yield', via
    openpyxl) and the legacy .xls (OLE2 d0cf11e0, 2018-2022, sheet 'Par-Yield', via xlrd).
    Same layout in both: a header row starting 'Tenor (Year)', then tenor in col0 and the
    annualized YTM% in col2."""
    magic = content[:4]
    if magic == b"PK\x03\x04":
        engine = "openpyxl"
    elif magic == b"\xd0\xcf\x11\xe0":
        engine = "xlrd"
    else:
        return {}
    try:
        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, engine=engine)
    except Exception:
        return {}
    key = next((k for k in sheets if "par" in k.lower() and "yield" in k.lower()), None)
    if key is None:
        return {}
    df = sheets[key]
    want = {float(t) for t in tenors}
    out, hdr = {}, False
    for row in df.itertuples(index=False, name=None):
        c0 = row[0] if len(row) else None
        if not hdr:
            if isinstance(c0, str) and c0.strip().lower().startswith("tenor"):
                hdr = True
            continue
        try:
            t = float(c0)
        except Exception:
            continue
        if t in want and len(row) > 2:
            v = _to_float(row[2])
            if v is not None:
                out[t] = v
    return out


def _fbil_gsec_curve(tenors, start="2018-01-01", recent=8, skip_dates=None,
                     max_dl=200, log=lambda m: None) -> pd.DataFrame:
    """Month-end-sampled (+ last `recent` daily) FBIL par-yield curve for `tenors`.
    Downloads one XLSX per chosen date (skipping `skip_dates` already on file), so the
    monthly history is built once then only the new tail is fetched. DataFrame[date x tenor]."""
    if requests is None or openpyxl is None:
        log("    (gsec skipped: requests/openpyxl unavailable)")
        return pd.DataFrame()
    dates = _fbil_gsec_dates(start, log=log)
    if not dates:
        return pd.DataFrame()
    s = pd.Series(dates, index=pd.DatetimeIndex(dates))
    month_end = list(s.groupby([s.index.year, s.index.month]).max().values)  # last business day per month
    chosen = sorted(set(pd.to_datetime(month_end)) | set(dates[-recent:]))
    skip = set(pd.to_datetime(list(skip_dates))) if skip_dates is not None else set()
    chosen = [d for d in chosen if d not in skip][-max_dl:]
    if not chosen:
        return pd.DataFrame()
    log(f"    gsec: {len(chosen)} dates to download (of {len(dates)} available; rest cached)")
    rows = {}
    for i, d in enumerate(chosen):
        try:
            r = requests.get(f"{FBIL_BASE}/gsec/downloadPublished",
                             params={"date": d.strftime("%Y-%m-%d"), "authenticated": "false"},
                             timeout=60, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.fbil.org.in/"})
            cur = _parse_par_yield(r.content, tenors)
            if cur:
                rows[pd.Timestamp(d).normalize()] = cur
        except Exception as e:
            log(f"      gsec {d.date()} failed: {e}")
        if (i + 1) % 25 == 0:
            log(f"      gsec {i+1}/{len(chosen)}")
        time.sleep(random.uniform(0.4, 0.9))
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


# ---- RBI repo / policy rate, via the BIS central-bank-policy-rate dataset ----
# BIS publishes each central bank's official policy rate sourced DIRECTLY from the
# central bank; for India (REF_AREA=IN) this IS the RBI repo rate ("From 3 Apr 2001
# onwards: official repo overnight rate", source attributed "Reserve Bank of India").
# Clean machine-readable SDMX-CSV with full daily history — far more reliable than
# scraping RBI's DBIE Angular SPA. We compress to step change-points (the rate only
# moves on MPC decisions) so the embed stays tiny and the step line is exact.
BIS_CBPOL = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0"


def _bis_cbpol_series(ref_area="IN", start="2000-01-01", step_compress=True, log=lambda m: None) -> pd.Series:
    if requests is None:
        return pd.Series(dtype="float64")
    try:
        r = requests.get(f"{BIS_CBPOL}/D.{ref_area}", params={"format": "csv", "startPeriod": start},
                         timeout=90, headers={"User-Agent": "Mozilla/5.0"})
        df = pd.read_csv(io.StringIO(r.text))
    except Exception as e:
        log(f"    bis cbpol {ref_area} failed: {e}")
        return pd.Series(dtype="float64")
    if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
        return pd.Series(dtype="float64")
    idx = pd.to_datetime(df["TIME_PERIOD"], errors="coerce")
    val = pd.to_numeric(df["OBS_VALUE"], errors="coerce")
    s = pd.Series(val.values, index=idx).dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    s.index = s.index.normalize()
    if step_compress and len(s):
        keep = s.ne(s.shift())
        keep.iloc[-1] = True            # always retain the latest level
        s = s[keep]
    return s


# ---- NSE FII/DII (latest day only from the public endpoint -> accumulate forward)
_NSE_HOME = "https://www.nseindia.com"
_NSE_FIIDII = "https://www.nseindia.com/api/fiidiiTradeReact"


def _nse_session():
    """A requests session with NSE cookies (land on the homepage first, like a
    browser). Reuses vistas.fetch's UA pool when available."""
    if requests is None:
        return None
    s = requests.Session()
    try:
        from . import fetch as _f
        s.headers.update(_f._pick_headers())
    except Exception:
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                          "Accept-Language": "en-US,en;q=0.9"})
    s.headers.update({"Referer": _NSE_HOME + "/", "Origin": _NSE_HOME})
    try:
        s.get(_NSE_HOME, timeout=25)
    except Exception:
        pass
    return s


def fetch_fiidii_today(log=lambda m: None) -> dict:
    """{'date': Timestamp, 'FII': net_cr, 'DII': net_cr} for the latest available
    day, or {} on failure. NSE's public endpoint returns only the latest day."""
    s = _nse_session()
    if s is None:
        return {}
    for attempt in range(2):
        try:
            r = s.get(_NSE_FIIDII, timeout=25,
                      headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
            data = r.json()
            out = {}
            d = None
            for row in data:
                cat = (row.get("category") or "").upper()
                net = float(re.sub(r"[, ]", "", str(row.get("netValue", "nan"))))
                d = pd.to_datetime(row.get("date"), dayfirst=True, errors="coerce")
                if "FII" in cat or "FPI" in cat:
                    out["FII"] = net
                elif "DII" in cat:
                    out["DII"] = net
            if out and d is not None and not pd.isna(d):
                out["date"] = pd.Timestamp(d).normalize()
                return out
        except Exception as e:
            log(f"    nse fii/dii attempt {attempt+1} failed: {e}")
            time.sleep(random.uniform(1.5, 3.0))
            s = _nse_session()
    return {}


def _fiidii_existing() -> pd.DataFrame:
    """Whatever FII/DII history is already in the latest snapshot (so accumulate
    forward never loses prior days)."""
    df = load()
    cols = [c for c in (NAME_BY_ID["fii_net"], NAME_BY_ID["dii_net"]) if c in getattr(df, "columns", [])]
    return df[cols].dropna(how="all") if len(df) and cols else pd.DataFrame()


# ============================================================ RBI WSS frame
def _rbi_wss_frame(log=lambda m: None) -> dict:
    """Fetch the RBI Weekly Statistical Supplement once and return its
    {friendly_name: pd.Series} map (forex / M3 / SCB levels). {} on any failure —
    rbi_wss never raises, but we guard defensively too. The Total forex level it
    returns is already USD bn (US$ Mn / 1000)."""
    if _rbi_wss is None:
        log("    rbi_wss module unavailable")
        return {}
    try:
        wss_s, _wss_m = _rbi_wss.fetch_series(start="2000-01-01", log=log)
        return wss_s or {}
    except Exception as e:
        log(f"    rbi_wss fetch failed: {e}")
        return {}


def _mospi_nas_frame(log=lambda m: None) -> dict:
    """Fetch ONLY the 3 headline GDP series we wire (nominal level, real level, real
    growth %), returning {friendly_name: pd.Series}. {} on any failure.

    We deliberately do NOT call mospi_nas.fetch_series() — that also pulls GVA
    (indicator 1, ~230 pages) and PFCE/GFCF, which would make the import hang. We
    reach into the module's audited low-level helpers and pull only indicator 5 (GDP,
    Current + Back annual) and 22 (GDP growth %, Current annual). The emitted NAMES are
    copied verbatim from mospi_nas._SPECS / _GROWTH_SPECS so they stay byte-identical
    to the catalog rows."""
    if _mospi_nas is None:
        log("    mospi_nas module unavailable")
        return {}
    try:
        out: dict[str, pd.Series] = {}
        # GDP levels (indicator 5): annual, splice the Back history behind Current.
        cur_recs = _mospi_nas._pull_indicator(5, "Current", log=log)
        back_recs = _mospi_nas._pull_indicator(5, "Back", log=log)
        nom = _mospi_nas._splice_back(
            _mospi_nas._build_series(cur_recs, "Annual", "current_price"),
            _mospi_nas._build_series(back_recs, "Annual", "current_price"))
        real = _mospi_nas._splice_back(
            _mospi_nas._build_series(cur_recs, "Annual", "constant_price"),
            _mospi_nas._build_series(back_recs, "Annual", "constant_price"))
        if nom is not None and len(nom):
            out["GDP — nominal (current prices, Rs cr, annual)"] = nom
        if real is not None and len(real):
            out["GDP — real (constant prices, Rs cr, annual)"] = real
        # Official real GDP growth % (indicator 22, Current annual, constant_price field).
        gr_recs = _mospi_nas._pull_indicator(22, "Current", log=log)
        growth = _mospi_nas._build_series(gr_recs, "Annual", "constant_price")
        if growth is not None and len(growth):
            out["GDP growth — real (% YoY, official, annual)"] = growth
        return out
    except Exception as e:
        log(f"    mospi_nas fetch failed: {e}")
        return {}


def _mospi_cpi_core_frame(log=lambda m: None) -> dict:
    """Fetch the MOSPI core-CPI module once and return its {friendly_name: pd.Series}
    map (we map only the Combined ex-food&fuel index + YoY rows we wire). {} on any
    failure. Cheap: the 2024-base legs only span 2025→present."""
    if _mospi_cpi_core is None:
        log("    mospi_cpi_core module unavailable")
        return {}
    try:
        s, _m = _mospi_cpi_core.fetch_series(start="2000-01-01", log=log)
        return s or {}
    except Exception as e:
        log(f"    mospi_cpi_core fetch failed: {e}")
        return {}


def _mospi_iip_ub_frame(log=lambda m: None) -> dict:
    """Fetch the MOSPI IIP use-based module once and return its
    {friendly_name: pd.Series} map (we map only the 6 use-based index rows we wire).
    {} on any failure. Cheap: two one-shot GETs (use-based + sectoral)."""
    if _mospi_iip_ub is None:
        log("    mospi_iip_usebased module unavailable")
        return {}
    try:
        s, _m = _mospi_iip_ub.fetch_series(start="2000-01-01", log=log)
        return s or {}
    except Exception as e:
        log(f"    mospi_iip_usebased fetch failed: {e}")
        return {}


def _yoy_by_date(series: pd.Series, days: int = 365, tol_days: int = 20) -> pd.Series:
    """DATE-BASED year-on-year growth (%) for an IRREGULAR-cadence series.

    For each observation at date t we look up the level ~`days` (default 365 =
    one year) earlier using a NEAREST-PRIOR match within `tol_days` tolerance, and
    compute  100*(level(t)/level(t-1y) - 1).  This is the CORRECT YoY for
    fortnightly/weekly data (M3, SCB credit) — unlike a fixed `.shift(12)`, which on
    ~26-obs/yr data lands ~6 months back and reports a half-year change as a "year".

    Worked example: M3 = ₹313.6 lakh-cr on 2026-05-31 and ₹290.0 lakh-cr on the
    obs nearest 2025-05-31 -> YoY = 100*(313.6/290.0 - 1) ≈ 8.1%.
    """
    if series is None or len(series) < 2:
        return pd.Series(dtype="float64")
    s = series.dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    if len(s) < 2:
        return pd.Series(dtype="float64")
    # Reindex the series onto each target date t-1y with a nearest-PRIOR (backward)
    # asof match bounded by tol_days, then divide aligned-by-position.
    targets = s.index - pd.Timedelta(days=days)
    prior = s.reindex(targets, method="ffill",
                      tolerance=pd.Timedelta(days=tol_days))
    prior.index = s.index                       # realign back to the current date t
    out = (s / prior - 1.0) * 100.0
    return out.dropna()


# ============================================================ build snapshot
def build_snapshot(progress=None) -> dict:
    """Fetch every wired series + merge into the dated India-Macro snapshot CSV
    (fresh-wins, so partial pulls never drop history). Never raises."""
    log = progress or (lambda m: print(m, flush=True))
    series = {}

    def _stash(name, s):
        if s is not None and len(s):
            series[name] = s
            log(f"        {len(s)} obs  {s.index.min().date()}..{s.index.max().date()}")

    # --- OGD (CPI tidy, WPI/IIP wide) + FBIL (T-bill) — skip placeholders/derived ---
    for c in MACRO_CATALOG:
        spec, kind = c["spec"], c["spec"].get("kind")
        if kind == "ogd_tidy":
            log(f"[macro] {c['name']} <- OGD {spec['resource']} {spec.get('filters')}")
            _stash(c["name"], _ogd_tidy_series(spec, log=log))
            time.sleep(random.uniform(0.3, 0.8))
        elif kind == "ogd_wide":
            log(f"[macro] {c['name']} <- OGD {spec['resource']} {spec.get('filters')} (wide)")
            _stash(c["name"], _ogd_wide_series(spec, log=log))
            time.sleep(random.uniform(0.3, 0.8))
        elif kind == "fbil":
            log(f"[macro] {c['name']} <- FBIL {spec['path']} ({spec.get('filterval')})")
            _stash(c["name"], _fbil_series(spec, log=log))
            time.sleep(random.uniform(0.3, 0.8))
        elif kind == "bis_cbpol":
            log(f"[macro] {c['name']} <- BIS cbpol {spec.get('ref_area')}")
            _stash(c["name"], _bis_cbpol_series(spec.get("ref_area", "IN"), log=log))
            time.sleep(random.uniform(0.3, 0.8))

    # --- MOSPI eSankhyiki CPI (fetch each sector's frame once; assign index + inflation) ---
    mospi = [c for c in MACRO_CATALOG if c["spec"].get("kind") == "mospi_cpi"]
    if mospi:
        for sc in sorted({c["spec"]["sector"] for c in mospi}):
            log(f"[macro] MOSPI eSankhyiki CPI sector {sc} (Back+Current, base 2024)")
            fr = _mospi_cpi_frame(sc, log=log)
            for c in mospi:
                if c["spec"]["sector"] == sc and len(fr) and c["spec"]["field"] in fr.columns:
                    _stash(c["name"], fr[c["spec"]["field"]].dropna())
            time.sleep(random.uniform(0.3, 0.8))

    # --- MOSPI eSankhyiki IIP (current; 2 one-shot calls -> 4 sub-indices) ---
    iip = [c for c in MACRO_CATALOG if c["spec"].get("kind") == "mospi_iip"]
    if iip:
        log("[macro] MOSPI eSankhyiki IIP (General + Sectoral, base 2011-12)")
        frames = _mospi_iip_frame(log=log)
        for c in iip:
            s = frames.get(c["spec"]["category"])
            if s is not None and len(s):
                _stash(c["name"], s)

    # --- MOSPI eSankhyiki WPI All-commodities headline (current) ---
    if any(c["spec"].get("kind") == "mospi_wpi" for c in MACRO_CATALOG):
        log("[macro] MOSPI eSankhyiki WPI All-commodities (base 2011-12)")
        s = _mospi_wpi_all_series(log=log)
        for c in MACRO_CATALOG:
            if c["spec"].get("kind") == "mospi_wpi" and len(s):
                _stash(c["name"], s)

    # --- FBIL G-sec par-yield curve (one XLSX/day; fetch once for all wired tenors) ---
    gsec = [c for c in MACRO_CATALOG if c["spec"].get("kind") == "fbil_gsec"]
    if gsec:
        tenors = sorted({c["spec"]["tenor"] for c in gsec})
        old = load()
        # skip ONLY dates where the 10Y already has a value (not the whole frame index —
        # else gsec month-ends coinciding with a daily T-bill date get wrongly skipped).
        skip = (old[NAME_BY_ID["gsec10"]].dropna().index
                if (len(old) and NAME_BY_ID["gsec10"] in old.columns) else None)
        log(f"[macro] India G-sec curve {tenors} <- FBIL (month-end + recent)")
        curve = _fbil_gsec_curve(tenors, skip_dates=skip, log=log)
        for c in gsec:
            t = c["spec"]["tenor"]
            if len(curve) and t in curve.columns:
                _stash(c["name"], curve[t].dropna())

    # --- RBI Weekly Statistical Supplement (forex / M3 / SCB levels) ---
    # Fetch the WSS frame ONCE, then map every catalog row of kind "rbi_wss" to its
    # like-named series (catalog NAME == rbi_wss friendly name). META_BY_NAME (built
    # from the catalog) auto-describes them, so the design stays catalog-driven.
    rbi_specs = [c for c in MACRO_CATALOG if c["spec"].get("kind") == "rbi_wss"]
    if rbi_specs:
        log("[macro] RBI WSS (forex reserves / M3 / SCB credit & deposits) <- rbi_wss")
        wss = _rbi_wss_frame(log=log)
        for c in rbi_specs:
            s = wss.get(c["name"])
            if s is not None and len(s):
                _stash(c["name"], s)

    # --- MOSPI NAS (3 headline GDP series) — fetch the curated GDP-only frame ONCE,
    # then map every catalog row of kind "mospi_nas" to its like-named series.
    nas_specs = [c for c in MACRO_CATALOG if c["spec"].get("kind") == "mospi_nas"]
    if nas_specs:
        log("[macro] MOSPI NAS (GDP nominal/real level + real growth) <- mospi_nas")
        nas = _mospi_nas_frame(log=log)
        for c in nas_specs:
            s = nas.get(c["name"])
            if s is not None and len(s):
                _stash(c["name"], s)

    # --- MOSPI core CPI (Combined ex food & fuel index + YoY) — frame once, then map ---
    core_specs = [c for c in MACRO_CATALOG if c["spec"].get("kind") == "mospi_cpi_core"]
    if core_specs:
        log("[macro] MOSPI core CPI (Combined ex food & fuel) <- mospi_cpi_core")
        core = _mospi_cpi_core_frame(log=log)
        for c in core_specs:
            s = core.get(c["name"])
            if s is not None and len(s):
                _stash(c["name"], s)

    # --- MOSPI IIP use-based (6 demand-side buckets) — frame once, then map ---
    ub_specs = [c for c in MACRO_CATALOG if c["spec"].get("kind") == "mospi_iip_ub"]
    if ub_specs:
        log("[macro] MOSPI IIP use-based (6 buckets) <- mospi_iip_usebased")
        ub = _mospi_iip_ub_frame(log=log)
        for c in ub_specs:
            s = ub.get(c["name"])
            if s is not None and len(s):
                _stash(c["name"], s)

    # --- derived YoY (% change vs 12 months earlier) from the MONTHLY index series ---
    for c in MACRO_CATALOG:
        spec = c["spec"]
        if spec.get("kind") == "yoy" and spec.get("base") in series:
            base = series[spec["base"]].sort_index()
            yoy = (base / base.shift(12) - 1.0) * 100.0
            yoy = yoy.dropna()
            if len(yoy):
                log(f"[macro] {c['name']} <- YoY of '{spec['base']}'")
                _stash(c["name"], yoy)

    # --- DATE-BASED YoY for IRREGULAR-cadence (fortnightly) RBI WSS levels ---
    # M3 / SCB credit are ~26 obs/yr, so the generic shift(12) above would be ~6
    # months. Use _yoy_by_date (nearest-prior ~365-day lookup) instead.
    for c in MACRO_CATALOG:
        spec = c["spec"]
        if spec.get("kind") == "rbi_wss_yoy" and spec.get("base") in series:
            yoy = _yoy_by_date(series[spec["base"]], days=365, tol_days=20)
            if len(yoy):
                log(f"[macro] {c['name']} <- date-based YoY of '{spec['base']}'  "
                    f"latest {yoy.index[-1].date()} = {yoy.iloc[-1]:.2f}%")
                _stash(c["name"], yoy)

    # --- NSE FII/DII (accumulate forward onto existing history) ---
    if any(c["spec"].get("kind") == "nse_fiidii" for c in MACRO_CATALOG):
        log("[macro] FII/DII <- NSE (latest day, accumulate forward)")
        tod = fetch_fiidii_today(log=log)
        prior = _fiidii_existing()
        fii = prior[NAME_BY_ID["fii_net"]].copy() if NAME_BY_ID["fii_net"] in prior.columns else pd.Series(dtype="float64")
        dii = prior[NAME_BY_ID["dii_net"]].copy() if NAME_BY_ID["dii_net"] in prior.columns else pd.Series(dtype="float64")
        if tod.get("date") is not None:
            if "FII" in tod:
                fii.loc[tod["date"]] = tod["FII"]
            if "DII" in tod:
                dii.loc[tod["date"]] = tod["DII"]
            log(f"        {tod['date'].date()}  FII {tod.get('FII')}  DII {tod.get('DII')}")
        if len(fii):
            series[NAME_BY_ID["fii_net"]] = fii.sort_index()
        if len(dii):
            series[NAME_BY_ID["dii_net"]] = dii.sort_index()

    if not series:
        return {"ok": False, "error": "no macro series fetched (no sources wired/reachable)"}

    wide = pd.DataFrame(series)
    wide.index = pd.DatetimeIndex(wide.index).normalize()
    wide = wide[~wide.index.duplicated(keep="last")].sort_index()
    wide.index.name = "Date"

    existing = latest_csv()
    if existing:
        try:
            old = pd.read_csv(existing)
            old["Date"] = pd.to_datetime(old["Date"], errors="coerce")
            old = old.dropna(subset=["Date"]).set_index("Date").sort_index()
            wide = wide.combine_first(old).sort_index()
        except Exception:
            pass

    out = _write_dated(wide)
    return {"ok": True, "file": os.path.basename(out), "n_series": wide.shape[1],
            "series": list(wide.columns), "n_rows": wide.shape[0],
            "asof": wide.index.max().date().isoformat(),
            "start": wide.index.min().date().isoformat()}


# ============================================================ snapshot I/O
def latest_csv():
    cands = glob.glob(os.path.join(DATA_DIR, "India Macro till *.csv"))
    if not cands:
        return None

    def _key(p):
        m = re.search(r"till (.+)\.csv$", os.path.basename(p))
        d = pd.to_datetime(m.group(1).strip(), errors="coerce") if m else None
        return d if pd.notna(d) else pd.Timestamp(os.path.getmtime(p), unit="s")

    return max(cands, key=_key)


def _write_dated(wide: pd.DataFrame) -> str:
    last = wide.index.max().date()
    out = os.path.join(DATA_DIR, f"India Macro till {last.strftime('%b %#d, %Y')}.csv")
    wide.reset_index().to_csv(out, index=False)
    return out


# ============================================================ serve
_CACHE = {"path": None, "df": None, "mtime": None}


def load() -> pd.DataFrame:
    path = latest_csv()
    if path is None:
        return pd.DataFrame()
    mtime = os.path.getmtime(path)
    if _CACHE["df"] is None or _CACHE["path"] != path or _CACHE["mtime"] != mtime:
        df = pd.read_csv(path)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
        df = df.apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")
        _CACHE.update({"path": path, "df": df, "mtime": mtime})
    return _CACHE["df"]


def available() -> list:
    df = load()
    return list(df.columns) if len(df) else []


def coverage() -> dict:
    df = load()
    out = {}
    for c in df.columns:
        s = df[c].dropna()
        if len(s):
            out[c] = {"start": s.index[0].strftime("%Y-%m-%d"),
                      "end": s.index[-1].strftime("%Y-%m-%d"), "n_obs": int(len(s))}
    return out


def meta() -> dict:
    """{friendly name: {group, unit, freq, source}} for present series — feeds the
    picker and the Macro tab's Definition/Method/Why blocks."""
    return {c: META_BY_NAME[c] for c in available() if c in META_BY_NAME}


def names() -> dict:
    """{friendly name: series id} — secondary search key for the picker."""
    return {c["name"]: c["id"] for c in MACRO_CATALOG if c["name"] in available()}


if __name__ == "__main__":
    print(json.dumps(build_snapshot(progress=lambda m: print(m, flush=True)),
                     indent=2, default=str))
