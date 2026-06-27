/* Headless-browser probe of the NEW Asset-Allocator tab (market breadth + moved Analyst Consensus
 * Flow) and the Screens-tab Rotation section (stock trail + portfolio centroids) in REAL Plotly.
 * These are runtime-injected / lazy-fetched UIs the VM-stub validator can't exercise, so this catches
 * the marker:undefined-class throw that blanks a panel into "—" (burned 2026-06-22).
 * Run: node _pup_allocator.js   (after a --no-push build, so output/terminal_site has the new JS)
 */
const http = require("http"), fs = require("fs"), path = require("path");
let pup; try { pup = require("puppeteer"); } catch (e) { console.log("puppeteer not installed:", e.message); process.exit(2); }
const ROOT = path.join(__dirname, "output", "terminal_site");
const MIME = { ".html": "text/html", ".js": "application/javascript", ".json": "application/json", ".css": "text/css" };
const server = http.createServer((req, res) => {
  let u = decodeURIComponent(req.url.split("?")[0]); if (u === "/") u = "/index.html";
  if (u === "/favicon.ico") { res.statusCode = 204; res.end(); return; }
  const fp = path.join(ROOT, u);
  fs.readFile(fp, (e, d) => {
    if (e) { res.statusCode = 404; res.end("nf"); return; }
    res.setHeader("content-type", MIME[path.extname(fp)] || "text/plain"); res.end(d);
  });
});
(async () => {
  await new Promise((r) => server.listen(0, r));
  const port = server.address().port;
  const browser = await pup.launch({ headless: "new", args: ["--no-sandbox", "--disable-setuid-sandbox"] });
  const page = await browser.newPage();
  const errs = [];
  const ignore = (u) => /favicon\.ico/.test(String(u || ""));
  page.on("console", (m) => { if (m.type() === "error" && !ignore(m.text())) errs.push("CONSOLE.error: " + m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERROR: " + e.message + "  @ " + String(e.stack || "").split("\n").slice(1, 4).map((s) => s.trim()).join("  <-  ")));
  page.on("response", (r) => { if (r.status() >= 400 && !ignore(r.url())) errs.push("HTTP " + r.status() + ": " + r.url()); });
  await page.goto(`http://localhost:${port}/index.html`, { waitUntil: "networkidle2", timeout: 45000 });

  // ---- 1) ASSET ALLOCATOR tab: breadth + moved consensus ----
  await page.evaluate(async () => {
    try { if (typeof setView === "function") setView("allocator"); } catch (e) {}
    try { if (typeof renderAllocator === "function") await renderAllocator(); } catch (e) {}
  });
  await new Promise((r) => setTimeout(r, 3000));
  const alloc = await page.evaluate(() => {
    const pane = document.getElementById("view-allocator");
    const tabBtn = Array.from(document.querySelectorAll("#tabs *")).find((e) => /asset allocator/i.test(e.textContent || ""));
    const plots = pane ? Array.from(pane.querySelectorAll(".js-plotly-plot")) : [];
    const withTraces = plots.filter((el) => el.querySelectorAll(".scatterlayer .trace, .barlayer .trace, .barlayer .point, .lines .js-line").length > 0).length;
    const cons = document.getElementById("consensus-cockpit");
    const consInAlloc = !!(cons && pane && pane.contains(cons));
    const consInMacro = !!(cons && document.getElementById("view-macro") && document.getElementById("view-macro").contains(cons));
    return {
      hasTabButton: !!tabBtn, hasPane: !!pane,
      hasBreadthData: !!(window.VISTAS_BREADTH && window.VISTAS_BREADTH.market),
      nPlots: plots.length, plotsWithTraces: withTraces,
      consInAlloc, consInMacro,
    };
  });

  // ---- 1a) TIME-NAV: the breadth "≥m% broke out" screen + the Consensus snapshot must have a date
  //          slider, and dragging the screen slider must show a historical "as of" date.
  const dateNav = await page.evaluate(async () => {
    const screenDn = !!document.querySelector("#ab-screen-dn .dn-sl");
    const consDn = !!document.querySelector("#cons-snap-dn .dn-sl");
    const sl = document.querySelector("#ab-screen-dn .dn-sl");
    let screenDateChanged = false;
    if (sl) {
      const before = (document.querySelector(".ab-screen-head") || {}).textContent || "";
      sl.value = String(Math.max(0, (parseInt(sl.max, 10) || 0) - 24));   // ~2 years back (populated breadth)
      sl.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 450));
      const after = (document.querySelector(".ab-screen-head") || {}).textContent || "";
      screenDateChanged = /historical/.test(after) && after !== before;
    }
    return { screenDn, consDn, screenDateChanged };
  });

  // ---- 1a2) CONSENSUS flow chart must now be the 3-component DECOMPOSITION (price · inflow · net-active),
  //           i.e. >=2 bar traces (not the old single net-flow bar), and a sector switch must keep it populated.
  const consFlow = await page.evaluate(async () => {
    const fire = (el, ev) => el && el.dispatchEvent(new Event(ev, { bubbles: true }));
    const cf = document.getElementById("cons-flow");
    const barTraces = () => cf ? cf.querySelectorAll(".barlayer .trace").length : 0;
    const before = barTraces();
    // switch the consensus sector chip (2nd chip) and confirm the decomposition still renders
    const chips = Array.from(document.querySelectorAll(".cchip"));
    if (chips.length > 1) { chips[1].click(); }
    await new Promise((r) => setTimeout(r, 500));
    const after = barTraces();
    const titles = Array.from(document.querySelectorAll("#consensus-cockpit .ctitle")).map((e) => e.textContent || "").join(" | ");
    return { before, after, decompTitle: /decomposition/i.test(titles) };
  });

  // ---- 1b) PER-SECTOR breadth chart must RE-PLOT on dropdown change (the innerHTML=""/Plotly.react
  //          bug: blank on the 2nd draw). Change SECTOR + METRIC and confirm the plot still has traces.
  const secChange = await page.evaluate(async () => {
    const fire = (el, ev) => el && el.dispatchEvent(new Event(ev, { bubbles: true }));
    const sel = document.getElementById("ab-sec-sel");
    const met = document.getElementById("ab-sec-metric");
    const plot = document.getElementById("plot-ab-sector");
    const traces = () => plot ? plot.querySelectorAll(".scatterlayer .trace, .scatterlayer .points path, .lines .js-line").length : 0;
    const before = traces();
    // switch to a different sector (2nd option) and a different metric, as a user would
    if (sel && sel.options.length > 1) { sel.selectedIndex = Math.min(1, sel.options.length - 1); fire(sel, "change"); }
    await new Promise((r) => setTimeout(r, 700));
    if (met) { met.value = "pct_above_50dma"; fire(met, "change"); }
    await new Promise((r) => setTimeout(r, 700));
    const afterA = traces();
    // and back to % above 200-DMA (the reported case) on yet another sector
    if (sel && sel.options.length > 2) { sel.selectedIndex = 2; fire(sel, "change"); }
    if (met) { met.value = "pct_above_200dma"; fire(met, "change"); }
    await new Promise((r) => setTimeout(r, 800));
    const afterB = traces();
    return { before, afterA, afterB, sector: sel ? sel.value : null };
  });

  // ---- 1c) SECTOR REL-PERF chart (EW/FF vs NIFTY 500 + 500 EW-vs-cap): >=2 line traces, and a horizon
  //          toggle must keep it populated (rebased redraw).
  const relPerf = await page.evaluate(async () => {
    const rp = document.getElementById("plot-ab-relperf");
    const tr = () => rp ? rp.querySelectorAll(".scatterlayer .trace, .scatterlayer .lines").length : 0;
    const before = tr();
    const btn = document.querySelector("#ab-rp-hz .fs-lvl[data-hz='0']");   // MAX
    if (btn) { btn.click(); }
    await new Promise((r) => setTimeout(r, 450));
    const afterMax = tr();
    return { before, afterMax, hasSeg: !!document.getElementById("ab-rp-hz") };
  });

  // ---- 1d) OWNERSHIP & FLOW tab (#102): the waterfall decomposition plot (>=2 bar traces = price ·
  //          inflow · net-active), a snapshot table, AMC-switch redraw, and a date-slider snapshot.
  const own = await page.evaluate(async () => {
    try { if (typeof setView === "function") setView("ownership"); } catch (e) {}
    try { if (typeof renderOwnership === "function") renderOwnership(); } catch (e) {}
    await new Promise((r) => setTimeout(r, 900));
    const pane = document.getElementById("view-ownership");
    const hasTab = !!document.querySelector('#tabs [data-view="ownership"]');
    const W = window.VISTAS_WATERFALL;
    const hasData = !!(W && W.cube && W.months && W.months.length);
    const decomp = document.getElementById("plot-wf-decomp");
    const bars = () => decomp ? decomp.querySelectorAll(".barlayer .trace").length : 0;
    const bars0 = bars();
    const snapRows0 = document.querySelectorAll("#wf-snap table tbody tr").length;
    const amc = document.getElementById("wf-amc");
    if (amc && amc.options.length > 1) { amc.selectedIndex = 1; amc.dispatchEvent(new Event("change", { bubbles: true })); }
    await new Promise((r) => setTimeout(r, 500));
    const bars1 = bars();
    const sl = document.querySelector("#wf-snap-dn .dn-sl");
    let snapChanged = false;
    if (sl) {
      const before = (document.querySelector("#wf-snap .ab-screen-head") || {}).textContent || "";
      sl.value = String(Math.max(0, (parseInt(sl.max, 10) || 0) - 12));
      sl.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 400));
      const after = (document.querySelector("#wf-snap .ab-screen-head") || {}).textContent || "";
      snapChanged = after !== before;
    }
    const snapRows1 = document.querySelectorAll("#wf-snap table tbody tr").length;
    return { hasTab, hasPane: !!pane, hasData, bars0, bars1, snapRows0, snapRows1, hasSlider: !!sl, snapChanged };
  });

  // ---- 1d2) PIVOT DRILL-DOWN (#102 P3): root at All AMCs -> expand an AMC (lazy-fetch its scheme file)
  //           -> expand a scheme -> sector children -> click a sector and the chart refocuses to it.
  const pivot = await page.evaluate(async () => {
    const seg = (k) => (k.match(/::/g) || []).length;
    const q = (s) => Array.from(document.querySelectorAll(s));
    const amc = document.getElementById("wf-amc");
    if (amc) { amc.value = "__ALL__"; amc.dispatchEvent(new Event("change", { bubbles: true })); }
    await new Promise((r) => setTimeout(r, 450));
    const amcRows = q('#wf-snap tr.wf-row[data-key^="amc::"]');
    const nAmcRows = amcRows.length;
    if (amcRows[0]) amcRows[0].click();                 // expand first AMC -> triggers lazy fetch
    await new Promise((r) => setTimeout(r, 1100));
    const schRows = q('#wf-snap tr.wf-row[data-key^="sch::"]').filter((tr) => seg(tr.dataset.key) === 2);
    const nSch = schRows.length;
    if (schRows[0]) schRows[0].click();                 // expand first scheme -> sector children
    await new Promise((r) => setTimeout(r, 500));
    const secRows = q('#wf-snap tr.wf-row').filter((tr) => seg(tr.dataset.key) === 3);
    const nSec = secRows.length;
    let headHasSector = false, bars = 0, nStk = 0, headHasStock = false;
    if (secRows[0]) {
      const secName = ((secRows[0].querySelector("td") || {}).textContent || "").replace(/^[\s▸▾]+/, "").trim();
      secRows[0].click();                               // focus (+maybe expand) a scheme×sector leaf
      await new Promise((r) => setTimeout(r, 450));
      const head = (document.getElementById("wf-head") || {}).textContent || "";
      headHasSector = secName.length > 0 && head.indexOf(secName) >= 0;
      const decomp = document.getElementById("plot-wf-decomp");
      bars = decomp ? decomp.querySelectorAll(".barlayer .trace").length : 0;
      // P4 stock leaf: stocks may already be present (if that sector expanded); else expand an expandable one
      let stkRows = q('#wf-snap tr.wf-row').filter((tr) => seg(tr.dataset.key) === 5);
      if (!stkRows.length) {
        const expSec = q('#wf-snap tr.wf-row').filter((tr) => seg(tr.dataset.key) === 3).find((tr) => tr.dataset.exp === "1");
        if (expSec) { expSec.click(); await new Promise((r) => setTimeout(r, 450)); stkRows = q('#wf-snap tr.wf-row').filter((tr) => seg(tr.dataset.key) === 5); }
      }
      nStk = stkRows.length;
      if (stkRows[0]) {
        const stName = ((stkRows[0].querySelector("td") || {}).textContent || "").trim().split(" (")[0];
        stkRows[0].click();                             // focus a stock leaf
        await new Promise((r) => setTimeout(r, 350));
        const head2 = (document.getElementById("wf-head") || {}).textContent || "";
        headHasStock = stName.length > 2 && head2.indexOf(stName) >= 0;
      }
    }
    return { nAmcRows, nSch, nSec, headHasSector, bars, nStk, headHasStock };
  });

  // ---- 1d3) THEME LENS (#102 P4): flow-by-NSE-thematic-index panel — selector, decomposition chart
  //           (>=2 bar traces), a theme table, and the chart stays populated on a theme switch.
  const theme = await page.evaluate(async () => {
    const sel = document.getElementById("wf-theme-sel");
    const hasSel = !!(sel && sel.options.length >= 1);
    const plot = document.getElementById("plot-wf-theme");
    const bars0 = plot ? plot.querySelectorAll(".barlayer .trace").length : 0;
    const rows0 = document.querySelectorAll("#wf-theme-tbl table tbody tr").length;
    let bars1 = bars0;
    if (sel && sel.options.length > 1) {
      sel.selectedIndex = 1; sel.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 450));
      bars1 = plot ? plot.querySelectorAll(".barlayer .trace").length : 0;
    }
    return { hasSel, bars0, rows0, bars1 };
  });

  // ---- 1d4) CROSS-AMC CROWDING (#102 P4b): sector mode (inline cube) -> AMC table + chart; switch to
  //           stock mode (lazy per-stock file) -> AMC table + chart stay populated.
  const crowd = await page.evaluate(async () => {
    const seg = document.getElementById("wf-crowd-mode");
    const plot = document.getElementById("plot-wf-crowd");
    const bars = () => plot ? plot.querySelectorAll(".barlayer .trace").length : 0;
    const trows = () => document.querySelectorAll("#wf-crowd-tbl table tbody tr").length;
    const secBars = bars(), secRows = trows();
    const stkBtn = seg ? seg.querySelector('[data-mode="stock"]') : null;
    let stkBars = 0, stkRows = 0, hasStkSel = false;
    if (stkBtn) {
      stkBtn.click();
      await new Promise((r) => setTimeout(r, 1200));     // lazy fetch of the per-stock crowd file
      hasStkSel = !!document.getElementById("wf-crowd-stk");
      stkBars = bars(); stkRows = trows();
    }
    return { secBars, secRows, hasStkSel, stkBars, stkRows };
  });

  // ---- 2) SCREEN tab: Rotation section (stock trail plot + centroid controls) ----
  await page.evaluate(async () => {
    try { if (typeof setView === "function") setView("screen"); } catch (e) {}
    try { if (typeof renderScreen === "function") await renderScreen(); } catch (e) {}
  });
  await new Promise((r) => setTimeout(r, 3500));
  const rot = await page.evaluate(() => {
    const txt = document.body.innerText || "";
    const hasRotWord = /rotation/i.test(txt);
    const rotEls = Array.from(document.querySelectorAll('[id*="rot" i], [class*="rot" i]')).length;
    // the real check: did the per-stock trail plot actually render Plotly traces?
    const trail = document.getElementById("plot-rot-stock");
    const trailTraces = trail ? trail.querySelectorAll(".scatterlayer .trace, .scatterlayer .points path").length : 0;
    const hasStockSel = !!document.getElementById("rot-stock-sel");
    const hasSubseg = !!document.getElementById("rot-subseg");
    return { hasRotWord, rotEls, trailTraces, hasStockSel, hasSubseg };
  });

  console.log("ALLOCATOR:", JSON.stringify(alloc));
  console.log("CONS-FLOW:", JSON.stringify(consFlow), "(decomposition: >=2 bar traces before/after sector switch + 'decomposition' title)");
  console.log("REL-PERF :", JSON.stringify(relPerf), "(>=2 line traces, survives horizon switch to MAX)");
  console.log("OWNERSHIP:", JSON.stringify(own), "(>=2 bar traces decomp + snapshot table rows + AMC-switch redraw + date-slider)");
  console.log("WF-PIVOT :", JSON.stringify(pivot), "(AMC -> schemes (lazy) -> sectors -> STOCKS -> click refocuses chart)");
  console.log("WF-THEME :", JSON.stringify(theme), "(NSE thematic-index lens: selector + >=2 bar traces + table rows + survives theme switch)");
  console.log("WF-CROWD :", JSON.stringify(crowd), "(cross-AMC crowding: sector AMC-table+chart + stock-mode lazy AMC-table+chart)");
  console.log("DATE-NAV :", JSON.stringify(dateNav), "(screen+consensus sliders + screen shows historical on drag)");
  console.log("SEC-CHANGE:", JSON.stringify(secChange), "(afterA/afterB must stay >=1 — the re-plot bug)");
  console.log("ROTATION :", JSON.stringify(rot));
  console.log(`\nCONSOLE/PAGE ERRORS (${errs.length}):`);
  errs.slice(0, 20).forEach((e) => console.log("  - " + e));

  const ok = errs.length === 0
    && alloc.hasTabButton && alloc.hasPane
    && alloc.plotsWithTraces >= 1
    && alloc.consInAlloc && !alloc.consInMacro
    && secChange.afterA >= 1 && secChange.afterB >= 1
    && consFlow.before >= 2 && consFlow.after >= 2 && consFlow.decompTitle
    && relPerf.before >= 2 && relPerf.afterMax >= 2 && relPerf.hasSeg
    && own.hasTab && own.hasPane && own.hasData && own.bars0 >= 2 && own.bars1 >= 2
    && own.snapRows0 >= 1 && own.hasSlider && own.snapChanged
    && pivot.nAmcRows >= 1 && pivot.nSch >= 1 && pivot.nSec >= 1 && pivot.headHasSector && pivot.bars >= 2
    && pivot.nStk >= 1 && pivot.headHasStock
    && theme.hasSel && theme.bars0 >= 2 && theme.rows0 >= 1 && theme.bars1 >= 2
    && crowd.secBars >= 2 && crowd.secRows >= 1 && crowd.hasStkSel && crowd.stkBars >= 2 && crowd.stkRows >= 1
    && dateNav.screenDn && dateNav.consDn && dateNav.screenDateChanged
    && rot.hasRotWord && rot.hasStockSel && rot.hasSubseg && rot.trailTraces >= 1;
  console.log("\n" + (ok
    ? "PASS: Asset-Allocator breadth renders, per-sector chart RE-PLOTS on dropdown change, Consensus moved here, Rotation trail renders, 0 errors."
    : "FAIL: see above (0 errors + alloc traced plot + per-sector RE-PLOT on change + consensus-in-allocator + rotation trail required)."));
  await browser.close(); server.close();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.log("PROBE THREW:", e.message); try { server.close(); } catch (x) {} process.exit(1); });
