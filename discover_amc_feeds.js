/* ===========================================================================================
 * discover_amc_feeds.js  —  Vistas Funds: LOCAL (residential-IP) portfolio-feed harness
 * -------------------------------------------------------------------------------------------
 * WHY THIS EXISTS (first principles)
 *   Most AMC monthly-portfolio files download fine from a datacenter IP (the cloud workflow wired
 *   ~49 of 55 that way). A handful sit behind a WAF / JS-render / captcha that only lets a REAL
 *   browser on a residential IP through (Kotak, Franklin, a few others). This harness is that
 *   browser. KV runs it on his own machine; it does for the stubborn houses exactly what the
 *   datacenter build does for the easy ones — find the latest monthly-portfolio workbook, download
 *   it, and hand it to the SAME parser.
 *
 * THE BRIDGE (how a residential download reaches the datacenter build)
 *   The Python engine (vistas/funds_portfolio.py) fetches workbooks cache-first from
 *   data/funds/portfolio_cache/<sanitised-url>.bin. This harness writes the bytes it downloads to
 *   that EXACT path (mirroring _cache_path), and records the resolved URL + method:"local-cache"
 *   in data/funds/amc_feed_patterns.json. Next build, the engine sees method local-cache ->
 *   reads the cache OFFLINE (no doomed datacenter fetch) -> parses. So a file KV pulls on his IP
 *   shows up in the published terminal with zero further work. Re-run monthly.
 *
 * WHAT IT DOES, per target AMC
 *   1. open the disclosure landing page in headless Chrome (real engine, real cookies),
 *   2. let JS render, optionally click a "Monthly Portfolio" tab/accordion to reveal links,
 *   3. collect every .xlsx/.xls/.zip link that looks like a monthly portfolio, pick the newest,
 *   4. download it THROUGH the browser session (cookies/WAF token apply), magic-byte verify,
 *   5. save bytes to the Python byte-cache + update amc_feed_patterns.json (merge, never clobber).
 *
 * USAGE
 *   node discover_amc_feeds.js                 # process every AMC flagged needs_harness in the registry
 *   node discover_amc_feeds.js --all           # try ALL AMCs in the registry (refresh everything locally)
 *   node discover_amc_feeds.js --key kotak-mahindra --key franklin-templeton
 *   node discover_amc_feeds.js --headed        # show the browser (needed for the toughest captchas)
 *   node discover_amc_feeds.js --key X --landing https://...   # ad-hoc: harvest a page not yet in the registry
 * =========================================================================================== */
"use strict";
const fs = require("fs");
const path = require("path");
let pup;
try { pup = require("puppeteer"); }
catch (e) { console.log("puppeteer not installed. Run:  npm i puppeteer"); process.exit(2); }

const ROOT = __dirname;
const FUNDS_DIR = path.join(ROOT, "data", "funds");
const PATTERNS = path.join(FUNDS_DIR, "amc_feed_patterns.json");
const FANOUT = path.join(FUNDS_DIR, "_amc_fanout.json");
const CACHE_DIR = path.join(FUNDS_DIR, "portfolio_cache");

// --- args -----------------------------------------------------------------------------------
const argv = process.argv.slice(2);
const FLAGS = new Set(argv.filter(a => a.startsWith("--") && !["--key", "--landing"].includes(a)));
const onlyKeys = [];
let adhocLanding = "";
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === "--key") onlyKeys.push(argv[++i]);
  else if (argv[i] === "--landing") adhocLanding = argv[++i];
}
const HEADED = FLAGS.has("--headed");
const ALL = FLAGS.has("--all");

// --- helpers --------------------------------------------------------------------------------
function readJSON(p, dflt) { try { return JSON.parse(fs.readFileSync(p, "utf-8")); } catch (e) { return dflt; } }

// MIRROR of funds_portfolio._cache_path — keep byte-for-byte identical so the build finds the file.
function cachePath(url) {
  const safe = url.replace(/[^A-Za-z0-9._-]+/g, "_").slice(-150) + ".bin";
  return path.join(CACHE_DIR, safe);
}
function isWorkbook(buf) {
  if (!buf || buf.length < 2) return false;
  return (buf[0] === 0x50 && buf[1] === 0x4b) ||           // 'PK'  -> xlsx / zip
         (buf[0] === 0xd0 && buf[1] === 0xcf);             // D0CF  -> legacy .xls
}

