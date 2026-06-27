"""
amc_site.py — render the Digital AMC dashboard: a TRADING-FLOOR + KANBAN hybrid.

Reads:
  output/_amc/org.json      (the workflow result: analysts -> FMs -> CIO + comm log)
  output/_amc/context.json  (real desk stats: sector ARM, FM median-IR + exemplars, division)
Writes:
  output/_amc/site/index.html   (self-contained, styled, interactive — no external assets)

Two linked zones on one page:
  • THE FLOOR (trading energy) — CIO command bar + 3-lens pulse, then division rows of FM desk
    tiles (stance dot + IR gauge) and a research-floor strip of analyst tiles (ARM gauge). Click any
    desk → modal with full detail.
  • DEAL FLOW (Kanban structure) — three columns, Analyst pitch → FM decision → CIO ruling. Hover a
    card to highlight the SAME stock across all columns; click → opens that desk; search/focus to
    trace one idea through the firm.

Display-plane only — touches no analytics.py formula. Run:  python -m vistas.amc_site
"""
from __future__ import annotations
import json, html, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AMC  = ROOT / "output" / "_amc"
SITE = AMC / "site"
BOOKS = ROOT / "amc_book"          # the per-AMC / per-scheme paper-trading books (replay artifacts)
SITE.mkdir(parents=True, exist_ok=True)

DIVISION_ORDER = ["Core Equity", "Strategy", "Thematic", "Hybrid & Asset Allocation", "Quant"]

def esc(x):
    return html.escape(str(x if x is not None else ""))

def trunc(s, n):
    s = str(s if s is not None else "")
    return esc(s if len(s) <= n else s[:n - 1].rstrip() + "…")

# ── stance / score visual encoders ───────────────────────────────────────────────────────────
STANCE_COLOR = {
    "bullish": "#16a34a", "risk-on": "#16a34a", "constructive": "#65a30d",
    "neutral": "#94a3b8", "balanced": "#94a3b8",
    "cautious": "#d97706", "defensive": "#d97706", "bearish": "#dc2626",
}
def stance_color(s):
    return STANCE_COLOR.get(str(s).lower(), "#94a3b8")

def stance_chip(s):
    c = stance_color(s)
    return f'<span class="chip" style="background:{c}1a;color:{c};border:1px solid {c}55">{esc(s)}</span>'

def stance_dot(s):
    c = stance_color(s)
    return f'<span class="dot" style="background:{c};box-shadow:0 0 7px {c}88" title="{esc(s)}"></span><span class="dotlbl" style="color:{c}">{esc(s)}</span>'

def arm_color(v):
    try:
        v = float(v)
    except Exception:
        return "#94a3b8"
    if v >= 50:
        t = (v - 50) / 50; return f"rgb({int(148-148*t+22*t)},{int(163+9*t)},{int(184-184*t+74*t)})"
    t = v / 50; return f"rgb({int(220-72*t)},{int(38+125*t)},{int(38+146*t)})"

def gauge(frac, color, label=""):
    frac = max(0.0, min(1.0, frac))
    return (f'<span class="gauge"><span class="gfill" style="width:{frac*100:.0f}%;background:{color}"></span></span>'
            + (f'<b class="gval" style="color:{color}">{label}</b>' if label != "" else ""))

def arm_gauge(v):
    try:
        f = float(v) / 100.0
    except Exception:
        return '<span class="muted">—</span>'
    return gauge(f, arm_color(v), esc(round(float(v))))

def ir_gauge(ir):
    try:
        ir = float(ir)
    except Exception:
        return '<span class="muted">n/a</span>'
    frac = (ir + 0.5) / 1.5          # map IR ∈ [-0.5, +1.0] → [0,1], 0 sits at 33%
    color = "#16a34a" if ir > 0.05 else ("#dc2626" if ir < -0.05 else "#94a3b8")
    return gauge(frac, color, f"{ir:+.2f}")

CONV = {"high": "●●●", "medium": "●●○", "low": "●○○"}
def conv_dots(c):
    return f'<span class="conv" title="conviction: {esc(c)}">{CONV.get(str(c).lower(),"")}</span>'

def li(items):
    return "".join(f"<li>{esc(x)}</li>" for x in (items or []))

# ── full-detail blocks (rendered hidden; shown in the modal on click) ────────────────────────
def analyst_detail(a, ctx_a):
    cov = (ctx_a or {}).get("coverage_n"); rec = (ctx_a or {}).get("recommending_n")
    arm_ew = (ctx_a or {}).get("arm_ew"); arm_ff = (ctx_a or {}).get("arm_ff")
    pitches = ""
    for p in a.get("pitches", []):
        to = ", ".join(p.get("to", []) or [])
        pitches += (
            f'<div class="pitch" data-stock="{esc(p.get("stock"))}"><div class="pitch-h">'
            f'<span class="act act-{esc(p.get("action"))}">{esc(p.get("action"))}</span> '
            f'<b>{esc(p.get("stock"))}</b> {conv_dots(p.get("conviction"))}'
            f'<span class="hz">{esc(p.get("horizon"))}</span><span class="to">→ {esc(to)}</span></div>'
            f'<div class="th">{esc(p.get("thesis"))}</div>'
            f'<div class="ev">{esc(p.get("evidence"))}</div></div>')
    return (
        f'<div class="md-h"><div class="md-who">🔬 {esc(a.get("name"))} <span class="role">Sector Analyst</span></div>{stance_chip(a.get("stance"))}</div>'
        f'<div class="md-metrics">Coverage <b>{esc(cov)}</b> stocks · {esc(rec)} recommending · Sector ARM '
        f'<b style="color:{arm_color(arm_ew)}">{esc(arm_ew)}</b> equal-wt / <b style="color:{arm_color(arm_ff)}">{esc(arm_ff)}</b> float-wt</div>'
        f'<div class="headline">{esc(a.get("headline"))}</div>'
        f'<div class="sub">Working on</div><ul class="wo">{li(a.get("working_on"))}</ul>'
        f'<div class="sub">Pitches</div><div class="pitches">{pitches or "<div class=muted>—</div>"}</div>'
        f'<div class="sub">Risks</div><ul class="risks">{li(a.get("risks"))}</ul>'
        f'<button class="trace" onclick="focusDesk(\'d-analyst-{esc(a.get("key"))}\')">▸ Trace this desk in the deal-flow</button>')

def fm_detail(f, ctx_f):
    c = ctx_f or {}
    med_ir = c.get("median_ir"); n = c.get("n_funds"); bench = c.get("benchmark"); div = c.get("division")
    ex = (c.get("exemplars") or [{}])[0]
    taken = "".join(
        f'<div class="tk" data-stock="{esc(t.get("stock"))}"><span class="dec dec-take">take</span> <b>{esc(t.get("stock"))}</b> '
        f'<span class="sz">{esc(t.get("size") or "")}</span> <span class="frm">from {esc(t.get("from"))}</span>'
        f'<div class="why">{esc(t.get("why"))}</div></div>' for t in f.get("pitches_taken", []))
    decl = "".join(
        f'<div class="tk decl" data-stock="{esc(d.get("stock"))}"><span class="dec dec-pass">pass</span> <b>{esc(d.get("stock"))}</b>'
        f'<div class="why">{esc(d.get("why_not"))}</div></div>' for d in f.get("pitches_declined", []))
    esc_rows = "".join(
        f'<div class="esc-row"><span class="rsn">{esc(e.get("reason"))}</span> {esc(e.get("topic"))} — <i>{esc(e.get("ask"))}</i></div>'
        for e in f.get("escalations", []))
    tilt = " · ".join(esc(t) for t in (f.get("book_tilt") or [])) or "—"
    exline = (f'exemplar <b>{esc(ex.get("scheme"))}</b>: IR {esc(ex.get("info_ratio"))}, IC {esc(ex.get("ic_mean"))} '
              f'(t {esc(ex.get("ic_t"))}), TE {esc(ex.get("tracking_error"))}% — <i>{esc(ex.get("verdict"))}</i>') if ex.get("scheme") else "no qualifying exemplar"
    return (
        f'<div class="md-h"><div class="md-who">📈 {esc(f.get("name"))} <span class="role">{esc(div)} · Fund Manager</span></div>{stance_chip(f.get("stance"))}</div>'
        f'<div class="md-metrics"><b>{esc(n)}</b> peer funds · category median IR <b>{esc(med_ir)}</b> · bench {esc(bench)}<br>'
        f'<span class="muted">{exline}</span></div>'
        f'<div class="headline">{esc(f.get("positioning"))}</div>'
        f'<div class="sub">Book tilt</div><div class="tilt">{tilt}</div>'
        f'<div class="sub">Pitches taken / passed</div><div class="taken">{taken}{decl}{"<div class=muted>—</div>" if not (taken or decl) else ""}</div>'
        + (f'<div class="sub">Escalated to CIO</div>{esc_rows}' if esc_rows else "")
        + f'<div class="sub">Investor experience</div><div class="exp">{esc(f.get("experience_note"))}</div>'
        + f'<button class="trace" onclick="focusDesk(\'d-fm-{esc(f.get("key"))}\')">▸ Trace this desk in the deal-flow</button>')

def cio_detail(cio):
    mp = cio.get("market_pulse", {}) or {}
    rulings = "".join(f'<li><b>{esc(r.get("on"))}:</b> {esc(r.get("ruling"))} <span class="muted">— {esc(r.get("rationale"))}</span></li>'
                      for r in cio.get("rulings", []))
    alloc = "".join(f'<tr><td>{esc(x.get("mandate"))}</td><td>{stance_chip(x.get("stance"))}</td><td>{esc(x.get("note"))}</td></tr>'
                    for x in cio.get("allocation", []))
    risks = "".join(f'<span class="rflag">⚠ {esc(x)}</span>' for x in cio.get("risk_flags", []))
    return (
        f'<div class="md-h"><div class="md-who">🏛️ Chief Investment Officer <span class="role">House view</span></div></div>'
        f'<div class="headline">{esc(cio.get("house_view"))}</div>'
        f'<div class="sub">3-lens market pulse <span class="muted">— the gaps between the lenses are the signal</span></div>'
        f'<div class="pulse">'
        f'<div class="lens"><div class="ln">Street · analysts / ARM</div>{esc(mp.get("street"))}</div>'
        f'<div class="lens"><div class="ln">Smart money · fund flows</div>{esc(mp.get("smart_money"))}</div>'
        f'<div class="lens"><div class="ln">Reward · price / quadrant</div>{esc(mp.get("reward"))}</div>'
        f'<div class="lens gap"><div class="ln">★ The gaps</div>{esc(mp.get("gaps"))}</div></div>'
        f'<div class="sub">Allocation across mandates</div><table>{alloc or "<tr><td class=muted>—</td></tr>"}</table>'
        f'<div class="sub">Rulings</div><ul>{rulings or "<li class=muted>none</li>"}</ul>'
        f'<div class="sub">Risk flags</div><div>{risks or "<span class=muted>none</span>"}</div>'
        f'<div class="sub">Summary</div><div class="exp">{esc(cio.get("summary"))}</div>')

