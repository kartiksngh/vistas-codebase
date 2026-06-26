"""
rotation.py — QUADRANT-ROTATION-OVER-TIME engine (task #44/#86/#87/#88).

WHAT THIS BUILDS
----------------
The terminal already plots a single point per entity in the Analyst (ARM, Y, 0-100) × FM
(net-active flow, X) quadrant. This engine turns that snapshot into a MONTHLY TRAIL — "how the
entity moved through the quadrant over time" — for the FOUR aggregate entity types:
    (2) each FUND scheme        peer_group = its SEBI category
    (3) each whole AMC book     peer_group = "AMC"
    (4) each CATEGORY           peer_group = "Category"
(Entity type (1), the per-STOCK trail, is built inside vistas/screens.py — the stock already has
its own ARM series and its own flow series; here we aggregate stocks into portfolio CENTROIDS.)

THE TWO AXES, AGGREGATED TO A PORTFOLIO CENTROID
------------------------------------------------
For a portfolio P in month t, over its EQUITY sleeve (we drop debt/cash/passive index legs and
RENORMALISE the weights to sum to 1 across the equity names we can place on both axes):

    centroid_arm(P,t)  = Σ_i  w_i(t) · ARM_i(t)
    centroid_flow(P,t) = Σ_i  w_i(t) · flow_i(t)

  • w_i(t)  = the holding weight = market_value_i / Σ market_value (renormalised to the placeable
              equity sleeve, so the weights used are exactly the names that have BOTH an ARM and a
              flow value that month — no silent half-coverage).
  • ARM_i(t)= the stock's LSEG StarMine ARM headline (ARM_100_REG, 0-100) as-of month-end t,
              forward-filled from its last change-point on/before the month-end (ARM is a
              step/point-in-time series, daily change-points 1998→now). 0-100 percentile.
  • flow_i(t)= the stock's CORP-ACTION-IMMUNE net active flow that month (₹cr; the same
              funds_flows.build_stock_series number the per-stock screen/cockpit uses). Because
              flow is a ₹-crore SUM, the weighted-mean centroid is a per-₹-of-portfolio average
              of the underlying name flows — direction (>0 net buying) is what matters, not scale.

"CENTROID" (plain English): the centre of mass of the portfolio in the quadrant. Picture every
equity holding as a dot placed at (its flow, its ARM); slide each dot's influence by how big a
slice of the portfolio it is; the centroid is the single weighted-average dot. A fund whose money
sits in high-ARM, being-bought names has a centroid in the top-right (Q1); one in low-ARM,
being-sold names sits bottom-left (Q4). Watching the centroid move month to month = the rotation.

"OWN_PCTILE" (plain English): where the entity's LATEST centroid sits inside its OWN history.
own_pctile.arm = the percentile rank of the latest centroid_arm among ALL of this entity's past
monthly centroid_arm values (0 = its lowest-ever, 100 = its highest-ever, 50 = its median month).
Same for flow. WHY: it answers "is this fund unusually constructive/aggressive FOR ITSELF right
now, vs just its normal stance?" — a self-relative read that strips out the entity's permanent
style (a perma-defensive fund is judged against its own range, not the universe's).
Method: linear-interpolated percentile of the last point within the sorted history (pandas
Series.rank(pct=True) on the value equal to the latest), ×100, rounded. Needs ≥3 months else null.

QUADRANT (same convention as the stock screen, vistas/screens.py quad()):
    rec = centroid_arm >= 50 (analysts net-recommending the book on average)
    buy = centroid_flow > 0  (the book is being net-bought on average)
    Q1 rec&buy · Q2 rec&¬buy · Q3 ¬rec&buy (FM ahead of the street) · Q4 neither.

OUTPUT  -> data/_rotation/centroids.json
    {
      "meta": {...currency, coverage, axis definitions...},
      "entities": [
        { entity_type: "fund"|"amc"|"category",
          entity_id, name, peer_group,                # peer_group tags drive the JS peer overlay
          points: [ {date, arm, flow, quad, n_holdings, equity_wt_covered} , ... ],  # monthly trail
          own_pctile: {arm, flow},                    # latest vs the entity's OWN history
          latest: {date, arm, flow, quad} },
        ...
      ]
    }

LICENSING: centroids are AGGREGATES (weighted means over many names) — no per-stock ARM ships
here, so this is publishable regardless of the ARM gating. (The per-stock screen traj ships raw
per-stock ARM, which is now allowed under ABSL sign-off.)

Display-plane only — Python-baked values, no JS-parity port. Never raises; returns {} on failure.
"""
from __future__ import annotations

