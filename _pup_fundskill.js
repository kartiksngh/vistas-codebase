/* Headless-browser probe of the REAL Fund-Skill tab. Serves the built terminal_site, opens it in
 * Chromium, clicks to the Fund Skill view via setView (the REAL dispatch — catches the view-div
 * `hidden` bug the VM stub misses), and verifies the view is visible + scorecard/leaderboard/chart
 * actually render with 0 thrown errors. Run: node _pup_fundskill.js
 */
const http = require("http"), fs = require("fs"), path = require("path");
let pup; try { pup = require("puppeteer"); } catch (e) { console.log("puppeteer not installed:", e.message); process.exit(2); }
const ROOT = path.join(__dirname, "output", "terminal_site");
const MIME = { ".html": "text/html", ".js": "application/javascript", ".json": "application/json", ".css": "text/css" };
const server = http.createServer((req, res) => {
  let u = decodeURIComponent(req.url.split("?")[0]); if (u === "/") u = "/index.html";
  fs.readFile(path.join(ROOT, u), (e, d) => {
    if (e) { res.statusCode = 404; res.end("nf"); return; }
    res.setHeader("content-type", MIME[path.extname(u)] || "text/plain"); res.end(d);
  });
});
(async () => {
  await new Promise((r) => server.listen(0, r));
  const port = server.address().port;
  const browser = await pup.launch({ headless: "new", args: ["--no-sandbox", "--disable-setuid-sandbox"] });
  const page = await browser.newPage();
  const errs = [];
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/i.test(m.text())) errs.push("CONSOLE.error: " + m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERROR: " + e.message + "  @ " + String(e.stack || "").split("\n").slice(1, 3).map((s) => s.trim()).join("  <-  ")));
  page.on("response", (r) => { if (r.status() >= 400 && !/favicon/i.test(r.url())) errs.push("HTTP " + r.status() + ": " + r.url()); });
  await page.goto(`http://localhost:${port}/index.html`, { waitUntil: "networkidle2", timeout: 45000 });

  // navigate to Fund Skill the way a user does — click the real tab dispatch
  await page.evaluate(async () => { try { setView("fundskill"); } catch (e) {} try { if (typeof renderFundSkill === "function") await renderFundSkill(); } catch (e) {} });
  await new Promise((r) => setTimeout(r, 4500));   // lazy-fetch the top scheme JSON + draw charts
  // re-select a 2nd scheme from the leaderboard to exercise re-render
  await page.evaluate(async () => {
    const man = window.VISTAS_FUNDS_ATTR_MANIFEST || {}; const ks = Object.keys(man);
    if (ks.length > 1) { try { FUNDSKILL_SYM = ks[1]; FS_WIN = null; await renderFundSkill(); } catch (e) {} }
  });
  await new Promise((r) => setTimeout(r, 2500));

  // ---- WINDOW-RECOMPUTE PARITY: the JS recompute over the FULL window must reproduce the baked
  //      Python metrics (within rounding) — else a narrowed-window verdict can't be trusted. Also
  //      exercise a NARROWED window (recompute path) + assert the rolling vantage plots render.
  const parity = await page.evaluate(async () => {
    const man = window.VISTAS_FUNDS_ATTR_MANIFEST || {};
    const ks = Object.keys(man).slice(0, 6);
    const FIELDS = { excess_cagr: 6e-4, cagr_paper: 6e-4, cagr_bench: 6e-4, info_ratio: 6e-4,
      t_stat: 6e-4, tracking_error: 6e-4, ic_mean: 6e-4, ic_t: 6e-4, hit_rate_monthly: 6e-4,
      mag_hit: 6e-4, slugging: 6e-3, avg_win: 6e-5, avg_loss: 6e-5, sizing_edge_cum: 6e-4, sizing_drag_cagr: 6e-4, eff_n: 0.06,
      port_hit_cnt: 6e-4, port_hit_aum: 6e-4, port_slug_cnt: 6e-4, port_slug_aum: 6e-4 };
    const bad = [], vwarn = [];
    let checked = 0;
    for (const k of ks) {
      const f = await ensureFundsAttr(k); if (!f || !f.ts || f.ts.length < 24) continue;
      checked++;
      const m = fsComputeWindow(f.ts, 0, f.ts.length - 1);
      for (const fld in FIELDS) {
        const b = f[fld], j = m[fld];
        if (b == null) continue;                      // baked nulled (too-short fund) — skip
        if (j == null || !isFinite(j)) { bad.push(`${k}.${fld}: js=${j} vs py=${b}`); continue; }
        // tolerance = baked-rounding + a small magnitude-proportional term: metrics built from a
        // DIFFERENCE OF LARGE COMPOUNDED LEVELS (e.g. sizing_edge_cum) inherit rounding error that
        // scales with the level (~tens), so a flat absolute bound would false-flag them. 3e-4·|py|
        // still catches any real formula bug (those are off by a large fraction of the value).
        const tol = FIELDS[fld] + 3e-4 * Math.abs(b);
        if (Math.abs(j - b) > tol) bad.push(`${k}.${fld}: js=${j.toFixed(6)} vs py=${b} (Δ=${Math.abs(j - b).toExponential(1)}, tol=${tol.toExponential(1)})`);
      }
      // verdict ladder: the deterministic metrics above are the hard gate; the verdict also depends on
      // a block-bootstrap whose PRNG can't bit-match numpy, so a borderline flip is a WARNING not a fail.
      const v = fsVerdict(m, f.is_thematic);
      if (v.verdict !== f.verdict && f.n_months >= 24) vwarn.push(`${k}.verdict: js="${v.verdict}" vs py="${f.verdict}"`);
    }
    // exercise the NARROWED window (trailing 24m) on the top scheme, then read the rendered DOM
    const top = Object.keys(man).sort((a, b) => ((man[b] || {}).t_stat || -99) - ((man[a] || {}).t_stat || -99))[0];
    FUNDSKILL_SYM = top; FS_WIN = null; FS_VANT.cat = null; await renderFundSkill();
    const ts = (FUNDS_ATTR_DATA[top] || {}).ts || [];
    // VANTAGE CONSISTENCY: the fund's own line must lie inside its category peer band — the fund IS one of
    // the funds in each cross-section, so min ≤ fund ≤ max by construction; a violation means the JS
    // fund-line math (fsVantageSeries) diverged from the Python envelope basis (different smoothing/units).
    let bandChecked = 0, bandViol = 0;
    try {
      const env = await ensureEnvelopes(), ftop = FUNDS_ATTR_DATA[top], cat = ftop.sebi_category, ser = fsVantageSeries(ftop.ts);
      for (const metric of ["port_hit_aum", "port_slug_aum", "nav_bat", "nav_slug"]) {
        const band = env && env[cat] && env[cat][metric]; if (!band) continue;
        const idx = {}; band.dates.forEach((d, i) => { idx[d] = i; });
        for (let i = 0; i < ser.ym.length; i++) {
          const v = ser[metric][i]; if (v == null || !isFinite(v)) continue;
          const j = idx[ser.ym[i]]; if (j == null) continue;
          bandChecked++;
          if (v < band.min[j] - 0.6 || v > band.max[j] + 0.6) bandViol++;
        }
      }
    } catch (e) { bad.push("band-check threw: " + e.message); }
    // exercise a NARROWED window + a category/level toggle on the vantage panel
    let windowedOK = false;
    if (ts.length > 30) { FS_WIN = { i0: ts.length - 24, i1: ts.length - 1, preset: "" }; await renderFundSkill(); windowedOK = true; }
    return { checked, bad, vwarn, windowedOK, top, tsLen: ts.length, bandChecked, bandViol };
  });
  await new Promise((r) => setTimeout(r, 1800));

  // exercise the NEW month dropdown on the holdings table — pick an earlier month, confirm re-render
  const hold = await page.evaluate(() => {
    const sel = document.getElementById("fs-hold-month");
    const nrows = () => document.querySelectorAll("#fs-hold-table table.fund-hold tbody tr").length;
    if (!sel) return { hasSel: false, nOpts: 0, rowsLatest: 0, rowsOther: 0 };
    const nOpts = sel.options.length, rowsLatest = nrows();
    if (nOpts > 1) { sel.value = sel.options[0].value; if (sel.onchange) sel.onchange(); }
    return { hasSel: true, nOpts, rowsLatest, rowsOther: nrows() };
  });
  await new Promise((r) => setTimeout(r, 300));

  const probe = await page.evaluate(() => {
    const view = document.getElementById("view-fundskill");
    const body = document.getElementById("fundskill-body");
    const cum = document.getElementById("plot-fs-cum");
    const sec = document.getElementById("plot-fs-sector");
    const rot = document.getElementById("plot-fs-rotation");
    const conc = document.getElementById("plot-fs-conc");
    const isPlotly = (el) => !!el && el.classList.contains("js-plotly-plot");
    const pl = (id) => isPlotly(document.getElementById(id));
    return {
      viewHidden: view ? !!view.hidden : "NO VIEW",
      bodyChars: body ? body.innerHTML.length : 0,
      lbRows: body ? body.querySelectorAll("table.fs-lb tbody tr").length : 0,
      hasScorecard: body ? !!body.querySelector(".fs-badge") : false,
      cumTraces: cum ? cum.querySelectorAll(".scatterlayer .trace, .plot .trace, path.js-line").length : 0,
      cumIsPlotly: isPlotly(cum),
      // NEW portfolio panels — real-Plotly render (the VM stub can't see pie/stacked-area throws)
      hasPortfolio: body ? !!body.querySelector("#plot-fs-sector") : false,
      sectorPie: sec ? sec.querySelectorAll(".pielayer .slice, g.slice").length : -1,
      sectorIsPlotly: isPlotly(sec),
      rotIsPlotly: isPlotly(rot),
      rotTraces: rot ? rot.querySelectorAll(".scatterlayer .trace").length : -1,
      concIsPlotly: isPlotly(conc),
      bySectorRows: body ? body.querySelectorAll("details table.fund-hold tbody tr").length : 0,
      // NEW window-adaptive + vantage-point panels
      hasWinBar: body ? !!body.querySelector(".fs-winbar #fs-win-start") : false,
      hasWinNote: body ? !!body.querySelector(".fs-winnote") : false,      // shows when a window is active
      hasWinChip: body ? !!body.querySelector(".fs-winchip") : false,
      hasVantBar: body ? !!body.querySelector("#fs-vant-cat") : false,
      hasPortStats: body ? /Hit rate \(AUM/.test(body.innerHTML) : false,  // portfolio-level scorecard block
      hasCrowd: body ? !!body.querySelector(".fs-crowd") : false,          // per-fund crowd-alignment (D#1)
      hasMarket: body ? !!body.querySelector(".fs-market") : false,        // market-wide flow panel (D#1)
      hasActiveShare: body ? !!body.querySelector(".fs-as") : false,       // peer-relative active share (guarded)
      hasSurv: body ? !!body.querySelector(".fs-surv") : false,            // survivorship data-quality panel
      benchIndices: (window.VISTAS_BENCHMARK_MANIFEST ? Object.keys(window.VISTAS_BENCHMARK_MANIFEST).length : 0),
      hasBenchCmp: !!(document.getElementById("fundskill-bench-host") && /Active share vs benchmark/.test((document.getElementById("fundskill-bench-host") || {}).innerHTML || "")),
      benchTilt: (function () { const el = document.getElementById("plot-fb-tilt"); return el ? el.querySelectorAll(".barlayer .trace").length : 0; })(),
      vantBat: pl("plot-fs-vant-bat"), vantSlug: pl("plot-fs-vant-slug"),
      sym: (typeof FUNDSKILL_SYM !== "undefined" ? FUNDSKILL_SYM : null),
      manifest: (window.VISTAS_FUNDS_ATTR_MANIFEST ? Object.keys(window.VISTAS_FUNDS_ATTR_MANIFEST).length : 0),
    };
  });
  console.log("Fund Skill probe:", JSON.stringify(probe, null, 1));

  // exercise the NEW multi-fund SIDE-BY-SIDE compare: add a 2nd skill fund as a peer, confirm the
  // comparison table (>=2 rows), grouped sector-tilt chart (>=2 bar traces) and disagreement table render.
  const cmp = await page.evaluate(async () => {
    const man = window.VISTAS_FUNDS_ATTR_MANIFEST || {}; const ks = Object.keys(man);
    if (ks.length < 2) return { tested: false };
    const top = ks.slice().sort((a, b) => ((man[b] || {}).t_stat || -99) - ((man[a] || {}).t_stat || -99))[0];
    const peer = ks.find((k) => k !== top);
    FUNDSKILL_SYM = top; FS_WIN = null; FS_VANT.cat = null;
    FUNDS_CMP = { peers: [peer], slug: FUNDS_CMP.slug, weight: "ffmcap" };
    try { await renderFundSkill(); } catch (e) { return { tested: true, threw: e.message }; }
    await new Promise((r) => setTimeout(r, 1300));
    const host = document.getElementById("fundskill-compare-host");
    const tbl = host ? host.querySelector("table.cmp-tbl") : null;
    const tilt = document.getElementById("plot-cmp-tilt");
    return { tested: true,
      hasHost: !!host && host.style.display !== "none",
      hasTitle: host ? /SIDE BY SIDE/.test(host.innerHTML) : false,
      rows: tbl ? tbl.querySelectorAll("tbody tr").length : 0,
      chips: host ? host.querySelectorAll(".cmp-chip").length : 0,
      nTables: host ? host.querySelectorAll("table.cmp-tbl").length : 0,   // metrics + disagreement = 2
      tiltIsPlotly: !!(tilt && tilt.classList.contains("js-plotly-plot")),
      tiltTraces: tilt ? tilt.querySelectorAll(".barlayer .trace").length : 0 };
  });
  await new Promise((r) => setTimeout(r, 300));
  console.log("Multi-fund compare:", JSON.stringify(cmp));

  // exercise the FM ACTION SHORTLIST (#39): evidence-ranked trim/add candidates (decision-support). Structure
  // + discipline guards are the HARD gate (a given fund may legitimately have 0 candidates); also try a few
  // skill funds to find one with >=1 candidate so the table path is exercised on real data.
  const fmsl = await page.evaluate(async () => {
    const man = window.VISTAS_FUNDS_ATTR_MANIFEST || {}; const ks = Object.keys(man);
    let best = null;
    for (const k of ks.slice(0, 8)) {
      FUNDSKILL_SYM = k; if (typeof FS_WIN !== "undefined") FS_WIN = null;
      try { await renderFundSkill(); } catch (e) { return { threw: e.message, sym: k }; }
      await new Promise((r) => setTimeout(r, 450));
      const host = document.getElementById("fm-shortlist-host");
      const panel = host && host.closest("section");           // the caveat <details> is a SIBLING of host
      const grid = host && host.querySelector(".fm-shortlist-grid");
      const cols = host ? host.querySelectorAll(".fm-col-h").length : 0;
      const rows = host ? host.querySelectorAll(".fm-shortlist-grid table.gauge-tbl tbody tr").length : 0;
      const txt = (host && host.textContent) || "";            // the TABLE area (for the action-verb guard)
      const panelTxt = (panel && panel.textContent) || txt;    // whole panel incl the caveat <details>
      const r = {
        sym: k, present: !!host, hasGrid: !!grid, cols, rows,
        notLoading: !/Loading…/.test(txt),
        notBlankDash: txt.replace(/\s+/g, "") !== "—",
        // discipline guard on the TABLE only (the caveat legitimately NAMES "expected return / price target" to say it shows NONE)
        noActionVerbs: !/\b(buy now|sell now|target price|price target|expected return|upside)\b/i.test(txt),
        hasCaveat: /decision-support/i.test(panelTxt) && /does not size/i.test(panelTxt),
        hasFlowCaveat: /mildly contrarian/i.test(panelTxt),
      };
      if (!best || r.rows > best.rows) best = r;
      if (r.rows > 0) break;   // found a fund with candidates — enough to exercise the table path
    }
    return best || { present: false };
  });
  await new Promise((r) => setTimeout(r, 300));
  console.log("FM action shortlist (#39):", JSON.stringify(fmsl));

  // exercise a HOLDINGS-ONLY fund (passive/debt — no skill): must render its book + "holdings only" banner, 0 throws
  const ho = await page.evaluate(async () => {
    const man = window.VISTAS_FUNDS_HOLDONLY_MANIFEST || {};
    const ks = Object.keys(man);
    if (!ks.length) return { tested: false, n: 0 };
    FUNDSKILL_SYM = ks[0]; if (typeof FS_WIN !== "undefined") FS_WIN = null;
    try { await renderFundSkill(); } catch (e) { return { tested: true, threw: e.message, n: ks.length, slug: ks[0] }; }
    await new Promise((r) => setTimeout(r, 1300));
    const body = document.getElementById("fundskill-body");
    return { tested: true, n: ks.length, slug: ks[0],
      rows: body ? body.querySelectorAll("table.fund-hold tbody tr").length : 0,
      hasBanner: body ? /holdings only/i.test(body.innerHTML) : false,
      hasLeaderboard: body ? body.querySelectorAll("table.fs-lb tbody tr").length > 0 : false };
  });
  await new Promise((r) => setTimeout(r, 300));
  console.log("Holdings-only fund:", JSON.stringify(ho));
  console.log("\nWindow-recompute parity (JS full-window vs baked Python):",
    JSON.stringify({ schemesChecked: parity.checked, mismatches: parity.bad.length, windowExercised: parity.windowedOK }, null, 1));
  if (parity.bad.length) { console.log("  PARITY MISMATCHES:"); parity.bad.slice(0, 20).forEach((b) => console.log("   ✗ " + b)); }
  if (parity.vwarn && parity.vwarn.length) { console.log("  verdict warnings (bootstrap-RNG borderline, non-fatal):"); parity.vwarn.slice(0, 10).forEach((b) => console.log("   ~ " + b)); }
  console.log(`\nERRORS (${errs.length}):`); errs.slice(0, 12).forEach((e) => console.log("  • " + e));
  // portfolio panels must render too (pie + rotation stacked-area); a scheme with a portfolio block must show them
  console.log(`Vantage band-consistency (fund line inside its peer min–max): ${parity.bandChecked - parity.bandViol}/${parity.bandChecked} points OK, ${parity.bandViol} violations`);
  const portfolioOK = !probe.hasPortfolio || (probe.sectorIsPlotly && probe.sectorPie > 0 && probe.rotIsPlotly);
  const vantageOK = probe.vantBat && probe.vantSlug && probe.hasVantBar && probe.hasPortStats;
  const windowOK = probe.hasWinBar && probe.hasWinNote && probe.hasWinChip;   // after a 24m window was applied
  const parityOK = parity.checked >= 2 && parity.bad.length === 0;
  const bandOK = parity.bandChecked > 50 && parity.bandViol === 0;            // fund line lies inside its own peer band
  const holdOK = hold.hasSel && hold.nOpts > 1 && hold.rowsLatest > 0 && hold.rowsOther > 0;
  console.log(`Holdings month-dropdown: ${hold.nOpts} months, latest=${hold.rowsLatest} rows, other-month=${hold.rowsOther} rows (re-render ${holdOK ? "OK" : "FAILED"}).`);
  const holdonlyOK = !ho.tested || (!ho.threw && ho.rows > 0 && ho.hasBanner);
  console.log(`Holdings-only branch: ${ho.tested ? (holdonlyOK ? "OK (" + ho.rows + " rows, banner)" : "FAILED " + (ho.threw || "(rows=" + ho.rows + " banner=" + ho.hasBanner + ")")) : "no holdonly funds"}.`);
  const crowdOK = probe.hasCrowd && probe.hasMarket;   // D#1 fund-side panels present for a store fund
  console.log(`Crowd-alignment panel: ${probe.hasCrowd ? "present" : "ABSENT"}; market-flow panel: ${probe.hasMarket ? "present" : "ABSENT"}.`);
  console.log(`Active-share block: ${probe.hasActiveShare ? "present" : "absent (fund may lack peers)"}; survivorship data-quality panel: ${probe.hasSurv ? "present" : "ABSENT"}.`);
  console.log(`Benchmark indices: ${probe.benchIndices}; vs-benchmark compare: ${probe.hasBenchCmp ? "present, tilt traces=" + probe.benchTilt : "absent (fund may lack an equity book)"}.`);
  const survOK = probe.hasSurv;   // data-quality panel must embed once census+premium exist
  // benchmark-compare must render with a drawn tilt plot when benchmarks shipped (fund has an equity book)
  const benchCmpOK = probe.benchIndices === 0 || (probe.hasBenchCmp && probe.benchTilt > 0);
  console.log("vs-benchmark gate:", benchCmpOK ? "OK" : "FAILED");
  // multi-fund side-by-side: with a peer added, the host shows >=2 fund rows, a grouped tilt chart with
  // >=2 bar traces, and the disagreement table (2 cmp-tbl tables). Skipped only if <2 skill funds exist.
  const cmpOK = !cmp.tested || (!cmp.threw && cmp.hasHost && cmp.hasTitle && cmp.rows >= 2 && cmp.chips >= 2 && cmp.nTables >= 2 && cmp.tiltIsPlotly && cmp.tiltTraces >= 2);
  console.log("multi-fund compare gate:", cmpOK ? "OK" : ("FAILED " + (cmp.threw || JSON.stringify(cmp))));
  // FM action shortlist (#114): panel present, 3 columns (TRIM | ADD-MORE | ADD), not loading/blank, no action-verb/return language, caveats on-surface
  const fmOK = !!fmsl.present && !fmsl.threw && fmsl.hasGrid && fmsl.cols === 3 && fmsl.notLoading &&
               fmsl.notBlankDash && fmsl.noActionVerbs && fmsl.hasCaveat && fmsl.hasFlowCaveat;
  console.log("FM shortlist gate:", fmOK ? ("OK (best fund " + fmsl.sym + ", " + fmsl.rows + " candidate rows)") : ("FAILED " + JSON.stringify(fmsl)));
  const ok = errs.length === 0 && probe.viewHidden === false && probe.bodyChars > 400 &&
             probe.lbRows > 0 && probe.hasScorecard && probe.cumTraces > 0 && portfolioOK &&
             vantageOK && windowOK && parityOK && bandOK && holdOK && holdonlyOK && crowdOK && survOK && benchCmpOK && cmpOK && fmOK;
  console.log("\n" + (ok ? "PASS: Funds cockpit visible; scorecard + leaderboard + growth + PORTFOLIO + VANTAGE plots render; month-dropdown holdings table re-renders; holdings-only funds render their book; multi-fund side-by-side compare renders; window recompute & portfolio metrics match baked Python; fund line inside peer band; 0 throws."
                          : `FAIL: see above (portfolioOK=${portfolioOK} vantageOK=${vantageOK} windowOK=${windowOK} parityOK=${parityOK} bandOK=${bandOK} holdOK=${holdOK} holdonlyOK=${holdonlyOK} crowdOK=${crowdOK} survOK=${survOK} benchCmpOK=${benchCmpOK} cmpOK=${cmpOK} fmOK=${fmOK}).`));
  await browser.close(); server.close();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.log("PROBE THREW:", e.message); try { server.close(); } catch (x) {} process.exit(1); });
