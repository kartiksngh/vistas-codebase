"""
amc_site.py — render the Digital AMC visual dashboard from the agentic org output.

Reads:
  output/_amc/org.json      (the workflow result: analysts -> FMs -> CIO + comm log)
  output/_amc/context.json  (real desk stats: sector ARM, FM median-IR + exemplars)
Writes:
  output/_amc/site/index.html   (self-contained, styled, lightly interactive)

Display-plane only. Run:  python -m vistas.amc_site
"""
from __future__ import annotations
import json, html, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AMC  = ROOT / "output" / "_amc"
SITE = AMC / "site"
SITE.mkdir(parents=True, exist_ok=True)

def esc(x):
    return html.escape(str(x if x is not None else ""))

STANCE_COLOR = {
    "bullish": "#16a34a", "risk-on": "#16a34a", "constructive": "#65a30d",
    "neutral": "#94a3b8", "balanced": "#94a3b8",
    "cautious": "#d97706", "defensive": "#d97706", "bearish": "#dc2626",
}
def stance_chip(s):
    c = STANCE_COLOR.get(str(s).lower(), "#94a3b8")
    return f'<span class="chip" style="background:{c}1a;color:{c};border:1px solid {c}55">{esc(s)}</span>'

def arm_color(v):
    try:
        v = float(v)
    except Exception:
        return "#94a3b8"
    # 0 red -> 50 grey -> 100 green
    if v >= 50:
        t = (v - 50) / 50; return f"rgb({int(148-148*t+22*t)},{int(163+9*t)},{int(184-184*t+74*t)})"
    t = v / 50; return f"rgb({int(220-72*t)},{int(38+125*t)},{int(38+146*t)})"

def arm_badge(v):
    if v is None:
        return ""
    return f'<span class="arm" style="background:{arm_color(v)}22;color:{arm_color(v)};border:1px solid {arm_color(v)}66">ARM {esc(v)}</span>'

CONV = {"high": "●●●", "medium": "●●○", "low": "●○○"}

def li(items):
    return "".join(f"<li>{esc(x)}</li>" for x in (items or []))

def analyst_card(a, ctx_a):
    arm_ew = ctx_a.get("arm_ew"); arm_ff = ctx_a.get("arm_ff"); cov = ctx_a.get("coverage_n")
    pitches = ""
    for p in a.get("pitches", []):
        to = ", ".join(p.get("to", []) or [])
        pitches += (
            f'<div class="pitch"><div class="pitch-h"><span class="act act-{esc(p.get("action"))}">{esc(p.get("action"))}</span> '
            f'<b>{esc(p.get("stock"))}</b> <span class="conv" title="conviction">{CONV.get(p.get("conviction"),"")}</span>'
            f'<span class="hz">{esc(p.get("horizon"))}</span><span class="to">→ {esc(to)}</span></div>'
            f'<div class="th">{esc(p.get("thesis"))}</div>'
            f'<div class="ev">{esc(p.get("evidence"))}</div></div>'
        )
    return f'''<div class="card analyst" data-name="{esc(a.get("name"))}">
      <div class="card-top"><div class="who">🔬 {esc(a.get("name"))} <span class="role">Analyst</span></div>{stance_chip(a.get("stance"))}</div>
      <div class="metrics">Coverage {esc(cov)} · Sector ARM <b style="color:{arm_color(arm_ew)}">{esc(arm_ew)}</b> EW / <b style="color:{arm_color(arm_ff)}">{esc(arm_ff)}</b> float-wt</div>
      <div class="headline">{esc(a.get("headline"))}</div>
      <div class="sub">Working on</div><ul class="wo">{li(a.get("working_on"))}</ul>
      <div class="sub">Pitches</div><div class="pitches">{pitches or '<div class="muted">—</div>'}</div>
      <div class="sub">Risks</div><ul class="risks">{li(a.get("risks"))}</ul>
    </div>'''

