"""
arm.py — full-history LSEG StarMine ARM ingestion: raw parquet dump  ->  compact India cache.

WHY THIS EXISTS
---------------
The original `starmine.py` read a small CSV (472 ISINs, 2024-07 → 2026-04). KV now supplies the FULL
StarMine Asia-Pacific ARM history as a large multi-part parquet dump (~1,456 files, ~145M rows, 1998 →
current), refreshed WEEKLY. This module turns that big, growing, mostly-non-India dump into a small,
fast, India-only **compiled cache** that the deck build reads — and is structured so a weekly refresh is
just "drop a new parquet in arm_repo/ and recompile" (no code change).

FOLDER LAYOUT (relocatable via $VISTAS_ARM_DIR; default <project>/arm_repo, git-ignored — licensed IP)
    arm_repo/
      historical/   the base dump (e.g. "ARM Full Historical Dump on June 25, 2026/*.parquet")
      weekly/       each weekly fetch drops arm_YYYY-MM-DD.parquet here
      compiled/     arm_india.parquet (+ _meta.json) — the cache the build reads (THIS module writes it)
      fetch/        KV's weekly fetch script

THE MAPPING (the crucial part — verified 2026-06-25)
----------------------------------------------------
The dump is Asia-Pacific/global, keyed by ISIN. We keep ONLY India equity ISINs (INE/IN9) carrying an
ARM mnemonic, and resolve each to our permanent `vst_id` via `idmap` (lineage-aware, so old/renamed
ISINs land on the live symbol). Verified coverage: 1,924 of 2,196 India ISINs map (87.6%); the 272
unmatched are overwhelmingly dead/delisted pre-2010 names outside our tradeable universe.

The compiled cache is FLAT long-form (ISIN, MNEMONIC, StartDate, Value_) over FULL history (kept for
research/backtests). The display card (starmine.card_from_raw) trims the plotted series — see starmine.py.
"""
from __future__ import annotations

import os
import glob
import json

# vistas/ is the package; project root is its parent.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
ARM_DIR = os.environ.get("VISTAS_ARM_DIR", os.path.join(_ROOT, "arm_repo"))
COMPILED = os.path.join(ARM_DIR, "compiled", "arm_india.parquet")
COMPILED_META = os.path.join(ARM_DIR, "compiled", "arm_india_meta.json")

# the ARM mnemonics we keep (headline + global/country/bucket variants + the four components).
KEEP_MNEM = {
    "ARM_100_REG", "ARM_100_GLOBAL", "ARM_100_CTRY", "ARM_5_REG", "ARM_1_REG", "ARM_EX_REC",
    "ARM_PREF_EARN_COMP_100", "ARM_SEC_EARN_COMP_100", "ARM_REVENUE_COMP_100", "ARM_REC_COMP_100",
}
_COLS = ["ISIN", "CMPNAME", "MNEMONIC", "StartDate", "Value_"]


# --------------------------------------------------------------------------- discovery
def source_parquets(arm_dir: str | None = None) -> list:
    """Every raw source parquet under arm_dir (recursive) EXCEPT the compiled cache itself.
    Recursive glob = a new weekly file dropped anywhere under arm_repo/ is auto-discovered (no code change)."""
    d = arm_dir or ARM_DIR
    comp = os.path.abspath(os.path.join(d, "compiled"))
    return sorted(f for f in glob.glob(os.path.join(d, "**", "*.parquet"), recursive=True)
                  if not os.path.abspath(f).startswith(comp))


def has_compiled() -> bool:
    return os.path.exists(COMPILED)


