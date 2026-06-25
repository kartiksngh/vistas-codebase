"""Vistas (passive) — a self-contained NSE-index analytics terminal.

A small Flask app + Plotly front-end that loads the project's NSE total-return
index history (and can fetch fresh data on demand) and renders a Bloomberg-style
GP/COMP/risk/alpha workbench over any chosen indices, benchmarks and date window.

Standalone & relocatable: it does NOT import the research `strategy/` package.
The analytics here re-implement the project's EXACT formula conventions
(log/simple returns, W-FRI weekly, 252-trading-day annualization, 365-day CAGR,
the >=25%-stale-day load filter) — see `vistas/analytics.py` for per-metric
provenance back to `strategy/fft_strategy_v1.py`.
"""

__version__ = "0.1.0"