def fm_card(f, ctx_f):
    med_ir = (ctx_f or {}).get("median_ir"); n = (ctx_f or {}).get("n_funds")
    bench = (ctx_f or {}).get("benchmark")
    ex = ((ctx_f or {}).get("exemplars") or [{}])[0]
    taken = "".join(
        f'<div class="tk"><span class="act act-take">take</span> <b>{esc(t.get("stock"))}</b> '
        f'<span class="sz">{esc(t.get("size") or "")}</span> <span class="frm">from {esc(t.get("from"))}</span>'
        f'<div class="why">{esc(t.get("why"))}</div></div>' for t in f.get("pitches_taken", []))
    decl = "".join(
        f'<div class="tk decl"><span class="act act-decline">pass</span> <b>{esc(d.get("stock"))}</b>'
        f'<div class="why">{esc(d.get("why_not"))}</div></div>' for d in f.get("pitches_declined", []))
    esc_rows = "".join(
        f'<div class="esc-row"><span class="rsn rsn-{esc(e.get("reason"))}">{esc(e.get("reason"))}</span> {esc(e.get("topic"))} — <i>{esc(e.get("ask"))}</i></div>'
        for e in f.get("escalations", []))
    tilt = " · ".join(esc(t) for t in (f.get("book_tilt") or []))
    return f'''<div class="card fm" data-name="{esc(f.get("name"))}">
      <div class="card-top"><div class="who">📈 {esc(f.get("name"))} <span class="role">Fund Manager</span></div>{stance_chip(f.get("stance"))}</div>
      <div class="metrics">{esc(n)} peer funds · category median IR <b>{esc(med_ir)}</b> · bench {esc(bench)}<br>
        <span class="muted">exemplar {esc(ex.get("scheme"))}: IR {esc(ex.get("info_ratio"))}, IC {esc(ex.get("ic_mean"))} (t {esc(ex.get("ic_t"))}), TE {esc(ex.get("tracking_error"))}% — <i>{esc(ex.get("verdict"))}</i></span></div>
      <div class="headline">{esc(f.get("positioning"))}</div>
      <div class="sub">Book tilt</div><div class="tilt">{tilt or '—'}</div>
      <div class="sub">Pitches taken / passed</div><div class="taken">{taken or ''}{decl or ''}{'<div class="muted">—</div>' if not (taken or decl) else ''}</div>
      {'<div class="sub">Escalated to CIO</div>'+esc_rows if esc_rows else ''}
      <div class="sub">Investor experience</div><div class="exp">{esc(f.get("experience_note"))}</div>
    </div>'''

def comm_log(org):
    rows = []
    for a in org.get("analysts", []):
        for p in a.get("pitches", []):
            to = ", ".join(p.get("to", []) or [])
            rows.append(("pitch", a.get("name"), to, f'{p.get("action","").upper()} {p.get("stock")} — {p.get("thesis")}'))
    for e in org.get("escalations", []):
        rows.append(("escalation", e.get("from"), "CIO", f'({e.get("reason")}) {e.get("topic")} — {e.get("ask")}'))
    for r in (org.get("cio", {}) or {}).get("rulings", []):
        rows.append(("ruling", "CIO", r.get("on"), f'{r.get("ruling")} — {r.get("rationale")}'))
    out = ""
    for kind, frm, to, msg in rows:
        out += (f'<div class="msg msg-{kind}"><span class="k">{esc(kind)}</span>'
                f'<span class="rt"><b>{esc(frm)}</b> → <b>{esc(to)}</b></span>'
                f'<span class="mtxt">{esc(msg)}</span></div>')
    return out or '<div class="muted">No messages this session.</div>'

def sector_strip(ctx):
    cells = ""
    for k, v in (ctx.get("analysts") or {}).items():
        ew = v.get("arm_ew")
        cells += (f'<div class="scell" title="{esc(v.get("desk"))}: ARM {esc(ew)} EW / {esc(v.get("arm_ff"))} float-wt, '
                  f'{esc(v.get("coverage_n"))} stocks" style="background:{arm_color(ew)}22;border-color:{arm_color(ew)}66">'
                  f'<div class="snm">{esc(v.get("desk"))}</div><div class="sarm" style="color:{arm_color(ew)}">{esc(ew)}</div></div>')
    return cells

def build():
    org = json.loads((AMC / "org.json").read_text(encoding="utf-8"))
    ctx = json.loads((AMC / "context.json").read_text(encoding="utf-8"))
    cio = org.get("cio", {}) or {}
    mp = cio.get("market_pulse", {}) or {}
    asof = org.get("data_asof") or ctx.get("data_asof")
    ca = ctx.get("analysts", {}) or {}
    cf = ctx.get("fund_managers", {}) or {}

    analyst_cards = "".join(analyst_card(a, ca.get(a.get("key"), {})) for a in org.get("analysts", []))
    fm_cards = "".join(fm_card(f, cf.get(f.get("key"), {})) for f in org.get("fund_managers", []))
    alloc = "".join(
        f'<tr><td>{esc(x.get("mandate"))}</td><td>{stance_chip(x.get("stance"))}</td><td>{esc(x.get("note"))}</td></tr>'
        for x in cio.get("allocation", []))
    rulings = "".join(f'<li><b>{esc(r.get("on"))}:</b> {esc(r.get("ruling"))} <span class="muted">— {esc(r.get("rationale"))}</span></li>'
                      for r in cio.get("rulings", []))
    risks = "".join(f'<span class="rflag">⚠ {esc(x)}</span>' for x in cio.get("risk_flags", []))

    html_doc = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vistas · Digital AMC</title>