const MONTHS = ["january","february","march","april","may","june","july","august","september","october","november","december"];
// score a URL by how recent its date tokens look (year then month) — higher = newer
function recencyScore(u) {
  const low = u.toLowerCase();
  let yr = 0; const ys = u.match(/20\d{2}/g); if (ys) yr = Math.max(...ys.map(Number));
  let mo = 0;
  MONTHS.forEach((m, i) => { if (low.includes(m) || low.includes(m.slice(0, 3))) mo = Math.max(mo, i + 1); });
  const m2 = u.match(/[_\-/](0[1-9]|1[0-2])[_\-/]/); if (m2) mo = Math.max(mo, Number(m2[1]));
  return yr * 100 + mo;
}
const INC = ["portfolio","monthly","monthy","mportfolio","mp-","_mp_","59a","reg59","scheme-portfolio","holdings"];
const EXC = ["factsheet","fact-sheet","fortnight","weekly","half-year","halfyear","annual","/nav","navhistory","/sid","/kim","addendum","riskometer","aum-","ter-","sip","performance"];
function looksPortfolio(u) {
  const low = u.toLowerCase();
  if (!/\.(xlsx|xls|zip)(\?|#|$)/i.test(low)) return false;
  if (!INC.some(k => low.includes(k))) return false;
  if (EXC.some(k => low.includes(k))) return false;
  return true;
}

// --- which AMCs to process ------------------------------------------------------------------
function targets() {
  const patt = readJSON(PATTERNS, { amcs: {} });
  const amcs = patt.amcs || patt || {};
  const fan = readJSON(FANOUT, []);
  const fanByKey = {}; fan.forEach(f => { fanByKey[f.key] = f; });
  const out = [];
  const want = (key) => onlyKeys.length ? onlyKeys.includes(key) : true;

  if (onlyKeys.length && adhocLanding) {                   // ad-hoc single page not yet in the registry
    return [{ key: onlyKeys[0], amc: onlyKeys[0], landing: adhocLanding, file_type: "xlsx" }];
  }
  const keys = new Set([...Object.keys(amcs), ...fan.map(f => f.key)]);
  for (const key of keys) {
    if (!want(key)) continue;
    const spec = amcs[key] || {};
    const fo = fanByKey[key] || {};
    const landing = spec.landing || (spec.resolve && spec.resolve.landing) || fo.landing || "";
    const needsHarness = spec.needs_harness || ["waf-blocked", "render-needed", "local-cache"].includes((spec.method || "").toLowerCase())
                         || ["waf-blocked", "render-needed"].includes((fo.method || "").toLowerCase());
    if (!ALL && !onlyKeys.length && !needsHarness) continue;
    if (!landing) { console.log(`  (skip ${key}: no landing url known)`); continue; }
    out.push({ key, amc: spec.amc || fo.amc || key, landing, file_type: spec.file_type || fo.file_type || "xlsx" });
  }
  return out;
}

// --- find candidate file URLs on a rendered page --------------------------------------------
async function findLinks(page) {
  // 1) anchors + any element with an href/data-href/onclick file link, after render
  let urls = await page.evaluate(() => {
    const out = new Set();
    const push = (u) => { if (u) out.add(u); };
    document.querySelectorAll("a[href]").forEach(a => push(a.href));
    document.querySelectorAll("[data-href],[data-url],[data-file]").forEach(e => {
      push(e.getAttribute("data-href")); push(e.getAttribute("data-url")); push(e.getAttribute("data-file"));
    });
    // links hiding in inline scripts / hydration JSON
    const html = document.documentElement.innerHTML;
    const re = /https?:\/\/[^\s'"<>()]+?\.(?:xlsx|xls|zip)\b/gi;
    let m; while ((m = re.exec(html))) push(m[0]);
    return Array.from(out);
  });
  return urls.filter(looksPortfolio);
}

async function maybeRevealMonthly(page) {
  // click an element whose text mentions "monthly portfolio" to expand a tab/accordion, then settle
  try {
    const clicked = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll("a,button,li,span,div,h3,h4"));
      const t = els.find(e => /monthly\s*portfolio/i.test((e.textContent || "").trim()) && e.offsetParent !== null);
      if (t) { t.click(); return true; }
      return false;
    });
    if (clicked) await new Promise(r => setTimeout(r, 2500));
  } catch (e) {}
}

