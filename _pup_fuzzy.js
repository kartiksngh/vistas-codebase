/* Headless-browser probe of the REAL fuzzy search in the built terminal_site.
 * Serves the site, loads it in Chromium, and: (1) asserts 0 runtime errors,
 * (2) runs the acceptance queries through the in-page window.fuzzyScore (proves the
 * shipped inlined code runs identically in a browser), (3) drives the universe
 * MultiSelect + command-palette DOM with a typo query to catch any wiring throw.
 * Run: node _pup_fuzzy.js
 */
const http = require("http"), fs = require("fs"), path = require("path");
let pup; try { pup = require("puppeteer"); } catch (e) { console.log("puppeteer not installed:", e.message); process.exit(2); }
const ROOT = path.join(__dirname, "output", "terminal_site");
const MIME = { ".html": "text/html", ".js": "application/javascript", ".json": "application/json", ".css": "text/css" };
const server = http.createServer((req, res) => {
  let u = decodeURIComponent(req.url.split("?")[0]); if (u === "/") u = "/index.html";
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
  const ignorable = (s) => /favicon\.ico/.test(s) || /Failed to load resource: the server responded with a status of 404/.test(s);
  page.on("console", (m) => { if (m.type() === "error" && !ignorable(m.text())) errs.push("CONSOLE.error: " + m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERROR: " + e.message));
  page.on("response", (r) => { if (r.status() >= 400 && !ignorable(r.url())) errs.push("HTTP " + r.status() + ": " + r.url()); });
  await page.goto(`http://localhost:${port}/index.html`, { waitUntil: "networkidle2", timeout: 45000 });
  await new Promise((r) => setTimeout(r, 1500));

  // (1)+(2) — fuzzyScore runs in-browser on the acceptance universe
  const scoreProbe = await page.evaluate(() => {
    if (typeof window.fuzzyScore !== "function" || typeof window.fuzzyPrep !== "function")
      return { ok: false, why: "fuzzyScore/fuzzyPrep not global" };
    const UNIV = [
      ["ABSL Flexi", "Aditya Birla Sun Life Flexi Cap Fund"],
      ["Kotak Quant", "Kotak Quant Fund"],
      ["Kotak Mahindra Bank", "Kotak Mahindra Bank"],
      ["HDFC Bank", "HDFC Bank"], ["ICICI Bank", "ICICI Bank"], ["Axis Bank", "Axis Bank"],
      ["HDFC Small Cap", "HDFC Small Cap Fund"],
      ["ICICI Pru Tech", "ICICI Prudential Technology Fund"],
      ["SBI Bluechip", "SBI Blue Chip Fund"],
      ["Nippon Small Cap", "Nippon India Small Cap Fund"],
      ["Tata Motors", "Tata Motors"], ["Tata Steel", "Tata Steel"],
      ["Bata India", "Bata India"], ["Coal India", "Coal India"],
    ].map(([id, nm]) => ({ id, prep: window.fuzzyPrep(nm, "") }));
    const top = (q) => UNIV.map((u) => [u.id, window.fuzzyScore(q, u.prep)])
      .filter((x) => x[1] > 0).sort((a, b) => b[1] - a[1]);
    const cases = [["absl flexi", "ABSL Flexi"], ["koatq qiant", "Kotak Quant"],
      ["hdfc smallcap", "HDFC Small Cap"], ["icici pru tech", "ICICI Pru Tech"], ["sbi blue", "SBI Bluechip"]];
    const results = cases.map(([q, want]) => { const r = top(q); return { q, want, top: r.length ? r[0][0] : null, ok: r.length > 0 && r[0][0] === want }; });
    // leak regressions (review NO-GO): short tokens must NOT surface clearly-wrong names
    const ids = (q) => top(q).map((x) => x[0]);
    const leaks = [
      { q: "kota", ok: !ids("kota").some((n) => /Tata|Bata/.test(n)) && ids("kota").some((n) => /Kotak/.test(n)), got: ids("kota") },
      { q: "bank", ok: !ids("bank").includes("Bata India"), got: ids("bank") },
      { q: "cit", ok: !ids("cit").includes("Coal India"), got: ids("cit") },
    ];
    const junk = top("zxqwvk");
    return { ok: true, results, leaks, junkN: junk.length };
  });

  // (3) — drive the REAL universe MultiSelect + command palette DOM (wiring must not throw)
  const domProbe = await page.evaluate(async () => {
    const out = { ms: null, cmdk: null };
    try {
      const ms = document.querySelector(".ms");
      if (ms) {
        ms.querySelector(".box").click();                  // open the popover
        const inp = ms.querySelector(".search");
        inp.value = "reliance"; inp.dispatchEvent(new Event("input", { bubbles: true }));
        await new Promise((r) => setTimeout(r, 150));
        out.ms = { opts: ms.querySelectorAll(".list .opt").length };
      }
    } catch (e) { out.ms = { err: e.message }; }
    try {
      if (typeof cmdkOpen === "function") cmdkOpen();
      const ci = document.getElementById("cmdk-input");
      if (ci) {
        // typo query: should still surface rows via edit distance, no throw
        if (typeof cmdkRender === "function") cmdkRender("relianc");
        out.cmdk = { rows: document.querySelectorAll("#cmdk-list .cmdk-row").length };
      }
      if (typeof cmdkClose === "function") cmdkClose();
    } catch (e) { out.cmdk = { err: e.message }; }
    return out;
  });

  console.log("fuzzyScore in-browser:", scoreProbe.ok ? "available" : "MISSING — " + scoreProbe.why);
  if (scoreProbe.ok) {
    scoreProbe.results.forEach((r) => console.log(`  ${r.ok ? "PASS" : "FAIL"}  "${r.q}" -> top=${r.top} (want ${r.want})`));
    (scoreProbe.leaks || []).forEach((l) => console.log(`  ${l.ok ? "PASS" : "FAIL"}  leak "${l.q}" -> [${l.got.join(", ") || "empty"}]`));
    console.log(`  Gibberish "zxqwvk" -> ${scoreProbe.junkN} matches (expect 0): ${scoreProbe.junkN === 0 ? "PASS" : "FAIL"}`);
  }
  console.log("DOM wiring:", JSON.stringify(domProbe));
  console.log(`\nRUNTIME ERRORS (${errs.length}):`);
  errs.slice(0, 20).forEach((e) => console.log("  • " + e));

  const accOk = scoreProbe.ok && scoreProbe.results.every((r) => r.ok) && (scoreProbe.leaks || []).every((l) => l.ok) && scoreProbe.junkN === 0;
  const domOk = domProbe.ms && !domProbe.ms.err && domProbe.cmdk && !domProbe.cmdk.err;
  const pass = accOk && domOk && errs.length === 0;
  console.log("\n" + (pass ? "FUZZY PROBE PASS" : "FUZZY PROBE FAIL"));
  await browser.close(); server.close();
  process.exit(pass ? 0 : 1);
})().catch((e) => { console.log("PROBE THREW:", e.message); try { server.close(); } catch (x) {} process.exit(1); });
