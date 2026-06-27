/* Headless-browser probe of the REAL Fundamentals render (real Plotly). Serves the built
 * terminal_site over HTTP, opens it in Chromium, navigates to Fundamentals=RELIANCE, and
 * captures the console/page error that the dispatch try/catch swallows into "—".
 * Run: node _pup_fund.js [SYM]
 */
const http = require("http"), fs = require("fs"), path = require("path");
let pup; try { pup = require("puppeteer"); } catch (e) { console.log("puppeteer not installed:", e.message); process.exit(2); }
const SYM = process.argv[2] || "RELIANCE";
const ROOT = path.join(__dirname, "output", "terminal_site");
const MIME = { ".html": "text/html", ".js": "application/javascript", ".json": "application/json", ".css": "text/css" };
const server = http.createServer((req, res) => {
  let u = decodeURIComponent(req.url.split("?")[0]); if (u === "/") u = "/index.html";
  const fp = path.join(ROOT, u);
  fs.readFile(fp, (e, d) => {
    if (e) { res.statusCode = 404; res.end("nf"); return; }
    let body = d;
    if (u === "/index.html") {  // patch the swallowing catch so the real throw is captured
      body = Buffer.from(String(d).replace('catch (e) { note(p.id, "—"); }',
        'catch (e) { note(p.id, "—"); (window.__FERR=window.__FERR||[]).push(p.id+" :: "+(e&&e.message)+"  @ "+((e&&e.stack||"").split("\\n").slice(1,4).map(function(s){return s.trim()}).join("  <-  "))); }'));
      if (String(body).indexOf("__FERR") === -1) console.log("WARN: catch not patched (string mismatch)");
    }
    res.setHeader("content-type", MIME[path.extname(fp)] || "text/plain"); res.end(body);
  });
});
(async () => {
  await new Promise((r) => server.listen(0, r));
  const port = server.address().port;
  const browser = await pup.launch({ headless: "new", args: ["--no-sandbox", "--disable-setuid-sandbox"] });
  const page = await browser.newPage();
  const errs = [];
  page.on("console", (m) => { if (m.type() === "error") errs.push("CONSOLE.error: " + m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERROR: " + e.message + "  @ " + String(e.stack || "").split("\n").slice(1, 4).map((s) => s.trim()).join("  <-  ")));
  page.on("response", (r) => { if (r.status() >= 400) errs.push("HTTP " + r.status() + ": " + r.url()); });
  await page.goto(`http://localhost:${port}/index.html`, { waitUntil: "networkidle2", timeout: 45000 });
  // drive into Fundamentals for the symbol (lazy-load + render), then settle
  await page.evaluate(async (sym) => {
    window.__RX = [];
    if (window.Plotly && Plotly.react) {
      const orig = Plotly.react;
      Plotly.react = function (id, data, layout, config) {
        if (String(id).indexOf("fund-dupont3") >= 0 && window.__RX.length < 1) {
          window.__RX.push({ id: String(id), n: Array.isArray(data) ? data.length : ("NOT-ARRAY:" + typeof data),
            holes: Array.isArray(data) ? data.map((t, k) => (t === undefined || t === null) ? k : -1).filter((k) => k >= 0) : [],
            types: Array.isArray(data) ? data.map((t) => (t === undefined ? "UNDEF" : t === null ? "NULL" : (t.type || "?"))) : [],
            first: (Array.isArray(data) && data[0]) ? JSON.stringify(data[0]).slice(0, 500) : null,
            layoutKeys: layout ? Object.keys(layout) : null });
        }
        return orig.apply(this, arguments);
      };
    }
    try { location.hash = "fundamentals=" + sym; } catch (e) {}
    try { if (typeof applyHash === "function") applyHash(); } catch (e) {}   // sets module-scoped FUND_SYM
    try { if (typeof ensureFundamentals === "function") await ensureFundamentals(sym); } catch (e) {}
    try { if (typeof setView === "function") setView("fundamentals"); } catch (e) {}
    try { if (typeof renderFundamentals === "function") await renderFundamentals(); } catch (e) {}
  }, SYM);
  await new Promise((r) => setTimeout(r, 2500));
  const probe = await page.evaluate(async () => {
    const ids = ["plot-fund-dupont3", "plot-fund-growthlvl", "plot-fund-price", "plot-fund-arm", "plot-fund-armts", "plot-macro-infl"];
    const out = {};
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) { out[id] = "NO EL"; return; }
      const svgTraces = el.querySelectorAll(".scatterlayer .trace, .barlayer .trace, .plot .trace").length;
      const note = el.querySelector(".empty-note");
      out[id] = { svgTraces, note: note ? note.textContent : null, isPlotly: el.classList.contains("js-plotly-plot") };
    });
    // ── ARM time-nav checks: histogram has a date slider; trajectory plots headline + components (>=2)
    const armDn = !!document.querySelector("#tog-fund-arm .dn-sl");
    const armBars = document.getElementById("plot-fund-arm");
    const armBarN = armBars ? armBars.querySelectorAll(".barlayer .point, .barlayer .trace").length : 0;
    const armtsEl = document.getElementById("plot-fund-armts");
    const armtsTraces = armtsEl ? armtsEl.querySelectorAll(".scatterlayer .trace, .scatterlayer .lines").length : 0;
    // drag the histogram date slider to a PAST date and confirm the "as of" caption changes
    const sl = document.querySelector("#tog-fund-arm .dn-sl");
    let armDateChanged = false;
    if (sl) {
      const capBefore = (document.querySelector("#stat-fund-arm, #tbl-fund-arm .src") || {}).textContent || "";
      sl.value = "3"; sl.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 400));
      const capAfter = (document.querySelector("#tbl-fund-arm .src") || {}).textContent || "";
      armDateChanged = capAfter && capAfter !== capBefore;
    }
    return { panels: out, armDn, armBarN, armtsTraces, armDateChanged,
             fundLoaded: !!(window.FUND_DATA && window.FUND_DATA.RELIANCE && window.FUND_DATA.RELIANCE.analytics), plotlyVer: (window.Plotly && Plotly.version) || "?" };
  });
  const ferr = await page.evaluate(() => window.__FERR || []);
  console.log("Plotly version:", probe.plotlyVer, "| FUND_DATA.RELIANCE.analytics:", probe.fundLoaded);
  console.log("panel probe:", JSON.stringify(probe.panels, null, 1));
  console.log("ARM time-nav:", JSON.stringify({ histDateSlider: probe.armDn, histBars: probe.armBarN, trajTraces: probe.armtsTraces, histDateChangedOnDrag: probe.armDateChanged }));
  const armNavOk = probe.armDn && probe.armtsTraces >= 2 && probe.armBarN >= 1;
  console.log(armNavOk ? "ARM-NAV PASS: histogram has a date slider, trajectory plots headline + components."
                       : "ARM-NAV FAIL: expected #tog-fund-arm .dn-sl + >=2 trajectory traces + >=1 histogram bar.");
  console.log(`\nSWALLOWED PANEL THROWS (__FERR ${ferr.length}):`);
  ferr.slice(0, 16).forEach((e) => console.log("  • " + e));
  console.log(`\nNETWORK/CONSOLE ERRORS (${errs.length}):`);
  errs.slice(0, 18).forEach((e) => console.log("  • " + e));
  await browser.close(); server.close();
})().catch((e) => { console.log("PROBE THREW:", e.message); try { server.close(); } catch (x) {} process.exit(1); });
