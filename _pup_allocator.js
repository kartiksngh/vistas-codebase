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
    && dateNav.screenDn && dateNav.consDn && dateNav.screenDateChanged
    && rot.hasRotWord && rot.hasStockSel && rot.hasSubseg && rot.trailTraces >= 1;
  console.log("\n" + (ok
    ? "PASS: Asset-Allocator breadth renders, per-sector chart RE-PLOTS on dropdown change, Consensus moved here, Rotation trail renders, 0 errors."
    : "FAIL: see above (0 errors + alloc traced plot + per-sector RE-PLOT on change + consensus-in-allocator + rotation trail required)."));
  await browser.close(); server.close();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.log("PROBE THREW:", e.message); try { server.close(); } catch (x) {} process.exit(1); });