# ── tiles (compact, on the floor) ─────────────────────────────────────────────────────────────
def fm_tile(f, ctx_f):
    c = ctx_f or {}
    return (
        f'<div class="tile fm" id="d-fm-{esc(f.get("key"))}-tile" onclick="openModal(\'d-fm-{esc(f.get("key"))}\')">'
        f'<div class="t-top"><span class="t-name">{esc(f.get("name"))}</span>{stance_dot(f.get("stance"))}</div>'
        f'<div class="t-gauge">IR {ir_gauge(c.get("median_ir"))}</div>'
        f'<div class="t-sub">{esc(c.get("n_funds"))} funds · {trunc(c.get("benchmark"), 22)}</div>'
        f'<div class="t-call">{trunc(f.get("positioning"), 90)}</div></div>')

def analyst_tile(a, ctx_a):
    c = ctx_a or {}
    return (
        f'<div class="tile an" onclick="openModal(\'d-analyst-{esc(a.get("key"))}\')">'
        f'<div class="t-top"><span class="t-name">{esc(c.get("desk") or a.get("name"))}</span>{stance_dot(a.get("stance"))}</div>'
        f'<div class="t-gauge">ARM {arm_gauge(c.get("arm_ew"))}</div>'
        f'<div class="t-sub">{esc(c.get("coverage_n"))} stocks · {esc(c.get("recommending_n"))}★ recommending</div>'
        f'<div class="t-call">{trunc(a.get("headline"), 90)}</div></div>')

# ── Kanban deal-flow cards ────────────────────────────────────────────────────────────────────
def kanban_pitches(org):
    out = ""
    for a in org.get("analysts", []):
        for p in a.get("pitches", []):
            to = ", ".join(p.get("to", []) or [])
            out += (f'<div class="kc kc-pitch" data-stock="{esc(p.get("stock"))}" data-modal="d-analyst-{esc(a.get("key"))}" '
                    f'onclick="openModal(\'d-analyst-{esc(a.get("key"))}\')">'
                    f'<div class="kc-h"><span class="act act-{esc(p.get("action"))}">{esc(p.get("action"))}</span> '
                    f'<b>{esc(p.get("stock"))}</b> {conv_dots(p.get("conviction"))}</div>'
                    f'<div class="kc-meta">{esc(a.get("name"))} <span class="arrow">→</span> {esc(to)}</div>'
                    f'<div class="kc-txt">{trunc(p.get("thesis"), 120)}</div></div>')
    return out or '<div class="muted pad">No pitches this session.</div>'

def kanban_decisions(org, cf):
    out = ""
    for f in org.get("fund_managers", []):
        div = (cf.get(f.get("key"), {}) or {}).get("division", "")
        for t in f.get("pitches_taken", []):
            out += (f'<div class="kc kc-take" data-stock="{esc(t.get("stock"))}" data-div="{esc(div)}" data-modal="d-fm-{esc(f.get("key"))}" '
                    f'onclick="openModal(\'d-fm-{esc(f.get("key"))}\')">'
                    f'<div class="kc-h"><span class="dec dec-take">taken</span> <b>{esc(t.get("stock"))}</b> '
                    f'<span class="sz">{esc(t.get("size") or "")}</span></div>'
                    f'<div class="kc-meta">{esc(f.get("name"))} <span class="muted">· from {esc(t.get("from"))}</span></div>'
                    f'<div class="kc-txt">{trunc(t.get("why"), 120)}</div></div>')
        for d in f.get("pitches_declined", []):
            out += (f'<div class="kc kc-pass" data-stock="{esc(d.get("stock"))}" data-div="{esc(div)}" data-modal="d-fm-{esc(f.get("key"))}" '
                    f'onclick="openModal(\'d-fm-{esc(f.get("key"))}\')">'
                    f'<div class="kc-h"><span class="dec dec-pass">passed</span> <b>{esc(d.get("stock"))}</b></div>'
                    f'<div class="kc-meta">{esc(f.get("name"))}</div>'
                    f'<div class="kc-txt">{trunc(d.get("why_not"), 120)}</div></div>')
    return out or '<div class="muted pad">No decisions this session.</div>'

def kanban_rulings(org):
    cio = org.get("cio", {}) or {}
    out = ""
    for e in org.get("escalations", []):
        out += (f'<div class="kc kc-esc" data-stock="{esc(e.get("topic"))}" data-modal="d-cio" onclick="openModal(\'d-cio\')">'
                f'<div class="kc-h"><span class="dec dec-esc">escalated</span> <b>{trunc(e.get("topic"), 40)}</b></div>'
                f'<div class="kc-meta">{esc(e.get("from"))} <span class="arrow">→</span> CIO <span class="muted">({esc(e.get("reason"))})</span></div>'
                f'<div class="kc-txt">{trunc(e.get("ask"), 110)}</div></div>')
    for r in cio.get("rulings", []):
        out += (f'<div class="kc kc-rule" data-stock="{esc(r.get("on"))}" data-modal="d-cio" onclick="openModal(\'d-cio\')">'
                f'<div class="kc-h"><span class="dec dec-rule">ruling</span> <b>{trunc(r.get("on"), 40)}</b></div>'
                f'<div class="kc-txt">{trunc(r.get("ruling"), 120)}</div>'
                f'<div class="kc-meta muted">{trunc(r.get("rationale"), 110)}</div></div>')
    return out or '<div class="muted pad">No escalations reached the CIO this session.</div>'

