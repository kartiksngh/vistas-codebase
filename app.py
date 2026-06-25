"""
Vistas (passive) — Flask entrypoint.

Serves the Plotly front-end (static/) and a small JSON API:

  GET  /                  -> the terminal UI
  GET  /api/catalog       -> selectable index universe + defaults + data as-of
  POST /api/analyze       -> the full analytics bundle for a selection + window
  POST /api/refresh       -> pull last_date->today from NSE, write a new snapshot
  POST /api/add_index     -> fetch full history for a new index, add to snapshot
  GET  /api/health        -> liveness probe

Run locally (Windows-friendly):   python app.py        (serves http://127.0.0.1:8753)
Run for hosting (Linux/gunicorn):  gunicorn app:app     (Procfile / render.yaml)
"""
from __future__ import annotations

import os
import threading
import traceback

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory, send_file

from vistas import data, analytics, catalog, fetch, export, deck

# Calendar-day pre-window buffer per rolling window, so rolling series are seeded
# from the window start (requirement: alpha/beta available from 'from', not from
# 'from' + one rolling window). A touch more than the window length.
BUFFER_DAYS = {"1M": 45, "3M": 115, "6M": 205, "1Y": 400, "2Y": 770, "3Y": 1140, "5Y": 1880}

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

app = Flask(__name__, static_folder=None)

# Offline-deck auto-save bookkeeping (a fetch-all is polled by many threads, so the
# "save exactly once when it finishes" guard is a lock-protected test-and-set).
_AUTOSAVE = {"fetch_saved": True}
_AUTOSAVE_LOCK = threading.Lock()


def _autosave_deck(reason: str, timestamped: bool = True):
    """Write fresh offline decks after a data change (best-effort; never breaks the
    request if it fails). Saves the Terminal v2 deck (TR/PR + valuation) and keeps the
    legacy Passive v1 deck refreshed too."""
    out = {}
    for label, fn in (("terminal", deck.save_terminal_deck), ("passive", deck.save_deck)):
        try:
            r = fn(reason=reason, timestamped=timestamped)
            print(f"[vistas] {label} deck saved ({reason}): {r['file']} ({r.get('size_mb')} MB)")
            out[label] = r
        except Exception as e:
            traceback.print_exc()
            out[label] = {"ok": False, "error": str(e)}
    return out


# ----------------------------------------------------------------------------- static UI
@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/static/<path:fname>")
def static_files(fname):
    return send_from_directory(STATIC, fname)


# ----------------------------------------------------------------------------- API
@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "vistas-passive"})


@app.route("/api/catalog")
def api_catalog():
    try:
        cat = catalog.list_indices()
        cat["measures"] = data.measures_present()     # which of TR/PR/PE/PB/DY are loadable
        return jsonify(cat)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    try:
        body = request.get_json(force=True) or {}
        tickers = body.get("tickers") or []
        benchmarks = body.get("benchmarks") or []
        start = body.get("start")
        end = body.get("end")
        freq = body.get("freq", "daily")
        rolling_window = body.get("rolling_window", "1Y")
        alpha_type = body.get("alpha_type", "excess")
        rf_annual = float(body.get("rf_annual", 0.0) or 0.0)
        measure = body.get("measure", "TR")           # TR (default) or PR
        if data.kind(measure) != "level":
            return jsonify({"error": f"{measure} is not a price level — use /api/valuation."}), 400
        if measure not in data.measures_present():
            return jsonify({"error": f"No {measure} data loaded (run a refresh / backfill)."}), 400

        cols = list(dict.fromkeys(list(tickers) + list(benchmarks)))
        if not cols:
            return jsonify({"error": "Select at least one index."}), 400

        # Fetch with a pre-window buffer so rolling series are seeded from `start`.
        buf = BUFFER_DAYS.get(rolling_window, 400)
        start_buf = (pd.Timestamp(start) - pd.Timedelta(days=buf)).strftime("%Y-%m-%d") if start else None
        px = data.get_level_frame(cols, measure, start_buf, end)   # indices + stocks, merged
        if px.empty:
            return jsonify({"error": "No data in the selected window for those indices."}), 400

        bundle = analytics.analyze(
            px, tickers, benchmarks, window_start=start, freq=freq,
            rolling_window=rolling_window, alpha_type=alpha_type, rf_annual=rf_annual)
        return jsonify(bundle)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/valuation", methods=["POST"])