import os
import json

# entity caps / gates
ARM_REC = 50.0          # ARM >= 50 = analysts net-recommending (same as screens.py)
MAX_POINTS = 120        # cap each trail at the last 120 months (10y) so the JSON stays lean
MIN_PCTILE_OBS = 3      # need >= 3 historical centroids before an own-percentile is meaningful
MIN_EQUITY_COVER = 0.30 # need >= 30% of the equity sleeve (by weight) placeable on BOTH axes,
                        # else the centroid is too sparse to trust -> the month is skipped


# --------------------------------------------------------------------------- ARM monthly history
def _build_arm_by_vid(log=print):
    """{vst_id -> sorted [(date 'YYYY-MM-DD', arm_headline_value)]} over FULL history.

    Merges every lineage ISIN that maps to a vst_id into one date-sorted headline series (so an
    ISIN re-issue doesn't split the trail), dedup by date keeping the last value. The headline
    mnemonic is ARM_100_REG (the regional 0-100 percentile)."""
    try:
        from . import arm as _arm, idmap
    except Exception as e:
        log(f"[rotation] ARM/idmap import failed ({e}) — ARM axis unavailable.")
        return {}
    try:
        raw = _arm.load_raw()          # {ISIN: {"name":.., "mnem": {MNEM: [(date,val)..]}}}
    except Exception as e:
        log(f"[rotation] arm.load_raw failed ({e}) — ARM axis unavailable.")
        return {}
    if not raw:
        log("[rotation] no compiled ARM cache — ARM axis will be empty (flow-only centroids).")
        return {}
    by_vid: dict = {}
    for isin, rec in raw.items():
        head = (rec.get("mnem") or {}).get("ARM_100_REG") or []
        if not head:
            continue
        vid = idmap.resolve_to_vid(isin, kind="isin")
        if not vid:
            continue
        by_vid.setdefault(vid, []).extend((d, float(v)) for d, v in head if d and v is not None)
    out = {}
    for vid, pts in by_vid.items():
        # date-sort, dedup by date keeping the last value seen for that date
        pts.sort(key=lambda t: t[0])
        dedup = {}
        for d, v in pts:
            dedup[d] = v
        out[vid] = sorted(dedup.items(), key=lambda t: t[0])
    log(f"[rotation] ARM history compiled for {len(out)} vst_ids.")
    return out


def _arm_on_month(series, ym):
    """ARM value in force as-of the END of month `ym` (YYYY-MM): the value of the most recent
    change-point at/before the month-end (forward-fill). None if no change-point yet exists."""
    if not series:
        return None
    cutoff = ym + "-31"      # string compare; any day in the month is < 'YYYY-MM-31' boundary works
    v = None
    for d, val in series:
        if d <= cutoff:
            v = val
        else:
            break
    return v


# --------------------------------------------------------------------------- flow monthly history
def _build_flow_by_vid(months_back, log=print):
    """{vst_id -> {ym -> net_active_flow_cr}} from the canonical per-stock flow series (the same
    funds_flows.build_stock_series number the screen/cockpit uses). Keyed by vst_id (so it joins
    the holdings store directly)."""
    try:
        from . import funds_flows as _ff
    except Exception as e:
        log(f"[rotation] funds_flows import failed ({e}) — flow axis unavailable.")
        return {}
    try:
        ser = _ff.build_stock_series(months_back=months_back)   # {nse_symbol: {months,flow,..,vst_id}}
    except Exception as e:
        log(f"[rotation] build_stock_series failed ({e}) — flow axis unavailable.")
        return {}
    by_vid: dict = {}
    for sym, d in (ser or {}).items():
        vid = d.get("vst_id")
        if not vid:
            continue
        m, fl = d.get("months") or [], d.get("flow") or []
        by_vid[vid] = {ym: f for ym, f in zip(m, fl) if isinstance(f, (int, float))}
    log(f"[rotation] flow history compiled for {len(by_vid)} vst_ids.")
    return by_vid