STYLE = """
:root{--bg:#0a0e16;--pnl:#121826;--pnl2:#0e1320;--bd:#1f2937;--bd2:#2a3548;--fg:#e5e7eb;--mut:#7c8aa0;--acc:#38bdf8;--gold:#fbbf24}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 50% -200px,#13203a 0%,var(--bg) 60%);color:var(--fg);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1480px;margin:0 auto;padding:18px}
a{color:var(--acc)}
h1{font-size:22px;margin:0}
.muted{color:var(--mut)} .pad{padding:14px}
.bar{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--bd);padding-bottom:12px;margin-bottom:14px;flex-wrap:wrap;gap:10px}
.sub-title{color:var(--mut);font-size:13px;margin-top:3px}
.tag{background:#fbbf241a;color:var(--gold);border:1px solid #fbbf2455;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.04em}
.section-h{font-size:13px;letter-spacing:.07em;text-transform:uppercase;color:var(--mut);margin:24px 0 12px;border-bottom:1px solid var(--bd);padding-bottom:6px;display:flex;justify-content:space-between;align-items:center}
.section-h .hint{font-size:10.5px;text-transform:none;letter-spacing:0;color:#5b6678}
/* command bar */
.cmd{display:grid;grid-template-columns:1.5fr 1fr;gap:14px;margin-bottom:6px}
.concl{background:linear-gradient(100deg,#0e1c30,#101725);border:1px solid #24405e;border-left:4px solid var(--acc);padding:15px 17px;border-radius:12px;cursor:pointer;transition:.15s}
.concl:hover{border-color:var(--acc)}
.concl .lbl{color:var(--acc);font-weight:700;font-size:11px;letter-spacing:.07em;text-transform:uppercase}
.concl .big{font-size:15px;margin-top:7px;color:#f1f5f9}
.concl .more{color:var(--mut);font-size:11px;margin-top:8px}
.tape{background:var(--pnl);border:1px solid var(--bd);border-radius:12px;padding:13px 15px;display:flex;flex-direction:column;gap:9px;justify-content:center}
.tape .row{display:flex;align-items:center;gap:10px;font-size:12.5px}
.tape .k{color:var(--mut);min-width:108px}
.tape b{color:#f1f5f9}
.pill{font-weight:700;border-radius:20px;padding:1px 9px;font-size:11px}
/* gauges */
.gauge{display:inline-block;width:74px;height:8px;border-radius:6px;background:#1b2433;vertical-align:middle;overflow:hidden;border:1px solid #232f44}
.gfill{display:block;height:100%;border-radius:6px}
.gval{font-size:12px;margin-left:7px;font-weight:700;vertical-align:middle}
/* tiles + divisions */
.divh{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#9fb0c7;margin:14px 0 8px;display:flex;align-items:center;gap:9px}
.divh::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--bd2),transparent)}
.divh .cnt{color:var(--mut);font-weight:600;letter-spacing:0;text-transform:none;font-size:11px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(228px,1fr));gap:10px}
.tile{background:var(--pnl);border:1px solid var(--bd);border-radius:10px;padding:11px 12px;cursor:pointer;transition:.13s;position:relative}
.tile:hover{transform:translateY(-2px);border-color:var(--bd2);box-shadow:0 8px 22px #0008}
.tile.fm{background:linear-gradient(180deg,#121a2b,#0f1420)}
.tile.an{background:linear-gradient(180deg,#0f1726,#0e1320)}
.t-top{display:flex;justify-content:space-between;align-items:center;gap:6px}
.t-name{font-weight:700;font-size:13.5px;color:#f1f5f9}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;flex:none}
.dotlbl{font-size:9.5px;text-transform:capitalize;margin-left:5px}
.t-gauge{margin:9px 0 5px;font-size:11px;color:var(--mut)}
.t-sub{font-size:11px;color:var(--mut)}
.t-call{font-size:11.5px;color:#cbd5e1;margin-top:7px;border-top:1px dashed var(--bd);padding-top:7px;min-height:32px}
.chip{padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;text-transform:capitalize;white-space:nowrap}
/* kanban */
.kan{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
.kcol{background:var(--pnl2);border:1px solid var(--bd);border-radius:12px;padding:8px;min-height:120px}
.kcol-h{font-size:11.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;padding:6px 8px 9px;display:flex;align-items:center;gap:8px}
.kcol-h .num{background:#1b2433;color:var(--mut);border-radius:20px;padding:0 8px;font-size:11px}
.kcol.c1 .kcol-h{color:#86efac} .kcol.c2 .kcol-h{color:#7dd3fc} .kcol.c3 .kcol-h{color:#fcd34d}
.kbody{display:flex;flex-direction:column;gap:7px;max-height:560px;overflow:auto;padding:2px}
.kc{background:var(--pnl);border:1px solid var(--bd);border-left:3px solid var(--bd2);border-radius:8px;padding:8px 9px;cursor:pointer;transition:.1s}
.kc:hover{border-color:var(--bd2)}
.kc.hot{border-left-color:var(--gold);box-shadow:0 0 0 1px var(--gold)55, 0 6px 16px #0007;background:#161d2c}
.kc.dim{opacity:.22;filter:saturate(.4)}
.kc-pitch{border-left-color:#16a34a66} .kc-take{border-left-color:#16a34a} .kc-pass{border-left-color:#dc262688}
.kc-esc{border-left-color:#d97706} .kc-rule{border-left-color:#38bdf8}
.kc-h{display:flex;align-items:center;gap:6px;flex-wrap:wrap} .kc-h b{font-size:13px}
.kc-meta{font-size:10.5px;color:var(--mut);margin-top:3px} .arrow{color:var(--acc)}
.kc-txt{font-size:11.5px;color:#cbd5e1;margin-top:4px}
.act{font-size:9.5px;font-weight:700;text-transform:uppercase;padding:1px 6px;border-radius:4px;border:1px solid}
.act-accumulate,.act-add{color:#16a34a;border-color:#16a34a55;background:#16a34a14}
.act-reduce,.act-avoid{color:#dc2626;border-color:#dc262655;background:#dc262614}
.act-hold,.act-watch{color:#d97706;border-color:#d9770655;background:#d9770614}
.dec{font-size:9.5px;font-weight:700;text-transform:uppercase;padding:1px 6px;border-radius:4px;border:1px solid}
.dec-take{color:#16a34a;border-color:#16a34a55;background:#16a34a14}
.dec-pass{color:#dc2626;border-color:#dc262655;background:#dc262614}
.dec-esc{color:#d97706;border-color:#d9770655;background:#d9770614}
.dec-rule{color:#38bdf8;border-color:#38bdf855;background:#38bdf814}
.conv{color:var(--gold);font-size:9px;letter-spacing:1px} .sz{font-size:10px;color:var(--gold)}
.hz{color:var(--mut);font-size:10px;border:1px solid var(--bd);padding:0 5px;border-radius:4px} .to{color:var(--acc);font-size:10px;margin-left:auto}
/* deal-flow toolbar */
.flowbar{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.flowbar input{background:var(--pnl);border:1px solid var(--bd2);color:var(--fg);border-radius:8px;padding:6px 10px;font-size:12.5px;width:200px}
.flowbar .focusnote{font-size:11.5px;color:var(--gold)}
.flowbar .clr{cursor:pointer;color:var(--acc);font-size:11.5px;text-decoration:underline}
/* modal */
.overlay{position:fixed;inset:0;background:#04070cdd;display:none;align-items:flex-start;justify-content:center;z-index:50;padding:34px 16px;overflow:auto}
.overlay.on{display:flex}
.modal{background:var(--pnl);border:1px solid var(--bd2);border-radius:14px;max-width:680px;width:100%;padding:20px 22px;box-shadow:0 30px 80px #000b;position:relative}
.modal .x{position:absolute;top:12px;right:14px;cursor:pointer;color:var(--mut);font-size:20px;line-height:1}
.modal .x:hover{color:var(--fg)}
.md-h{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px}
.md-who{font-weight:800;font-size:17px} .role{color:var(--mut);font-weight:500;font-size:11px;border:1px solid var(--bd);padding:1px 7px;border-radius:10px;margin-left:6px}
.md-metrics{color:var(--mut);font-size:12px;margin:6px 0 4px;border-bottom:1px dashed var(--bd);padding-bottom:9px}
.headline{font-size:14px;margin:9px 0;color:#f1f5f9}
.sub{font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin:13px 0 5px}
ul{margin:4px 0;padding-left:18px} li{margin:2px 0}
.pitch{background:var(--pnl2);border:1px solid var(--bd);border-radius:7px;padding:8px;margin-bottom:6px}
.pitch-h{display:flex;align-items:center;gap:6px;flex-wrap:wrap} .pitch-h b{font-size:13px}
.th{font-size:12.5px;margin:3px 0} .ev{font-size:11px;color:var(--mut);font-family:ui-monospace,Menlo,monospace}
.tilt{font-size:12.5px} .exp{font-size:12.5px;color:#cbd5e1}
.tk{font-size:12.5px;margin:5px 0;border-left:2px solid #16a34a55;padding-left:9px} .tk.decl{border-left-color:#dc262655}
.tk .why{color:var(--mut);font-size:11.5px} .frm{font-size:10px;color:var(--acc)}
.esc-row{font-size:12px;margin:4px 0} .rsn{font-size:9px;font-weight:700;text-transform:uppercase;padding:1px 5px;border-radius:4px;border:1px solid #d9770655;color:#d97706;background:#d9770614}
.pulse{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.lens{background:var(--pnl2);border:1px solid var(--bd);border-radius:8px;padding:10px;font-size:12px}
.lens .ln{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--acc);font-weight:700;margin-bottom:4px} .lens.gap .ln{color:var(--gold)}
table{width:100%;border-collapse:collapse;font-size:12.5px} td{padding:5px 6px;border-bottom:1px solid var(--bd)}
.rflag{display:inline-block;background:#dc26261a;color:#fca5a5;border:1px solid #dc262655;padding:2px 8px;border-radius:6px;font-size:11.5px;margin:3px 4px 3px 0}
.trace{margin-top:14px;background:#10203a;color:var(--acc);border:1px solid #24405e;border-radius:8px;padding:7px 12px;font-size:12px;cursor:pointer;font-weight:600}
.trace:hover{background:#16294a}
.foot{color:var(--mut);font-size:11.5px;margin-top:26px;border-top:1px solid var(--bd);padding-top:13px}
@media(max-width:1000px){.kan{grid-template-columns:1fr}.cmd{grid-template-columns:1fr}}
/* ── top tabs (Trading Floor | Schemes & Books) ── */
.tabs{display:flex;gap:6px;margin:4px 0 18px;border-bottom:2px solid var(--bd);flex-wrap:wrap}
.tabbtn{background:none;border:none;color:var(--mut);font:600 13.5px/1 inherit;padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px}
.tabbtn:hover{color:var(--fg)}
.tabbtn.on{color:var(--acc);border-bottom-color:var(--acc)}
.tabview{display:none} .tabview.on{display:block}
/* ── books table (schemes overview) ── */
.bktbl,.bltbl,.holdtbl{width:100%;border-collapse:collapse;font-size:12.5px}
.bktbl th,.bltbl th{text-align:left;font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--mut);padding:7px 8px;border-bottom:1px solid var(--bd2)}
.bktbl td{padding:9px 8px;border-bottom:1px solid var(--bd);vertical-align:middle}
.bktbl tbody tr:hover{background:#141c2c}
.num{text-align:right;font-variant-numeric:tabular-nums}
th.num{text-align:right}
.spark{display:block}
/* ── firms: AMC selector + firm header ── */
.firmsel{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 4px}
.firmpill{background:var(--pnl);border:1px solid var(--bd2);border-radius:10px;padding:8px 13px;cursor:pointer;text-align:left;color:var(--fg);transition:.12s;min-width:150px}
.firmpill:hover{border-color:var(--acc)}
.firmpill.on{border-color:var(--acc);background:linear-gradient(180deg,#10243c,#0e1726);box-shadow:0 0 0 1px var(--acc)55}
.firmpill .fp-name{display:block;font-weight:700;font-size:13.5px;color:#f1f5f9}
.firmpill .fp-meta{display:block;font-size:10.5px;color:var(--mut);margin-top:2px}
.firmhdr{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;background:linear-gradient(100deg,#0e1c30,#101725);border:1px solid #24405e;border-left:4px solid var(--acc);border-radius:12px;padding:13px 17px;margin:6px 0 12px}
.fh-name{font-size:18px;font-weight:800;color:#f1f5f9}
.fh-tag{font-size:10px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--gold);background:#fbbf241a;border:1px solid #fbbf2455;padding:1px 8px;border-radius:20px;margin-left:6px;vertical-align:middle}
.fh-sub{font-size:11.5px;color:var(--mut);margin-top:2px}
.fh-stats{display:flex;gap:10px;flex-wrap:wrap}
.fh-stat{background:#0e1320aa;border:1px solid var(--bd);border-radius:9px;padding:7px 13px;min-width:84px}
.fh-stat .k{display:block;font-size:9.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
.fh-stat b{font-size:15px;color:#f1f5f9}
/* ── scheme panel ── */
.schhead h2{font-size:19px;margin:0;color:#f1f5f9}
.scbacklist{margin-bottom:8px}
/* ── scorecard cards ── */
.scwin{font-size:11.5px;margin:2px 0 10px}
.sccards{display:grid;grid-template-columns:repeat(auto-fill,minmax(186px,1fr));gap:10px}
.sccard{background:var(--pnl);border:1px solid var(--bd);border-radius:10px;padding:11px 13px}
.sck{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}
.scv{font-size:21px;font-weight:800;margin:3px 0}
.scs{font-size:10.5px;color:var(--mut)}
.screads{font-size:12.5px;padding-left:18px} .screads li{margin:5px 0;color:#cbd5e1}
.scnote{font-size:11px;border-top:1px dashed var(--bd);padding-top:9px;margin-top:9px;line-height:1.6}
/* ── fact sheet ── */
.fs-asof{font-size:12px;color:var(--mut);margin-bottom:8px}
.fstats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.fstat{background:var(--pnl);border:1px solid var(--bd);border-radius:9px;padding:8px 13px;min-width:96px}
.fstat .k{display:block;font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
.fstat b{font-size:15px}
.fs-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:18px}
.holdtbl td{padding:5px 7px;border-bottom:1px solid var(--bd);font-size:12px}
.ptag{font-size:9px;font-weight:700;text-transform:uppercase;padding:1px 6px;border-radius:4px;border:1px solid}
.pt-structural{color:#38bdf8;border-color:#38bdf855;background:#38bdf814}
.pt-tactical{color:#fbbf24;border-color:#fbbf2455;background:#fbbf2414}
.pt-cyclical{color:#a78bfa;border-color:#a78bfa55;background:#a78bfa14}
.secrow{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:12px}
.secn{min-width:118px;color:#cbd5e1} .secv{min-width:42px;text-align:right;color:var(--mut)}
.secbar{flex:1;height:8px;background:#1b2433;border-radius:6px;border:1px solid #232f44;overflow:hidden}
.secfill{display:block;height:100%;background:linear-gradient(90deg,#1d4ed8,#38bdf8);border-radius:6px}
/* ── blotter ── */
.blwrap{max-height:560px;overflow:auto;border:1px solid var(--bd);border-radius:10px}
.bltbl th{position:sticky;top:0;background:var(--pnl2);z-index:1}
.bltbl td{padding:7px 8px;border-bottom:1px solid var(--bd);font-size:12px;vertical-align:top}
.bl-why{color:var(--mut);font-size:11.5px;max-width:340px}
@media(max-width:1000px){.fs-grid{grid-template-columns:1fr}}
"""