# --------------------------------------------------------------------------- compile (weekly / on-change)
def compile_india(arm_dir: str | None = None, log=print) -> dict | None:
    """Scan every source parquet, keep only India (INE/IN9) ARM rows for the KEEP_MNEM mnemonics,
    de-duplicate by (ISIN, MNEMONIC, StartDate) keeping the last value seen (so weekly re-fetch overlaps
    are harmless), and write the compact compiled cache + meta. Returns the meta dict (or None if no source)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyarrow.compute as pc
    import pandas as pd

    fs = source_parquets(arm_dir)
    if not fs:
        log(f"[arm] no source parquet found under {arm_dir or ARM_DIR} — skipping compile.")
        return None
    need = pa.array(sorted(KEEP_MNEM))
    parts, names, scanned = [], {}, 0
    for f in fs:
        t = pq.read_table(f, columns=_COLS)
        scanned += t.num_rows
        isin = t.column("ISIN")
        t = t.filter(pc.or_(pc.starts_with(isin, "INE"), pc.starts_with(isin, "IN9")))
        if t.num_rows:
            t = t.filter(pc.is_in(t.column("MNEMONIC"), value_set=need))
        if not t.num_rows:
            continue
        # capture a display name per ISIN (first non-null wins)
        for a, nm in zip(t.column("ISIN").to_pylist(), t.column("CMPNAME").to_pylist()):
            if a and nm and a not in names:
                names[a] = nm
        # normalise StartDate -> 'YYYY-MM-DD' string column, keep the 4 cache columns
        sd = t.column("StartDate")
        try:
            sd_str = pc.strftime(sd, format="%Y-%m-%d")
        except Exception:
            sd_str = pa.array([(s.date().isoformat() if hasattr(s, "date") else str(s)[:10])
                               for s in sd.to_pylist()])
        parts.append(pa.table({"ISIN": t.column("ISIN"), "MNEMONIC": t.column("MNEMONIC"),
                               "StartDate": sd_str, "Value_": t.column("Value_").cast(pa.float64())}))
    if not parts:
        log("[arm] no India ARM rows in any source parquet.")
        return None
    df = pa.concat_tables(parts).to_pandas()
    df = df.dropna(subset=["StartDate", "Value_"])
    before = len(df)
    df = df.drop_duplicates(subset=["ISIN", "MNEMONIC", "StartDate"], keep="last")
    os.makedirs(os.path.dirname(COMPILED), exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), COMPILED, compression="zstd")
    meta = {
        "n_rows": int(len(df)), "n_isins": int(df["ISIN"].nunique()),
        "date_min": (df["StartDate"].min() if len(df) else None),
        "date_max": (df["StartDate"].max() if len(df) else None),
        "sources": len(fs), "scanned_rows": int(scanned), "deduped": int(before - len(df)),
        "names": names,
    }
    with open(COMPILED_META, "w", encoding="utf-8") as g:
        json.dump(meta, g)
    log(f"[arm] compiled {meta['n_rows']:,} India rows | {meta['n_isins']} ISINs | "
        f"{meta['date_min']} to {meta['date_max']} | from {len(fs)} files (deduped {meta['deduped']:,}).")
    return meta


# --------------------------------------------------------------------------- load (deck build)
def load_raw(path: str | None = None) -> dict:
    """Read the compiled India cache -> {ISIN: {"name": str, "mnem": {MNEMONIC: [(date, val), ...] sorted}}}
    — the exact shape starmine.card_from_raw expects. Returns {} if the cache is missing.

    This auto-compiles once if the cache is absent but source parquet exist (first build after a fresh
    dump). Compile is also exposed standalone (compile_india) for the weekly pipeline step."""
    p = path or COMPILED
    if not os.path.exists(p):
        if source_parquets():
            compile_india()
        if not os.path.exists(p):
            return {}
    import pyarrow.parquet as pq
    names = {}
    if os.path.exists(COMPILED_META):
        try:
            with open(COMPILED_META, encoding="utf-8") as f:
                names = (json.load(f) or {}).get("names", {})
        except Exception:
            names = {}
    tab = pq.read_table(p, columns=["ISIN", "MNEMONIC", "StartDate", "Value_"])
    I = tab.column("ISIN").to_pylist(); M = tab.column("MNEMONIC").to_pylist()
    S = tab.column("StartDate").to_pylist(); V = tab.column("Value_").to_pylist()
    out: dict = {}
    for a, m, s, v in zip(I, M, S, V):
        if not a or v is None or not s:
            continue
        rec = out.setdefault(a, {"name": names.get(a, ""), "mnem": {}})
        rec["mnem"].setdefault(m, []).append((s, float(v)))
    for rec in out.values():
        for m, ser in rec["mnem"].items():
            ser.sort(key=lambda t: t[0])
            rec["mnem"][m] = ser
    return out


def compiled_meta() -> dict:
    if os.path.exists(COMPILED_META):
        try:
            with open(COMPILED_META, encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}
    return {}


if __name__ == "__main__":   # one-shot recompile: python -m vistas.arm
    compile_india()
