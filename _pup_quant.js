/* Headless-browser probe of the REAL Quant & MI render (real Plotly). Serves the built
 * terminal_site over HTTP, opens it, navigates to Quant & MI for a symbol, and captures any
 * console/page error (incl. Plotly cleanData throws that a VM stub can't see). The VM stub
 * missed the marker:undefined bug once (burned 2026-06-22) — this is the gold-standard check.
 * Run: node _pup_quant.js [SYM]
 */
const http = require("http"), fs = require("fs"), path = require("path");
let pup; try { pup = require("puppeteer"); } catch (e) { console.log("puppeteer not installed:", e.message); process.exit(2); }
const SYM = process.argv[2] || "RELIANCE";
const ROOT = path.join(__dirname, "output", "terminal_site");
const MIME = { ".html": "text/html", ".js": "application/javascript", ".json": "application/json", ".css": "text/css" };
const server = http.createServer((req, res) => {
  let u = decodeURIComponent(req.url.split("?")[0]); if (u === "/") u = "/index.html";
  if (u === "/favicon.ico") { res.statusCode = 204; res.end(); return; }   // avoid a benign 404 the browser auto-requests
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
  const ignore = (u) => /favicon\.ico/.test(String(u || ""));   // browser-requested asset our tiny test server doesn't serve — not an app error
  page.on("console", (m) => { if (m.type() === "error" && !ignore(m.text())) errs.push("CONSOLE.error: " + m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERROR: " + e.message + "  @ " + String(e.stack || "").split("\n").slice(1, 4).map((s) => s.trim()).join("  <-  ")));
  page.on("response", (r) => { if (r.status() >= 400 && !ignore(r.url())) errs.push("HTTP " + r.status() + ": " + r.url()); });
  await page.goto(`http://localhost:${port}/index.html`, { waitUntil: "networkidle2", timeout: 45000 });
  await page.evaluate(async (sym) => {
    // QUANT_SYM is a module-scoped `let` (NOT a window property) — assign it directly in this
    // page-global eval, else the render silently uses the default symbol (burned 2026-06-25).
    try { QUANT_SYM = sym; } catch (e) {}
    try { if (typeof setView === "function") setView("quant"); } catch (e) {}
    try { QUANT_SYM = sym; } catch (e) {}
    try { if (typeof ensureQuant === "function") await ensureQuant(sym); } catch (e) {}
    try { if (typeof renderQuant === "function") await renderQuant(); } catch (e) {}
  }, SYM);
  await new Promise((r) => setTimeout(r, 2500));
  const probe = await page.evaluate(() => {
    const ids = ["plot-quant-returns", "plot-quant-rs", "plot-quant-own", "plot-quant-flow"];
    const out = {};
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) { out[id] = "NO EL"; return; }
      const svgTraces = el.querySelectorAll(".scatterlayer .trace, .barlayer .trace, .trace.bars").length;
      const note = el.querySelector(".empty-note");
      out[id] = { svgTraces, note: note ? note.textContent : null, isPlotly: el.classList.contains("js-plotly-plot") };
    });
    const body = document.getElementById("quant-body");
    const hasFlow = !!(body && /Smart-money flow/.test(body.innerHTML));
    const hasHolders = !!(body && /Mutual-fund ownership/.test(body.innerHTML));
    const flowEl = document.getElementById("plot-quant-flow");
    return { panels: out, bodyChars: body ? body.innerHTML.length : 0,
      hasSnapshot: !!(body && /Research snapshot/.test(body.innerHTML)),
      hasFlowPanel: hasFlow,
      hasHoldersPanel: hasHolders,
      flowRendered: hasFlow ? !!(flowEl && flowEl.classList.contains("js-plotly-plot")) : null,
      dims: Array.from(document.querySelectorAll("#quant-body .q-chip")).map((c) => c.textContent),
      plotlyVer: (window.Plotly && Plotly.version) || "?" };
  });
  console.log("Plotly version:", probe.plotlyVer, "| body chars:", probe.bodyChars, "| snapshot:", probe.hasSnapshot, "| dim chips:", probe.dims.join(","));
  console.log("smart-money flow panel:", probe.hasFlowPanel ? ("present, plot rendered=" + probe.flowRendered) : "absent (symbol not in funds universe)");
  console.log("fund-ownership panel:", probe.hasHoldersPanel ? "present" : "absent");
  console.log("panel probe:", JSON.stringify(probe.panels, null, 1));
  console.log(`\nCONSOLE/PAGE ERRORS (${errs.length}):`);
  errs.slice(0, 18).forEach((e) => console.log("  • " + e));
  // if the flow panel is present it MUST have rendered as a real Plotly chart (no swallowed throw)
  const flowOk = !probe.hasFlowPanel || probe.flowRendered === true;
  const ok = errs.length === 0 && probe.hasSnapshot && probe.bodyChars > 500 && flowOk;
  console.log("\n" + (ok ? "PASS: Quant & MI renders in real Chromium with no errors (incl. smart-money flow)." : "FAIL: see above."));
  await browser.close(); server.close();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.log("PROBE THREW:", e.message); try { server.close(); } catch (x) {} process.exit(1); });