SCRIPT = """
function openModal(id){
  var src=document.getElementById(id); if(!src)return;
  document.getElementById('modalbody').innerHTML='<span class="x" onclick="closeModal()">×</span>'+src.innerHTML;
  document.getElementById('overlay').classList.add('on'); document.body.style.overflow='hidden';
}
function closeModal(){document.getElementById('overlay').classList.remove('on');document.body.style.overflow='';}
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeModal();});
// same-stock highlight across the three kanban columns
function norm(s){return (s||'').toString().trim().toUpperCase();}
document.addEventListener('mouseover',function(e){
  var c=e.target.closest('.kc'); if(!c)return; var s=norm(c.getAttribute('data-stock')); if(!s)return;
  document.querySelectorAll('.kc').forEach(function(k){ if(norm(k.getAttribute('data-stock'))===s) k.classList.add('hot'); });
});
document.addEventListener('mouseout',function(e){
  var c=e.target.closest('.kc'); if(!c)return;
  document.querySelectorAll('.kc.hot').forEach(function(k){k.classList.remove('hot');});
});
// focus the deal-flow on one desk (called from a desk modal)
function focusDesk(modalId){
  closeModal();
  document.querySelectorAll('.kc').forEach(function(k){ k.classList.toggle('dim', k.getAttribute('data-modal')!==modalId); });
  var note=document.getElementById('focusnote'); var nm=(document.getElementById(modalId)||{});
  note.style.display='';
  document.getElementById('kanban').scrollIntoView({behavior:'smooth',block:'start'});
}
function clearFocus(){
  document.querySelectorAll('.kc.dim').forEach(function(k){k.classList.remove('dim');});
  document.getElementById('focusnote').style.display='none';
  var b=document.getElementById('flowsearch'); if(b)b.value='';
}
// search box: dim cards whose stock doesn't match
function flowSearch(){
  var q=norm(document.getElementById('flowsearch').value);
  var note=document.getElementById('focusnote');
  if(!q){clearFocus();return;}
  note.style.display='';
  document.querySelectorAll('.kc').forEach(function(k){ k.classList.toggle('dim', norm(k.getAttribute('data-stock')).indexOf(q)<0); });
}
// ── top tabs: Trading Floor | Schemes & Books ──
function showTab(id){
  document.querySelectorAll('.tabview').forEach(function(v){v.classList.toggle('on', v.id==='tab-'+id);});
  document.querySelectorAll('.tabbtn').forEach(function(b){b.classList.toggle('on', b.getAttribute('data-tab')===id);});
  window.scrollTo({top:0,behavior:'smooth'});
}
// ── schemes sub-nav: open one scheme panel, hide the overview ──
function openScheme(key){
  document.getElementById('schemes-overview').style.display='none';
  document.querySelectorAll('.schpanel').forEach(function(p){p.style.display = (p.id==='sch-'+key)?'block':'none';});
  document.getElementById('schemes-back').style.display='';
  window.scrollTo({top:0,behavior:'smooth'});
}
function schemesHome(){
  document.querySelectorAll('.schpanel').forEach(function(p){p.style.display='none';});
  document.getElementById('schemes-overview').style.display='block';
  document.getElementById('schemes-back').style.display='none';
}
// ── firm selector: switch which digital firm (AMC) is shown ──
function showFirm(key){
  document.querySelectorAll('.firmblock').forEach(function(b){b.style.display=(b.id==='firm-'+key)?'block':'none';});
  document.querySelectorAll('.firmpill').forEach(function(p){p.classList.toggle('on', p.getAttribute('data-firm')===key);});
}
"""

# ══════════════════════════════════════════════════════════════════════════════════════════════
#  BOOKS — surface the per-scheme paper-trading replay artifacts (NAV / fact-sheet / blotter / scorecard)
#  These read amc_book/<amc>/<scheme>/{book.json, replay/scorecard.json, replay/monthly_summary.json,
#  daily/*.json, blotter.jsonl}. Everything degrades to "—" if a file is missing — never raises.
#  LICENCE: the books carry NO raw per-stock ARM by design; we surface only derived weights, play-types,
#  NAV, and the aggregate IC/TC/IR that the replay already reduced. We never compute or show raw ARM.
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None