# --------------------------------------------------------------------------- holdings panel
def _load_equity_holdings(root, log=print):
    """The monthly EQUITY holdings panel -> a DataFrame with ym, vst_id, scheme_name, amc,
    sebi_category, market_value (₹cr). Equity sleeve only (drops debt/cash/derivatives) and drops
    passive index/ETF schemes (their 'flow' self-cancels and they aren't an active stance)."""
    import pandas as pd
    p = os.path.join(root, "data", "funds", "history", "holdings_history.parquet")
    try:
        h = pd.read_parquet(p, columns=["ym", "vst_id", "scheme_name", "navindia_code", "amc",
                                        "sebi_category", "investment_type", "market_value"])
    except Exception as e:
        log(f"[rotation] holdings store unreadable ({e}).")
        return None
    h = h[h["investment_type"].astype(str).str.strip().str.lower() == "equity"].copy()
    h = h[h["vst_id"].notna() & (h["vst_id"].astype(str).str.strip() != "")]
    h = h[h["market_value"].fillna(0) > 0]
    cat = h["sebi_category"].astype(str).str.lower()
    nm = h["scheme_name"].astype(str).str.lower()
    passive = (cat.str.contains("index|etf|exchange traded", na=False)
               | nm.str.contains(r"index|etf|nifty|sensex|bse |s&p|bharat bond", na=False))
    h = h[~passive].copy()
    h["amc"] = h["amc"].astype(str)
    h["sebi_category"] = h["sebi_category"].astype(str)
    return h


# --------------------------------------------------------------------------- centroid maths
def _pctile_of_latest(values):
    """Percentile (0-100) of the LAST value within the full list of values (its own history),
    linear-interpolated tie handling via rank(pct). None if < MIN_PCTILE_OBS points."""
    if not values or len(values) < MIN_PCTILE_OBS:
        return None
    import pandas as pd
    s = pd.Series(values, dtype="float64").dropna()
    if len(s) < MIN_PCTILE_OBS:
        return None
    last = s.iloc[-1]
    # average-rank percentile of the latest value within the whole series
    pct = (s.rank(pct=True, method="average").iloc[-1])
    return round(float(pct) * 100.0, 1)


def _quad(arm, flow):
    rec = (arm is not None and arm >= ARM_REC)
    buy = (flow is not None and flow > 0)
    if rec and buy:
        return 1
    if rec and not buy:
        return 2
    if (not rec) and buy:
        return 3
    return 4


def _centroid_points(panel, arm_by_vid, arm_series_cache, flow_by_vid):
    """Given one entity's monthly equity holdings (DataFrame grouped by ym already filtered),
    produce the sorted trail of {date, arm, flow, quad, n_holdings, equity_wt_covered}.
    For each month: weight by market_value over the names placeable on BOTH axes (renormalised);
    skip a month with < MIN_EQUITY_COVER of the equity weight covered."""
    pts = []
    for ym, g in panel.groupby("ym"):
        mv_total = float(g["market_value"].sum())
        if mv_total <= 0:
            continue
        num_arm = num_flow = wt_cov = 0.0
        n_hold = 0
        for vid, mv in g.groupby("vst_id")["market_value"].sum().items():
            ser = arm_series_cache.get(vid)
            if ser is None:
                ser = arm_series_cache[vid] = arm_by_vid.get(vid) or []
            arm_v = _arm_on_month(ser, ym)
            flow_v = (flow_by_vid.get(vid) or {}).get(ym)
            if arm_v is None or flow_v is None:
                continue
            w = float(mv)
            num_arm += w * arm_v
            num_flow += w * flow_v
            wt_cov += w
            n_hold += 1
        if wt_cov <= 0 or (wt_cov / mv_total) < MIN_EQUITY_COVER:
            continue
        arm_c = num_arm / wt_cov
        flow_c = num_flow / wt_cov
        pts.append({
            "date": ym, "arm": round(arm_c, 1), "flow": round(flow_c, 1),
            "quad": _quad(arm_c, flow_c), "n_holdings": int(n_hold),
            "equity_wt_covered": round(wt_cov / mv_total, 3),
        })
    pts.sort(key=lambda p: p["date"])
    return pts[-MAX_POINTS:]


def _entity_record(entity_type, entity_id, name, peer_group, points):
    if not points:
        return None
    own = {"arm": _pctile_of_latest([p["arm"] for p in points]),
           "flow": _pctile_of_latest([p["flow"] for p in points])}
    last = points[-1]
    return {
        "entity_type": entity_type, "entity_id": str(entity_id), "name": str(name),
        "peer_group": peer_group, "points": points, "own_pctile": own,
        "latest": {"date": last["date"], "arm": last["arm"], "flow": last["flow"], "quad": last["quad"]},
    }


