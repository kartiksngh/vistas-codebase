"""
_link_unmapped_holdings.py — extend the Co_Code->vst_id holdings identity map by LINKING unmapped
equity holdings to vst_ids THAT ALREADY EXIST in the price master. No new identities are minted.

WHY: many "unmapped" fund holdings (vst_id NULL) are recent IPOs / demerger entities that DID make it
into our NSE price master (bhavcopy) — only the Capitaline Co_Code->vst_id LINK was missing. The holdings
spine is Co_Code (vistas/_build_history_store.py), resolved through data/funds/_history_identity_map.json.

TWO link routes, both to EXISTING vst_ids (ISIN-join = the gold-standard resolver; identifier-resolution skill):
  A) ISIN-LINK    : the co_code's modal valid India-equity ISIN resolves to an existing vst_id (idmap.isin_index).
  B) PARTLYPAID   : a "... Partly Paidup" tranche -> its PARENT's vst_id, by EXACT normalized-name match
                    to the master (the partly-paid IN9... line is the same economic entity as the INE... parent).

SKIPPED (left unmapped, by design): debt/GSEC/TBILL/REPO, foreign (US...) equities, REITs/InvITs, and any
name not already in the master (no speculative mint). Collisions are detected and refused, never guessed.

Run:  python _link_unmapped_holdings.py            # DRY RUN (reports, writes nothing)
      python _link_unmapped_holdings.py --apply    # backup + update map + re-map parquet in place
"""
from __future__ import annotations
import os, json, sys, shutil, argparse
import pandas as pd
from collections import Counter
from vistas import idmap

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
MAP_JSON = os.path.join(ROOT, "data", "funds", "_history_identity_map.json")
PARQUET = os.path.join(ROOT, "data", "funds", "history", "holdings_history.parquet")