<style>
:root{{--bg:#0b0f17;--pnl:#121826;--pnl2:#0f1420;--bd:#1f2937;--fg:#e5e7eb;--mut:#7c8aa0;--acc:#38bdf8;--gold:#fbbf24}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:1320px;margin:0 auto;padding:18px}}
h1{{font-size:22px;margin:0}} .sub-title{{color:var(--mut);font-size:13px;margin-top:2px}}
.bar{{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--bd);padding-bottom:12px;margin-bottom:14px;flex-wrap:wrap;gap:8px}}
.tag{{background:var(--gold)1a;color:var(--gold);border:1px solid var(--gold)55;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:600}}
.concl{{background:linear-gradient(90deg,#0e1a2b,#101725);border:1px solid #24405e;border-left:4px solid var(--acc);padding:14px 16px;border-radius:10px;margin-bottom:16px}}
.concl .lbl{{color:var(--acc);font-weight:700;font-size:11px;letter-spacing:.06em;text-transform:uppercase}}
.section-h{{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin:22px 0 10px;border-bottom:1px solid var(--bd);padding-bottom:6px}}
.cio{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.panel{{background:var(--pnl);border:1px solid var(--bd);border-radius:10px;padding:14px}}
.pulse{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}}
.lens{{background:var(--pnl2);border:1px solid var(--bd);border-radius:8px;padding:10px}}
.lens .ln{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--acc);font-weight:700}}
.lens.gap .ln{{color:var(--gold)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}}
.card{{background:var(--pnl);border:1px solid var(--bd);border-radius:10px;padding:14px}}
.card.fm{{background:#10131f}}
.card-top{{display:flex;justify-content:space-between;align-items:center;gap:8px}}
.who{{font-weight:700;font-size:15px}} .role{{color:var(--mut);font-weight:500;font-size:11px;border:1px solid var(--bd);padding:1px 6px;border-radius:10px;margin-left:4px}}
.metrics{{color:var(--mut);font-size:12px;margin:8px 0;border-bottom:1px dashed var(--bd);padding-bottom:8px}}
.headline{{font-size:13.5px;margin:8px 0;color:#f1f5f9}}
.sub{{font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--mut);margin:10px 0 4px}}
ul{{margin:4px 0;padding-left:18px}} li{{margin:2px 0}}
.chip{{padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;text-transform:capitalize;white-space:nowrap}}
.arm{{padding:1px 6px;border-radius:5px;font-size:11px;font-weight:700}}
.pitch{{background:var(--pnl2);border:1px solid var(--bd);border-radius:7px;padding:8px;margin-bottom:6px}}
.pitch-h{{display:flex;align-items:center;gap:6px;flex-wrap:wrap}} .pitch-h b{{font-size:13px}}
.act{{font-size:10px;font-weight:700;text-transform:uppercase;padding:1px 6px;border-radius:4px;border:1px solid}}
.act-accumulate,.act-add,.act-take{{color:#16a34a;border-color:#16a34a55;background:#16a34a14}}
.act-reduce,.act-avoid,.act-decline{{color:#dc2626;border-color:#dc262655;background:#dc262614}}
.act-hold,.act-watch{{color:#d97706;border-color:#d9770655;background:#d9770614}}
.conv{{color:var(--gold);font-size:9px;letter-spacing:1px}} .hz{{color:var(--mut);font-size:10px;border:1px solid var(--bd);padding:0 5px;border-radius:4px}}
.to{{color:var(--acc);font-size:10px;margin-left:auto}}
.th{{font-size:12.5px;margin:3px 0}} .ev{{font-size:11px;color:var(--mut);font-family:ui-monospace,Menlo,monospace}}
.muted{{color:var(--mut)}} .tilt{{font-size:12px}}
.tk{{font-size:12px;margin:4px 0;padding-left:2px;border-left:2px solid #16a34a55;padding-left:8px}} .tk.decl{{border-left-color:#dc262655}}
.tk .why{{color:var(--mut);font-size:11px}} .sz{{font-size:10px;color:var(--gold)}} .frm{{font-size:10px;color:var(--acc)}}
.esc-row{{font-size:12px;margin:3px 0}} .rsn{{font-size:9px;font-weight:700;text-transform:uppercase;padding:1px 5px;border-radius:4px;border:1px solid #d9770655;color:#d97706;background:#d9770614}}
.exp{{font-size:12px;color:#cbd5e1}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}} td{{padding:5px 6px;border-bottom:1px solid var(--bd)}}
.rflag{{display:inline-block;background:#dc26261a;color:#fca5a5;border:1px solid #dc262655;padding:2px 8px;border-radius:6px;font-size:11.5px;margin:3px 4px 3px 0}}
.strip{{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px;margin-bottom:6px}}
.scell{{border:1px solid;border-radius:7px;padding:7px 8px}} .snm{{font-size:11px;color:#cbd5e1}} .sarm{{font-size:18px;font-weight:800}}
.log{{background:var(--pnl);border:1px solid var(--bd);border-radius:10px;padding:6px 12px;max-height:420px;overflow:auto}}
.msg{{display:grid;grid-template-columns:78px 190px 1fr;gap:8px;align-items:baseline;padding:6px 2px;border-bottom:1px solid #161c2a;font-size:12px}}
.msg .k{{font-size:9px;font-weight:700;text-transform:uppercase;color:var(--mut)}}
.msg-pitch .k{{color:#16a34a}} .msg-escalation .k{{color:#d97706}} .msg-ruling .k{{color:var(--acc)}}
.msg .rt{{color:#cbd5e1}} .mtxt{{color:var(--mut)}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:22px;border-top:1px solid var(--bd);padding-top:12px}}
@media(max-width:760px){{.cio,.pulse{{grid-template-columns:1fr}}.msg{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">

<div class="bar"><div><h1>Vistas · <span style="color:var(--acc)">Digital AMC</span></h1>
  <div class="sub-title">An agentic paper-trading firm running on the terminal — {len(org.get("analysts",[]))} sector analysts · {len(org.get("fund_managers",[]))} fund managers · 1 CIO</div></div>
  <div style="text-align:right"><span class="tag">PAPER · {esc(asof)}</span><div class="sub-title">data as of {esc(asof)}</div></div></div>

<div class="concl"><div class="lbl">CIO — firm conclusion</div><div style="margin-top:6px">{esc(cio.get("conclusion"))}</div></div>

<div class="section-h">Sector ARM pulse — analyst revision momentum (0–100, equal-weighted)</div>
<div class="strip">{sector_strip(ctx)}</div>

<div class="section-h">The CIO desk</div>
<div class="cio">
  <div class="panel"><div class="sub">House view</div><div class="headline">{esc(cio.get("house_view"))}</div>
    <div class="sub">3-lens market pulse <span class="muted">— the gaps between the lenses are the signal</span></div>
    <div class="pulse">
      <div class="lens"><div class="ln">Street (analysts / ARM)</div>{esc(mp.get("street"))}</div>
      <div class="lens"><div class="ln">Smart money (fund flows)</div>{esc(mp.get("smart_money"))}</div>
      <div class="lens"><div class="ln">Reward (price / quadrant)</div>{esc(mp.get("reward"))}</div>
      <div class="lens gap"><div class="ln">★ The gaps</div>{esc(mp.get("gaps"))}</div>
    </div></div>
  <div class="panel"><div class="sub">Allocation across mandates</div>
    <table>{alloc or '<tr><td class="muted">—</td></tr>'}</table>
    <div class="sub">Rulings</div><ul>{rulings or '<li class="muted">none</li>'}</ul>
    <div class="sub">Risk flags</div><div>{risks or '<span class="muted">none</span>'}</div>
    <div class="sub">Summary</div><div class="exp">{esc(cio.get("summary"))}</div></div>
</div>

<div class="section-h">Analyst desks — {len(org.get("analysts",[]))} sector specialists</div>
<div class="grid">{analyst_cards}</div>

<div class="section-h">Fund managers — one per mandate (real category skill stats shown)</div>
<div class="grid">{fm_cards}</div>

<div class="section-h">Communication log — pitches → decisions → escalations → rulings</div>
<div class="log">{comm_log(org)}</div>

<div class="foot">
<b>How to read this.</b> This is an <b>experimental, paper-money</b> research firm: agents — sector analysts,
fund managers, a CIO — read the terminal's real data and produce grounded views. <b>Every view cites the
actual numbers</b> (ARM 0–100 analyst-revision momentum, net fund flows in ₹cr, growth, real fund IR/IC).
Agents <b>synthesize</b> the validated signals into judgment; they do not manufacture alpha. Skill is judged
by the <b>Fundamental Law of Active Management</b> (IR = IC·√breadth·TC): analysts on IC, managers on IR (and
the transfer-coefficient leak), the firm on breadth. <b>Not investment advice.</b> Generated {esc(datetime.date(2026,6,26).isoformat())} · data as of {esc(asof)}.
</div>
</div></body></html>'''

    (SITE / "index.html").write_text(html_doc, encoding="utf-8")
    print(f"[amc_site] wrote {SITE/'index.html'}  ({len(html_doc)//1024} KB; "
          f"{len(org.get('analysts',[]))} analysts, {len(org.get('fund_managers',[]))} FMs)")

if __name__ == "__main__":
    build()