// --- download a URL through the browser session ---------------------------------------------
async function downloadVia(page, url) {
  try {
    const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
    if (!resp) return null;
    const buf = await resp.buffer();
    return isWorkbook(buf) ? buf : null;
  } catch (e) { return null; }
}

// --- main -----------------------------------------------------------------------------------
(async () => {
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const tgts = targets();
  if (!tgts.length) { console.log("No AMCs to harvest (nothing flagged needs_harness; use --all or --key)."); process.exit(0); }
  console.log(`Harness: ${tgts.length} AMC(s) — ${tgts.map(t => t.key).join(", ")}`);

  const browser = await pup.launch({
    headless: HEADED ? false : "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
  });
  const patt = readJSON(PATTERNS, { amcs: {} });
  patt.amcs = patt.amcs || {};
  const report = [];

  for (const t of tgts) {
    const page = await browser.newPage();
    await page.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36");
    let cached = [], nLinks = 0;
    try {
      await page.goto(t.landing, { waitUntil: "networkidle2", timeout: 60000 });
      await maybeRevealMonthly(page);
      const links = Array.from(new Set(await findLinks(page)));
      nLinks = links.length;
      if (links.length) {
        // A per-scheme (MULTI) house lists one file PER scheme for the latest month; a combined
        // house lists one. Keep every link of the NEWEST month (same recency score) and download
        // them ALL — that's the complete current portfolio set the datacenter build can't get.
        const maxS = Math.max(...links.map(recencyScore));
        let latest = links.filter((u) => recencyScore(u) === maxS);
        if (!latest.length) latest = links;
        latest.sort((a, b) => recencyScore(b) - recencyScore(a));
        for (const u of latest.slice(0, 200)) {
          const buf = await downloadVia(page, u);
          if (buf) { fs.writeFileSync(cachePath(u), buf); cached.push(u); }   // warm the Python byte-cache
          // re-open the landing so the next file nav keeps the session/WAF token
          await page.goto(t.landing, { waitUntil: "domcontentloaded", timeout: 60000 }).catch(() => {});
        }
      }
    } catch (e) {
      console.log(`  ${t.key}: ERROR ${e.message}`);
    }
    if (cached.length) {
      const multi = cached.length > 1;
      patt.amcs[t.key] = Object.assign({}, patt.amcs[t.key], {
        amc: t.amc, landing: t.landing, method: "local-cache", file_type: t.file_type,
        multi: multi, verified_url: cached[0], verified_urls: multi ? cached : undefined,
        harness_asof: new Date().toISOString().slice(0, 10), needs_harness: false, source: "local-harness",
      });
      console.log(`  ✓ ${t.key}: cached ${cached.length} file(s)${multi ? " (multi/per-scheme)" : ""}`);
      report.push({ key: t.key, ok: true, files: cached.length });
    } else {
      console.log(`  ✗ ${t.key}: no workbook found among ${nLinks} candidate link(s) on ${t.landing}`);
      report.push({ key: t.key, ok: false, links: nLinks });
    }
    await page.close();
  }

  await browser.close();
  fs.writeFileSync(PATTERNS, JSON.stringify(patt, null, 1));
  const ok = report.filter(r => r.ok).length;
  console.log(`\nHarness done: ${ok}/${report.length} cached. Updated ${path.relative(ROOT, PATTERNS)}.`);
  console.log("Next: re-run the build (pipeline/Publish Last Build.bat) — the engine reads these from cache offline.");
  process.exit(0);
})().catch(e => { console.log("HARNESS THREW:", e.message); process.exit(1); });
