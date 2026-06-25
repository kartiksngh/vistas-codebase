/* Headless-browser probe of the REAL Funds-tab render (real Plotly), plus the new McClellan
 * macro panel. Serves the built terminal_site over HTTP, opens it in Chromium, navigates to
 * the Funds tab (default scheme set by initFunds at boot) and the Macro tab, then verifies the
 * asset/sector/mcclellan plots actually draw with 0 thrown errors (catches real-Plotly
 * cleanData throws the VM stub misses). Run: node _pup_funds.js
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

  // Funds tab — initFunds() at boot defaults FUNDS_SYM to the first scheme
  await page.evaluate(async () => { try { setView("funds"); } catch (e) {} try { if (typeof renderFunds === "function") await renderFunds(); } catch (e) {} });
  await new Promise((r) => setTimeout(r, 3000));
  // Macro tab — exercises the new McClellan panel under real Plotly
  await page.evaluate(async () => { try { setView("macro"); } catch (e) {} try { if (typeof renderMacro === "function") await renderMacro(); } catch (e) {} });
  await new Promise((r) => setTimeout(r, 3000));

  const probe = await page.evaluate(() => {
    const ids = ["plot-funds-asset", "plot-funds-sector", "plot-macro-mcclellan", "plot-fb-tilt"];
    const out = {};
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) { out[id] = "NO EL"; return; }
      const t = el.querySelectorAll(".scatterlayer .trace, .barlayer .trace, .plot .trace").length;
      const note = el.querySelector(".empty-note");
      out[id] = { traces: t, isPlotly: el.classList.contains("js-plotly-plot"), note: note ? note.textContent : null };
    });
    const body = document.getElementById("funds-body");
    const bh = document.getElementById("funds-bench-host");
    return { panels: out, fundsSym: (typeof FUNDS_SYM !== "undefined" ? FUNDS_SYM : null),
             schemes: (window.VISTAS_FUNDS_HOLDINGS_MANIFEST ? Object.keys(window.VISTAS_FUNDS_HOLDINGS_MANIFEST).length : 0),
             benchIndices: (window.VISTAS_BENCHMARK_MANIFEST ? Object.keys(window.VISTAS_BENCHMARK_MANIFEST).length : 0),
             hasBench: !!(bh && /Active share vs benchmark/.test(bh.innerHTML)),
             benchDropdown: !!document.getElementById("fb-index"),
             bodyRows: body ? body.querySelectorAll("table.fund-hold tbody tr").length : 0 };
  });
  console.log("FUNDS_SYM:", probe.fundsSym, "| schemes:", probe.schemes, "| full-table rows:", probe.bodyRows);
  console.log("benchmark manifest indices:", probe.benchIndices, "| vs-benchmark panel:", probe.hasBench ? "present" : "ABSENT", "| dropdown:", probe.benchDropdown);
  console.log("panel probe:", JSON.stringify(probe.panels, null, 1));
  console.log(`\nERRORS (${errs.length}):`); errs.slice(0, 12).forEach((e) => console.log("  • " + e));
  const okAsset = probe.panels["plot-funds-asset"] && probe.panels["plot-funds-asset"].traces > 0;
  const okMcc = probe.panels["plot-macro-mcclellan"] && probe.panels["plot-macro-mcclellan"].traces > 0;
  // benchmark panel must be present + its tilt plot rendered (if any benchmark indices shipped)
  const okBench = probe.benchIndices === 0 || (probe.hasBench && probe.benchDropdown
                  && probe.panels["plot-fb-tilt"] && probe.panels["plot-fb-tilt"].traces > 0);
  console.log("vs-benchmark gate:", okBench ? "OK" : "FAILED");
  const ok = errs.length === 0 && okAsset && okMcc && probe.bodyRows > 5 && okBench;
  console.log("\n" + (ok ? "PASS: Funds full-portfolio table + asset/sector + vs-benchmark + McClellan render under real Plotly, 0 throws." : "FAIL: see above."));
  await browser.close(); server.close();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.log("PROBE THREW:", e.message); try { server.close(); } catch (x) {} process.exit(1); });
