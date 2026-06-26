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
"""

def build():
    org = json.loads((AMC / "org.json").read_text(encoding="utf-8"))
    ctx = json.loads((AMC / "context.json").read_text(encoding="utf-8"))
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
          f"{len(analysts)} analysts, {len(fms)} FMs, {n_pitch} pitches / {n_take+n_pass} decisions / {n_rule} rulings)")

if __name__ == "__main__":
    build()
