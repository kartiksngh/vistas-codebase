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
  console.log("ROTATION :", JSON.stringify(rot));
  console.log(`\nCONSOLE/PAGE ERRORS (${errs.length}):`);
  errs.slice(0, 20).forEach((e) => console.log("  - " + e));

  const ok = errs.length === 0
    && alloc.hasTabButton && alloc.hasPane
    && alloc.plotsWithTraces >= 1
    && alloc.consInAlloc && !alloc.consInMacro
    && rot.hasRotWord && rot.hasStockSel && rot.hasSubseg && rot.trailTraces >= 1;
  console.log("\n" + (ok
    ? "PASS: Asset-Allocator tab renders breadth in real Chromium, Consensus moved here (not in Macro), Rotation trail plot renders, 0 errors."
    : "FAIL: see above (0 errors + alloc tab w/ >=1 traced plot + consensus-in-allocator + rotation trail-plot rendered required)."));
  await browser.close(); server.close();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.log("PROBE THREW:", e.message); try { server.close(); } catch (x) {} process.exit(1); });