def _norm_name(s: str) -> str:
    """Lower, drop punctuation/suffixes, collapse spaces — for parent matching of partly-paid lines."""
    s = str(s or "").lower()
    for junk in ["partly paidup", "partly paid up", "partly paid", "(partly paid)"]:
        s = s.replace(junk, " ")
    out = []
    for tok in s.replace(".", " ").replace(",", " ").replace("&", " and ").split():
        if tok in ("ltd", "limited", "pvt", "private", "the", "co", "company", "of", "india", "ind"):
            continue
        out.append(tok)
    return " ".join(out).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the map + re-map the parquet (default: dry run)")
    args = ap.parse_args()

    master = json.load(open(MAP_JSON, encoding="utf-8"))["master"]   # {co_code: {vst_id,name,nse_symbol,conf}}
    print(f"existing co_code->vst_id master entries: {len(master):,}")

    isin_idx = idmap.isin_index()        # ISIN -> {symbol,name,vst_id,symbols}
    sym_idx = idmap.symbol_index()       # symbol -> {...}
    # name -> vst_id for parent matching of partly-paid lines (from the security master, via symbol_index)
    name2vid = {}
    for sym, rec in sym_idx.items():
        nm = _norm_name(rec.get("name") or sym)
        if nm:
            name2vid.setdefault(nm, (rec["vst_id"], sym, rec.get("name")))

    df = pd.read_parquet(PARQUET)
    df["co_code"] = df["co_code"].astype(str)
    end = df["ym"].max()
    un = df[df["vst_id"].isna()].copy()
    un["isin_norm"] = un["reported_isin"].map(idmap.normalize_isin)

    # precompute per-co_code latest-month market value once (vectorized; avoids a per-co_code full scan)
    mv_latest_by_cc = (df[df["ym"] == end].groupby("co_code")["market_value"].sum().to_dict())

    # ---- per-unmapped-co_code: dominant valid india-equity ISIN + name ----
    rows_by_cc = {}
    for cc, g in un.groupby("co_code"):
        isins = [x for x in g["isin_norm"] if idmap.is_valid_isin(x) and idmap.is_india_equity_isin(x)]
        modal = Counter(isins).most_common(1)[0][0] if isins else None
        rows_by_cc[cc] = {"name": g["company_name"].iloc[0], "isin": modal,
                          "mv_latest": mv_latest_by_cc.get(cc, 0.0),
                          "mv_all": g["market_value"].sum()}

    def _toks(s):
        return set(_norm_name(s).split())

    # VERIFIED rename whitelist: ISIN-link where the holding's vendor name LAGS a legit rename, confirmed by
    # the ISIN being agreed by BOTH the holding and our live (bhavcopy) master. cc 22655 = MALCO Energy Ltd
    # (a Vedanta group shell) reported under its old name but carrying ISIN INE704J01044, which the live
    # master maps to Vedanta Oil and Gas Ltd (VOGL) — a Vedanta-demerger rename, ₹2,196 cr across 29 funds.
    RENAME_WHITELIST = {"22655"}

    setA, setB, skipped, excluded_review = {}, {}, [], []
    for cc, info in rows_by_cc.items():
        isin, name = info["isin"], info["name"]
        # route A: ISIN -> existing vst_id, AUTO-ACCEPT only if the holding name shares a token with the
        # master name (or it's a verified rename). This gate caught 3 contaminated non-security co_codes
        # (Net CA / Clearing Corp / Derivatives) whose junk id-bags held a stray check-digit-valid equity ISIN.
        if isin and isin in isin_idx:
            hit = isin_idx[isin]
            name_ok = bool(_toks(name) & _toks(hit.get("name"))) or str(cc) in RENAME_WHITELIST
            if name_ok:
                setA[cc] = {"vst_id": hit["vst_id"], "name": hit.get("name"), "nse_symbol": hit.get("symbol"),
                            "conf": "isin-link", "via": isin, "src_name": name}
                continue
            excluded_review.append((cc, hit.get("symbol"), name, hit.get("name")))   # name-mismatch -> review, NOT applied
            continue
        # route B: partly-paid -> parent by exact normalized name
        if "partly paid" in str(name).lower():
            pn = _norm_name(name)
            if pn in name2vid:
                vid, sym, mname = name2vid[pn]
                setB[cc] = {"vst_id": vid, "name": mname, "nse_symbol": sym,
                            "conf": "partly-paid-link", "via": "name:" + pn, "src_name": name}
                continue
        skipped.append((cc, name, isin))

    # ---- safety: refuse if a co_code we add already exists in the master (we only ADD unmapped ones). ----
    collisions = []
    for cc in list(setA) + list(setB):
        if cc in master:
            collisions.append(("already-in-master", cc, master[cc].get("vst_id")))

    add = {**setA, **setB}
    # coverage math (value-weighted), equity sleeve
    eq = df[df["investment_type"].astype(str).str.contains("quity", na=False)]
    eq_val = eq["market_value"].sum()
    eq_res_before = eq.loc[eq["vst_id"].notna(), "market_value"].sum()
    recov_all = sum(rows_by_cc[cc]["mv_all"] for cc in add)
    recov_latest = sum(rows_by_cc[cc]["mv_latest"] for cc in add)
    latest_unmapped_val = df[(df["ym"] == end) & (df["vst_id"].isna())]["market_value"].sum()

    print(f"\n=== LINK PLAN (no mint) ===")
    print(f"  route A ISIN-link        : {len(setA):>4} co_codes")
    print(f"  route B partly-paid->parent: {len(setB):>4} co_codes")
    print(f"  skipped (debt/foreign/REIT/defunct/not-in-master): {len(skipped):>4} co_codes")
    print(f"  collisions (refuse if >0): {len(collisions)}")
    print(f"\n  value recovered  : all-history Rs {recov_all:,.0f} cr | latest-month Rs {recov_latest:,.0f} cr")
    print(f"  latest unmapped value before: Rs {latest_unmapped_val:,.0f} cr  -> after: Rs {latest_unmapped_val - recov_latest:,.0f} cr")
    print(f"  equity value resolved: {100*eq_res_before/eq_val:.2f}% -> {100*(eq_res_before+recov_all)/eq_val:.2f}%")

    print(f"\n  --- top 25 links by latest market value ---")
    top = sorted(add.items(), key=lambda kv: -rows_by_cc[kv[0]]["mv_latest"])[:25]
    for cc, m in top:
        print(f"    {m['nse_symbol']:12} {m['vst_id']:10} <- cc {cc:>8}  Rs{rows_by_cc[cc]['mv_latest']:8.0f}cr  [{m['conf']}]  {str(m['src_name'])[:34]}")
    print(f"\n  --- ALL route-B partly-paid links ({len(setB)}) ---")
    for cc, m in setB.items():
        print(f"    {m['nse_symbol']:12} {m['vst_id']:10} <- cc {cc:>8}  holding='{str(m['src_name'])[:36]}'  parent='{str(m['name'])[:30]}'")
    print(f"\n  --- EXCLUDED name-mismatches ({len(excluded_review)}) — ISIN hit but holding-name disagrees (NOT applied) ---")
    for cc, sym, hn, mn in excluded_review:
        print(f"    cc {cc:>8}  holding='{str(hn)[:38]}'  ->  would-be {sym} ('{str(mn)[:28]}')  [EXCLUDED]")
    if collisions:
        print("\n  !! COLLISIONS (will refuse to apply):"); [print("    ", c) for c in collisions[:20]]

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
        return
    if collisions:
        print("\nREFUSING to apply: collisions present."); sys.exit(1)

    # ---- APPLY: backup, update master, re-map parquet in place ----
    shutil.copy2(MAP_JSON, MAP_JSON + ".bak")
    full = json.load(open(MAP_JSON, encoding="utf-8"))
    for cc, m in add.items():
        full["master"][str(cc)] = {"vst_id": m["vst_id"], "name": m["name"],
                                   "nse_symbol": m["nse_symbol"], "conf": m["conf"]}
    json.dump(full, open(MAP_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"\nupdated {MAP_JSON} (+{len(add)} entries; backup at {MAP_JSON}.bak)")

    # re-map ONLY the newly-resolvable null rows; never touch already-mapped rows
    addmap = {str(cc): m for cc, m in add.items()}
    mask = df["vst_id"].isna() & df["co_code"].astype(str).isin(addmap)
    n_before = df["vst_id"].notna().sum()
    df.loc[mask, "vst_id"] = df.loc[mask, "co_code"].astype(str).map(lambda c: addmap[c]["vst_id"])
    df.loc[mask, "id_conf"] = df.loc[mask, "co_code"].astype(str).map(lambda c: addmap[c]["conf"])
    df.loc[mask, "vid_name"] = df.loc[mask, "co_code"].astype(str).map(lambda c: addmap[c]["name"])
    df.loc[mask, "nse_symbol"] = df.loc[mask, "co_code"].astype(str).map(lambda c: addmap[c]["nse_symbol"])
    df.to_parquet(PARQUET, index=False)
    print(f"re-mapped parquet: filled {int(mask.sum()):,} rows; vst_id non-null {n_before:,} -> {df['vst_id'].notna().sum():,}")


if __name__ == "__main__":
    main()