def api_valuation():
    """Valuation bundle (P/E, P/B or Div Yield) for a selection + window."""
    try:
        body = request.get_json(force=True) or {}
        tickers = body.get("tickers") or []
        benchmarks = body.get("benchmarks") or []
        start = body.get("start")
        end = body.get("end")
        freq = body.get("freq", "daily")
        measure = body.get("measure", "PE")
        kind = body.get("kind") or data.kind(measure)
        if measure not in data.measures_present():
            return jsonify({"error": f"No {measure} data loaded. Run a valuation backfill."}), 400
        cols = list(dict.fromkeys(list(tickers) + list(benchmarks)))
        if not cols:
            return jsonify({"error": "Select at least one index."}), 400
        pxv = data.get_series(cols, measure, None, end)   # full history to end; windowed inside
        if pxv.empty:
            return jsonify({"error": f"No {measure} data for those indices."}), 400
        bundle = analytics.valuation_analyze(pxv, tickers, benchmarks, measure=measure,
                                             kind=kind, window_start=start, freq=freq)
        return jsonify(bundle)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/fundamentals")
def api_fundamentals():
    """All cached Screener fundamentals as {SYM: bundle} (valuation + statements; the
    bulky price block is dropped — stocks already carry price). Mirrors the deck embed."""
    try:
        from vistas import deck as _deck
        from vistas import fundamentals as _fund
        out = _deck._fundamentals_dataset() or {}
        # attach the computed analytics block to each bundle (compute never raises;
        # the Fundamentals UI reads FUND_DATA[sym].analytics).
        out = {sym: _fund.attach(bundle) for sym, bundle in out.items()}
        return jsonify(out)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/save_terminal_deck", methods=["POST"])
def api_save_terminal_deck():
    """Build + write the Terminal Deck v2 (TR/PR + valuation) to ../output."""
    try:
        return jsonify(deck.save_terminal_deck(reason="manual"))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Pull fresh NSE data into a new snapshot, then reload the store.
    Degrades gracefully (returns ok:False) when the API is unreachable."""
    try:
        body = request.get_json(silent=True) or {}
        res = fetch.update(dry=bool(body.get("dry")))
        if res.get("ok") and res.get("updated"):
            data.reload()
            res["asof"] = data.asof()
            res["source_file"] = data.source_filename()
            res["deck"] = _autosave_deck("refresh")        # data changed -> new offline deck
        return jsonify(res)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/export_excel")
def api_export_excel():
    """Full multi-sheet NAV dump (all indices, all dates) as an .xlsx download —
    consolidated 'All NAV' sheet + one sheet per NSE category."""
    try:
        buf, fname = export.build_nav_workbook()
        return send_file(buf, as_attachment=True, download_name=fname,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/save_deck", methods=["POST"])
def api_save_deck():
    """Build + write a self-contained offline deck (current data) to ../output."""
    try:
        return jsonify(deck.save_deck(reason="manual"))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/fetch_all", methods=["POST"])
def api_fetch_all():
    """Start a background fetch of every not-yet-local catalog index (full history),
    so the user never clicks per-index. Returns immediately; poll /api/fetch_status."""
    try:
        res = fetch.fetch_all_start()
        if res.get("ok") and not res.get("nothing_to_fetch"):
            with _AUTOSAVE_LOCK:
                _AUTOSAVE["fetch_saved"] = False       # arm a one-shot save for when it finishes
        return jsonify(res)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/fetch_status")
def api_fetch_status():
    st = fetch.fetch_status()
    if st.get("finished") and not st.get("running"):
        try:
            data.reload()                 # pick up the newly-written snapshot
            # atomic test-and-set so concurrent polls save exactly one deck
            do_save = False
            with _AUTOSAVE_LOCK:
                if st.get("added") and not _AUTOSAVE["fetch_saved"]:
                    _AUTOSAVE["fetch_saved"] = True
                    do_save = True
            if do_save:
                _autosave_deck("fetch_all")   # one fresh deck per completed fetch-all
        except Exception:
            pass
    return jsonify(st)


@app.route("/api/fetch_cancel", methods=["POST"])
def api_fetch_cancel():
    return jsonify(fetch.fetch_all_cancel())


@app.route("/api/add_index", methods=["POST"])
def api_add_index():
    try:
        body = request.get_json(force=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "No index name supplied."}), 400
        res = fetch.add_index(name)
        if res.get("ok") and res.get("added"):
            data.reload()
            res["deck"] = _autosave_deck("add_index")     # new index -> new offline deck
        return jsonify(res)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


def main():
    # warm the cache + surface load errors at startup
    try:
        df = data.load()
        print(f"[vistas] loaded {df.shape[1]} indices x {df.shape[0]} days "
              f"(as of {data.asof()}) from {data.source_filename()}")
        # refresh only the _latest deck on boot (no timestamped spam every run);
        # timestamped copies are written on data updates + the manual Save button
        _autosave_deck("startup", timestamped=False)
    except Exception as e:
        print(f"[vistas] WARNING: data load failed at startup: {e}")
    host = os.environ.get("VISTAS_HOST", "127.0.0.1")
    port = int(os.environ.get("VISTAS_PORT", os.environ.get("PORT", 8753)))
    try:
        from waitress import serve
        print(f"[vistas] serving on http://{host}:{port}  (Ctrl+C to stop)")
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
