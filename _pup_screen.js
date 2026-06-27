/* Headless-browser probe of the REAL "Screens" tab render (Smart-money vs the Street).
 * Serves the built terminal_site over HTTP, opens it, switches to the Screens tab, and verifies
 * the 4-quadrant scatter renders under real Plotly, the detail table has rows, the window toggle
 * (3m/1m) re-renders, and the AMC dropdown actually filters. Catches Plotly cleanData throws a
 * VM stub can't see. Run: node _pup_screen.js
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

  const tabEnabled = await page.evaluate(() => {
    const b = document.querySelector('#tabs .tab[data-view="screen"]');
    return !!(b && !b.disabled);
  });

  // enter the Screens tab (renderScreen now lazy-fetches the ~1.5MB screen JSON — wait for it)
  await page.evaluate(async () => { try { if (typeof setView === "function") setView("screen"); } catch (e) {} try { if (typeof renderScreen === "function") await renderScreen(); } catch (e) {} });
  await new Promise((r) => setTimeout(r, 3500));

  const def = await page.evaluate(() => {
    const el = document.getElementById("plot-screen");
    const body = document.getElementById("screen-body");
    const markers = el ? el.querySelectorAll(".scatterlayer .points path.point").length : 0;
    const legend = el ? el.querySelectorAll(".legend .traces").length : 0;
    const rows = document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length;
    const chips = document.querySelectorAll("#screen-body .screen-chip").length;
    const win = document.querySelector("#screen-win button.active");
    const amc = document.getElementById("screen-amc");
    const amcOpts = amc ? amc.options.length : 0;
    return {
      isPlotly: !!(el && el.classList.contains("js-plotly-plot")),
      markers, legend, rows, chips,
      winLabel: win ? win.textContent : null,
      amcOpts,
      hasMeth: !!(body && /Definition · Method · Why/.test(body.innerHTML)),
      bodyChars: body ? body.innerHTML.length : 0
    };
  });

  // toggle to 1-month flow — must re-render with markers and not throw
  const oneM = await page.evaluate(async () => {
    const btn = Array.from(document.querySelectorAll("#screen-win button")).find((b) => b.dataset.win === "1m");
    if (btn) btn.click();
    await new Promise((r) => setTimeout(r, 800));
    const el = document.getElementById("plot-screen");
    return {
      active: (document.querySelector("#screen-win button.active") || {}).textContent || null,
      markers: el ? el.querySelectorAll(".scatterlayer .points path.point").length : 0,
      rows: document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length
    };
  });

  // #106 — flow-basis decomposition toggle: 3 options (gross / price-adj / net-active); switching must
  // re-render the scatter on the new basis (markers + rows + 4 chips present, x-axis title reflects the
  // basis) with no throws, then reset to the default for the subsequent filter tests.
  const basis = await page.evaluate(async () => {
    const seg = document.getElementById("screen-basis");
    const btns = seg ? Array.from(seg.querySelectorAll("button")).map((b) => b.dataset.basis) : [];
    const def = (seg && (seg.querySelector("button.active") || {}).dataset || {}).basis || null;
    const click = async (k) => {
      const b = Array.from(document.querySelectorAll("#screen-basis button")).find((x) => x.dataset.basis === k);
      if (b) b.click();
      await new Promise((r) => setTimeout(r, 900));
      const el = document.getElementById("plot-screen");
      let title = "";
      try { title = (el && el._fullLayout && el._fullLayout.xaxis && el._fullLayout.xaxis.title && el._fullLayout.xaxis.title.text) || ""; } catch (e) {}
      if (!title) { const t = el && el.querySelector(".xtitle"); title = t ? t.textContent : ""; }
      return {
        markers: el ? el.querySelectorAll(".scatterlayer .points path.point").length : 0,
        rows: document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length,
        chips: document.querySelectorAll("#screen-body .screen-chip").length, title,
      };
    };
    const netActive = await click("net_active");
    const gross = await click("gross");
    await click("price_adj");                    // reset to default for the subsequent filter tests
    return { present: !!seg, btns, def, netActive, gross };
  });
  console.log("flow-basis toggle:", JSON.stringify(basis));

  // pick the first real AMC in the dropdown → rows must filter to a subset (>0, <= all)
  const filt = await page.evaluate(async () => {
    const sel = document.getElementById("screen-amc");
    if (!sel || sel.options.length < 2) return { tested: false };
    const before = document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length;
    sel.value = sel.options[1].value;
    sel.dispatchEvent(new Event("change"));
    await new Promise((r) => setTimeout(r, 800));
    const after = document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length;
    const el = document.getElementById("plot-screen");
    return { tested: true, amc: sel.options[1].value, before, after, markers: el ? el.querySelectorAll(".scatterlayer .points path.point").length : 0 };
  });

  // AMC still selected: add a ₹cr magnitude threshold → must reduce further + show the coverage funnel
  const mag = await page.evaluate(async () => {
    const rup = document.getElementById("screen-rupee");
    const before = document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length;
    if (rup) { rup.value = "100"; rup.dispatchEvent(new Event("change")); }
    await new Promise((r) => setTimeout(r, 900));
    const after = document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length;
    const sub = (document.querySelector("#screen-body .screen-sub") || {}).textContent || "";
    return { has: !!rup, before, after, hasFunnel: /→/.test(sub) && /₹100cr/.test(sub) };
  });
  // Excel-like column filter on the FULL universe: 6M < 0 → must reduce
  const colf = await page.evaluate(async () => {
    const amc = document.getElementById("screen-amc"); if (amc) { amc.value = ""; amc.dispatchEvent(new Event("change")); }
    await new Promise((r) => setTimeout(r, 700));
    const rup = document.getElementById("screen-rupee"); if (rup) { rup.value = "0"; rup.dispatchEvent(new Event("change")); }
    await new Promise((r) => setTimeout(r, 700));
    const before = document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length;
    const inp = document.querySelector('#screen-body input.screen-cf[data-fk="ret_6m"]');
    if (inp) { inp.value = "<0"; inp.dispatchEvent(new Event("change")); }
    await new Promise((r) => setTimeout(r, 900));
    const after = document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length;
    // clear it again
    const inp2 = document.querySelector('#screen-body input.screen-cf[data-fk="ret_6m"]');
    if (inp2) { inp2.value = ""; inp2.dispatchEvent(new Event("change")); }
    await new Promise((r) => setTimeout(r, 600));
    return { has: !!inp, before, after };
  });

  // sort by a numeric column (ARM header) — must re-render without throwing
  const sorted = await page.evaluate(async () => {
    const th = document.querySelector('#screen-body th[data-sort="arm"]');
    if (th) th.click();
    await new Promise((r) => setTimeout(r, 500));
    return { rows: document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length };
  });

  // enriched columns (#43): flow 6m/12m, ownership (Prom/FII/DII/Public), MF; and the %↔₹ toggle
  const enrich = await page.evaluate(async () => {
    const heads = () => Array.from(document.querySelectorAll("#screen-body table.screen-tbl thead th")).map((t) => t.textContent.trim());
    const h0 = heads();
    const hasFlow6 = !!document.querySelector('#screen-body th[data-sort="flow_6m"]');
    const hasFlow12 = !!document.querySelector('#screen-body th[data-sort="flow_12m"]');
    const hasOwn = ["own_promoter", "own_fii", "own_dii", "own_public", "mf_pct_mcap"].every((k) => !!document.querySelector(`#screen-body th[data-sort="${k}"]`));
    const pctHdr = h0.some((t) => /FII\s*%/.test(t));
    // flip to ₹ cr
    const btn = Array.from(document.querySelectorAll("#screen-amt button")).find((b) => b.dataset.amt === "cr");
    if (btn) btn.click();
    await new Promise((r) => setTimeout(r, 700));
    const h1 = heads();
    const crHdr = h1.some((t) => /₹cr/.test(t));
    const rowsAfter = document.querySelectorAll("#screen-body table.screen-tbl tbody tr").length;
    return { hasFlow6, hasFlow12, hasOwn, pctHdr, crHdr, rowsAfter, nCols: h0.length };
  });
  console.log("enriched columns + toggle:", JSON.stringify(enrich));

  console.log("Screens tab enabled:", tabEnabled);
  console.log("default (3m):", JSON.stringify(def));
  console.log("toggle 1m:", JSON.stringify(oneM));
  console.log("AMC filter:", JSON.stringify(filt));
  console.log("AMC + ₹100cr:", JSON.stringify(mag));
  console.log("column filter 6M<0:", JSON.stringify(colf));
  console.log("sort by ARM:", JSON.stringify(sorted));
  console.log(`\nERRORS (${errs.length}):`);
  errs.slice(0, 18).forEach((e) => console.log("  • " + e));

  const universeLarge = def.rows > 200;                              // (a) all-stocks universe, not the 59 watchlist
  const filtOk = !filt.tested || (filt.after > 0 && filt.after < filt.before);   // (c1) AMC selection reduces
  const magOk = !mag.has || (mag.after <= mag.before && mag.hasFunnel);           // (c2) ₹cr threshold reduces + funnel shown
  const colOk = !colf.has || (colf.after < colf.before);                          // (d) Excel column filter reduces
  const enrichOk = enrich.hasFlow6 && enrich.hasFlow12 && enrich.hasOwn && enrich.pctHdr && enrich.crHdr && enrich.rowsAfter > 0;
  const basisOk = basis.present && basis.btns.length === 3 && basis.def === "price_adj"
    && basis.netActive.markers > 0 && basis.netActive.rows > 0 && basis.netActive.chips === 4
    && /net-active/i.test(basis.netActive.title) && basis.gross.markers > 0 && /gross/i.test(basis.gross.title);
  console.log("gates:", JSON.stringify({ universeLarge, filtOk, magOk, colOk, enrichOk, basisOk }));
  const ok = errs.length === 0 && tabEnabled && def.isPlotly && def.markers > 0 && universeLarge && def.chips === 4
    && def.amcOpts > 1 && def.hasMeth && oneM.markers > 0 && filtOk && magOk && colOk && sorted.rows > 0 && enrichOk && basisOk;
  console.log("\n" + (ok
    ? "PASS: all-stocks Screens — large universe, AMC + ₹cr magnitude filters with coverage funnel, Excel column filters, %↔₹ toggle, sort, scatter all work; 0 throws."
    : "FAIL: see above."));
  await browser.close(); server.close();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.log("PROBE THREW:", e.message); try { server.close(); } catch (x) {} process.exit(1); });
