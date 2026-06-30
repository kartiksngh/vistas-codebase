# Market-cap filter (Large/Mid/Small/Micro + free ₹-threshold) — spec (QUEUED, 2026-06-30)

> KV-requested: a **point-in-time** market-cap filter across the flow / holdings / screen panels, so one can
> ask "as of Jan 1 2025, for the **Large** bucket (or stocks **> ₹X cr**), what was the money waterfall /
> AMC crowding / fund holdings / screen." Display-plane (render-only, no parity port) + one new data builder.

## Where the filter goes (alongside the existing sector/stock filters)
- **AMC crowding**, **money-flow waterfall**, **portfolio "what it owns"**, **screen tab**.
- **Screen tab** also gains a **SECTOR** filter (today it has only "holdings of AMC") → add **sector** + **market-cap**.

## The classification — POINT-IN-TIME, mcap-RANK (the key design call)
KV's described method = index membership (NIFTY 100→Large, Midcap 150→Mid, Smallcap 250→Small; the three = NIFTY 500; not-in-500 AND mcap < lowest-of-top-500 → Micro).

**Feasibility finding (checked the data):**
- **Historical membership is NOT available** — `data/benchmarks/` is only the CURRENT constituent snapshot (asof 2026-06-25); `_manifest.json` mcap is a single 31Dec2025 AMFI snapshot. So membership can't classify a past date.
- **A point-in-time mcap series IS derivable:** `vistas/shares.py` holds REAL NSE issued shares (`issuedSize`, changes only on corp-actions); green daily closes run 2000→2026. ⇒ **`mcap(t) = green_close(t) × issuedSize`**.
- **NSE's buckets ARE mcap-rank by construction** (NIFTY 100 = top-100 by mcap, Midcap 150 = ranks 101–250, Smallcap 250 = 251–500). So **ranking all eligible NSE stocks by `mcap(t)` each month-end reproduces KV's exact Large/Mid/Small/Micro — point-in-time, historically**, and Micro = rank > 500 (below the top-500 line = KV's floor guard, automatic). Cross-check the current period against the live constituent snapshot.
- This same `mcap(t)` **powers the free threshold**: filter to stocks with `mcap(t) > ₹X cr` as-of the selected date.

**Honest caveat (flag inline):** `issuedSize` is current, applied backward → `mcap(t)` ignores past share-count changes (buybacks / QIPs / dilutions). Robust for RANK bucketing (rank tolerant to modest drift); APPROXIMATE for the absolute ₹-threshold. A precise historical mcap needs historical share counts (not available).

## Build
1. **`vistas/marketcap.py` (new):** build the monthly panel `mcap(t) = close(t) × issuedSize` for all NSE stocks (reuse `shares.load()` + the price panel); per (vst_id, month) emit `{bucket ∈ Large/Mid/Small/Micro (rank-cut 100/250/500), mcap_cr}`. Embed compactly in the deck (e.g. `{vst_id:{month:[bucketCode, mcap_cr]}}`, or per-month rank-cut lines).
2. **`static/vistas.js`:** a **reusable cap-filter control** = Large/Mid/Small/Micro multiselect **+** a `mcap > ₹X cr` numeric, BOTH evaluated **as-of the panel's selected date** (ties to the OWNERSHIP_FLOW_UPGRADE date-pickers). Wire into crowding, waterfall, portfolio, screen. Screen also gets a sector dropdown.
3. Filter semantics = point-in-time: a stock's bucket/mcap is read AS-OF the panel date, so "Jan 1 2025 · Large" shows what was Large *then*.
4. Render-only (no analytics parity port). Validate: `_deck_runtime_test.js` PASS, 0 errors; never set a Plotly trace key to `undefined`.

## Notes / reconcile
- Existing large/mid/small usage spans `amc_firm` / `amc_replay` / `funds_*` (a **turnover-rank** size proxy from the FM side). The new **mcap-rank** bucket is the cleaner truth; the FM/skill side can later adopt it (it also fixes the "size leg = turnover-rank proxy" caveat in the skill-engine factor library).
- Pairs with `OWNERSHIP_FLOW_UPGRADE.md` (the cap filter is evaluated as-of the same date-picker) and benefits the benchmark fix (size-category benchmarks become point-in-time-consistent).
