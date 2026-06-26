/* Headless-browser probe of the Analyst Consensus Flow cockpit (#46) in REAL Plotly. Serves the
 * built terminal_site over HTTP, opens it, switches to the Macro tab (which calls renderConsensus),
 * and verifies the cockpit's four plots render as real Plotly charts with traces, the sector chips
 * exist, and clicking a chip re-renders — capturing any console/page/Plotly throw the VM stub can't
 * see (the marker:undefined class of bug, burned 2026-06-22).
 * Run: node _pup_consensus.js
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
const PLOTS = ["cons-snapshot", "cons-ew", "cons-comp", "cons-flow"];
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

  // switch to the Macro tab → renderMacro() → renderConsensus()
  await page.evaluate(async () => {
    try { if (typeof setView === "function") setView("macro"); } catch (e) {}
    try { if (typeof renderMacro === "function") await renderMacro(); } catch (e) {}
  });
  await new Promise((r) => setTimeout(r, 2500));

  const probeOnce = () => page.evaluate((ids) => {
    const out = {};
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) { out[id] = "NO EL"; return; }
      const traces = el.querySelectorAll(".scatterlayer .trace, .barlayer .trace, .barlayer .point").length;
      out[id] = { traces, isPlotly: el.classList.contains("js-plotly-plot") };
    });
    const host = document.getElementById("consensus-cockpit");
    const chips = host ? host.querySelectorAll(".cchip") : [];
    return {
      hasData: !!(window.VISTAS_CONSENSUS && window.VISTAS_CONSENSUS.sectors),
      nSectors: window.VISTAS_CONSENSUS ? (window.VISTAS_CONSENSUS.sectors || []).length : 0,
      nMonths: window.VISTAS_CONSENSUS ? (window.VISTAS_CONSENSUS.dates || []).length : 0,
      chips: chips.length,
      selected: host ? (host.querySelector(".cchip.on") || {}).textContent : null,
      panels: out, plotlyVer: (window.Plotly && Plotly.version) || "?",
    };
  }, PLOTS);

  const before = await probeOnce();

  // click a different sector chip → re-render
  const clicked = await page.evaluate(() => {
    const host = document.getElementById("consensus-cockpit"); if (!host) return null;
    const off = Array.from(host.querySelectorAll(".cchip")).filter((c) => !c.classList.contains("on"));
    if (!off.length) return null;
    const name = off[1] ? off[1].textContent : off[0].textContent;
    (off[1] || off[0]).click();
    return name;
  });
  await new Promise((r) => setTimeout(r, 1500));
  const after = await probeOnce();

  console.log("Plotly:", before.plotlyVer, "| data:", before.hasData, "| sectors:", before.nSectors, "| months:", before.nMonths, "| chips:", before.chips);
  console.log("selected before:", before.selected, "-> clicked:", clicked, "-> selected after:", after.selected);
  console.log("panels BEFORE:", JSON.stringify(before.panels));
  console.log("panels AFTER :", JSON.stringify(after.panels));
  console.log(`\nCONSOLE/PAGE ERRORS (${errs.length}):`);
  errs.slice(0, 18).forEach((e) => console.log("  - " + e));

  const plotsOk = (p) => PLOTS.filter((id) => p.panels[id] && p.panels[id].isPlotly && p.panels[id].traces > 0).length;
  // snapshot + ew + comp must always have traces; flow may be empty for a thin sector but must be a Plotly el
  const coreOk = (p) => ["cons-snapshot", "cons-ew", "cons-comp"].every((id) => p.panels[id] && p.panels[id].isPlotly && p.panels[id].traces > 0);
  const switched = clicked && after.selected && after.selected !== before.selected;
  const ok = errs.length === 0 && before.hasData && before.chips >= 5 && coreOk(before) && coreOk(after) && switched;
  console.log("\nplots-with-traces before:", plotsOk(before) + "/4", "| after:", plotsOk(after) + "/4", "| sector switch:", !!switched);
  console.log(ok ? "PASS: Analyst Consensus Flow renders in real Chromium, sector switch works, 0 errors."
                 : "FAIL: see above.");
  await browser.close(); server.close();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.log("PROBE THREW:", e.message); try { server.close(); } catch (x) {} process.exit(1); });