def _read_jsonl(p, limit=None):
    out = []
    try:
        with open(p, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
                if limit and len(out) >= limit:
                    break
    except Exception:
        pass
    return out

def load_books():
    """Walk amc_book/ data-driven. Returns a list of scheme dicts (no hard-coding of names)."""
    books = []
    if not BOOKS.exists():
        return books
    for amc_dir in sorted(p for p in BOOKS.iterdir() if p.is_dir()):
        for sch_dir in sorted(p for p in amc_dir.iterdir() if p.is_dir()):
            book = _read_json(sch_dir / "book.json") or {}
            scorecard = _read_json(sch_dir / "replay" / "scorecard.json") or {}
            monthly = _read_json(sch_dir / "replay" / "monthly_summary.json") or []
            # newest daily fact-sheet: the daily/ folder holds one file per month, each a {date: sheet} map
            daily_sheet, daily_date = None, None
            ddir = sch_dir / "daily"
            if ddir.exists():
                for dfile in sorted(p for p in ddir.iterdir() if p.suffix == ".json"):
                    dd = _read_json(dfile) or {}
                    for dt in sorted(dd.keys()):
                        if dd[dt]:
                            daily_sheet, daily_date = dd[dt], dt
            # fall back to the committed FACT_SHEET_*.json if no daily/ rollup
            if daily_sheet is None:
                fs = sorted(sch_dir.glob("FACT_SHEET_*.json"))
                if fs:
                    daily_sheet = _read_json(fs[-1]) or None
                    if daily_sheet:
                        daily_date = daily_sheet.get("header", {}).get("asof")
            blotter = _read_jsonl(sch_dir / "blotter.jsonl")
            nav_series = []                      # [(date, nav)] — prefer monthly (light); nav.csv is daily (heavy)
            for m in (monthly or []):
                if isinstance(m, dict) and m.get("date") is not None and m.get("nav") is not None:
                    nav_series.append((m["date"], m["nav"]))
            # benchmark NAV line (rebased to 100 at the book start by the replay) — daily CSV, sampled
            # at the book's month-end NAV dates so the two lines share one x-axis for the overlay.
            bench_daily = _read_nav_csv(sch_dir / "replay" / "benchmark_nav.csv")
            bench_series = _align_bench(nav_series, bench_daily) if bench_daily else []
            # live-forward (paper) NAV since this book's live inception (the round seam), marked daily —
            # distinct from the 2015→ replay track. Thin (1 pt) right after a seam; grows each trading day.
            live_nav = _read_live_nav(AMC / "live" / "nav" / (_live_slug(book.get("scheme") or sch_dir.name) + ".csv"))
            books.append({
                "amc": book.get("amc") or amc_dir.name,
                "scheme": book.get("scheme") or sch_dir.name,
                "key": _bk(amc_dir.name + "-" + sch_dir.name),
                "category": book.get("category"),
                # show the benchmark the track is ACTUALLY scored against (the scorecard's resolved index —
                # a SECTOR index for theme-fenced funds), not the seam book's generic stated benchmark.
                "benchmark": scorecard.get("scorecard", {}).get("benchmark_name") or book.get("benchmark"),
                "aum0_cr": book.get("aum0_cr"),
                "code": book.get("code"),
                "n_positions": len(book.get("positions", {}) or {}),
                "book": book,
                "scorecard": scorecard,
                "monthly": monthly or [],
                "daily_sheet": daily_sheet,
                "daily_date": daily_date,
                "blotter": blotter,
                "nav_series": nav_series,
                "bench_series": bench_series,   # [(date, bench_nav)] aligned to nav_series dates (or [])
                "live_nav_series": live_nav,    # [(date, nav)] forward paper track since the live seam (or [])
            })
    return books

def _live_slug(scheme):
    """Mirror amc_live.slug — the forward-NAV csv filename for a scheme (non-alnum → underscore)."""
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", str(scheme)).strip("_")

def _read_live_nav(p):
    """Read a live-forward NAV csv (date,total_cr,nav,day_return_pct from amc_daily_mark) → [(date,nav)].
    This is the PAPER track from the book's live inception forward (NAV base-100 at inception), distinct
    from the 2015→ replay track. Returns [] if absent/bad. NAV levels only — no raw ARM."""
    out = []
    try:
        with open(p, encoding="utf-8") as fh:
            fh.readline()                          # header: date,total_cr,nav,day_return_pct
            for ln in fh:
                parts = ln.strip().split(",")
                if len(parts) >= 3:
                    try:
                        out.append((parts[0][:10], float(parts[2])))
                    except Exception:
                        pass
    except Exception:
        return []
    return out

def _read_nav_csv(p):
    """Read a replay nav.csv / benchmark_nav.csv into {date_str: float}. Returns {} if absent/bad.
    No raw ARM anywhere — these are NAV levels only."""
    out = {}
    try:
        with open(p, encoding="utf-8") as fh:
            head = fh.readline()                 # 'date,nav'
            for ln in fh:
                parts = ln.strip().split(",")
                if len(parts) >= 2:
                    try:
                        out[parts[0][:10]] = float(parts[1])
                    except Exception:
                        pass
    except Exception:
        return {}
    return out

def _align_bench(nav_series, bench_daily):
    """Sample the daily benchmark NAV at each of the book's month-end NAV dates (exact date, else the
    nearest earlier benchmark observation) → [(date, bench_nav)] sharing the book's x-axis. Both are
    already rebased to 100 at inception by the replay, so the lines start together."""
    if not nav_series or not bench_daily:
        return []
    bdates = sorted(bench_daily.keys())
    out = []
    import bisect
    for d, _ in nav_series:
        ds = str(d)[:10]
        if ds in bench_daily:
            out.append((d, bench_daily[ds])); continue
        i = bisect.bisect_right(bdates, ds) - 1   # nearest benchmark obs on/before this book date
        if i >= 0:
            out.append((d, bench_daily[bdates[i]]))
    return out

def _bk(s):
    """slugify a string into a safe DOM id fragment"""
    return "".join(c if (c.isalnum()) else "-" for c in str(s)).strip("-").lower()

def _fmt(v, nd=2, pct=False, plus=False):
    try:
        v = float(v)
    except Exception:
        return "—"
    s = f"{v:+.{nd}f}" if plus else f"{v:.{nd}f}"
    return s + ("%" if pct else "")

def sparkline(series, w=150, h=34, up="#16a34a", dn="#dc2626", bench=None, bench_color="#64748b"):
    """Inline SVG NAV sparkline from [(date,nav),…] — fully offline, no Plotly. Colour by net direction.
    If `bench` ([(date,nav),…], same rebase) is given, overlay it as a thin dashed reference line on a
    SHARED y-scale (so book-vs-benchmark separation is visible at a glance)."""
    ys = [float(n) for _, n in series if n is not None]
    if len(ys) < 2:
        return '<span class="muted">—</span>'
    bys = [float(n) for _, n in (bench or []) if n is not None]
    lo = min(ys + bys); hi = max(ys + bys)        # shared scale across both lines
    rng = (hi - lo) or 1.0

    def _poly(vals):
        m = len(vals); pts = []
        for i, y in enumerate(vals):
            px = (i / (m - 1)) * (w - 2) + 1 if m > 1 else 1
            py = h - 2 - ((y - lo) / rng) * (h - 4)
            pts.append(f"{px:.1f},{py:.1f}")
        return " ".join(pts)

    color = up if ys[-1] >= ys[0] else dn
    poly = _poly(ys)
    area = f"1,{h-1} " + poly + f" {w-1},{h-1}"
    bench_poly = (f'<polyline points="{_poly(bys)}" fill="none" stroke="{bench_color}" stroke-width="1.1" '
                  f'stroke-dasharray="3,2" stroke-linejoin="round" opacity="0.85"/>') if len(bys) >= 2 else ""
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<polygon points="{area}" fill="{color}" opacity="0.10"/>'
            f'{bench_poly}'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="1.6" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')

def nav_vs_bench_chart(b, w=620, h=190):
    """A labelled NAV-vs-benchmark line chart for the scheme panel — book NAV (solid) overlaid with the
    benchmark NAV line (dashed), both rebased to 100 at inception by the replay, on a shared y-scale and
    one date x-axis. Pure inline SVG (offline). Returns '' if there's no NAV path."""
    ns = b.get("nav_series") or []
    bs = b.get("bench_series") or []
    ys = [float(n) for _, n in ns if n is not None]
    if len(ys) < 2:
        return ""
    bys = [float(n) for _, n in bs if n is not None]
    dates = [str(d)[:10] for d, n in ns if n is not None]
    lo = min(ys + bys); hi = max(ys + bys); rng = (hi - lo) or 1.0
    padL, padB = 38, 18
    iw, ih = w - padL - 8, h - padB - 10

    def _x(i, m): return padL + (i / (m - 1)) * iw if m > 1 else padL
    def _y(v): return 10 + ih - ((v - lo) / rng) * ih
    def _poly(vals): return " ".join(f"{_x(i,len(vals)):.1f},{_y(v):.1f}" for i, v in enumerate(vals))

    # y gridlines at 100 (the inception base) and the max
    base_y = _y(100.0)
    grid = (f'<line x1="{padL}" y1="{base_y:.1f}" x2="{padL+iw}" y2="{base_y:.1f}" stroke="#2a3548" '
            f'stroke-width="1" stroke-dasharray="2,3"/><text x="4" y="{base_y+3:.1f}" fill="#7c8aa0" font-size="9">100</text>'
            f'<text x="4" y="{_y(hi)+8:.1f}" fill="#7c8aa0" font-size="9">{hi:.0f}</text>')
    nlab = len(dates)
    xlab = ""
    for i in (0, nlab // 2, nlab - 1):
        if 0 <= i < nlab:
            xlab += f'<text x="{_x(i,nlab):.1f}" y="{h-4}" fill="#7c8aa0" font-size="9" text-anchor="middle">{esc(dates[i])}</text>'
    book_poly = f'<polyline points="{_poly(ys)}" fill="none" stroke="#38bdf8" stroke-width="2" stroke-linejoin="round"/>'
    bench_poly = (f'<polyline points="{_poly(bys)}" fill="none" stroke="#94a3b8" stroke-width="1.5" '
                  f'stroke-dasharray="5,3" stroke-linejoin="round"/>') if len(bys) >= 2 else ""
    end_book = ys[-1]; end_bench = bys[-1] if bys else None
    legend = (f'<span style="color:#38bdf8;font-weight:700">— Book NAV {end_book:.0f}</span>'
              + (f' &nbsp; <span style="color:#94a3b8">- - {esc(b.get("benchmark") or "benchmark")} {end_bench:.0f}</span>'
                 if end_bench is not None else ' &nbsp; <span class="muted">(no benchmark series)</span>'))
    return (f'<div class="navchart-legend" style="font-size:11.5px;margin:2px 0 4px">{legend} '
            f'<span class="muted">· rebased to 100 at inception</span></div>'
            f'<svg class="navchart" width="100%" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet" '
            f'style="background:#0e1320;border:1px solid #1f2937;border-radius:10px">'
            f'{grid}{xlab}{bench_poly}{book_poly}</svg>')

# ── (1) SCHEMES OVERVIEW — one row per scheme: mandate, bench, NAV (latest + since-inception), sparkline ──
def schemes_overview(books):
    rows = ""
    for b in books:
        ns = b["nav_series"]
        nav0 = ns[0][1] if ns else None
        nav1 = ns[-1][1] if ns else None
        since = None
        if nav0 and nav1:
            try:
                since = (float(nav1) / float(nav0) - 1.0) * 100.0
            except Exception:
                since = None
        sc = b["scorecard"].get("scorecard", {}) if b["scorecard"] else {}
        cagr = sc.get("book", {}).get("cagr_pct")
        bench_cagr = sc.get("benchmark", {}).get("cagr_pct")
        excess = sc.get("benchmark", {}).get("excess_cagr_pct")
        excol = "#16a34a" if (isinstance(excess, (int, float)) and excess > 0) else ("#dc2626" if isinstance(excess, (int, float)) else "var(--mut)")
        rows += (
            f'<tr onclick="openScheme(\'{b["key"]}\')" style="cursor:pointer">'
            f'<td><b>{esc(b["scheme"])}</b><div class="t-sub">{esc(b["amc"])}</div></td>'
            f'<td>{esc(b["category"]) or "—"}</td>'
            f'<td>{esc(b["benchmark"]) or "—"}</td>'
            f'<td class="num">{_fmt(nav1)}</td>'
            f'<td class="num" style="color:{"#16a34a" if (isinstance(since,(int,float)) and since>=0) else "#dc2626"}">{_fmt(since,1,True,True) if since is not None else "—"}</td>'
            f'<td class="num">{_fmt(cagr,2,True)}<span class="muted"> / {_fmt(bench_cagr,2,True)} bm</span></td>'
            f'<td class="num" style="color:{excol}">{_fmt(excess,2,True,True)}</td>'
            f'<td>{sparkline(ns, bench=b.get("bench_series"))}</td></tr>')
    if not rows:
        return '<div class="muted pad">No scheme books found under amc_book/.</div>'
    return (
        '<table class="bktbl"><thead><tr>'
        '<th>Scheme</th><th>Mandate</th><th>Benchmark</th><th class="num">NAV</th>'
        '<th class="num">Since incep.</th><th class="num">CAGR / bench</th><th class="num">Excess</th>'
        '<th>NAV path</th></tr></thead><tbody>' + rows + '</tbody></table>'
        '<div class="muted" style="font-size:11px;margin-top:6px">NAV rebased to 100 at inception (2015-01-30). '
        'Click a row to open the scheme. "Since incep." = NAV/100 − 1 over the replay window; CAGR vs its benchmark TR; '
        'Excess = book CAGR − benchmark CAGR. Sparkline = month-end book NAV path (solid) vs its benchmark TR '
        '(dashed grey), shared scale.</div>')

# ══════════════════════════════════════════════════════════════════════════════════════════════
#  FIRMS — group the per-scheme books into per-AMC digital FIRMS (the North-Star unit). One firm =
#  one paper book per distinct equity/hybrid scheme of that AMC. An AMC selector lets the same view
#  clone to other AMCs as they are built (we begin with the full digital-ABSL firm).
# ══════════════════════════════════════════════════════════════════════════════════════════════
def firm_short(amc):
    """A compact firm label for the selector (drop the ' Mutual Fund' / AMC suffix)."""
    s = str(amc or "").strip()
    for suf in (" Mutual Fund", " Asset Management Company", " Asset Management", " AMC"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s or str(amc or "")

def _book_aum(b):
    """The scheme's seed AUM (₹cr): book.aum0_cr, else the replay diag, else the latest fact-sheet AUM."""
    cands = [b.get("aum0_cr"),
             ((b.get("scorecard") or {}).get("diag") or {}).get("aum0_cr"),
             ((b.get("daily_sheet") or {}).get("header") or {}).get("aum_cr")]
    for v in cands:
        try:
            if v is not None:
                return float(v)
        except Exception:
            pass
    return None

def _firm_aum(bs):
    return sum(a for a in (_book_aum(x) for x in bs) if a is not None)

def _firm_groups(books):
    """{amc: [books]} → list ordered by desk count desc, then firm AUM desc, so the fullest firm
    (digital-ABSL, 28 desks) is the default selection."""
    g = {}
    for b in books:
        g.setdefault(b["amc"], []).append(b)
    return sorted(g.items(), key=lambda kv: (-len(kv[1]), -_firm_aum(kv[1])))

def firm_header(amc, bs):
    """A firm summary strip: firm AUM (Σ scheme AUM), desk count, paper desks beating their benchmark TR."""
    aum = _firm_aum(bs)
    scored = [x for x in bs if (x.get("scorecard") or {}).get("scorecard")]
    beat = 0
    for x in scored:
        ex = ((x["scorecard"]["scorecard"].get("benchmark") or {}).get("excess_cagr_pct"))
        if isinstance(ex, (int, float)) and ex > 0:
            beat += 1
    asof = max([x.get("daily_date") for x in bs if x.get("daily_date")], default=None)
    return (
        f'<div class="firmhdr"><div class="fh-l">'
        f'<div class="fh-name">{esc(firm_short(amc))} <span class="fh-tag">digital firm</span></div>'
        f'<div class="fh-sub">{esc(amc)}</div></div>'
        f'<div class="fh-stats">'
        f'<div class="fh-stat"><span class="k">Firm AUM</span><b>₹{aum:,.0f} cr</b></div>'
        f'<div class="fh-stat"><span class="k">Desks</span><b>{len(bs)}</b></div>'
        + (f'<div class="fh-stat"><span class="k">Beat bench · paper</span><b>{beat}/{len(scored)}</b></div>' if scored else "")
        + (f'<div class="fh-stat"><span class="k">As of</span><b>{esc(asof)}</b></div>' if asof else "")
        + '</div></div>')

def firms_view(books):
    """The Firms & Schemes tab body: an AMC selector + one firm block (header + schemes table) each.
    The fullest firm (digital-ABSL) shows by default; pills switch firms; rows open a scheme panel."""
    if not books:
        return '<div class="muted pad">No scheme books found under amc_book/.</div>'
    groups = _firm_groups(books)
    pills, blocks = "", ""
    for i, (amc, bs) in enumerate(groups):
        key = _bk(amc)
        aum = _firm_aum(bs)
        on = " on" if i == 0 else ""
        pills += (f'<button class="firmpill{on}" data-firm="{key}" onclick="showFirm(\'{key}\')">'
                  f'<span class="fp-name">{esc(firm_short(amc))}</span>'
                  f'<span class="fp-meta">{len(bs)} desk{"s" if len(bs)!=1 else ""} · ₹{aum:,.0f} cr</span></button>')
        blocks += (f'<div class="firmblock" id="firm-{key}" style="display:{"block" if i==0 else "none"}">'
                   f'{firm_header(amc, bs)}{schemes_overview(bs)}</div>')
    note = ('<div class="muted" style="font-size:11px;margin:8px 0 2px">A <b>digital firm</b> = one paper book per '
            'distinct equity/hybrid scheme of that AMC, each run by the deterministic rules-FM under its own SEBI '
            'mandate + liquidity caps (paper-money, no look-ahead, free — no LLM). <b>Firm AUM</b> = Σ each scheme\'s '
            'real AUM. <b>Beat bench · paper</b> = desks whose paper-track CAGR exceeds their benchmark TR since 2015.'
            '<br><b>★ Read the historical track honestly:</b> a <b>true SECTOR/THEME fund</b> (Banking, Pharma, Digital, '
            'Consumption, Transport, Manufacturing, Infra) is now <b>theme-fenced</b> — its 2015→ replay picks ONLY from '
            'its own sector(s), and is scored against its <b>sector TR index</b> (NIFTY Healthcare / Financial Services / '
            'India Manufacturing …), which removes the broad-market sector beta that made every thematic desk look alike. '
            'A residual high IR on some desks (e.g. Digital, Transport) is NOT certified pure stock-selection alpha — the '
            'rules-FM is NOT cap-weight-constrained, so within the sector it tilts to smid + momentum names that beat the '
            'cap-weighted sector index; that is a <b>factor tilt vs the index</b>, read it as such (Banking, IR ~0.45, is '
            'the honest baseline). '
            '<b>Diversified mandates</b> (Large/Flexi/Value/Multi-Cap/Small…) select from the broad market vs their broad '
            'benchmark, as they should. <b>Characteristic themes</b> (PSU/MNC/ESG/Business-Cycle) are defined by ownership/'
            'style, not a sector — with no point-in-time eligibility flag they stay on the broad universe vs NIFTY 500, so '
            'their excess still carries some style beta (flagged, a known limitation). The <b>current fact sheet / holdings '
            '(the seam book) IS each scheme\'s real disclosed portfolio</b> — theme-faithful.</div>')
    return f'<div class="firmsel">{pills}</div>{note}<div class="firmblocks">{blocks}</div>'

# ── (2) FACT SHEET — newest daily sheet: key stats, top holdings, sector mix ──
def factsheet_block(b):
    fs = b["daily_sheet"]
    if not fs:
        return '<div class="muted">No fact sheet on file for this scheme.</div>'
    hdr = fs.get("header", {}) if isinstance(fs, dict) else {}
    rows = fs.get("rows", []) or []
    sectors = fs.get("sectors", []) or []
    footer = fs.get("footer", {}) or {}
    asof = hdr.get("asof") or b["daily_date"] or "—"
    # licence guard: never surface a raw ARM field even if a stray one appears
    SAFE_DROP = {"arm", "arm_score", "arm_raw", "arm_ew", "arm_ff"}
    top = sorted(rows, key=lambda r: r.get("pct_assets", 0) or 0, reverse=True)[:12]
    hold = "".join(
        f'<tr><td>{esc(r.get("name"))}<span class="muted"> · {esc(r.get("sym"))}</span></td>'
        f'<td>{esc(r.get("sector"))}</td>'
        f'<td><span class="ptag pt-{esc(r.get("play_type"))}">{esc(r.get("play_type") or "")}</span></td>'
        f'<td class="num">{_fmt(r.get("pct_assets"))}%</td></tr>'
        for r in top if not (SAFE_DROP & set(r.keys())))
    secbars = ""
    smax = max((s.get("pct_assets", 0) or 0 for s in sectors), default=1) or 1
    for s in sectors[:12]:
        pa = s.get("pct_assets", 0) or 0
        secbars += (f'<div class="secrow"><span class="secn">{esc(s.get("sector"))}</span>'
                    f'<span class="secbar"><span class="secfill" style="width:{(pa/smax)*100:.0f}%"></span></span>'
                    f'<span class="secv">{_fmt(pa,1)}%</span></div>')
    stats = (
        f'<div class="fstat"><span class="k">AUM</span><b>₹{_fmt(hdr.get("aum_cr"),0)} cr</b></div>'
        f'<div class="fstat"><span class="k">Holdings</span><b>{esc(footer.get("n_holdings"))}</b></div>'
        f'<div class="fstat"><span class="k">Equity</span><b>{_fmt(100-(footer.get("cash_pct") or 0),1)}%</b></div>'
        f'<div class="fstat"><span class="k">Cash</span><b>{_fmt(footer.get("cash_pct"),1)}%</b></div>'
        f'<div class="fstat"><span class="k">Day</span><b style="color:{"#16a34a" if (footer.get("day_return_pct") or 0)>=0 else "#dc2626"}">{_fmt(footer.get("day_return_pct"),2,True,True)}%</b></div>')
    return (
        f'<div class="fs-asof">Fact sheet as of <b>{esc(asof)}</b></div>'
        f'<div class="fstats">{stats}</div>'
        f'<div class="fs-grid">'
        f'<div><div class="sub">Top holdings</div><table class="holdtbl"><tbody>{hold or "<tr><td class=muted>—</td></tr>"}</tbody></table></div>'
        f'<div><div class="sub">Sector mix</div>{secbars or "<div class=muted>—</div>"}</div>'
        f'</div>')

# ── (3) TRADE REGISTER — blotter.jsonl as a paginated audit trail ──
def trade_register(b):
    bl = b["blotter"]
    if not bl:
        return '<div class="muted">No trades on the blotter yet for this scheme.</div>'
    rows = ""
    for t in bl:
        side = str(t.get("side", "")).upper()
        sc = "#16a34a" if side == "BUY" else ("#dc2626" if side in ("SELL", "TRIM") else "#94a3b8")
        rows += (
            f'<tr>'
            f'<td>{esc(t.get("date"))}</td>'
            f'<td style="color:{sc};font-weight:700">{esc(side)}</td>'
            f'<td><b>{esc(t.get("sym"))}</b><span class="muted"> {trunc(t.get("name"),28)}</span></td>'
            f'<td class="num">{_fmt(t.get("value_cr"),1)}</td>'
            f'<td><span class="ptag pt-{esc(t.get("play_type"))}">{esc(t.get("play_type") or "")}</span></td>'
            f'<td class="bl-why">{trunc(t.get("rationale"),120)}</td>'
            f'</tr>')
    return (
        f'<div class="muted" style="font-size:11px;margin-bottom:6px">{len(bl)} trade record(s) · value in ₹cr · '
        'play-type = the FM\'s structural / cyclical / tactical tag · rationale = the brain\'s note (no raw analyst scores shown).</div>'
        '<div class="blwrap"><table class="bltbl"><thead><tr>'
        '<th>Date</th><th>Side</th><th>Name</th><th class="num">₹cr</th><th>Play</th><th>Rationale</th>'
        f'</tr></thead><tbody>{rows}</tbody></table></div>')

# ── (4) SCORECARD — IC·√BR·TC / IR vs benchmark TR + real scheme NAV, with plain-English reads ──
def scorecard_block(b):
    raw = b["scorecard"].get("scorecard", {}) if b["scorecard"] else {}
    if not raw:
        return '<div class="muted">No scorecard yet — run the replay engine to populate it.</div>'
    bk = raw.get("book", {}) or {}
    bm = raw.get("benchmark", {}) or {}
    rs = raw.get("real_scheme", {}) or {}
    fl = raw.get("fundamental_law", {}) or {}
    win = raw.get("window", {}) or {}
    ir = bm.get("info_ratio")
    excess = bm.get("excess_cagr_pct")
    ic = fl.get("ic_mean"); ic_t = fl.get("ic_tstat"); tc = fl.get("transfer_coefficient")
    irc = "#16a34a" if (isinstance(ir, (int, float)) and ir > 0.05) else ("#dc2626" if (isinstance(ir, (int, float)) and ir < -0.05) else "var(--mut)")

    def card(lbl, val, sub, color="var(--fg)"):
        return f'<div class="sccard"><div class="sck">{lbl}</div><div class="scv" style="color:{color}">{val}</div><div class="scs">{sub}</div></div>'

    cards = (
        card("Book CAGR", _fmt(bk.get("cagr_pct"), 2, True), f'vol {_fmt(bk.get("vol_pct"),1,True)} · Sharpe {_fmt(bk.get("sharpe"),2)} · MaxDD {_fmt(bk.get("maxdd_pct"),0,True)}')
        + card(f'vs {esc(raw.get("benchmark_name") or "bench")}', _fmt(excess, 2, True, True), 'excess CAGR over benchmark TR',
               "#16a34a" if (isinstance(excess, (int, float)) and excess > 0) else "#dc2626")
        + card("Information Ratio", _fmt(ir, 2, plus=True), f'TE {_fmt(bm.get("tracking_error_pct"),1,True)} · β {_fmt(bm.get("beta"),2)}', irc)
        + card("IC (skill)", _fmt(ic, 3, plus=True), f't-stat {_fmt(ic_t,1)} · per-bet forecast↔outcome corr',
               "#16a34a" if (isinstance(ic, (int, float)) and ic > 0) else "var(--mut)")
        + card("Transfer coeff.", _fmt(tc, 2), 'how much skill survives the long-only / cap constraints')
        + card("vs REAL scheme", _fmt(rs.get("book_minus_real_cagr_pct"), 2, True, True),
               f'book − real CAGR · real {_fmt(rs.get("real_cagr_pct"),1,True)} over {_fmt(rs.get("overlap_years"),1)}y',
               "#16a34a" if (isinstance(rs.get("book_minus_real_cagr_pct"), (int, float)) and rs.get("book_minus_real_cagr_pct", 0) > 0) else "#dc2626")
    )

    # plain-English one-liners
    reads = []
    if isinstance(ir, (int, float)):
        verdict = ("adds value over its benchmark" if ir > 0.3 else
                   "edges its benchmark" if ir > 0.05 else
                   "tracks its benchmark" if ir > -0.05 else "lags its benchmark")
        reads.append(f'<b>IR {_fmt(ir,2,plus=True)}</b> — the book {verdict}: it earned {_fmt(excess,2,True,True)} excess CAGR '
                     f'per {_fmt(bm.get("tracking_error_pct"),1,True)} of tracking error.')
    if isinstance(ic, (int, float)):
        reads.append(f'<b>IC {_fmt(ic,3,plus=True)}</b> (t {_fmt(ic_t,1)}) — the selection signal is '
                     f'{"genuinely informative" if (ic_t or 0) > 2 else "weak"}; ~0.05 is a good single-name forecast correlation.')
    if isinstance(tc, (int, float)):
        reads.append(f'<b>TC {_fmt(tc,2)}</b> — about {_fmt((tc or 0)*100,0)}% of the raw signal survives the '
                     'long-only / position-cap constraints (the rest is the implementation leak).')
    if isinstance(rs.get("book_minus_real_cagr_pct"), (int, float)):
        d = rs["book_minus_real_cagr_pct"]
        reads.append(f'<b>vs real {esc(rs.get("matched_name") or "scheme")}</b> — the paper book ran '
                     f'{_fmt(abs(d),2,True)} {"ahead of" if d>0 else "behind"} the actual fund over {_fmt(rs.get("overlap_years"),1)} years.')
    note = fl.get("note")
    reads_html = "".join(f'<li>{r}</li>' for r in reads)
    return (
        f'<div class="scwin muted">Window {esc(win.get("start"))} → {esc(win.get("end"))} · {_fmt(win.get("years"),1)} years</div>'
        f'<div class="sccards">{cards}</div>'
        '<div class="sub">Plain-English read <span class="muted">— Fundamental Law of Active Management: IR = IC · √breadth · TC</span></div>'
        f'<ul class="screads">{reads_html or "<li class=muted>—</li>"}</ul>'
        + (f'<div class="scnote muted">{esc(note)}</div>' if note else '')
        + '<div class="scnote muted" style="margin-top:8px"><b>Definitions.</b> '
          '<b>IC</b> (information coefficient) = the rank correlation between the signal\'s forecast and the realised next-period '
          'return, per bet — how often the call is right. <b>Breadth (BR)</b> = the number of independent bets per year — more, '
          'de-correlated bets compound skill. <b>TC</b> (transfer coefficient) = the fraction of that skill that actually reaches '
          'the live portfolio after long-only and position-size constraints. <b>IR</b> (information ratio) = excess return ÷ '
          'tracking error — the realised skill the law predicts as IC·√BR·TC. Breadth here is an UPPER bound (monthly holdings '
          'are not independent), so implied IR is a ceiling, not a forecast.</div>')

# ── live-forward (paper) NAV since the seam — the agentic firm's REAL forward track ──
def live_nav_block(b):
    ls = b.get("live_nav_series") or []
    if not ls:
        return ('<div class="muted" style="font-size:12px">No live-forward track yet — the book is marked '
                'every trading day from its seam date forward.</div>')
    d0, n0 = ls[0]
    d1, n1 = ls[-1]
    if len(ls) < 2:
        return (f'<div class="muted" style="font-size:12px">Live-forward paper track begins <b>{esc(d0)}</b> at NAV 100 '
                'and is marked every trading day forward — one observation so far. (The chart above is the 2015→ '
                'rules-FM replay; this track is the firm\'s actual go-forward performance.)</div>')
    since = (float(n1) / 100.0 - 1.0) * 100.0
    col = "#16a34a" if since >= 0 else "#dc2626"
    return (f'<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">'
            f'<div class="fstat"><span class="k">Live NAV</span><b>{_fmt(n1)}</b></div>'
            f'<div class="fstat"><span class="k">Since seam</span><b style="color:{col}">{_fmt(since,2,True,True)}%</b></div>'
            f'<div class="fstat"><span class="k">Trading days</span><b>{len(ls)}</b></div>'
            f'<div>{sparkline(ls, w=240)}</div></div>'
            f'<div class="muted" style="font-size:11px;margin-top:6px">Paper track from the live seam (<b>{esc(d0)}</b>) '
            'forward, NAV base-100 at the seam, marked each trading day (no trades between monthly rounds). This is the '
            'firm\'s actual forward performance — distinct from the deterministic 2015→ replay above.</div>')

# ── per-scheme panel (hidden; revealed by the Schemes tab nav) ──
def scheme_panel(b):
    return (
        f'<div class="schpanel" id="sch-{b["key"]}" style="display:none">'
        f'<div class="schhead"><div><h2>{esc(b["scheme"])}</h2>'
        f'<div class="sub-title">{esc(b["amc"])} · {esc(b["category"]) or "—"} · benchmark {esc(b["benchmark"]) or "—"} · '
        f'{b["n_positions"]} holdings</div></div></div>'
        f'<div class="section-h">NAV vs benchmark <span class="hint">paper-trade book NAV overlaid on its benchmark TR, '
        'rebased to 100 at inception</span></div>'
        f'{nav_vs_bench_chart(b) or "<div class=muted>No NAV path yet.</div>"}'
        f'<div class="section-h">Live-forward NAV <span class="hint">the paper book\'s actual track since the live seam, '
        'marked daily — the agentic firm going forward</span></div>'
        f'{live_nav_block(b)}'
        f'<div class="section-h">Scorecard <span class="hint">paper-trade skill vs benchmark TR &amp; the real fund</span></div>'
        f'{scorecard_block(b)}'
        f'<div class="section-h">Fact sheet <span class="hint">latest daily snapshot</span></div>'
        f'{factsheet_block(b)}'
        f'<div class="section-h">Trade register <span class="hint">the blotter — every paper trade, audit-grade</span></div>'
        f'{trade_register(b)}'
        f'</div>')

# ───────────────────────────────────────────── LIVE-FORWARD round (the LLM FMs take the seat)
LF_CSS = """
<style>
.lf-intro{color:#9fb0c0;font-size:13px;line-height:1.55;max-width:1000px;margin:2px 0 16px}
.lf-cio{background:#0d1b2acc;border:1px solid #1e3a5f;border-left:3px solid var(--acc);border-radius:10px;padding:14px 16px;margin:0 0 18px}
.lf-cio .lbl{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#38bdf8;margin-bottom:6px}
.lf-fv{font-size:14px;line-height:1.6;color:#e6edf3}
.lf-sub{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#7d8ea0;margin:12px 0 4px}
.lf-risk li{color:#fca5a5;font-size:12.5px;line-height:1.5;margin:3px 0}
.lf-cross li{color:#a7c0d8;font-size:12.5px;line-height:1.5;margin:3px 0}
.lf-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px}
.lf-card{background:#0c1622;border:1px solid #1c2c40;border-radius:10px;padding:14px 16px}
.lf-h{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.lf-h b{font-size:15px;color:#e6edf3}
.lf-stance{font-size:13px;color:#7dd3fc;font-style:italic;margin:2px 0 8px}
.lf-thesis{font-size:12.5px;line-height:1.55;color:#bcccdc;margin-bottom:10px}
.lf-vq{margin:8px 0}
.lf-dev{width:100%;border-collapse:collapse;font-size:12px;margin-top:5px}
.lf-dev th{color:#7d8ea0;font-weight:600;text-align:left;padding:2px 6px;border-bottom:1px solid #1c2c40}
.lf-dev td{padding:2px 6px;border-bottom:1px solid #14202e;color:#cdd9e5}
.lf-bets{margin:10px 0 4px}
.lf-bet{font-size:12px;line-height:1.45;color:#bcccdc;padding:5px 0;border-top:1px solid #14202e}
.lf-bet b{color:#e6edf3}
.lf-th{color:#9fb0c0}
.lf-fx{color:#d99a2b}
.pill2{background:#1e3a5f;color:#7dd3fc;border-radius:4px;padding:1px 6px;font-size:10.5px}
.lf-guard{font-size:11.5px;color:#7d8ea0;margin-top:8px;border-top:1px solid #14202e;padding-top:6px}
</style>"""


def load_round():
    """The latest live-forward round (round_latest.json) + each scheme's pre-registered theses
    (prereg.jsonl, this round only). Returns None if no round has run yet (site degrades gracefully)."""
    p = AMC / "live" / "round_latest.json"
    if not p.exists():
        return None
    try:
        rd = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    asof = str(rd.get("asof"))
    name2s = {s.get("scheme"): s for s in rd.get("schemes", [])}
    for s in rd.get("schemes", []):
        s["_prereg"] = []
    for pf in BOOKS.glob("*/*/prereg.jsonl"):
        try:
            rows = [json.loads(l) for l in pf.read_text(encoding="utf-8").splitlines() if l.strip()]
        except Exception:
            continue
        rows = [r for r in rows if str(r.get("date")) == asof and r.get("decided_by") == "LLM-FM"]
        if not rows:
            continue
        sch = None
        bj = pf.parent / "book.json"
        if bj.exists():
            try:
                sch = json.loads(bj.read_text(encoding="utf-8")).get("scheme")
            except Exception:
                sch = None
        s = name2s.get(sch)
        if s is not None:
            s["_prereg"] = sorted(rows, key=lambda r: -float(r.get("target_pct") or 0))
    return rd


def _lf_dev_row(d):
    delta = d.get("delta_pct") or 0
    col = "#16a34a" if delta > 0 else "#dc2626"
    return (f'<tr><td><b>{esc(d.get("sym"))}</b></td><td class="muted">{esc((d.get("name") or "")[:22])}</td>'
            f'<td style="text-align:right">{(d.get("llm_pct") or 0):.1f}%</td>'
            f'<td style="text-align:right" class="muted">{(d.get("quant_pct") or 0):.1f}%</td>'
            f'<td style="text-align:right;color:{col}">{"+" if delta>0 else ""}{delta:.1f}</td></tr>')


def lf_scheme_card(s):
    vq = s.get("vs_quant") or {}
    devs = (vq.get("top_deviations") or [])[:6]
    dev_tbl = ""
    if devs:
        dev_tbl = ('<table class="lf-dev"><thead><tr><th>Name</th><th></th>'
                   '<th style="text-align:right">LLM</th><th style="text-align:right">Rules</th>'
                   '<th style="text-align:right">&Delta;</th></tr></thead><tbody>'
                   + "".join(_lf_dev_row(d) for d in devs) + '</tbody></table>')
    prereg = s.get("_prereg") or []
    bets = ""
    if prereg:
        bets = '<div class="lf-bets"><div class="lf-sub">Pre-registered bets — thesis &amp; falsifier (anti-hindsight)</div>' + "".join(
            f'<div class="lf-bet"><b>{esc(b.get("sym"))}</b> <span class="pill2">{esc(b.get("play_type"))}</span> '
            f'<span class="muted">{float(b.get("target_pct") or 0):.1f}%</span><br>'
            f'<span class="lf-th">{esc(b.get("thesis"))}</span><br>'
            f'<span class="lf-fx">&#10007; wrong if: {esc(b.get("falsification"))}</span></div>'
            for b in prereg[:6]) + '</div>'
    g = "; ".join(s.get("guardrail_notes") or [])
    return (f'<div class="lf-card">'
            f'<div class="lf-h"><b>{esc(s.get("scheme"))}</b>'
            f'<span class="tag">{s.get("n_holdings")} names · {s.get("deployed_pct")}% deployed · '
            f'{s.get("n_trades")} trades · turnover {s.get("turnover_pct")}%</span></div>'
            f'<div class="lf-stance">{esc(s.get("stance"))}</div>'
            f'<div class="lf-thesis">{esc(s.get("book_thesis"))}</div>'
            f'<div class="lf-vq"><span class="lf-sub">vs the rules-FM baseline</span> '
            f'<span class="muted">{vq.get("n_llm_only","?")} LLM-only · {vq.get("n_quant_only","?")} rules-only names</span>'
            f'{dev_tbl}</div>'
            f'{bets}'
            + (f'<div class="lf-guard">&#9881; guardrail: {esc(g)}</div>' if g else '')
            + '</div>')


def live_forward_tab(rd):
    cio = rd.get("cio") or {}
    risk = "".join(f'<li>{esc(r)}</li>' for r in (cio.get("risk_flags") or []))
    cross = "".join(f'<li>{esc(c)}</li>' for c in (cio.get("cross_scheme_notes") or []))
    cio_box = ""
    if cio:
        cio_box = (f'<div class="lf-cio"><div class="lbl">CIO — firm review of this round</div>'
                   f'<div class="lf-fv">{esc(cio.get("firm_view"))}</div>'
                   + (f'<div class="lf-sub">&#9888; Risk flags</div><ul class="lf-risk">{risk}</ul>' if risk else "")
                   + (f'<div class="lf-sub">Cross-scheme breadth</div><ul class="lf-cross">{cross}</ul>' if cross else "")
                   + '</div>')
    cards = "".join(lf_scheme_card(s) for s in rd.get("schemes", []))
    return (LF_CSS +
            '<div class="section-h">Live-Forward — the LLM fund managers take the seat '
            f'<span class="hint">as of {esc(rd.get("asof"))} · from the seam on, the agents decide; a deterministic guardrail enforces the mandate</span></div>'
            '<div class="lf-intro">The historical track was built by the deterministic <b>rules-FM</b>. From the latest data '
            'date forward, the <b>LLM fund-manager agents</b> read their desk (the inherited book + a candidate universe with '
            'validated signals + the rules-FM target as a reference) and set a genuine <b>conviction</b> target book; a '
            'deterministic guardrail then clips to the mandate / liquidity / equity-floor. Every bet is <b>pre-registered with a '
            'falsifier</b> (anti-hindsight), it is <b>paper-money only</b> with no look-ahead, and the committed audit trail '
            'carries <b>no licensed analyst values</b>. Skill will be scored — once next month\'s data lands — by whether the '
            'pre-registered theses played out, via the Fundamental Law (IC&middot;&radic;breadth&middot;TC).</div>'
            f'{cio_box}'
            f'<div class="lf-grid">{cards}</div>')


def build():
    org = json.loads((AMC / "org.json").read_text(encoding="utf-8"))
    ctx = json.loads((AMC / "context.json").read_text(encoding="utf-8"))
    books = load_books()          # the per-scheme paper-trading books (NAV / fact-sheet / blotter / scorecard)
    round_doc = load_round()      # the latest live-forward LLM round (None until the first round has run)
    cio = org.get("cio", {}) or {}
    mp = cio.get("market_pulse", {}) or {}
    asof = org.get("data_asof") or ctx.get("data_asof")
    ca = ctx.get("analysts", {}) or {}
    cf = ctx.get("fund_managers", {}) or {}
    analysts = org.get("analysts", [])
    fms = org.get("fund_managers", [])

    # ── computed market tape (real numbers, the "energy") ──
    arm_vals = [v.get("arm_ew") for v in ca.values() if isinstance(v.get("arm_ew"), (int, float))]
    mean_arm = round(sum(arm_vals) / len(arm_vals), 1) if arm_vals else None
    a_bull = sum(1 for a in analysts if str(a.get("stance")).lower() in ("bullish", "constructive"))
    a_caut = sum(1 for a in analysts if str(a.get("stance")).lower() in ("cautious", "bearish"))
    fm_on = sum(1 for f in fms if str(f.get("stance")).lower() == "risk-on")
    fm_def = sum(1 for f in fms if str(f.get("stance")).lower() == "defensive")
    n_pitch = sum(len(a.get("pitches", [])) for a in analysts)
    n_take = sum(len(f.get("pitches_taken", [])) for f in fms)
    n_pass = sum(len(f.get("pitches_declined", [])) for f in fms)
    n_esc = len(org.get("escalations", []))
    n_rule = len(cio.get("rulings", []))

    tape = (
        f'<div class="row"><span class="k">Street ARM</span> {arm_gauge(mean_arm)} '
        f'<span class="muted">mean across {len(arm_vals)} sector desks</span></div>'
        f'<div class="row"><span class="k">Analyst stance</span> '
        f'<span class="pill" style="background:#16a34a1a;color:#16a34a">{a_bull} bullish</span>'
        f'<span class="pill" style="background:#d977061a;color:#d97706">{a_caut} cautious</span>'
        f'<span class="muted">of {len(analysts)} desks</span></div>'
        f'<div class="row"><span class="k">FM stance</span> '
        f'<span class="pill" style="background:#16a34a1a;color:#16a34a">{fm_on} risk-on</span>'
        f'<span class="pill" style="background:#d977061a;color:#d97706">{fm_def} defensive</span>'
        f'<span class="muted">of {len(fms)} mandates</span></div>'
        f'<div class="row"><span class="k">Deal flow</span> <b>{n_pitch}</b> pitched '
        f'<span class="muted">→</span> <b style="color:#16a34a">{n_take}</b> taken / <b style="color:#dc2626">{n_pass}</b> passed '
        f'<span class="muted">→</span> <b style="color:#d97706">{n_esc}</b> escalated, <b style="color:#38bdf8">{n_rule}</b> ruled</div>')

    # ── the floor: FM tiles grouped by division (no desk falls through — leftovers go to "Other") ──
    fm_by_key = {f.get("key"): f for f in fms}
    floor = ""
    rendered = set()
    divisions = DIVISION_ORDER + [d for d in {v.get("division") for v in cf.values()} if d and d not in DIVISION_ORDER]
    for dv in divisions:
        keys = [k for k in fm_by_key if cf.get(k, {}).get("division") == dv]
        if not keys:
            continue
        rendered.update(keys)
        tiles = "".join(fm_tile(fm_by_key[k], cf.get(k, {})) for k in keys)
        floor += f'<div class="divh">{esc(dv)}<span class="cnt">{len(keys)} desk{"s" if len(keys)!=1 else ""}</span></div><div class="tiles">{tiles}</div>'
    leftover = [k for k in fm_by_key if k not in rendered]
    if leftover:
        tiles = "".join(fm_tile(fm_by_key[k], cf.get(k, {})) for k in leftover)
        floor += f'<div class="divh">Other<span class="cnt">{len(leftover)} desk{"s" if len(leftover)!=1 else ""}</span></div><div class="tiles">{tiles}</div>'

    analyst_tiles = "".join(analyst_tile(a, ca.get(a.get("key"), {})) for a in analysts)

    # ── hidden detail blocks for the modal ──
    details = "".join(f'<div id="d-analyst-{esc(a.get("key"))}">{analyst_detail(a, ca.get(a.get("key"), {}))}</div>' for a in analysts)
    details += "".join(f'<div id="d-fm-{esc(f.get("key"))}">{fm_detail(f, cf.get(f.get("key"), {}))}</div>' for f in fms)
    details += f'<div id="d-cio">{cio_detail(cio)}</div>'

    # ── kanban ──
    kan = (
        f'<div class="kcol c1"><div class="kcol-h">① Analyst pitches <span class="num">{n_pitch}</span></div>'
        f'<div class="kbody">{kanban_pitches(org)}</div></div>'
        f'<div class="kcol c2"><div class="kcol-h">② Fund-manager decisions <span class="num">{n_take+n_pass}</span></div>'
        f'<div class="kbody">{kanban_decisions(org, cf)}</div></div>'
        f'<div class="kcol c3"><div class="kcol-h">③ CIO rulings <span class="num">{n_esc+n_rule}</span></div>'
        f'<div class="kbody">{kanban_rulings(org)}</div></div>')

    # ── schemes & books tab: overview table + one hidden panel per scheme ──
    n_books = len(books)
    schemes_panels = "".join(scheme_panel(b) for b in books)
    schemes_tab = (
        '<div id="schemes-back" class="scbacklist" style="display:none">'
        '<span class="clr" onclick="schemesHome()">← all schemes</span></div>'
        '<div id="schemes-overview">'
        '<div class="section-h">Digital firms &amp; schemes <span class="hint">per-AMC paper-trading firms — pick a firm, '
        'then click a scheme for its scorecard, fact sheet &amp; blotter</span></div>'
        f'{firms_view(books)}</div>'
        f'{schemes_panels}')

    # ── live-forward tab (only once the first LLM round has run) ──
    lf_btn = (f'<button class="tabbtn" data-tab="liveforward" onclick="showTab(\'liveforward\')">'
              f'Live-Forward · {esc(round_doc.get("asof"))}</button>') if round_doc else ""
    lf_view = (f'<div class="tabview" id="tab-liveforward">{live_forward_tab(round_doc)}</div>') if round_doc else ""

    gen = datetime.date(2026, 6, 26).isoformat()
    html_doc = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Vistas · Digital AMC</title><style>' + STYLE + '</style></head><body><div class="wrap">'
        # header
        f'<div class="bar"><div><h1>Vistas · <span style="color:var(--acc)">Digital AMC</span></h1>'
        f'<div class="sub-title">An agentic paper-trading firm on the terminal — {len(analysts)} sector analysts · '
        f'{len(fms)} fund managers across {len([d for d in divisions if any(cf.get(k,{}).get("division")==d for k in fm_by_key)])} divisions · 1 CIO</div></div>'
        f'<div style="text-align:right"><span class="tag">PAPER · {esc(asof)}</span>'
        f'<div class="sub-title">data as of {esc(asof)}</div></div></div>'
        # top tabs
        '<div class="tabs">'
        '<button class="tabbtn on" data-tab="floor" onclick="showTab(\'floor\')">Trading Floor</button>'
        f'<button class="tabbtn" data-tab="schemes" onclick="showTab(\'schemes\')">Firms &amp; Schemes · {n_books}</button>'
        f'{lf_btn}'
        '</div>'
        # ════ TAB 1: the floor ════
        '<div class="tabview on" id="tab-floor">'
        # command bar: conclusion + tape
        '<div class="cmd">'
        f'<div class="concl" onclick="openModal(\'d-cio\')"><div class="lbl">CIO — firm conclusion</div>'
        f'<div class="big">{esc(cio.get("conclusion"))}</div>'
        f'<div class="more">▸ click for the full house view, 3-lens pulse, rulings & allocation</div></div>'
        f'<div class="tape">{tape}</div></div>'
        # the floor
        '<div class="section-h">The Floor — fund-manager desks <span class="hint">click a desk for its book, pitches & skill stats</span></div>'
        f'{floor}'
        '<div class="section-h">Research Floor — sector analysts <span class="hint">ARM = analyst revision momentum, 0–100</span></div>'
        f'<div class="tiles">{analyst_tiles}</div>'
        # deal flow kanban
        '<div class="section-h" id="kanban">Deal Flow — pitch → decision → ruling '
        '<span class="hint">hover a card to trace the same stock across columns</span></div>'
        '<div class="flowbar"><input id="flowsearch" placeholder="🔎 trace a stock…" oninput="flowSearch()">'
        '<span class="focusnote" id="focusnote" style="display:none">showing focused cards · '
        '<span class="clr" onclick="clearFocus()">clear</span></span></div>'
        f'<div class="kan">{kan}</div>'
        '</div>'   # /tab-floor
        # ════ TAB 2: schemes & books ════
        f'<div class="tabview" id="tab-schemes">{schemes_tab}</div>'
        f'{lf_view}'
        # footer
        '<div class="foot"><b>How to read this.</b> An <b>experimental, paper-money</b> research firm: agents — '
        'sector analysts, fund managers, a CIO — read the terminal\'s real data and produce grounded views. '
        '<b>Every view cites the actual numbers</b> (ARM 0–100 analyst-revision momentum, net fund flows in ₹cr, '
        'growth, real fund IR/IC). Agents <b>synthesize</b> validated signals into judgment; they do not '
        'manufacture alpha. Skill is judged by the <b>Fundamental Law of Active Management</b> (IR = IC·√breadth·TC): '
        'analysts on IC, managers on IR (and the transfer-coefficient leak), the firm on breadth. '
        f'<b>Not investment advice.</b> Generated {esc(gen)} · data as of {esc(asof)}.</div>'
        '</div>'
        # modal overlay + hidden details
        '<div class="overlay" id="overlay" onclick="if(event.target===this)closeModal()"><div class="modal" id="modalbody"></div></div>'
        f'<div id="details" style="display:none">{details}</div>'
        '<script>' + SCRIPT + '</script></body></html>')

    (SITE / "index.html").write_text(html_doc, encoding="utf-8")
    print(f"[amc_site] wrote {SITE/'index.html'}  ({len(html_doc)//1024} KB; "
          f"{len(analysts)} analysts, {len(fms)} FMs, {n_pitch} pitches / {n_take+n_pass} decisions / {n_rule} rulings; "
          f"{len(books)} scheme book{'s' if len(books)!=1 else ''})")

if __name__ == "__main__":
    build()
