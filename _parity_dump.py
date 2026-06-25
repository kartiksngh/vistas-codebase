"""Parity harness (1/2): dump embedded data + Python analyze() bundles.

Writes _parity/dataset_<name>.json (the embedded {dates, series} an offline deck
carries) and _parity/py_<i>.json (analytics.analyze output) for a spread of
configs, then _parity_check.js re-runs the JS port on the SAME data and diffs
every field.

Two datasets:
  * main  — the real loaded frame (clean NSE data).
  * edge  — main with a single price set to 0.0, to exercise the zero-price /
            +inf-return convention (must be NaN/null on BOTH sides).

Python is called the SAME way the offline JS does: full pre-history frame sliced
only to <= end, window_start = start (isolates PORT correctness from the live
app's buffered-fetch path, which is checked separately at the bottom).
"""
import json, os
import numpy as np
import pandas as pd

from vistas import data, analytics

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_parity")
os.makedirs(OUT, exist_ok=True)

df = data.load()
EMBED = ["NIFTY 50", "NIFTY 500", "NIFTY BANK", "NIFTY IT", "NIFTY PHARMA",
         "NIFTY FMCG", "NIFTY AUTO", "NIFTY METAL", "NIFTY CONSUMER SERVICES",
         "NIFTY MIDCAP 100"]
EMBED = [c for c in EMBED if c in df.columns]
sub = df[EMBED]

# edge dataset: inject a single 0.0 price into NIFTY METAL (a data-error level that
# makes the NEXT return +inf in pandas) to prove the zero-price convention matches.
sub_edge = sub.copy()
z_date = sub_edge.index[(sub_edge.index >= pd.Timestamp("2015-06-01")) & sub_edge["NIFTY METAL"].notna()][0]
sub_edge.loc[z_date, "NIFTY METAL"] = 0.0

DATASETS = {"main": sub, "edge": sub_edge}
for name, frame in DATASETS.items():
    ds = {"dates": [d.strftime("%Y-%m-%d") for d in frame.index],
          "series": {c: [None if pd.isna(v) else float(v) for v in frame[c].to_numpy()] for c in frame.columns}}
    with open(os.path.join(OUT, f"dataset_{name}.json"), "w") as f:
        json.dump(ds, f)

DATA_END = sub.index[-1].strftime("%Y-%m-%d")

CONFIGS = [
    dict(tickers=["NIFTY IT", "NIFTY PHARMA", "NIFTY BANK"], benchmarks=["NIFTY 500"],
         start="2008-01-01", end=None, freq="daily", rolling_window="1Y", alpha_type="excess", rf_annual=0.0),
    dict(tickers=["NIFTY IT", "NIFTY PHARMA", "NIFTY BANK"], benchmarks=["NIFTY 500"],
         start="2008-01-01", end=None, freq="weekly", rolling_window="1Y", alpha_type="excess", rf_annual=0.0),
    dict(tickers=["NIFTY BANK", "NIFTY IT"], benchmarks=["NIFTY 50", "NIFTY 500"],
         start="2010-01-01", end=None, freq="daily", rolling_window="3Y", alpha_type="jensen", rf_annual=0.05),
    dict(tickers=["NIFTY CONSUMER SERVICES", "NIFTY MIDCAP 100"], benchmarks=["NIFTY 500"],
         start="2015-01-01", end=None, freq="daily", rolling_window="1Y", alpha_type="excess", rf_annual=0.0),
    dict(tickers=[], benchmarks=["NIFTY 50"],
         start="2006-01-01", end=None, freq="daily", rolling_window="1Y", alpha_type="excess", rf_annual=0.0),
    dict(tickers=["NIFTY IT", "NIFTY FMCG", "NIFTY AUTO", "NIFTY METAL"], benchmarks=["NIFTY 500"],
         start="2007-01-01", end=None, freq="weekly", rolling_window="3Y", alpha_type="jensen", rf_annual=0.0),
    dict(tickers=["NIFTY BANK"], benchmarks=["NIFTY 500"],
         start="2024-09-01", end=None, freq="daily", rolling_window="3M", alpha_type="excess", rf_annual=0.0),
    dict(tickers=["NIFTY IT", "NIFTY PHARMA"], benchmarks=["NIFTY 500"],
         start="2010-01-01", end="2018-12-31", freq="daily", rolling_window="1Y", alpha_type="excess", rf_annual=0.0),
    dict(tickers=["NIFTY 50", "NIFTY MIDCAP 100"], benchmarks=["NIFTY 500"],
         start="2000-01-01", end=None, freq="weekly", rolling_window="5Y", alpha_type="excess", rf_annual=0.0),
    # edge: window starting EXACTLY on a year-end (calendar-year dedup)
    dict(tickers=["NIFTY IT", "NIFTY BANK"], benchmarks=["NIFTY 500"],
         start="2014-12-31", end=None, freq="daily", rolling_window="1Y", alpha_type="excess", rf_annual=0.0),
    # edge: zero-price in NIFTY METAL (daily + weekly), excess + jensen
    dict(_dataset="edge", tickers=["NIFTY METAL", "NIFTY IT"], benchmarks=["NIFTY 500"],
         start="2014-06-01", end=None, freq="daily", rolling_window="1Y", alpha_type="excess", rf_annual=0.0),
    dict(_dataset="edge", tickers=["NIFTY METAL", "NIFTY IT"], benchmarks=["NIFTY 500"],
         start="2014-06-01", end=None, freq="weekly", rolling_window="3Y", alpha_type="jensen", rf_annual=0.0),
]