# --------------------------------------------------------------------------- build
def build_centroids(site_data_dir, root, months_back=120, progress=None):
    """Build the portfolio-centroid rotation trails (fund / AMC / category) -> centroids.json.

    `site_data_dir` = the built site's data/ dir (where centroids.json is written under _rotation/);
    `root` = repo root (for the holdings store + ARM cache). Returns the out dict (or {} on failure)."""
    log = progress or (lambda m: None)
    h = _load_equity_holdings(root, log=log)
    if h is None or not len(h):
        log("[rotation] no equity holdings — skipped.")
        return {}
    arm_by_vid = _build_arm_by_vid(log=log)
    flow_by_vid = _build_flow_by_vid(months_back=months_back, log=log)
    if not flow_by_vid:
        log("[rotation] no flow history — cannot place the FM axis; skipped.")
        return {}
    arm_cache: dict = {}

    # keep only months we can actually place a flow on (the flow series caps at months_back)
    flow_months = set()
    for d in flow_by_vid.values():
        flow_months |= set(d.keys())
    if flow_months:
        h = h[h["ym"].isin(flow_months)].copy()
    if not len(h):
        log("[rotation] holdings and flow months don't overlap — skipped.")
        return {}

    entities = []

    # (2) FUND scheme — peer_group = its SEBI category. Identify a scheme by navindia_code (stable
    #     code; scheme_name can vary) and carry the latest scheme_name + category as the label.
    n_fund = 0
    for code, g in h.groupby("navindia_code"):
        last_row = g.sort_values("ym").iloc[-1]
        cat = last_row["sebi_category"] or "Uncategorised"
        rec = _entity_record("fund", code, last_row["scheme_name"], cat,
                             _centroid_points(g, arm_by_vid, arm_cache, flow_by_vid))
        if rec:
            entities.append(rec)
            n_fund += 1

    # (3) AMC book — the whole AMC's equity holdings together. peer_group = "AMC".
    n_amc = 0
    for amc, g in h.groupby("amc"):
        rec = _entity_record("amc", amc, amc, "AMC",
                             _centroid_points(g, arm_by_vid, arm_cache, flow_by_vid))
        if rec:
            entities.append(rec)
            n_amc += 1

    # (4) CATEGORY — every fund in a SEBI category pooled. peer_group = "Category".
    n_cat = 0
    for cat, g in h.groupby("sebi_category"):
        rec = _entity_record("category", cat, cat, "Category",
                             _centroid_points(g, arm_by_vid, arm_cache, flow_by_vid))
        if rec:
            entities.append(rec)
            n_cat += 1

    hold_ym = h["ym"].max()
    out = {
        "title": "Quadrant rotation over time — portfolio centroids",
        "meta": {
            "axes": {
                "analyst_y": "weighted-mean LSEG StarMine ARM (ARM_100_REG, 0-100); >=50 = recommending",
                "fm_x": "weighted-mean corp-action-immune net active flow (₹cr/month); >0 = net bought",
                "weight": "holding market_value, renormalised to the placeable equity sleeve",
            },
            "centroid": ("holding-weighted mean of each equity name's ARM (y) and net active flow (x); "
                         "the portfolio's centre of mass in the quadrant"),
            "own_pctile": ("percentile rank (0-100) of the LATEST centroid within the entity's OWN monthly "
                           "history; 50 = a typical month for this entity, 100 = its most constructive/"
                           "aggressive ever. Null if < %d months." % MIN_PCTILE_OBS),
            "quadrant_labels": {"1": "Recommending + Bought", "2": "Recommending + Not bought",
                                "3": "Not recommending + Bought (FM ahead of street)", "4": "Neither"},
            "min_equity_cover": MIN_EQUITY_COVER, "max_points": MAX_POINTS,
            "months_back": months_back, "holdings_asof": hold_ym,
            "n_fund": n_fund, "n_amc": n_amc, "n_category": n_cat,
            "arm_available": bool(arm_by_vid),
            "note": ("Centroids are AGGREGATES (weighted means over many names) — no per-stock ARM is "
                     "shipped here. ARM is forward-filled from its last change-point on/before each "
                     "month-end. A month is dropped if < %.0f%% of the equity sleeve (by weight) can be "
                     "placed on BOTH axes." % (MIN_EQUITY_COVER * 100)),
        },
        "entities": entities,
    }
    os.makedirs(os.path.join(site_data_dir, "_rotation"), exist_ok=True)
    with open(os.path.join(site_data_dir, "_rotation", "centroids.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[rotation] centroids: {n_fund} funds + {n_amc} AMCs + {n_cat} categories "
        f"= {len(entities)} entities; ARM={'on' if arm_by_vid else 'OFF'}; holdings {hold_ym}")
    return out


if __name__ == "__main__":   # standalone: python -m vistas.rotation [site_data_dir]
    import sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _site = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_root, "output", "terminal_site", "data")
    build_centroids(_site, _root, progress=print)