for i, cfg in enumerate(CONFIGS):
    frame = DATASETS[cfg.get("_dataset", "main")]
    end = cfg["end"] or DATA_END
    frame = frame[frame.index <= pd.Timestamp(end)]
    bundle = analytics.analyze(frame, cfg["tickers"], cfg["benchmarks"], window_start=cfg["start"],
                               freq=cfg["freq"], rolling_window=cfg["rolling_window"],
                               alpha_type=cfg["alpha_type"], rf_annual=cfg["rf_annual"])
    with open(os.path.join(OUT, f"py_{i}.json"), "w") as f:
        json.dump(bundle, f, allow_nan=False)

with open(os.path.join(OUT, "configs.json"), "w") as f:
    json.dump(CONFIGS, f)

print(f"[dump] {len(CONFIGS)} configs, {len(EMBED)} indices, {len(sub)} dates; zero-price at {z_date.date()} -> {OUT}")

# --- separate sanity: app's buffered-fetch path must match the full-frame path on
#     the WINDOW outputs (confirms BUFFER_DAYS >= rolling window, so offline == app).
from app import BUFFER_DAYS
cfg = CONFIGS[7]
end = cfg["end"]
buf = BUFFER_DAYS[cfg["rolling_window"]]
start_buf = (pd.Timestamp(cfg["start"]) - pd.Timedelta(days=buf)).strftime("%Y-%m-%d")
full = analytics.analyze(sub[sub.index <= pd.Timestamp(end)], cfg["tickers"], cfg["benchmarks"],
                         window_start=cfg["start"], freq=cfg["freq"], rolling_window=cfg["rolling_window"])
bufd = analytics.analyze(sub[(sub.index >= pd.Timestamp(start_buf)) & (sub.index <= pd.Timestamp(end))],
                         cfg["tickers"], cfg["benchmarks"], window_start=cfg["start"],
                         freq=cfg["freq"], rolling_window=cfg["rolling_window"])
def _maxdiff(a, b):
    m = 0.0
    if isinstance(a, dict):
        for k in a:
            m = max(m, _maxdiff(a[k], b.get(k)))
    elif isinstance(a, list):
        for x, y in zip(a, b or []):
            m = max(m, _maxdiff(x, y))
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        m = max(m, abs(a - b))
    return m
print(f"[buffer-equivalence] full-frame vs buffered window outputs, max abs diff = {_maxdiff(full, bufd):.3e}")

# ===== VALUATION parity =====================================================
# Synthetic valuation frame (deterministic seed): 4 series over ~600 business days,
# with a LATE-starting series (D) and an internal gap (A) to exercise percentile / z /
# bands / cross-section / spread / density on the ratio & yield kinds.
vidx = pd.bdate_range("2018-01-01", periods=600)
_rng = np.random.default_rng(42)


def _wander(base, drift, vol, n):
    return np.maximum(base + np.cumsum(_rng.normal(drift, vol, n)), 0.5)


vA, vB = _wander(20, 0.004, 0.10, 600), _wander(28, 0.010, 0.18, 600)
vC, vD = _wander(22, 0.003, 0.09, 600), _wander(15, -0.002, 0.12, 600)
vD[:220] = np.nan          # late inception
vA[300:312] = np.nan       # internal gap
val_frame = pd.DataFrame({"IDX A": vA, "IDX B": vB, "IDX C": vC, "IDX D": vD}, index=vidx)
val_ds = {"dates": [d.strftime("%Y-%m-%d") for d in val_frame.index],
          "series": {c: [None if pd.isna(v) else float(v) for v in val_frame[c].to_numpy()]
                     for c in val_frame.columns}}
with open(os.path.join(OUT, "dataset_val.json"), "w") as f:
    json.dump(val_ds, f)

VAL_CONFIGS = [
    dict(measure="PE", kind="ratio", tickers=["IDX A", "IDX B"], benchmarks=["IDX C"], start="2019-01-01", end=None, freq="daily"),
    dict(measure="PE", kind="ratio", tickers=["IDX A", "IDX B"], benchmarks=["IDX C"], start="2019-01-01", end=None, freq="weekly"),
    dict(measure="DY", kind="yield", tickers=["IDX A", "IDX B", "IDX D"], benchmarks=["IDX C"], start="2019-06-01", end="2021-12-31", freq="daily"),
    dict(measure="PB", kind="ratio", tickers=["IDX B"], benchmarks=[], start="2018-06-01", end=None, freq="daily"),
    dict(measure="PE", kind="ratio", tickers=["IDX D", "IDX A"], benchmarks=["IDX C"], start="2018-01-01", end=None, freq="daily"),
    dict(measure="PE", kind="ratio", tickers=["IDX A"], benchmarks=["IDX C"], start="2018-01-01", end=None, freq="weekly"),
]
VAL_END = val_frame.index[-1].strftime("%Y-%m-%d")
for i, cfg in enumerate(VAL_CONFIGS):
    end = cfg["end"] or VAL_END
    fr = val_frame[val_frame.index <= pd.Timestamp(end)]
    b = analytics.valuation_analyze(fr, cfg["tickers"], cfg["benchmarks"], measure=cfg["measure"],
                                    kind=cfg["kind"], window_start=cfg["start"], freq=cfg["freq"])
    with open(os.path.join(OUT, f"val_py_{i}.json"), "w") as f:
        json.dump(b, f, allow_nan=False)
with open(os.path.join(OUT, "val_configs.json"), "w") as f:
    json.dump(VAL_CONFIGS, f)
print(f"[dump] {len(VAL_CONFIGS)} valuation configs -> {OUT}")
