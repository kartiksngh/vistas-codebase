/* Vistas — passive NSE index terminal (client).
   Talks to the Flask JSON API; renders every panel with Plotly. */
"use strict";

// --------------------------------------------------------------------- utils
const $ = (id) => document.getElementById(id);
const FONT = { family: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif', size: 12.5, color: "#33373d" };

let CAT = null;          // catalog
let BUNDLE = null;       // last performance bundle (analyze result)
let VAL_BUNDLE = null;   // last valuation bundle (valuation_analyze result)
let COLORS = {};         // name -> {base, light, dark}  (unique hue per series)
let CSEL = {};           // per-chart hidden-series sets — each chart plots its own subset, independent of the rest
let runTimer = null;
let fetchPoll = null;

// Bloomberg-style views + the measure each one reads.
let VIEW = "performance";       // "performance" | "valuation"
// GP/level-chart display mode (#49) — pure display layer, no analytics change:
//   "rebase"   = rebased to 100 at the window start (the analytics default; like-for-like paths)
//   "absolute" = the underlying total-return index level (recovered from the raw measure frame)
let GP_MODE = "rebase";
let PERF_MEASURE = "TR";        // Performance tab: "TR" (default) | "PR"
let VAL_MEASURE = "PE";         // Valuation tab: "PE" | "PB" | "DY"
const MEASURE_KIND = { TR: "level", PR: "level", NTR: "level", PE: "ratio", PB: "ratio", DY: "yield" };
const VAL_LABEL = { PE: "P/E (price ÷ trailing earnings)", PB: "P/B (price ÷ book value)", DY: "Dividend yield (%, trailing)" };

// Fundamentals tab (Screener): offline deck embeds window.VISTAS_FUNDAMENTALS =
// {SYM:{name,valuation,statements,...}}; the live app fetches /api/fundamentals.
let FUND_DATA = (typeof window !== "undefined" && window.VISTAS_FUNDAMENTALS) ? window.VISTAS_FUNDAMENTALS : null;
let FUND_SYM = null;
// Quant & MI (per-stock cockpit) — data is lazy-fetched per symbol like fundamentals.
let QUANT_DATA = (typeof window !== "undefined" && window.VISTAS_QUANT) ? window.VISTAS_QUANT : null;
let QUANT_SYM = null, QUANT_COMBO = null;
// #51 — flow-decomposition view for the per-stock smart-money panel:
//   "net_active" = conviction (weight-space, inflow-immune)  [DEFAULT]
//   "price_adj"  = price stripped only (still has scheme inflows)  ·  "gross" = raw ₹ change
let SMF_MODE = "net_active";
let FUNDS_HOLD_DATA = null, FUNDS_SYM = null, FUNDS_COMBO = null;
let FUNDS_ATTR_DATA = null, FUNDSKILL_SYM = null, FUNDSKILL_COMBO = null, FUNDSKILL_SORT = { key: "t_stat", dir: -1 }, FUNDSKILL_CAT = "";
let FS_WIN = null;          // {i0,i1} indices into the current scheme's ts[] (null = full history); reset on scheme change
let FS_VANT = { cat: null, blevel: "port", slevel: "port" };   // vantage panel: peer category + per-plot NAV/Portfolio level
let FUNDS_ENV = null;       // lazy-loaded peer-envelope (per category × metric cross-section), cached once
let FUND_STMT = "quarters";   // which statement table the Fundamentals tab shows
const FUND_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"];

// cross-tab window memory — used ONLY when "Link window across tabs" (#linkdates) is OFF,
// so each tab can keep its own From/To. When linked (default) the global #start/#end applies
// everywhere and these stay null.
let TAB_WIN = { performance: null, valuation: null, fundamentals: null, macro: null };

// OFFLINE mode = a self-contained saved deck: the full dataset + catalog are
// embedded in the page and every metric is computed client-side by
// vistas_analytics.js (the verified JS port of analytics.py). No server.
const OFFLINE = (typeof window !== "undefined") && !!(window.VISTAS_DATA && window.VISTAS_CATALOG);

async function getCatalog() {
  if (OFFLINE) return window.VISTAS_CATALOG;
  return await (await fetch("/api/catalog")).json();
}

// Multi-measure data. A v2 offline deck embeds window.VISTAS_MEASURES =
// {TR:{dates,series}, PR:{…}, PE:{…}, PB:{…}, DY:{…}}. A v1 deck only has
// window.VISTAS_DATA (TR) — fall back to that so old decks still work.
const MEASURES = (typeof window !== "undefined" && window.VISTAS_MEASURES) ? window.VISTAS_MEASURES : null;
function measureData(m) {
  if (MEASURES && MEASURES[m]) return MEASURES[m];
  if (m === "TR" && typeof window !== "undefined" && window.VISTAS_DATA) return window.VISTAS_DATA;
  return null;
}
function hasMeasure(m) {
  if (OFFLINE) return !!measureData(m);
  return (CAT && Array.isArray(CAT.measures)) ? CAT.measures.includes(m) : (m === "TR");
}

// Merge a mixed selection of INDICES (from the chosen measure frame) and STOCKS (from
// the embedded yfinance frame) onto a UNION date axis — the browser mirror of
// data.get_level_frame, so RELIANCE vs NIFTY 50 charts like-for-like in the offline deck.
function mergeLevel(measure, names) {
  const md = measureData(measure);
  // extra non-index level frames (stocks, world cross-asset), each {dates, series}
  const extras = [];
  if (typeof window !== "undefined" && window.VISTAS_STOCKS) extras.push(window.VISTAS_STOCKS);
  if (typeof window !== "undefined" && window.VISTAS_WORLD) extras.push(window.VISTAS_WORLD);
  if (typeof window !== "undefined" && window.VISTAS_FUNDS) extras.push(window.VISTAS_FUNDS);
  const hasIdx = (n) => !!(md && md.series[n]);
  const findExtra = (n) => extras.find((e) => e.series[n]);
  const idxNames = names.filter(hasIdx);
  const extraNames = names.filter((n) => !hasIdx(n) && findExtra(n));
  if (!extraNames.length) return md;                 // pure-index -> unchanged fast path
  const frames = [];                                 // each source that contributes a selected series
  if (idxNames.length && md) frames.push({ dates: md.dates, series: md.series, names: idxNames });
  extras.forEach((e) => { const ns = extraNames.filter((n) => e.series[n]); if (ns.length) frames.push({ dates: e.dates, series: e.series, names: ns }); });
  if (frames.length === 1) {                         // single source -> return it (analyze() filters to the selection)
    return frames[0].series === (md && md.series) ? md : { dates: frames[0].dates, series: frames[0].series };
  }
  const dateSet = new Set();                          // union date axis across sources
  frames.forEach((f) => f.dates.forEach((d) => dateSet.add(d)));
  const dates = [...dateSet].sort();
  const series = {};
  frames.forEach((f) => { const ix = {}; f.dates.forEach((d, i) => (ix[d] = i)); f.names.forEach((n) => { const a = f.series[n]; series[n] = dates.map((d) => (d in ix ? a[ix[d]] : null)); }); });
  return { dates, series };
}

// --------------------------------------------------------------------- lazy-load (hosted hybrid)
// A hosted site embeds only a watchlist; the rest of the per-stock prices + per-company
// fundamentals are fetched on demand from small static files. window.VISTAS_LAZY absent
// (the single-file offline deck) => every function below is a no-op and nothing is fetched.
const LAZY = (typeof window !== "undefined" && window.VISTAS_LAZY) ? window.VISTAS_LAZY : null;
let STOCK_NAMES = null;
function stockNameSet() {
  if (!STOCK_NAMES) STOCK_NAMES = new Set(((CAT && CAT.indices) || []).filter((o) => o.group === "Stocks").map((o) => o.name));
  return STOCK_NAMES;
}
async function fetchJSON(url) { try { const r = await fetch(url); if (!r.ok) return null; return await r.json(); } catch (e) { return null; } }
// Per-symbol filename base, IDENTICAL to Python's urllib.parse.quote(name, safe="") used by
// deck.py _safe_name — encodeURIComponent leaves !*'() unescaped but quote() encodes them,
// so encode those too (world names contain "(VIX)").
function safeName(n) { return encodeURIComponent(n).replace(/[!*'()]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase()); }
// URL path segment for a lazy fetch. The on-disk file is named safeName(n) — which contains
// LITERAL "%xx" (e.g. "NIFTY%20BANK.json"). A web server decodes the request path ONCE, so to
// fetch that exact file the URL must be the %-encoding OF the filename (double-encoded). e.g.
// "NIFTY BANK" -> file "NIFTY%20BANK.json" -> request "NIFTY%2520BANK.json" -> server decodes
// once -> "NIFTY%20BANK.json". (Plain names like RELIANCE are unaffected: encode() is a no-op.)
function lazyURL(n) { return encodeURIComponent(safeName(n)); }

// Merge fetched {dates,series} frames into a growing in-memory store on a UNION date axis.
// Generic over stocks / indices / world (all share the {dates,series} shape).
function mergeFrames(store, frames) {
  const dset = new Set(store.dates);
  frames.forEach((f) => f.dates.forEach((d) => dset.add(d)));
  const dates = [...dset].sort();
  const reindex = (oldDates, arr) => { const ix = {}; oldDates.forEach((d, i) => (ix[d] = i)); return dates.map((d) => (d in ix ? arr[ix[d]] : null)); };
  const series = {};
  Object.keys(store.series).forEach((n) => (series[n] = reindex(store.dates, store.series[n])));
  frames.forEach((f) => Object.keys(f.series).forEach((n) => (series[n] = reindex(f.dates, f.series[n]))));
  store.dates = dates; store.series = series;
}

async function ensureStocksLoaded(names) {
  if (!LAZY || !LAZY.stocks) return;
  const sd = window.VISTAS_STOCKS || (window.VISTAS_STOCKS = { dates: [], series: {} });
  const set = stockNameSet();
  const need = names.filter((n) => set.has(n) && !sd.series[n]);
  if (!need.length) return;
  const frames = (await Promise.all(need.map((n) => fetchJSON(LAZY.base + "stocks/" + lazyURL(n) + ".json")))).filter(Boolean);
  if (frames.length) mergeFrames(sd, frames);
}

// indices are lazy-loaded per measure (the shell embeds only the default selection inline)
let IDX_NAMESET = {};
function indexNameSet(measure) {
  if (!IDX_NAMESET[measure]) IDX_NAMESET[measure] = new Set((LAZY && LAZY.indices && LAZY.indices[measure]) || []);
  return IDX_NAMESET[measure];
}
async function ensureIndicesLoaded(measure, names) {
  if (!LAZY || !LAZY.indices) return;
  const set = indexNameSet(measure);
  const md = MEASURES ? MEASURES[measure] : null;
  const need = names.filter((n) => set.has(n) && !(md && md.series[n]));
  if (!need.length) return;
  const store = (MEASURES && MEASURES[measure]) || (MEASURES[measure] = { dates: [], series: {} });
  const frames = (await Promise.all(need.map((n) => fetchJSON(LAZY.base + "indices/" + measure + "/" + lazyURL(n) + ".json")))).filter(Boolean);
  if (frames.length) mergeFrames(store, frames);
}

// world / cross-asset is lazy-loaded too (used in Performance mixes + the Macro global panels)
let WORLD_NAMESET = null;
function worldNameSet() { if (!WORLD_NAMESET) WORLD_NAMESET = new Set((LAZY && LAZY.world) || []); return WORLD_NAMESET; }
async function ensureWorldLoaded(names) {
  if (!LAZY || !LAZY.world) return;
  const wd = window.VISTAS_WORLD || (window.VISTAS_WORLD = { dates: [], series: {} });
  const set = worldNameSet();
  const need = names.filter((n) => set.has(n) && !wd.series[n]);
  if (!need.length) return;
  const frames = (await Promise.all(need.map((n) => fetchJSON(LAZY.base + "world/" + lazyURL(n) + ".json")))).filter(Boolean);
  if (frames.length) mergeFrames(wd, frames);
}
// active mutual-fund NAV is lazy-loaded too (selectable in the Prices view; NAV is a TR level)
let FUNDS_NAMESET = null;
function fundsNameSet() { if (!FUNDS_NAMESET) FUNDS_NAMESET = new Set((LAZY && LAZY.funds) || []); return FUNDS_NAMESET; }
async function ensureFundsLoaded(names) {
  if (!LAZY || !LAZY.funds) return;
  const fd = window.VISTAS_FUNDS || (window.VISTAS_FUNDS = { dates: [], series: {} });
  const set = fundsNameSet();
  const need = names.filter((n) => set.has(n) && !fd.series[n]);
  if (!need.length) return;
  const frames = (await Promise.all(need.map((n) => fetchJSON(LAZY.base + "funds_nav/" + lazyURL(n) + ".json")))).filter(Boolean);
  if (frames.length) mergeFrames(fd, frames);
}
function fundManifest() { return (LAZY && typeof window !== "undefined" && window.VISTAS_FUND_MANIFEST) ? window.VISTAS_FUND_MANIFEST : null; }
async function ensureFundamentals(sym) {
  if (!sym) return null;
  if (FUND_DATA && FUND_DATA[sym]) return FUND_DATA[sym];
  if (!LAZY || !LAZY.fundamentals) return null;
  const b = await fetchJSON(LAZY.base + "fundamentals/" + lazyURL(sym) + ".json");
  if (b) { if (!FUND_DATA) FUND_DATA = {}; FUND_DATA[sym] = b; }
  return b;
}
function quantManifest() { return (LAZY && typeof window !== "undefined" && window.VISTAS_QUANT_MANIFEST) ? window.VISTAS_QUANT_MANIFEST : null; }
async function ensureQuant(sym) {
  if (!sym) return null;
  if (QUANT_DATA && QUANT_DATA[sym]) return QUANT_DATA[sym];
  if (!LAZY || !LAZY.quant) return (QUANT_DATA && QUANT_DATA[sym]) || null;
  const b = await fetchJSON(LAZY.base + "quant/" + lazyURL(sym) + ".json");
  if (b) { if (!QUANT_DATA) QUANT_DATA = {}; QUANT_DATA[sym] = b; }
  return b;
}
// per-scheme mutual-fund HOLDINGS (look-through) — lazy-fetched into the Funds tab
function fundsHoldManifest() { return (LAZY && typeof window !== "undefined" && window.VISTAS_FUNDS_HOLDINGS_MANIFEST) ? window.VISTAS_FUNDS_HOLDINGS_MANIFEST : null; }
async function ensureFundsHoldings(key) {
  if (!key) return null;
  if (FUNDS_HOLD_DATA && FUNDS_HOLD_DATA[key]) return FUNDS_HOLD_DATA[key];
  if (!LAZY || !LAZY.funds_holdings) return (FUNDS_HOLD_DATA && FUNDS_HOLD_DATA[key]) || null;
  const b = await fetchJSON(LAZY.base + "funds_portfolio/" + lazyURL(key) + ".json");
  if (b) { if (!FUNDS_HOLD_DATA) FUNDS_HOLD_DATA = {}; FUNDS_HOLD_DATA[key] = b; }
  return b;
}

function fundsAttrManifest() { return (LAZY && typeof window !== "undefined" && window.VISTAS_FUNDS_ATTR_MANIFEST) ? window.VISTAS_FUNDS_ATTR_MANIFEST : null; }
// passive index/ETF + debt/liquid funds NOT in the 13-yr active-skill store — cockpit shows their book only
function fundsHoldonlyManifest() { return (LAZY && typeof window !== "undefined" && window.VISTAS_FUNDS_HOLDONLY_MANIFEST) ? window.VISTAS_FUNDS_HOLDONLY_MANIFEST : null; }
async function ensureFundsAttr(key) {
  if (!key) return null;
  if (FUNDS_ATTR_DATA && FUNDS_ATTR_DATA[key]) return FUNDS_ATTR_DATA[key];
  if (!LAZY || !LAZY.funds_attribution) return (FUNDS_ATTR_DATA && FUNDS_ATTR_DATA[key]) || null;
  const b = await fetchJSON(LAZY.base + "funds_attribution/" + lazyURL(key) + ".json");
  if (b) { if (!FUNDS_ATTR_DATA) FUNDS_ATTR_DATA = {}; FUNDS_ATTR_DATA[key] = b; }
  return b;
}

// NSE index BENCHMARK portfolios (EW + free-float-mcap reconstructed weights) — lazy per-index files
let BENCH_CACHE = {};
let FUNDS_BENCH = { slug: null, weight: "ffmcap" };       // current Funds-tab benchmark selection
let FUNDS_CMP = { peers: [], slug: null, weight: "ffmcap" }; // multi-fund side-by-side compare: peer fund codes + shared benchmark
const CMP_PAL = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e"];
function benchmarkManifest() { return (typeof window !== "undefined" && window.VISTAS_BENCHMARK_MANIFEST) ? window.VISTAS_BENCHMARK_MANIFEST : {}; }
async function ensureBenchmark(slug) {
  if (!slug) return null;
  if (BENCH_CACHE[slug]) return BENCH_CACHE[slug];
  const base = (LAZY && LAZY.base) || "data/";
  const b = await fetchJSON(base + "benchmarks/" + slug + ".json");
  if (b) BENCH_CACHE[slug] = b;
  return b;
}
// pick the standard benchmark slug for a fund's SEBI category (else Nifty 500 = broad-market default)
function defaultBenchForCategory(cat) {
  const m = benchmarkManifest();
  const slugOf = (name) => (m[name] && m[name].slug) || null;
  const c = String(cat || "").toLowerCase();
  let want = "NIFTY 500";
  if (/large\s*&|large and mid|large \& mid/.test(c)) want = "NIFTY LARGEMIDCAP 250";
  else if (/large/.test(c)) want = "NIFTY 100";
  else if (/mid/.test(c) && /small/.test(c)) want = "NIFTY MIDSMALLCAP 400";
  else if (/mid/.test(c)) want = "NIFTY MIDCAP 150";
  else if (/small/.test(c)) want = "NIFTY SMALLCAP 250";
  else if (/value|contra/.test(c)) want = "NIFTY 500";
  else if (/bank|financial/.test(c)) want = "NIFTY FINANCIAL SERVICES";
  else if (/pharma|health/.test(c)) want = "NIFTY PHARMA";
  else if (/\bit\b|technology/.test(c)) want = "NIFTY IT";
  else if (/fmcg|consum/.test(c)) want = "NIFTY FMCG";
  return slugOf(want) || slugOf("NIFTY 500") || slugOf("NIFTY 50");
}
// compare a fund's equity book to a benchmark: peer-free, true benchmark-relative active share + tilts
function fundsBenchCompare(holdings, bench, weightKey) {
  const wk = weightKey === "ew" ? "w_ew" : "w_ffmcap";
  // fund equity weights by vst_id, renormalised to the equity sleeve (sum 100). Accepts the baked
  // equity-book shape ({vst_id,pct,sector} — no asset_class) and the funds_portfolio shape (asset_class+industry).
  const eq = (holdings || []).filter((h) => h.vst_id && h.pct != null && (h.asset_class === undefined || /equ/i.test(h.asset_class || "")));
  let fsum = 0; const fW = {}, fSecOf = {}, fNameOf = {};
  eq.forEach((h) => { const w = +h.pct || 0; if (w <= 0) return; fW[h.vst_id] = (fW[h.vst_id] || 0) + w; fsum += w; fSecOf[h.vst_id] = h.sector || h.industry || "Unclassified"; fNameOf[h.vst_id] = h.name || h.symbol; });
  if (fsum > 0) Object.keys(fW).forEach((k) => { fW[k] = fW[k] * 100 / fsum; });
  // benchmark weights by vst_id
  const bW = {}, bSecOf = {}, bNameOf = {}, bSymOf = {};
  (bench.constituents || []).forEach((c) => { if (!c.vst_id) return; const w = +c[wk] || 0; bW[c.vst_id] = (bW[c.vst_id] || 0) + w; bSecOf[c.vst_id] = c.sector || "Unclassified"; bNameOf[c.vst_id] = c.name || c.symbol; bSymOf[c.vst_id] = c.symbol; });
  const ids = new Set([...Object.keys(fW), ...Object.keys(bW)]);
  let as2 = 0, overlap = 0, nOv = 0;
  const diffs = [];
  ids.forEach((id) => {
    const wf = fW[id] || 0, wb = bW[id] || 0;
    as2 += Math.abs(wf - wb); overlap += Math.min(wf, wb); if (wf > 0 && wb > 0) nOv++;
    diffs.push({ id, sym: bSymOf[id] || (fSecOf[id] ? "" : ""), name: bNameOf[id] || fNameOf[id] || id, wf, wb, d: wf - wb, sector: bSecOf[id] || fSecOf[id] || "—" });
  });
  const active_share = as2 / 2;                            // ½·Σ|wf−wb|
  // sector tilt: fund sector wt − benchmark sector wt
  const secF = {}, secB = {};
  Object.keys(fW).forEach((id) => { const s = fSecOf[id] || "Unclassified"; secF[s] = (secF[s] || 0) + fW[id]; });
  Object.keys(bW).forEach((id) => { const s = bSecOf[id] || "Unclassified"; secB[s] = (secB[s] || 0) + bW[id]; });
  const secs = new Set([...Object.keys(secF), ...Object.keys(secB)]);
  const tilt = [...secs].map((s) => ({ sector: s, f: secF[s] || 0, b: secB[s] || 0, d: (secF[s] || 0) - (secB[s] || 0) })).sort((a, b) => Math.abs(b.d) - Math.abs(a.d));
  const over = diffs.filter((x) => x.d > 0).sort((a, b) => b.d - a.d).slice(0, 8);
  const under = diffs.filter((x) => x.d < 0).sort((a, b) => a.d - b.d).slice(0, 8);
  return { active_share, overlap, n_overlap: nOv, n_fund: Object.keys(fW).length, n_bench: Object.keys(bW).length, tilt, over, under, eq_covered: eq.length };
}

async function computeBundle(body, measure) {
  measure = measure || "TR";
  if (OFFLINE) {
    const names = [...(body.tickers || []), ...(body.benchmarks || [])];
    const md = mergeLevel(measure, names) || measureData("TR");
    if (!md) return { error: `No ${measure} data embedded in this deck.` };
    return window.VistasAnalytics.analyze(md, body);
  }
  return await (await fetch("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.assign({}, body, { measure })) })).json();
}
async function computeValuation(body) {     // body carries measure + kind
  if (OFFLINE) {
    const md = measureData(body.measure);
    if (!md) return { error: `No ${body.measure} data embedded in this deck.` };
    return window.VistasAnalytics.valuationAnalyze(md, body);
  }
  return await (await fetch("/api/valuation", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
}

function num(x, d = 2) { return (x === null || x === undefined || Number.isNaN(x)) ? "—" : Number(x).toFixed(d); }
function pct(x, d = 2) { return (x === null || x === undefined || Number.isNaN(x)) ? "—" : (x * 100).toFixed(d) + "%"; }
function clsNum(x) { return (x === null || x === undefined || Number.isNaN(x)) ? "" : (x >= 0 ? "pos" : "neg"); }

function toast(msg, kind = "", ms = 3200) {
  const t = document.createElement("div");
  t.className = "toast " + kind; t.innerHTML = msg;
  $("toast").appendChild(t);
  if (ms) setTimeout(() => t.remove(), ms);
  return t;
}

// ---- colour system: one unique, evenly-spaced hue per selected series --------
function hsl(h, s, l) { return `hsl(${h}, ${s}%, ${l}%)`; }
function hsla(c, a) { return c.replace("hsl(", "hsla(").replace(")", `, ${a})`); }
function buildColors() {
  COLORS = {};
  const names = [...(BUNDLE.meta.tickers || []), ...(BUNDLE.meta.benchmarks || [])];
  const n = Math.max(names.length, 1);
  names.forEach((nm, i) => {
    const h = Math.round((360 * i) / n);
    COLORS[nm] = { base: hsl(h, 58, 44), light: hsl(h, 62, 63), dark: hsl(h, 72, 30) };
  });
  // the PRIMARY benchmark (e.g. NIFTY 500) is the neutral reference — a light warm grey so it
  // reads clearly on the dark canvas (it was black, invisible on the black-&-gold theme).
  const pb = primaryBench();
  if (pb) COLORS[pb] = { base: "#cdc7b7", light: "#8a8578", dark: "#e2ddcf" };
}
function primaryBench() { return (BUNDLE.meta.benchmarks || [])[0] || null; }
function cbase(n) { return (COLORS[n] && COLORS[n].base) || "#888"; }
function isBench(n) { return (BUNDLE.meta.benchmarks || []).includes(n) && !(BUNDLE.meta.tickers || []).includes(n); }
// translucent fill for area charts (light grey for the primary benchmark)
function fillColor(n) {
  if (n === primaryBench()) return "rgba(140,140,140,0.22)";
  const c = cbase(n);
  return c.indexOf("hsl") === 0 ? hsla(c, 0.15) : "rgba(120,120,120,0.15)";
}

// ---- per-chart series visibility (each chart independent; default = all shown) ----
function hiddenSet(key) { return CSEL[key] || (CSEL[key] = new Set()); }
function shown(key, name) { return !hiddenSet(key).has(name); }
function shownPair(key, t, b) { const h = hiddenSet(key); return !h.has(t) && !h.has(b); }
function attEsc(s) { return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;"); }
// A per-chart series toggle bar. Two ways to choose what's plotted, independent per chart:
//   • the CHECKBOXES pick any N series at once (check / uncheck);
//   • clicking a series' COLOUR CHIP isolates it (shows ONLY that one); click it again = show all;
//   • the "all" button restores everything.
// State = CSEL[key] (the set of HIDDEN names); `rerender` redraws just this chart.
function renderToggleBar(el, key, items, rerender) {
  const h = hiddenSet(key);
  const names = items.map((it) => it[0]);
  [...h].forEach((nm) => { if (!names.includes(nm)) h.delete(nm); });   // prune de-selected series
  if (items.length <= 1) { el.innerHTML = ""; return; }                 // nothing to toggle
  el.innerHTML = `<span class="toglbl">show</span>` + items.map(([nm, col]) =>
    `<span class="togitem"><button type="button" class="swbtn" style="background:${col}" data-only="${attEsc(nm)}" title="show only ${attEsc(nm)}"></button>` +
    `<label><input type="checkbox" data-n="${attEsc(nm)}" ${h.has(nm) ? "" : "checked"}>${nm}</label></span>`).join("") +
    `<button type="button" class="togall" title="show all series">all</button>`;
  el.querySelectorAll("input[type=checkbox]").forEach((cb) => cb.addEventListener("change", () => {
    if (cb.checked) h.delete(cb.dataset.n); else h.add(cb.dataset.n);
    rerender();                              // re-render ONLY this chart
  }));
  el.querySelectorAll(".swbtn").forEach((b) => b.addEventListener("click", () => {       // solo / isolate
    const only = b.dataset.only;
    const alreadySolo = (names.length - h.size === 1) && !h.has(only);   // this one is the lone visible series
    h.clear();
    if (!alreadySolo) names.forEach((nm) => { if (nm !== only) h.add(nm); });   // click the solo'd chip again = show all
    renderToggleBar(el, key, items, rerender); rerender();
  }));
  const allb = el.querySelector(".togall");
  if (allb) allb.addEventListener("click", () => { h.clear(); renderToggleBar(el, key, items, rerender); rerender(); });
}
function buildToggle(key, list) {
  const el = $("tog-" + key); if (!el) return;
  renderToggleBar(el, key, list.map((nm) => [nm, cbase(nm)]), () => { if (RENDER[key]) RENDER[key](); });
}

function baseLayout(extra) {
  return Object.assign({
    font: FONT, margin: { l: 56, r: 18, t: 14, b: 36 }, hovermode: "x unified",
    legend: { orientation: "h", y: -0.18, font: { size: 11 } },
    paper_bgcolor: "#F4F5F7", plot_bgcolor: "#F4F5F7",
    xaxis: { gridcolor: "#dfe3e8", zeroline: false },
    yaxis: { gridcolor: "#dfe3e8", zeroline: false },
  }, extra || {});
}
const PCONF = { responsive: true, displaylogo: false,
  modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"] };

// Plotly does NOT rescale the Y-axis when you zoom the X-range or drag the rangeslider, so on a
// long-history chart the zoomed-in window stays squished against the full-range Y-scale (KV: NAV
// zoom "doesn't rescale, so no point zooming"). attachYAutoscale re-fits Y to whatever X-window
// is in view (across the visible traces only), and restores autoscale on a double-click reset.
// Attached once per plot element; survives Plotly.react (which keeps the element + its handlers).
function attachYAutoscale(id) {
  const gd = $(id);
  if (!gd || typeof gd.on !== "function" || gd._yauto) return;   // no-op in the headless test stub
  gd._yauto = true;
  gd.on("plotly_relayout", (ev) => {
    if (!ev || gd._yauto_busy) return;
    if (ev["xaxis.autorange"] || ev["autosize"]) {               // double-click reset -> autoscale both
      gd._yauto_busy = true;
      Promise.resolve(Plotly.relayout(gd, { "yaxis.autorange": true })).then(() => { gd._yauto_busy = false; });
      return;
    }
    let x0 = ev["xaxis.range[0]"], x1 = ev["xaxis.range[1]"];
    if ((x0 === undefined || x1 === undefined) && ev["xaxis.range"]) { x0 = ev["xaxis.range"][0]; x1 = ev["xaxis.range"][1]; }
    if (x0 === undefined || x1 === undefined) return;             // not an x-zoom event
    const t0 = +new Date(x0), t1 = +new Date(x1);
    const isLog = !!(gd.layout && gd.layout.yaxis && gd.layout.yaxis.type === "log");
    let lo = Infinity, hi = -Infinity;
    (gd.data || []).forEach((tr) => {
      if (tr.visible === "legendonly" || tr.visible === false || tr.yaxis === "y2") return;
      const xs = tr.x || [], ys = tr.y || [];
      for (let i = 0; i < xs.length; i++) {
        const tx = +new Date(xs[i]); if (tx < t0 || tx > t1) continue;
        const yv = ys[i]; if (yv === null || yv === undefined || Number.isNaN(yv)) continue;
        if (isLog && yv <= 0) continue;
        if (yv < lo) lo = yv; if (yv > hi) hi = yv;
      }
    });
    if (!isFinite(lo) || !isFinite(hi) || lo === hi) return;
    let rng;
    if (isLog) { const a = Math.log10(lo), b = Math.log10(hi), p = (b - a) * 0.05 || 0.02; rng = [a - p, b + p]; }
    else { const p = (hi - lo) * 0.05 || Math.abs(hi) * 0.02 || 1; rng = [lo - p, hi + p]; }
    gd._yauto_busy = true;
    Promise.resolve(Plotly.relayout(gd, { "yaxis.range": rng, "yaxis.autorange": false })).then(() => { gd._yauto_busy = false; });
  });
}

// #49 follow-on — RE-REBASE on crop/zoom for rebase-to-100 charts. When the user crops the x-range
// (drag-zoom or rangeslider), restart every series at 100 from the LEFTMOST VISIBLE date so the two
// paths stay comparable INSIDE the window, then fit Y. Only when rebasing is ON (gd._rebaseOn());
// otherwise it behaves exactly like attachYAutoscale (fit Y only — e.g. the GP "Absolute level" mode).
// The draw site must, right after Plotly.react, set on the graph div:
//   gd._rebaseOn    = () => <bool>                          (is the chart currently rebased-to-100)
//   gd._rebaseBaseY = traces.map(t => (t.y||[]).slice())    (the COMMON-START basis y per trace; re-rebasing
//                     always works off THIS, so successive zooms never compound)
// then call attachRebaseZoom(id). Wired once; reads those live so it survives Plotly.react. Secondary-axis
// traces (volume etc., tr.yaxis==="y2") are left untouched — they are not rebasable levels.
function attachRebaseZoom(id) {
  const gd = $(id);
  if (!gd || typeof gd.on !== "function" || gd._rbwired) return;   // no-op in the headless stub
  gd._rbwired = true;
  gd.on("plotly_relayout", (ev) => {
    if (!ev || gd._rb_busy) return;
    const data = gd.data || [];
    const rebaseOn = !!(gd._rebaseOn && gd._rebaseOn());
    const baseY = (rebaseOn && gd._rebaseBaseY && gd._rebaseBaseY.length === data.length) ? gd._rebaseBaseY : null;
    // double-click / autoscale reset -> restore the common-start basis (if rebasing) + autoscale both
    if (ev["xaxis.autorange"] || ev["autosize"]) {
      gd._rb_busy = true;
      const done = () => { gd._rb_busy = false; };
      if (baseY) Promise.resolve(Plotly.update(gd, { y: baseY.map((a) => a.slice()) }, { "yaxis.autorange": true, "xaxis.autorange": true })).then(done);
      else Promise.resolve(Plotly.relayout(gd, { "yaxis.autorange": true })).then(done);
      return;
    }
    let x0 = ev["xaxis.range[0]"], x1 = ev["xaxis.range[1]"];
    if ((x0 === undefined || x1 === undefined) && ev["xaxis.range"]) { x0 = ev["xaxis.range"][0]; x1 = ev["xaxis.range"][1]; }
    if (x0 === undefined || x1 === undefined) return;               // not an x-zoom event
    const t0 = +new Date(x0), t1 = +new Date(x1);
    const isLog = !!(gd.layout && gd.layout.yaxis && gd.layout.yaxis.type === "log");
    const newYs = baseY ? [] : null;
    let lo = Infinity, hi = -Infinity;
    data.forEach((tr, ti) => {
      const xs = tr.x || [];
      const isY2 = (tr.yaxis === "y2");
      let yArr;
      if (baseY && !isY2) {                                         // re-rebase this level to 100 at the left edge
        const srcY = baseY[ti] || [];
        let base = null;
        for (let i = 0; i < xs.length; i++) { if (+new Date(xs[i]) < t0) continue; const v = srcY[i]; if (v == null || Number.isNaN(v)) continue; base = v; break; }
        yArr = base ? srcY.map((v) => (v == null || Number.isNaN(v)) ? v : +(v / base * 100).toFixed(4)) : srcY.slice();
      } else { yArr = tr.y || []; }
      if (newYs) newYs.push(yArr);
      if (tr.visible === "legendonly" || tr.visible === false || isY2) return;   // excluded from the Y fit
      for (let i = 0; i < xs.length; i++) { const tx = +new Date(xs[i]); if (tx < t0 || tx > t1) continue; const yv = yArr[i]; if (yv == null || Number.isNaN(yv)) continue; if (isLog && yv <= 0) continue; if (yv < lo) lo = yv; if (yv > hi) hi = yv; }
    });
    const apply = {};
    if (isFinite(lo) && isFinite(hi) && lo !== hi) {
      if (isLog) { const a = Math.log10(lo), b = Math.log10(hi), p = (b - a) * 0.05 || 0.02; apply["yaxis.range"] = [a - p, b + p]; }
      else { const p = (hi - lo) * 0.05 || Math.abs(hi) * 0.02 || 1; apply["yaxis.range"] = [lo - p, hi + p]; }
      apply["yaxis.autorange"] = false;
    }
    gd._rb_busy = true;
    const done = () => { gd._rb_busy = false; };
    if (newYs) Promise.resolve(Plotly.update(gd, { y: newYs }, apply)).then(done);
    else if (Object.keys(apply).length) Promise.resolve(Plotly.relayout(gd, apply)).then(done);
    else done();
  });
}

// ---- Relative-strength chart: horizon presets + crop-and-rebase (#48) ----------------------
// The baked rs_line now carries full (≤8y) history; the user picks a horizon (1M…5Y/MAX) or
// custom dates, and the line is re-rebased to 100 at the FIRST date in the chosen window so
// relative performance inside the crop is readable. Frontend-only; no analytics.py change.
let QRS_FULL = null;        // the full-history RS traces for the currently-shown stock
function rsPreset(key) {
  if (!QRS_FULL || !QRS_FULL.length) return null;
  let dmin = null, dmax = null;
  QRS_FULL.forEach((t) => { const d = t.x; if (d && d.length) { if (dmin === null || d[0] < dmin) dmin = d[0]; if (dmax === null || d[d.length - 1] > dmax) dmax = d[d.length - 1]; } });
  if (dmax === null) return null;
  if (key === "MAX") return [dmin, dmax];
  const mo = { "1M": 1, "3M": 3, "6M": 6, "1Y": 12, "2Y": 24, "3Y": 36, "5Y": 60 }[key] || 12;
  const s = new Date(dmax); s.setMonth(s.getMonth() - mo);
  const sIso = s.toISOString().slice(0, 10);
  return [sIso < dmin ? dmin : sIso, dmax];
}
function rsCrop(start, end) {
  if (!QRS_FULL) return;
  const cropped = QRS_FULL.map((t) => {
    const xs = [], ys = []; let base = null;
    for (let i = 0; i < t.x.length; i++) {
      const d = t.x[i]; if (d < start || d > end) continue;
      const v = t.y[i]; if (v === null || v === undefined) continue;
      if (base === null) base = v;
      xs.push(d); ys.push(base ? +(v / base * 100).toFixed(2) : v);
    }
    return Object.assign({}, t, { x: xs, y: ys });
  });
  Plotly.react("plot-quant-rs", cropped, baseLayout({ yaxis: { gridcolor: "#dfe3e8" } }), PCONF);
  attachYAutoscale("plot-quant-rs");
}
function buildRSCtl(defKey) {
  const host = $("rs-ctl"); if (!host) return;
  const keys = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "MAX"];
  host.innerHTML = '<div class="dr-presets">'
    + keys.map((k) => `<button data-k="${k}"${k === defKey ? ' class="active"' : ''}>${k}</button>`).join("")
    + '</div><input type="date" class="dr-from" id="rs-from"><span class="drsep">→</span><input type="date" class="dr-to" id="rs-to">';
  const clearActive = () => host.querySelectorAll(".dr-presets button").forEach((x) => x.classList.remove("active"));
  host.querySelectorAll(".dr-presets button").forEach((b) => b.addEventListener("click", () => {
    clearActive(); b.classList.add("active");
    const r = rsPreset(b.dataset.k);
    if (r) { rsCrop(r[0], r[1]); const f = $("rs-from"), t = $("rs-to"); if (f) f.value = r[0]; if (t) t.value = r[1]; }
  }));
  const f = $("rs-from"), t = $("rs-to");
  const onManual = () => { if (f.value && t.value && f.value <= t.value) { clearActive(); rsCrop(f.value, t.value); } };
  if (f) f.addEventListener("change", onManual);
  if (t) t.addEventListener("change", onManual);
}

// --------------------------------------------------------------------- fuzzy search
// Typo- + acronym-tolerant scoring shared by every search box (universe picker,
// single-select combos, command palette). Pure display layer — touches no analytics,
// so there is NO Python/parity port. Returns a 0..1 score; 0 = no match.
//   "absl flexi"  -> Aditya Birla Sun Life Flexi Cap Fund   (acronym token + word token)
//   "koatq qiant" -> Kotak Quant                            (Damerau edit distance: typos+transpositions)
// Design: tokenize the query; EVERY token must clear a floor against SOME target field
// (AND semantics); each token is scored as the best of {substring, word-prefix, acronym-
// prefix, edit-distance}. Targets are prepped once (memoized) — never per keystroke.
function fuzzyNorm(s) { return String(s == null ? "" : s).toLowerCase().replace(/[^a-z0-9\s]+/g, " ").replace(/\s+/g, " ").trim(); }
function fuzzyTokens(s) { const n = fuzzyNorm(s); return n ? n.split(" ") : []; }
function fuzzyAcr(words) { return words.map((w) => w[0]).join(""); }
// Build a reusable target descriptor. `primary` drives the acronym (the display name);
// `extra` (ticker, aliases, AMC, category, sub-label) widens substring/word matching
// without polluting the acronym.
function fuzzyPrep(primary, extra) {
  const pNorm = fuzzyNorm(primary);
  const pWords = pNorm ? pNorm.split(" ") : [];
  const allNorm = fuzzyNorm((primary || "") + " " + (extra || ""));
  const allWords = allNorm ? allNorm.split(" ") : [];
  return { norm: allNorm, words: allWords, flat: allNorm.replace(/ /g, ""), acr: fuzzyAcr(pWords) };
}
// Damerau–Levenshtein (handles substitution/insert/delete AND transposition, e.g.
// "koatq"->"kotak") with a cap + per-row early-exit so non-matches bail cheaply.
function damerauLev(a, b, cap) {
  const la = a.length, lb = b.length;
  if (cap == null) cap = Math.max(la, lb);
  if (Math.abs(la - lb) > cap) return cap + 1;
  if (!la) return lb; if (!lb) return la;
  let row0 = new Array(lb + 1), row1 = new Array(lb + 1), row2 = new Array(lb + 1);
  for (let j = 0; j <= lb; j++) row1[j] = j;
  for (let i = 1; i <= la; i++) {
    row2[0] = i; let rowMin = i;
    const ai = a.charCodeAt(i - 1);
    for (let j = 1; j <= lb; j++) {
      const cost = ai === b.charCodeAt(j - 1) ? 0 : 1;
      let v = Math.min(row1[j] + 1, row2[j - 1] + 1, row1[j - 1] + cost);
      if (i > 1 && j > 1 && ai === b.charCodeAt(j - 2) && a.charCodeAt(i - 2) === b.charCodeAt(j - 1))
        v = Math.min(v, row0[j - 2] + 1);   // transposition
      row2[j] = v; if (v < rowMin) rowMin = v;
    }
    if (rowMin > cap) return cap + 1;        // whole row already over budget — bail
    const tmp = row0; row0 = row1; row1 = row2; row2 = tmp;
  }
  return row1[lb];
}
// Best score of ONE query token against a prepped target.
function fuzzyTokenScore(qt, P) {
  if (!qt) return 1;
  if (P.norm.indexOf(qt) === 0) return 1.0;                 // prefix of whole name
  let best = 0;
  if (P.norm.indexOf(qt) >= 0) best = 0.86;                 // substring somewhere
  for (const w of P.words) {
    if (w === qt) { best = 1.0; break; }                    // exact word
    if (w.indexOf(qt) === 0) { if (best < 0.95) best = 0.95; }   // word prefix
    else if (best < 0.85 && w.indexOf(qt) >= 0) best = 0.85;     // word substring
  }
  if (best < 1.0 && P.acr) {
    if (P.acr === qt) best = 1.0;
    else if (P.acr.indexOf(qt) === 0 && best < 0.92) best = 0.92;   // acronym prefix ("absl"⊂"abslfcf")
  }
  // glued compound ("smallcap" = "small cap"): exact char sequence ignoring spaces — leak-proof
  if (best < 0.9 && qt.length >= 4 && P.flat.indexOf(qt) >= 0) best = 0.9;
  if (best >= 0.95) return best;                            // good enough — skip edit distance
  if (qt.length < 4) return best;                          // short tokens: substring/prefix/acronym only —
                                                           // edit distance on 2-3 chars leaks (kota→tata, bank→bata)
  const tol = Math.floor((qt.length - 1) / 4) + 1;         // ~1 typo per 4 chars (len 4-7 → 1 edit, not 2)
  for (const w of P.words) {
    if (Math.abs(w.length - qt.length) > tol) continue;
    const d = damerauLev(qt, w, tol);
    if (d <= tol) { const s = 1 - d / Math.max(qt.length, w.length); if (s > best) best = s; }
  }
  if (P.acr && Math.abs(P.acr.length - qt.length) <= tol) {
    const d = damerauLev(qt, P.acr, tol);
    if (d <= tol) { const s = 0.85 * (1 - d / Math.max(qt.length, P.acr.length)); if (s > best) best = s; }
  }
  return best;
}
const FUZZY_FLOOR = 0.5;
// Full query score against a prepped target (or a raw string). AND across query tokens.
function fuzzyScore(query, prep) {
  const P = (typeof prep === "string") ? fuzzyPrep(prep) : prep;
  const qToks = fuzzyTokens(query);
  if (!qToks.length) return String(query == null ? "" : query).trim() ? 0 : 1;   // punctuation-only query → no match; empty → all
  let sum = 0;
  for (const qt of qToks) {
    const s = fuzzyTokenScore(qt, P);
    if (s < FUZZY_FLOOR) return 0;                          // every query token must hit something
    sum += s;
  }
  let score = sum / qToks.length;
  const qn = fuzzyNorm(query);
  if (qn && P.norm.indexOf(qn) >= 0) score = Math.min(1, score + 0.15);   // contiguity bonus
  return score;
}

// --------------------------------------------------------------------- MultiSelect
class MultiSelect {
  constructor(el, opts) {
    this.el = el; this.placeholder = opts.placeholder || "select…";
    this.onToggle = opts.onToggle || (() => {});
    this.onPickNoHistory = opts.onPickNoHistory || null;
    this.options = []; this.sel = [];
    el.classList.add("ms");
    el.innerHTML = `<div class="box empty" data-ph="${this.placeholder}"></div>
      <div class="pop"><input class="search" placeholder="name, ticker or acronym…"><div class="list"></div></div>`;
    this.box = el.querySelector(".box");
    this.pop = el.querySelector(".pop");
    this.search = el.querySelector(".search");
    this.list = el.querySelector(".list");
    this.box.addEventListener("click", (e) => {
      if (e.target.classList.contains("x")) return;
      el.classList.add("open"); this.search.focus(); this.renderList();
    });
    this.search.addEventListener("input", () => this.renderList());
    document.addEventListener("click", (e) => { if (!el.contains(e.target)) el.classList.remove("open"); });
  }
  setOptions(options) { this.options = options; this.renderBox(); }
  get value() { return [...this.sel]; }
  setValue(arr) { this.sel = arr.filter(Boolean); this.renderBox(); }
  add(name) { if (!this.sel.includes(name)) { this.sel.push(name); this.renderBox(); } }
  remove(name) { this.sel = this.sel.filter((x) => x !== name); this.renderBox(); }
  _hasHistory(name) { const o = this.options.find((x) => x.name === name); return !o || o.has_history; }
  renderBox() {
    this.box.classList.toggle("empty", this.sel.length === 0);
    this.box.innerHTML = this.sel.map((n) =>
      `<span class="tag ${this._hasHistory(n) ? "" : "nohist"}">${n}<span class="x" data-n="${n}">×</span></span>`).join("");
    this.box.querySelectorAll(".x").forEach((x) =>
      x.addEventListener("click", (e) => { e.stopPropagation(); this.remove(x.dataset.n); this.onToggle(); }));
  }
  // memoized fuzzy descriptor: acronym from the company label ("HUL"→Hindustan Unilever,
  // "TCS"→Tata Consultancy Services); ticker + former-ticker aliases widen matching.
  _prep(o) {
    if (o._fz === undefined) o._fz = fuzzyPrep(o.label || o.name, o.name + " " + (o.aliases ? o.aliases.join(" ") : ""));
    return o._fz;
  }
  // fuzzy score (0 = no match): name/label substring, word-prefix, acronym, or edit-distance typo.
  _score(o, q) { return q ? fuzzyScore(q, this._prep(o)) : 1; }
  _match(o, q) { return this._score(o, q) > 0; }
  renderList() {
    const q = this.search.value.trim().toLowerCase();
    const groups = {};
    this.options.forEach((o) => {
      const sc = this._score(o, q);
      if (sc <= 0) return;
      (groups[o.group] = groups[o.group] || []).push([o, sc]);
    });
    let html = "";
    Object.keys(groups).forEach((g) => {
      const arr = groups[g];
      if (q) arr.sort((a, b) => b[1] - a[1]);   // best match first within each group
      html += `<div class="grp">${g}</div>`;
      arr.forEach(([o]) => {
        const checked = this.sel.includes(o.name) ? "checked" : "";
        const meta = o.has_history ? `${o.start ? o.start.slice(0, 4) : ""}–${o.end ? o.end.slice(0, 4) : ""}` : "not local";
        const lblText = (o.label || "") + ((o.aliases && o.aliases.length) ? `  ·  was ${o.aliases.join(", ")}` : "");
        const lbl = lblText ? `<span class="olabel">${lblText}</span>` : "";
        html += `<label class="opt ${o.has_history ? "" : "nohist"}">
          <input type="checkbox" data-n="${o.name}" ${checked}>
          <span class="name">${o.name}</span>${lbl}<span class="meta">${meta}</span></label>`;
      });
    });
    this.list.innerHTML = html || `<div class="empty-note">no match</div>`;
    this.list.querySelectorAll("input[type=checkbox]").forEach((cb) =>
      cb.addEventListener("change", () => this._toggle(cb.dataset.n, cb.checked)));
  }
  _toggle(name, on) {
    if (!on) { this.remove(name); this.onToggle(); return; }
    if (!this._hasHistory(name) && this.onPickNoHistory) { this.onPickNoHistory(name, this); return; }
    this.add(name); this.onToggle();
  }
}

let MS_T, MS_B;

// --------------------------------------------------------------------- init
async function init() {
  try {
    CAT = await getCatalog();
  } catch (e) { toast("Failed to load catalog: " + e, "err", 0); return; }
  if (CAT.error) { toast("Catalog error: " + CAT.error, "err", 0); return; }

  refreshCatalogUI();
  const offNote = OFFLINE ? (CAT.deck_built ? ` · offline deck built ${CAT.deck_built}` : " · offline deck") : "";
  $("foot").textContent = `Vistas passive · source ${CAT.source_file} · history ${CAT.data_start} → ${CAT.data_end}${offNote}`;

  MS_T = new MultiSelect($("ms-tickers"), { placeholder: "add indices…", onToggle: scheduleRun, onPickNoHistory: pickFetch });
  MS_B = new MultiSelect($("ms-bench"), { placeholder: "add benchmarks…", onToggle: scheduleRun, onPickNoHistory: pickFetch });
  MS_T.setOptions(CAT.indices); MS_B.setOptions(CAT.indices);
  MS_T.setValue(CAT.default_tickers || []);
  MS_B.setValue(CAT.default_benchmark ? [CAT.default_benchmark] : []);

  $("start").value = CAT.data_start; $("start").min = CAT.data_start; $("start").max = CAT.data_end;
  $("end").value = CAT.data_end; $("end").min = CAT.data_start; $("end").max = CAT.data_end;
  setPresetActive("0");

  ["start", "end", "freq", "rollwin", "alphatype", "rf"].forEach((id) => $(id).addEventListener("change", scheduleRun));
  ["start", "end"].forEach((id) => $(id).addEventListener("change", syncDateStrips));   // keep the tab date strips in sync
  $("run").addEventListener("click", run);
  $("presets").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => applyPreset(b.dataset.y)));
  $("logscale").addEventListener("change", () => BUNDLE && renderGP());
  initOptsToggle();
  initCtlCondense();
  if (OFFLINE) {
    // server-only controls don't apply in a saved deck — hide them
    ["refresh", "fetchall", "fetchcancel", "addidx", "exportxl", "savedeck"].forEach((id) => { const b = $(id); if (b) b.style.display = "none"; });
  } else {
    $("refresh").addEventListener("click", doRefresh);
    $("fetchall").addEventListener("click", startFetchAll);
    $("fetchcancel").addEventListener("click", async () => { await fetch("/api/fetch_cancel", { method: "POST" }); });
    $("addidx").addEventListener("click", () => {
      const n = prompt("Exact NSE index name to fetch (e.g. NIFTY HEALTHCARE):");
      if (n) pickFetch(n.trim(), MS_T);
    });
    $("exportxl").addEventListener("click", () => {
      toast(`<span class="spinner"></span>Building full NAV workbook… the download will start shortly.`, "work", 5000);
      window.location = "/api/export_excel";   // attachment response -> browser downloads, page stays
    });
    $("savedeck").addEventListener("click", doSaveDeck);
  }
  segWire("alpha-seg", renderAlpha);
  segWire("risk-seg", renderRisk);
  $("month-pick").addEventListener("change", renderMonth);
  document.querySelectorAll("[data-csv]").forEach((b) => b.addEventListener("click", () => exportCSV(b.dataset.csv)));

  // tabs + measure selectors (Bloomberg-style views)
  $("tabs").querySelectorAll(".tab[data-view]").forEach((b) =>
    b.addEventListener("click", () => { if (!b.disabled) setView(b.dataset.view); }));
  segMeasure("measure-seg", (m) => {
    PERF_MEASURE = m;
    $("measure-note").textContent = (m === "PR")
      ? "Price-return index — dividends NOT reinvested."
      : "Total-return index — dividends reinvested.";
    run();
  });
  segMeasure("val-seg", (m) => { VAL_MEASURE = m; runValuation(true); });
  syncMeasureAvailability();
  await initFundamentals();
  await initQuant();
  await initFunds();
  await initFundSkill();

  // resume a fetch-all already in progress (e.g. page reloaded mid-run)
  if (!OFFLINE) {
    try { const st = await (await fetch("/api/fetch_status")).json(); if (st.running) { $("fetchall").style.display = "none"; $("fetchcancel").style.display = ""; pollFetch(); } } catch (e) {}
  }

  initCmdk();
  initScreen();
  initMacro();
  initGPToggle();       // #49 rebase / absolute level toggle on the GP chart
  initAllocator();      // Asset Allocator tab (market breadth) + relocate the consensus cockpit here
  initOwnership();      // Ownership & Flow tab (#102) — the money-flow waterfall (AMC -> sector)
  initDateStrips();     // compact window strips on the Fundamentals + Macro measurebars
  applyHash();          // restore a shared/bookmarked view+selection before the first compute
  run();
}

function refreshCatalogUI() {
  $("asof").innerHTML = `data as of <b>${CAT.data_asof}</b> · ${CAT.n_local} indices local`;
  $("datainfo").textContent = (OFFLINE || CAT.n_total === CAT.n_local)
    ? `${CAT.source_file} · ${CAT.n_total} indices (offline deck)`
    : `${CAT.source_file} · ${CAT.n_total} selectable (${CAT.n_local} with local history, rest fetchable)`;
}

function segWire(id, fn) {
  $(id).querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => {
      $(id).querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); if (BUNDLE) fn();
    }));
}
function segVal(id) { return $(id).querySelector("button.active").dataset.k; }
function setPresetActive(y) { $("presets").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.y === y)); }
function presetWindow(y) {           // [start,end] for a quick-range button (y years; "0" = Max)
  if (y === "0") return [CAT.data_start, CAT.data_end];
  const end = new Date(CAT.data_end), s = new Date(end);
  s.setFullYear(s.getFullYear() - parseInt(y, 10));
  const min = new Date(CAT.data_start);
  return [(s < min ? min : s).toISOString().slice(0, 10), CAT.data_end];
}
function applyPreset(y) {
  setPresetActive(y);
  const [s, e] = presetWindow(y); $("start").value = s; $("end").value = e;
  syncDateStrips();
  scheduleRun();
}

// ---- cross-tab date-range linking (KV 2026-06-21) ----
// One shared window (#start/#end) drives Performance, Fundamentals and Macro. The full control
// bar is hidden on Fundamentals + Macro, so each gets a COMPACT date strip in its measurebar
// (quick ranges + From/To) bound to the same #start/#end. With #linkdates ON (default) the
// window is shared across all tabs and a tab switch keeps it; with it OFF, each tab remembers
// its own window (TAB_WIN), restored on switch.
function linked() { const c = $("linkdates"); return !c || c.checked; }
function activePresetY() { const a = $("presets").querySelector("button.active"); return a ? a.dataset.y : ""; }
function activeTabRerender() {
  if (VIEW === "macro") renderMacro();
  else if (VIEW === "fundamentals") renderFundamentals();
  else scheduleRun();
}
function syncDateStrips() {           // mirror the shared #start/#end (+active preset) into both strips
  const s = $("start").value, e = $("end").value, y = activePresetY();
  document.querySelectorAll(".daterange").forEach((host) => {
    const f = host.querySelector(".dr-from"), t = host.querySelector(".dr-to");
    if (f) f.value = s; if (t) t.value = e;
    host.querySelectorAll(".dr-presets button").forEach((b) => b.classList.toggle("active", b.dataset.y === y));
  });
}
function buildDateStrip(hostId) {
  const host = $(hostId); if (!host) return;
  host.innerHTML =
    '<span class="drlbl">Window</span>' +
    '<div class="dr-presets">' +
      ["1", "3", "5", "10", "0"].map((y) => `<button data-y="${y}">${y === "0" ? "Max" : y + "Y"}</button>`).join("") +
    "</div>" +
    '<input type="date" class="dr-from"><span class="drsep">→</span><input type="date" class="dr-to">';
  host.querySelectorAll(".dr-presets button").forEach((b) => b.addEventListener("click", () => {
    setPresetActive(b.dataset.y);
    const [s, e] = presetWindow(b.dataset.y); $("start").value = s; $("end").value = e;
    onWindowChanged();
  }));
  const from = host.querySelector(".dr-from"), to = host.querySelector(".dr-to");
  from.min = to.min = CAT.data_start; from.max = to.max = CAT.data_end;
  from.addEventListener("change", () => { if (from.value) { $("start").value = from.value; setPresetActive(""); onWindowChanged(); } });
  to.addEventListener("change", () => { if (to.value) { $("end").value = to.value; setPresetActive(""); onWindowChanged(); } });
}
function onWindowChanged() {           // a measurebar date strip changed the shared window
  syncDateStrips();
  if (!linked()) TAB_WIN[VIEW] = [$("start").value, $("end").value];
  activeTabRerender();
}
function initDateStrips() {
  buildDateStrip("macro-daterange");
  buildDateStrip("fund-daterange");
  syncDateStrips();
  const ld = $("linkdates");
  if (ld) ld.addEventListener("change", () => { if (ld.checked) activeTabRerender(); });  // re-link → adopt global window
}
function scheduleRun() { clearTimeout(runTimer); runTimer = setTimeout(run, 250); }

// advanced-options drawer: expanded on a wide screen, collapsed on a phone (keeps the
// control bar slim on mobile — universe/benchmark + quick-range + Analyze stay visible)
function initOptsToggle() {
  const adv = $("adv"), btn = $("optstoggle");
  if (!adv || !btn) return;
  const set = (open) => { adv.classList.toggle("collapsed", !open); btn.classList.toggle("on", open); btn.setAttribute("aria-expanded", open ? "true" : "false"); };
  let open = false;   // collapsed by default — From/To/Frequency now live on the always-visible control line (KV 2026-06-22)
  set(open);
  btn.addEventListener("click", () => { open = !open; set(open); if (open) { const c = $("ctl"); if (c) c.classList.remove("condensed"); } });
}

// The Prices control bar now SCROLLS AWAY with the content (only the tab strip stays pinned), so
// the old condense-on-scroll workaround is retired — kept as a no-op for call-site stability.
// (KV 2026-06-23)
function initCtlCondense() { /* no-op: the control bar scrolls away naturally now */ }

// --------------------------------------------------------------------- fetch flows
async function pickFetch(name, ms) {
  if (OFFLINE) { toast("This is an offline deck — fetching new indices needs the live Vistas app.", "err", 5000); return; }
  if (!confirm(`"${name}" has no local history. Fetch its full history from NSE now? (one-time, needs internet)`)) return;
  const t = toast(`<span class="spinner"></span>Fetching ${name} from NSE…`, "work", 0);
  try {
    const r = await (await fetch("/api/add_index", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) })).json();
    t.remove();
    if (!r.ok) { toast(`Could not fetch "${name}": ${r.error || "unknown"}`, "err", 6000); return; }
    if (r.added) {
      toast(`Added ${name} (${r.n_obs} obs).`, "ok");
      CAT = await (await fetch("/api/catalog")).json();
      MS_T.setOptions(CAT.indices); MS_B.setOptions(CAT.indices); refreshCatalogUI();
    } else { toast(r.message || "Already present.", "ok"); }
    ms.add(name); run();
  } catch (e) { t.remove(); toast("Fetch error: " + e, "err", 6000); }
}

async function doRefresh() {
  if (OFFLINE) return;
  const t = toast(`<span class="spinner"></span>Refreshing to today…`, "work", 0);
  try {
    const r = await (await fetch("/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json();
    t.remove();
    if (!r.ok) { toast("Refresh failed: " + (r.error || "API unreachable (expected on a cloud host)"), "err", 7000); return; }
    if (r.updated) {
      toast(`Updated → ${r.new_asof} (+${r.rows_added} rows).`, "ok");
      CAT = await (await fetch("/api/catalog")).json(); refreshCatalogUI(); run();
    } else { toast(r.message || "Already up to date.", "ok"); }
  } catch (e) { t.remove(); toast("Refresh error: " + e, "err", 7000); }
}

async function doSaveDeck() {
  if (OFFLINE) return;
  const t = toast(`<span class="spinner"></span>Building offline deck (embedding full history)…`, "work", 0);
  try {
    const r = await (await fetch("/api/save_deck", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json();
    t.remove();
    if (!r.ok) { toast("Save deck failed: " + (r.error || "unknown"), "err", 7000); return; }
    toast(`Saved offline deck → <b>${r.file}</b> (${r.size_mb} MB) in the <b>output/</b> folder. Open it in any browser — no server, no internet needed.`, "ok", 9000);
  } catch (e) { t.remove(); toast("Save deck error: " + e, "err", 7000); }
}

async function startFetchAll() {
  if (OFFLINE) return;
  if (!confirm("Fetch FULL history for every NSE index not yet local?\n\nThis can take several minutes and needs internet. You can keep using Vistas while it runs; click Stop to cancel.")) return;
  try {
    const r = await (await fetch("/api/fetch_all", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json();
    if (!r.ok) { toast("Fetch-all failed: " + (r.error || "unknown"), "err", 7000); return; }
    if (r.nothing_to_fetch) { toast(r.message || "All catalog indices already local.", "ok"); return; }
    $("fetchall").style.display = "none"; $("fetchcancel").style.display = "";
    pollFetch();
  } catch (e) { toast("Fetch-all error: " + e, "err", 7000); }
}

function pollFetch() {
  if (OFFLINE) return;
  clearTimeout(fetchPoll);
  fetchPoll = setTimeout(async () => {
    try {
      const st = await (await fetch("/api/fetch_status")).json();
      if (st.running) {
        $("fetchprog").textContent = `Fetching ${st.done}/${st.total}… ${st.current || ""}  (${st.added.length} added, ${st.failed.length} failed)`;
        pollFetch();
      } else {
        $("fetchcancel").style.display = "none"; $("fetchall").style.display = "";
        if (st.error) { toast("Fetch-all error: " + st.error, "err", 9000); $("fetchprog").textContent = ""; return; }
        $("fetchprog").textContent = `Done: ${(st.added || []).length} added, ${(st.failed || []).length} failed.`;
        setTimeout(() => { $("fetchprog").textContent = ""; }, 9000);
        toast(`Fetch-all complete: ${(st.added || []).length} added, ${(st.failed || []).length} failed.`, "ok", 6000);
        CAT = await (await fetch("/api/catalog")).json();
        MS_T.setOptions(CAT.indices); MS_B.setOptions(CAT.indices); refreshCatalogUI();
      }
    } catch (e) { $("fetchcancel").style.display = "none"; $("fetchall").style.display = ""; toast("Fetch status error: " + e, "err", 6000); }
  }, 1500);
}

// --------------------------------------------------------------------- analyze
async function run() {
  const tickers = MS_T.value, benchmarks = MS_B.value;
  if (!tickers.length && !benchmarks.length) { toast("Select at least one index.", "err"); return; }
  const body = {
    tickers, benchmarks, start: $("start").value, end: $("end").value,
    freq: $("freq").value, rolling_window: $("rollwin").value,
    alpha_type: $("alphatype").value, rf_annual: (parseFloat($("rf").value) || 0) / 100,
  };
  const t = toast(`<span class="spinner"></span>Computing…`, "work", 0);
  try {
    const need = [...tickers, ...benchmarks];                 // hosted: fetch any not-yet-loaded series
    await Promise.all([ensureIndicesLoaded(PERF_MEASURE, need), ensureWorldLoaded(need), ensureStocksLoaded(need), ensureFundsLoaded(need)]);
    const r = await computeBundle(body, PERF_MEASURE);
    t.remove();
    if (r.error) { toast(r.error, "err", 5000); return; }
    CSEL = {};            // fresh analysis -> every chart starts with all series shown
    BUNDLE = r; buildColors(); renderAll();
    runValuation(false);  // keep the Valuation tab in sync with the same selection/window
    writeHash();
  } catch (e) { t.remove(); toast("Analyze error: " + e, "err", 6000); }
}

// ---- valuation: compute + render the Valuation tab ----
async function runValuation(activate) {
  if (!hasMeasure(VAL_MEASURE)) {
    VAL_BUNDLE = null;
    $("val-note").textContent = `No ${VAL_MEASURE} data in this deck.`;
    if (activate || VIEW === "valuation") valuationEmpty(`No ${VAL_MEASURE} data available.`);
    return;
  }
  const body = {
    tickers: MS_T.value, benchmarks: MS_B.value, start: $("start").value, end: $("end").value,
    freq: $("freq").value, measure: VAL_MEASURE, kind: MEASURE_KIND[VAL_MEASURE] || "ratio",
  };
  try {
    await ensureIndicesLoaded(VAL_MEASURE, [...MS_T.value, ...MS_B.value]);   // hosted: fetch ratio series
    const r = await computeValuation(body);
    if (r.error) { VAL_BUNDLE = null; if (VIEW === "valuation") valuationEmpty(r.error); return; }
    VAL_BUNDLE = r;
    $("val-note").textContent = `${VAL_LABEL[VAL_MEASURE] || VAL_MEASURE} · each series judged against its own history`;
    if (VIEW === "valuation" || activate) renderValuation();
  } catch (e) { VAL_BUNDLE = null; if (VIEW === "valuation") valuationEmpty("Valuation error: " + e); }
}

function renderAll() {
  // per-chart "show" checkboxes (all selected series; toggle within a chart without
  // affecting the others). Monthly has its own single-series picker.
  const allcols = [...BUNDLE.meta.tickers, ...BUNDLE.meta.benchmarks];
  ["gp", "comp", "alpha", "risk", "corrmat", "capture", "cy", "dist"].forEach((key) => buildToggle(key, allcols));
  renderGP(); renderCOMP(); renderAlpha(); renderRisk();
  renderCorrMatrix(); renderCapture(); renderCY(); renderMonthPicker(); renderMonth(); renderDist();
  afterPaint(() => viewPlotsResize("performance"));
}

function lineTrace(name, x, y, opts) {
  return Object.assign({
    type: "scatter", mode: "lines", name, x, y, connectgaps: false,
    line: { color: cbase(name), width: isBench(name) ? 1.6 : 2.1, dash: isBench(name) ? "dot" : "solid" },
  }, opts || {});
}

// GP -----------------------------------------------------------------
// #49 — "Rebase to 100" vs "Absolute level" toggle (pure DISPLAY layer; analytics.py untouched).
// BUNDLE.levels[c] is already rebased to 100 at the common-start by the analytics layer. To show the
// underlying total-return index LEVEL we recover the raw absolute value at the first charted date from
// the embedded measure frame and SCALE the rebased series by it (a single per-series constant — the
// shape is identical, only the y-units change). If the raw frame can't be matched we fall back to the
// rebased series so the panel never breaks.
function gpAbsoluteSeries(c, x) {
  const reb = (BUNDLE.levels || {})[c]; if (!reb || !reb.length) return null;
  try {
    const md = mergeLevel(PERF_MEASURE, [c]);                 // raw level frame for this one series
    if (!md || !md.series || !md.series[c] || !md.dates) return null;
    const idxOf = {}; md.dates.forEach((d, i) => { idxOf[d] = i; });
    // anchor on the first charted date that has BOTH a rebased value and a raw level
    let scale = null;
    for (let i = 0; i < x.length; i++) {
      const rv = reb[i]; if (rv === null || rv === undefined || !isFinite(rv) || rv === 0) continue;
      const j = idxOf[x[i]]; if (j === undefined) continue;
      const raw = md.series[c][j];
      if (raw === null || raw === undefined || !isFinite(raw)) continue;
      scale = raw / rv; break;                                 // raw = rebased × scale
    }
    if (scale === null) return null;
    return reb.map((v) => (v === null || v === undefined || !isFinite(v)) ? null : +(v * scale).toFixed(4));
  } catch (e) { return null; }
}
function renderGP() {
  const x = BUNDLE.dates;
  const cols = [...BUNDLE.meta.tickers, ...BUNDLE.meta.benchmarks].filter((c) => shown("gp", c));
  const absMode = (GP_MODE === "absolute");
  let anyAbs = false;
  const traces = cols.map((c) => {
    let y = BUNDLE.levels[c];
    if (absMode) { const a = gpAbsoluteSeries(c, x); if (a) { y = a; anyAbs = true; } }
    return lineTrace(c, x, y);
  });
  const log = $("logscale").checked;
  const showingAbs = absMode && anyAbs;
  const yaxis = { gridcolor: "#dfe3e8", title: showingAbs ? "Index level" : "Rebased (=100)", type: log ? "log" : "linear" };
  // clean log ticks at 1/2/5 per decade (100·200·500·1k…) — readable at any range,
  // not the cluttered 1-9 minor labels, and not so sparse it looks unchanged
  if (log) { yaxis.dtick = "D2"; yaxis.tickformat = "~s"; }
  const layout = baseLayout({
    yaxis,
    xaxis: { gridcolor: "#dfe3e8", rangeslider: { thickness: 0.07 }, type: "date" },
  });
  Plotly.react("plot-gp", traces, layout, PCONF);
  // zoom/crop -> rebase mode: restart each series at 100 from the left edge of the window, then fit Y;
  // absolute mode: fit Y only (no re-rebasing). Stash the common-start basis so re-rebasing never compounds.
  const gpgd = $("plot-gp");
  if (gpgd) { gpgd._rebaseOn = () => (GP_MODE === "rebase"); gpgd._rebaseBaseY = traces.map((t) => (t.y || []).slice()); }
  attachRebaseZoom("plot-gp");
  const parts = cols.map((c) => { const s = BUNDLE.stats.find((r) => r.name === c); return `${c}: ${pct(s.cagr)} CAGR, ${pct(s.total_return)} total`; });
  let head = `Window ${BUNDLE.meta.start} → ${BUNDLE.meta.end} · ${BUNDLE.meta.n_obs} ${BUNDLE.meta.freq} obs`
    + (showingAbs ? "\nShowing the underlying total-return index LEVEL (not rebased). Toggle “Rebase to 100” for like-for-like paths." : "");
  if (absMode && !anyAbs)
    head += `\nℹ Absolute level isn’t available for these series in this deck — showing the rebased view.`;
  if (!absMode && BUNDLE.meta.truncated)
    head += `\n⚠ All series rebased to 100 at ${BUNDLE.meta.common_start} — the latest date EVERY selected series has data (you requested ${BUNDLE.meta.requested_start}; earlier history is excluded so the comparison is like-for-like). Per-series start dates are in the COMP table.`;
  const excl = BUNDLE.meta.excluded_noncontinuous || [];
  if (excl.length)
    head += `\n⚠ Excluded (non-continuous over this window — a multi-month trading gap would fabricate a return): ${excl.join(", ")}. Pick a shorter, continuous window to chart them.`;
  $("stat-gp").textContent = head + "\n" + parts.join("  |  ");
}

// #49 — inject the "Rebase to 100 / Absolute level" segmented toggle into the GP panel's .tools bar.
// Done once at init (idempotent); flips GP_MODE and re-renders only the GP chart.
function initGPToggle() {
  const panel = $("p-gp"); if (!panel) return;
  const tools = panel.querySelector(".tools"); if (!tools || $("gp-mode-seg")) return;
  const seg = document.createElement("span");
  seg.className = "gp-mode-seg fs-lvl-seg";
  seg.id = "gp-mode-seg";
  seg.title = "Switch between rebased-to-100-at-window-start and the underlying index level";
  seg.innerHTML =
    `<button type="button" class="fs-lvl${GP_MODE === "rebase" ? " on" : ""}" data-mode="rebase">Rebase to 100</button>`
    + `<button type="button" class="fs-lvl${GP_MODE === "absolute" ? " on" : ""}" data-mode="absolute">Absolute level</button>`;
  tools.insertBefore(seg, tools.firstChild);
  seg.querySelectorAll(".fs-lvl").forEach((b) => b.addEventListener("click", () => {
    GP_MODE = b.dataset.mode;
    seg.querySelectorAll(".fs-lvl").forEach((x) => x.classList.toggle("on", x.dataset.mode === GP_MODE));
    if (BUNDLE) renderGP();
  }));
}

// COMP ---------------------------------------------------------------
function renderCOMP() {
  const cols = ["", "Since", "CAGR", "Total", "Vol", "Sharpe", "Sortino", "MaxDD", "Calmar", "Best 1Y", "Worst 1Y", "α vs primary"];
  const rows = BUNDLE.stats.filter((s) => shown("comp", s.name));
  let h = "<table class='stats'><thead><tr>" + cols.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
  rows.forEach((s) => {
    const sw = `<span class="swatch" style="background:${cbase(s.name)}"></span>`;
    h += `<tr class="${s.is_benchmark ? "bench" : ""}">
      <td class="name">${sw}${s.name}</td>
      <td title="series' own first data date; metrics are computed over the common comparison window">${s.inception || "—"}</td>
      <td class="${clsNum(s.cagr)}">${pct(s.cagr)}</td><td class="${clsNum(s.total_return)}">${pct(s.total_return)}</td>
      <td>${pct(s.vol)}</td><td class="${clsNum(s.sharpe)}">${num(s.sharpe)}</td><td class="${clsNum(s.sortino)}">${num(s.sortino)}</td>
      <td class="neg">${pct(s.maxdd)}</td><td>${num(s.calmar)}</td>
      <td class="pos">${pct(s.best_1y)}</td><td class="neg">${pct(s.worst_1y)}</td>
      <td class="${clsNum(s.alpha_vs_primary)}">${s.alpha_vs_primary === null ? "—" : pct(s.alpha_vs_primary)}</td></tr>`;
  });
  $("comp-table").innerHTML = h + "</tbody></table>"
    + `<div class="q-note" style="margin-top:6px;font-size:12px">Sharpe/Sortino/Calmar use a risk-free rate that <b>defaults to 0%</b>, so they are return-per-unit-of-risk, not the academic excess-over-rf version. Ratios are window-dependent and noisy over short windows — read over ≥3–5y.</div>`;
  // per-index bars: lighter shade = CAGR, darker = alpha vs primary
  const names = rows.map((s) => s.name);
  const cagr = rows.map((s) => s.cagr === null ? null : s.cagr * 100);
  const alpha = rows.map((s) => s.alpha_vs_primary === null ? null : s.alpha_vs_primary * 100);
  const traces = [
    { type: "bar", opacity: 0.74, name: "CAGR", x: names, y: cagr, marker: { color: names.map((n) => (COLORS[n] || {}).light || "#9bb") } },
    { type: "bar", opacity: 0.74, name: "α vs primary", x: names, y: alpha, marker: { color: names.map((n) => (COLORS[n] || {}).dark || "#446") } },
  ];
  const layout = baseLayout({
    barmode: "group", showlegend: false, hovermode: "closest",
    margin: { l: 56, r: 18, t: 28, b: 60 },
    yaxis: { title: "% p.a.", gridcolor: "#dfe3e8", zeroline: true, zerolinecolor: "#bcc3cc" },
    annotations: [{ xref: "paper", yref: "paper", x: 0, y: 1.13, showarrow: false,
      text: "Each index = its own colour · lighter bar = CAGR · darker bar = α vs primary", font: { size: 11, color: "#62707d" } }],
  });
  Plotly.react("plot-comp", traces, layout, PCONF);
}

// Rolling alpha / beta -----------------------------------------------
function renderAlpha() {
  const k = segVal("alpha-seg");
  const data = BUNDLE.rolling[k] || {};
  const x = BUNDLE.dates;
  const isAlpha = k === "alpha";
  const annual = BUNDLE.rolling.alpha_annualized;
  let traces = Object.keys(data).filter((key) => { const p = key.split("|"); return shownPair("alpha", p[0], p[1]); }).map((key) => {
    const t = key.split("|")[0];
    let y = data[key];
    if (isAlpha) y = y.map((v) => v === null ? null : v * 100);
    return { type: "scatter", mode: "lines", name: key, x, y, connectgaps: false, line: { color: cbase(t), width: 1.9, dash: isBench(t) ? "dot" : "solid" } };
  });
  if (!traces.length) { Plotly.purge("plot-alpha"); $("plot-alpha").innerHTML = "<div class='empty-note'>Select at least one index and one benchmark.</div>"; $("stat-alpha").textContent = ""; return; }
  const title = isAlpha ? (annual ? "Rolling α (% p.a.)" : "Rolling α (cumulative %)") : "Rolling β";
  Plotly.react("plot-alpha", traces, baseLayout({ yaxis: { title, gridcolor: "#dfe3e8", zeroline: true, zerolinecolor: "#bcc3cc" }, xaxis: { gridcolor: "#dfe3e8", type: "date" } }), PCONF);
  attachYAutoscale("plot-alpha");
  $("alpha-note").textContent = `${BUNDLE.meta.rolling_window} rolling window · ${isAlpha ? (annual ? "annualized" : "cumulative") : "beta"} · ${BUNDLE.meta.alpha_type === "jensen" ? "Jensen" : "excess"} α · seeded from window start`;
  $("stat-alpha").textContent = traces.map((tr) => {
    const v = tr.y.filter((z) => z !== null);
    const last = v.length ? v[v.length - 1] : null, mean = v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
    return `${tr.name}: latest ${isAlpha ? num(last) + "%" : num(last)}, mean ${isAlpha ? num(mean) + "%" : num(mean)}`;
  }).join("\n");
}

// Rolling risk -------------------------------------------------------
function renderRisk() {
  const k = segVal("risk-seg");
  const data = BUNDLE.rolling[k] || {};
  const x = BUNDLE.dates;
  const perPair = (k === "corr" || k === "relstrength");
  const keys = Object.keys(data).filter((key) => perPair ? shownPair("risk", key.split("|")[0], key.split("|")[1]) : shown("risk", key));
  let traces = keys.map((key) => {
    const base = perPair ? key.split("|")[0] : key;
    let y = data[key];
    if (k === "vol" || k === "drawdown") y = y.map((v) => v === null ? null : v * 100);
    const isDD = (k === "drawdown");
    const area = isDD && isBench(base);                 // benchmark drawdown = light area; selected indices = line
    const tr = { type: "scatter", mode: "lines", name: key, x, y, connectgaps: false,
      fill: area ? "tozeroy" : "none",
      line: { color: cbase(base), width: isBench(base) ? 1.7 : 2.0, dash: (isBench(base) && !isDD) ? "dot" : "solid" } };
    if (area) tr.fillcolor = fillColor(base);
    return tr;
  });
  if (!traces.length) { Plotly.purge("plot-risk"); $("plot-risk").innerHTML = "<div class='empty-note'>No series for this view (correlation & relative strength need a benchmark).</div>"; $("stat-risk").textContent = ""; return; }
  const titles = { drawdown: "Drawdown (%)", vol: "Volatility (% p.a.)", sharpe: "Rolling Sharpe", corr: "Rolling correlation", relstrength: "Relative strength (=100)" };
  const z = (k === "sharpe" || k === "corr");
  Plotly.react("plot-risk", traces, baseLayout({ yaxis: { title: titles[k], gridcolor: "#dfe3e8", zeroline: z, zerolinecolor: "#bcc3cc" }, xaxis: { gridcolor: "#dfe3e8", type: "date" } }), PCONF);
  attachYAutoscale("plot-risk");
  $("risk-note").textContent = (k === "drawdown") ? "full-window underwater" : `${BUNDLE.meta.rolling_window} rolling · seeded from window start`;
  $("stat-risk").textContent = traces.map((tr) => {
    const v = tr.y.filter((q) => q !== null); if (!v.length) return `${tr.name}: —`;
    const last = v[v.length - 1], ex = k === "drawdown" ? Math.min(...v) : (v.reduce((a, b) => a + b, 0) / v.length);
    const lbl = k === "drawdown" ? "worst" : "mean", u = (k === "vol" || k === "drawdown") ? "%" : "";
    return `${tr.name}: latest ${num(last)}${u}, ${lbl} ${num(ex)}${u}`;
  }).join("\n");
}

// Correlation matrix (all selected series) ---------------------------
const CORR_SCALE = [[0, "#b3402f"], [0.5, "#e8c84d"], [1, "#2e7d52"]];  // red low -> yellow mid -> green high
function renderCorrMatrix() {
  const cm = BUNDLE.corr_matrix;
  const labels = (cm && cm.labels ? cm.labels : []).filter((l) => shown("corrmat", l));
  if (!cm || labels.length < 2) {
    Plotly.purge("plot-corrmat");
    $("plot-corrmat").innerHTML = "<div class='empty-note'>Keep at least two series checked to see the correlation matrix.</div>";
    return;
  }
  const ix = labels.map((l) => cm.labels.indexOf(l));
  const z = ix.map((i) => ix.map((j) => cm.z[i][j]));
  const trace = {
    type: "heatmap", x: labels, y: labels, z, zmin: -1, zmax: 1, colorscale: CORR_SCALE,
    colorbar: { title: "ρ", thickness: 12 },
    text: z.map((row) => row.map((v) => v === null ? "" : v.toFixed(2))), texttemplate: "%{text}", textfont: { size: 10 },
    hovertemplate: "%{y} ↔ %{x}: %{z:.3f}<extra></extra>",
  };
  Plotly.react("plot-corrmat", [trace], baseLayout({
    hovermode: "closest",
    xaxis: { type: "category", gridcolor: "#F4F5F7", tickangle: -30, automargin: true },
    yaxis: { type: "category", autorange: "reversed", gridcolor: "#F4F5F7", automargin: true },
    margin: { l: 40, r: 18, t: 14, b: 40 },
  }), PCONF);
  $("corr-freq").textContent = BUNDLE.meta.freq;
}

// Capture ------------------------------------------------------------
function renderCapture() {
  const p = (BUNDLE.pairs || []).filter((r) => shownPair("capture", r.name, r.benchmark));
  if (!p.length) { $("capture-table").innerHTML = "<div class='empty-note'>Add a benchmark (and keep an index checked) to compute capture.</div>"; Plotly.purge("plot-capture"); return; }
  let h = "<table class='stats'><thead><tr><th></th><th>Benchmark</th><th>Up capture</th><th>Down capture</th><th>Ratio</th><th>β</th><th>Tracking error</th><th>Info ratio</th></tr></thead><tbody>";
  p.forEach((r) => {
    const sw = `<span class="swatch" style="background:${cbase(r.name)}"></span>`;
    h += `<tr><td class="name">${sw}${r.name}</td><td>${r.benchmark}</td><td>${pct(r.up_capture)}</td><td>${pct(r.down_capture)}</td>
      <td class="${(r.capture_ratio || 0) >= 1 ? "pos" : "neg"}">${num(r.capture_ratio)}</td><td>${num(r.beta)}</td><td>${pct(r.tracking_error)}</td><td class="${clsNum(r.info_ratio)}">${num(r.info_ratio)}</td></tr>`;
  });
  $("capture-table").innerHTML = h + "</tbody></table>";
  const traces = p.map((r) => ({
    type: "scatter", mode: "markers+text", name: `${r.name} vs ${r.benchmark}`,
    x: [r.down_capture === null ? null : r.down_capture * 100], y: [r.up_capture === null ? null : r.up_capture * 100],
    text: [r.name], textposition: "top center", textfont: { size: 10 }, marker: { size: 12, color: cbase(r.name) },
  }));
  traces.push({ type: "scatter", mode: "lines", name: "up = down", x: [0, 150], y: [0, 150], line: { color: "#9aa3ad", dash: "dash", width: 1 }, hoverinfo: "skip", showlegend: false });
  Plotly.react("plot-capture", traces, baseLayout({ showlegend: false, xaxis: { title: "Down capture (%)", gridcolor: "#dfe3e8" }, yaxis: { title: "Up capture (%)", gridcolor: "#dfe3e8" }, margin: { l: 56, r: 18, t: 14, b: 44 } }), PCONF);
}

// Calendar year (returns + alpha, both shown) ------------------------
function renderCY() {
  const cy = BUNDLE.calendar_year;
  if (!cy || !cy.years.length) { Plotly.purge("plot-cy"); Plotly.purge("plot-cy-alpha"); return; }
  const cols = Object.keys(cy.series).filter((c) => shown("cy", c));
  const rt = cols.map((c) => ({ type: "bar", opacity: 0.74, name: c, x: cy.years, y: cy.series[c].map((v) => v === null ? null : v * 100), marker: { color: cbase(c) } }));
  Plotly.react("plot-cy", rt, baseLayout({ barmode: "group", hovermode: "closest", xaxis: { type: "category", gridcolor: "#dfe3e8" }, yaxis: { title: "Return (%)", gridcolor: "#dfe3e8", zeroline: true, zerolinecolor: "#bcc3cc" } }), PCONF);
  $("stat-cy").textContent = cols.map((c) => { const s = cy.stats_return[c]; return `${c}: ${s.pos === null ? "—" : (s.pos * 100).toFixed(0) + "%"} years +ve / ${s.neg === null ? "—" : (s.neg * 100).toFixed(0) + "%"} −ve  (${s.n} yrs)`; }).join("\n");
  // alpha
  const atick = cy.primary_benchmark ? BUNDLE.meta.tickers.filter((t) => shownPair("cy", t, cy.primary_benchmark)) : [];
  if (atick.length) {
    const at = atick.map((t) => { const key = `${t}|${cy.primary_benchmark}`; return { type: "bar", opacity: 0.74, name: t, x: cy.years, y: (cy.alpha[key] || []).map((v) => v === null ? null : v * 100), marker: { color: cbase(t) } }; });
    Plotly.react("plot-cy-alpha", at, baseLayout({ barmode: "group", hovermode: "closest", xaxis: { type: "category", gridcolor: "#dfe3e8" }, yaxis: { title: `Alpha vs ${cy.primary_benchmark} (%)`, gridcolor: "#dfe3e8", zeroline: true, zerolinecolor: "#bcc3cc" } }), PCONF);
    $("stat-cy-alpha").textContent = atick.map((t) => { const s = cy.stats_alpha[`${t}|${cy.primary_benchmark}`]; return s ? `${t}: ${(s.pos * 100).toFixed(0)}% years α+ / ${(s.neg * 100).toFixed(0)}% α−  (${s.n} yrs)` : `${t}: —`; }).join("\n");
  } else { Plotly.purge("plot-cy-alpha"); $("stat-cy-alpha").textContent = "Add a benchmark (and keep an index checked) to see calendar-year alpha."; }
}

// Monthly heatmap ----------------------------------------------------
const HEAT_SCALE = [[0, "#b3402f"], [0.5, "#F4F5F7"], [1, "#2e7d52"]];  // red low -> white -> green high
function renderMonthPicker() {
  const names = Object.keys(BUNDLE.monthly || {});
  const cur = $("month-pick").value;
  $("month-pick").innerHTML = names.map((n) => `<option ${n === cur ? "selected" : ""}>${n}</option>`).join("");
}
function renderMonth() {
  const name = $("month-pick").value || Object.keys(BUNDLE.monthly || {})[0];
  const m = BUNDLE.monthly[name];
  if (!m) { Plotly.purge("plot-month"); return; }
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const z = m.z.map((row) => row.map((v) => v === null ? null : v * 100));
  const trace = { type: "heatmap", x: months, y: m.years.map(String), z, zmid: 0, colorscale: HEAT_SCALE, colorbar: { title: "%", thickness: 12 }, hovertemplate: "%{y} %{x}: %{z:.2f}%<extra></extra>" };
  Plotly.react("plot-month", [trace], baseLayout({ hovermode: "closest", yaxis: { type: "category", autorange: "reversed", gridcolor: "#F4F5F7" }, xaxis: { type: "category", gridcolor: "#F4F5F7" }, margin: { l: 56, r: 18, t: 14, b: 36 } }), PCONF);
}

// Distributions (density curves) -------------------------------------
function densityGrid(containerId, dataByHorizon, horizons, units, kind) {
  const cont = $(containerId);
  cont.innerHTML = "";
  if (!horizons || !horizons.length) {
    cont.innerHTML = `<div class='empty-note'>Selected window is too short for any horizon${kind === "alpha" ? " (or no benchmark selected)" : ""}.</div>`;
    return;
  }
  horizons.forEach((label) => {
    const id = (kind === "alpha" ? "dalp_" : "dret_") + label.replace(/[^a-z0-9]/gi, "");
    const unit = (units[label] === "cagr") ? "% p.a." : "%";
    const div = document.createElement("div");
    div.className = "dchart";
    div.innerHTML = `<div class="dtitle">${label} ${kind === "alpha" ? "alpha" : "return"} · density</div><div id="${id}" style="height:240px"></div>`;
    cont.appendChild(div);
    const series = dataByHorizon[label];
    const traces = Object.keys(series).filter((nm) => shown("dist", nm)).map((nm) => {
      const s = series[nm];
      return {
        // density curves are LINES only (no fill) — a filled curve, esp. the black
        // benchmark, paints over the others
        type: "scatter", mode: "lines", name: `${nm} (μ ${(s.mean * 100).toFixed(1)}%, σ ${(s.std * 100).toFixed(1)}%)`,
        x: s.x.map((v) => v * 100), y: s.y, line: { color: cbase(nm), width: 1.9, dash: isBench(nm) ? "dot" : "solid" },
      };
    });
    Plotly.newPlot(id, traces, baseLayout({
      hovermode: "closest", showlegend: true, legend: { orientation: "h", y: -0.32, font: { size: 9 } },
      margin: { l: 44, r: 10, t: 6, b: 34 },
      xaxis: { title: unit, gridcolor: "#dfe3e8", zeroline: true, zerolinecolor: "#bcc3cc" },
      yaxis: { title: "density", gridcolor: "#dfe3e8", showticklabels: false },
    }), PCONF);
  });
}
function renderDist() {
  const d = BUNDLE.distribution || {};
  densityGrid("distret", d.return || {}, d.horizons_return || [], d.units || {}, "return");
  densityGrid("distalpha", d.alpha || {}, d.horizons_alpha || [], d.units || {}, "alpha");
}

// chart key -> render fn, so a per-chart "show" toggle re-renders only that chart
const RENDER = { gp: renderGP, comp: renderCOMP, alpha: renderAlpha, risk: renderRisk, corrmat: renderCorrMatrix, capture: renderCapture, cy: renderCY, dist: renderDist,
  vallevel: () => renderValLevel(), valxsec: () => renderValXsec(), valspread: () => renderValSpread(), valdist: () => renderValDist() };

// --------------------------------------------------------------------- CSV export
function download(fname, rows) {
  const csv = rows.map((r) => r.map((c) => { const s = (c === null || c === undefined) ? "" : String(c); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; }).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = fname; a.click();
}
function exportCSV(which) {
  if (which === "vallevel" || which === "valxsec" || which === "valspread") {
    if (!VAL_BUNDLE) return;
    if (which === "vallevel") {
      const cols = [...VAL_BUNDLE.meta.tickers, ...VAL_BUNDLE.meta.benchmarks];
      const rows = [["Date", ...cols]]; VAL_BUNDLE.dates.forEach((dt, i) => rows.push([dt, ...cols.map((c) => VAL_BUNDLE.series[c][i])]));
      download(`vistas_${VAL_MEASURE}_level.csv`, rows);
    } else if (which === "valxsec") {
      const rows = [["Index", "Current", "Percentile", "Zscore", "CheapRich", "AsOf"]];
      (VAL_BUNDLE.cross_section.rows || []).forEach((r) => rows.push([r.name, r.value, r.percentile, r.zscore, r.cheap_rich, r.date]));
      download(`vistas_${VAL_MEASURE}_crosssection.csv`, rows);
    } else if (VAL_BUNDLE.spread) {
      const tk = Object.keys(VAL_BUNDLE.spread.series);
      const rows = [["Date", ...tk.map((t) => `${t}_minus_${VAL_BUNDLE.spread.primary}`)]];
      VAL_BUNDLE.dates.forEach((dt, i) => rows.push([dt, ...tk.map((t) => VAL_BUNDLE.spread.series[t][i])]));
      download(`vistas_${VAL_MEASURE}_spread.csv`, rows);
    }
    return;
  }
  if (which === "fund") {
    const b = FUND_DATA && FUND_SYM && FUND_DATA[FUND_SYM];
    const pl = (b && b.statements && b.statements.profit_loss) || [];
    if (!pl.length) return;
    const cols = Object.keys(pl[0]);
    const rows = [cols.map((c) => (c === "Unnamed: 0" ? "Item" : c))];
    pl.forEach((r) => rows.push(cols.map((c) => r[c])));
    download(`vistas_${FUND_SYM}_pnl.csv`, rows);
    return;
  }
  if (!BUNDLE) return;
  const x = BUNDLE.dates;
  if (which === "gp") {
    const cols = [...BUNDLE.meta.tickers, ...BUNDLE.meta.benchmarks];
    const rows = [["Date", ...cols]]; x.forEach((dt, i) => rows.push([dt, ...cols.map((c) => BUNDLE.levels[c][i])]));
    download("vistas_nav.csv", rows);
  } else if (which === "comp") {
    const rows = [["Index", "CAGR", "Total", "Vol", "Sharpe", "Sortino", "MaxDD", "Calmar", "Best1Y", "Worst1Y", "AlphaVsPrimary"]];
    BUNDLE.stats.forEach((s) => rows.push([s.name, s.cagr, s.total_return, s.vol, s.sharpe, s.sortino, s.maxdd, s.calmar, s.best_1y, s.worst_1y, s.alpha_vs_primary]));
    download("vistas_comp.csv", rows);
  } else if (which === "alpha" || which === "risk") {
    const k = which === "alpha" ? segVal("alpha-seg") : segVal("risk-seg");
    const data = BUNDLE.rolling[k] || {}; const keys = Object.keys(data);
    const rows = [["Date", ...keys]]; x.forEach((dt, i) => rows.push([dt, ...keys.map((kk) => data[kk][i])]));
    download(`vistas_${which}_${k}.csv`, rows);
  } else if (which === "capture") {
    const rows = [["Index", "Benchmark", "UpCapture", "DownCapture", "Ratio", "Beta", "TrackingError", "InfoRatio"]];
    (BUNDLE.pairs || []).forEach((r) => rows.push([r.name, r.benchmark, r.up_capture, r.down_capture, r.capture_ratio, r.beta, r.tracking_error, r.info_ratio]));
    download("vistas_capture.csv", rows);
  } else if (which === "cy") {
    const cy = BUNDLE.calendar_year; const cols = Object.keys(cy.series);
    const akeys = Object.keys(cy.alpha || {});
    const rows = [["Year", ...cols.map((c) => c + " ret"), ...akeys.map((k) => k + " alpha")]];
    cy.years.forEach((y, i) => rows.push([y, ...cols.map((c) => cy.series[c][i]), ...akeys.map((k) => cy.alpha[k][i])]));
    download("vistas_calendar_year.csv", rows);
  } else if (which === "corrmat") {
    const cm = BUNDLE.corr_matrix; const rows = [["", ...cm.labels]];
    cm.labels.forEach((l, i) => rows.push([l, ...cm.z[i]]));
    download("vistas_corr_matrix.csv", rows);
  } else if (which === "month") {
    const name = $("month-pick").value; const m = BUNDLE.monthly[name];
    const rows = [["Year", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]];
    m.years.forEach((y, i) => rows.push([y, ...m.z[i]]));
    download(`vistas_monthly_${name}.csv`, rows);
  }
}

// =================================================================== Valuation tab
function segMeasure(id, fn) {
  const el = $(id); if (!el) return;
  el.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    if (b.disabled) return;
    el.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); fn(b.dataset.m);
  }));
}

function syncMeasureAvailability() {
  ["TR", "PR"].forEach((m) => { const b = $("measure-seg") && $("measure-seg").querySelector(`[data-m="${m}"]`); if (b) { b.disabled = !hasMeasure(m); if (b.disabled) b.title = `No ${m} data in this deck`; } });
  const valM = ["PE", "PB", "DY"].filter(hasMeasure);
  ["PE", "PB", "DY"].forEach((m) => { const b = $("val-seg") && $("val-seg").querySelector(`[data-m="${m}"]`); if (b) { b.disabled = !hasMeasure(m); if (b.disabled) b.title = `No ${m} data in this deck`; } });
  if (valM.length) {
    if (!valM.includes(VAL_MEASURE)) VAL_MEASURE = valM[0];
    const seg = $("val-seg"); if (seg) seg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.m === VAL_MEASURE));
  }
  const vtab = $("tabs") && $("tabs").querySelector('[data-view="valuation"]');
  if (vtab) { const any = valM.length > 0; vtab.disabled = !any; vtab.style.opacity = any ? "" : ".45"; vtab.title = any ? "" : "No valuation data in this deck yet"; }
  if (PERF_MEASURE === "PR" && !hasMeasure("PR")) { PERF_MEASURE = "TR"; const seg = $("measure-seg"); if (seg) seg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.m === "TR")); }
}

// Plotly only re-measures on WINDOW resize, never on a container being shown/hidden — so a
// chart drawn while its tab was hidden keeps a 0/near-0 width and looks empty or "shrunk to the
// left" until a page resize. After a view becomes visible (and after async renders finish) we
// force every plot in it to re-measure to its real width. afterPaint waits for layout to flush.
function afterPaint(fn) {
  if (typeof requestAnimationFrame !== "undefined") requestAnimationFrame(() => requestAnimationFrame(fn));
  else if (typeof setTimeout !== "undefined") setTimeout(fn, 30);
}
function viewPlotsResize(v) {
  if (typeof document === "undefined" || typeof window === "undefined" || !window.Plotly
      || !Plotly.Plots || !Plotly.Plots.resize) return;
  const root = document.getElementById("view-" + (v || VIEW));
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll(".js-plotly-plot").forEach((gd) => { try { Plotly.Plots.resize(gd); } catch (e) {} });
}

function hasFundFor(sym) {                 // does this symbol have fundamentals (a company)?
  const m = fundManifest();
  return !!(sym && ((m && m[sym]) || (FUND_DATA && FUND_DATA[sym])));
}
function perfCompanies() {                 // Performance-selected names that are companies w/ fundamentals
  return (typeof MS_T !== "undefined" && MS_T ? MS_T.value : []).filter(hasFundFor);
}
function setView(v) {
  const prev = VIEW;
  if (!linked()) TAB_WIN[prev] = [$("start").value, $("end").value];   // remember the leaving tab's window
  VIEW = v;
  $("view-performance").hidden = (v !== "performance");
  $("view-valuation").hidden = (v !== "valuation");
  if ($("view-fundamentals")) $("view-fundamentals").hidden = (v !== "fundamentals");
  if ($("view-quant")) $("view-quant").hidden = (v !== "quant");
  if ($("view-screen")) $("view-screen").hidden = (v !== "screen");
  if ($("view-funds")) $("view-funds").hidden = (v !== "funds");
  if ($("view-fundskill")) $("view-fundskill").hidden = (v !== "fundskill");
  if ($("view-macro")) $("view-macro").hidden = (v !== "macro");
  if ($("view-allocator")) $("view-allocator").hidden = (v !== "allocator");
  if ($("view-ownership")) $("view-ownership").hidden = (v !== "ownership");
  // the Universe/Benchmark/range control bar only applies to Performance + Valuation; hide it on
  // the self-contained Fundamentals + Quant + Macro + Allocator + Ownership tabs (own pickers / fixed windows).
  const ctl = $("ctl"); if (ctl) ctl.style.display = (v === "fundamentals" || v === "quant" || v === "screen" || v === "funds" || v === "fundskill" || v === "macro" || v === "allocator" || v === "ownership") ? "none" : "";
  $("tabs").querySelectorAll(".tab[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  if (!linked() && TAB_WIN[v]) { $("start").value = TAB_WIN[v][0]; $("end").value = TAB_WIN[v][1]; }   // restore this tab's own window
  syncDateStrips();
  // Carry the selection across tabs so switching feels continuous (KV: "my selections should load
  // by default" — TCS+INFY in Performance should be what Fundamentals shows, not a reset to 20MICRONS).
  let handled = false;
  if (v === "fundamentals" && prev !== "fundamentals") {
    const co = perfCompanies();
    if (co.length) {                                  // seed Fundamentals from the Performance companies
      if (!FUND_SYM || !co.includes(FUND_SYM)) FUND_SYM = co[0];
      if (!FUND_SYM2 || !co.includes(FUND_SYM2) || FUND_SYM2 === FUND_SYM) FUND_SYM2 = co.find((c) => c !== FUND_SYM) || null;
      if (FUND_COMBO) FUND_COMBO.setValue(FUND_SYM);
      if (FUND_COMBO2) FUND_COMBO2.setValue(FUND_SYM2);
    }
  } else if ((v === "performance" || v === "valuation") && prev === "fundamentals" && typeof MS_T !== "undefined" && MS_T) {
    let changed = false;                              // carry the viewed company back into the universe
    [FUND_SYM, FUND_SYM2].forEach((s) => { if (s && !MS_T.value.includes(s)) { MS_T.add(s); changed = true; } });
    if (changed) { run(); handled = true; }           // run() recomputes Performance + syncs Valuation
  }
  // entering Quant: carry over the company in view (from Fundamentals or the Performance picks)
  if (v === "quant") {
    const co = perfCompanies();
    if (FUND_SYM && (prev === "fundamentals")) QUANT_SYM = FUND_SYM;
    if (!QUANT_SYM) QUANT_SYM = FUND_SYM || (co.length ? co[0] : QUANT_SYM);
    if (QUANT_COMBO && QUANT_SYM) QUANT_COMBO.setValue(QUANT_SYM);
  }
  // Plotly draws at 0-width inside a hidden div, so (re)render the now-visible view
  if (!handled) {
    if (v === "valuation") { if (VAL_BUNDLE) renderValuation(); else runValuation(true); }
    else if (v === "fundamentals") renderFundamentals();
    else if (v === "quant") renderQuant();
    else if (v === "screen") renderScreen();
    else if (v === "funds") renderFunds();
    else if (v === "fundskill") renderFundSkill();
    else if (v === "macro") renderMacro();
    else if (v === "allocator") renderAllocator();
    else if (v === "ownership") renderOwnership();
    else if (BUNDLE) renderAll();
  }
  afterPaint(() => viewPlotsResize(v));     // re-measure now-visible plots once layout settles
  writeHash();
}

function initMacro() {
  const tab = $("tabs") && $("tabs").querySelector('[data-view="macro"]');
  // enabled if we have India macro inline, world inline, OR a lazy world catalog to fetch
  const has = !!(macroFrameI() || worldFrameM() || (LAZY && LAZY.world && LAZY.world.length));
  if (tab) { tab.disabled = !has; tab.style.opacity = has ? "" : ".45"; tab.title = has ? "" : "No world/cross-asset data in this deck yet"; }
  // keep the Macro view in sync with the global window
  ["start", "end"].forEach((id) => { const el = $(id); if (el) el.addEventListener("change", () => { if (VIEW === "macro") renderMacro(); }); });
  const pr = $("presets"); if (pr) pr.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { if (VIEW === "macro") setTimeout(renderMacro, 0); }));
}

function valuationEmpty(msg) {
  ["plot-vallevel", "plot-valxsec", "plot-valspread", "plot-valdist"].forEach((id) => { if ($(id)) { Plotly.purge(id); $(id).innerHTML = `<div class='empty-note'>${msg}</div>`; } });
  ["val-gauge", "valxsec-table"].forEach((id) => { if ($(id)) $(id).innerHTML = ""; });
  ["stat-vallevel", "stat-valspread", "stat-valdist"].forEach((id) => { if ($(id)) $(id).textContent = ""; });
}

function valFmt(v) { if (v === null || v === undefined || Number.isNaN(v)) return "—"; return VAL_MEASURE === "DY" ? Number(v).toFixed(2) + "%" : Number(v).toFixed(2) + "×"; }
function valUnit() { return VAL_MEASURE === "DY" ? "Dividend yield (%)" : (VAL_MEASURE === "PB" ? "P/B (×)" : "P/E (×)"); }

function renderValuation() {
  if (!VAL_BUNDLE) return;
  const cols = [...(VAL_BUNDLE.meta.tickers || []), ...(VAL_BUNDLE.meta.benchmarks || [])];
  ["vallevel", "valxsec", "valspread", "valdist"].forEach((key) => buildToggle(key, cols));
  renderValLevel(); renderValGauge(); renderValXsec(); renderValSpread(); renderValDist();
  afterPaint(() => viewPlotsResize("valuation"));
}

function renderValLevel() {
  const x = VAL_BUNDLE.dates;
  const cols = [...VAL_BUNDLE.meta.tickers, ...VAL_BUNDLE.meta.benchmarks].filter((c) => shown("vallevel", c));
  const traces = cols.map((c) => lineTrace(c, x, VAL_BUNDLE.series[c]));
  if (cols.length === 1 && VAL_BUNDLE.bands[cols[0]]) {        // single series -> mean/σ band lines
    const b = VAL_BUNDLE.bands[cols[0]], n = x.length, flat = (v) => new Array(n).fill(v);
    const band = (y, lbl, w, dash) => ({ type: "scatter", mode: "lines", name: lbl, x, y: flat(y), line: { color: "#9aa6b2", width: w, dash }, hoverinfo: "skip" });
    if (b.mean !== null) traces.push(band(b.mean, "mean", 1.3, "dash"));
    [["sd1_hi", "+1σ"], ["sd1_lo", "−1σ"], ["sd2_hi", "+2σ"], ["sd2_lo", "−2σ"]].forEach(([k, l]) => { if (b[k] !== null) traces.push(band(b[k], l, 0.8, "dot")); });
  }
  Plotly.react("plot-vallevel", traces, baseLayout({ yaxis: { title: valUnit(), gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8", rangeslider: { thickness: 0.07 }, type: "date" } }), PCONF);
  attachYAutoscale("plot-vallevel");
  $("stat-vallevel").textContent = `Window ${VAL_BUNDLE.meta.start} → ${VAL_BUNDLE.meta.end} · ${VAL_BUNDLE.meta.n_obs} obs\n` +
    VAL_BUNDLE.stats.filter((s) => cols.includes(s.name)).map((s) => `${s.name}: now ${valFmt(s.current)} (mean ${valFmt(s.mean)}, ${num(s.percentile, 0)}%ile, z ${num(s.zscore)}, ${s.cheap_rich})`).join("  |  ");
}

function renderValGauge() {
  const rows = VAL_BUNDLE.stats.filter((s) => s.current !== null);
  if (!rows.length) { $("val-gauge").innerHTML = "<div class='empty-note'>No data.</div>"; return; }
  const yld = VAL_MEASURE === "DY";
  let h = `<table class="gauge-tbl"><thead><tr><th>Index</th><th>Current</th><th>Mean</th><th>Range (min–max)</th><th>Percentile in own history</th><th>z</th><th></th></tr></thead><tbody>`;
  rows.forEach((s) => {
    const sw = `<span class="swatch" style="background:${cbase(s.name)}"></span>`;
    const pin = (s.percentile === null) ? "" : `<div class="gauge-bar${yld ? " yld" : ""}"><div class="gauge-pin" style="left:${Math.max(0, Math.min(100, s.percentile))}%"></div></div>`;
    h += `<tr><td class="name">${sw}${s.name}</td><td>${valFmt(s.current)}</td><td>${valFmt(s.mean)}</td>
      <td>${valFmt(s.min)} – ${valFmt(s.max)}</td>
      <td style="min-width:200px">${pin}<div style="font-size:10.5px;color:#62707d;margin-top:2px">${num(s.percentile, 0)}ᵗʰ percentile</div></td>
      <td>${num(s.zscore)}</td><td><span class="tag-cr ${s.cheap_rich}">${s.cheap_rich}</span></td></tr>`;
  });
  $("val-gauge").innerHTML = h + "</tbody></table>";
}

function renderValXsec() {
  const rows = (VAL_BUNDLE.cross_section.rows || []).filter((r) => shown("valxsec", r.name) && r.value !== null);
  if (!rows.length) { Plotly.purge("plot-valxsec"); $("valxsec-table").innerHTML = "<div class='empty-note'>No data.</div>"; return; }
  const names = rows.map((r) => r.name), vals = rows.map((r) => r.value);
  const trace = { type: "bar", opacity: 0.74, x: names, y: vals, marker: { color: names.map(cbase) }, text: vals.map(valFmt), textposition: "outside", hovertemplate: "%{x}: %{y}<extra></extra>" };
  Plotly.react("plot-valxsec", [trace], baseLayout({ showlegend: false, hovermode: "closest", margin: { l: 56, r: 18, t: 18, b: 86 }, xaxis: { type: "category", tickangle: -25, automargin: true }, yaxis: { title: valUnit(), gridcolor: "#dfe3e8" } }), PCONF);
  let h = `<table class="stats"><thead><tr><th></th><th>Current</th><th>Percentile</th><th>z</th><th>Rich/Cheap</th><th>As of</th></tr></thead><tbody>`;
  rows.forEach((r) => { h += `<tr><td class="name"><span class="swatch" style="background:${cbase(r.name)}"></span>${r.name}</td><td>${valFmt(r.value)}</td><td>${num(r.percentile, 0)}%</td><td>${num(r.zscore)}</td><td><span class="tag-cr ${r.cheap_rich}">${r.cheap_rich}</span></td><td>${r.date || "—"}</td></tr>`; });
  $("valxsec-table").innerHTML = h + "</tbody></table>";
}

function renderValSpread() {
  const sp = VAL_BUNDLE.spread;
  if (!sp) { Plotly.purge("plot-valspread"); $("plot-valspread").innerHTML = "<div class='empty-note'>Add a benchmark (its valuation is the baseline) to see spreads.</div>"; $("stat-valspread").textContent = ""; return; }
  const x = VAL_BUNDLE.dates;
  const tickers = Object.keys(sp.series).filter((t) => shown("valspread", t));
  if (!tickers.length) { Plotly.purge("plot-valspread"); $("plot-valspread").innerHTML = "<div class='empty-note'>No index series selected.</div>"; $("stat-valspread").textContent = ""; return; }
  const traces = tickers.map((t) => lineTrace(t, x, sp.series[t]));
  traces.push({ type: "scatter", mode: "lines", name: "0", x, y: new Array(x.length).fill(0), line: { color: "#bcc3cc", width: 1, dash: "dash" }, hoverinfo: "skip", showlegend: false });
  Plotly.react("plot-valspread", traces, baseLayout({ yaxis: { title: `${VAL_MEASURE} spread vs ${sp.primary}`, gridcolor: "#dfe3e8", zeroline: true, zerolinecolor: "#bcc3cc" }, xaxis: { gridcolor: "#dfe3e8", type: "date" } }), PCONF);
  $("stat-valspread").textContent = tickers.map((t) => { const st = sp.stats[t]; return st ? `${t} − ${sp.primary}: latest ${valFmt(st.current)}, mean ${valFmt(st.mean)}, ${num(st.percentile, 0)}%ile` : `${t}: —`; }).join("\n");
}

function renderValDist() {
  const dist = VAL_BUNDLE.distribution || {};
  const cols = [...VAL_BUNDLE.meta.tickers, ...VAL_BUNDLE.meta.benchmarks].filter((c) => shown("valdist", c) && dist[c]);
  if (!cols.length) { Plotly.purge("plot-valdist"); $("plot-valdist").innerHTML = "<div class='empty-note'>Not enough data for a distribution.</div>"; $("stat-valdist").textContent = ""; return; }
  const traces = cols.map((c) => { const d = dist[c], s = VAL_BUNDLE.stats.find((q) => q.name === c) || {}; return { type: "scatter", mode: "lines", name: `${c} (now ${valFmt(s.current)})`, x: d.x, y: d.y, line: { color: cbase(c), width: 1.9, dash: isBench(c) ? "dot" : "solid" } }; });
  const shapes = cols.map((c) => { const s = VAL_BUNDLE.stats.find((q) => q.name === c); if (!s || s.current === null) return null; return { type: "line", x0: s.current, x1: s.current, yref: "paper", y0: 0, y1: 1, line: { color: cbase(c), width: 1, dash: "dot" } }; }).filter(Boolean);
  Plotly.react("plot-valdist", traces, baseLayout({ hovermode: "closest", shapes, xaxis: { title: valUnit(), gridcolor: "#dfe3e8" }, yaxis: { title: "density", gridcolor: "#dfe3e8", showticklabels: false } }), PCONF);
  $("stat-valdist").textContent = "Vertical dotted line = current value." + (VAL_MEASURE === "DY" ? " Right tail = high yield (cheap)." : " Right tail = expensive.");
}

// =================================================================== Fundamentals tab (Screener)
let FUND_SYM2 = null;            // optional compare company
let FUND_COMBO = null, FUND_COMBO2 = null;

// single-select searchable picker (ticker / company name / acronym) — used for both pickers
class ComboBox {
  constructor(el, opts) {
    this.el = el; this.onPick = opts.onPick || (() => {}); this.allowNone = !!opts.allowNone;
    this.hideSym = !!opts.hideSym;   // for fund pickers: sym is a meaningless internal code — show the (optional) sub instead
    this.items = []; this.value = null;
    el.classList.add("combo");
    el.innerHTML = `<input class="cbin" placeholder="${opts.placeholder || "search…"}" autocomplete="off"><div class="cbpop"><div class="cblist"></div></div>`;
    this.input = el.querySelector(".cbin"); this.list = el.querySelector(".cblist");
    this.input.addEventListener("focus", () => { this.input.select(); this.openIt(); });
    this.input.addEventListener("input", () => this.openIt());
    this.input.addEventListener("keydown", (e) => { if (e.key === "Escape") { this.closeIt(); this.input.blur(); } });
    document.addEventListener("click", (e) => { if (!el.contains(e.target)) this.closeIt(); });
  }
  setItems(items) { this.items = items; }
  setValue(v) { this.value = v; const it = this.items.find((x) => x.sym === v); this.input.value = it ? it.disp : (v || ""); }
  openIt() { this.el.classList.add("open"); this.renderList(); }
  closeIt() { this.el.classList.remove("open"); }
  // fuzzy descriptor: acronym from the display name; the internal sym + sub-label
  // (AMC / category) widen matching so "absl flexi" or a category word both hit.
  _prep(it) { if (it._fz === undefined) it._fz = fuzzyPrep(it.name || it.sym, (it.sym || "") + " " + (it.sub || "")); return it._fz; }
  _score(it, q) { return q ? fuzzyScore(q, this._prep(it)) : 1; }
  _match(it, q) { return this._score(it, q) > 0; }
  renderList() {
    const q = this.input.value.trim().toLowerCase();
    let html = this.allowNone ? `<div class="cbopt" data-s="__none__"><span class="cbname">— none —</span></div>` : "";
    let arr = this.items.map((it) => [it, this._score(it, q)]).filter((x) => x[1] > 0);
    if (q) arr.sort((a, b) => b[1] - a[1]);   // best match first
    html += arr.slice(0, 100).map(([it]) => {
      const lead = this.hideSym ? (it.sub ? `<span class="cbsub">${fEsc(it.sub)}</span>` : "") : `<span class="cbsym">${it.sym}</span>`;
      return `<div class="cbopt${it.sym === this.value ? " sel" : ""}" data-s="${it.sym}">${lead}<span class="cbname">${fEsc(it.name || "")}</span></div>`;
    }).join("");
    this.list.innerHTML = html || `<div class="empty-note">no match</div>`;
    this.list.querySelectorAll(".cbopt").forEach((o) => o.addEventListener("mousedown", (e) => { e.preventDefault(); this.pick(o.dataset.s); }));
  }
  pick(sym) {
    if (sym === "__none__") { this.value = null; this.input.value = ""; }
    else { this.value = sym; const it = this.items.find((x) => x.sym === sym); this.input.value = it ? it.disp : sym; }
    this.closeIt(); this.onPick(this.value);
  }
}

function fundSelectedSyms() {
  const out = [];
  if (FUND_SYM && FUND_DATA && FUND_DATA[FUND_SYM]) out.push(FUND_SYM);
  if (FUND_SYM2 && FUND_SYM2 !== FUND_SYM && FUND_DATA && FUND_DATA[FUND_SYM2]) out.push(FUND_SYM2);
  return out;
}

// ============================================================ QUANT & MI (per-stock cockpit)
// Renders the per-symbol block computed by vistas/stock_intel.py (data/quant/<SYM>.json):
// snapshot · market behaviour · business · valuation · ownership · data-quality. Card-heavy with
// three new charts (trailing returns, relative strength, shareholding trend). Diagnostics only.
function qChip(verdict) {
  const map = { positive: ["pos", "Positive"], negative: ["neg", "Negative"], neutral: ["neu", "Neutral"], insufficient: ["na", "Not enough data"] };
  const m = map[verdict] || map.insufficient;
  return `<span class="q-chip ${m[0]}">${m[1]}</span>`;
}
function qStat(label, val) { return `<div class="q-stat"><span class="q-stat-l">${fEsc(label)}</span><span class="q-stat-v">${val}</span></div>`; }
function qList(title, arr, cls) {
  if (!arr || !arr.length) return "";
  return `<div class="q-list ${cls || ""}"><div class="q-list-t">${fEsc(title)}</div><ul>${arr.map((x) => `<li>${fEsc(x)}</li>`).join("")}</ul></div>`;
}
function qFlag(f) {
  const st = f.status || "na";
  const v = (f.value === null || f.value === undefined || f.value === "") ? "" : (f.value + (f.unit || ""));
  return `<div class="q-flag ${st}"><div class="q-flag-h"><span class="q-flag-l">${fEsc(f.label)}</span><span class="q-flag-v">${fEsc(v)}</span></div>`
    + `<div class="q-flag-r">${fEsc(f.read || (st === "na" ? "not applicable for this company type" : ""))}</div>`
    + `<details><summary>Definition · Why</summary><p><b>What:</b> ${fEsc(f.meaning || "")}</p><p><b>Why:</b> ${fEsc(f.why_useful || "")}</p>${f.why_it_can_fail ? `<p><b>Can fail:</b> ${fEsc(f.why_it_can_fail)}</p>` : ""}</details></div>`;
}
async function renderQuant() {
  const host = $("quant-body"); if (!host) return;
  if (!QUANT_SYM) { host.innerHTML = `<div class='empty-note'>Pick a company above to load its Quant &amp; MI cockpit.</div>`; return; }
  let q = (QUANT_DATA && QUANT_DATA[QUANT_SYM]) || null;
  if (!q) { host.innerHTML = `<div class='empty-note'>Loading ${fEsc(QUANT_SYM)}…</div>`; q = await ensureQuant(QUANT_SYM); }
  if (!q) { host.innerHTML = `<div class='empty-note'>No Quant &amp; MI data cached for ${fEsc(QUANT_SYM)} (it may lack price or fundamentals history).</div>`; return; }
  const m = q.market || {}, biz = q.business || {}, val = q.valuation || {}, own = q.ownership || {}, sn = q.snapshot || {}, dq = q.data_quality || {};
  const num = (x, s) => (x === null || x === undefined) ? "—" : (x + (s || ""));

  let html = `<div class="quant-head"><div class="qh-name">${fEsc(q.name || q.symbol)} <span class="qh-sym">${fEsc(q.symbol)}</span>${q.is_bank ? ' <span class="qh-bank">bank schema</span>' : ""}</div>`;
  if (m.ok) html += `<div class="qh-px">₹${num(m.price)} <span class="qh-asof">as of ${fEsc(m.asof)}</span></div>`;
  html += `</div>`;

  // ---- Research snapshot ---- each dimension shows the EXACT signals that drove its verdict + its rule
  const dims = sn.dimensions || {};
  html += `<section class="panel qsnap"><h2><span class="tag-sec">SNAPSHOT</span>Research snapshot — rule-based, diagnostics only</h2>`;
  html += `<div class="q-conf">Overall data confidence: <b>${fEsc(sn.confidence || "—")}</b></div>`;
  html += `<div class="q-dimgrid">` + ["Market", "Business", "Valuation", "Ownership"].map((d) => {
    const dd = dims[d] || {};
    const drivers = dd.drivers || [];
    const body = drivers.length
      ? `<ul class="q-drv">` + drivers.map((x) => { const c = x.charAt(0) === "+" ? "pos" : x.charAt(0) === "·" ? "neu" : "neg"; return `<li class="${c}">${fEsc(x)}</li>`; }).join("") + `</ul>`
      : `<div class="q-drv-none">not enough data for this dimension</div>`;
    const rule = dd.rule ? `<details class="q-rule"><summary>how this verdict is scored</summary><p>${fEsc(dd.rule)}</p></details>` : "";
    return `<div class="q-dimcard"><div class="q-dim-head"><span class="q-dim-l">${d}</span>${qChip(dd.verdict)}</div>${body}${rule}</div>`;
  }).join("") + `</div>`;
  html += `<div class="q-lists">` + qList("Top positives (across all four)", sn.top_positives, "pos") + qList("Key risks (across all four)", sn.key_risks, "neg") + qList("Monitor next", sn.monitor_next, "mon") + qList("Caveats", sn.caveats, "cav") + `</div>`;
  if (sn.disclaimer) html += `<div class="q-disc">${fEsc(sn.disclaimer)}</div>`;
  if (sn.method) html += `<details><summary>How the snapshot is computed (all four dimensions)</summary><p>${fEsc(sn.method)}</p></details>`;
  html += `</section>`;

  // ---- Market behaviour ----
  html += `<section class="panel"><h2><span class="tag-sec">MARKET</span>Market behaviour</h2>`;
  if (m.ok) {
    const hl = m.high_low || {}, dd = m.drawdown || {}, dma = m.dma || {}, liq = m.liquidity || {};
    html += `<div class="q-stats">`
      + qStat("52-wk high dist", num(hl.dist_from_52w_high_pct, "%"))
      + qStat("vs 50-DMA", num((dma["50DMA"] || {}).px_vs, "%"))
      + qStat("vs 200-DMA", num((dma["200DMA"] || {}).px_vs, "%"))
      + qStat("Golden cross", m.golden_cross === "above" ? "50 &gt; 200 ✓" : m.golden_cross === "below" ? "50 &lt; 200" : "—")
      + qStat("Drawdown now", num(dd.current_from_peak, "%"))
      + qStat("Max DD (1Y)", num(dd.maxdd_1y, "%"))
      + qStat("Liquidity", liq.median_turnover_cr != null ? "₹" + liq.median_turnover_cr + " cr/d" : "—")
      + qStat("Sector", fEsc(m.sector_index || m.industry || "—"))
      + `</div>`;
    if (liq.warning) html += `<div class="q-warn">${fEsc(liq.warning)}</div>`;
    html += `<div class="qgrid2">`
      + `<div class="qcell"><div class="q-cap">Trailing returns</div><div class="plot" id="plot-quant-returns" style="height:260px"></div></div>`
      + `<div class="qcell"><div class="q-cap">Relative strength vs benchmarks <span class="muted" style="font-weight:400">(rebased to 100 at the start of the chosen window)</span></div>`
      + `<div class="rs-ctl" id="rs-ctl" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:2px 0 6px"></div>`
      + `<div class="plot" id="plot-quant-rs" style="height:260px"></div></div>`
      + `</div>`;
    html += `<details><summary>Definition · Method · Why</summary><p><b>Returns:</b> ${fEsc((m.formulas || {}).returns || "")}</p><p><b>Relative strength:</b> ${fEsc((m.formulas || {}).relative_strength || "")}</p><p><b>Why:</b> ${fEsc((m.notes || {}).why_useful || "")}</p><p><b>Can fail:</b> ${fEsc((m.notes || {}).why_it_can_fail || "")}</p></details>`;
  } else html += `<div class='empty-note'>${fEsc(m.na || "no market data")}</div>`;
  html += `</section>`;

  // ---- Business confirmation ----
  html += `<section class="panel"><h2><span class="tag-sec">BUSINESS</span>Business confirmation</h2>`;
  if (biz.ok) html += `<div class="q-flags">` + (biz.flags || []).map((f) => qFlag(f)).join("") + `</div>`;
  else html += `<div class='empty-note'>${fEsc(biz.na || "no fundamentals cached")}</div>`;
  html += `</section>`;

  // ---- Valuation context ----
  html += `<section class="panel"><h2><span class="tag-sec">VALUATION</span>Valuation context</h2>`;
  if (val.ok) {
    html += `<div class="q-stats">`
      + qStat("P/E now", num(val.pe_now)) + qStat("P/E %ile (10y)", num(val.pe_percentile)) + qStat("Median P/E", num(val.median_pe))
      + qStat("PEG", num(val.peg)) + qStat("Earnings yield", num(val.earnings_yield_pct, "%")) + qStat("P/B", num(val.pb))
      + (val.is_bank ? "" : qStat("EV/EBITDA", num(val.ev_ebitda)))
      + qStat("Quality", num(val.quality_score, "/100")) + qStat("PAT 5y CAGR", num(val.pat_cagr_5y_pct, "%"))
      + `</div>`;
    const cheap = val.cheapness || {};
    if (cheap.read) html += `<div class="q-read ${fEsc(cheap.status || "")}">${fEsc(cheap.read)}</div>`;
    if (val.expectations) html += `<div class="q-read">${fEsc(val.expectations)}</div>`;
    if (val.cyclical_caveat) html += `<div class="q-read weak">⚠ ${fEsc(val.cyclical_caveat)}</div>`;
    html += `<details><summary>Definition · Method · Why</summary><p>${fEsc(val.meaning || "")}</p><p><b>Why:</b> ${fEsc(val.why_useful || "")}</p><p><b>Can fail:</b> ${fEsc(val.why_it_can_fail || "")}</p></details>`;
  } else html += `<div class='empty-note'>${fEsc(val.na || "no fundamentals cached")}</div>`;
  html += `</section>`;

  // ---- Ownership & governance ----
  html += `<section class="panel"><h2><span class="tag-sec">OWNERSHIP</span>Ownership &amp; governance</h2>`;
  const holders = own.holders || {};
  if (Object.keys(holders).length) {
    html += `<div class="q-stats">` + Object.keys(holders).map((k) => {
      const h = holders[k]; const chg = (h.chg_1y_pp != null) ? ` <span class="q-chg ${h.chg_1y_pp >= 0 ? 'up' : 'dn'}">(${h.chg_1y_pp >= 0 ? '+' : ''}${h.chg_1y_pp}pp/yr)</span>` : "";
      return qStat(k, num(h.latest_pct, "%") + chg);
    }).join("") + `</div>`;
    if (own.pledge && own.pledge.latest_pct != null) html += `<div class="q-warn">Promoter pledge: ${own.pledge.latest_pct}%</div>`;
    html += `<div class="q-cap">Shareholding trend (% of equity, quarterly)</div><div class="plot" id="plot-quant-own" style="height:280px"></div>`;
    const reads = own.reads || {};
    if (Object.keys(reads).length) html += `<div class="q-reads">` + Object.values(reads).map((r) => `<div class="q-read">${fEsc(r)}</div>`).join("") + `</div>`;
  } else html += `<div class='empty-note'>${fEsc(own.na || "no shareholding data cached")}</div>`;
  const evs = own.events || [];
  if (evs.length) {
    html += `<div class="q-evt-t">Corporate actions (recent)</div><table class="q-evt"><thead><tr><th>Ex-date</th><th>Action</th><th>Materiality</th></tr></thead><tbody>`
      + evs.slice(0, 12).map((e) => `<tr><td>${fEsc(e.date)}</td><td>${fEsc(e.subject)}</td><td><span class="q-mat ${fEsc(String(e.materiality).toLowerCase())}">${fEsc(e.materiality)}</span></td></tr>`).join("") + `</tbody></table>`;
  }
  if (own.placeholders && own.placeholders.length) {
    html += `<details class="q-ph"><summary>Coming next (MVP-2) — labelled placeholders</summary><ul>` + own.placeholders.map((p) => `<li><b>${fEsc(p.label)}:</b> ${fEsc(p.note)}</li>`).join("") + `</ul></details>`;
  }
  if (own.meaning) html += `<details><summary>Definition · Method · Why</summary><p>${fEsc(own.meaning)}</p><p><b>Why:</b> ${fEsc(own.why_useful || "")}</p><p><b>Can fail:</b> ${fEsc(own.why_it_can_fail || "")}</p></details>`;
  html += `</section>`;

  // ---- Smart-money flow (cross-AMC active mutual-fund manager flows; MoneyBall Layer D#1) ----
  // #51 — 3-way flow decomposition. The panel can plot any of three history arrays (all aligned to
  // smf.months[]) and headline the matching decomp scalar:
  //   Net-active  = conviction, weight-space, inflow-immune  [DEFAULT]
  //   Price-adj   = price stripped only (== the legacy `flow`; still carries scheme inflows)
  //   Gross       = raw ₹ change (price + inflow + conviction)
  // If the new keys are absent (older deck), we fall back to the legacy `flow`/`rank` behaviour.
  const smf = q.smart_money_flow || null;
  if (smf && smf.months && smf.months.length) {
    const n = smf.months.length, i = n - 1;
    const hasDecomp = !!(smf.net_active || smf.price_adj || smf.gross || smf.decomp);
    // which history array + headline scalar the current mode resolves to (graceful fallbacks)
    const SMF_MODES = [
      { key: "net_active", label: "Net-active (conviction)", hist: smf.net_active,
        tip: "Net-active = weight-space conviction, inflow-immune (the rupees managers re-weighted toward the stock independent of price and fresh scheme inflows)." },
      { key: "price_adj", label: "Price-adjusted", hist: smf.price_adj || smf.flow,
        tip: "Price-adjusted = strips price drift only; still includes new money the scheme received and had to deploy (inflow-contaminated)." },
      { key: "gross", label: "Gross", hist: smf.gross,
        tip: "Gross = the raw ₹ change in the position (price move + scheme inflows + active conviction, undecomposed)." },
    ];
    let modes = hasDecomp ? SMF_MODES.filter((mo) => Array.isArray(mo.hist) && mo.hist.length) : [];
    let curMode = modes.find((mo) => mo.key === SMF_MODE) || modes[0] || null;
    if (curMode) SMF_MODE = curMode.key;
    // history series + the headline ₹cr scalar for the current month, by mode (legacy fallback uses smf.flow)
    const histSeries = curMode ? curMode.hist : (smf.flow || []);
    const dcomp = smf.decomp || {};
    const scalarFor = (mo) => {
      if (!mo) return (smf.flow ? smf.flow[i] : null);
      if (mo.key === "net_active") return (dcomp.net_active_cr != null ? dcomp.net_active_cr : (mo.hist ? mo.hist[i] : null));
      if (mo.key === "price_adj") return (dcomp.price_adj_cr != null ? dcomp.price_adj_cr : (mo.hist ? mo.hist[i] : null));
      return (dcomp.gross_cr != null ? dcomp.gross_cr : (mo.hist ? mo.hist[i] : null));
    };
    const lf = scalarFor(curMode);
    const lb = smf.breadth[i], lr = smf.rank[i], nc = smf.nclean[i], lca = smf.ca[i];
    const buy = smf.buyers[i], sel = smf.sellers[i];
    const dB = (n >= 2 && lb != null && smf.breadth[i - 1] != null) ? lb - smf.breadth[i - 1] : null;
    // rank/intensity headline prefers the net-active conviction context when present
    const li = (curMode && curMode.key === "net_active" && dcomp.na_intensity != null) ? dcomp.na_intensity
             : (smf.intensity && smf.intensity[i] != null) ? smf.intensity[i] : null;
    const rk = (curMode && curMode.key === "net_active" && dcomp.na_rank != null) ? dcomp.na_rank : lr;
    const ncc = (curMode && curMode.key === "net_active" && dcomp.na_nclean != null) ? dcomp.na_nclean : nc;
    const flowTxt = (lf == null) ? "—" : `<span class="${lf >= 0 ? 'pos' : 'neg'}">${lf >= 0 ? '+' : ''}₹${Math.round(lf).toLocaleString('en-IN')} cr</span>`;
    const rankTxt = (rk && ncc) ? (lf >= 0 ? `#${rk} most accumulated` : `#${ncc - rk + 1} most reduced`) + ` <span class="q-sub">of ${ncc}${li != null ? `, ${li >= 0 ? '+' : ''}${li}% of position` : ''}</span>` : "—";
    const flowLbl = curMode ? curMode.label : "Net flow";
    html += `<section class="panel"><h2><span class="tag-sec">FLOW</span>Smart-money flow — net active mutual-fund buying</h2>`;
    if (lca) html += `<div class="q-warn">A structural corporate action (merger/demerger) affected this stock in ${fEsc(smf.months[i])}; that month's flow is quarantined, not a clean discretionary signal.</div>`;
    if (modes.length > 1) {
      html += `<div class="smf-modeseg fs-lvl-seg" title="How the flow is decomposed — Gross (raw ₹) → Price-adjusted (price stripped) → Net-active (inflow-immune conviction)">`
        + modes.map((mo) => `<button type="button" class="fs-lvl smf-modebtn${mo.key === SMF_MODE ? " on" : ""}" data-smf="${mo.key}" title="${attEsc(mo.tip)}">${fEsc(mo.label)}</button>`).join("")
        + `</div>`;
    }
    html += `<div class="q-stats">`
      + qStat(`${flowLbl} (${fEsc(smf.months[i])})`, flowTxt)
      + qStat("Conviction rank", rankTxt)
      + qStat("Active funds holding", lb != null ? lb : "—")
      + qStat("Funds added / trimmed", `${buy} / ${sel}`)
      + qStat("Breadth Δ (1m)", dB != null ? `${dB >= 0 ? '+' : ''}${dB} funds` : "—")
      + `</div>`;
    const capLbl = curMode ? curMode.label.toLowerCase() : "net active-manager";
    html += `<div class="qcell"><div class="q-cap">${fEsc(capLbl)} flow (₹ cr, bars) &amp; breadth (# funds, line) — last ${n} months</div><div class="plot" id="plot-quant-flow" style="height:300px"></div></div>`;
    html += `<details><summary>Definition · Method · Why</summary><p><b>Three views of the same flow</b> (the decomposition, switchable above): <b>Gross</b> = the raw rupee change in the position (price move + scheme inflows + conviction, undecomposed); <b>Price-adjusted</b> = strips the price drift only [ end-value − start-value×(1 + the stock's total return that month) ] — but still carries the fresh money a scheme received and had to deploy; <b>Net-active</b> = the inflow-immune <i>conviction</i> figure, measured in weight-space [ AUM×(1+R) × Δ(active weight) ], so a fund merely parking new inflows pro-rata shows ~zero. Corporate actions (splits/bonuses/mergers) are absorbed (total return) / bridged (merger swaps) / quarantined (demergers). <b>Breadth</b> = number of active funds holding it; <b>added/trimmed</b> = funds that raised vs cut net of drift. <b>Conviction rank</b> orders the stock by flow as a % of the average position that month — size-neutral. <b>Why:</b> price tells you what happened; this tells you what professional managers <i>decided</i> — and only the Net-active view isolates real conviction from money they were simply handed. Source: all-AMC monthly holdings × our NSE total-return panel. Coverage control: only schemes reporting in both months are counted. Diagnostic, not a guaranteed signal.</p></details>`;
    html += `</section>`;
    // stash the resolved history for the chart render below (avoids recomputing)
    smf._activeHist = histSeries;
    smf._activeLabel = flowLbl;
  }

  // ---- Mutual-fund ownership (who owns this stock) — links the Funds intelligence into the stock cockpit ----
  const fh = q.fund_holders || null;
  if (fh && fh.top && fh.top.length) {
    html += `<section class="panel"><h2><span class="tag-sec">HOLDERS</span>Mutual-fund ownership — who owns this stock</h2>`;
    html += `<div class="q-stats">` + qStat("Funds holding", fh.n_funds) + qStat("Total MF holding", `₹${Math.round(fh.total_cr).toLocaleString('en-IN')} cr`) + qStat("As of", fEsc(fh.ym || '')) + `</div>`;
    html += `<table class="gauge-tbl"><thead><tr><th>Fund</th><th>AMC</th><th class="num">% of fund</th><th class="num">₹cr</th></tr></thead><tbody>`
      + fh.top.map((t) => `<tr><td>${fEsc(t.name)}</td><td>${fEsc(t.amc)}</td><td class="num">${t.pct != null ? t.pct + '%' : '—'}</td><td class="num">${Math.round(t.cr).toLocaleString('en-IN')}</td></tr>`).join('')
      + `</tbody></table>`;
    html += `<details><summary>Method</summary><p>The mutual-fund schemes holding this stock at the latest portfolio snapshot (${fEsc(fh.ym || '')}), ranked by rupee value; "% of fund" is the stock's weight in that scheme. Source: the all-AMC monthly holdings store. Read it with the smart-money flow above — who owns it, and who's been buying or selling.</p></details>`;
    html += `</section>`;
  }

  // ---- Data quality ----
  html += `<section class="panel qdq"><h2><span class="tag-sec">DATA</span>Data quality &amp; provenance</h2><div class="q-stats">`
    + qStat("Price as-of", fEsc(dq.price_asof || "—")) + qStat("Fundamentals fetched", fEsc(dq.fundamentals_fetched || "—"))
    + qStat("Has fundamentals", dq.has_fundamentals ? "yes" : "no") + qStat("Has shareholding", dq.has_shareholding ? "yes" : "no")
    + `</div><div class="q-note">${fEsc(dq.note || "")}</div></section>`;

  host.innerHTML = html;

  // ---- charts: build AFTER innerHTML. Never set a Plotly trace key to undefined (omit it). ----
  try {
    if (m.ok) {
      const order = ["1M", "3M", "6M", "12M"], rr = m.returns || {};
      const ys = order.map((k) => (rr[k] === undefined ? null : rr[k]));
      const bar = { type: "bar", x: order, y: ys, marker: { color: ys.map((v) => v == null ? "#9aa0a6" : v >= 0 ? "#2ca02c" : "#d62728") }, hovertemplate: "%{x}: %{y}%<extra></extra>" };
      Plotly.react("plot-quant-returns", [bar], baseLayout({ yaxis: { title: "%", gridcolor: "#dfe3e8", zeroline: true, zerolinecolor: "#bbb" } }), PCONF);
      const COL = { "NIFTY 50": "#1f77b4", "NIFTY 500": "#9467bd" };
      const rsTraces = []; const rb = m.rs_broad || {};
      Object.keys(rb).forEach((bn) => { const ln = (rb[bn] || {}).rs_line; if (ln && ln.values && ln.values.length) rsTraces.push({ type: "scatter", mode: "lines", name: "vs " + bn, x: ln.dates, y: ln.values, line: { color: COL[bn] || "#888", width: 2 }, hovertemplate: bn + ": %{y}<extra></extra>" }); });
      const rsec = m.rs_sector || {}; if (rsec.rs_line && rsec.rs_line.values && rsec.rs_line.values.length) rsTraces.push({ type: "scatter", mode: "lines", name: "vs " + (m.sector_index || "sector"), x: rsec.rs_line.dates, y: rsec.rs_line.values, line: { color: "#d62728", width: 2, dash: "dot" }, hovertemplate: "sector: %{y}<extra></extra>" });
      if (rsTraces.length) { QRS_FULL = rsTraces; buildRSCtl("1Y"); const r = rsPreset("1Y"); if (r) rsCrop(r[0], r[1]); else { Plotly.react("plot-quant-rs", rsTraces, baseLayout({ yaxis: { gridcolor: "#dfe3e8" } }), PCONF); attachYAutoscale("plot-quant-rs"); } }
      else { QRS_FULL = null; const rc = $("rs-ctl"); if (rc) rc.innerHTML = ""; const el = $("plot-quant-rs"); if (el) el.innerHTML = "<div class='empty-note'>no benchmark overlap to compute relative strength</div>"; }
      attachYAutoscale("plot-quant-returns");
    }
    if (Object.keys(holders).length) {
      const HCOL = { Promoter: "#8c564b", FII: "#1f77b4", DII: "#2ca02c", Public: "#7f7f7f", Government: "#bcbd22" };
      const otr = Object.keys(holders).filter((k) => holders[k].values && holders[k].values.some((v) => v != null))
        .map((k) => ({ type: "scatter", mode: "lines+markers", name: k, x: holders[k].periods, y: holders[k].values, line: { color: HCOL[k] || "#888", width: 2 }, hovertemplate: k + ": %{y}%<extra></extra>" }));
      if (otr.length) { Plotly.react("plot-quant-own", otr, baseLayout({ yaxis: { title: "%", gridcolor: "#dfe3e8" } }), PCONF); attachYAutoscale("plot-quant-own"); }
    }
    if (smf && smf.months && smf.months.length && $("plot-quant-flow")) {
      const hist = (smf._activeHist && smf._activeHist.length) ? smf._activeHist : smf.flow;   // #51 active decomposition view
      const fcol = (hist || []).map((v, idx) => (smf.ca && smf.ca[idx]) ? "#c9ccd1" : (v >= 0 ? "#2ca02c" : "#d62728"));
      const fbar = { type: "bar", x: smf.months, y: hist, name: (smf._activeLabel || "net flow") + " ₹cr", marker: { color: fcol }, hovertemplate: "%{x}: ₹%{y} cr<extra></extra>" };
      const bln = { type: "scatter", mode: "lines", name: "breadth (# funds)", x: smf.months, y: smf.breadth, yaxis: "y2", line: { color: "#1f77b4", width: 2 }, hovertemplate: "%{x}: %{y} funds<extra></extra>" };
      Plotly.react("plot-quant-flow", [fbar, bln], baseLayout({ yaxis: { title: "₹ cr", gridcolor: "#dfe3e8", zeroline: true, zerolinecolor: "#bbb" }, yaxis2: { title: "# funds", overlaying: "y", side: "right", showgrid: false } }), PCONF);
    }
  } catch (e) { console.error("renderQuant charts:", e); }

  // #51 — wire the flow-decomposition mode switch (re-renders the whole quant cockpit with the new view)
  host.querySelectorAll(".smf-modebtn").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.smf === SMF_MODE) return;
    SMF_MODE = b.dataset.smf;
    renderQuant();
  }));
}
async function initQuant() {
  const tab = $("tabs") && $("tabs").querySelector('[data-view="quant"]');
  const manifest = quantManifest();
  const syms = manifest ? Object.keys(manifest).sort() : (QUANT_DATA ? Object.keys(QUANT_DATA).sort() : []);
  if (tab) { const any = syms.length > 0; tab.disabled = !any; tab.style.opacity = any ? "" : ".45"; tab.title = any ? "" : "No Quant & MI data in this deck yet"; }
  if (!syms.length) return;
  const nameOf = (s) => manifest ? (manifest[s] || "") : ((QUANT_DATA && QUANT_DATA[s] && QUANT_DATA[s].name) || "");
  const items = syms.map((s) => { const nm = nameOf(s); return { sym: s, name: nm, disp: nm ? s + " — " + nm : s }; });
  if (!QUANT_SYM) QUANT_SYM = syms[0];
  if ($("quant-combo")) { QUANT_COMBO = new ComboBox($("quant-combo"), { placeholder: "name, ticker or acronym…", onPick: (v) => { QUANT_SYM = v || syms[0]; renderQuant(); writeHash(); } }); QUANT_COMBO.setItems(items); QUANT_COMBO.setValue(QUANT_SYM); }
}

// ---- Funds tab: compare the fund's book to a chosen NSE index benchmark (dropdown; EW / free-float) ----
async function renderFundsBench(holdings, category, hostId) {
  hostId = hostId || "funds-bench-host";
  const hostEl = $(hostId); if (!hostEl) return;
  const man = benchmarkManifest(); const names = Object.keys(man);
  if (!names.length || !(holdings && holdings.length)) { hostEl.style.display = "none"; return; }
  hostEl.style.display = "";
  if (!FUNDS_BENCH.slug || !names.some((n) => man[n].slug === FUNDS_BENCH.slug)) FUNDS_BENCH.slug = defaultBenchForCategory(category);
  let curName = names.find((n) => man[n].slug === FUNDS_BENCH.slug) || names[0];
  FUNDS_BENCH.slug = man[curName].slug;
  hostEl.innerHTML = `<h2><span class="tag-sec">VS BENCHMARK</span>Compare to a benchmark</h2><div class='empty-note'>Loading ${fEsc(curName)}…</div>`;
  const bench = await ensureBenchmark(FUNDS_BENCH.slug);
  if (!bench) { hostEl.innerHTML = `<h2><span class="tag-sec">VS BENCHMARK</span>Compare to a benchmark</h2><div class='empty-note'>Benchmark unavailable.</div>`; return; }
  const wk = FUNDS_BENCH.weight;
  const r = fundsBenchCompare(holdings || [], bench, wk);
  const opts = names.map((n) => `<option value="${man[n].slug}"${man[n].slug === FUNDS_BENCH.slug ? " selected" : ""}>${fEsc(n)}</option>`).join("");
  const asColor = r.active_share >= 70 ? "pos" : r.active_share <= 50 ? "neg" : "neu";
  let html = `<h2><span class="tag-sec">VS BENCHMARK</span>Compare to a benchmark</h2>`;
  html += `<div class="fb-controls"><label>Benchmark <select id="fb-index">${opts}</select></label>`
    + `<label>Weights <select id="fb-weight"><option value="ffmcap"${wk === "ffmcap" ? " selected" : ""}>free-float mcap</option><option value="ew"${wk === "ew" ? " selected" : ""}>equal weight</option></select></label></div>`;
  if (!r.eq_covered) html += `<div class="q-warn">This fund has no equity holdings to compare (debt/liquid).</div>`;
  html += `<div class="q-stats">`
    + qStat("Active share vs benchmark", `<span class="${asColor}">${r.active_share.toFixed(1)}%</span>`)
    + qStat("Overlap", `${r.overlap.toFixed(1)}%`)
    + qStat("Common names", `${r.n_overlap}`)
    + qStat("Fund equity names", `${r.n_fund}`)
    + qStat("Benchmark names", `${r.n_bench}`)
    + `</div>`;
  if (bench.low_confidence) html += `<div class="q-warn">Benchmark mcap coverage is partial — reconstructed weights are approximate here.</div>`;
  html += `<div class="qgrid2"><div class="qcell"><div class="q-cap">Sector tilt vs benchmark (fund − benchmark, % pts)</div><div class="plot" id="plot-fb-tilt" style="height:300px"></div></div>`;
  const ovRow = (x) => `<tr><td>${fEsc(x.name || x.sym || "")}</td><td>${fEsc(x.sym || "")}</td><td class="num">${x.wf.toFixed(2)}</td><td class="num">${x.wb.toFixed(2)}</td><td class="num ${x.d >= 0 ? "pos" : "neg"}">${x.d >= 0 ? "+" : ""}${x.d.toFixed(2)}</td></tr>`;
  html += `<div class="qcell"><div class="q-cap">Biggest active <span class="pos">over-weights</span> (held &gt; benchmark)</div><table class="gauge-tbl"><thead><tr><th>Stock</th><th>Tkr</th><th class="num">Fund%</th><th class="num">Bmk%</th><th class="num">Δ</th></tr></thead><tbody>${r.over.map(ovRow).join("") || "<tr><td>—</td></tr>"}</tbody></table>`;
  html += `<div class="q-cap" style="margin-top:8px">Biggest active <span class="neg">under-weights</span> (benchmark names under-held / absent)</div><table class="gauge-tbl"><tbody>${r.under.map(ovRow).join("") || "<tr><td>—</td></tr>"}</tbody></table></div></div>`;
  html += `<details><summary>Definition · Method · Why</summary><p><b>Active share vs benchmark</b> = ½·Σ|w_fund − w_benchmark| across every stock by identity — the % of the fund that differs from the index (≲50% hugs the index, ≳70% is highly differentiated). On the fund's EQUITY sleeve renormalised to 100%, vs the reconstructed benchmark. <b>Overlap</b> = Σ min(w_fund, w_bmk). <b>Sector tilt</b> = fund sector weight − benchmark sector weight. <b>Honest caveat:</b> benchmark weights are RECONSTRUCTED — ${fEsc(bench.note || "")} — AMFI full mcap × (1−promoter%) with a simple cap, NOT official NSE IWF/caps (close for broad indices, approximate for capped sectoral ones). The ≥70 "high"/≤50 "closet" bands are practitioner conventions (Cremers-Petajisto), not data-derived. Sector/thematic mandates inflate active share by construction. This is the true benchmark-relative active share, complementing the peer-relative one in the Fund Skill tab.</p></details>`;
  hostEl.innerHTML = html;
  try {
    const t = r.tilt.slice(0, 12).slice().reverse();
    const tel = document.getElementById("plot-fb-tilt");   // guard: a concurrent re-render may have wiped it
    if (tel && t.length) {
      const cats = t.map((s) => s.sector);   // PIN the y category order to the data order (+ purge stale
      Plotly.purge(tel);                     // state) so a re-render can't desync ticks/bars/hover labels
      Plotly.react(tel, [{ type: "bar", orientation: "h", x: t.map((s) => s.d), y: cats, marker: { color: t.map((s) => s.d >= 0 ? "#2ca02c" : "#d62728") }, hovertemplate: "%{y}: %{x:.2f} pts<extra></extra>" }], baseLayout({ yaxis: { type: "category", categoryorder: "array", categoryarray: cats, automargin: true }, xaxis: { title: "fund − benchmark (% pts)", gridcolor: "#dfe3e8", zeroline: true }, margin: { l: 175, r: 12, t: 8, b: 36 } }), PCONF);
    }
  } catch (e) { console.error("fb tilt:", e); }
  const si = $("fb-index"); if (si) si.onchange = () => { FUNDS_BENCH.slug = si.value; renderFundsBench(holdings, category, hostId); };
  const sw = $("fb-weight"); if (sw) sw.onchange = () => { FUNDS_BENCH.weight = sw.value; renderFundsBench(holdings, category, hostId); };
}

// ── MULTI-FUND side-by-side vs ONE benchmark ──────────────────────────────────────────────────────
// KV's "compare one OR MULTIPLE schemes side by side to a chosen benchmark". Anchor = the selected
// fund; peers added from the dropdown. Each fund's equity book is the same baked crowd_flow.equity_holdings
// (vst_id+pct+sector), lazy-fetched per fund client-side — no rebuild. Same fundsBenchCompare engine, run
// once per fund against ONE shared (cached) benchmark object. Display-only; no analytics.py change.
function cmpShort(s) { s = String(s || ""); return s.length > 30 ? s.slice(0, 28) + "…" : s; }
function cmpEqW(holdings) {                 // fund equity weights by vst_id, renormalised to the equity sleeve (sum 100)
  const eq = (holdings || []).filter((h) => h.vst_id && h.pct != null && (h.asset_class === undefined || /equ/i.test(h.asset_class || "")));
  let s = 0; const w = {}, nm = {};
  eq.forEach((h) => { const x = +h.pct || 0; if (x <= 0) return; w[h.vst_id] = (w[h.vst_id] || 0) + x; s += x; nm[h.vst_id] = h.name || h.symbol || h.vst_id; });
  if (s > 0) Object.keys(w).forEach((k) => { w[k] = w[k] * 100 / s; });
  return { w, nm };
}
async function renderFundCompare(anchorCode, anchorCat, hostId) {
  const hostEl = $(hostId); if (!hostEl) return;
  const man = fundsAttrManifest() || {};
  const bm = benchmarkManifest(); const benchNames = Object.keys(bm);
  if (!anchorCode || !man[anchorCode] || !benchNames.length) { hostEl.style.display = "none"; return; }
  hostEl.style.display = "";
  // compared set = anchor + valid distinct peers (cap 5)
  let set = [anchorCode].concat((FUNDS_CMP.peers || []).filter((c) => c !== anchorCode && man[c]));
  set = set.filter((c, i) => set.indexOf(c) === i).slice(0, 5);
  FUNDS_CMP.peers = set.filter((c) => c !== anchorCode);
  // shared benchmark (seed from the panel above, else the anchor's category default)
  if (!FUNDS_CMP.slug || !benchNames.some((n) => bm[n].slug === FUNDS_CMP.slug)) FUNDS_CMP.slug = FUNDS_BENCH.slug || defaultBenchForCategory(anchorCat);
  const curName = benchNames.find((n) => bm[n].slug === FUNDS_CMP.slug) || benchNames[0];
  FUNDS_CMP.slug = bm[curName].slug;
  const wk = FUNDS_CMP.weight, hdr = `<h2><span class="tag-sec">SIDE BY SIDE</span>Compare funds vs a benchmark</h2>`;
  hostEl.innerHTML = hdr + `<div class='empty-note'>Loading…</div>`;
  const bench = await ensureBenchmark(FUNDS_CMP.slug);
  if (!bench) { hostEl.innerHTML = hdr + `<div class='empty-note'>Benchmark unavailable.</div>`; return; }
  // fetch each fund's book + compute against the one bench
  const rows = [];
  for (const code of set) {
    const fa = await ensureFundsAttr(code);
    const book = (fa && fa.crowd_flow && fa.crowd_flow.equity_holdings) || [];
    rows.push({ code, name: (man[code] && man[code].name) || code, cat: (man[code] && man[code].category) || "", r: fundsBenchCompare(book, bench, wk), eqw: cmpEqW(book), anchor: code === anchorCode });
  }
  const bwk = wk === "ew" ? "w_ew" : "w_ffmcap", bW = {}, bNm = {};
  (bench.constituents || []).forEach((c) => { if (c.vst_id) { bW[c.vst_id] = (+c[bwk] || 0); bNm[c.vst_id] = c.name || c.symbol; } });
  const colOf = (code) => CMP_PAL[set.indexOf(code) % CMP_PAL.length];

  // controls
  const opts = benchNames.map((n) => `<option value="${bm[n].slug}"${bm[n].slug === FUNDS_CMP.slug ? " selected" : ""}>${fEsc(n)}</option>`).join("");
  const dlOpts = Object.keys(man).filter((c) => man[c].name).sort((a, b) => man[a].name.localeCompare(man[b].name)).map((c) => `<option value="${fEsc(man[c].name)}">`).join("");
  let html = hdr;
  html += `<div class="fb-controls"><label>Benchmark <select id="cmp-index">${opts}</select></label>`
    + `<label>Weights <select id="cmp-weight"><option value="ffmcap"${wk === "ffmcap" ? " selected" : ""}>free-float mcap</option><option value="ew"${wk === "ew" ? " selected" : ""}>equal weight</option></select></label>`
    + `<label>Add fund <input id="cmp-add-in" class="cmp-add-in" list="cmp-fundlist" placeholder="type a fund name…"></label><datalist id="cmp-fundlist">${dlOpts}</datalist></div>`;
  // fund chips
  html += `<div class="cmp-chips">` + set.map((c) => `<span class="cmp-chip" style="border-left-color:${colOf(c)}">${fEsc((man[c] && man[c].name) || c)}${c === anchorCode ? ' <span class="cmp-anchor">selected</span>' : `<button class="cmp-x" data-c="${fEsc(c)}" title="remove">×</button>`}</span>`).join("") + `</div>`;
  if (set.length < 2) html += `<div class="q-cap" style="text-align:left;margin:2px 0 8px">Add one or more funds above to compare their bets side by side against the same index.</div>`;

  // metrics table
  const tnum = (v, suff) => (v == null || isNaN(v)) ? "—" : v.toFixed(1) + (suff || "");
  const topOW = (r) => (r.over && r.over.length) ? `${fEsc(r.over[0].name)} <span class="pos">+${r.over[0].d.toFixed(1)}</span>` : "—";
  const topUW = (r) => (r.under && r.under.length) ? `${fEsc(r.under[0].name)} <span class="neg">${r.under[0].d.toFixed(1)}</span>` : "—";
  html += `<div class="screen-tblwrap"><table class="gauge-tbl cmp-tbl"><thead><tr><th>Fund</th><th>Category</th><th class="num">Active share</th><th class="num">Overlap</th><th class="num">Eq names</th><th>Top over-weight</th><th>Top under-weight</th></tr></thead><tbody>`;
  html += rows.map((x) => `<tr${x.anchor ? ' class="cmp-anchor-row"' : ''}><td class="lft"><span class="cmp-dot" style="background:${colOf(x.code)}"></span><b>${fEsc(x.name)}</b></td><td class="lft sec">${fEsc(x.cat)}</td><td class="num">${tnum(x.r.active_share, "%")}</td><td class="num">${tnum(x.r.overlap, "%")}</td><td class="num">${x.r.n_fund}</td><td class="lft">${topOW(x.r)}</td><td class="lft">${topUW(x.r)}</td></tr>`).join("");
  html += `</tbody></table></div>`;

  // grouped sector-tilt chart
  html += `<div class="q-cap" style="margin-top:10px">Sector tilt vs ${fEsc(curName)} (fund − benchmark, % pts) — one colour per fund</div><div class="plot" id="plot-cmp-tilt" style="height:340px"></div>`;

  // biggest disagreements (only meaningful with >=2 funds)
  if (set.length >= 2) {
    const idset = {};
    rows.forEach((x) => Object.keys(x.eqw.w).forEach((id) => { idset[id] = true; }));
    const dis = Object.keys(idset).map((id) => {
      const ws = rows.map((x) => x.eqw.w[id] || 0);
      return { id, nm: (rows.map((x) => x.eqw.nm[id]).find(Boolean) || bNm[id] || id), ws, spread: Math.max(...ws) - Math.min(...ws), b: bW[id] || 0 };
    }).sort((a, b) => b.spread - a.spread).slice(0, 8);
    html += `<div class="q-cap" style="margin-top:12px">Biggest disagreements — stocks these funds weight most differently (% of equity sleeve)</div>`;
    html += `<div class="screen-tblwrap"><table class="gauge-tbl cmp-tbl"><thead><tr><th>Stock</th>` + rows.map((x) => `<th class="num"><span class="cmp-dot" style="background:${colOf(x.code)}"></span>${fEsc(cmpShort(x.name))}</th>`).join("") + `<th class="num">Bmk</th></tr></thead><tbody>`;
    html += dis.map((d) => `<tr><td class="lft">${fEsc(d.nm)}</td>` + d.ws.map((w) => `<td class="num">${w ? w.toFixed(1) : "·"}</td>`).join("") + `<td class="num">${d.b ? d.b.toFixed(1) : "·"}</td></tr>`).join("");
    html += `</tbody></table></div>`;
  }

  html += `<details><summary>Definition · Method · Why</summary><p><b>What:</b> any of your selected funds compared <b>side by side</b> against the SAME reconstructed NSE index. <b>Active share vs benchmark</b> = ½·Σ|w_fund − w_benchmark| by stock identity (the % of the fund that differs from the index); <b>Overlap</b> = Σ min(w_fund, w_bmk). <b>Sector tilt</b> = fund sector weight − benchmark sector weight, grouped so you read how differently each fund leans. <b>Biggest disagreements</b> = the stocks with the widest spread of weights across the chosen funds (a clean read of where the managers actually differ). Each fund's EQUITY sleeve is renormalised to 100% before the comparison. <b>Caveat:</b> index weights are RECONSTRUCTED (free-float = mcap × (1−promoter%) as a proxy for NSE's IWF), close for broad indices, approximate for capped sectorals.</p></details>`;
  hostEl.innerHTML = html;

  // grouped sector-tilt: top sectors by max |tilt| across funds
  try {
    const tiltMaps = rows.map((x) => { const m = {}; (x.r.tilt || []).forEach((t) => { m[t.sector] = t.d; }); return m; });
    const score = {};
    rows.forEach((x) => (x.r.tilt || []).forEach((t) => { score[t.sector] = Math.max(score[t.sector] || 0, Math.abs(t.d)); }));
    const topSecs = Object.keys(score).sort((a, b) => score[b] - score[a]).slice(0, 10);
    const tel = document.getElementById("plot-cmp-tilt");
    if (tel && topSecs.length) Plotly.react(tel, rows.map((x, i) => ({ type: "bar", name: cmpShort(x.name), x: topSecs, y: topSecs.map((s) => tiltMaps[i][s] || 0), marker: { color: colOf(x.code) }, hovertemplate: "%{x}: %{y:.2f} pts<extra>" + fEsc(cmpShort(x.name)) + "</extra>" })),
      baseLayout({ barmode: "group", xaxis: { tickangle: -35, gridcolor: "#eef1f4" }, yaxis: { title: "fund − benchmark (% pts)", gridcolor: "#eef1f4", zeroline: true, zerolinecolor: "#c8ced4" }, legend: { orientation: "h", y: -0.4, font: { size: 10 } }, margin: { l: 54, r: 12, t: 8, b: 130 } }), PCONF);
  } catch (e) { console.error("cmp tilt:", e); }

  // wire controls
  const si = $("cmp-index"); if (si) si.onchange = () => { FUNDS_CMP.slug = si.value; renderFundCompare(anchorCode, anchorCat, hostId); };
  const sw = $("cmp-weight"); if (sw) sw.onchange = () => { FUNDS_CMP.weight = sw.value; renderFundCompare(anchorCode, anchorCat, hostId); };
  hostEl.querySelectorAll(".cmp-x").forEach((b) => { b.onclick = () => { FUNDS_CMP.peers = (FUNDS_CMP.peers || []).filter((c) => c !== b.dataset.c); renderFundCompare(anchorCode, anchorCat, hostId); }; });
  const ai = $("cmp-add-in");
  if (ai) ai.onchange = () => {
    const v = ai.value.trim();
    const code = Object.keys(man).find((c) => man[c].name === v);
    if (code && code !== anchorCode && !(FUNDS_CMP.peers || []).includes(code)) { FUNDS_CMP.peers.push(code); renderFundCompare(anchorCode, anchorCat, hostId); }
    else ai.value = "";
  };
}

// ============================== SCREENS tab — "Smart-money vs the Street" cross-sectional NSE-500 screen ==============================
// Pre-filtered watchlist (price correction + deteriorating fundamentals), split into 4 quadrants by
// Analyst (LSEG StarMine ARM >=50 = recommending) x FM (corp-action-adjusted net active flow >0 = buying).
// Display-only: renders the Python-baked window.VISTAS_SCREEN_SVS — no analytics here, nothing to parity-port.
let SCREEN_WIN = "3m";                 // FM-flow window driving the quadrant split (KV: default 3-month; 1-month also shown)
let SCREEN_AMC = "";                   // "" = All MF; else only stocks held by this AMC (magnitude filters apply)
let SCREEN_AMT = false;                // ownership/MF columns: false = % (of mcap), true = absolute ₹ cr
let SCREEN_RUPEE_MIN = 0;              // AMC/MF holding ₹cr threshold (0 = any)
let SCREEN_PCTAUM_MIN = null;          // min % of the selected AMC's AUM in the stock (null = off)
let SCREEN_TROUBLED = false;           // optional "troubled only" preset (price correction + deteriorating)
let SCREEN_COLF = {};                  // Excel-like per-column filters: {key: ">5" | "<0" | "10-50" | ...}
let SCREEN_SORT = { key: "mcap_cr", dir: -1 };
let SCREEN_DATA = null;                // lazy-fetched full screen object (rows are ~1.5MB, not inlined)
let SCREEN_FLOW_BASIS = "price_adj";   // #106 FM-axis flow basis: "price_adj" (legacy default) | "gross" | "net_active"
const SCREEN_BASIS_LABEL = { price_adj: "Price-adjusted", gross: "Gross", net_active: "Net-active (conviction)" };
const SCREEN_BASIS_AXIS = {
  price_adj: "price-adjusted net flow (₹cr, signed-log)",
  gross: "gross flow — price + inflow + active (₹cr, signed-log)",
  net_active: "net-active conviction flow — inflow-immune (₹cr, signed-log)",
};
const SCREEN_BASIS_DEF = {
  price_adj: "strips price drift only (= the legacy axis); still carries new scheme money deployed pro-rata",
  gross: "the raw ₹ change in MF holdings — price move + scheme inflows + active conviction, undecomposed",
  net_active: "weight-space conviction, inflow-immune — the rupees managers re-weighted toward the stock independent of price and fresh inflows (the truest read of conviction)",
};
const SCREEN_QCOL = { 1: "#2ca02c", 2: "#1f77b4", 3: "#9467bd", 4: "#8a8f98" };
const SCREEN_DET = { "operating": { c: "#d62728", t: "operating" }, "headline-only": { c: "#ff7f0e", t: "headline-only" }, "mixed": { c: "#e0a000", t: "mixed" } };
// --- Quadrant-rotation (A3) state ---
let ROT_VIEW = "stock";                // rotation sub-view: "stock" (per-stock trail) | "centroid" (portfolio)
let ROT_STOCK = null;                  // selected stock symbol for the stock-trail panel
let ROT_STOCK_MON = null;              // null = show full trail; else the month index the slider is on
let ROT_CENTROIDS = null;             // lazy-fetched centroids.json ({meta, entities:[...]})
let ROT_CENT_FETCHED = false;          // guard so we fetch the 6.35MB file at most once
let ROT_ENTITY = null;                 // selected entity_id for the centroid trail
let ROT_CENT_MON = null;               // month index for the centroid slider (null = full)
const ROT_QCOL = { 1: "#2ca02c", 2: "#1f77b4", 3: "#9467bd", 4: "#8a8f98" };   // Q1 rec+buy · Q2 rec+notbuy · Q3 notrec+buy · Q4 notrec+sell
const ROT_QLBL = { 1: "Q1 · recommending & buying", 2: "Q2 · recommending, not buying", 3: "Q3 · not recommending, buying", 4: "Q4 · not recommending, selling" };
function rotSlog(f) { const v = +f || 0; return Math.sign(v) * Math.log10(1 + Math.abs(v)); }   // signed-log ₹cr (keeps x=0 exact)
// the shared 4-quadrant backdrop (zero-flow vertical + ARM=50 horizontal dividers + corner labels)
function rotQuadLayout(xsAll, extra) {
  const fin = (xsAll || []).filter((v) => isFinite(v));
  const xlo = Math.min(0, ...(fin.length ? fin : [0])), xhi = Math.max(0, ...(fin.length ? fin : [0]));
  const xpad = Math.max(0.4, (xhi - xlo) * 0.14);
  const ticks = [-5000, -1000, -200, 0, 200, 1000, 5000];
  const fmtCr = (t) => { if (t === 0) return "0"; const a = Math.abs(t); return (t < 0 ? "−" : "+") + (a >= 1000 ? (a / 1000) + "k" : a); };
  const x0 = xlo - xpad, x1 = xhi + xpad;
  const ann = (x, y, txt, ax) => ({ x, y, xref: "x", yref: "y", text: txt, showarrow: false, font: { size: 10, color: "#9aa6b2" }, xanchor: ax, yanchor: (y > 50 ? "top" : "bottom") });
  return baseLayout(Object.assign({
    hovermode: "closest",
    xaxis: { title: "←  net selling      net active flow (₹cr, signed-log)      net buying  →", tickvals: ticks.map(rotSlog), ticktext: ticks.map(fmtCr), range: [x0, x1], gridcolor: "#eef1f4", zeroline: false },
    yaxis: { title: "LSEG StarMine ARM (analyst, 0–100; ≥50 recommending)", range: [-6, 106], gridcolor: "#eef1f4", zeroline: false },
    shapes: [
      { type: "rect", x0: 0, x1: x1, y0: 50, y1: 106, fillcolor: "rgba(44,160,44,0.05)", line: { width: 0 }, layer: "below" },
      { type: "rect", x0: x0, x1: 0, y0: 50, y1: 106, fillcolor: "rgba(31,119,180,0.05)", line: { width: 0 }, layer: "below" },
      { type: "rect", x0: 0, x1: x1, y0: -6, y1: 50, fillcolor: "rgba(148,103,189,0.05)", line: { width: 0 }, layer: "below" },
      { type: "rect", x0: x0, x1: 0, y0: -6, y1: 50, fillcolor: "rgba(138,143,152,0.06)", line: { width: 0 }, layer: "below" },
      { type: "line", x0: 0, x1: 0, y0: -6, y1: 106, line: { color: "#9aa6b2", width: 1.2, dash: "dot" } },
      { type: "line", x0: x0, x1: x1, y0: 50, y1: 50, line: { color: "#9aa6b2", width: 1.2, dash: "dot" } },
    ],
    annotations: [
      ann(x1, 104, "Q1 · rec & buying", "right"), ann(x0, 104, "Q2 · rec, not buying", "left"),
      ann(x1, -4, "Q3 · not rec, buying", "right"), ann(x0, -4, "Q4 · not rec, selling", "left"),
    ],
    margin: { l: 58, r: 18, t: 10, b: 70 },
  }, extra || {}));
}

function screenMarker() { return (typeof window !== "undefined" && window.VISTAS_SCREEN_SVS) || null; }
function screenData() {   // full object with rows: the lazy-fetched cache, else an inlined full object (small decks)
  if (SCREEN_DATA && SCREEN_DATA.rows) return SCREEN_DATA;
  const m = screenMarker();
  return (m && m.rows && m.rows.length) ? m : null;
}
async function ensureScreen() {   // fetch the full screen JSON once (rows live in data/_screens/, not inlined)
  if (SCREEN_DATA && SCREEN_DATA.rows) return SCREEN_DATA;
  const m = screenMarker();
  if (m && m.rows && m.rows.length) { SCREEN_DATA = m; return SCREEN_DATA; }   // small deck inlined it
  try {
    const base = (typeof LAZY !== "undefined" && LAZY && LAZY.base) || "data/";
    const d = await fetchJSON(base + "_screens/smart_vs_street.json");
    if (d && d.rows) SCREEN_DATA = d;
  } catch (e) { console.error("ensureScreen:", e); }
  return SCREEN_DATA;
}

function initScreen() {
  const tab = $("tabs") && $("tabs").querySelector('[data-view="screen"]');
  const m = screenMarker();                                   // marker (lazy stub) OR a full inline
  const has = !!(m && (m.lazy || (m.rows && m.rows.length)));
  if (tab) { tab.disabled = !has; tab.style.opacity = has ? "" : ".45"; tab.title = has ? "" : "No screen data in this deck yet"; }
}

function screenNum(x) { return (x === null || x === undefined || isNaN(x)) ? -Infinity : x; }

function screenAmcList(rows) {
  const s = new Set();
  rows.forEach((r) => (r.amcs || []).forEach((a) => s.add(a)));
  return Array.from(s).sort();
}

function screenSortRows(rows) {
  const k = SCREEN_SORT.key, dir = SCREEN_SORT.dir;
  const val = (r) => {
    if (k === "name") return (r.name || r.symbol || "").toLowerCase();
    if (k === "sector") return (r.sector || "").toLowerCase();
    if (k === "deterioration") return (r.deterioration || "");
    if (k === "arm_asof") return (r.arm_asof || "");
    if (k === "flow") return screenNum(r["flow_" + SCREEN_WIN]);
    if (k === "quadrant") return screenNum(r["quadrant_" + SCREEN_WIN]);
    if (k === "namc") return (r.amcs || []).length;
    return screenNum(r[k]);
  };
  rows.sort((a, b) => { const x = val(a), y = val(b); if (x < y) return -dir; if (x > y) return dir; return 0; });
  return rows;
}

function screenPctCell(x, frac) {           // ret_6m/dd_52w arrive already in %, eps/ebitda as fractions (frac=true)
  let v = (x === null || x === undefined || isNaN(x)) ? null : (frac ? x * 100 : x);
  if (v === null) return '<td class="num">—</td>';
  return `<td class="num ${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "+" : ""}${v.toFixed(1)}%</td>`;
}
function screenFlowCell(v) {
  if (v === null || v === undefined || isNaN(v)) return '<td class="num">—</td>';
  return `<td class="num ${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "+" : "−"}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>`;
}

// the numeric value of a column IN THE USER'S DISPLAYED UNITS (so a ">5" filter on EPS YoY means >5%)
function screenColVal(r, k) {
  if (k === "eps_yoy" || k === "ebitda_yoy") return (r[k] == null ? null : r[k] * 100);
  if (k === "namc") return (r.amcs || []).length;
  if (k === "quadrant") return r["quadrant_" + SCREEN_WIN];
  return r[k];
}
// Excel-like filter expression: ">5" "<0" ">=10" "<=50" "=3" "0" (>=0 default) or a range "10-50" / "a~b" / "a,b"
function screenColMatch(v, expr) {
  expr = String(expr == null ? "" : expr).trim().replace(/\s|%|,(?=\d)/g, (m) => (m === "," ? "," : ""));
  if (!expr) return true;
  if (v === null || v === undefined || isNaN(v)) return false;   // an active filter excludes blanks
  let m = expr.match(/^(>=|<=|>|<|=)(-?\d+\.?\d*)$/);
  if (m) { const x = parseFloat(m[2]); return { ">": v > x, "<": v < x, ">=": v >= x, "<=": v <= x, "=": v === x }[m[1]]; }
  m = expr.match(/^(-?\d+\.?\d*)[~,](-?\d+\.?\d*)$/) || expr.match(/^(\d+\.?\d*)-(\d+\.?\d*)$/);  // range
  if (m) { const a = parseFloat(m[1]), b = parseFloat(m[2]); return v >= Math.min(a, b) && v <= Math.max(a, b); }
  m = expr.match(/^(-?\d+\.?\d*)$/);
  if (m) return v >= parseFloat(m[1]);     // bare number = ">=" (Excel-ish convenience)
  return true;                              // unparseable -> ignore (don't drop rows on a typo)
}
const SCREEN_FILT_COLS = ["ret_6m", "dd_52w", "eps_yoy", "ebitda_yoy", "arm", "flow_1m", "flow_3m",
  "flow_6m", "flow_12m", "net_breadth", "quadrant", "own_promoter", "own_fii", "own_dii", "own_public", "mf_pct_mcap", "namc"];

// #106 — remap each row's FM-axis values to the selected flow basis (gross / price-adjusted / net-active),
// recomputing the buying flags, quadrant, and 3M-vs-1M agreement so filters/sort/table/scatter/trail all
// follow it. Returns NEW shallow copies — never mutates the shared row store (the Funds tab keeps the
// default basis). Falls back to the legacy values for an older deck whose rows carry no `fb` block.
function screenApplyBasis(rows) {
  const basis = SCREEN_FLOW_BASIS;
  const quad = (rec, buy) => (rec && buy) ? 1 : (rec && !buy) ? 2 : ((!rec) && buy) ? 3 : 4;
  return (rows || []).map((r) => {
    const s = r.fb && r.fb[basis];
    if (!s) return r;                                   // older deck w/o fb, or missing basis → leave legacy values
    const c = Object.assign({}, r);
    c.flow_1m = s[0]; c.flow_3m = s[1]; c.flow_6m = s[2]; c.flow_12m = s[3];
    c.buying_1m = s[0] > 0; c.buying_3m = s[1] > 0;
    c.quadrant_1m = quad(r.recommending, c.buying_1m);
    c.quadrant_3m = quad(r.recommending, c.buying_3m);
    c.flow_agreement = (c.buying_3m === c.buying_1m) ? "confirmed" : "inflecting";
    if (r.fb.breadth && r.fb.breadth[basis] != null) c.net_breadth = r.fb.breadth[basis];   // Breadth follows the basis too
    if (Array.isArray(r.traj)) {                        // make the rotation trail follow the basis too
      c.traj = r.traj.map((p) => {
        const raw = (basis === "gross") ? p.g : (basis === "net_active") ? p.n : p.flow;
        const fv = (raw == null) ? p.flow : raw;        // graceful fallback to price-adj on a missing month
        const a = p.arm;
        const tq = (a != null && a >= 50 && fv > 0) ? 1 : (a != null && a >= 50) ? 2 : (fv > 0) ? 3 : 4;
        return Object.assign({}, p, { flow: fv, quad: tq });
      });
    }
    return c;
  });
}

// the filter funnel: base -> troubled? -> AMC magnitude -> column filters; returns rows + the coverage stages
function screenFilter(d) {
  const all = d.rows || [];
  const aum = d.amc_aum || {};
  let rows = all.slice();
  const funnel = [];
  if (SCREEN_TROUBLED) { rows = rows.filter((r) => r.troubled); funnel.push({ label: "troubled", n: rows.length }); }
  if (SCREEN_AMC) {
    rows = rows.filter((r) => r.amc_cr && r.amc_cr[SCREEN_AMC] != null);
    funnel.push({ label: `held by ${SCREEN_AMC}`, n: rows.length });
    if (SCREEN_RUPEE_MIN > 0) { rows = rows.filter((r) => r.amc_cr[SCREEN_AMC] >= SCREEN_RUPEE_MIN); funnel.push({ label: `> ₹${SCREEN_RUPEE_MIN}cr held`, n: rows.length }); }
    if (SCREEN_PCTAUM_MIN != null) { const a = aum[SCREEN_AMC] || 0; rows = rows.filter((r) => a > 0 && (r.amc_cr[SCREEN_AMC] / a * 100) >= SCREEN_PCTAUM_MIN); funnel.push({ label: `> ${SCREEN_PCTAUM_MIN}% of AMC AUM`, n: rows.length }); }
  } else if (SCREEN_RUPEE_MIN > 0) {
    rows = rows.filter((r) => (r.mf_cr || 0) >= SCREEN_RUPEE_MIN); funnel.push({ label: `MF > ₹${SCREEN_RUPEE_MIN}cr`, n: rows.length });
  }
  const colf = Object.entries(SCREEN_COLF).filter(([, v]) => v && String(v).trim());
  if (colf.length) { rows = rows.filter((r) => colf.every(([k, e]) => screenColMatch(screenColVal(r, k), e))); funnel.push({ label: "column filters", n: rows.length }); }
  return { rows, funnel, base: all.length };
}

function screenTableHTML(rows) {
  const win = SCREEN_WIN;                 // drives the Quad column; all 4 flow windows are shown explicitly
  const th = (k, lbl, isNum) => {
    const cls = [isNum ? "num" : "", SCREEN_SORT.key === k ? "sorted " + (SCREEN_SORT.dir < 0 ? "dn" : "up") : ""].filter(Boolean).join(" ");
    return `<th data-sort="${k}"${cls ? ` class="${cls}"` : ""}>${lbl}</th>`;
  };
  const detTag = (r) => {
    const d = SCREEN_DET[r.deterioration] || { c: "#8a8f98", t: r.deterioration || "—" };
    return `<span class="det-tag" style="color:${d.c}">${fEsc(d.t)}</span>${r.oneoff_flag ? ' <span class="oneoff" title="|EPS YoY| > 80% — likely a one-off / corp-action artefact, not a clean trend">⚠</span>' : ""}`;
  };
  const qChip = (q) => `<td class="num"><span class="qbadge" style="background:${SCREEN_QCOL[q] || "#8a8f98"}" title="${fEsc((screenData().quadrant_labels || {})[q] || "")}">${q}</span></td>`;
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fmtDt = (s) => { if (!s) return "—"; const p = String(s).slice(0, 10).split("-"); return p.length < 3 ? s : `${p[2]}-${MON[(+p[1]) - 1] || p[1]}-${p[0].slice(2)}`; };
  const u = SCREEN_AMT ? " ₹cr" : " %";   // ownership/MF column unit, flipped by the %↔₹ toggle
  // ownership cell: % of mcap, or absolute ₹cr (= pct × mcap / 100) when the toggle is on
  const ownCell = (pct, mc) => {
    if (pct == null || isNaN(pct)) return '<td class="num">—</td>';
    if (SCREEN_AMT) { const a = (mc ? pct * mc / 100 : null); return a == null ? '<td class="num">—</td>' : `<td class="num">${Math.round(a).toLocaleString()}</td>`; }
    return `<td class="num">${pct.toFixed(1)}</td>`;
  };
  const mfCell = (r) => SCREEN_AMT
    ? (r.mf_cr == null ? '<td class="num">—</td>' : `<td class="num">${Math.round(r.mf_cr).toLocaleString()}</td>`)
    : (r.mf_pct_mcap == null ? '<td class="num">—</td>' : `<td class="num">${r.mf_pct_mcap.toFixed(1)}</td>`);
  const fcell = (k) => `<th class="filt"><input class="screen-cf" data-fk="${k}" value="${fEsc(SCREEN_COLF[k] || "")}" placeholder="" title="Excel-like: >5, <0, >=10, <=50, =3, or a range 10-50"></th>`;
  const fb = `<th class="filt"></th>`;
  const headRow = `<tr>${th("name", "Stock")}${th("sector", "Sector")}${th("ret_6m", "6M", true)}${th("dd_52w", "Off hi", true)}`
    + `${th("deterioration", "Earnings")}${th("eps_yoy", "EPS YoY", true)}${th("ebitda_yoy", "EBITDA YoY", true)}${th("arm", "ARM", true)}${th("arm_asof", "ARM as-of")}`
    + `${th("flow_1m", "Flow 1M", true)}${th("flow_3m", "Flow 3M", true)}${th("flow_6m", "Flow 6M", true)}${th("flow_12m", "Flow 12M", true)}`
    + `${th("net_breadth", "Breadth", true)}<th>Agree</th>${th("quadrant", "Quad", true)}`
    + `${th("own_promoter", "Prom" + u, true)}${th("own_fii", "FII" + u, true)}${th("own_dii", "DII" + u, true)}${th("own_public", "Public" + u, true)}${th("mf_pct_mcap", "MF" + u, true)}`
    + `${th("namc", "AMCs", true)}</tr>`;
  const filtRow = `<tr class="screen-filt">${fb}${fb}${fcell("ret_6m")}${fcell("dd_52w")}${fb}${fcell("eps_yoy")}${fcell("ebitda_yoy")}${fcell("arm")}${fb}`
    + `${fcell("flow_1m")}${fcell("flow_3m")}${fcell("flow_6m")}${fcell("flow_12m")}${fcell("net_breadth")}${fb}${fcell("quadrant")}`
    + `${fcell("own_promoter")}${fcell("own_fii")}${fcell("own_dii")}${fcell("own_public")}${fcell("mf_pct_mcap")}${fcell("namc")}</tr>`;
  const head = `<thead>${headRow}${filtRow}</thead>`;
  const body = rows.map((r) => `<tr>`
    + `<td class="lft"><b>${fEsc(r.name || r.symbol)}</b> <span class="tkr">${fEsc(r.symbol)}</span></td>`
    + `<td class="lft sec">${fEsc(r.sector || "—")}</td>`
    + screenPctCell(r.ret_6m, false) + screenPctCell(r.dd_52w, false)
    + `<td class="lft">${detTag(r)}</td>`
    + screenPctCell(r.eps_yoy, true) + screenPctCell(r.ebitda_yoy, true)
    + `<td class="num"${r.arm_stale ? ' title="ARM not revised in >90 days — stale, not counted as recommending"' : ""}>${r.arm != null ? (r.arm_stale ? `<span class="stale">${r.arm.toFixed(0)}⌛</span>` : r.arm.toFixed(0)) : "—"}${r.recommending ? ' <span class="rec">rec</span>' : ""}</td>`
    + `<td class="num ${r.arm_stale ? "stale" : ""}" title="date of the stock's latest ARM revision change-point">${fmtDt(r.arm_asof)}</td>`
    + screenFlowCell(r.flow_1m) + screenFlowCell(r.flow_3m) + screenFlowCell(r.flow_6m) + screenFlowCell(r.flow_12m)
    + `<td class="num">${r.net_breadth >= 0 ? "+" : ""}${r.net_breadth}</td>`
    + `<td class="lft"><span class="agree-${fEsc(r.flow_agreement)}">${fEsc(r.flow_agreement)}</span></td>`
    + qChip(r["quadrant_" + win])
    + ownCell(r.own_promoter, r.mcap_cr) + ownCell(r.own_fii, r.mcap_cr) + ownCell(r.own_dii, r.mcap_cr) + ownCell(r.own_public, r.mcap_cr) + mfCell(r)
    + `<td class="num" title="${fEsc((r.amcs || []).join(", "))}">${(r.amcs || []).length}</td>`
    + `</tr>`).join("");
  return `<div class="screen-tblwrap"><table class="gauge-tbl fs-lb screen-tbl">${head}<tbody>${body || '<tr><td class="lft">No stocks match this filter.</td></tr>'}</tbody></table></div>`;
}

function screenScatter(rows, win, qlab) {
  const el = document.getElementById("plot-screen"); if (!el) return;
  const fkey = "flow_" + win, qkey = "quadrant_" + win;
  const slog = (f) => { const v = +f || 0; return Math.sign(v) * Math.log10(1 + Math.abs(v)); };   // signed-log: keeps the x=0 boundary exact, compresses the huge ₹cr range
  const nAmc = (r) => (r.amcs || []).length;
  const maxAmc = Math.max(1, ...rows.map(nAmc));
  const sz = (r) => 8 + 16 * Math.sqrt(nAmc(r) / maxAmc);
  const detC = (r) => (SCREEN_DET[r.deterioration] || { c: "#8a8f98" }).c;
  const pctTxt = (x) => (x === null || x === undefined || isNaN(x)) ? "n/a" : ((x >= 0 ? "+" : "") + (x * 100).toFixed(0) + "%");
  const traces = [1, 2, 3, 4].map((q) => {
    const rs = rows.filter((r) => r[qkey] === q);
    return {
      type: "scatter", mode: "markers", name: `${qlab[q] || ("Q" + q)} (${rs.length})`, cliponaxis: false,
      x: rs.map((r) => slog(r[fkey])), y: rs.map((r) => r.arm),
      marker: { size: rs.map(sz), color: SCREEN_QCOL[q], opacity: 0.85, line: { width: 2, color: rs.map(detC) } },
      customdata: rs.map((r) => [r.name || r.symbol, r.sector || "", r.ret_6m, r.dd_52w, r.arm, r.flow_3m, r.flow_1m, r.net_breadth, r.deterioration, r.flow_agreement, pctTxt(r.eps_yoy), pctTxt(r.ebitda_yoy), nAmc(r), r.oneoff_flag ? "  ⚠ one-off" : ""]),
      hovertemplate: "<b>%{customdata[0]}</b> · %{customdata[1]}<br>ARM %{customdata[4]:.0f} · 6M %{customdata[2]:.1f}% · off-hi %{customdata[3]:.1f}%<br>Flow 3M ₹%{customdata[5]:,.0f}cr · 1M ₹%{customdata[6]:,.0f}cr · breadth %{customdata[7]}<br>EPS YoY %{customdata[10]} · EBITDA YoY %{customdata[11]} · %{customdata[8]}%{customdata[13]}<br>%{customdata[9]} · held by %{customdata[12]} AMCs<extra></extra>"
    };
  });
  const ticks = [-2000, -500, -100, 0, 100, 500, 2000, 8000];
  const fmtCr = (t) => { if (t === 0) return "0"; const a = Math.abs(t); return (t < 0 ? "−" : "+") + (a >= 1000 ? (a / 1000) + "k" : a); };
  // pad the axes so the largest corner bubbles aren't half-cut (KV: "corner bubbles coming half cut")
  const xsAll = rows.map((r) => slog(r[fkey])).filter((v) => isFinite(v));
  const xlo = Math.min(0, ...xsAll), xhi = Math.max(0, ...xsAll), xpad = Math.max(0.35, (xhi - xlo) * 0.12);
  const layout = baseLayout({
    hovermode: "closest",
    xaxis: { title: `←  net selling      ${SCREEN_BASIS_AXIS[SCREEN_FLOW_BASIS] || "net flow (₹cr, signed-log)"}      net buying  →`, tickvals: ticks.map(slog), ticktext: ticks.map(fmtCr), range: [xlo - xpad, xhi + xpad], gridcolor: "#eef1f4", zeroline: false },
    yaxis: { title: "LSEG StarMine ARM (analyst-revision score)", range: [-6, 106], gridcolor: "#eef1f4", zeroline: false },
    shapes: [
      { type: "line", x0: 0, x1: 0, yref: "paper", y0: 0, y1: 1, line: { color: "#aab0b8", width: 1, dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 50, y1: 50, line: { color: "#aab0b8", width: 1, dash: "dot" } }
    ],
    legend: { orientation: "h", y: -0.2, font: { size: 11 } },
    margin: { l: 58, r: 18, t: 10, b: 96 }
  });
  Plotly.react(el, traces, layout, PCONF);
}

async function renderScreen() {
  const host = $("screen-body"); if (!host) return;
  if (!screenData()) host.innerHTML = "<div class='empty-note'>Loading screen…</div>";
  const d = await ensureScreen();
  if (!d || !d.rows || !d.rows.length) { host.innerHTML = "<div class='empty-note'>No screen data in this deck.</div>"; return; }
  const win = SCREEN_WIN, qkey = "quadrant_" + win;
  const aum = d.amc_aum || {};
  const amcNames = Object.keys(aum).sort();
  if (SCREEN_AMC && !aum[SCREEN_AMC]) SCREEN_AMC = "";           // selection no longer valid → reset to All MF
  const dB = Object.assign({}, d, { rows: screenApplyBasis(d.rows) });   // #106 remap the FM axis to the chosen flow basis
  const ff = screenFilter(dB);                                  // base → troubled → AMC magnitude → column filters
  let rows = ff.rows;
  // Q3 relabel: drop the unproven "FM ahead of the street" lead-lag claim — on our data fund flow
  // does not predict forward returns, so this is a positioning disagreement to investigate, not a signal.
  if (d.quadrant_labels) d.quadrant_labels["3"] = "Analysts cautious · funds buying — a disagreement to investigate";
  const qlab = d.quadrant_labels || {};
  const qc = { 1: 0, 2: 0, 3: 0, 4: 0 };
  rows.forEach((r) => { qc[r[qkey]] = (qc[r[qkey]] || 0) + 1; });
  const nPlot = rows.filter((r) => r.arm != null).length;

  let html = `<section class="panel">`;
  html += `<h2><span class="tag-sec">SCREEN</span>${fEsc(d.title)} — ${fEsc(d.universe)}</h2>`;
  // coverage funnel (KV's ask): universe -> each filter stage -> plotted
  let cov = `<b>${ff.base.toLocaleString()}</b> ${SCREEN_AMC ? "stocks" : "MF-held stocks"}`;
  ff.funnel.forEach((s) => { cov += ` → <b>${s.n.toLocaleString()}</b> <span class="cov-l">(${fEsc(s.label)})</span>`; });
  if (!ff.funnel.length) cov += ` <span class="cov-l">(no filters)</span>`;
  cov += ` · <b>${nPlot}</b> plotted${rows.length - nPlot ? ` <span class="cov-l">(${rows.length - nPlot} lack ARM — table only)</span>` : ""}`;
  html += `<p class="screen-sub">${cov}. Holdings as of ${fEsc(d.holdings_asof)}.</p>`;

  // controls
  const amcOpts = ['<option value="">All MF</option>'].concat(amcNames.map((a) =>
    `<option value="${fEsc(a)}"${a === SCREEN_AMC ? " selected" : ""}>${fEsc(a)}</option>`)).join("");
  const crOpts = [[0, "any ₹"], [10, "≥ ₹10cr"], [100, "≥ ₹100cr"], [500, "≥ ₹500cr"], [1000, "≥ ₹1000cr"]]
    .map(([v, l]) => `<option value="${v}"${SCREEN_RUPEE_MIN === v ? " selected" : ""}>${l}</option>`).join("");
  html += `<div class="fb-controls screen-ctl">`
    + `<span class="seg" id="screen-win"><button data-win="3m" class="${win === "3m" ? "active" : ""}">3-month flow</button><button data-win="1m" class="${win === "1m" ? "active" : ""}">1-month flow</button></span>`
    + `<span class="seg" id="screen-basis" title="How fund buying is measured on the horizontal axis — Gross (raw ₹) → Price-adjusted (price stripped; the legacy axis) → Net-active (inflow-immune conviction, the truest read)"><button data-basis="gross" class="${SCREEN_FLOW_BASIS === "gross" ? "active" : ""}">Gross</button><button data-basis="price_adj" class="${SCREEN_FLOW_BASIS === "price_adj" ? "active" : ""}">Price-adj.</button><button data-basis="net_active" class="${SCREEN_FLOW_BASIS === "net_active" ? "active" : ""}">Net-active</button></span>`
    + `<span class="seg" id="screen-amt" title="ownership & MF columns: percent of market cap, or absolute ₹ crore"><button data-amt="pct" class="${!SCREEN_AMT ? "active" : ""}">%</button><button data-amt="cr" class="${SCREEN_AMT ? "active" : ""}">₹ cr</button></span>`
    + `<label>Holdings of <select id="screen-amc">${amcOpts}</select></label>`
    + `<label>Min holding <select id="screen-rupee">${crOpts}</select></label>`
    + `<label title="position as % of the selected AMC's total AUM (needs a specific AMC)">% of AMC AUM ≥ <input id="screen-pctaum" type="number" min="0" step="0.5" style="width:64px" value="${SCREEN_PCTAUM_MIN == null ? "" : SCREEN_PCTAUM_MIN}" placeholder="any"${SCREEN_AMC ? "" : " disabled"}></label>`
    + `<label class="chk"><input type="checkbox" id="screen-troubled"${SCREEN_TROUBLED ? " checked" : ""}> troubled only</label>`
    + `</div>`;
  html += `<div class="screen-chips">`;
  [1, 2, 3, 4].forEach((q) => { html += `<span class="screen-chip" style="border-left-color:${SCREEN_QCOL[q]}"><b style="color:${SCREEN_QCOL[q]}">${qc[q] || 0}</b> ${fEsc(qlab[q] || ("Q" + q))}</span>`; });
  html += `</div>`;
  html += `<div class="plot" id="plot-screen" style="height:460px"></div>`;
  html += `<div class="q-cap"><b>Fund-buying axis = ${fEsc(SCREEN_BASIS_LABEL[SCREEN_FLOW_BASIS])} flow</b> — ${fEsc(SCREEN_BASIS_DEF[SCREEN_FLOW_BASIS])}. Switch the basis with the Gross / Price-adj. / Net-active toggle above; <b>Net-active</b> is the truest conviction read (it removes both price drift and mechanically-deployed scheme inflows). Bubble size = number of AMCs holding the stock (ownership breadth). Border colour = deterioration type (<span style="color:#d62728">operating</span> / <span style="color:#ff7f0e">headline-only</span> / <span style="color:#e0a000">mixed</span>). Horizontal split at ARM 50 (analysts recommending); vertical split at zero flow (FMs net buying). Stocks without ARM appear in the table only.</div>`;
  html += `</section>`;

  html += `<section class="panel"><h2><span class="tag-sec">DETAIL</span>Stocks <span class="screen-cnt">(${rows.length})</span> · type Excel-style filters under any column (e.g. <code>&gt;5</code>, <code>&lt;0</code>, <code>10-50</code>)</h2>`;
  html += screenTableHTML(screenSortRows(rows.slice()));
  html += `</section>`;

  html += `<section class="panel"><details><summary>Definition · Method · Why</summary><div class="screen-meth">`
    + `<p><b>What this is.</b> Every stock the mutual-fund industry holds (the names with a money-flow axis), plotted by where the two professional crowds stand: sell-side analysts (ARM) on the vertical, buy-side fund managers (net active flow) on the horizontal. <b>No pre-filter</b> — slice the universe with the <b>AMC-holding</b> filters (pick an AMC, set a minimum ₹cr held and/or a minimum % of that AMC's AUM) and the <b>Excel-style per-column filters</b> (e.g. 6M &lt; 0, ARM &gt; 60). "Troubled only" is the optional old preset (price correction + deteriorating earnings).</p>`
    + `<p><b>Analyst axis (vertical).</b> LSEG StarMine <b>ARM</b> (0–100; ≥50 = revisions turning up = "recommending"). Each stock shows its <b>ARM as-of date</b> (latest revision change-point); a score not revised in &gt;90 days is flagged <b>stale (⌛)</b> and is NOT counted as recommending. <b>FM axis (horizontal) — selectable flow basis (#106).</b> The fund-buying axis decomposes the raw rupee change in MF holdings into three figures you can switch between: <b>Gross</b> = the raw ₹ change (price move + scheme inflows + active conviction, undecomposed); <b>Price-adjusted</b> = Σ over funds of (end − start × (1 + index TR)) — strips price/split drift only, but still carries new scheme money the fund had to deploy pro-rata (this was the legacy axis); <b>Net-active</b> = the weight-space conviction flow, also inflow-immune (the rupees managers actively re-weighted toward the stock, independent of price AND fresh inflows) — the truest read of conviction. 3M default (persistence = conviction); 1M + breadth + a 3M-vs-1M agreement flag alongside. The quadrant, chip counts and rotation trail all recompute on the chosen basis.</p>`
    + `<p><b>AMC filters.</b> Pick an AMC → the funnel shows how many of its holdings survive each threshold (e.g. "395 held → 250 ≥₹10cr → 200 ≥2% of AUM"). "% of AMC AUM" = the position ₹cr ÷ that AMC's total disclosed AUM. MF holdings/AUM are reconstructed from the monthly disclosed portfolios. <b>Ownership columns</b>: Prom/FII/DII/Public (latest quarterly %); Public = retail+HNI+corporates (true retail not separately disclosed); MF = MF industry's % of market cap (a subset of DII). The <b>% ↔ ₹cr</b> toggle flips ownership/MF between percent and rupees.</p>`
    + `<p><b>Deterioration tag.</b> operating = EPS&amp;EBITDA both down; headline-only = EPS down, EBITDA up (one-off/below-the-line); |EPS YoY|&gt;80% ⚠ likely a corp-action artefact.</p>`
    + `<p>We make <b>NO claim that fund managers lead analysts</b> — on our data fund flow does not predict forward returns; each quadrant is a positioning disagreement to investigate, not a signal. The analyst (ARM) axis is forward-validated on our panel; the fund-flow axis is a diagnostic only. <b>Diagnostics only — not buy/sell advice.</b></p>`
    + `</div></details></section>`;

  // ---- ROTATION sub-view (A3): how positions move through the ARM × flow quadrant over time ----
  html += rotationSectionHTML(rows);

  host.innerHTML = html;

  // wire controls
  const seg = $("screen-win"); if (seg) seg.querySelectorAll("button").forEach((b) => { b.onclick = () => { SCREEN_WIN = b.dataset.win; renderScreen(); }; });
  const bseg = $("screen-basis"); if (bseg) bseg.querySelectorAll("button").forEach((b) => { b.onclick = () => { SCREEN_FLOW_BASIS = b.dataset.basis; renderScreen(); }; });
  const amtSeg = $("screen-amt"); if (amtSeg) amtSeg.querySelectorAll("button").forEach((b) => { b.onclick = () => { SCREEN_AMT = (b.dataset.amt === "cr"); renderScreen(); }; });
  const amc = $("screen-amc"); if (amc) amc.onchange = () => { SCREEN_AMC = amc.value; if (!SCREEN_AMC) SCREEN_PCTAUM_MIN = null; renderScreen(); };
  const rup = $("screen-rupee"); if (rup) rup.onchange = () => { SCREEN_RUPEE_MIN = +rup.value || 0; renderScreen(); };
  const pa = $("screen-pctaum"); if (pa) pa.onchange = () => { const v = parseFloat(pa.value); SCREEN_PCTAUM_MIN = (isFinite(v) && v > 0) ? v : null; renderScreen(); };
  const tb = $("screen-troubled"); if (tb) tb.onchange = () => { SCREEN_TROUBLED = tb.checked; renderScreen(); };
  host.querySelectorAll(".screen-cf").forEach((inp) => { inp.onchange = () => {
    const k = inp.dataset.fk, v = inp.value.trim();
    if (v) SCREEN_COLF[k] = v; else delete SCREEN_COLF[k];
    renderScreen();
  }; });
  host.querySelectorAll("th[data-sort]").forEach((th) => { th.onclick = () => {
    const k = th.dataset.sort;
    if (SCREEN_SORT.key === k) SCREEN_SORT.dir *= -1; else { SCREEN_SORT.key = k; SCREEN_SORT.dir = (k === "name" || k === "sector" || k === "deterioration" || k === "arm_asof") ? 1 : -1; }
    renderScreen();
  }; });

  try { screenScatter(rows.filter((r) => r.arm != null), win, qlab); } catch (e) { console.error("screen scatter:", e); }
  try { wireRotation(rows); } catch (e) { console.error("wireRotation:", e); }
}

// ============================== QUADRANT ROTATION (A3) — stock trail + portfolio centroids =============
// Two surfaces, both on the ARM (Y, analyst) × net-active-flow (X, FM conviction) quadrant:
//   1) STOCK TRAIL  — the selected stock's own (arm,flow) path over <=36 months, from row.traj[].
//   2) CENTROIDS    — a fund/AMC/category's holding-weighted centroid trail (lazy-fetched centroids.json),
//                     with peer trails faint in the background + own-history percentile readout.
// All guarded; markers fade oldest→newest; latest = solid/largest. No Plotly key ever set to undefined.

function rotationSectionHTML(rows) {
  const withTraj = (rows || []).filter((r) => Array.isArray(r.traj) && r.traj.length);
  let h = `<section class="panel rot-wrap"><h2><span class="tag-sec">ROTATION</span>Quadrant rotation — how positioning moves over time</h2>`;
  h += `<details><summary>Definition · Method · Why</summary>`
    + `<p><b>Axes:</b> Y = LSEG StarMine <b>ARM</b> (analyst-revision score, 0–100; ≥50 = "recommending"). X = <b>net active flow</b> (fund-manager conviction, ₹cr, signed-log; &gt;0 = net buying). The four quadrants: <b>Q1</b> rec &amp; buying (top-right), <b>Q2</b> rec, not buying (top-left), <b>Q3</b> not rec, buying (bottom-right), <b>Q4</b> not rec, selling (bottom-left).</p>`
    + `<p><b>Stock trail</b> = the chosen stock's (ARM, flow) plotted month by month (markers fade oldest→newest; the newest is solid/largest). <b>Centroid</b> = a fund/AMC/category's <i>holding-weighted</i> mean (ARM, flow) of its equity book each month; <b>own-percentile</b> = where this month's value ranks within that entity's own trail (is it unusually constructive/aggressive <i>for itself</i> right now). Peers sharing the same peer-group are drawn faint behind it.</p>`
    + `<p><b>Why:</b> a static dot says where you stand; the trail says which way you're <i>rotating</i> — into or out of the street's favour, and into or out of fund-manager conviction. Coincident positioning, not a forward signal.</p></details>`;

  // sub-tab toggle
  h += `<div class="rot-ctlrow"><span class="ab-ctllbl">View</span>`
    + `<span class="rot-subseg fs-lvl-seg" id="rot-subseg">`
    + `<button type="button" class="fs-lvl${ROT_VIEW === "stock" ? " on" : ""}" data-rv="stock">Stock trail</button>`
    + `<button type="button" class="fs-lvl${ROT_VIEW === "centroid" ? " on" : ""}" data-rv="centroid">Portfolio centroids</button>`
    + `</span></div>`;

  // STOCK panel
  h += `<div id="rot-stock-pane"${ROT_VIEW === "stock" ? "" : " hidden"}>`;
  if (!withTraj.length) {
    h += `<div class="empty-note">No per-stock trajectory in this deck (the screen rows carry no <code>traj</code> yet).</div>`;
  } else {
    if (!ROT_STOCK || !withTraj.some((r) => (r.symbol || r.name) === ROT_STOCK)) ROT_STOCK = withTraj[0].symbol || withTraj[0].name;
    const opts = withTraj.map((r) => { const id = r.symbol || r.name; return `<option value="${attEsc(id)}"${id === ROT_STOCK ? " selected" : ""}>${fEsc(r.name || r.symbol)} ${fEsc(r.symbol || "")}</option>`; }).join("");
    h += `<div class="rot-ctlrow"><span class="ab-ctllbl">Stock</span><select id="rot-stock-sel" class="rot-sel">${opts}</select>`
      + `<button type="button" class="rot-play" id="rot-stock-play">▶ play</button>`
      + `<input type="range" id="rot-stock-slider" class="rot-slider" min="0" max="0" value="0" step="1">`
      + `<span class="rot-monthlbl" id="rot-stock-mon">full</span></div>`;
    h += `<div id="rot-stock-warn"></div>`;
    h += `<div class="plot" id="plot-rot-stock" style="height:440px"></div>`;
  }
  h += `</div>`;

  // CENTROID panel (lazy)
  h += `<div id="rot-cent-pane"${ROT_VIEW === "centroid" ? "" : " hidden"}>`;
  h += `<div id="rot-cent-host"><div class="empty-note">Loading portfolio centroids…</div></div>`;
  h += `</div>`;

  h += `</section>`;
  return h;
}

function wireRotation(rows) {
  const seg = $("rot-subseg");
  if (seg) seg.querySelectorAll(".fs-lvl").forEach((b) => b.addEventListener("click", () => {
    ROT_VIEW = b.dataset.rv;
    seg.querySelectorAll(".fs-lvl").forEach((x) => x.classList.toggle("on", x.dataset.rv === ROT_VIEW));
    const sp = $("rot-stock-pane"), cp = $("rot-cent-pane");
    if (sp) sp.hidden = (ROT_VIEW !== "stock");
    if (cp) cp.hidden = (ROT_VIEW !== "centroid");
    if (ROT_VIEW === "centroid") ensureRotCentroids().then(renderRotCentroid);
    else afterPaint(() => viewPlotsResize("screen"));
  }));

  // stock-trail controls
  const ssel = $("rot-stock-sel");
  if (ssel) {
    ssel.addEventListener("change", () => { ROT_STOCK = ssel.value; ROT_STOCK_MON = null; syncStockSlider(rows); drawRotStock(rows); });
    syncStockSlider(rows);
    drawRotStock(rows);
    const sl = $("rot-stock-slider");
    if (sl) sl.addEventListener("input", () => { const v = +sl.value; ROT_STOCK_MON = (v >= +sl.max) ? null : v; drawRotStock(rows); });
    const pb = $("rot-stock-play"); if (pb) pb.addEventListener("click", () => playRotStock(rows));
  }

  // centroid view: if it's the active sub-tab on (re)render, kick the lazy fetch
  if (ROT_VIEW === "centroid") ensureRotCentroids().then(renderRotCentroid);
}

function rotStockRow(rows) { return (rows || []).find((r) => (r.symbol || r.name) === ROT_STOCK) || null; }
function syncStockSlider(rows) {
  const r = rotStockRow(rows); const sl = $("rot-stock-slider"); const lbl = $("rot-stock-mon");
  if (!r || !sl) return;
  const n = (r.traj || []).length;
  sl.max = String(Math.max(0, n - 1)); sl.value = sl.max; ROT_STOCK_MON = null;
  if (lbl) lbl.textContent = "full";
}

// fade oldest→newest: opacities ramp 0.18→1; sizes 6→16; latest marker is solid + ringed.
function rotFadeMarkers(pts, colorByQuad) {
  const n = pts.length;
  const op = pts.map((_, i) => 0.18 + 0.82 * (n <= 1 ? 1 : i / (n - 1)));
  const sz = pts.map((_, i) => 6 + 10 * (n <= 1 ? 1 : i / (n - 1)));
  const col = pts.map((p) => colorByQuad ? (ROT_QCOL[p.quad] || "#8a8f98") : "#1f77b4");
  // last marker bigger + ringed
  if (n) { sz[n - 1] = 18; op[n - 1] = 1; }
  return { op, sz, col };
}

function drawRotStock(rows) {
  const el = $("plot-rot-stock"); if (!el) return;
  try {
    const r = rotStockRow(rows);
    const warn = $("rot-stock-warn"); if (warn) warn.innerHTML = "";
    if (!r || !Array.isArray(r.traj) || !r.traj.length) { Plotly.purge("plot-rot-stock"); el.innerHTML = `<div class="empty-note">No trajectory for this stock.</div>`; return; }
    Plotly.purge("plot-rot-stock");   // clear prior plot cleanly (innerHTML="" leaves stale Plotly state → blank on stock/slider change)
    const mode = r.arm_history_mode || "ffill";
    if (warn && mode !== "ffill") warn.innerHTML = `<div class="rot-warn">ARM = last-known (no history); only flow moves on the chart.</div>`;
    let pts = r.traj.slice();
    if (ROT_STOCK_MON != null) pts = pts.slice(0, Math.min(pts.length, ROT_STOCK_MON + 1));
    if (!pts.length) pts = r.traj.slice(0, 1);
    const xs = pts.map((p) => rotSlog(p.flow));
    const ys = pts.map((p) => (p.arm == null ? null : p.arm));
    const { op, sz, col } = rotFadeMarkers(pts, true);
    // a single path line + per-point markers (color by quadrant). Build with a marker.color array.
    const pathTr = {
      type: "scatter", mode: "lines+markers", name: r.name || r.symbol,
      x: xs, y: ys, connectgaps: true,
      line: { color: "#9aa6b2", width: 1.4 },
      marker: { size: sz, color: col, opacity: op, line: { width: 1, color: "#ffffff" } },
      customdata: pts.map((p) => [p.date, (p.arm == null ? "n/a" : p.arm), p.flow, (ROT_QLBL[p.quad] || "")]),
      hovertemplate: "<b>%{customdata[0]}</b><br>ARM %{customdata[1]}<br>flow ₹%{customdata[2]:,.0f} cr<br>%{customdata[3]}<extra></extra>",
    };
    const layout = rotQuadLayout(xs, { showlegend: false });
    Plotly.react("plot-rot-stock", [pathTr], layout, PCONF);
  } catch (e) { console.error("drawRotStock:", e); Plotly.purge("plot-rot-stock"); el.innerHTML = `<div class="empty-note">Stock trail unavailable.</div>`; }
}

let _rotPlayTimer = null;
function playRotStock(rows) {
  const r = rotStockRow(rows); const sl = $("rot-stock-slider"); const lbl = $("rot-stock-mon");
  if (!r || !sl || !Array.isArray(r.traj) || !r.traj.length) return;
  if (_rotPlayTimer) { clearInterval(_rotPlayTimer); _rotPlayTimer = null; }
  let i = 0; const n = r.traj.length;
  _rotPlayTimer = setInterval(() => {
    ROT_STOCK_MON = i; sl.value = String(i);
    if (lbl) lbl.textContent = (r.traj[i] && r.traj[i].date) || String(i);
    drawRotStock(rows);
    i++;
    if (i >= n) { clearInterval(_rotPlayTimer); _rotPlayTimer = null; ROT_STOCK_MON = null; sl.value = sl.max; if (lbl) lbl.textContent = "full"; drawRotStock(rows); }
  }, 420);
}

// lazy-fetch the 6.35MB centroids.json at most once (mirrors ensureScreen's lazy pattern)
async function ensureRotCentroids() {
  if (ROT_CENTROIDS) return ROT_CENTROIDS;
  // an inlined small build may bake it
  if (typeof window !== "undefined" && window.VISTAS_ROTATION && window.VISTAS_ROTATION.entities) { ROT_CENTROIDS = window.VISTAS_ROTATION; return ROT_CENTROIDS; }
  if (ROT_CENT_FETCHED) return ROT_CENTROIDS;
  ROT_CENT_FETCHED = true;
  try {
    const base = (typeof LAZY !== "undefined" && LAZY && LAZY.base) || "data/";
    const d = await fetchJSON(base + "_rotation/centroids.json");
    if (d && d.entities) ROT_CENTROIDS = d;
  } catch (e) { console.error("ensureRotCentroids:", e); }
  return ROT_CENTROIDS;
}

function renderRotCentroid() {
  const host = $("rot-cent-host"); if (!host) return;
  try {
    const C = ROT_CENTROIDS;
    if (!C || !Array.isArray(C.entities) || !C.entities.length) {
      host.innerHTML = `<div class="empty-note">Portfolio-centroid data isn’t available in this deck (data/_rotation/centroids.json not found).</div>`;
      return;
    }
    const ents = C.entities;
    const types = Array.from(new Set(ents.map((e) => e.entity_type))).filter(Boolean);
    if (!ROT_ENTITY || !ents.some((e) => e.entity_id === ROT_ENTITY)) ROT_ENTITY = (ents[0] || {}).entity_id;
    // build the scaffold once
    if (!host.dataset.built) {
      const typeOpts = types.map((t) => `<option value="${attEsc(t)}">${fEsc(t)}</option>`).join("");
      host.innerHTML =
        `<div class="rot-ctlrow">
           <span class="ab-ctllbl">Type</span><select id="rot-cent-type" class="rot-sel" style="min-width:130px">${typeOpts}</select>
           <span class="ab-ctllbl">Entity</span><select id="rot-cent-ent" class="rot-sel"></select>
           <button type="button" class="rot-play" id="rot-cent-play">▶ play</button>
           <input type="range" id="rot-cent-slider" class="rot-slider" min="0" max="0" value="0" step="1">
           <span class="rot-monthlbl" id="rot-cent-mon">full</span>
         </div>
         <div class="rot-pctile" id="rot-cent-pctile"></div>
         <div id="rot-cent-warn"></div>
         <div class="plot" id="plot-rot-cent" style="height:460px"></div>
         <div class="rot-note">Faint trails = peers in the same peer-group (relative-to-peers view). Markers fade oldest→newest; latest = solid/largest. Months where the equity book is &lt;50% covered are dimmed.</div>`;
      host.dataset.built = "1";
      const tsel = $("rot-cent-type");
      if (tsel) tsel.addEventListener("change", () => { fillRotEntitySel(tsel.value); });
      // entity select + slider + play wired after fill
    }
    // (re)fill type→entity selects
    const tsel = $("rot-cent-type");
    const curType = (ents.find((e) => e.entity_id === ROT_ENTITY) || {}).entity_type || types[0];
    if (tsel && tsel.value !== curType) tsel.value = curType;
    fillRotEntitySel(curType);
  } catch (e) { console.error("renderRotCentroid:", e); host.innerHTML = `<div class="empty-note">Portfolio centroids unavailable.</div>`; }
}

function rotEntsOfType(t) { return (ROT_CENTROIDS.entities || []).filter((e) => e.entity_type === t); }
function fillRotEntitySel(type) {
  const esel = $("rot-cent-ent"); if (!esel) return;
  const list = rotEntsOfType(type);
  if (!list.some((e) => e.entity_id === ROT_ENTITY)) { ROT_ENTITY = (list[0] || {}).entity_id; ROT_CENT_MON = null; }
  esel.innerHTML = list.map((e) => `<option value="${attEsc(e.entity_id)}"${e.entity_id === ROT_ENTITY ? " selected" : ""}>${fEsc(e.name || e.entity_id)}</option>`).join("");
  if (!esel.dataset.wired) {
    esel.addEventListener("change", () => { ROT_ENTITY = esel.value; ROT_CENT_MON = null; syncCentSlider(); drawRotCentroid(); });
    const sl = $("rot-cent-slider"); if (sl) sl.addEventListener("input", () => { const v = +sl.value; ROT_CENT_MON = (v >= +sl.max) ? null : v; drawRotCentroid(); });
    const pb = $("rot-cent-play"); if (pb) pb.addEventListener("click", playRotCentroid);
    esel.dataset.wired = "1";
  }
  syncCentSlider();
  drawRotCentroid();
}

function rotCurEntity() { return (ROT_CENTROIDS.entities || []).find((e) => e.entity_id === ROT_ENTITY) || null; }
function syncCentSlider() {
  const e = rotCurEntity(); const sl = $("rot-cent-slider"); const lbl = $("rot-cent-mon");
  if (!e || !sl) return;
  const n = (e.points || []).length;
  sl.max = String(Math.max(0, n - 1)); sl.value = sl.max; ROT_CENT_MON = null;
  if (lbl) lbl.textContent = "full";
}

function drawRotCentroid() {
  const el = $("plot-rot-cent"); if (!el) return;
  try {
    const e = rotCurEntity();
    if (!e || !Array.isArray(e.points) || !e.points.length) { Plotly.purge("plot-rot-cent"); el.innerHTML = `<div class="empty-note">No centroid trail for this entity.</div>`; return; }
    Plotly.purge("plot-rot-cent");   // clear prior plot cleanly (innerHTML="" leaves stale Plotly state → blank on entity/slider change)
    const traces = [];
    // peer background trails (same peer_group, excluding self) — faint, no markers
    const peers = (ROT_CENTROIDS.entities || []).filter((p) => p.entity_id !== e.entity_id && p.peer_group && p.peer_group === e.peer_group && p.entity_type === e.entity_type).slice(0, 12);
    peers.forEach((p) => {
      const pts = p.points || []; if (!pts.length) return;
      traces.push({ type: "scatter", mode: "lines", name: p.name || p.entity_id, x: pts.map((q) => rotSlog(q.flow)), y: pts.map((q) => (q.arm == null ? null : q.arm)), connectgaps: true, line: { color: "rgba(150,160,170,0.30)", width: 1 }, hoverinfo: "skip", showlegend: false });
    });
    // the focal entity's trail
    let pts = e.points.slice();
    if (ROT_CENT_MON != null) pts = pts.slice(0, Math.min(pts.length, ROT_CENT_MON + 1));
    if (!pts.length) pts = e.points.slice(0, 1);
    const xs = pts.map((p) => rotSlog(p.flow));
    const ys = pts.map((p) => (p.arm == null ? null : p.arm));
    const { op, sz, col } = rotFadeMarkers(pts, true);
    // dim months with low equity coverage (mark with a thin ring instead of solid)
    const ringCol = pts.map((p) => ((p.equity_wt_covered != null && p.equity_wt_covered < 0.5) ? "#b3402f" : "#ffffff"));
    const ringW = pts.map((p) => ((p.equity_wt_covered != null && p.equity_wt_covered < 0.5) ? 2 : 1));
    traces.push({
      type: "scatter", mode: "lines+markers", name: e.name || e.entity_id,
      x: xs, y: ys, connectgaps: true,
      line: { color: "#1f3a55", width: 1.8 },
      marker: { size: sz, color: col, opacity: op, line: { width: ringW, color: ringCol } },
      customdata: pts.map((p) => [p.date, (p.arm == null ? "n/a" : p.arm), p.flow, (ROT_QLBL[p.quad] || ""), (p.n_holdings == null ? "?" : p.n_holdings), (p.equity_wt_covered == null ? "?" : (p.equity_wt_covered * 100).toFixed(0) + "%")]),
      hovertemplate: "<b>%{customdata[0]}</b><br>ARM %{customdata[1]} · flow ₹%{customdata[2]:,.0f} cr<br>%{customdata[3]}<br>%{customdata[4]} holdings · equity covered %{customdata[5]}<extra></extra>",
      showlegend: false,
    });
    Plotly.react("plot-rot-cent", traces, rotQuadLayout(xs, { showlegend: false }), PCONF);

    // own-percentile readout (latest value vs the entity's own trail)
    const pc = $("rot-cent-pctile");
    if (pc) {
      const op2 = e.own_pctile || {};
      const lat = e.latest || (e.points[e.points.length - 1] || {});
      const fmtp = (v) => (v == null || isNaN(v)) ? "—" : Math.round(v) + "%ile";
      pc.innerHTML = `<span class="rp">As of <b>${fEsc(lat.date || "—")}</b></span>`
        + `<span class="rp">ARM now <b>${lat.arm == null ? "—" : Number(lat.arm).toFixed(0)}</b> · vs own history <b>${fmtp(op2.arm)}</b></span>`
        + `<span class="rp">Flow now <b>${lat.flow == null ? "—" : (lat.flow >= 0 ? "+" : "") + Math.round(lat.flow).toLocaleString("en-IN") + " cr"}</b> · vs own history <b>${fmtp(op2.flow)}</b></span>`
        + `<span class="rp" title="100 = most constructive/aggressive this entity has ever been for itself">${(op2.arm != null && op2.arm >= 70) ? "unusually constructive for itself" : (op2.arm != null && op2.arm <= 30) ? "unusually cautious for itself" : ""}</span>`;
    }
    const warn = $("rot-cent-warn");
    if (warn) { const low = (e.points || []).some((p) => p.equity_wt_covered != null && p.equity_wt_covered < 0.5); warn.innerHTML = low ? `<div class="rot-warn">Some months have &lt;50% of the equity book covered (red-ringed markers) — read those positions with care.</div>` : ""; }
  } catch (err) { console.error("drawRotCentroid:", err); Plotly.purge("plot-rot-cent"); el.innerHTML = `<div class="empty-note">Centroid trail unavailable.</div>`; }
}

let _rotCentTimer = null;
function playRotCentroid() {
  const e = rotCurEntity(); const sl = $("rot-cent-slider"); const lbl = $("rot-cent-mon");
  if (!e || !sl || !Array.isArray(e.points) || !e.points.length) return;
  if (_rotCentTimer) { clearInterval(_rotCentTimer); _rotCentTimer = null; }
  let i = 0; const n = e.points.length;
  _rotCentTimer = setInterval(() => {
    ROT_CENT_MON = i; sl.value = String(i);
    if (lbl) lbl.textContent = (e.points[i] && e.points[i].date) || String(i);
    drawRotCentroid();
    i++;
    if (i >= n) { clearInterval(_rotCentTimer); _rotCentTimer = null; ROT_CENT_MON = null; sl.value = sl.max; if (lbl) lbl.textContent = "full"; drawRotCentroid(); }
  }, 420);
}

// ============================== FUNDS tab — mutual-fund portfolio holdings (look-through) ==============================
async function renderFunds() {
  const host = $("funds-body"); if (!host) return;
  if (!FUNDS_SYM) { host.innerHTML = `<div class='empty-note'>Pick a scheme above to load its portfolio holdings.</div>`; return; }
  let f = (FUNDS_HOLD_DATA && FUNDS_HOLD_DATA[FUNDS_SYM]) || null;
  if (!f) { host.innerHTML = `<div class='empty-note'>Loading ${fEsc(FUNDS_SYM)}…</div>`; f = await ensureFundsHoldings(FUNDS_SYM); }
  if (!f) { host.innerHTML = `<div class='empty-note'>No holdings cached for ${fEsc(FUNDS_SYM)}.</div>`; return; }
  const cov = f.coverage || {}, con = f.concentration || {}, aa = f.asset_alloc || {}, sa = f.sector_alloc || {}, top = f.top_holdings || [], H = f.holdings || [];
  const num = (x, s) => (x === null || x === undefined) ? "—" : (x + (s || ""));

  let html = `<div class="quant-head"><div class="qh-name">${fEsc(f.name || FUNDS_SYM)}${f.amc ? ` <span class="qh-sym">${fEsc(f.amc)}</span>` : ""}</div>`;
  html += `<div class="qh-px"><span class="qh-asof">portfolio as on ${fEsc(f.asof || "—")}</span></div></div>`;

  // ---- Snapshot ----
  html += `<section class="panel"><h2><span class="tag-sec">PORTFOLIO</span>Snapshot</h2><div class="q-stats">`
    + qStat("Holdings", num(con.n_holdings))
    + qStat("Top-10 weight", num(con.top10_pct, "%"))
    + qStat("Herfindahl", num(con.herfindahl))
    + qStat("Σ % to NAV", num(cov.pct_sum, "%"))
    + qStat("ISIN resolved", num(cov.isin_resolved_pct, "%"))
    + qStat("Basis", cov.gross_exposure ? "gross (hedged)" : "net")
    + `</div>`;
  if (cov.gross_exposure) html += `<div class="q-warn">Weights sum above 100% — a hedged/arbitrage/multi-asset fund (long equity + cash collateral + derivative legs are all reported). This is gross exposure, not an error.</div>`;
  html += `<div class="q-note">Source: ${fEsc(f.source || "AMC monthly portfolio disclosure")}.</div></section>`;

  // ---- Asset & sector mix ----
  html += `<section class="panel"><h2><span class="tag-sec">ALLOCATION</span>Asset &amp; sector mix</h2><div class="qgrid2">`
    + `<div class="qcell"><div class="q-cap">Asset allocation (% of NAV)</div><div class="plot" id="plot-funds-asset" style="height:280px"></div></div>`
    + `<div class="qcell"><div class="q-cap">Equity sector allocation (% of NAV, top 15)</div><div class="plot" id="plot-funds-sector" style="height:280px"></div></div>`
    + `</div></section>`;

  // ---- vs Benchmark (populated async after render: dropdown + benchmark-relative active share/tilt) ----
  html += `<section class="panel" id="funds-bench-host" style="display:none"></section>`;

  // ---- Categorized portfolio — grouped by sector (equity) / asset class (debt & cash), with subtotals ----
  const fmtN = (x, d) => (x == null) ? "—" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: d == null ? 0 : d });
  const all = H.slice();
  const isEq = (h) => /equ/i.test(h.asset_class || "") || (!h.asset_class && h.industry && !/^(AAA|AA|A1|A\+|SOV|unrated)/i.test(h.industry || ""));
  const eqH = all.filter(isEq), neqH = all.filter((h) => !isEq(h));
  const grp = (rows, keyFn, fallback) => {
    const m = {}; rows.forEach((h) => { const k = keyFn(h) || fallback; (m[k] = m[k] || []).push(h); });
    return Object.keys(m).map((k) => ({ k, rows: m[k].sort((a, b) => (b.pct || 0) - (a.pct || 0)), wt: m[k].reduce((t, h) => t + (h.pct || 0), 0), n: m[k].length }))
      .sort((a, b) => b.wt - a.wt);
  };
  const secGroups = grp(eqH, (h) => h.industry, "Unclassified");
  const clsGroups = grp(neqH, (h) => h.asset_class, "Other");
  const anyMat = all.some((h) => h.maturity);
  const cr = (h) => (h.mktval_lakh != null) ? h.mktval_lakh / 100 : null;
  const wtCell = (p) => p == null ? "—" : (Math.round(p * 100) / 100);
  html += `<section class="panel"><h2><span class="tag-sec">HOLDINGS</span>Portfolio by sector — ${all.length} holdings, ${secGroups.length} equity sectors</h2>`;
  html += `<div style="overflow-x:auto"><table class="gauge-tbl fund-hold"><thead><tr><th>Sector / Holding</th><th>Ticker</th><th>Shares</th><th>Mkt val (₹ cr)</th><th>Wt %</th>${anyMat ? "<th>Maturity</th>" : ""}</tr></thead><tbody>`;
  const groupBlock = (label, g) => {
    let s = `<tr class="fh-sec"><td><b>${fEsc(label)}</b> <span class="fh-n">${g.n}</span></td><td></td><td></td><td></td><td class="num"><b>${wtCell(g.wt)}</b></td>${anyMat ? "<td></td>" : ""}</tr>`;
    g.rows.forEach((h) => {
      s += `<tr><td class="name" style="padding-left:20px">${fEsc(h.name)}</td><td>${fEsc(h.symbol || "—")}</td><td class="num">${fmtN(h.quantity, 0)}</td><td class="num">${fmtN(cr(h), 1)}</td><td class="num">${wtCell(h.pct)}</td>${anyMat ? `<td>${fEsc(h.maturity || "—")}</td>` : ""}</tr>`;
    });
    return s;
  };
  secGroups.forEach((g) => { html += groupBlock(g.k, g); });
  if (clsGroups.length) {
    html += `<tr class="fh-div"><td colspan="${anyMat ? 6 : 5}">Debt, cash &amp; other</td></tr>`;
    clsGroups.forEach((g) => { html += groupBlock(g.k, g); });
  }
  html += `</tbody></table></div>`;
  html += `<details><summary>Definition · Method · Why</summary><p><b>What:</b> the fund's whole book from the AMC's monthly SEBI portfolio disclosure, now <b>grouped by sector</b> (equity) and <b>by asset class</b> (debt / cash / derivatives), each with a weight subtotal — so you read the bets, not a 100-line dump.</p><p><b>Method:</b> parsed line-by-line from the AMC's monthly portfolio XLSX; % to NAV normalised to percent; market value ₹ lakh→₹ cr (÷100); ticker via the ISIN map; sector = the disclosed industry (equity) — sectors sorted by weight, holdings within a sector sorted by weight. Gross-exposure funds (arbitrage/multi-asset) can exceed 100% (long + collateral + derivative legs all listed).</p><p><b>Why:</b> the actual book is the cause behind the NAV — exposure, concentration and sector bets read straight off it. See the <b>Fund Skill</b> tab for the same fund's 13-year sector rotation and holdings-based manager skill.</p></details>`;
  html += `</section>`;

  host.innerHTML = html;

  // ---- charts AFTER innerHTML. Never set a Plotly trace key to undefined (omit it). ----
  try {
    const aKeys = Object.keys(aa);
    if (aKeys.length) {
      const labelMap = { equity: "Equity", debt: "Debt", money_market: "Money market", cash: "Cash & equiv.", mf_units: "MF/REIT units", derivative: "Derivatives", other: "Other" };
      const xs = aKeys.map((k) => labelMap[k] || k), ys = aKeys.map((k) => aa[k]);
      Plotly.react("plot-funds-asset", [{ type: "bar", x: xs, y: ys, marker: { color: "#1f77b4" }, hovertemplate: "%{x}: %{y}%<extra></extra>" }], baseLayout({ yaxis: { title: "% of NAV", gridcolor: "#dfe3e8" } }), PCONF);
      attachYAutoscale("plot-funds-asset");
    }
    const sKeys = Object.keys(sa);
    if (sKeys.length) {
      const sk = sKeys.slice().sort((a, b) => sa[a] - sa[b]).slice(-15);   // ascending so the biggest sits on top of a horizontal bar
      Plotly.react("plot-funds-sector", [{ type: "bar", orientation: "h", x: sk.map((k) => sa[k]), y: sk, marker: { color: "#8c564b" }, hovertemplate: "%{y}: %{x}%<extra></extra>" }], baseLayout({ xaxis: { title: "% of NAV", gridcolor: "#dfe3e8" }, margin: { l: 170, r: 12, t: 10, b: 36 } }), PCONF);
    } else { const el = $("plot-funds-sector"); if (el) el.innerHTML = "<div class='empty-note'>no equity sector data (a debt / liquid fund)</div>"; }
  } catch (e) { console.error("renderFunds charts:", e); }
  try { await renderFundsBench(f.holdings, f.category || f.name, "funds-bench-host"); } catch (e) { console.error("renderFundsBench:", e); }
}

async function initFunds() {
  const tab = $("tabs") && $("tabs").querySelector('[data-view="funds"]');
  const manifest = fundsHoldManifest();
  const keys = manifest ? Object.keys(manifest).sort((a, b) => String(manifest[a] || a).localeCompare(String(manifest[b] || b))) : (FUNDS_HOLD_DATA ? Object.keys(FUNDS_HOLD_DATA) : []);
  if (tab) { const any = keys.length > 0; tab.disabled = !any; tab.style.opacity = any ? "" : ".45"; tab.title = any ? "" : "No fund-holdings data in this deck yet"; }
  if (!keys.length) return;
  const nameOf = (k) => manifest ? (manifest[k] || k) : ((FUNDS_HOLD_DATA && FUNDS_HOLD_DATA[k] && FUNDS_HOLD_DATA[k].name) || k);
  const items = keys.map((k) => { const nm = nameOf(k); return { sym: k, name: nm, disp: nm }; });
  if (!FUNDS_SYM) FUNDS_SYM = keys[0];
  if ($("funds-combo")) { FUNDS_COMBO = new ComboBox($("funds-combo"), { placeholder: "fund name…", hideSym: true, onPick: (v) => { FUNDS_SYM = v || keys[0]; renderFunds(); writeHash(); } }); FUNDS_COMBO.setItems(items); FUNDS_COMBO.setValue(FUNDS_SYM); }
}

// ============================== FUND SKILL tab — holdings-based manager attribution ==============================
function _skillColor(v) {
  const m = { "skilled": "#1a7f37", "good selector, weak sizer": "#9a6700", "ahead but not yet significant": "#9a6700",
    "lagging benchmark": "#b42318", "insufficient history": "#6e7781", "index-like": "#6e7781", "undefined": "#6e7781", "inconclusive": "#6e7781" };
  return m[v] || "#6e7781";
}
function fsPct(x, d) { return (x === null || x === undefined || (typeof x === "number" && !isFinite(x))) ? "—" : (Number(x) * 100).toFixed(d == null ? 1 : d) + "%"; }
function fsNum(x, d) { return (x === null || x === undefined || (typeof x === "number" && !isFinite(x))) ? "—" : Number(x).toFixed(d == null ? 2 : d); }

// ============================================================================================
// WINDOW-ADAPTIVE RECOMPUTE — the browser recomputes every skill metric + the verdict over ANY
// start→end window from the baked monthly series ts[]={ym,A,rp,rb,ic,n,herf,sz}. This is a faithful
// JS port of vistas/funds_attribution.py::scheme_metrics restricted to a sub-window, so a fund's
// skill can be judged over the exact span a given manager ran it (managers change — a single
// full-history verdict blends regimes). For the FULL window the baked Python values are shown
// verbatim (no recompute → no rounding drift); only a NARROWED window triggers this recompute.
// ============================================================================================
function _std1(a) { const n = a.length; if (n < 2) return NaN; const m = a.reduce((s, x) => s + x, 0) / n; let v = 0; for (const x of a) v += (x - m) * (x - m); return Math.sqrt(v / (n - 1)); }
function _mean(a) { return a.length ? a.reduce((s, x) => s + x, 0) / a.length : NaN; }
// numpy-default ('linear') percentile of an ASCENDING-sorted array; p in 0..100
function _pctl(sorted, p) { if (!sorted.length) return NaN; const idx = (sorted.length - 1) * p / 100, lo = Math.floor(idx), hi = Math.ceil(idx); if (lo === hi) return sorted[lo]; const f = idx - lo; return sorted[lo] * (1 - f) + sorted[hi] * f; }
// deterministic PRNG (mulberry32) so the bootstrap verdict is stable across renders (seed = 1234567+n,
// matching the Python seed; the draws aren't bit-identical to numpy but are statistically equivalent)
function _mulberry32(seed) { let a = seed >>> 0; return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
// CIRCULAR block-bootstrap: %tile that mean(active) > 0 (handles autocorrelation), port of _block_bootstrap_mean
function _blockBootstrapMean(a, nBoot, block) {
  nBoot = nBoot || 2000; block = block || 3;
  a = a.filter((x) => x != null && isFinite(x)); const n = a.length;
  if (n < 6) return [NaN, NaN, NaN];
  const nb = Math.ceil(n / block), rng = _mulberry32(1234567 + n), means = new Array(nBoot);
  for (let b = 0; b < nBoot; b++) {
    let sum = 0, cnt = 0;
    for (let k = 0; k < nb && cnt < n; k++) { const st = Math.floor(rng() * n); for (let o = 0; o < block && cnt < n; o++) { sum += a[(st + o) % n]; cnt++; } }
    means[b] = sum / n;
  }
  means.sort((x, y) => x - y);
  let pos = 0; for (const m of means) if (m > 0) pos++;
  return [_pctl(means, 2.5), _pctl(means, 97.5), pos / nBoot];
}
// recompute ALL skill metrics over ts[i0..i1] (inclusive). Returns RAW (may hold NaN); verdict reads raw.
function fsComputeWindow(ts, i0, i1) {
  const w = ts.slice(i0, i1 + 1), n = w.length;
  const A = w.map((r) => r.A).filter((v) => v != null && isFinite(v));
  const rp = w.map((r) => (r.rp == null ? 0 : r.rp)), rb = w.map((r) => (r.rb == null ? 0 : r.rb));
  const oms = w.map((r) => (+r.ym.slice(0, 4)) * 12 + (+r.ym.slice(5, 7)));
  const span_m = oms.length ? (Math.max.apply(null, oms) - Math.min.apply(null, oms) + 1) : n;
  const years = span_m / 12, gappy = span_m > n;
  const prod = (arr) => arr.reduce((p, x) => p * (1 + x), 1);
  const cum_p = prod(rp), cum_b = prod(rb);
  const cagr_p = (years > 0 && cum_p > 0) ? Math.pow(cum_p, 1 / years) - 1 : NaN;
  const cagr_b = (years > 0 && cum_b > 0) ? Math.pow(cum_b, 1 / years) - 1 : NaN;
  const excess = (isFinite(cagr_p) && isFinite(cagr_b)) ? cagr_p - cagr_b : NaN;
  const mA = _mean(A), sA = _std1(A);
  const ir = (sA > 0) ? (mA / sA) * Math.sqrt(12) : NaN;       // = (mean·ppy)/(sd·√ppy)
  const t = isFinite(ir) ? ir * Math.sqrt(years) : NaN;
  const years_needed = (isFinite(ir) && ir > 0) ? Math.pow(1.96 / ir, 2) : NaN;
  const te = sA * Math.sqrt(12);
  const icv = w.map((r) => r.ic).filter((v) => v != null && isFinite(v));
  const ic_mean = _mean(icv), icSd = _std1(icv);
  const ic_t = (icv.length > 3 && icSd > 0) ? ic_mean / (icSd / Math.sqrt(icv.length)) : NaN;
  // sizing edge: ew (equal-weight counterfactual) = rp − sz. ANNUALISED = (Π(1+rp)/Π(1+ew))^(1/yrs)−1
  // (scale-free per-year drag); cumulative kept for the verdict's sign. Gappy → arithmetic sum/years.
  const szAll = w.every((r) => r.sz != null && isFinite(r.sz));
  let sizing_cum, sizing_cagr;
  if (szAll) {
    const _pr = prod(w.map((r) => r.rp)), _pe = prod(w.map((r) => r.rp - r.sz));
    sizing_cum = _pr - _pe;                                                  // cumulative absolute (verdict reads its sign)
    sizing_cagr = (_pe > 0 && years > 0) ? Math.pow(_pr / _pe, 1 / years) - 1 : NaN;   // annualised drag vs equal-wt
  } else {
    sizing_cum = w.reduce((s, r) => s + ((r.sz != null && isFinite(r.sz)) ? r.sz : 0), 0);
    sizing_cagr = years > 0 ? sizing_cum / years : NaN;
  }
  const hit = A.length ? A.filter((x) => x > 0).length / A.length : NaN;
  const sumAbs = A.reduce((s, x) => s + Math.abs(x), 0);
  const mag_hit = sumAbs > 0 ? A.reduce((s, x) => s + Math.max(x, 0), 0) / sumAbs : NaN;
  const up = A.filter((x) => x > 0), dn = A.filter((x) => x < 0);
  const avg_win = up.length ? _mean(up) : NaN, avg_loss = dn.length ? _mean(dn) : NaN;
  const slugging = (up.length && dn.length && avg_loss !== 0) ? avg_win / Math.abs(avg_loss) : NaN;
  const herfLast = n ? w[n - 1].herf : NaN, eff_n = (herfLast > 0) ? 1 / herfLast : NaN;
  const avg_names = n ? _mean(w.map((r) => r.n || 0)) : NaN;
  // PORTFOLIO-level (stock cross-section) batting & slug = period MEAN of the baked monthly series
  const _wm = (key) => _mean(w.map((r) => r[key]).filter((x) => x != null && isFinite(x)));
  const port_hit_cnt = _wm("hc"), port_hit_aum = _wm("ha"), port_slug_cnt = _wm("sc"), port_slug_aum = _wm("sa2");
  const pp = _blockBootstrapMean(A);
  return { n_months: n, years: years, gappy: gappy, excess_cagr: excess, cagr_paper: cagr_p, cagr_bench: cagr_b,
    info_ratio: ir, t_stat: t, years_needed: years_needed, tracking_error: te, ic_mean: ic_mean, ic_t: ic_t,
    sizing_edge_cum: sizing_cum, sizing_drag_cagr: sizing_cagr, hit_rate_monthly: hit, mag_hit: mag_hit, slugging: slugging, avg_win: avg_win,
    avg_loss: avg_loss, eff_n: eff_n, avg_names: avg_names, boot_meanA_lo: pp[0], boot_meanA_hi: pp[1],
    boot_p_positive: pp[2], _mA: mA,
    port_hit_cnt: port_hit_cnt, port_hit_aum: port_hit_aum, port_slug_cnt: port_slug_cnt, port_slug_aum: port_slug_aum };
}
// port of the scheme_metrics verdict ladder (same gates, same wording) for a recomputed window
function fsVerdict(m, is_thematic) {
  const fin = (v) => v != null && isFinite(v), pct = (x, d) => (x * 100).toFixed(d == null ? 1 : d);
  const t = m.t_stat, te = m.tracking_error, n = m.n_months, excess = m.excess_cagr, ic_t = m.ic_t,
    sz = m.sizing_edge_cum, p = m.boot_p_positive, mA = m._mA, yn = m.years_needed;
  const src = (fin(ic_t) && ic_t >= 2) ? "holding-rank-driven" : ((fin(sz) && sz > 0 && (!fin(ic_t) || ic_t < 1)) ? "sizing-aided" : "mixed-source");
  const them = is_thematic ? " — but vs the broad market, so largely a sector bet, not pure selection" : "";
  const sig = fin(t) && t >= 2 && mA > 0 && fin(p) && p >= 0.95;
  if (n < 24) return { verdict: "insufficient history", verdict_why: `only ${n} months — no skill verdict` };
  if (!fin(t)) return { verdict: "undefined", verdict_why: "no active-return variance" };
  if (te < 0.02) return { verdict: "index-like", verdict_why: `tracking error ${pct(te)}% — little active risk to judge` };
  if (sig) return { verdict: "skilled", verdict_why: `+${pct(excess)}%/yr gross, t=${t.toFixed(1)}, bootstrap ${pct(p, 0)}% (${src})${them}` };
  if (fin(ic_t) && ic_t >= 2 && fin(sz) && sz < 0) return { verdict: "good selector, weak sizer", verdict_why: `holding-IC-t=${ic_t.toFixed(1)} but sizing drag ${fin(m.sizing_drag_cagr) ? pct(m.sizing_drag_cagr) : "—"}%/yr` };
  if (fin(excess) && excess > 0) { const need = fin(yn) ? ` (need t≥2 & bootstrap≥95%; ~${yn.toFixed(0)}y more)` : ""; return { verdict: "ahead but not yet significant", verdict_why: `+${pct(excess)}%/yr, t=${fin(t) ? t.toFixed(1) : "—"}${need}` }; }
  if (fin(excess) && excess <= 0) return { verdict: "lagging benchmark", verdict_why: `${pct(excess)}%/yr` };
  return { verdict: "inconclusive", verdict_why: "" };
}
// ---- VANTAGE-POINT peer envelope (KV's MoneyBall) -------------------------------------------------
// The selected fund's own monthly metric series, computed with the SAME math the Python envelope uses
// (vistas/funds_attribution.py::fund_vantage_series) so the fund's line sits exactly inside the band.
const FS_ENV_ROLL = 36, FS_ENV_SMOOTH = 3;
function _ma(x, win, minp) {                          // trailing mean, min_periods
  const out = [];
  for (let i = 0; i < x.length; i++) {
    const w = x.slice(Math.max(0, i - win + 1), i + 1).filter((v) => v != null && isFinite(v));
    out.push(w.length >= minp ? w.reduce((s, v) => s + v, 0) / w.length : null);
  }
  return out;
}
function _rollBat(A, win) {
  const out = [];
  for (let i = 0; i < A.length; i++) {
    if (i < win - 1) { out.push(null); continue; }
    const w = A.slice(i - win + 1, i + 1).filter((v) => v != null && isFinite(v));
    out.push(w.length ? Math.round(100 * w.filter((v) => v > 0).length / w.length * 1000) / 1000 : null);
  }
  return out;
}
function _rollSlug(A, win) {
  const out = [];
  for (let i = 0; i < A.length; i++) {
    if (i < win - 1) { out.push(null); continue; }
    const w = A.slice(i - win + 1, i + 1).filter((v) => v != null && isFinite(v));
    const up = w.filter((v) => v > 0), dn = w.filter((v) => v < 0);
    out.push((up.length && dn.length) ? Math.round(_mean(up) / Math.abs(_mean(dn)) * 10000) / 10000 : null);
  }
  return out;
}
function fsVantageSeries(ts) {
  const ym = ts.map((r) => r.ym);
  const ha = ts.map((r) => r.ha), sa = ts.map((r) => r.sa2), A = ts.map((r) => r.A);
  const px = (arr) => arr.map((v) => (v != null && isFinite(v)) ? Math.round(v * 100000) / 1000 : null);  // ×100, 3dp
  return { ym: ym, port_hit_aum: px(_ma(ha, FS_ENV_SMOOTH, 2)), port_slug_aum: px(_ma(sa, FS_ENV_SMOOTH, 2)),
    nav_bat: _rollBat(A, FS_ENV_ROLL), nav_slug: _rollSlug(A, FS_ENV_ROLL) };
}
async function ensureEnvelopes() {                    // lazy-load the per-category peer envelope once
  if (FUNDS_ENV) return FUNDS_ENV;
  if (!LAZY || !LAZY.funds_attribution) return null;
  const e = await fetchJSON(LAZY.base + "funds_attribution/_envelopes.json");
  if (e) FUNDS_ENV = e;
  return FUNDS_ENV;
}
function _sanWin(m) { const o = {}; for (const k in m) { if (k === "_mA") continue; const v = m[k]; o[k] = (typeof v === "number" && !isFinite(v)) ? null : v; } return o; }

// stable sector → colour so a sector keeps its hue across the donut and the rotation bands
const _SEC_COLORS = {
  "Financial Services": "#1f77b4", "Information Technology": "#17becf", "Oil Gas & Consumable Fuels": "#8c564b",
  "Fast Moving Consumer Goods": "#2ca02c", "Automobile and Auto Components": "#ff7f0e", "Healthcare": "#e377c2",
  "Capital Goods": "#9467bd", "Metals & Mining": "#7f7f7f", "Consumer Durables": "#bcbd22", "Power": "#d62728",
  "Construction": "#aec7e8", "Construction Materials": "#ffbb78", "Chemicals": "#98df8a", "Telecommunication": "#c5b0d5",
  "Consumer Services": "#f7b6d2", "Services": "#c49c94", "Realty": "#dbdb8d", "Textiles": "#9edae5",
  "Media Entertainment & Publication": "#ff9896", "Diversified": "#c7c7c7",
  "Unclassified": "#cfd4da", "Other": "#e3e6ea",
};
function _secColor(s, i) { return _SEC_COLORS[s] || ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666"][(i || 0) % 8]; }

function fsPortfolioHTML(p) {
  if (!p) return "";
  const num = (x, s) => (x === null || x === undefined) ? "—" : (x + (s || ""));
  let h = `<section class="panel"><h2><span class="tag-sec">PORTFOLIO</span>What it owns — as on ${fEsc(p.asof || "—")}</h2>`;
  h += `<div class="q-stats">`
    + qStat("Equity", num(p.equity_pct_fund, "%"))
    + qStat("Equity names", num(p.n_equity))
    + qStat("Top-10 (latest)", (p.conc_ts && p.conc_ts.length) ? num(p.conc_ts[p.conc_ts.length - 1].top10, "%") : "—")
    + qStat("Sector-classified", num(p.sector_cover, "%"))
    + `</div>`;
  // donut + top holdings
  h += `<div class="qgrid2">`
    + `<div class="qcell"><div class="q-cap">Equity sector mix (% of equity)</div><div class="plot" id="plot-fs-sector" style="height:300px"></div></div>`
    + `<div class="qcell"><div class="q-cap">Top holdings (% of fund)</div><div style="max-height:300px;overflow-y:auto"><table class="gauge-tbl"><thead><tr><th>Holding</th><th>Sector</th><th class="num">Wt %</th></tr></thead><tbody>`;
  (p.top_holdings || []).forEach((t) => {
    h += `<tr><td class="name">${fEsc(t.name)}</td><td style="font-size:12px;color:#56606a">${fEsc(t.sector || "—")}</td><td class="num">${t.pct == null ? "—" : t.pct.toFixed(2)}</td></tr>`;
  });
  h += `</tbody></table></div></div></div>`;
  // sector rotation (full width)
  if (p.rotation && p.rotation.dates && p.rotation.dates.length > 1) {
    h += `<div class="q-cap" style="margin-top:14px">Sector rotation — how the equity book shifted over time (semiannual, % of equity)</div><div class="plot" id="plot-fs-rotation" style="height:340px"></div>`;
  }
  // concentration trend
  if (p.conc_ts && p.conc_ts.length > 1) {
    h += `<div class="q-cap" style="margin-top:12px">Concentration over time — top-10 weight &amp; number of names</div><div class="plot" id="plot-fs-conc" style="height:230px"></div>`;
  }
  // full book — every position for ANY month, via a month dropdown (the unified store, by_month/names)
  if (p.by_month && p.months && p.months.length) {
    const last = p.months.length - 1;
    h += `<div class="fs-hold-head"><span class="q-cap" style="margin:0">Full holdings — every position, pick a month</span>`
      + `<label class="fs-hold-pick">Portfolio month <select id="fs-hold-month" class="fs-sel">`
      + p.months.map((m, i) => `<option value="${fEsc(m)}"${i === last ? " selected" : ""}>${fEsc(m)}</option>`).join("")
      + `</select></label></div><div id="fs-hold-table"></div>`;
  } else if (p.by_sector && p.by_sector.length) {   // fallback for older data without by_month
    h += `<details style="margin-top:12px"><summary>Categorized holdings — every equity position grouped by sector, with subtotals (${p.by_sector.length} sectors)</summary>`;
    h += `<div style="overflow-x:auto"><table class="gauge-tbl fund-hold"><thead><tr><th>Sector / Holding</th><th>Ticker</th><th class="num">Wt % of fund</th></tr></thead><tbody>`;
    p.by_sector.forEach((b) => {
      h += `<tr style="background:#f3f5f7"><td><b>${fEsc(b.sector)}</b></td><td></td><td class="num"><b>${b.pct == null ? "—" : b.pct.toFixed(2)}</b></td></tr>`;
      b.names.forEach((n) => {
        h += `<tr><td style="padding-left:22px">${fEsc(n.name)}</td><td>${fEsc(n.symbol || "—")}</td><td class="num">${n.pct == null ? "—" : n.pct.toFixed(2)}</td></tr>`;
      });
    });
    h += `</tbody></table></div></details>`;
  }
  h += `<details><summary>Definition · Method · Why</summary><p><b>What:</b> the fund's actual book from its monthly SEBI portfolio disclosure — asset split, the equity sector mix, top positions, every holding grouped by sector with subtotals, and how the sector weights rotated over the fund's history (Apr-2013 onward where available).</p><p><b>Method:</b> from the 13-year monthly holdings look-through; <b>sector</b> = NSE macro-industry (one of ~18 groups) for each stock, via the Nifty Total-Market list (≈750 names); positions outside that list (delisted / sub-microcap) fall to <b>"Unclassified"</b>, shown as its own band — "sector-classified %" is the share of the equity book we could tag, so nothing is hidden. Sector mix and rotation are <b>% of the equity sleeve</b> (renormalised to 100%); top holdings and the categorized table are <b>% of the whole fund</b>; rotation/concentration are sampled semiannually to keep the picture readable.</p><p><b>Why:</b> before judging skill you want to SEE the book — what it bets on, how concentrated it is, and how those bets drifted across cycles. Diagnostics only.</p></details>`;
  h += `</section>`;
  return h;
}

// Render the WHOLE book for one month from the compact store arrays: names=[[name,symbol,sector],…],
// by_month={ym:[[nameIdx,pct,mktval_cr],…]}. Grouped by sector/asset-class, subtotals, weight-sorted.
function fsRenderHoldings(p, ym) {
  const host = $("fs-hold-table"); if (!host) return;
  const rows = (p.by_month && p.by_month[ym]) || [], nm = p.names || [];
  if (!rows.length) { host.innerHTML = `<div class='empty-note'>No holdings disclosed for ${fEsc(ym)}.</div>`; return; }
  const groups = {};
  rows.forEach((r) => {
    const meta = nm[r[0]] || ["?", null, "Unclassified"];
    const sec = meta[2] || "Unclassified";
    (groups[sec] = groups[sec] || []).push({ name: meta[0], symbol: meta[1], pct: r[1], cr: r[2] });
  });
  const gs = Object.keys(groups).map((s) => ({
    sector: s, rows: groups[s].sort((a, b) => (b.pct || 0) - (a.pct || 0)),
    wt: groups[s].reduce((t, x) => t + (x.pct || 0), 0), n: groups[s].length,
  })).sort((a, b) => b.wt - a.wt);
  const tot = rows.reduce((t, r) => t + (r[1] || 0), 0);
  const fcr = (x) => x == null ? "—" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: 1 });
  let h = `<div class="q-cap" style="margin:4px 0 6px">${rows.length} holdings · Σ ${tot.toFixed(1)}% of NAV · ${gs.length} sector / asset groups</div>`;
  h += `<div style="overflow-x:auto;max-height:540px;overflow-y:auto"><table class="gauge-tbl fund-hold"><thead><tr><th>Sector / Holding</th><th>Ticker</th><th class="num">Mkt val (₹cr)</th><th class="num">Wt %</th></tr></thead><tbody>`;
  gs.forEach((g) => {
    h += `<tr style="background:#f3f5f7"><td><b>${fEsc(g.sector)}</b> <span class="fh-n">${g.n}</span></td><td></td><td></td><td class="num"><b>${g.wt.toFixed(2)}</b></td></tr>`;
    g.rows.forEach((x) => {
      h += `<tr><td style="padding-left:22px">${fEsc(x.name)}</td><td>${fEsc(x.symbol || "—")}</td><td class="num">${fcr(x.cr)}</td><td class="num">${x.pct == null ? "—" : x.pct.toFixed(2)}</td></tr>`;
    });
  });
  h += `</tbody></table></div>`;
  host.innerHTML = h;
}

function fsDrawPortfolio(p) {
  if (!p) return;
  try {
    // ---- sector donut (top 11 + Other) ----
    const sn = (p.sector_now || []).slice();
    if (sn.length && $("plot-fs-sector")) {
      const top = sn.slice(0, 11); const restPct = sn.slice(11).reduce((a, x) => a + (x.pct || 0), 0);
      const labels = top.map((x) => x.sector).concat(restPct > 0 ? ["Other"] : []);
      const vals = top.map((x) => x.pct).concat(restPct > 0 ? [Math.round(restPct * 10) / 10] : []);
      Plotly.react("plot-fs-sector", [{
        type: "pie", hole: 0.5, labels: labels, values: vals, sort: false,
        marker: { colors: labels.map((s, i) => _secColor(s, i)) },
        textposition: "inside", texttemplate: "%{label}<br>%{value}%", insidetextorientation: "radial",
        hovertemplate: "%{label}: %{value}% of equity<extra></extra>",
      }], baseLayout({ showlegend: false, margin: { l: 8, r: 8, t: 8, b: 8 } }), PCONF);
    }
    // ---- sector rotation (stacked area) ----
    const ro = p.rotation;
    if (ro && ro.dates && ro.dates.length > 1 && $("plot-fs-rotation")) {
      const traces = (ro.sectors || []).map((s, i) => ({
        type: "scatter", mode: "lines", name: s, x: ro.dates, y: ro.matrix[i],
        stackgroup: "one", line: { width: 0.5, color: _secColor(s, i) }, fillcolor: _secColor(s, i),
        hovertemplate: "%{x} · " + s + ": %{y}%<extra></extra>",
      }));
      Plotly.react("plot-fs-rotation", traces, baseLayout({
        yaxis: { title: "% of equity", gridcolor: "#dfe3e8", range: [0, 100] },
        legend: { orientation: "h", y: -0.18, font: { size: 10 } }, margin: { l: 50, r: 12, t: 6, b: 60 },
      }), PCONF);
    }
    // ---- concentration over time ----
    const ct = p.conc_ts;
    if (ct && ct.length > 1 && $("plot-fs-conc")) {
      const xs = ct.map((c) => c.ym);
      Plotly.react("plot-fs-conc", [
        { type: "scatter", mode: "lines", name: "Top-10 weight", x: xs, y: ct.map((c) => c.top10), line: { color: "#8c564b", width: 2 }, hovertemplate: "%{x}: %{y}% in top 10<extra></extra>" },
        { type: "bar", name: "# names", x: xs, y: ct.map((c) => c.n), yaxis: "y2", marker: { color: "#cfd9e3" }, hovertemplate: "%{x}: %{y} names<extra></extra>" },
      ], baseLayout({
        yaxis: { title: "top-10 %", gridcolor: "#dfe3e8" }, yaxis2: { title: "# names", overlaying: "y", side: "right", showgrid: false },
        legend: { orientation: "h", y: 1.18 }, margin: { l: 48, r: 44, t: 6, b: 30 },
      }), PCONF);
    }
    // ---- full holdings table + month dropdown (the whole book, any month from the store) ----
    if (p.by_month && p.months && p.months.length) {
      const sel = $("fs-hold-month");
      const ym0 = (sel && sel.value) || p.months[p.months.length - 1];
      fsRenderHoldings(p, ym0);
      if (sel) sel.onchange = () => fsRenderHoldings(p, sel.value);
    }
  } catch (e) { console.error("fsDrawPortfolio:", e); }
}

// window-control bar: start/end month dropdowns + quick presets, populated from the scheme's months
function fsWinBar(ctx) {
  const opt = (sel) => ctx.months.map((m, i) => `<option value="${i}"${i === sel ? " selected" : ""}>${fEsc(m)}</option>`).join("");
  const pre = (p, lbl) => `<button type="button" class="fs-win-preset${ctx.preset === p ? " on" : ""}" data-preset="${p}">${lbl}</button>`;
  return `<div class="fs-winbar"><span class="fs-win-lbl">Analysis window</span>`
    + `<select id="fs-win-start" class="fs-sel fs-win-sel">${opt(ctx.i0)}</select>`
    + `<span class="fs-win-arrow">→</span>`
    + `<select id="fs-win-end" class="fs-sel fs-win-sel">${opt(ctx.i1)}</select>`
    + `<span class="fs-win-presets">${pre("full", "Full")}${pre("10y", "10Y")}${pre("5y", "5Y")}${pre("3y", "3Y")}</span></div>`;
}
// the VANTAGE-POINT panel: peer envelope (min–max shaded, 25/75th dotted, median) of each metric
// ACROSS the funds in a category, with the selected fund's own line inside — KV's MoneyBall view.
function fsVantagePanelHTML(ctx, f) {
  const cats = (ctx && ctx.cats) || [];
  const selCat = FS_VANT.cat || f.sebi_category;
  const catOpt = cats.map((c) => `<option value="${fEsc(c)}"${c === selCat ? " selected" : ""}>${fEsc(c)}</option>`).join("");
  const lvlSeg = (plot, cur) => `<span class="fs-lvl-seg">`
    + `<button type="button" class="fs-lvl${cur === "port" ? " on" : ""}" data-plot="${plot}" data-lvl="port">Portfolio</button>`
    + `<button type="button" class="fs-lvl${cur === "nav" ? " on" : ""}" data-plot="${plot}" data-lvl="nav">NAV</button></span>`;
  let h = `<section class="panel"><h2><span class="tag-sec">MONEYBALL</span>Where the fund ranks vs its category — peer envelope</h2>`;
  h += `<div class="fs-vantbar">Peer category <select id="fs-vant-cat" class="fs-sel">${catOpt}</select>`
    + `<span class="fs-vant-key"><span class="fs-vk-band"></span> peer min–max · <span class="fs-vk-dash">– –</span> 25th/75th-%ile · median · <b style="color:#1a7f37">━</b> ${fEsc(f.scheme_name)}</span></div>`;
  h += `<div class="fs-vant-cap"><span>Batting / hit rate — how often the manager beat the benchmark</span>${lvlSeg("bat", FS_VANT.blevel)}</div><div class="plot" id="plot-fs-vant-bat" style="height:250px"></div>`;
  h += `<div class="fs-vant-cap" style="margin-top:12px"><span>Slug rate — magnitude / hindsight quintile capture</span>${lvlSeg("slug", FS_VANT.slevel)}</div><div class="plot" id="plot-fs-vant-slug" style="height:250px"></div>`;
  h += `<details><summary>Definition · Method · Why</summary><p><b>What:</b> the selected fund's batting and slug rates plotted <i>against the full envelope of its category peers</i> over time — the shaded band is the range from the worst to the best fund in the category (min→max) on each date, the dashed lines are the 25th- and 75th-percentile peer, the thin line is the median peer, and the bold green line is this fund. The band depends only on the <b>category</b> and the <b>metric</b> — switching to another scheme in the same category leaves it unchanged; only changing category or metric moves it.</p><p><b>Method (two levels, toggle per plot):</b> <b>Portfolio</b> reads the manager's stock-picking straight off the monthly holdings (KV's MoneyBall "vantage point"): <b>hit rate</b> = AUM-weighted share of holdings whose next-month total return beat the SEBI-category benchmark (alpha ≥ 0); <b>slug rate</b> = net AUM in the top minus the bottom quartile of that month's <i>full tradeable universe</i> by return (did the book lean toward the eventual winners?). Both are smoothed with a 3-month trailing mean. <b>NAV</b> reads it through the fund return: <b>batting</b> = % of the trailing 36 months the fund beat its benchmark; <b>slug</b> = average up-month active ÷ |average down-month active| over 36 months. For each date and metric we take the cross-section across all funds in the category and report min / 25th / 50th / 75th / max (linear-interpolation percentile).</p><p><b>Why:</b> 60% batting means nothing until you know the field — if the 75th-percentile peer is at 65% and the best at 80%, a 60% fund is mid-pack. The envelope turns a lone number into a rank, and the history shows whether the fund is climbing or sliding within its category.</p><p><b>Caveat:</b> slugging uses gross (pre-cost) active return, and the portfolio version is built on <b>forward-looking</b> universe quartiles (a hindsight decomposition with look-ahead) — read it as descriptive attribution, not a tradable signal. Diagnostics only.</p></details></section>`;
  return h;
}
// one peer-envelope plot: shaded min–max + dotted 25/75 + median + the fund's own line + a neutral anchor
function _fsEnvPlot(divId, band, fundX, fundY, fundName, kind, level) {
  if (!$(divId)) return;
  const isSlugRatio = (kind === "slug" && level === "nav");
  const neutral = (kind === "bat") ? 50 : (isSlugRatio ? 1 : 0);
  const yTitle = (kind === "bat") ? "% beat" : (isSlugRatio ? "× (win/loss)" : "% net (top−bottom)");
  const hov = isSlugRatio ? "%{y:.2f}×" : "%{y:.1f}%";
  const traces = [];
  if (band && band.dates && band.dates.length) {
    const d = band.dates;
    traces.push({ type: "scatter", mode: "lines", x: d, y: band.min, line: { width: 0, color: "rgba(0,0,0,0)" }, hoverinfo: "skip", showlegend: false });
    traces.push({ type: "scatter", mode: "lines", x: d, y: band.max, line: { width: 0, color: "rgba(0,0,0,0)" }, fill: "tonexty", fillcolor: "rgba(70,130,180,0.13)", name: "peer min–max", hovertemplate: "%{x} · max " + hov + "<extra></extra>" });
    traces.push({ type: "scatter", mode: "lines", x: d, y: band.p75, line: { color: "#7e8a96", width: 1, dash: "dot" }, name: "25/75th %ile", hovertemplate: "%{x} · 75th " + hov + "<extra></extra>" });
    traces.push({ type: "scatter", mode: "lines", x: d, y: band.p25, line: { color: "#7e8a96", width: 1, dash: "dot" }, showlegend: false, hovertemplate: "%{x} · 25th " + hov + "<extra></extra>" });
    traces.push({ type: "scatter", mode: "lines", x: d, y: band.p50, line: { color: "#5b6670", width: 1.2 }, name: "median peer", hovertemplate: "%{x} · median " + hov + "<extra></extra>" });
  }
  if (fundX && fundY) traces.push({ type: "scatter", mode: "lines", x: fundX, y: fundY, line: { color: "#1a7f37", width: 2.6 }, name: fundName, connectgaps: false, hovertemplate: "%{x} · " + hov + "<extra></extra>" });
  const shapes = [{ type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: neutral, y1: neutral, line: { color: "#c0392b", width: 1, dash: "dot" } }];
  // Focus the y-axis on the SIGNAL series — median, 25/75th band, the fund's own line, and the neutral
  // anchor — with padding. Otherwise Plotly autoranges to the peer MIN–MAX envelope, whose occasional
  // one-fund spikes (e.g. a 2018 outlier near 95% hit rate) blow the scale wide and squash the whole
  // panel flat. The faint min–max band stays as background context and is allowed to clip past the axis.
  const yaxis = { title: yTitle, gridcolor: "#dfe3e8" };
  if (band && band.dates && band.dates.length) {
    let lo = Infinity, hi = -Infinity;
    const eat = (arr) => { if (arr) for (const v of arr) if (v != null && isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } };
    eat(band.p25); eat(band.p50); eat(band.p75); if (fundY) eat(fundY);
    if (isFinite(lo) && isFinite(hi)) {
      lo = Math.min(lo, neutral); hi = Math.max(hi, neutral);                 // keep the red neutral line in view
      const pad = Math.max((hi - lo) * 0.15, (kind === "bat") ? 3 : (isSlugRatio ? 0.15 : 2));
      lo -= pad; hi += pad;
      if (kind === "bat") { lo = Math.max(0, lo); hi = Math.min(100, hi); }   // hit rate lives in 0–100%
      yaxis.range = [lo, hi]; yaxis.autorange = false;
    }
  }
  Plotly.react(divId, traces, baseLayout({ yaxis: yaxis, shapes: shapes,
    legend: { orientation: "h", y: -0.18, font: { size: 10 } }, margin: { l: 52, r: 12, t: 6, b: 40 } }), PCONF);
}
async function fsDrawVantage(f, ts) {
  try {
    const env = await ensureEnvelopes();
    const selCat = FS_VANT.cat || f.sebi_category;
    const series = fsVantageSeries(ts);
    const inCat = (selCat === f.sebi_category);
    const metricOf = (kind, level) => (kind === "bat")
      ? (level === "port" ? "port_hit_aum" : "nav_bat")
      : (level === "port" ? "port_slug_aum" : "nav_slug");
    const one = (divId, kind, level) => {
      const metric = metricOf(kind, level);
      const band = (env && env[selCat] && env[selCat][metric]) ? env[selCat][metric] : null;
      _fsEnvPlot(divId, band, inCat ? series.ym : null, inCat ? series[metric] : null, f.scheme_name, kind, level);
    };
    one("plot-fs-vant-bat", "bat", FS_VANT.blevel);
    one("plot-fs-vant-slug", "slug", FS_VANT.slevel);
  } catch (e) { console.error("fsDrawVantage:", e); }
}
function fsScorecardHTML(f, ctx) {
  ctx = ctx || {};
  const wmark = f._windowed ? ` <span class="fs-winchip">window</span>` : "";
  const badge = `<span class="fs-badge" style="background:${_skillColor(f.verdict)}">${fEsc(f.verdict)}</span>`;
  let h = `<div class="quant-head"><div class="qh-name">${fEsc(f.scheme_name)}${f.amc ? ` <span class="qh-sym">${fEsc(f.amc)}</span>` : ""}</div><div class="qh-px">${badge}${wmark}</div></div>`;
  h += `<section class="panel"><h2><span class="tag-sec">SKILL</span>Verdict</h2>`;
  if (ctx.months && ctx.months.length) h += fsWinBar(ctx);
  if (f._windowed) {
    h += `<div class="fs-winnote">Recomputed for <b>${fEsc(ctx.months[ctx.i0])} → ${fEsc(ctx.months[ctx.i1])}</b> · ${f.n_months} months (${fsNum(f.years, 1)}y). Every figure & the verdict below cover ONLY this span — use it to judge a single manager's tenure.`
      + (f.n_months < 24 ? ` <b>Note:</b> under 24 months is too short to <i>prove</i> skill statistically (the verdict reads “insufficient history”); the figures are descriptive of the period.` : "")
      + `</div>`;
  }
  if (f.verdict_why) h += `<div class="q-note" style="font-size:13.5px;margin:10px 0">${fEsc(f.verdict_why)}</div>`;
  h += `<div class="q-note" style="font-size:12px;margin:8px 0;color:#62707d">This is a full-history, scheme-level read: it can blend <b>multiple managers</b> (we have no manager-tenure data), and a within-category style/factor tilt (momentum/value/size) can read as "skill". Diagnostic of the past track record, not a forward signal; the bootstrap p only says the past beat was unlikely to be pure luck.</div>`;
  h += `<div class="q-stats">`
    + qStat("Excess vs bench", fsPct(f.excess_cagr) + "/yr")
    + qStat("t-stat (IR·√yrs)", fsNum(f.t_stat, 1))
    + qStat("Information ratio", fsNum(f.info_ratio, 2))
    + qStat("Holding-rank IC-t", fsNum(f.ic_t, 1))
    + qStat("Years of data", fsNum(f.years, 1))
    + qStat("Yrs to prove skill", f.years_needed == null ? "—" : fsNum(f.years_needed, 0))
    + `</div><div class="q-stats">`
    + qStat("Fund CAGR (gross)", fsPct(f.cagr_paper) + "/yr")
    + qStat("Benchmark CAGR", fsPct(f.cagr_bench) + "/yr")
    + qStat("Tracking error", fsPct(f.tracking_error))
    + qStat("Eff. holdings (1/HHI)", fsNum(f.eff_n, 0))
    + `</div>`;
  // ---- MoneyBall "batting" decomposition: a manager can win on FREQUENCY (batting avg) or MAGNITUDE
  //      (slugging); sizing is the tie-breaker — same picks, did they size winners bigger? ----
  h += `<div class="q-cap" style="margin-top:6px">Batting — consistency × magnitude (Joe Peta / MoneyBall lens)</div><div class="q-stats">`
    + qStat("Batting avg (hit rate)", fsPct(f.hit_rate_monthly, 0))
    + qStat("Slugging (win/loss size)", f.slugging == null ? "—" : fsNum(f.slugging, 2) + "×")
    + qStat("Magnitude hit", fsPct(f.mag_hit, 0))
    + qStat("Avg winning month", fsPct(f.avg_win, 2))
    + qStat("Avg losing month", fsPct(f.avg_loss, 2))
    + qStat("Sizing edge (vs equal-wt /yr)", f.sizing_drag_cagr == null ? "—" : fsPct(f.sizing_drag_cagr) + "/yr")
    + `</div>`;
  h += `<div class="q-note" style="font-size:11.5px;color:#62707d">Batting is gross / pre-cost — a fund can beat &gt;50% of months gross and still trail net of fees. Flat/tie months count as a miss (strict &gt;). Diagnostic.</div>`;
  // ---- PORTFOLIO level (KV's MoneyBall): batting & slug read straight off the actual stock holdings,
  //      not through the aggregated NAV — % of stocks/AUM that beat, and top-vs-bottom-quintile capture ----
  // allocation benefit = AUM-weighted − count: positive ⇒ the manager put MORE money on the winners than
  // an equal-weighted version of the same picks would (sizing skill, not just selection breadth)
  const _sgn = (x) => x == null ? "—" : (x >= 0 ? "+" : "") + fsPct(x, 1);
  const _abh = (f.port_hit_aum == null || f.port_hit_cnt == null) ? null : f.port_hit_aum - f.port_hit_cnt;
  const _abs = (f.port_slug_aum == null || f.port_slug_cnt == null) ? null : f.port_slug_aum - f.port_slug_cnt;
  h += `<div class="q-cap" style="margin-top:6px">Stock-picking — read off the actual holdings each month (MoneyBall portfolio level)</div><div class="q-stats">`
    + qStat("Hit rate (AUM-wtd)", fsPct(f.port_hit_aum, 0))
    + qStat("Hit rate (count)", fsPct(f.port_hit_cnt, 0))
    + qStat("Alloc. benefit (hit)", _sgn(_abh))
    + qStat("Slug rate (AUM)", fsPct(f.port_slug_aum, 1))
    + qStat("Slug rate (count)", fsPct(f.port_slug_cnt, 1))
    + qStat("Alloc. benefit (slug)", _sgn(_abs))
    + `</div>`;
  h += `<div class="q-note"><b>Benchmark:</b> ${fEsc(f.benchmark)} · <b>Category:</b> ${fEsc(f.sebi_category)}${f.is_hybrid ? " (equity sleeve only)" : ""}.</div>`;
  h += `<div class="q-warn" style="margin-top:8px">${fEsc(f.basis || "")}</div></section>`;
  h += fsPortfolioHTML(f.portfolio);   // portfolio panel (leads with the sector chart) first…
  h += fsVantagePanelHTML(ctx, f);     // …then the MoneyBall peer-envelope panel below it
  h += `<section class="panel"><h2><span class="tag-sec">TRACK</span>Growth of ₹1 — holdings-implied (gross) vs benchmark</h2><div class="plot" id="plot-fs-cum" style="height:300px"></div>`;
  h += `<div class="q-cap" style="margin-top:10px">Monthly active return (fund − benchmark) &amp; per-month selection IC</div><div class="plot" id="plot-fs-active" style="height:210px"></div></section>`;
  h += `<details><summary>Definition · Method · Why</summary><p><b>What:</b> holdings-based fund-manager skill. A fund's edge over its benchmark is, exactly, A = Σ wᵢ·rᵢ − R_b: each start-of-month weight wᵢ times that holding's <b>total return</b> next month, minus the category-benchmark return. We compute this every month over the fund's history and ask whether it's repeatable skill or luck.</p><p><b>Method:</b> equity holdings (renormalised to 100%) × next-month total return per security (Bloomberg TR, verified vs our NSE prices to 0.15 bp ex-dividend), vs the SEBI-category TR index — which already strips most of the cap-size tilt, so the residual excess is mostly stock-selection + sizing. <b>Information Ratio</b> = mean active ÷ its volatility (annualised); <b>t-stat = IR·√years</b> (an IR of 0.5 needs ~16 years to prove skill at p&lt;0.05 — hence "years to prove"), and a "skilled" verdict additionally requires a <b>bootstrap</b> resample to clear 95% positive. <b>Caveat:</b> the t-stat (IR·√years) assumes independent monthly active returns; they are autocorrelated, so it <b>overstates</b> significance. A = gross holdings-implied active return (pre-fee/cost) — the investor's net IR is lower. Diagnostic, not a forward signal. <b>Holding-rank IC</b> = the monthly rank-correlation of <i>holding weight</i> vs forward return (Fama-MacBeth t) — a cap-tilt-contaminated proxy for true selection, since a pure active-weight IC needs point-in-time benchmark weights; <b>sizing edge</b> = the <b>annualised (per-year)</b> drag or gain of the fund's actual weighting vs equal-weighting the same names — (Π(1+r_actual)/Π(1+r_equal))^(1/yrs)−1 — the "tie-breaker" between two managers holding the same stocks. Reported per-year (not as a cumulative gap) so it doesn't grow just because the track record is longer. The <b>BATTING</b> panel splits the edge the MoneyBall way: <b>batting average</b> = share of months the fund beat its benchmark (consistency); <b>slugging</b> = average winning-month active return ÷ |average losing-month| (magnitude — a manager can win by being right often OR by sizing the rare big wins); a great manager scores on at least one. The excess is <b>GROSS</b> — pre-fee, pre-cash-drag, pre-trading-cost, and pre-factor-deflation (within-category style tilts not yet removed; a sectoral/thematic fund's excess is largely a sector bet, not selection). Domestic equity sleeve only; month-end snapshots.</p><p><b>Why:</b> NAV is the outcome; the holdings are the cause. This reads a manager's stock-picking, sizing and consistency straight off their actual decisions — separating repeatable skill from a lucky run. Diagnostics only — not investment advice.</p></details>`;
  return h;
}

function fsLeaderboardHTML(man) {
  const cats = Array.from(new Set(Object.values(man).map((m) => m.category).filter(Boolean))).sort();
  let rows = Object.keys(man).map((k) => Object.assign({ k }, man[k]));
  if (FUNDSKILL_CAT) rows = rows.filter((r) => r.category === FUNDSKILL_CAT);
  const sk = FUNDSKILL_SORT.key, dir = FUNDSKILL_SORT.dir;
  rows.sort((a, b) => { const av = a[sk], bv = b[sk]; if (av == null) return 1; if (bv == null) return -1; return (av < bv ? -1 : av > bv ? 1 : 0) * dir; });
  let h = `<section class="panel"><h2><span class="tag-sec">SCOREBOARD</span>All funds — holdings-based skill (${rows.length})</h2>`;
  h += `<div style="margin-bottom:8px"><select id="fs-cat" class="fs-sel"><option value="">All categories</option>${cats.map((c) => `<option value="${fEsc(c)}"${c === FUNDSKILL_CAT ? " selected" : ""}>${fEsc(c)}</option>`).join("")}</select> <span class="q-note">click a row to load · click a column to sort</span></div>`;
  h += `<div style="overflow-x:auto;max-height:520px;overflow-y:auto"><table class="gauge-tbl fs-lb"><thead><tr>`
    + `<th data-sk="scheme_name">Scheme</th><th data-sk="category">Category</th><th data-sk="verdict">Verdict</th>`
    + `<th data-sk="excess_cagr" class="num">Excess/yr</th><th data-sk="t_stat" class="num">t</th><th data-sk="ic_t" class="num">IC-t</th><th data-sk="n_months" class="num">Mo.</th></tr></thead><tbody>`;
  rows.slice(0, 400).forEach((r) => {
    const sel = r.k === FUNDSKILL_SYM ? ' class="fs-sel-row"' : "";
    h += `<tr data-k="${fEsc(r.k)}"${sel}><td class="name">${fEsc(r.name || r.k)}</td><td>${fEsc(r.category || "")}</td>`
      + `<td><span class="fs-dot" style="background:${_skillColor(r.verdict)}"></span>${fEsc(r.verdict || "")}</td>`
      + `<td class="num">${r.excess_cagr == null ? "—" : (r.excess_cagr * 100).toFixed(1) + "%"}</td>`
      + `<td class="num">${r.t_stat == null ? "—" : r.t_stat.toFixed(1)}</td>`
      + `<td class="num">${r.ic_t == null ? "—" : r.ic_t.toFixed(1)}</td>`
      + `<td class="num">${r.n_months == null ? "—" : r.n_months}</td></tr>`;
  });
  h += `</tbody></table></div>`;
  if (rows.length > 400) h += `<div class="q-note">showing top 400 by the current sort; use the search box at the top to jump to any scheme.</div>`;
  h += `</section>`;
  return h;
}

function fsWireLeaderboard() {
  const cat = $("fs-cat"); if (cat) cat.addEventListener("change", () => { FUNDSKILL_CAT = cat.value; renderFundSkill(); });
  document.querySelectorAll(".fs-lb th[data-sk]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sk;
    if (FUNDSKILL_SORT.key === k) FUNDSKILL_SORT.dir *= -1;
    else FUNDSKILL_SORT = { key: k, dir: (k === "scheme_name" || k === "category" || k === "verdict") ? 1 : -1 };
    renderFundSkill();
  }));
  document.querySelectorAll(".fs-lb tbody tr[data-k]").forEach((tr) => tr.addEventListener("click", () => {
    FUNDSKILL_SYM = tr.dataset.k; FS_WIN = null; FS_VANT.cat = null; if (FUNDSKILL_COMBO) FUNDSKILL_COMBO.setValue(FUNDSKILL_SYM); renderFundSkill(); writeHash();
  }));
}

// resolve the active window from FS_WIN (indices into ts), clamped + with preset highlight
function fsResolveWindow(ts) {
  const months = ts.map((r) => r.ym), n = months.length;
  let i0 = 0, i1 = n ? n - 1 : 0, preset = "full";
  if (FS_WIN && n) {
    i0 = Math.max(0, Math.min(FS_WIN.i0 | 0, n - 1));
    i1 = Math.max(i0, Math.min(FS_WIN.i1 | 0, n - 1));
    preset = FS_WIN.preset || "";
  }
  const fullWin = (i0 === 0 && i1 === n - 1);
  if (fullWin) preset = "full";
  return { months, i0, i1, fullWin, preset };
}
// preset window = trailing K years ending at the last month (Full clears the window)
function fsSetPreset(ts, p) {
  const n = ts.length; if (!n) return;
  if (p === "full") { FS_WIN = null; return; }
  const yrs = { "3y": 3, "5y": 5, "10y": 10 }[p]; if (!yrs) return;
  FS_WIN = { i0: Math.max(0, n - yrs * 12), i1: n - 1, preset: p };
}
function fsWireWindow(ts) {
  const s0 = $("fs-win-start"), s1 = $("fs-win-end");
  const apply = () => {
    let i0 = parseInt(s0.value, 10), i1 = parseInt(s1.value, 10);
    if (i0 > i1) { const t = i0; i0 = i1; i1 = t; }
    FS_WIN = (i0 === 0 && i1 === ts.length - 1) ? null : { i0: i0, i1: i1, preset: "" };
    renderFundSkill();
  };
  if (s0) s0.addEventListener("change", apply);
  if (s1) s1.addEventListener("change", apply);
  document.querySelectorAll(".fs-win-preset").forEach((b) => b.addEventListener("click", () => { fsSetPreset(ts, b.dataset.preset); renderFundSkill(); }));
}
// growth-of-₹1 + monthly-active charts over the SELECTED window, rebased to ₹1 at the window start
function fsDrawWindowedCharts(f, ts, i0, i1) {
  try {
    const w = ts.slice(i0, i1 + 1), xs = w.map((p) => p.ym);
    let cp = 1, cb = 1; const fp = [], fb = [];
    w.forEach((p) => { cp *= (1 + (p.rp || 0)); cb *= (1 + (p.rb || 0)); fp.push(Math.round(cp * 1000) / 1000); fb.push(Math.round(cb * 1000) / 1000); });
    const suf = (i0 === 0 && i1 === ts.length - 1) ? "" : " · rebased to ₹1 at window start";
    Plotly.react("plot-fs-cum", [
      { type: "scatter", mode: "lines", name: "Fund (gross, holdings-implied)", x: xs, y: fp, line: { color: "#1a7f37", width: 2 }, hovertemplate: "%{x}: ₹%{y}<extra></extra>" },
      { type: "scatter", mode: "lines", name: "Benchmark", x: xs, y: fb, line: { color: "#999", width: 1.5 }, hovertemplate: "%{x}: ₹%{y}<extra></extra>" },
    ], baseLayout({ yaxis: { title: "₹1 grown" + suf, gridcolor: "#dfe3e8" }, legend: { orientation: "h", y: 1.12 }, margin: { l: 54, r: 12, t: 6, b: 30 } }), PCONF);
    attachYAutoscale("plot-fs-cum");
    Plotly.react("plot-fs-active", [
      { type: "bar", name: "Active (mo.)", x: xs, y: w.map((p) => p.A == null ? null : Math.round(p.A * 10000) / 100), marker: { color: "#1f77b4" }, hovertemplate: "%{x}: %{y}%<extra></extra>" },
      { type: "scatter", mode: "lines", name: "Selection IC", x: xs, y: w.map((p) => p.ic), yaxis: "y2", line: { color: "#d62728", width: 1 }, hovertemplate: "%{x}: IC %{y}<extra></extra>" },
    ], baseLayout({ yaxis: { title: "active %", gridcolor: "#dfe3e8" }, yaxis2: { title: "IC", overlaying: "y", side: "right", range: [-1, 1], showgrid: false }, legend: { orientation: "h", y: 1.18 }, margin: { l: 50, r: 44, t: 6, b: 30 } }), PCONF);
  } catch (e) { console.error("fsDrawWindowedCharts:", e); }
}
function fsCategories(man) { return Array.from(new Set(Object.values(man || {}).map((m) => m.category).filter(Boolean))).sort(); }
// wire the vantage panel — category dropdown + per-plot NAV/Portfolio toggles redraw WITHOUT a full
// re-render (the peer band doesn't depend on the analysis window, so this is cheap + keeps scroll)
function fsWireVantage(f, ts) {
  const cat = $("fs-vant-cat");
  if (cat) cat.addEventListener("change", () => { FS_VANT.cat = cat.value; fsDrawVantage(f, ts); });
  document.querySelectorAll(".fs-lvl").forEach((b) => b.addEventListener("click", () => {
    const plot = b.dataset.plot, lvl = b.dataset.lvl;
    if (plot === "bat") FS_VANT.blevel = lvl; else FS_VANT.slevel = lvl;
    document.querySelectorAll('.fs-lvl[data-plot="' + plot + '"]').forEach((x) => x.classList.toggle("on", x.dataset.lvl === lvl));
    fsDrawVantage(f, ts);
  }));
}
// ---- per-fund CROWD ALIGNMENT / herding (MoneyBall D#1, fund side) ----
function fsCrowdHTML(f) {
  const cf = f && f.crowd_flow; if (!cf || cf.herding_avg == null) return "";
  const h = cf.herding_avg, pct = cf.contrarian_pctile;
  const style = cf.style;  // 'against' | 'balanced' | 'with' — category-relative terciles (audit 2026-06-26)
  const label = style === "against" ? "Trades against the consensus" : style === "with" ? "Trades with the consensus" : "Balanced";
  // NEUTRAL by design: herding does NOT predict forward returns, so with/against the crowd is positioning, not quality — never colour it good/bad.
  const posPct = Math.max(2, Math.min(98, (h + 1) * 50));
  const pbasis = cf.pctile_basis === "category" ? `its ${fEsc(cf.category || "category")} peers` : "all funds";
  let html = `<section class="panel fs-crowd"><h2><span class="tag-sec">CROWD</span>Crowd alignment — does this manager trade WITH or AGAINST the consensus?</h2>`;
  html += `<div class="q-stats">`
    + qStat("Herding score", `${h >= 0 ? '+' : ''}${h.toFixed(2)}`)
    + qStat("vs peers", pct != null ? `more independent than ${pct}% of ${pbasis}` : "—")
    + qStat("Style", `<span class="q-sub">${fEsc(label)}</span>`)
    + (cf.turnover_annual != null ? qStat("Turnover (1-way, ann.)", `${cf.turnover_annual}%` + (cf.turnover_pctile != null ? ` <span class="q-sub">${cf.turnover_pctile}th pctile</span>` : "")) : "")
    + `</div>`;
  html += `<div class="fs-spectrum"><span>against consensus</span><div class="fs-spec-track"><div class="fs-spec-dot" style="left:${posPct}%"></div></div><span>with consensus</span></div>`;
  // peer-relative Active Share (guarded 2026-06-25) — honest by default: bands/percentile only when reliable
  const as = cf.active_share;
  if (as && (as.active_share != null || as.caveat)) {
    const v = as.active_share, reliable = as.reliable;
    const band = v == null ? "" : v >= 70 ? "differentiated" : v <= 50 ? "closet (peer-hugger)" : "moderate";
    const bcls = v == null ? "neu" : v >= 70 ? "pos" : v <= 50 ? "neg" : "neu";
    html += `<div class="fs-as"><div class="q-cap">Peer active share — how differently is this fund positioned vs its category peers' <b>combined portfolio</b> (NOT a market benchmark)?</div><div class="q-stats">`;
    if (v != null) {
      html += qStat("Peer active share", `<span class="${bcls}">${v}%</span>` + (reliable ? ` <span class="q-sub">${fEsc(band)}</span>` : ""));
      if (reliable && as.active_share_pctile != null) html += qStat(`Within ${fEsc(as.category || 'category')}`, `${as.active_share_pctile}th pctile`);
    } else html += qStat("Peer active share", `<span class="q-sub">not comparable (too few peers)</span>`);
    html += `</div>`;
    html += `<div class="q-note">Measured against the <b>AUM-weighted combined book of category peers</b>, not the fund's index benchmark (we don't hold index constituent weights). It answers "how unlike my peers am I" — a useful, distinct lens; a true benchmark-based active share (vs the category's actual index) is a planned complement.</div>`;
    if (!reliable && as.caveat) html += `<div class="q-note">⚠ ${fEsc(as.caveat)}</div>`;
    else if (reliable && as.predictive_validated === true) html += `<div class="q-note">In ${fEsc(as.category)}, higher active share has historically gone with higher subsequent peer-relative return on our panel (a process diagnostic, not a forward-tested signal).</div>`;
    else if (reliable && as.predictive_validated === false) html += `<div class="q-note">Cleanly measured, but in ${fEsc(as.category)} active share did NOT predict outperformance on our panel — read it as positioning, not a selection signal.</div>`;
    html += `</div>`;
  }
  const lt = cf.latest;
  if (lt) {
    const row = (t) => `<tr><td>${fEsc(t.name || t.sym || '')}</td><td>${fEsc(t.sym || '')}</td><td class="num ${t.cr >= 0 ? 'pos' : 'neg'}">${t.cr >= 0 ? '+' : ''}${Math.round(t.cr).toLocaleString('en-IN')}</td><td>${t.crowd ? '<span class="q-sub">with crowd</span>' : '<span class="pos">against</span>'}</td></tr>`;
    const buys = (lt.buys || []).slice(0, 6), sells = (lt.sells || []).slice(0, 6);
    html += `<div class="qgrid2"><div><div class="q-cap">Biggest adds — net of drift (₹cr, ${fEsc(cf.ym || '')})</div><table class="gauge-tbl"><tbody>${buys.map(row).join('') || '<tr><td>—</td></tr>'}</tbody></table></div>`;
    html += `<div><div class="q-cap">Biggest trims</div><table class="gauge-tbl"><tbody>${sells.map(row).join('') || '<tr><td>—</td></tr>'}</tbody></table></div></div>`;
  }
  html += `<details><summary>Definition · Method · Why</summary><p><b>Herding score</b> (−1…+1) = the trade-size-weighted sign-agreement of this fund's monthly active trades (net of price drift) with the rest of the industry's trades in the SAME stocks (the fund itself excluded). −1 = always AGAINST the contemporaneous crowd; +1 = always WITH it. <b>The percentile and style band are measured against the fund's own SEBI category</b> (like-for-like), using data-derived terciles — not hand-set cut-offs. <b>This is a persistent STYLE TRAIT</b> (a fund that trades against the crowd tends to keep doing so — per-fund year-over-year rank-correlation +0.31 across 769 funds, 2013–2026), so it fairly describes process. <b>It is positioning / diagnostic, NOT a forward signal:</b> on our full 13-year panel, herding does NOT predict a fund's forward category-excess return at 3, 6 or 12 months (cross-sectional rank-correlation ≈ 0, t&lt;1; the contrarian-minus-consensus return spread ≈ 0). We make <b>no leadership claim</b> — a low score means the fund traded against the consensus that month, not that it anticipated where the crowd would go (when tested, against-the-crowd funds did not lead the crowd). <b>Turnover</b> = the one-way % of the equity book actively traded per year (net of drift) — a description of how actively the manager trades, not a quality judgement (on our recent window higher turnover happened to coincide with higher return, but that is regime-specific, so read it as style, not skill). Source: all-AMC monthly holdings × our NSE total-return panel, last 18 months. <b>Active share</b> = ½·Σ|w_fund − w_peers| over the equity sleeve, where w_peers is the EX-SELF, AUM-weighted aggregate book of the fund's SEBI-category peers (Cremers-Petajisto proxy — we lack official index weights, so this is differentiation from PEERS, not from the cap-weighted index). Percentile is WITHIN category. Sector/thematic funds (mandate-driven) and hybrids (equity-sleeve only — cash/debt bet invisible) are flagged, not ranked against diversified funds; on our panel high active share predicted higher peer-relative return only in Large-Cap, ELSS, Focused and Flexi. A true <b>benchmark-based</b> active share (vs each category's actual index, e.g. Nifty 100 / Midcap 150) is a planned complement — it needs index constituent weights we don't yet hold.</p></details>`;
  html += `</section>`;
  return html;
}

// ---- market-wide money-flow (MoneyBall D#1, CIO/analyst lens) ----
function fsMarketFlowsHTML() {
  const mf = (typeof window !== "undefined" && window.VISTAS_MARKET_FLOWS) || null;
  if (!mf || !mf.top_bought) return "";
  const row = (t) => `<tr><td>${fEsc(t.name || t.sym || '')}</td><td>${fEsc(t.sym || '')}</td><td class="num ${t.cr >= 0 ? 'pos' : 'neg'}">${t.cr >= 0 ? '+' : ''}${Math.round(t.cr).toLocaleString('en-IN')}</td><td class="num">${t.breadth}</td><td class="num">${t.dbreadth >= 0 ? '+' : ''}${t.dbreadth}</td></tr>`;
  const tbl = (rows) => `<table class="gauge-tbl"><thead><tr><th>Stock</th><th>Ticker</th><th class="num">Net ₹cr</th><th class="num">#funds</th><th class="num">Δ</th></tr></thead><tbody>${rows.map(row).join('')}</tbody></table>`;
  let html = `<details class="panel fs-market"><summary><b>Market money-flow — where active managers moved (${fEsc(mf.ym || '')})</b>, net of drift &amp; corporate actions</summary>`;
  html += `<div class="qgrid2"><div><div class="q-cap"><span class="pos">Most bought</span> (net active ₹cr)</div>${tbl((mf.top_bought || []).slice(0, 10))}</div>`;
  html += `<div><div class="q-cap"><span class="neg">Most sold</span></div>${tbl((mf.top_sold || []).slice(0, 10))}</div></div>`;
  if (mf.ca_quarantined && mf.ca_quarantined.length) html += `<div class="q-note">Excluded this month (structural corporate action — flow not a clean signal): ${mf.ca_quarantined.map((x) => fEsc(x.sym || x.name)).join(', ')}.</div>`;
  html += `<p class="q-note">Net active flow per stock = Σ over active equity funds of [end value − start value×(1+the stock's total return)] — the rupees managers actually moved, price drift &amp; corporate actions removed (merger swaps bridged, demergers quarantined). Breadth = # active funds holding; Δ = month-on-month change.</p>`;
  html += `</details>`;
  return html;
}

// ---- data-quality: survivorship coverage of the holdings panel (CIO/quant honesty) ----
function fsSurvivorshipHTML() {
  const s = (typeof window !== "undefined" && window.VISTAS_SURVIVORSHIP) || null;
  if (!s || s.funds_ever == null) return "";
  const prem = s.premium_annual_pct;
  let html = `<details class="panel fs-market fs-surv"><summary><b>Data quality — survivorship coverage of the holdings panel</b> ${prem != null ? `(measured bias ≈ ${prem.toFixed(2)}%/yr)` : ""}</summary>`;
  html += `<div class="q-stats">`
    + qStat("Equity funds ever (Apr-2013→now)", `${s.funds_ever}`)
    + qStat("Still live", `${s.funds_live}`)
    + qStat("Died over the period", `${s.funds_dead}`)
    + qStat("Dead funds NOT in our holdings", `<span class="neg">${s.missing_dead}</span>` + (s.missing_dead_ge24mo != null ? ` <span class="q-sub">(${s.missing_dead_ge24mo} lived ≥2yr)</span>` : ""))
    + `</div>`;
  if (prem != null && s.all_cagr != null) {
    html += `<div class="q-stats">`
      + qStat("All equity funds, survivorship-FREE (CAGR)", `${s.all_cagr}%/yr`)
      + qStat("Survivors only (CAGR)", `<span class="pos">${s.surv_cagr}%/yr</span>`)
      + qStat("Survivorship premium", `<span class="neg">+${prem.toFixed(2)}%/yr</span>`)
      + `</div>`;
  }
  html += `<p class="q-note"><b>What this means.</b> Our monthly holdings come from a vendor file that back-fills the <i>current</i> scheme list, so funds that merged or wound up before ~2024 are largely absent (their portfolios aren't archived by any free source). The measured bias above = the equal-weight CAGR of <i>every</i> equity fund alive in each month (including ones that later died), versus only today's survivors, on a survivorship-free NAV panel built from AMFI history (~${s.premium_n_codes || ''} scheme series, ${s.premium_years || ''}y). <b>Cross-sectional metrics on this terminal — who owns a stock now, current money-flows, current active share — are NOT affected</b> (we hold the full live universe). The caveat applies only to <i>historical/persistence</i> claims, which are scoped accordingly. (Death-count caveat: raw "funds that disappeared" overstates failure — much of it is AMC rebrands and renames where the portfolio survives under a new name.)</p>`;
  html += `</details>`;
  return html;
}

// ── FM ACTION SHORTLIST (task #39) — a per-fund EVIDENCE shortlist: held names whose validated
// forces have WEAKENED (trim candidates) + in-mandate un-held names whose forces have STRENGTHENED
// (add candidates). Decision-SUPPORT for the fund manager (human, or the FM agent in the Agentic
// AMC) — it ranks signals ALREADY validated + published on this terminal; it sizes nothing, predicts
// no return, and is NOT an instruction. Deck-only JS: pure filter+sort over the already-baked
// smart_vs_street.json (ARM/flow/quadrant) + the fund's baked equity book — no new file, no new raw-ARM
// surface, no JS↔Python parity port (no formula; numbers come pre-computed). Ranked by analyst-revision
// momentum (ARM, the one forward-validated axis, IC~0.03-0.045) ONLY; flow is a sign-flag, never the key.
const FM_SHORTLIST_CAVEAT = `<details class="q-note"><summary>Definition · Method · Why · Limits</summary>
<p><b>Evidence shortlist — decision-support for the fund manager, not instructions.</b> This panel ranks names by forces <i>already validated and published on this terminal</i>; it invents no new signal and forecasts no return. <b>It does not size, time, or recommend trades — the fund manager decides and sizes under the mandate's constraints.</b></p>
<p>The book is split into <b>three force-based action lists</b> (held vs not-held × strengthening vs weakening):</p>
<p><b>Held · weakening (TRIM)</b> = names this fund currently holds that now sit in the weak quadrant (analysts not revising up <i>and</i> funds net-selling). Sorted by ARM, weakest first. <i>Candidates to review for trimming, not "sells."</i></p>
<p><b>Held · strengthening (ADD-MORE)</b> = names this fund <b>already holds</b> that sit in the strong quadrant (analysts revising up <i>and</i> funds net-buying). Sorted by ARM, best first; the Held-wt vs Bench-wt columns flag where the position is still underweight (room to add). <i>Candidates to consider adding to, not "buys."</i></p>
<p><b>Not held · strengthening (ADD)</b> = in-mandate names this fund does <b>not</b> hold that sit in the strong quadrant. Sorted by ARM. <i>Candidates to research, not "buys."</i></p>
<p><b>Honest limits:</b> Analyst-revision (ARM) has a <b>small</b> forward edge on our data (rank-correlation ~0.03–0.045 over 1–6 months) — useful across many names, never decisive on one. <b>Net fund flow shows what managers <i>did</i>; on our data it does <i>not</i> predict returns (crowding has if anything been mildly contrarian) — read it as positioning, not a buy signal.</b> The <b>Breadth</b> column = how many of the funds holding the name <i>added</i> vs <i>trimmed</i> it last month (a size-neutral headcount) on the <b>price-adjusted</b> basis — price stripped out, but scheme inflows still counted (so it is <i>not</i> the inflow-immune "net-active" conviction measure). We do <b>not</b> combine the two into one score (the blend did not beat analyst-revisions alone) and show <b>no expected return, price target, or confidence %</b>. This is a small, mechanically-filtered snapshot from the latest disclosed holdings month — not the full opportunity set; absence from a list is not a verdict. Mandate eligibility uses reconstructed (not official) benchmark weights. ARM scores older than 90 days are excluded (not shown as "weak").</p></details>`;

function fmShortlistHTML() {
  return `<section class="panel">
    <h2><span class="tag-sec">FORCE WATCHLIST</span>Evidence shortlist — decision-support, not instructions</h2>
    <div id="fm-shortlist-host"><div class="empty-note">Loading…</div></div>
    ${FM_SHORTLIST_CAVEAT}
  </section>`;
}

function fmCr(v) { return Number(v || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 }); }

// benchmark constituent weights for a fund's SEBI category -> {symbol: wt%, "vid:<id>": wt%} (or null if
// the bench file can't load). Reuses the cockpit's category→benchmark map + the lazy benchmark cache.
async function fmBenchWeights(category) {
  try {
    const slug = defaultBenchForCategory(category);
    if (!slug) return null;
    const bench = await ensureBenchmark(slug);
    if (!bench || !bench.constituents) return null;
    const w = {};
    bench.constituents.forEach((c) => {
      const wt = +((c.w_ffmcap != null ? c.w_ffmcap : (c.w_ew != null ? c.w_ew : 0))) || 0;
      if (c.symbol) w[c.symbol] = wt;
      if (c.vst_id != null) w["vid:" + c.vst_id] = wt;
    });
    return w;
  } catch (e) { console.error("fmBenchWeights:", e); return null; }
}

// rationale QUOTES the signal, never a verdict ("weak"/"trim"). e.g. "analysts revising up (ARM 62); funds net-buying 3M (₹240cr)"
function fmRationale(r) {
  const a = "analysts " + (r.recommending ? "revising up" : "not revising up") + " (ARM " + (r.arm != null ? Math.round(r.arm) : "—") + ")";
  const f = "funds net-" + (r.buying_3m ? "buying" : "selling") + " 3M (₹" + fmCr(Math.abs(r.flow_3m || 0)) + "cr)";
  return a + "; " + f;
}

// the fund's held weight for a screen row — join on vst_id FIRST (the baked equity_holdings book is
// vst_id-keyed; symbol is dropped in the vst_id groupby), then fall back to symbol. Returns null if not held.
function fmHeldWt(r, held) {
  if (r.vst_id != null && held["vid:" + r.vst_id] != null) return held["vid:" + r.vst_id];
  if (r.symbol != null && held[r.symbol] != null) return held[r.symbol];
  return null;
}

function fmRow(r, held, benchW) {
  const s = r.symbol;
  const bw = benchW ? (benchW[s] != null ? benchW[s] : (r.vst_id != null ? benchW["vid:" + r.vst_id] : undefined)) : undefined;
  const hw = fmHeldWt(r, held);
  return {
    symbol: s, name: r.name, sector: r.sector, vst_id: r.vst_id,
    cur: (hw != null ? hw : null),
    bench_wt: (bw != null ? bw : null),
    not_held: (hw == null),
    arm: r.arm, arm_asof: r.arm_asof, arm_stale: !!r.arm_stale, recommending: !!r.recommending,
    flow_3m: r.flow_3m, flow_1m: r.flow_1m, buying_3m: !!r.buying_3m,
    net_breadth: r.net_breadth, mf_nfunds: r.mf_nfunds, quadrant_3m: r.quadrant_3m,
    rationale: fmRationale(r),
  };
}

function fmShortlistTable(rows, mode) {
  const showAddCols = (mode === "add" || mode === "add_more");   // add + add-more show Bench/Held wt + #funds
  if (!rows.length) {
    const what = mode === "add" ? "in-mandate strengthening-force candidates (not held)"
      : mode === "add_more" ? "held names with strengthening forces" : "weakening-force holdings";
    return `<div class="empty-note">No ${what} this month.</div>`;
  }
  const num = (v) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(2);
  const flowCell = (v) => {
    if (v == null || isNaN(v)) return `<td class="num">—</td>`;
    return `<td class="num ${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "+" : "−"}${fmCr(Math.abs(v))}</td>`;
  };
  let h = `<div class="screen-tblwrap"><table class="gauge-tbl"><thead><tr><th>Stock</th><th>Sector</th>`
    + (showAddCols ? `<th class="num">Bench wt</th><th class="num">Held wt</th>` : `<th class="num">Wt %</th>`)
    + `<th class="num">ARM</th><th class="num">Flow 3M ₹cr</th><th class="num">Breadth</th>`
    + (showAddCols ? `<th class="num">#funds</th>` : ``)
    + `<th>What the signals say</th></tr></thead><tbody>`;
  rows.forEach((x) => {
    h += `<tr><td class="lft"><b>${fEsc(x.symbol)}</b> <span class="sec">${fEsc(x.name || "")}</span></td>`
      + `<td class="lft sec">${fEsc(x.sector || "—")}</td>`;
    h += showAddCols ? `<td class="num">${num(x.bench_wt)}</td><td class="num">${x.cur == null ? "—" : num(x.cur)}</td>`
               : `<td class="num">${x.cur == null ? "—" : num(x.cur)}</td>`;
    h += `<td class="num ${x.recommending ? "pos" : "neg"}" title="as of ${fEsc(x.arm_asof || "—")}">${x.arm == null ? "—" : Math.round(x.arm)}</td>`
      + flowCell(x.flow_3m)
      + `<td class="num">${x.net_breadth == null ? "—" : (x.net_breadth > 0 ? "+" : "") + x.net_breadth}</td>`;
    if (showAddCols) h += `<td class="num">${x.mf_nfunds == null ? "—" : x.mf_nfunds}</td>`;
    h += `<td class="lft sec">${fEsc(x.rationale)}</td></tr>`;
  });
  return h + `</tbody></table></div>`;
}

async function renderFMShortlist(f) {
  const host = $("fm-shortlist-host"); if (!host) return;
  await ensureScreen();
  const screen = screenData();
  if (!screen || !screen.rows) { host.innerHTML = `<div class="empty-note">Screen data unavailable in this deck.</div>`; return; }
  const TRIM_QUADS = [4], ADD_QUAD = 1, CAP = 15;   // 4 = Neither (weak); 1 = Recommending+Buying (strong)
  // index the screen by BOTH keys; the held book is keyed on vst_id (the canonical identity every baked
  // equity_holdings row carries) — symbol is dropped in the vst_id groupby, so a symbol-only join is EMPTY
  // (the #39 bug: held names showed as not-held + the weakening column came up 0). Join on vst_id.
  const rows = {}, rowsByVid = {};
  screen.rows.forEach((r) => { if (r.symbol) rows[r.symbol] = r; if (r.vst_id != null) rowsByVid[r.vst_id] = r; });
  const book = (f.crowd_flow && f.crowd_flow.equity_holdings) || [];
  const held = {};
  book.forEach((h) => {
    const wt = +((h.pct != null ? h.pct : (h.weight != null ? h.weight : 0))) || 0;
    if (h.vst_id != null) held["vid:" + h.vst_id] = wt;       // primary key (book is vst_id-keyed)
    const s = h.symbol || h.sym; if (s) held[s] = wt;          // symbol fallback (e.g. a paper book that carries it)
  });
  const benchW = await fmBenchWeights(f.sebi_category);
  const usable = (r) => r && r.arm != null && !r.arm_stale;
  // TRIM: held + weak quadrant, weakest ARM first (ties -> larger outflow). Resolve each book row to a
  // screen row by vst_id (then symbol); dedupe so a name can't appear twice.
  const seenTrim = {};
  const heldRows = book.map((h) => (h.vst_id != null && rowsByVid[h.vst_id]) || rows[h.symbol || h.sym] || null)
    .filter((r) => { if (!r) return false; const k = r.symbol || r.vst_id; if (seenTrim[k]) return false; seenTrim[k] = 1; return true; });
  let trim = heldRows.filter((r) => usable(r) && TRIM_QUADS.indexOf(r.quadrant_3m) >= 0)
    .map((r) => fmRow(r, held, benchW)).sort((a, b) => (a.arm - b.arm) || (Math.abs(b.flow_3m || 0) - Math.abs(a.flow_3m || 0)));
  // ADD-MORE: HELD names in the strong quadrant (recommending & buying) — consider increasing, best ARM first
  let addMore = heldRows.filter((r) => usable(r) && r.quadrant_3m === ADD_QUAD)
    .map((r) => fmRow(r, held, benchW)).sort((a, b) => b.arm - a.arm);
  // ADD: strong quadrant, in-mandate, NOT held (held strong names are ADD-MORE), best ARM first
  let add = screen.rows.filter((r) => {
    if (!usable(r) || r.quadrant_3m !== ADD_QUAD) return false;
    if (fmHeldWt(r, held) != null) return false;                  // held -> it's an ADD-MORE, not a new ADD
    const s = r.symbol;
    const bw = benchW ? (benchW[s] != null ? benchW[s] : (r.vst_id != null ? benchW["vid:" + r.vst_id] : undefined)) : undefined;
    if (benchW && bw == null) return false;                       // bench loaded but name out of mandate universe
    return true;
  }).map((r) => fmRow(r, held, benchW)).sort((a, b) => b.arm - a.arm);
  trim = trim.slice(0, CAP); addMore = addMore.slice(0, CAP); add = add.slice(0, CAP);
  host.innerHTML =
    `<div class="fm-shortlist-grid fm-3col">
       <div><h3 class="fm-col-h">Held · weakening — consider TRIM (${trim.length})</h3>${fmShortlistTable(trim, "trim")}</div>
       <div><h3 class="fm-col-h">Held · strengthening — consider ADD-MORE (${addMore.length})</h3>${fmShortlistTable(addMore, "add_more")}</div>
       <div><h3 class="fm-col-h">Not held · strengthening — consider ADD (${add.length})</h3>${fmShortlistTable(add, "add")}</div>
     </div>
     <div class="q-note">As of ${fEsc(screen.holdings_asof || "—")} · ranked by analyst-revision momentum (ARM); flow shown as a sign-flag, not the ranking key. Decision-support — the FM decides &amp; sizes.</div>`;
}

async function renderFundSkill() {
  const host = $("fundskill-body"); if (!host) return;
  const man = fundsAttrManifest() || {};
  const hold = fundsHoldonlyManifest() || {};
  const keys = Object.keys(man);
  if (!keys.length && !Object.keys(hold).length) { host.innerHTML = `<div class='empty-note'>No fund data in this deck yet.</div>`; return; }
  // holdings-only fund (passive index/ETF or debt — no 13-yr active history): show its book, skip skill
  if (FUNDSKILL_SYM && !man[FUNDSKILL_SYM] && hold[FUNDSKILL_SYM]) { await fsRenderHoldonly(FUNDSKILL_SYM, hold[FUNDSKILL_SYM], man); return; }
  if (!FUNDSKILL_SYM || !man[FUNDSKILL_SYM]) FUNDSKILL_SYM = keys.slice().sort((a, b) => ((man[b] || {}).t_stat || -99) - ((man[a] || {}).t_stat || -99))[0];
  const f = await ensureFundsAttr(FUNDSKILL_SYM);
  if (!f) { host.innerHTML = `<div class='empty-note'>No skill data cached for ${fEsc(FUNDSKILL_SYM)}.</div>` + fsLeaderboardHTML(man); fsWireLeaderboard(); return; }
  const ts = f.ts || [];
  const ctx = fsResolveWindow(ts);
  ctx.cats = fsCategories(man);                 // peer categories for the vantage dropdown
  // EFFECTIVE metrics: baked Python (exact) for the full window; recomputed in-browser for a window
  let fEff = f;
  if (!ctx.fullWin && ts.length) {
    const m = fsComputeWindow(ts, ctx.i0, ctx.i1);
    fEff = Object.assign({}, f, _sanWin(m), fsVerdict(m, f.is_thematic), { _windowed: true });
  }
  host.innerHTML = fsMarketFlowsHTML() + fsSurvivorshipHTML() + fsScorecardHTML(fEff, ctx) + fsCrowdHTML(f)
    + fmShortlistHTML()
    + `<section class="panel" id="fundskill-bench-host" style="display:none"></section>`
    + `<section class="panel" id="fundskill-compare-host" style="display:none"></section>` + fsLeaderboardHTML(man);
  fsWireLeaderboard();
  fsWireWindow(ts);
  fsWireVantage(f, ts);
  if (f.portfolio) fsDrawPortfolio(f.portfolio);
  fsDrawWindowedCharts(f, ts, ctx.i0, ctx.i1);
  fsDrawVantage(f, ts);
  // benchmark-relative comparison (true active share vs a chosen index) — from the fund's baked equity book
  try {
    const book = (f.crowd_flow && f.crowd_flow.equity_holdings) || [];
    await renderFundsBench(book, f.sebi_category, "fundskill-bench-host");
  } catch (e) { console.error("fundskill bench:", e); }
  // multi-fund side-by-side compare (anchor = this fund; peers added in-panel)
  try {
    await renderFundCompare(FUNDSKILL_SYM, f.sebi_category, "fundskill-compare-host");
  } catch (e) { console.error("fundskill compare:", e); }
  // FM action shortlist (#39): evidence-ranked trim/add candidates from validated forces (decision-support)
  try {
    await renderFMShortlist(f);
  } catch (e) { console.error("fm shortlist:", e); }
}

// holdings table for a live-AMC book (funds_portfolio shape), grouped by sector(equity)/asset-class
function _holdoTable(H) {
  const all = (H || []).slice();
  const lab = (x) => (/equ/i.test(x.asset_class || "") ? (x.industry || "Unclassified") : (x.asset_class || "Other"));
  const cr = (x) => (x.mktval_lakh != null ? x.mktval_lakh / 100 : null);
  const groups = {}; all.forEach((x) => { const k = lab(x); (groups[k] = groups[k] || []).push(x); });
  const gs = Object.keys(groups).map((k) => ({ k, rows: groups[k].sort((a, b) => (b.pct || 0) - (a.pct || 0)),
    wt: groups[k].reduce((t, x) => t + (x.pct || 0), 0), n: groups[k].length })).sort((a, b) => b.wt - a.wt);
  const fcr = (v) => v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 1 });
  let s = `<div style="overflow-x:auto;max-height:540px;overflow-y:auto"><table class="gauge-tbl fund-hold"><thead><tr><th>Sector / Holding</th><th>Ticker</th><th class="num">Mkt val (₹cr)</th><th class="num">Wt %</th></tr></thead><tbody>`;
  gs.forEach((g) => {
    s += `<tr style="background:#f3f5f7"><td><b>${fEsc(g.k)}</b> <span class="fh-n">${g.n}</span></td><td></td><td></td><td class="num"><b>${g.wt.toFixed(2)}</b></td></tr>`;
    g.rows.forEach((x) => { s += `<tr><td style="padding-left:22px">${fEsc(x.name)}</td><td>${fEsc(x.symbol || "—")}</td><td class="num">${fcr(cr(x))}</td><td class="num">${x.pct == null ? "—" : Number(x.pct).toFixed(2)}</td></tr>`; });
  });
  return s + `</tbody></table></div>`;
}

// Holdings-only cockpit for a fund NOT in the skill store (passive index/ETF + debt). Shows the latest
// book + asset/sector mix + a clear "no manager-skill history" banner, then the skill leaderboard below.
async function fsRenderHoldonly(slug, meta, man) {
  const host = $("fundskill-body"); if (!host) return;
  meta = meta || {};
  let f = await ensureFundsHoldings(slug);
  if (!f) { host.innerHTML = `<div class='empty-note'>No holdings cached for ${fEsc(meta.name || slug)}.</div>` + fsLeaderboardHTML(man || {}); fsWireLeaderboard(); return; }
  const cov = f.coverage || {}, con = f.concentration || {}, aa = f.asset_alloc || {}, sa = f.sector_alloc || {}, H = f.holdings || [];
  const num = (x, s) => (x == null) ? "—" : (x + (s || ""));
  let h = `<div class="quant-head"><div class="qh-name">${fEsc(f.name || meta.name || slug)}${f.amc ? ` <span class="qh-sym">${fEsc(f.amc)}</span>` : ""}</div><div class="qh-px"><span class="fs-badge" style="background:#6e7781">holdings only</span></div></div>`;
  h += `<section class="panel"><div class="q-warn">Passive index/ETF or debt / very-new fund — <b>no manager-skill history</b> (not in the 13-yr active-equity store, so excess/IR/batting can't be measured). Showing the latest disclosed book only.</div>`;
  h += `<div class="q-stats">` + qStat("Holdings", num(con.n_holdings)) + qStat("Top-10 weight", num(con.top10_pct, "%"))
    + qStat("Σ % to NAV", num(cov.pct_sum, "%")) + qStat("Portfolio as on", fEsc(f.asof || "—")) + `</div>`;
  h += `<div class="qgrid2"><div class="qcell"><div class="q-cap">Asset allocation (% of NAV)</div><div class="plot" id="plot-ho-asset" style="height:260px"></div></div>`
    + `<div class="qcell"><div class="q-cap">Equity sector (% of NAV, top 15)</div><div class="plot" id="plot-ho-sector" style="height:260px"></div></div></div></section>`;
  h += `<section class="panel"><h2><span class="tag-sec">HOLDINGS</span>Full book — ${H.length} positions</h2>${_holdoTable(H)}</section>`;
  host.innerHTML = h + fsLeaderboardHTML(man || {});
  fsWireLeaderboard();
  try {
    const aKeys = Object.keys(aa);
    if (aKeys.length) {
      const lm = { equity: "Equity", debt: "Debt", money_market: "Money mkt", cash: "Cash & equiv.", mf_units: "MF/REIT", derivative: "Derivatives", other: "Other" };
      Plotly.react("plot-ho-asset", [{ type: "bar", x: aKeys.map((k) => lm[k] || k), y: aKeys.map((k) => aa[k]), marker: { color: "#1f77b4" }, hovertemplate: "%{x}: %{y}%<extra></extra>" }], baseLayout({ yaxis: { title: "% of NAV", gridcolor: "#dfe3e8" } }), PCONF);
    }
    const sKeys = Object.keys(sa);
    if (sKeys.length) {
      const sk = sKeys.slice().sort((a, b) => sa[a] - sa[b]).slice(-15);
      Plotly.react("plot-ho-sector", [{ type: "bar", orientation: "h", x: sk.map((k) => sa[k]), y: sk, marker: { color: "#8c564b" }, hovertemplate: "%{y}: %{x}%<extra></extra>" }], baseLayout({ xaxis: { title: "% of NAV", gridcolor: "#dfe3e8" }, margin: { l: 170, r: 12, t: 10, b: 36 } }), PCONF);
    } else { const el = $("plot-ho-sector"); if (el) el.innerHTML = "<div class='empty-note'>no equity sector data (a debt / liquid fund)</div>"; }
  } catch (e) { console.error("fsRenderHoldonly charts:", e); }
}

async function initFundSkill() {
  const tab = $("tabs") && $("tabs").querySelector('[data-view="fundskill"]');
  const man = fundsAttrManifest() || {};
  const hold = fundsHoldonlyManifest() || {};
  const keys = Object.keys(man);
  const anyFund = keys.length > 0 || Object.keys(hold).length > 0;
  if (tab) { tab.disabled = !anyFund; tab.style.opacity = anyFund ? "" : ".45"; tab.title = anyFund ? "" : "No fund data in this deck yet"; }
  if (!anyFund) return;
  const items = keys.map((k) => ({ sym: k, name: (man[k].name || k), disp: (man[k].name || k), sub: (man[k] || {}).category || "" }));
  // holdings-only funds (passive index/ETF + debt) — latest book only, flagged in the picker
  Object.keys(hold).forEach((s) => items.push({ sym: s, name: (hold[s].name || s), disp: (hold[s].name || s), sub: ((hold[s].amc || "") + " · holdings only").trim() }));
  items.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  if (!FUNDSKILL_SYM) FUNDSKILL_SYM = keys.length ? keys.slice().sort((a, b) => ((man[b] || {}).t_stat || -99) - ((man[a] || {}).t_stat || -99))[0] : Object.keys(hold)[0];
  if ($("fundskill-combo")) { FUNDSKILL_COMBO = new ComboBox($("fundskill-combo"), { placeholder: "fund name…", hideSym: true, onPick: (v) => { FUNDSKILL_SYM = v || FUNDSKILL_SYM; FS_WIN = null; FS_VANT.cat = null; renderFundSkill(); writeHash(); } }); FUNDSKILL_COMBO.setItems(items); FUNDSKILL_COMBO.setValue(FUNDSKILL_SYM); }
}

async function initFundamentals() {
  if (!FUND_DATA && !OFFLINE) {
    try { const r = await (await fetch("/api/fundamentals")).json(); FUND_DATA = (r && !r.error) ? r : null; } catch (e) { FUND_DATA = null; }
  }
  const tab = $("tabs") && $("tabs").querySelector('[data-view="fundamentals"]');
  const manifest = fundManifest();   // hosted: every company is searchable; data fetched on open
  const syms = manifest ? Object.keys(manifest).sort() : (FUND_DATA ? Object.keys(FUND_DATA).sort() : []);
  if (tab) { const any = syms.length > 0; tab.disabled = !any; tab.style.opacity = any ? "" : ".45"; tab.title = any ? "" : "No Screener fundamentals in this deck yet"; }
  if (!syms.length) return;
  const nameOf = (s) => manifest ? (manifest[s] || "") : ((FUND_DATA && FUND_DATA[s] && FUND_DATA[s].name) || "");
  const items = syms.map((s) => { const nm = nameOf(s); return { sym: s, name: nm, disp: nm ? s + " — " + nm : s }; });
  FUND_SYM = syms[0];
  if ($("fund-combo")) { FUND_COMBO = new ComboBox($("fund-combo"), { placeholder: "name, ticker or acronym…", onPick: (v) => { FUND_SYM = v || syms[0]; renderFundamentals(); writeHash(); } }); FUND_COMBO.setItems(items); FUND_COMBO.setValue(FUND_SYM); }
  if ($("fund-combo2")) { FUND_COMBO2 = new ComboBox($("fund-combo2"), { placeholder: "add a 2nd company…", allowNone: true, onPick: (v) => { FUND_SYM2 = v; renderFundamentals(); } }); FUND_COMBO2.setItems(items); }
  const seg = $("fund-stmt-seg");
  if (seg) seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    if (b.disabled) return;
    seg.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); FUND_STMT = b.dataset.s; renderFundTable(fundSelectedSyms());
  }));
  buildFundDom();
}

// ---- statement parsers (robust to Screener's non-breaking spaces / '+' suffixes) ----
function fNum(x) { if (x === null || x === undefined) return null; const s = String(x).replace(/,/g, "").replace(/%/g, "").replace(/[\s ]/g, "").trim(); const v = parseFloat(s); return Number.isFinite(v) ? v : null; }
function _fnorm(s) { return String(s === null || s === undefined ? "" : s).replace(/[\s ]+/g, "").toLowerCase(); }
function fRow(rows, key) { if (!rows) return null; key = _fnorm(key); for (const r of rows) { const lab = _fnorm(r["Unnamed: 0"] !== undefined ? r["Unnamed: 0"] : Object.values(r)[0]); if (lab.includes(key)) return r; } return null; }
function fYears(rows, key) { const r = fRow(rows, key); if (!r) return { years: [], vals: [] }; const years = Object.keys(r).filter((k) => k !== "Unnamed: 0"); return { years, vals: years.map((y) => fNum(r[y])) }; }
function fyLabel(y) { return String(y).replace("Mar ", "FY"); }
function fCagr(vals) { const v = (vals || []).filter((x) => x !== null && x > 0); if (v.length < 2) return null; return (Math.pow(v[v.length - 1] / v[0], 1 / (v.length - 1)) - 1) * 100; }
function cagrLast(vals, years) { const v = (vals || []).filter((x) => x !== null && x > 0); if (v.length < 2) return null; const n = Math.min(years, v.length - 1); return (Math.pow(v[v.length - 1] / v[v.length - 1 - n], 1 / n) - 1) * 100; }
function lastNonNull(arr) { if (!arr) return null; for (let i = arr.length - 1; i >= 0; i--) if (arr[i] !== null && arr[i] !== undefined) return arr[i]; return null; }
// align series B onto series A's years (both {years,vals}) then combine elementwise
function alignYears(a, b, fn) { return { years: a.years, vals: a.years.map((y, i) => { const j = b.years.indexOf(y); return fn(a.vals[i], j >= 0 ? b.vals[j] : null); }) }; }

// ---- panel catalog (metadata only; renderers are closures built in renderFundamentals) ----
// Every chart panel reads from FUND_DATA[sym].analytics (the Python `fundamentals.compute`
// block — the single source of truth). Modules A–J map onto the analytics keys noted on each
// panel. `kind` controls the DOM slots buildFundDom emits:
//   "plot"  -> a Plotly div (+ optional statline)         "table" -> an HTML table div
//   "both"  -> plot AND table (e.g. Quality: bar + decomposed rows)
// `tog:true` adds a per-panel series "show" bar; `freq3:true` adds an ANNUAL/QUARTERLY/TTM seg.
const FUND_SECTIONS = [
  { key: "valpx", title: "Price & valuation" },
  { key: "estimates", title: "Analyst estimates · LSEG StarMine" },
  { key: "growth", title: "Growth (A)" },
  { key: "margins", title: "Margins & returns (B)" },
  { key: "dupont", title: "DuPont decomposition (C)" },
  { key: "cashq", title: "Cash flow & quality (D)" },
  { key: "balance", title: "Balance sheet & leverage (E)" },
  { key: "quality", title: "Quality & cycle (G·H)" },
  { key: "lens", title: "Factor lens & narrative (I·J)" },
  { key: "own", title: "Ownership" },
];
const FUND_PANELS = [
  // ----------------------------------------------------------- Price & valuation (incl. Module F)
  { id: "price", sec: "valpx", tag: "PRICE", kind: "plot", stat: true, wide: true, dateaxis: true,
    title: "Price · 50 & 200-day moving averages · volume", keys: "price (raw bundle)",
    what: "The share price (line) with its average price over the last 50 and 200 trading days, and daily traded volume as bars beneath.",
    method: "The 200-day average is the long-trend line; price above a rising 200-DMA is the textbook uptrend. The 50-day crossing above the 200-day (a 'golden cross') marks momentum turning up; below it ('death cross'), down. Tall volume bars say a move had conviction behind it.",
    why: "One glance tells you whether the market is accumulating or dumping this stock — the trend you'd be fighting or riding — before you read a single fundamental." },
  { id: "pe", sec: "valpx", tag: "VALUATION · F", kind: "plot", stat: true, dateaxis: true,
    title: "P/E vs its own median P/E", keys: "analytics.valuation.pe_series · .pe_now · .median_pe · .pe_percentile",
    what: "The price-to-earnings multiple through time (solid), with the stock's own long-run median P/E as a flat dashed line for reference.",
    method: "P/E = price ÷ earnings-per-share — the rupees you pay for each rupee of yearly profit. Above the dashed median = the market is paying more than this stock's own norm; below = less. The readout gives today's P/E, the median, and where today sits in its 10-year range (a percentile: 10th = near its cheapest ever, 90th = near its dearest).",
    why: "Anchors 'expensive or cheap' to the stock's OWN history instead of a gut feel — the single cleanest first valuation read." },
  { id: "eps", sec: "valpx", tag: "EPS", kind: "plot", stat: true, dateaxis: true,
    title: "Earnings per share (Rs)", keys: "analytics.valuation snapshot · raw EPS series",
    what: "Earnings per share — net profit divided by the number of shares — plotted over time, in rupees.",
    method: "A line rising steadily is the engine of value; flat or falling EPS means there's nothing underneath for the price to compound on. Watch for downward steps caused by issuing new shares — more shares split the same profit, so EPS falls even if the business didn't.",
    why: "Over years, price follows EPS. If EPS isn't growing, any price rise is just the market paying a richer multiple — which can un-pay just as fast." },
  { id: "ev_ebitda", sec: "valpx", tag: "VALUATION · F", kind: "plot", stat: true, dateaxis: true,
    title: "EV/EBITDA vs its own median", keys: "analytics.valuation.ev_ebitda_series · .now · .median · .percentile",
    what: "Enterprise value (market cap + debt) divided by operating profit (EBITDA) through time, with the stock's own long-run median as a dashed line.",
    method: "EV/EBITDA values the WHOLE business — equity plus debt — against its cash operating profit, so two firms with very different borrowing are judged fairly on the business itself. Lower = cheaper. EV here uses GROSS debt (the data source doesn't isolate cash, so cash isn't netted off — a small upward bias for cash-rich firms). Blank for banks, which have no operating EBITDA.",
    why: "The multiple a strategic or private-equity buyer actually thinks in — it strips out how the company is financed, so the comparison is about the business, not the balance-sheet mix." },
  { id: "ps", sec: "valpx", tag: "VALUATION · F", kind: "plot", stat: true, dateaxis: true,
    title: "Price/Sales vs its own median", keys: "analytics.valuation.ps_series · .now · .median · .percentile",
    what: "Market cap divided by sales (price-to-sales) through time, with the stock's own median as a dashed reference.",
    method: "P/S = what you pay for each rupee of revenue. It keeps working when earnings are tiny, volatile or negative (a loss-maker has no P/E but still has a P/S), so it's the steady valuation anchor through margin cycles and turnarounds. Lower = cheaper — but read it WITH margins: a low P/S on a wafer-thin-margin business isn't truly cheap.",
    why: "Sales are far harder to massage than earnings, so P/S is the most manipulation-resistant first read — and the one that still speaks when the P/E goes blank." },
  { id: "ev_sales", sec: "valpx", tag: "VALUATION · F", kind: "plot", stat: true, dateaxis: true,
    title: "EV/Sales vs its own median", keys: "analytics.valuation.ev_sales_series · .now · .median · .percentile",
    what: "Enterprise value (market cap + debt) divided by sales through time, with the stock's own median dashed.",
    method: "EV/Sales is Price/Sales done at the whole-firm level — it adds debt to the numerator, so a company that looks cheap on P/S only because it is loaded with debt no longer does. Lower = cheaper; gross debt is used (cash not netted). Blank for banks.",
    why: "Catches the leverage that a bare Price/Sales hides — two firms on the same P/S can be very differently valued once their borrowings are counted." },
  { id: "pb", sec: "valpx", tag: "VALUATION · F", kind: "plot", stat: true, dateaxis: true,
    title: "P/B vs its own median", keys: "analytics.valuation.pb_series · .now · .median · .percentile",
    what: "Price-to-book — market cap divided by net worth (shareholders' equity) — through time, with the stock's own median dashed.",
    method: "P/B = what you pay for each rupee of the company's accounting net worth. It's the natural anchor for asset-heavy and financial businesses (banks, NBFCs, capital goods) where book value is real and earnings are lumpy. Read it WITH return on equity: a high P/B is only justified by a high, durable ROE — a bank at 3× book earning 8% ROE is dear; at 3× book earning 18% it can be fair. Lower = cheaper.",
    why: "For banks and asset-heavy firms, P/B paired with ROE is the primary valuation lens — earnings multiples there mislead through the credit cycle." },
  { id: "dy", sec: "valpx", tag: "VALUATION · F", kind: "plot", stat: true, dateaxis: true,
    title: "Dividend yield vs its own median", keys: "analytics.valuation.dy_series · .now · .median · .percentile",
    what: "The dividend yield — annual dividend as a % of the share price — through time, with the stock's own median dashed.",
    method: "Dividend yield = dividend per share ÷ price (derived here as the dividend-payout ratio ÷ P/E). Unlike the multiples, HIGHER is 'cheaper' — you're paid more income per rupee invested. A yield well above its own norm can flag either genuine value or a market doubting the dividend is sustainable; check it against the payout ratio and free cash flow. Near-zero for deliberate non-payers (high-reinvestment compounders).",
    why: "For income and the steadier end of the market, the yield and its trend is the first thing read — a yield spiking above its history is often the earliest 'this looks cheap' signal." },
  { id: "fcfy", sec: "valpx", tag: "VALUATION · F", kind: "plot", stat: true, dateaxis: true,
    title: "Free-cash-flow yield vs its own median", keys: "analytics.valuation.fcfy_series · .now · .median · .percentile",
    what: "Free cash flow as a % of market cap, through time, with the stock's own median dashed.",
    method: "FCF yield = free cash flow (operating cash − capex) ÷ market cap — the cash the business actually throws off per rupee of price, after keeping the lights on and investing. HIGHER = cheaper. It's the multiple hardest to fake: earnings can be flattered, but cash is cash. A high earnings yield with a low FCF yield is the classic warning that profit isn't converting to cash. Lumpy by nature (capex cycles), so read the trend, not one year.",
    why: "Cash, not accounting profit, funds dividends, buybacks and debt repayment — so FCF yield is the truest 'what am I really getting' read." },
  { id: "valsnap", sec: "valpx", tag: "VALUATION · F", kind: "table",
    title: "Valuation snapshot — multiples & yields", keys: "analytics.valuation.snapshot {pe, pb, ev_ebitda, ev_sales, mcap_sales, earnings_yield, fcf_yield, mcap_collected_cr, mktcap_cr, mcap_cohort, mcap_source}",
    what: "Today's full set of valuation multiples and yields in one table — P/E, P/B, EV/EBITDA, EV/Sales, market-cap/Sales, earnings yield, free-cash-flow yield — plus two market-cap rows and the AMFI size cohort, on the latest reported year and latest price.",
    method: "Multiples (P/E, P/B, EV/EBITDA…) are price relative to a fundamental: lower = cheaper. Yields are their inverse and read the opposite way — earnings yield (profit ÷ price) and FCF yield (free cash ÷ price), higher = cheaper. For banks the EBITDA-based rows are blank: a lender has no operating EBITDA, so those multiples are meaningless. Two market-cap rows: 'Market cap (collected)' is the COLLECTED figure — AMFI's published full market cap, or exact NSE issuedSize × NSE price where pulled — never estimated from earnings; 'Market cap (derived, approx)' is the older price × (profit ÷ EPS) reconstruction, kept only as a labelled fallback (and as the consistent driver of the EV/PB/Mcap-ratio rows above). The 'Size cohort' is AMFI's SEBI Large/Mid/Small classification.",
    why: "Cross-checks value from several independent angles at once — a stock can look cheap on P/E yet dear on cash-flow yield, and this is where you catch the disagreement." },
  { id: "peers", sec: "valpx", tag: "PEERS", kind: "table", wide: true,
    title: "Closest 10 peers — valuation & growth", keys: "analytics.peers {sector, self, rows[]}",
    what: "The ten companies closest to this one — same sector, nearest market cap — side by side on the headline valuation multiples (P/E, EV/EBITDA, P/S, EV/Sales, P/B), return on equity, and 3-year sales & earnings growth. This stock's own row is highlighted at the top.",
    method: "Peers are the same-sector names whose market cap is closest to this stock's (size-matched, so it's like-for-like). Each multiple is the latest reported figure; growth is the 3-year compound annual rate of sales and net profit; ROE is the latest return on equity. A multiple well BELOW the peer set while growth/ROE is AT or ABOVE it is the classic 'cheap for no obvious reason' screen; a multiple ABOVE the set is only earned by faster growth or fatter, more durable returns.",
    why: "A multiple means nothing in isolation — 20× is dear for a utility and cheap for a compounder. Peers turn 'expensive or cheap' from a gut call into a relative, like-for-like judgement." },
  // ----------------------------------------------------------- LSEG StarMine — Analyst Revision Model (ARM)
  { id: "arm", sec: "estimates", tag: "LSEG STARMINE · ARM", kind: "both", wide: true, tog: true,
    title: "Analyst Revision Score — headline & the parts driving it", keys: "starmine.headline · starmine.components[]",
    what: "LSEG StarMine's Analyst Revision Score (ARM, 0–100) for this stock — the headline, plus the four component scores that drive it: revisions to preferred earnings (EPS), secondary earnings (EBITDA), revenue, and analyst recommendations.",
    method: "ARM is a region-relative PERCENTILE of analyst estimate-revision momentum: ~100 = analysts are raising estimates the most in the region, near 0 = cutting the most. The component bars show WHICH expectations are moving — a high headline carried mostly by Recommendations but with soft Earnings is a weaker, less durable signal than one led by earnings revisions. The headline is a coverage/profitability-weighted blend of the parts, NOT their literal average.",
    why: "High ARM = analysts are upgrading this stock fastest right now. On our data this carries a real but SMALL average edge over ~1 month (cross-sectional IC ≈ 0.03–0.045 — proven in aggregate, NOT a per-name guarantee) and it MEAN-REVERTS within weeks. Read it as a short-horizon sentiment tilt, not a buy signal. Read it WITH valuation: high ARM on a cheap stock is the constructive combo; high-but-falling ARM is a tailwind already fading." },
  { id: "armts", sec: "estimates", tag: "LSEG STARMINE · ARM", kind: "plot", stat: true, wide: true, dateaxis: true,
    title: "Analyst Revision Score — trajectory (direction matters more than level)", keys: "starmine.headline.series",
    what: "The headline Analyst Revision Score plotted through time, so you read the DIRECTION of analyst sentiment, not just today's dot.",
    method: "ARM mean-reverts, so the slope matters more than the level: a score climbing from the 40s into the 70s is fresh upward-revision momentum; a score sliding down from a peak is momentum fading even while the level still looks high. Treat it as a short-horizon (~1-month) timing overlay, rebalanced often — not a buy-and-hold rating.",
    why: "A point-in-time score can mislead; the trajectory tells you whether the estimate cycle for this stock is turning up or rolling over — the part that actually anticipates the next move." },
  // ----------------------------------------------------------- Module A: GROWTH
  { id: "growthlvl", sec: "growth", tag: "GROWTH · A", kind: "plot", stat: true, tog: true, freq3: true,
    title: "Growth — levels (Sales / EBITDA / PAT / EPS / Net worth / CFO / FCF)",
    keys: "analytics.growth[metric].level · .yoy · .cagr · .ttm · .ttm_yoy; analytics.quarterly[metric]",
    what: "The actual rupee size of each chosen line — sales, operating profit (EBITDA), net profit (PAT), EPS, net worth, operating cash (CFO), free cash (FCF) — through time. The toggle switches Annual / Quarterly / trailing-12-months.",
    method: "Read the SHAPE, not just the slope. Lines rising together and roughly in step = sales pulling profit and cash up with them (healthy). If profit lags sales, margins are leaking; if cash lags profit, earnings aren't turning into money. TTM is the latest run-rate. Bank EBITDA is blank — it isn't a banking concept.",
    why: "Shows whether the business is genuinely getting bigger and whether the top line is reaching the bottom line — the substance behind any growth claim." },
  { id: "growthyoy", sec: "growth", tag: "GROWTH · A", kind: "plot", stat: true, tog: true,
    title: "Growth — YoY % and CAGR table", keys: "analytics.growth[metric].yoy · .accel · .cagr{3y,5y,10y}",
    what: "The year-on-year growth rate (%) of each metric as lines, with its 3-, 5- and 10-year compound annual growth rate (CAGR) summarised below.",
    method: "Each point is this year versus last. A RISING line means growth is accelerating; a falling line means it's fading even while still positive — the inflection that matters. CAGR boils the lumpy path into one 'per-year' number. We leave YoY blank when last year's base was zero or negative, where a % would only mislead.",
    why: "Separates a business that's speeding up from one quietly slowing — usually the thing the market re-rates on, well before the absolute numbers look bad." },
  // ----------------------------------------------------------- Module B: MARGINS & RETURNS
  { id: "margins", sec: "margins", tag: "MARGINS · B", kind: "plot", stat: true, tog: true,
    title: "Margins — OPM · EBIT · PAT (Financing margin for banks)",
    keys: "analytics.margins.{OPM, EBIT margin, PAT margin, Financing margin}",
    what: "Operating (OPM), EBIT and net (PAT) profit each as a % of sales, through time. Banks show Financing margin instead.",
    method: "A margin is how many paise of every sales-rupee the company keeps as profit. Stable or rising margins signal pricing power and cost discipline; a steady slide signals competition or cost pressure. The gap from operating down to net margin is exactly what interest, depreciation and tax consume.",
    why: "Holding margins through a full cycle is the surest fingerprint of a durable franchise — and the rarest, which is why the market pays up for it." },
  { id: "returns", sec: "margins", tag: "RETURNS · B", kind: "plot", stat: true, tog: true,
    title: "Return on capital — ROCE · ROE · ROA", keys: "analytics.margins.{ROCE, ROE, ROA, Operating leverage}",
    what: "Return on capital employed (ROCE), on equity (ROE) and on assets (ROA), each a %, through time.",
    method: "Each is profit divided by the capital used to earn it. ROCE (pre-tax, pre-financing) is the purest read of the business itself; ROE adds the boost from leverage; ROA is the rawest. What you want is a high AND steady ROCE that clears the company's cost of capital — that gap is where value is created.",
    why: "Tells you whether a rupee kept inside this business earns a great return or a mediocre one — the line between a compounder and a value-destroyer." },
  // ----------------------------------------------------------- Module C: DUPONT
  { id: "dupont3", sec: "dupont", tag: "DUPONT · C", kind: "plot", stat: true, tog: true,
    title: "3-step DuPont — ROE = net margin × asset turnover × equity multiplier",
    keys: "analytics.dupont.three_step.{npm, asset_turnover, equity_multiplier, roe}",
    what: "ROE split into its three multiplying parts — net margin × asset turnover × equity multiplier — over time. Their product is exactly the ROE.",
    method: "It answers WHY the ROE is what it is. Net margin = profitability; asset turnover (sales ÷ assets) = how hard the assets are worked; equity multiplier (assets ÷ equity) = how much debt is amplifying the result. ROE built on margin and turnover is earned and durable; ROE built on the multiplier is borrowed and fragile.",
    why: "Two companies with identical ROE can be opposites in quality — this shows which one is a genuinely high-return business and which is just leveraged." },
  { id: "dupont5", sec: "dupont", tag: "DUPONT · C", kind: "plot", stat: true, tog: true,
    title: "5-step DuPont — tax × interest burden × EBIT margin × turnover × leverage",
    keys: "analytics.dupont.five_step.{tax_burden, interest_burden, ebit_margin, asset_turnover, leverage}",
    what: "The finer, five-factor split of ROE — tax burden × interest burden × EBIT margin × asset turnover × leverage.",
    method: "It carves the three-step further to isolate two drags: the tax burden and the interest burden (each a fraction below 1 — the smaller, the bigger the bite). So you can see how much of ROE is the operating engine versus what's left after the taxman and lenders take their cut. The EBIT legs are blank for banks, whose business IS interest.",
    why: "Strips out tax and financing effects so a flattering ROE — propped up by cheap debt or a one-off low-tax year — is exposed for what it is." },
  { id: "waterfall", sec: "dupont", tag: "DUPONT · C", kind: "table",
    title: "Latest-year profit waterfall & EPS bridge", keys: "analytics.dupont.waterfall · .eps_bridge",
    what: "The latest year's income statement as a step-down — Sales → operating profit → pre-tax profit → tax → net profit — plus a bridge explaining the change in EPS.",
    method: "Each step subtracts one block of cost, so you watch exactly where the money goes on its way to net profit. The EPS bridge then splits the year's EPS change into three causes — more revenue, a better margin, or a changed share count — which add back to the total.",
    why: "Turns 'profit rose X%' into 'rose BECAUSE of this' — letting you tell a real gain (more sales, fatter margins) from a cosmetic one (a buyback shrinking the share count)." },
  // ----------------------------------------------------------- Module D: CASH FLOW & QUALITY
  { id: "cashflow", sec: "cashq", tag: "CASH · D", kind: "plot", stat: true, tog: true,
    title: "Cash flow — CFO · FCF · Capex · cumulative CFO vs PAT",
    keys: "analytics.cashflow.{CFO, FCF, Capex, cum_CFO, cum_PAT}",
    what: "Operating cash flow (CFO), free cash flow (FCF) and capex each year, plus two compounding lines — cumulative CFO versus cumulative net profit.",
    method: "CFO is the cash the operations actually generated; FCF is what's left after capex — the cash owners could pocket. If cumulative CFO keeps pace with or beats cumulative profit over the years, the profit is real cash. Capex funded out of CFO (not fresh debt) means growth pays for itself.",
    why: "'Profit is an opinion; cash is a fact.' This is where you verify the profit line isn't an accounting mirage." },
  { id: "earnq", sec: "cashq", tag: "QUALITY · D", kind: "plot", stat: true, tog: true,
    title: "Earnings quality — CFO/PAT · FCF/PAT · Capex/Sales · accrual ratio",
    keys: "analytics.cashflow.{CFO/PAT, FCF/PAT, Capex/Sales, Accrual ratio}",
    what: "Earnings-quality ratios through time: cash conversion (CFO/PAT and FCF/PAT, in ×), capex intensity (capex/sales, %), and the Sloan accrual ratio (%).",
    method: "CFO/PAT around 1 or above means each rupee of reported profit actually arrived as a rupee of cash — high quality. A persistently LOW conversion, or a HIGH accrual ratio (profit driven by book entries rather than cash), is the classic signature of aggressive or fragile accounting.",
    why: "The cheapest, most reliable red-flag screen there is — companies that eventually blow up usually report profits their cash flow quietly failed to confirm for years first." },
  // ----------------------------------------------------------- Module E: BALANCE & LEVERAGE
  { id: "balance", sec: "balance", tag: "BALANCE · E", kind: "plot", stat: true, tog: true,
    title: "Balance sheet — Net worth · Debt · Total assets",
    keys: "analytics.balance.{Net worth, Debt, Total assets}",
    what: "The three balance-sheet totals through time, in crore: net worth (the owners' capital), total debt, and total assets. For banks, 'debt' includes customer deposits.",
    method: "Assets = net worth + what's owed; the split tells you who funds the business — owners or lenders. The warning shapes are debt climbing faster than net worth (leverage creeping up) and assets ballooning without matching profit (growth that isn't earning its keep).",
    why: "The scale and the funding mix are the foundation that decides whether a company rides out a bad year or is forced to raise capital at the worst possible moment." },
  { id: "leverage", sec: "balance", tag: "LEVERAGE · E", kind: "plot", stat: true, tog: true,
    title: "Leverage — D/E · Debt/EBITDA · Interest coverage",
    keys: "analytics.balance.{D/E, Debt/EBITDA, Interest coverage}",
    what: "Three solvency gauges through time: debt-to-equity (×), Debt/EBITDA (× — roughly the years of profit it would take to repay the debt), and interest coverage (× — operating profit ÷ the interest bill).",
    method: "The safe pattern is low and FALLING D/E and Debt/EBITDA alongside high and RISING interest coverage. Coverage under about 2–3× is the danger zone — a downturn or a rate rise could threaten the interest payments themselves. The EBITDA-based ratios are blank for banks, where deposits aren't 'debt' in this sense.",
    why: "Leverage is what turns a rough patch into a solvency crisis — this shows how much cushion exists before the debt becomes the whole story." },
  // ----------------------------------------------------------- Module G: QUALITY (fully decomposed) + H: CYCLE
  { id: "quality", sec: "quality", tag: "QUALITY · G", kind: "both", wide: true,
    title: "Quality scorecard — fully decomposed (no black-box number)",
    keys: "analytics.quality.{score, components:[{label,value,unit,score,weight,note}], method}",
    what: "The composite quality score opened up into every component — each with its raw value, a 0–100 sub-score bar, and its weight. The headline is the explicit average of these, never an opaque number.",
    method: "Each leg scores one dimension — profitability, return on capital, balance-sheet safety, cash conversion and so on — against fixed in-house thresholds, then the legs are averaged. Read the BARS: a high composite with one short bar pinpoints the single weak spot; a low one shows exactly what's dragging it down. Caveat: the thresholds are ABSOLUTE and identical across sectors, so this composite partly ranks SECTOR characteristics, not just company quality; the cut-offs are round-number conventions and the 6 components are equal-weighted (not optimised). A diagnostic, not a sector-neutral quality rank.",
    why: "Lets you trust — or argue with — the quality score by seeing precisely which strengths and weaknesses produced it, rather than accepting a figure you can't audit." },
  { id: "cycle", sec: "quality", tag: "CYCLE · H", kind: "both",
    title: "Cycle position — where it sits vs its OWN history",
    keys: "analytics.cycle.{gauges:[{label,current,percentile,zscore}], flags, method}",
    what: "For margin, return on capital, sales growth, valuation and leverage, where the LATEST reading sits inside the company's own history — as a percentile and a z-score (how many standard deviations from its own mean) — plus any signpost flags.",
    method: "Near the 100th percentile / a high positive z = a record high for that metric; near the bottom = a trough. This is a mean-reversion lens: peak margins and peak valuations tend to fade back, trough margins often recover. The flags are heuristics to notice, not signals to act on.",
    why: "Stops you projecting a top-of-cycle year forever — a 'cheap' P/E sitting on peak-cycle margins is the classic value trap this is built to catch." },
  // ----------------------------------------------------------- Module I: FACTOR LENS + J: NARRATIVE
  { id: "factorlens", sec: "lens", tag: "FACTOR LENS · I", kind: "table", wide: true,
    title: "Factor-informed lens — six axes, own-yardstick (Value · Quality · Profitability · Investment · Momentum · Low-risk)",
    keys: "derived from analytics.valuation / margins / growth / cashflow / cycle (own-history percentiles)",
    what: "Where this one stock leans on the six academic factor axes — Value, Quality, Profitability, Investment, Momentum, Low-risk — judged by its OWN history (a level read plus its own-history percentile), not ranked against a peer panel.",
    method: "Each axis bundles the relevant fundamentals into one plain read — Value from earnings yield + cheapness, Quality from returns + cash conversion, Momentum from sales/EPS growth + its acceleration, and so on. The honest limits are stated up front: it's a single-name lens, not a true cross-sectional factor loading (no peer ranking, no market beta).",
    why: "Translates the company into the language of the factors that academically drive long-run returns (Fama-French, AQR) — without faking the peer cross-section we don't have." },
  { id: "narrative", sec: "lens", tag: "NARRATIVE · J", kind: "table", wide: true,
    title: "Narrative-to-numbers — what the price requires, vs what this firm has delivered",
    keys: "derived from analytics.valuation.{pe_now, median_pe, snapshot.pb, earnings_yield} + growth.cagr + margins.ROE (own history)",
    what: "Reverse-engineers today's price into the future it REQUIRES — the growth, the ROE, and the years of premium it's implicitly paying for — across a 10/12/14% cost-of-equity band, then checks each against what the firm has actually achieved.",
    method: "Instead of guessing a fair price, it asks 'what must come true to justify the price you'd pay today?' and grades that demand against the company's own record: pessimistic (≤25th percentile of its history), supported (25–75th), optimistic (>75th), or unprecedented (beyond anything it has ever done). The cost of equity is shown as a band, never a false single number.",
    why: "Upgrades 'is it expensive?' to the sharper 'is the price paying for a future this company has actually proven it can produce?' — the question that separates a fair price from a hope." },
  // ----------------------------------------------------------- Ownership
  { id: "share", sec: "own", tag: "OWNERSHIP", kind: "plot", tog: true,
    title: "Shareholding pattern", keys: "raw bundle statements.shareholding",
    what: "The % of the company held by Promoters, foreign institutions (FIIs), domestic institutions (DIIs) and the public, quarter by quarter.",
    method: "Rising promoter or institutional stakes mean informed insiders and professionals are buying — generally constructive; a steadily FALLING promoter stake is a skin-in-the-game warning. But read it fairly: a QIP or ESOP issuance dilutes promoter % on paper without any actual selling.",
    why: "Ownership is who's voting with their own money — the people closest to the business adding to or trimming their bet is a signal worth weighing." },
];

// build the Fundamentals sub-nav + section/panel skeleton ONCE. Each panel emits the slots
// its `kind` needs: a toggle bar (tog), a 3-way ANNUAL/QUARTERLY/TTM seg (freq3), a Plotly div
// (plot/both), a statline (stat), and/or an HTML table div (table/both). The Method <details>
// now also carries the analytics keys the panel maps to (KV reporting discipline).
function buildFundDom() {
  const host = $("fund-panels"); if (!host || host.dataset.built) return;
  const nav = $("fund-nav");
  if (nav) nav.innerHTML = FUND_SECTIONS.map((s) => `<a href="#fsec-${s.key}">${s.title}</a>`).join("");
  let html = "";
  FUND_SECTIONS.forEach((s) => {
    html += `<div class="fsec" id="fsec-${s.key}">${s.title}</div><div class="fgrid">`;
    FUND_PANELS.filter((p) => p.sec === s.key).forEach((p) => {
      const hasPlot = p.kind === "plot" || p.kind === "both";
      const hasTable = p.kind === "table" || p.kind === "both";
      const seg = p.freq3 ? `<div class="subctl"><div class="seg fund-freq" data-pid="${p.id}">
          <button data-f="annual" class="active">Annual</button><button data-f="quarterly">Quarterly</button><button data-f="ttm">TTM</button>
        </div></div>` : "";
      const tog = p.tog ? `<div class="seltog" id="tog-fund-${p.id}"></div>` : "";
      const plot = hasPlot ? `<div class="plot" id="plot-fund-${p.id}" style="height:330px"></div>` : "";
      const stat = (p.stat && hasPlot) ? `<div class="statline" id="stat-fund-${p.id}"></div>` : "";
      const tbl = hasTable ? `<div class="fund-tblwrap" id="tbl-fund-${p.id}"></div>` : "";
      html += `<section class="panel fpanel${p.wide ? " wide" : ""}" id="p-fund-${p.id}">
        <h2><span class="tag-sec">${p.tag}</span>${p.title}</h2>
        <details><summary>What it shows · How to read it · Why it matters</summary>
          <p><b>What it shows:</b> ${p.what}</p><p><b>How to read it:</b> ${p.method}</p><p><b>Why it matters:</b> ${p.why}</p>
          ${p.keys ? `<p class="src">Maps to: <code>${p.keys}</code></p>` : ""}</details>
        ${seg}${tog}${plot}${stat}${tbl}
      </section>`;
    });
    html += `</div>`;
  });
  host.innerHTML = html;
  host.dataset.built = "1";
}

function renderFundHeader(syms, C, st) {
  const hdr = $("fund-header"); if (!hdr) return;
  const chip = (l, v, cls) => `<div class="fchip"><span class="fk">${l}</span><span class="fv ${cls || ""}">${v}</span></div>`;
  const mny = (v) => v === null ? "—" : (Math.abs(v) >= 1e5 ? (v / 1e5).toFixed(2) + "L cr" : Math.round(v).toLocaleString() + " cr");
  hdr.innerHTML = syms.map((sym, i) => {
    const b = FUND_DATA[sym], pl = st(sym, "profit_loss"), rat = st(sym, "ratios"), bs = st(sym, "balance_sheet"), sh = st(sym, "shareholding"), val = b.valuation || {}, px = b.price || {};
    const pe = val["Price to Earning"] || [], curPE = pe.length ? pe[pe.length - 1][1] : null;
    const med = val["Median PE"] || [], medPE = med.length ? med.reduce((a, p) => a + (p[1] || 0), 0) / med.length : null;
    const pr = px.Price || [], last = pr.length ? lastNonNull(pr.map((a) => a[1])) : null;
    const wk = pr.slice(-52).map((a) => a[1]).filter((v) => v !== null && v !== undefined);
    const hi = wk.length ? Math.max(...wk) : null, lo = wk.length ? Math.min(...wk) : null;
    const sV = fYears(pl, "Sales").vals, npV = fYears(pl, "Net Profit").vals;
    const sCagr = cagrLast(sV, 5), pCagr = cagrLast(npV, 5);
    const roce = lastNonNull(fYears(rat, "ROCE").vals), opm = lastNonNull(fYears(pl, "OPM").vals);
    const eq = lastNonNull(fYears(bs, "Equity Capital").vals) || 0, res = lastNonNull(fYears(bs, "Reserves").vals) || 0, bor = lastNonNull(fYears(bs, "Borrowings").vals);
    const de = (bor !== null && (eq + res)) ? bor / (eq + res) : null;
    const prom = fRow(sh, "Promoter"), pq = prom ? Object.keys(prom).filter((k) => k !== "Unnamed: 0") : [], promV = (prom && pq.length) ? fNum(prom[pq[pq.length - 1]]) : null;
    return `<div class="fco" style="border-left:4px solid ${C(i)}">
      <div class="fname"><span class="fdot" style="background:${C(i)}"></span>${b.name || sym} <span class="fsym">${sym}${b.consolidated ? " · consolidated" : ""}</span><button class="flink" data-perf="${sym}" title="Chart this stock's price in the Performance tab">Chart price ↗</button></div>
      <div class="fchips">
        ${chip("Price", last !== null ? "Rs " + last.toFixed(1) : "—")}
        ${chip("52-wk range", (lo !== null && hi !== null) ? "Rs " + lo.toFixed(0) + "–" + hi.toFixed(0) : "—")}
        ${chip("P/E", curPE !== null ? curPE.toFixed(1) + "x" : "—", (curPE !== null && medPE !== null) ? (curPE < medPE ? "pos" : "neg") : "")}
        ${chip("Median P/E", medPE !== null ? medPE.toFixed(1) + "x" : "—")}
        ${chip("ROCE", roce !== null ? roce.toFixed(1) + "%" : "—")}
        ${chip("OPM", opm !== null ? opm.toFixed(1) + "%" : "—")}
        ${chip("Sales 5y CAGR", sCagr !== null ? (sCagr >= 0 ? "+" : "") + sCagr.toFixed(1) + "%" : "—", sCagr !== null ? (sCagr >= 0 ? "pos" : "neg") : "")}
        ${chip("Profit 5y CAGR", pCagr !== null ? (pCagr >= 0 ? "+" : "") + pCagr.toFixed(1) + "%" : "—", pCagr !== null ? (pCagr >= 0 ? "pos" : "neg") : "")}
        ${chip("Sales", mny(lastNonNull(sV)))}
        ${chip("Debt/Equity", de !== null ? de.toFixed(2) : "—", de !== null ? (de <= 0.5 ? "pos" : (de >= 1.5 ? "neg" : "")) : "")}
        ${chip("Promoter", promV !== null ? promV.toFixed(1) + "%" : "—")}
      </div></div>`;
  }).join("");
  hdr.querySelectorAll("[data-perf]").forEach((btn) => btn.addEventListener("click", () => gotoPerformance([btn.dataset.perf])));
}

// per-panel ANNUAL/QUARTERLY/TTM toggle state (panel id -> "annual"|"quarterly"|"ttm")
let FUND_FREQ = {};
// dashes to distinguish a 2nd compare company on the same metric line
const FUND_DASH = ["solid", "dot", "dash", "dashdot"];
// HTML-escape for table cells / hover strings built from analytics strings
function fEsc(s) { return String(s === null || s === undefined ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
// read the computed analytics block for a symbol (the Python fundamentals.compute output)
function fAnalytics(sym) { const b = FUND_DATA && FUND_DATA[sym]; const a = b && b.analytics; return (a && a.ok) ? a : null; }
function fStarmine(sym) { const b = FUND_DATA && FUND_DATA[sym]; return (b && b.starmine && b.starmine.headline) ? b.starmine : null; }
// is a metric sub-block bank-nulled? (Python sets `.na` to an explanatory string)
function fNa(obj) { return (obj && typeof obj === "object" && obj.na) ? obj.na : null; }
// pretty number for a value + unit (₹ cr -> L cr, % stays %, × stays ×)
function fVal(v, unit) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (unit === "%") return Number(v).toFixed(1) + "%";
  if (unit === "×" || unit === "x" || unit === "ratio") return Number(v).toFixed(2) + "×";
  if (unit === "₹ cr" || unit === "INR crore") return Math.abs(v) >= 1e5 ? (v / 1e5).toFixed(2) + "L cr" : Math.round(v).toLocaleString() + " cr";
  return Number(v).toFixed(2);
}

async function renderFundamentals() {
  buildFundDom();
  if (LAZY && LAZY.fundamentals) { await ensureFundamentals(FUND_SYM); if (FUND_SYM2) await ensureFundamentals(FUND_SYM2); }
  const syms = fundSelectedSyms();
  if (!FUND_DATA || !syms.length) {
    if ($("fund-header")) $("fund-header").innerHTML = `<div class='empty-note'>No fundamentals cached — run "Pull Screener Fundamentals.bat".</div>`;
    FUND_PANELS.forEach((p) => {
      const pl = $("plot-fund-" + p.id); if (pl) { Plotly.purge(pl); pl.innerHTML = ""; }
      const tb = $("tbl-fund-" + p.id); if (tb) tb.innerHTML = "";
    });
    renderFundTable([]); return;
  }
  FUND_SYM = syms[0];
  const multi = syms.length > 1;
  // ★ PRESERVED: clamp the date-axis time-series panels (price / P-E / EPS) to the shared
  // cross-tab window (#start/#end). Statement-axis panels are annual periods, not dates, so the
  // clamp only applies to the date-stamped series (price, valuation pe_series, EPS series).
  const W0 = $("start").value, W1 = $("end").value;
  const inWin = (d) => (!W0 || d >= W0) && (!W1 || d <= W1);
  const clip = (arr) => (arr || []).filter((a) => a && inWin(a[0]));
  const C = (i) => FUND_COLORS[i % FUND_COLORS.length];
  const st = (sym, sec) => (FUND_DATA[sym].statements && FUND_DATA[sym].statements[sec]) || [];
  const setStat = (id, txt) => { const e = $("stat-fund-" + id); if (e) e.textContent = txt || ""; };
  // purge before react: a prior render left Plotly state on the div; `innerHTML=""` would wipe the
  // SVG but NOT that state, so react() would try to update vanished DOM -> blank on re-select/compare.
  const draw = (id, traces, extra) => { const el = $("plot-fund-" + id); if (el) { Plotly.purge("plot-fund-" + id); Plotly.react("plot-fund-" + id, traces, baseLayout(extra), PCONF); attachYAutoscale("plot-fund-" + id); } };
  const note = (id, msg) => { const el = $("plot-fund-" + id); if (el) { Plotly.purge(el); el.innerHTML = `<div class='empty-note'>${msg}</div>`; } setStat(id, ""); const tb = $("tbl-fund-" + id); if (tb) tb.innerHTML = ""; };
  const setTbl = (id, html) => { const el = $("tbl-fund-" + id); if (el) el.innerHTML = html; };
  // a greyed "n/a — not meaningful for banks" badge using the Python .na text verbatim
  const naBadge = (txt) => `<div class="fund-na">n/a — ${fEsc(txt)}</div>`;

  // build a per-panel "show" toggle bar over a list of [label,colour] items, re-rendering only it
  const buildFundToggle = (pid, items, rerender) => { const el = $("tog-fund-" + pid); if (el) renderToggleBar(el, "fund-" + pid, items, rerender); };
  // wire the ANNUAL/QUARTERLY/TTM 3-way seg for a panel (once), re-rendering that panel on click
  const wireFreq = (pid, rerender) => {
    const seg = document.querySelector(`.fund-freq[data-pid="${pid}"]`); if (!seg || seg.dataset.wired) return;
    seg.dataset.wired = "1";
    seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      seg.querySelectorAll("button").forEach((x) => x.classList.remove("active")); b.classList.add("active");
      FUND_FREQ[pid] = b.dataset.f; rerender();
    }));
  };

  // Plot one analytics metric across the selected companies. `pick(a)` returns the metric block
  // {periods/dates, values, unit, formula, na?} (or null) from a company's analytics. Honors .na
  // (greyed line skipped, badge shown), the formula hovertemplate, and compare dashes.
  const metricTraces = (key, defs, opts) => {
    opts = opts || {};
    const traces = []; let na = null;
    syms.forEach((sym, i) => {
      const a = fAnalytics(sym); if (!a) return;
      defs.forEach((d, j) => {
        const blk = d.pick(a); if (!blk) return;
        if (fNa(blk)) { na = na || fNa(blk); return; }                  // bank-nulled -> badge, no line
        const xs = (opts.dateaxis ? (blk.dates || blk.periods) : blk.periods) || [];
        let ys = blk.values || [];
        if (opts.clip && xs.length) { const keep = xs.map((x) => inWin(x)); ys = ys.map((v, k) => keep[k] ? v : null); }
        if (!ys.some((v) => v !== null && v !== undefined)) return;
        const nm = (multi ? sym + " " : "") + d.lbl;
        const hov = blk.formula ? `${fEsc(nm)}: %{y}<br><span style='font-size:10px'>${fEsc(blk.formula)}</span><extra></extra>` : `${fEsc(nm)}: %{y}<extra></extra>`;
        // Build the trace with ONLY the keys that apply. Never pass `marker:undefined` /
        // `line:undefined`: Plotly's cleanData does `'line' in trace.marker` and throws
        // "Cannot use 'in' operator to search for 'line' in undefined" on an undefined marker
        // (the key exists), blanking EVERY metricTraces panel. (Fixed 2026-06-22.)
        const tr = { type: opts.bar ? "bar" : "scatter", name: nm,
          x: opts.dateaxis ? xs : xs.map(fyLabel), y: ys, opacity: opts.bar ? 0.78 : 1, hovertemplate: hov };
        if (opts.bar) tr.marker = { color: d.col || C(i) };
        else { tr.mode = "lines+markers"; tr.line = { color: d.col || C(i), width: 2, dash: multi ? FUND_DASH[i % FUND_DASH.length] : (d.dash || "solid") }; }
        traces.push(tr);
      });
    });
    return { traces, na };
  };

  renderFundHeader(syms, C, st);

  const R = {};

  // ============================================================ Price · DMA · volume (raw bundle)
  R.price = () => {
    const px = (s) => FUND_DATA[s].price || {};
    if (!syms.some((s) => (px(s).Price || []).length)) { note("price", "No price history embedded for this company."); return; }
    if (!multi) {
      const p = px(syms[0]), pr = clip(p.Price), d50 = clip(p.DMA50), d200 = clip(p.DMA200), vol = clip(p.Volume);
      const traces = [{ type: "scatter", mode: "lines", name: "Price", x: pr.map((a) => a[0]), y: pr.map((a) => a[1]), line: { color: C(0), width: 1.6 } }];
      if (d50.length) traces.push({ type: "scatter", mode: "lines", name: "50-DMA", x: d50.map((a) => a[0]), y: d50.map((a) => a[1]), line: { color: "#d99a2b", width: 1.1 } });
      if (d200.length) traces.push({ type: "scatter", mode: "lines", name: "200-DMA", x: d200.map((a) => a[0]), y: d200.map((a) => a[1]), line: { color: "#b3402f", width: 1.1 } });
      if (vol.length) traces.push({ type: "bar", opacity: 0.74, name: "Volume", x: vol.map((a) => a[0]), y: vol.map((a) => a[1]), yaxis: "y2", marker: { color: "rgba(120,140,160,0.28)" }, hoverinfo: "skip" });
      draw("price", traces, { yaxis: { title: "Price", gridcolor: "#dfe3e8" }, yaxis2: { overlaying: "y", side: "right", showgrid: false, rangemode: "tozero", showticklabels: false }, xaxis: { type: "date", gridcolor: "#dfe3e8", rangeslider: { thickness: 0.06 } } });
      const lst = lastNonNull(pr.map((a) => a[1])), wk = pr.slice(-52).map((a) => a[1]).filter((v) => v !== null && v !== undefined);
      const hi = wk.length ? Math.max(...wk) : null, lo = wk.length ? Math.min(...wk) : null;
      const yrAgo = pr.length > 52 ? pr[pr.length - 53][1] : (pr.length ? pr[0][1] : null);
      const chg = (lst !== null && yrAgo) ? (lst / yrAgo - 1) * 100 : null;
      setStat("price", lst !== null ? `Last Rs ${lst.toFixed(1)} · 52-wk Rs ${lo !== null ? lo.toFixed(0) : "—"}–${hi !== null ? hi.toFixed(0) : "—"} · 1-yr ${chg !== null ? (chg >= 0 ? "+" : "") + chg.toFixed(1) + "%" : "—"}` : "");
    } else {
      const traces = syms.map((sym, i) => { const pr = clip(px(sym).Price), base = pr.find((a) => a[1] !== null && a[1] !== undefined), b0 = base ? base[1] : null; return { type: "scatter", mode: "lines", name: sym, x: pr.map((a) => a[0]), y: pr.map((a) => (a[1] !== null && a[1] !== undefined && b0) ? a[1] / b0 * 100 : null), line: { color: C(i), width: 1.7 } }; });
      draw("price", traces, { yaxis: { title: "Rebased (=100)", gridcolor: "#dfe3e8" }, xaxis: { type: "date", gridcolor: "#dfe3e8", rangeslider: { thickness: 0.06 } } });
      setStat("price", "Prices rebased to 100 at the common start so the two paths are comparable.");
    }
  };

  // ============================================================ Module F: P/E vs median (date-axis, clamped)
  R.pe = () => {
    const traces = [], shapes = [];
    syms.forEach((sym, i) => {
      const a = fAnalytics(sym); if (!a || !a.valuation) return;
      const ser = a.valuation.pe_series || { dates: [], values: [] };
      const x = ser.dates || [], yv = ser.values || [];
      const xs = [], ys = []; for (let k = 0; k < x.length; k++) if (inWin(x[k])) { xs.push(x[k]); ys.push(yv[k]); }
      traces.push({ type: "scatter", mode: "lines", name: multi ? sym : "P/E", x: xs, y: ys, line: { color: C(i), width: 1.4 }, hovertemplate: `${multi ? sym : "P/E"}: %{y}<extra></extra>` });
      const m = a.valuation.median_pe;
      if (m !== null && m !== undefined && xs.length) shapes.push({ type: "line", x0: xs[0], x1: xs[xs.length - 1], y0: m, y1: m, line: { color: C(i), width: 1.2, dash: "dash" } });
    });
    draw("pe", traces, { shapes, yaxis: { title: "P/E (×)", gridcolor: "#dfe3e8" }, xaxis: { type: "date", gridcolor: "#dfe3e8", rangeslider: { thickness: 0.06 } } });
    setStat("pe", syms.map((sym) => { const a = fAnalytics(sym); if (!a || !a.valuation) return `${sym}: —`; const v = a.valuation, cur = v.pe_now, m = v.median_pe, pc = v.pe_percentile; return `${sym}: P/E ${num(cur, 1)}× vs median ${num(m, 1)}× (${num(pc, 0)}ᵗʰ %ile of own history${(cur !== null && m !== null) ? ", " + (cur < m ? "cheaper than its norm" : "richer than its norm") : ""})`; }).join("   ·   "));
  };

  // ============================================================ EPS series (date-axis, clamped)
  R.eps = () => {
    const traces = syms.map((sym, i) => { const e = clip(FUND_DATA[sym].valuation && FUND_DATA[sym].valuation.EPS); return { type: "scatter", mode: "lines", name: multi ? sym : "EPS", x: e.map((a) => a[0]), y: e.map((a) => a[1]), line: { color: C(i), width: 1.8 } }; });
    draw("eps", traces, { yaxis: { title: "EPS (Rs)", gridcolor: "#dfe3e8" }, xaxis: { type: "date", gridcolor: "#dfe3e8" } });
    setStat("eps", syms.map((sym) => { const e = (FUND_DATA[sym].valuation && FUND_DATA[sym].valuation.EPS) || [], l = e.length ? lastNonNull(e.map((a) => a[1])) : null; return `${sym}: EPS ${l !== null ? "Rs " + l.toFixed(1) : "—"}`; }).join("   ·   "));
  };

  // ============================================================ Module F: valuation snapshot (table)
  R.valsnap = () => {
    const ROWS = [["pe", "P/E", "×", "pe"], ["pb", "P/B", "×", "pb"], ["ev_ebitda", "EV/EBITDA", "×", "ev_ebitda"],
      ["ev_sales", "EV/Sales", "×", "ev_sales"], ["mcap_sales", "Mcap/Sales", "×", "mcap_sales"],
      ["earnings_yield", "Earnings yield", "%", "earnings_yield"], ["fcf_yield", "FCF yield", "%", "fcf_yield"],
      ["mcap_collected_cr", "Market cap (collected · AMFI/NSE)", "₹ cr", null],
      ["mktcap_cr", "Market cap (derived, approx)", "₹ cr", null],
      ["mcap_cohort", "Size cohort (AMFI)", "txt", null],
      ["mcap_source", "Market-cap source", "txt", null]];
    let h = `<table class="gauge-tbl"><thead><tr><th>Metric</th>` + syms.map((s) => `<th>${fEsc(s)}</th>`).join("") + `<th>Formula</th></tr></thead><tbody>`;
    ROWS.forEach(([k, lbl, unit, fkey]) => {
      const snaps = syms.map((s) => { const a = fAnalytics(s); return a && a.valuation && a.valuation.snapshot ? a.valuation.snapshot : {}; });
      const a0 = fAnalytics(syms[0]); const formula = (a0 && a0.valuation && a0.valuation.formulas && fkey) ? a0.valuation.formulas[fkey] : "";
      const cell = (sn) => unit === "txt" ? fEsc(sn[k] == null ? "—" : sn[k]) : fVal(sn[k], unit);
      h += `<tr><td class="name">${fEsc(lbl)}</td>` + snaps.map((sn) => `<td>${cell(sn)}</td>`).join("") + `<td class="fmono">${fEsc(formula || "")}</td></tr>`;
    });
    h += `</tbody></table>`;
    setTbl("valsnap", h);
  };

  // ============================================================ Module F: EV/EBITDA · P/S · EV/Sales · P/B · yields (date-axis)
  const valMultiChart = (panelId, seriesKey, label, unit, higherIsCheaper) => {
    unit = unit || "×";
    const traces = [], shapes = [];
    let any = false;
    syms.forEach((sym, i) => {
      const a = fAnalytics(sym); if (!a || !a.valuation) return;
      const ser = a.valuation[seriesKey] || { dates: [], values: [] };
      const x = ser.dates || [], yv = ser.values || [];
      const xs = [], ys = []; for (let k = 0; k < x.length; k++) if (inWin(x[k])) { xs.push(x[k]); ys.push(yv[k]); }
      if (ys.some((v) => v !== null && v !== undefined)) any = true;
      traces.push({ type: "scatter", mode: "lines", name: multi ? sym : label, x: xs, y: ys, line: { color: C(i), width: 1.4 }, connectgaps: false, hovertemplate: `${multi ? sym : label}: %{y}<extra></extra>` });
      const m = ser.median;
      if (m !== null && m !== undefined && xs.length) shapes.push({ type: "line", x0: xs[0], x1: xs[xs.length - 1], y0: m, y1: m, line: { color: C(i), width: 1.2, dash: "dash" } });
    });
    if (!any) { note(panelId, `${label} not meaningful for this company.`); return; }
    draw(panelId, traces, { shapes, yaxis: { title: `${label} (${unit})`, gridcolor: "#dfe3e8" }, xaxis: { type: "date", gridcolor: "#dfe3e8", rangeslider: { thickness: 0.06 } } });
    setStat(panelId, syms.map((sym) => { const a = fAnalytics(sym); const v = a && a.valuation && a.valuation[seriesKey]; if (!v) return `${sym}: —`; const cur = v.now, m = v.median, pc = v.percentile; const cheap = higherIsCheaper ? (cur > m) : (cur < m); return `${sym}: ${label} ${num(cur, 1)}${unit} vs median ${num(m, 1)}${unit} (${num(pc, 0)}ᵗʰ %ile of own history${(cur !== null && cur !== undefined && m !== null && m !== undefined) ? ", " + (cheap ? "cheaper than its norm" : "richer than its norm") : ""})`; }).join("   ·   "));
  };
  R.ev_ebitda = () => valMultiChart("ev_ebitda", "ev_ebitda_series", "EV/EBITDA", "×", false);
  R.ps = () => valMultiChart("ps", "ps_series", "P/S", "×", false);
  R.ev_sales = () => valMultiChart("ev_sales", "ev_sales_series", "EV/Sales", "×", false);
  R.pb = () => valMultiChart("pb", "pb_series", "P/B", "×", false);
  R.dy = () => valMultiChart("dy", "dy_series", "Dividend yield", "%", true);
  R.fcfy = () => valMultiChart("fcfy", "fcfy_series", "FCF yield", "%", true);

  // ============================================================ Closest-10-peers comparison (table)
  R.peers = () => {
    const a = fAnalytics(syms[0]);
    const pk = a && a.peers;
    if (!pk || !(pk.rows && pk.rows.length)) { setTbl("peers", `<div class='empty-note'>No peer set (needs sector + market cap).</div>`); return; }
    const COLS = [["name", "Company", "txt"], ["mcap_cr", "Mcap ₹cr", "int"], ["pe", "P/E", "x"], ["ev_ebitda", "EV/EBITDA", "x"], ["ps", "P/S", "x"], ["ev_sales", "EV/Sales", "x"], ["pb", "P/B", "x"], ["roe", "ROE %", "pct"], ["sales_gr", "Sales 3y", "pct"], ["pat_gr", "PAT 3y", "pct"]];
    const fc = (v, t) => { if (v === null || v === undefined) return "—"; if (t === "txt") return fEsc(v); if (t === "int") return Math.round(v).toLocaleString("en-IN"); if (t === "pct") return num(v, 1) + "%"; return num(v, 1) + "×"; };
    let h = `<div class="fund-sub">Same sector (${fEsc(pk.sector || "—")}), nearest market cap. This stock highlighted.</div>`;
    h += `<table class="gauge-tbl peers-tbl"><thead><tr>` + COLS.map(([k, l]) => `<th>${fEsc(l)}</th>`).join("") + `</tr></thead><tbody>`;
    const rows = [Object.assign({ _self: true }, pk.self || {}), ...pk.rows];
    rows.forEach((r) => {
      h += `<tr${r._self ? ' class="peer-self"' : ""}>` + COLS.map(([k, l, t]) => `<td${t === "txt" ? ' class="name"' : ""}>${fc(r[k], t)}</td>`).join("") + `</tr>`;
    });
    h += `</tbody></table>`;
    setTbl("peers", h);
  };

  // ============================================================ LSEG StarMine ARM — headline + sum-of-parts
  R.arm = () => {
    if (!syms.some((s) => fStarmine(s))) { note("arm", "No LSEG StarMine coverage for this company."); return; }
    const CATS = ["Headline ARM", "Pref earnings", "Sec earnings", "Revenue", "Recommendations"];
    const KEYS = ["ARM_PREF_EARN_COMP_100", "ARM_SEC_EARN_COMP_100", "ARM_REVENUE_COMP_100", "ARM_REC_COMP_100"];
    // master date grid for the time-nav = the headline series of the first covered stock (≈25 months baked)
    const base = fStarmine(syms.find((s) => fStarmine(s)));
    const master = (base && base.headline && Array.isArray(base.headline.series)) ? base.headline.series.map((a) => a[0]) : [];
    const r1 = (v) => (v === null || v === undefined) ? null : Math.round(v * 10) / 10;
    const drawBars = () => {
      const at = (ARM_HIST_DATE && master.length) ? ARM_HIST_DATE : (master.length ? master[master.length - 1] : null);
      const atLatest = !at || (master.length && at === master[master.length - 1]);
      const traces = syms.map((s, i) => {
        const sm = fStarmine(s); if (!sm) return null;
        const cm = {}; (sm.components || []).forEach((c) => { cm[c.key] = c; });
        const hv = atLatest ? sm.headline.score : r1(seriesValAt(sm.headline.series, at));
        const ys = [hv].concat(KEYS.map((k) => { const c = cm[k]; if (!c) return null; return atLatest ? c.score : r1(seriesValAt(c.series, at)); }));
        const tr = { type: "bar", name: multi ? s : "ARM", x: CATS, y: ys, opacity: 0.85,
          hovertemplate: `${multi ? fEsc(s) + " " : ""}%{x}: %{y}<extra></extra>` };
        tr.marker = multi ? { color: C(i) } : { color: ["#c9a23a", "#5b8db8", "#5b8db8", "#5b8db8", "#5b8db8"] };
        return tr;
      }).filter(Boolean);
      draw("arm", traces, { barmode: "group", yaxis: { title: "0–100 (region percentile)", range: [0, 100], gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
      const dash = (t) => (t === null || t === undefined) ? "—" : (t > 0 ? `▲ +${t}` : (t < 0 ? `▼ ${t}` : "flat"));
      const cell = (v) => (v === null || v === undefined) ? "—" : v;
      const rows = syms.map((s) => { const sm = fStarmine(s); if (!sm) return ""; const h = sm.headline;
        const hAt = atLatest ? h.score : r1(seriesValAt(h.series, at));
        return `<tr><td class="name">${fEsc(s)}</td><td><b>${cell(hAt)}</b></td><td>${cell(h.global)}</td><td>${cell(h.bucket5)}/5</td><td>${dash(h.trend_30d)}</td><td>${dash(h.trend_90d)}</td><td>${fEsc(sm.read)}</td></tr>`;
      }).join("");
      const c0 = fStarmine(syms[0]) || base;
      setTbl("arm", `<table class="gauge-tbl"><thead><tr><th>Stock</th><th>ARM @ date</th><th>Global</th><th>1–5</th><th>30d</th><th>90d</th><th>Read</th></tr></thead><tbody>${rows}</tbody></table>`
        + `<div class="src">Parts shown <b>as of ${fEsc(at || "—")}</b> (drag the date slider above) · Global/1–5/30d/90d are latest · latest ${fEsc(c0.asof)} · Source: ${fEsc(c0.source)}. ${fEsc(c0.usage)}</div>`);
    };
    const host = $("tog-fund-arm");
    if (host && master.length) {
      const idx = (ARM_HIST_DATE && master.indexOf(ARM_HIST_DATE) >= 0) ? master.indexOf(ARM_HIST_DATE) : null;
      dateNavControl(host, master, idx, (k) => { ARM_HIST_DATE = master[k]; drawBars(); });
    }
    drawBars();
  };

  // ============================================================ LSEG StarMine ARM — trajectory (date-axis)
  R.armts = () => {
    if (!syms.some((s) => fStarmine(s))) { note("armts", "No LSEG StarMine coverage for this company."); return; }
    const COMPS = [["Pref earnings", "ARM_PREF_EARN_COMP_100", "#5b8db8"], ["Sec earnings", "ARM_SEC_EARN_COMP_100", "#2e8b57"],
                   ["Revenue", "ARM_REVENUE_COMP_100", "#9467bd"], ["Recommendations", "ARM_REC_COMP_100", "#d99a2b"]];
    let traces;
    if (!multi) {                                   // single stock: headline + all 4 components (legend tick/untick)
      const sm = fStarmine(syms.find((s) => fStarmine(s)));
      const cm = {}; (sm.components || []).forEach((c) => { cm[c.key] = c; });
      const hs = clip(sm.headline.series);
      traces = [{ type: "scatter", mode: "lines", name: "Headline ARM", x: hs.map((a) => a[0]), y: hs.map((a) => a[1]), line: { color: "#c9a23a", width: 2.2 } }];
      COMPS.forEach(([lbl, key, col]) => {
        const c = cm[key]; if (!c || !Array.isArray(c.series) || !c.series.length) return;
        const s2 = clip(c.series);
        traces.push({ type: "scatter", mode: "lines", name: lbl, x: s2.map((a) => a[0]), y: s2.map((a) => a[1]), line: { color: col, width: 1.3, dash: "dot" } });
      });
    } else {                                        // multi-stock compare: one headline line per stock
      traces = syms.map((s, i) => { const sm = fStarmine(s); if (!sm) return null; const ser = clip(sm.headline.series);
        return { type: "scatter", mode: "lines", name: s + " (headline)", x: ser.map((a) => a[0]), y: ser.map((a) => a[1]), line: { color: C(i), width: 1.8 } };
      }).filter(Boolean);
    }
    draw("armts", traces, { showlegend: true, yaxis: { title: "ARM (0–100)", range: [0, 100], gridcolor: "#dfe3e8" }, xaxis: { type: "date", gridcolor: "#dfe3e8", rangeslider: { thickness: 0.06 } } });
    const sm0 = fStarmine(syms[0]); if (sm0) setStat("armts", (multi ? "" : "Headline + the 4 component lines — click any in the legend to show/hide. ") + `${syms[0]}: ${sm0.read} (as of ${sm0.asof})`);
  };

  // ============================================================ Module A: growth levels (3-way freq)
  R.growthlvl = () => {
    wireFreq("growthlvl", R.growthlvl);
    const freq = FUND_FREQ.growthlvl || "annual";
    const METS = ["Sales", "EBITDA", "PAT", "EPS", "Net worth", "CFO", "FCF"];
    const COL = { Sales: "#2ca02c", EBITDA: "#1f77b4", PAT: "#ff7f0e", EPS: "#9467bd", "Net worth": "#8c564b", CFO: "#17becf", FCF: "#d62728" };
    buildFundToggle("growthlvl", METS.map((m) => [m, COL[m]]), R.growthlvl);
    let na = null;
    if (freq === "ttm") {                       // TTM read-out table inside the plot div (numeric)
      let h = `<table class="gauge-tbl"><thead><tr><th>Metric</th>` + syms.map((s) => `<th>${fEsc(s)} TTM</th><th>${fEsc(s)} TTM YoY</th></tr>`).join("") + `</tr></thead><tbody>`;
      METS.filter((m) => shown("fund-growthlvl", m)).forEach((m) => {
        h += `<tr><td class="name">${m}</td>` + syms.map((s) => { const a = fAnalytics(s), g = a && a.growth && a.growth[m]; if (g && fNa(g)) return `<td colspan="2" class="namuted">n/a (bank)</td>`; return `<td>${g ? fVal(g.ttm, "₹ cr") : "—"}</td><td>${g && g.ttm_yoy !== null && g.ttm_yoy !== undefined ? (g.ttm_yoy * 100).toFixed(1) + "%" : "—"}</td>`; }).join("") + `</tr>`;
      });
      const el = $("plot-fund-growthlvl"); if (el) { Plotly.purge(el); el.innerHTML = h + `</tbody></table>`; }
      setStat("growthlvl", "TTM = sum of the last 4 quarters (flow); TTM YoY vs the prior-year TTM (quarters −8..−5).");
      return;
    }
    const defs = METS.filter((m) => shown("fund-growthlvl", m)).map((m) => ({ lbl: m, col: COL[m],
      pick: (a) => { const g = a.growth && a.growth[m]; if (!g) return null; if (fNa(g)) return g;
        if (freq === "quarterly") { const q = a.quarterly && a.quarterly[m]; return q ? { periods: a.quarterly.periods, values: q.level, na: q.na } : null; }
        return g.level; } }));
    const { traces, na: na2 } = metricTraces("growthlvl", defs, { bar: !multi && defs.length <= 2 });
    na = na2;
    if (!traces.length) { note("growthlvl", na ? na : "No growth data."); return; }
    draw("growthlvl", traces, { barmode: "group", hovermode: multi ? "x unified" : "closest", yaxis: { title: "₹ cr", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
    setStat("growthlvl", (freq === "quarterly" ? "Quarterly level. " : "Annual level. ") + (na ? "(Some metrics n/a for banks.) " : "") + syms.map((s) => { const a = fAnalytics(s); const g = a && a.growth && a.growth.Sales; const c = g && g.cagr; return c ? `${s}: Sales 5y CAGR ${c["5y"] !== null && c["5y"] !== undefined ? (c["5y"] * 100).toFixed(1) + "%" : "—"}` : ""; }).filter(Boolean).join("   ·   "));
  };

  // ============================================================ Module A: growth YoY + CAGR
  R.growthyoy = () => {
    const METS = ["Sales", "EBITDA", "PAT", "EPS"];
    const COL = { Sales: "#2ca02c", EBITDA: "#1f77b4", PAT: "#ff7f0e", EPS: "#9467bd" };
    buildFundToggle("growthyoy", METS.map((m) => [m, COL[m]]), R.growthyoy);
    const defs = METS.filter((m) => shown("fund-growthyoy", m)).map((m) => ({ lbl: m + " YoY", col: COL[m],
      pick: (a) => { const g = a.growth && a.growth[m]; if (!g) return null; if (fNa(g)) return g;
        const yoy = g.yoy || {}; return { periods: yoy.periods, values: (yoy.values || []).map((v) => v === null || v === undefined ? null : v * 100), formula: "YoY = value / prior − 1 (None off a ≤0 base)" }; } }));
    const { traces, na } = metricTraces("growthyoy", defs, {});
    if (!traces.length) { note("growthyoy", na ? na : "No growth data."); return; }
    draw("growthyoy", traces, { yaxis: { title: "% YoY", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
    setStat("growthyoy", syms.map((s) => { const a = fAnalytics(s); if (!a || !a.growth) return ""; return s + " CAGR — " + METS.map((m) => { const g = a.growth[m]; if (!g || fNa(g) || !g.cagr) return null; const c = g.cagr; const f = (x) => (x === null || x === undefined) ? "—" : (x * 100).toFixed(0) + "%"; return `${m} 3/5/10y ${f(c["3y"])}/${f(c["5y"])}/${f(c["10y"])}`; }).filter(Boolean).join(", "); }).filter(Boolean).join("   |   "));
  };

  // ============================================================ Module B: margins
  R.margins = () => {
    const a0 = fAnalytics(syms[0]); const bank = a0 && a0.is_bank;
    const METS = bank ? [["Financing margin", "#1f77b4"], ["PAT margin", "#ff7f0e"], ["OPM", "#2ca02c"], ["EBIT margin", "#9467bd"]]
                      : [["OPM", "#2ca02c"], ["EBIT margin", "#9467bd"], ["PAT margin", "#ff7f0e"]];
    buildFundToggle("margins", METS, R.margins);
    const defs = METS.filter(([m]) => shown("fund-margins", m)).map(([m, col]) => ({ lbl: m, col, pick: (a) => (a.margins && a.margins[m]) || null }));
    const { traces, na } = metricTraces("margins", defs, {});
    if (na) setTbl("margins", naBadge(na)); else setTbl("margins", "");
    if (!traces.length) { note("margins", na ? na : "No margin data."); return; }
    draw("margins", traces, { yaxis: { title: "%", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
    setStat("margins", syms.map((s) => { const a = fAnalytics(s); const m = a && a.margins && (bank ? a.margins["Financing margin"] : a.margins.OPM); return m ? `${s}: ${bank ? "Fin. margin" : "OPM"} ${num(lastNonNull(m.values), 1)}%` : ""; }).filter(Boolean).join("   ·   "));
  };

  // ============================================================ Module B: ROCE/ROE/ROA + op leverage
  R.returns = () => {
    const METS = [["ROCE", "#2ca02c"], ["ROE", "#1f77b4"], ["ROA", "#9467bd"], ["Operating leverage", "#8c564b"]];
    buildFundToggle("returns", METS, R.returns);
    const defs = METS.filter(([m]) => shown("fund-returns", m)).map(([m, col]) => ({ lbl: m, col,
      pick: (a) => { const o = a.margins && a.margins[m]; if (!o) return null; if (fNa(o)) return o;
        return { periods: o.periods, values: o.values, unit: o.unit, formula: o.formula, na: o.na }; } }));
    const { traces, na } = metricTraces("returns", defs, {});
    if (na) setTbl("returns", naBadge(na)); else setTbl("returns", "");
    if (!traces.length) { note("returns", na ? na : "No return data."); return; }
    draw("returns", traces, { yaxis: { title: "% (op-leverage = ×)", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
    setStat("returns", syms.map((s) => { const a = fAnalytics(s); const r = a && a.margins && a.margins.ROCE; const v = r && lastNonNull(r.values); return r ? `${s}: ROCE ${num(v, 1)}% (Screener-reported)` : ""; }).filter(Boolean).join("   ·   "));
  };

  // ============================================================ Module C: 3-step DuPont
  R.dupont3 = () => {
    const LEGS = [["npm", "Net margin", "#2ca02c"], ["asset_turnover", "Asset turnover", "#1f77b4"], ["equity_multiplier", "Equity multiplier", "#9467bd"], ["roe", "ROE (product)", "#d62728"]];
    buildFundToggle("dupont3", LEGS.map((l) => [l[1], l[2]]), R.dupont3);
    const a0 = fAnalytics(syms[0]); const f = a0 && a0.dupont && a0.dupont.three_step && a0.dupont.three_step.formula;
    const defs = LEGS.filter(([k, lbl]) => shown("fund-dupont3", lbl)).map(([k, lbl, col]) => ({ lbl, col,
      pick: (a) => { const d = a.dupont && a.dupont.three_step; if (!d) return null; return { periods: a.dupont.periods, values: d[k], formula: f }; } }));
    const { traces } = metricTraces("dupont3", defs, {});
    if (!traces.length) { note("dupont3", "No DuPont data."); return; }
    draw("dupont3", traces, { yaxis: { title: "factor / ROE (fraction)", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
    setStat("dupont3", "ROE = net margin × asset turnover × equity multiplier (identity holds to machine precision).");
  };

  // ============================================================ Module C: 5-step DuPont
  R.dupont5 = () => {
    const LEGS = [["tax_burden", "Tax burden", "#2ca02c"], ["interest_burden", "Interest burden", "#1f77b4"], ["ebit_margin", "EBIT margin", "#9467bd"], ["asset_turnover", "Asset turnover", "#ff7f0e"], ["leverage", "Leverage", "#8c564b"]];
    buildFundToggle("dupont5", LEGS.map((l) => [l[1], l[2]]), R.dupont5);
    const a0 = fAnalytics(syms[0]); const five0 = a0 && a0.dupont && a0.dupont.five_step;
    if (five0 && fNa(five0)) { setTbl("dupont5", naBadge(fNa(five0))); note("dupont5", fNa(five0)); return; } else setTbl("dupont5", "");
    const f = five0 && five0.formula;
    const defs = LEGS.filter(([k, lbl]) => shown("fund-dupont5", lbl)).map(([k, lbl, col]) => ({ lbl, col,
      pick: (a) => { const d = a.dupont && a.dupont.five_step; if (!d) return null; if (fNa(d)) return d; return { periods: a.dupont.periods, values: d[k], formula: f }; } }));
    const { traces, na } = metricTraces("dupont5", defs, {});
    if (na) { setTbl("dupont5", naBadge(na)); }
    if (!traces.length) { note("dupont5", na ? na : "No DuPont data."); return; }
    draw("dupont5", traces, { yaxis: { title: "factor (fraction)", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
    setStat("dupont5", "ROE = tax burden × interest burden × EBIT margin × asset turnover × leverage.");
  };

  // ============================================================ Module C: profit waterfall + EPS bridge (table)
  R.waterfall = () => {
    let h = "";
    syms.forEach((sym) => {
      const a = fAnalytics(sym); const w = a && a.dupont && a.dupont.waterfall, eb = a && a.dupont && a.dupont.eps_bridge;
      if (multi) h += `<div class="ftco">${fEsc(sym)}</div>`;
      if (!w) { h += `<div class='empty-note'>No waterfall for ${fEsc(sym)}.</div>`; return; }
      const row = (l, v, u) => `<tr><td class="name">${fEsc(l)}</td><td class="r">${fVal(v, u || "₹ cr")}</td></tr>`;
      h += `<table class="gauge-tbl"><thead><tr><th>Profit waterfall — ${fEsc(w.period || "")}</th><th class="r">₹ cr</th></tr></thead><tbody>`;
      h += row("Sales", w.sales) + row("− Expenses", w.expenses) + row("= Operating profit", w.operating_profit) + row("+ Other income", w.other_income) + row("− Interest", w.interest) + row("− Depreciation", w.depreciation) + row("= PBT", w.pbt) + row("− Tax", w.tax) + row("= PAT", w.pat);
      h += `</tbody></table>`;
      if (eb) {
        const r2 = (l, v) => `<tr><td class="name">${fEsc(l)}</td><td class="r">${v === null || v === undefined ? "—" : v.toFixed(2)}</td></tr>`;
        h += `<table class="gauge-tbl" style="margin-top:8px"><thead><tr><th>EPS bridge (Rs/sh)</th><th class="r">Δ</th></tr></thead><tbody>`;
        h += r2("From EPS", eb.from_eps) + r2("+ Revenue effect", eb.revenue_effect) + r2("+ Margin effect", eb.margin_effect) + r2("+ Share-count effect", eb.share_count_effect) + r2("Residual", eb.residual) + r2("= To EPS", eb.to_eps);
        h += `</tbody></table>`;
      }
    });
    setTbl("waterfall", h || `<div class='empty-note'>No data.</div>`);
  };

  // ============================================================ Module D: cash flow
  R.cashflow = () => {
    const METS = [["CFO", "#2ca02c"], ["FCF", "#1f77b4"], ["Capex", "#ff7f0e"], ["cum_CFO", "#17becf"], ["cum_PAT", "#8c564b"]];
    buildFundToggle("cashflow", METS, R.cashflow);
    const defs = METS.filter(([m]) => shown("fund-cashflow", m)).map(([m, col]) => ({ lbl: m, col,
      pick: (a) => { const cf = a.cashflow; if (!cf) return null;
        if (m === "cum_CFO" || m === "cum_PAT") return { periods: cf.periods, values: cf[m], formula: "Cumulative sum (compounding cash vs profit)" };
        const o = cf[m]; return o ? { periods: cf.periods, values: o.values, unit: o.unit, formula: o.formula } : null; } }));
    const { traces } = metricTraces("cashflow", defs, {});
    if (!traces.length) { note("cashflow", "No cash-flow data."); return; }
    draw("cashflow", traces, { yaxis: { title: "₹ cr", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
    setStat("cashflow", "Capex ≈ CFO − FCF. Cumulative CFO tracking cumulative PAT = real cash compounding.");
  };

  // ============================================================ Module D: earnings quality
  R.earnq = () => {
    const METS = [["CFO/PAT", "#2ca02c"], ["FCF/PAT", "#1f77b4"], ["Capex/Sales", "#ff7f0e"], ["Accrual ratio", "#d62728"]];
    buildFundToggle("earnq", METS, R.earnq);
    const defs = METS.filter(([m]) => shown("fund-earnq", m)).map(([m, col]) => ({ lbl: m, col,
      pick: (a) => (a.cashflow && a.cashflow[m]) || null }));
    const { traces } = metricTraces("earnq", defs, {});
    if (!traces.length) { note("earnq", "No quality data."); return; }
    draw("earnq", traces, { yaxis: { title: "× / %", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
    setStat("earnq", syms.map((s) => { const a = fAnalytics(s); const o = a && a.cashflow && a.cashflow["CFO/PAT"]; return o ? `${s}: CFO/PAT ${num(lastNonNull(o.values), 2)}×` : ""; }).filter(Boolean).join("   ·   "));
  };

  // ============================================================ Module E: balance sheet
  R.balance = () => {
    const METS = [["Net worth", "#1f77b4"], ["Debt", "#d62728"], ["Total assets", "#8c564b"]];
    buildFundToggle("balance", METS, R.balance);
    const defs = METS.filter(([m]) => shown("fund-balance", m)).map(([m, col]) => ({ lbl: m, col, pick: (a) => (a.balance && a.balance[m]) || null }));
    const { traces } = metricTraces("balance", defs, {});
    if (!traces.length) { note("balance", "No balance-sheet data."); return; }
    draw("balance", traces, { yaxis: { title: "₹ cr", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
    setStat("balance", syms.map((s) => { const a = fAnalytics(s); const d = a && a.balance && a.balance["D/E"]; return d && !fNa(d) ? `${s}: D/E ${num(lastNonNull(d.values), 2)}×` : ""; }).filter(Boolean).join("   ·   "));
  };

  // ============================================================ Module E: leverage (bank-nulled metrics greyed)
  R.leverage = () => {
    const METS = [["D/E", "#1f77b4"], ["Debt/EBITDA", "#ff7f0e"], ["Interest coverage", "#2ca02c"]];
    buildFundToggle("leverage", METS, R.leverage);
    const defs = METS.filter(([m]) => shown("fund-leverage", m)).map(([m, col]) => ({ lbl: m, col, pick: (a) => (a.balance && a.balance[m]) || null }));
    const { traces, na } = metricTraces("leverage", defs, {});
    if (na) setTbl("leverage", naBadge(na)); else setTbl("leverage", "");
    if (!traces.length) { note("leverage", na ? na : "No leverage data."); return; }
    draw("leverage", traces, { yaxis: { title: "×", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
    setStat("leverage", na ? "Debt/EBITDA & interest coverage are not meaningful for banks (greyed)." : "Low, falling leverage with rising coverage is the safe pattern.");
  };

  // ============================================================ Module G: Quality scorecard (FULLY DECOMPOSED)
  R.quality = () => {
    // compare-mode: render the primary company's full decomposition (one scorecard); both shown in table.
    const a = fAnalytics(syms[0]); const q = a && a.quality;
    if (!q || !q.components) { note("quality", "No quality scorecard."); setTbl("quality", ""); return; }
    const comps = q.components;
    // bar chart of each component's 0-100 score (the visible decomposition, never a lone number)
    const labels = comps.map((c) => c.label), scores = comps.map((c) => c.score);
    const colorOf = (s) => s === null || s === undefined ? "#bbb" : (s >= 66 ? "#2e7d52" : (s >= 33 ? "#d99a2b" : "#b3402f"));
    draw("quality", [{ type: "bar", orientation: "h", x: scores, y: labels, marker: { color: scores.map(colorOf) }, text: scores.map((s) => s === null ? "n/a" : Math.round(s)), textposition: "auto", hovertemplate: "%{y}: score %{x}/100<extra></extra>" }],
      { margin: { l: 220, r: 18, t: 10, b: 30 }, xaxis: { title: "component score (0–100)", range: [0, 100], gridcolor: "#dfe3e8" }, yaxis: { automargin: true }, showlegend: false });
    // full table: label, raw value+unit, 0-100 score BAR, weight, note — composite shown explicitly as the average
    let h = `<table class="gauge-tbl"><thead><tr><th>Component</th><th>Raw value</th><th style="min-width:160px">Score (0–100)</th><th>Weight</th><th>How it scores</th></tr></thead><tbody>`;
    comps.forEach((c) => {
      const bar = (c.score === null || c.score === undefined) ? `<span class="namuted">n/a</span>`
        : `<div class="gauge-bar"><div class="gauge-pin" style="left:${Math.max(0, Math.min(100, c.score))}%"></div></div><div style="font-size:10.5px;color:#62707d;margin-top:2px">${Math.round(c.score)}/100</div>`;
      h += `<tr><td class="name">${fEsc(c.label)}</td><td>${fVal(c.value, c.unit)}</td><td>${bar}</td><td>${c.weight === null || c.weight === undefined ? "—" : c.weight}</td><td class="fnote">${fEsc(c.note || "")}</td></tr>`;
    });
    // composite row spelled out as the explicit mean of the scored components (NO black box)
    const scored = comps.filter((c) => c.score !== null && c.score !== undefined);
    const avg = scored.length ? (scored.reduce((s, c) => s + c.score, 0) / scored.length) : null;
    h += `<tr class="frowtot"><td class="name">COMPOSITE</td><td colspan="2">= average of the ${scored.length} scored components above = (${scored.map((c) => Math.round(c.score)).join(" + ")}) / ${scored.length}</td><td colspan="2"><b>${avg === null ? "—" : avg.toFixed(1)}</b> / 100${(q.score !== null && q.score !== undefined) ? ` <span class="namuted">(engine: ${q.score})</span>` : ""}</td></tr>`;
    h += `</tbody></table><p class="fnote" style="margin-top:6px">${fEsc(q.method || "")}</p>`;
    if (multi) { const a2 = fAnalytics(syms[1]); if (a2 && a2.quality) h += `<p class="fnote" style="margin-top:4px">${fEsc(syms[1])} composite: <b>${a2.quality.score}</b>/100 (open ${fEsc(syms[1])} alone to see its full decomposition).</p>`; }
    setTbl("quality", h);
  };

  // ============================================================ Module H: cycle gauges + flags
  R.cycle = () => {
    const a = fAnalytics(syms[0]); const cy = a && a.cycle;
    if (!cy || !cy.gauges) { note("cycle", "No cycle data."); setTbl("cycle", ""); return; }
    const g = cy.gauges;
    // percentile bar chart (where each gauge sits in its OWN history)
    draw("cycle", [{ type: "bar", orientation: "h", x: g.map((x) => x.percentile), y: g.map((x) => x.label), marker: { color: g.map((x) => x.percentile === null ? "#bbb" : "#1f77b4") }, text: g.map((x) => x.percentile === null ? "—" : Math.round(x.percentile) + "%"), textposition: "auto", hovertemplate: "%{y}: %{x}ᵗʰ percentile<extra></extra>" }],
      { margin: { l: 150, r: 18, t: 10, b: 30 }, xaxis: { title: "percentile in own history", range: [0, 100], gridcolor: "#dfe3e8" }, yaxis: { automargin: true }, showlegend: false });
    let h = `<table class="gauge-tbl"><thead><tr><th>Gauge</th><th>Current</th><th>Percentile</th><th>z-score</th></tr></thead><tbody>`;
    g.forEach((x) => { h += `<tr><td class="name">${fEsc(x.label)}</td><td>${num(x.current, 2)}</td><td>${num(x.percentile, 0)}ᵗʰ</td><td>${num(x.zscore, 2)}</td></tr>`; });
    h += `</tbody></table>`;
    if (cy.flags && cy.flags.length) h += `<ul class="fflags">` + cy.flags.map((f) => `<li>${fEsc(f)}</li>`).join("") + `</ul>`;
    h += `<p class="fnote" style="margin-top:4px">${fEsc(cy.method || "")}</p>`;
    setTbl("cycle", h);
  };

  // ============================================================ Module I: factor-informed lens (own-yardstick)
  R.factorlens = () => {
    const a = fAnalytics(syms[0]);
    if (!a) { setTbl("factorlens", `<div class='empty-note'>No analytics for ${fEsc(syms[0])}.</div>`); return; }
    const v = a.valuation || {}, snap = v.snapshot || {}, mg = a.margins || {}, gr = a.growth || {}, cf = a.cashflow || {}, cy = a.cycle || {};
    const gaugeP = (lbl) => { const x = (cy.gauges || []).find((q) => q.label === lbl); return x ? x.percentile : null; };
    const med = (arr) => { const p = (arr || []).filter((x) => x !== null && x !== undefined).sort((m, n) => m - n); if (!p.length) return null; const k = p.length >> 1; return p.length % 2 ? p[k] : (p[k - 1] + p[k]) / 2; };
    const cagr5pct = (m) => { const g = gr[m]; const c = g && g.cagr && g.cagr["5y"]; return (c === null || c === undefined) ? null : c * 100; };
    const cheap = (v.pe_percentile === null || v.pe_percentile === undefined) ? null : (100 - v.pe_percentile);
    // null-safe "x%" / "x×" formatters (a bare `null*100` would print "0.0%", a silent error)
    const pStr = (x, d) => (x === null || x === undefined || Number.isNaN(x)) ? "—" : x.toFixed(d === undefined ? 1 : d) + "%";
    const rocOrRoe = (mg.ROCE && mg.ROCE.values) || (mg.ROE && mg.ROE.values);
    const salesYoyLast = (gr.Sales && gr.Sales.yoy) ? lastNonNull(gr.Sales.yoy.values) : null;
    const AX = [
      ["Value", "Earnings yield " + fVal(snap.earnings_yield, "%") + "; P/E " + num(v.pe_now, 1) + "× vs median " + num(v.median_pe, 1) + "×", cheap, "cheapness percentile = 1 − P/E percentile (own history). Higher = cheaper vs its own past.", "FF HML / Asness Value"],
      ["Quality (QMJ)", "Median ROCE/ROE " + pStr(med(rocOrRoe)) + "; CFO/PAT " + num(med(cf["CFO/PAT"] && cf["CFO/PAT"].values), 2) + "×", med(rocOrRoe), "Profitability + cash conversion + stability, each shown raw (see Quality scorecard).", "AQR QMJ"],
      ["Profitability", "ROCE " + pStr(lastNonNull(mg.ROCE && mg.ROCE.values)) + "; ROE " + pStr(lastNonNull(mg.ROE && mg.ROE.values)) + "; ROA " + pStr(lastNonNull(mg.ROA && mg.ROA.values)), gaugeP("ROCE/ROE"), "Own-history percentile of ROCE/ROE (cycle gauge). See DuPont for the why.", "FF RMW / Novy-Marx"],
      ["Investment", "Accrual ratio " + pStr(lastNonNull(cf["Accrual ratio"] && cf["Accrual ratio"].values)) + "; capex/sales " + pStr(lastNonNull(cf["Capex/Sales"] && cf["Capex/Sales"].values)), null, "Conservative (low asset growth / capex / accruals) = the FF winning side. Percentile needs a peer panel (gap).", "FF CMA"],
      ["Momentum", "Sales YoY " + pStr(salesYoyLast === null ? null : salesYoyLast * 100) + "; EPS 5y CAGR " + pStr(cagr5pct("EPS")), gaugeP("Sales growth (YoY)"), "Fundamental (earnings) momentum. Price momentum needs daily price + a benchmark to be relative (gap).", "Jegadeesh-Titman / AQR"],
      ["Low-risk", "Leverage D/E " + num(lastNonNull(a.balance && a.balance["D/E"] && a.balance["D/E"].values), 2) + "× + margin stability (see Quality)", null, "Standalone risk (earnings stability, leverage). True market beta / BAB needs the index panel (gap).", "BAB / low-vol"],
    ];
    let h = `<table class="gauge-tbl"><thead><tr><th>Axis</th><th>This name (level)</th><th style="min-width:150px">Own-history percentile</th><th>Read</th><th>Factor</th></tr></thead><tbody>`;
    AX.forEach(([axis, level, pctl, read, fac]) => {
      const bar = (pctl === null || pctl === undefined) ? `<span class="namuted">peer panel needed</span>`
        : `<div class="gauge-bar"><div class="gauge-pin" style="left:${Math.max(0, Math.min(100, pctl))}%"></div></div><div style="font-size:10.5px;color:#62707d;margin-top:2px">${Math.round(pctl)}ᵗʰ percentile</div>`;
      h += `<tr><td class="name">${fEsc(axis)}</td><td>${fEsc(level)}</td><td>${bar}</td><td class="fnote">${fEsc(read)}</td><td class="namuted">${fEsc(fac)}</td></tr>`;
    });
    h += `</tbody></table><p class="fnote" style="margin-top:6px"><b>Honest gaps:</b> this is a single-name, own-yardstick lens — NOT a cross-sectional factor loading. A true factor z-score needs a peer universe; market beta (BAB), CAPM alpha and relative momentum need the NSE index series joined; the risk-free rate isn't in the bundle. Where a peer/market input is required the percentile is marked "peer panel needed".</p>`;
    if (multi) h += `<p class="fnote">Showing ${fEsc(syms[0])} (the primary). Open a single company to read its lens in isolation.</p>`;
    setTbl("factorlens", h);
  };

  // ============================================================ Module J: narrative-to-numbers
  R.narrative = () => {
    const a = fAnalytics(syms[0]);
    if (!a || !a.valuation) { setTbl("narrative", `<div class='empty-note'>No valuation for ${fEsc(syms[0])}.</div>`); return; }
    const v = a.valuation, snap = v.snapshot || {}, mg = a.margins || {}, gr = a.growth || {};
    const pe = v.pe_now, pb = snap.pb, medpe = v.median_pe;
    // payout: median of the Dividend Payout% line isn't in analytics; use a robust default of 0.5 of earnings
    // as the cash stream when a payout isn't surfaced — STATED as an assumption (KV discipline).
    const roeHist = (mg.ROE && mg.ROE.values) || [];
    const med = (arr) => { const p = (arr || []).filter((x) => x !== null && x !== undefined).sort((m, n) => m - n); if (!p.length) return null; const k = p.length >> 1; return p.length % 2 ? p[k] : (p[k - 1] + p[k]) / 2; };
    const roeMed = med(roeHist);          // % units
    const roeClean = roeHist.filter((x) => x !== null && x !== undefined);
    const roeMin = roeClean.length ? Math.min(...roeClean) : null, roeMax = roeClean.length ? Math.max(...roeClean) : null;
    const epsCagr = gr.EPS && gr.EPS.cagr && gr.EPS.cagr["5y"] !== null && gr.EPS.cagr["5y"] !== undefined ? gr.EPS.cagr["5y"] : null;  // fraction
    const RBAND = [0.10, 0.12, 0.14];
    const payout = 0.5;                     // stated assumption: half of earnings as the cash stream
    const fmtPctBand = (fn) => RBAND.map((r) => { const x = fn(r); return x === null ? "—" : (x * 100).toFixed(1) + "%"; }).join(" / ");
    // J1 implied perpetual growth g = r − payout/(P/E)
    const g1 = (r) => (pe && pe > 0) ? (r - payout / pe) : null;
    // J2 implied ROE = r + P/B·(r − g), g pinned to sustainable = roeMed·(1−payout)
    const gsust = (roeMed !== null) ? (roeMed / 100) * (1 - payout) : null;
    const roeImp = (r) => (pb !== null && pb !== undefined && gsust !== null) ? (r + pb * (r - gsust)) : null;
    // J3 ROE − earnings-yield gap (WACC-free)
    const ey = snap.earnings_yield;        // % units
    const roeLast = lastNonNull(roeHist);  // %
    const gap = (roeLast !== null && ey !== null && ey !== undefined) ? (roeLast - ey) : null;
    // J4 implied premium years = ln(P/E ÷ median) ÷ ln(1+g_hist)
    const years = (pe && medpe && epsCagr !== null && (1 + epsCagr) > 0 && epsCagr !== 0) ? Math.log(pe / medpe) / Math.log(1 + epsCagr) : null;
    // verdict for a driver vs the firm's own history band
    const verdict = (implied, hist) => { const p = (hist || []).filter((x) => x !== null && x !== undefined); if (implied === null || !p.length) return ["—", "namuted"]; const lo = Math.min(...p), hi = Math.max(...p), mn = med(p); if (implied > hi) return ["unprecedented vs own record", "neg"]; if (implied > mn) return ["optimistic", "neg"]; if (implied < lo) return ["pessimistic / possibly cheap", "pos"]; return ["supported by its history", "pos"]; };
    const g1mid = g1(0.12);                 // fraction
    const vJ1 = verdict(g1mid !== null ? g1mid * 100 : null, (gr.EPS && gr.EPS.yoy && gr.EPS.yoy.values || []).map((x) => x === null ? null : x * 100));
    const roeImid = roeImp(0.12);           // fraction
    const vJ2 = verdict(roeImid !== null ? roeImid * 100 : null, roeHist);
    let h = `<table class="gauge-tbl"><thead><tr><th>Identity</th><th>What the price requires</th><th>Firm's own record</th><th>Verdict</th></tr></thead><tbody>`;
    h += `<tr><td class="name">J1 — implied perpetual growth<br><span class="fnote">g = r − payout/(P/E)</span></td><td>${fmtPctBand((r) => g1(r))}<br><span class="fnote">at r = 10 / 12 / 14%; payout=${payout} (assumed)</span></td><td>EPS 5y CAGR ${epsCagr === null ? "—" : (epsCagr * 100).toFixed(1) + "%"}</td><td class="${vJ1[1]}">${fEsc(vJ1[0])}</td></tr>`;
    h += `<tr><td class="name">J2 — implied ROE<br><span class="fnote">ROE = r + P/B·(r − g)</span></td><td>${fmtPctBand((r) => roeImp(r))}<br><span class="fnote">P/B ${num(pb, 2)}×; g=${gsust === null ? "—" : (gsust * 100).toFixed(1) + "%"}</span></td><td>ROE median ${num(roeMed, 1)}% (range ${num(roeMin, 0)}–${num(roeMax, 0)}%)</td><td class="${vJ2[1]}">${fEsc(vJ2[0])}</td></tr>`;
    h += `<tr><td class="name">J3 — ROE − earnings-yield gap<br><span class="fnote">WACC-free (most robust)</span></td><td>ROE ${num(roeLast, 1)}% − E/P ${num(ey, 1)}% = <b>${num(gap, 1)}pp</b></td><td>franchise premium the market charges</td><td class="namuted">large gap = big franchise premium priced in</td></tr>`;
    h += `<tr><td class="name">J4 — implied premium years<br><span class="fnote">ln(P/E ÷ median) ÷ ln(1+g)</span></td><td>${years === null ? "—" : years.toFixed(1) + " yrs"}<br><span class="fnote">P/E ${num(pe, 1)}× vs median ${num(medpe, 1)}×; g=${epsCagr === null ? "—" : (epsCagr * 100).toFixed(1) + "%"}</span></td><td>P/E ${num(v.pe_percentile, 0)}ᵗʰ %ile of own history</td><td class="namuted">${years !== null && years < 0 ? "below normal multiple (de-rating priced)" : "years of above-trend growth front-loaded"}</td></tr>`;
    h += `</tbody></table><p class="fnote" style="margin-top:6px"><b>Method:</b> cost of equity r is shown as a STATED band (10/12/14%), never a single point — J1/J2 are r-sensitive; J3/J4 import no r and are the most robust. Payout is assumed at ${payout} of earnings (the bundle's payout isn't surfaced in the analytics block); g for J2 is pinned to the firm's realised sustainable growth = median ROE × (1−payout). Verdicts compare the r=12% implied driver to the firm's OWN realised distribution (≤min=pessimistic, ≤median=supported, >median=optimistic, >max=unprecedented).</p>`;
    if (multi) h += `<p class="fnote">Showing ${fEsc(syms[0])} (the primary).</p>`;
    setTbl("narrative", h);
  };

  // ============================================================ Ownership (raw bundle)
  R.share = () => {
    let traces;
    if (!multi) { const sh = st(syms[0], "shareholding"); traces = []; [["Promoter", "#8c564b"], ["FII", "#1f77b4"], ["DII", "#2ca02c"], ["Public", "#7f7f7f"]].forEach(([g, col]) => { const r = fRow(sh, g); if (!r) return; const qs = Object.keys(r).filter((k) => k !== "Unnamed: 0"); traces.push({ type: "scatter", mode: "lines+markers", name: g, x: qs, y: qs.map((q) => fNum(r[q])), line: { color: col, width: 2 } }); }); }
    else { traces = syms.map((sym, i) => { const r = fRow(st(sym, "shareholding"), "Promoter"); if (!r) return null; const qs = Object.keys(r).filter((k) => k !== "Unnamed: 0"); return { type: "scatter", mode: "lines+markers", name: sym + " Promoter", x: qs, y: qs.map((q) => fNum(r[q])), line: { color: C(i), width: 2 } }; }).filter(Boolean); }
    if (!traces.length) { note("share", "No shareholding data for this company."); return; }
    draw("share", traces, { yaxis: { title: "% holding", gridcolor: "#dfe3e8" }, xaxis: { gridcolor: "#dfe3e8" } });
  };

  FUND_PANELS.forEach((p) => { try { if (R[p.id]) R[p.id](); } catch (e) { note(p.id, "—"); } });
  renderFundTable(syms);
  afterPaint(() => viewPlotsResize("fundamentals"));
}

function renderFundTable(syms) {
  const el = $("fund-table"); if (!el) return;
  const sec = FUND_STMT || "quarters";
  if (!FUND_DATA || !syms || !syms.length) { el.innerHTML = ""; return; }
  let h = "";
  syms.forEach((sym) => {
    const rows = (FUND_DATA[sym] && FUND_DATA[sym].statements && FUND_DATA[sym].statements[sec]) || [];
    if (syms.length > 1) h += `<div class="ftco">${sym}</div>`;
    if (!rows.length) { h += `<div class='empty-note'>No ${sec.replace("_", " ")} data for ${sym}.</div>`; return; }
    const cols = Object.keys(rows[0]);
    h += "<table class='ftable'><thead><tr>" + cols.map((c, i) => `<th${i ? " class='r'" : ""}>${c === "Unnamed: 0" ? "" : c}</th>`).join("") + "</tr></thead><tbody>";
    rows.forEach((r) => { h += "<tr>" + cols.map((c, i) => `<td${i ? " class='r'" : ""}>${(r[c] === null || r[c] === undefined) ? "" : r[c]}</td>`).join("") + "</tr>"; });
    h += "</tbody></table>";
  });
  el.innerHTML = h;
}

// =================================================================== Macro tab (India-first + global)
// India-native series come from window.VISTAS_MACRO (built by vistas/macro.py — CPI/WPI
// inflation, policy & market rates, money & credit, the external sector, real activity,
// FII/DII flows). The world/cross-asset frame (window.VISTAS_WORLD) supplies the global
// comparators. India panels render FIRST; any panel with no data hides itself.
function worldFrameM() { return (typeof window !== "undefined" && window.VISTAS_WORLD) ? window.VISTAS_WORLD : null; }
function macroFrameI() { return (typeof window !== "undefined" && window.VISTAS_MACRO) ? window.VISTAS_MACRO : null; }
function macroFrame(src) { return src === "india" ? macroFrameI() : worldFrameM(); }
function macroSlice(name, src) {
  const w = macroFrame(src); if (!w || !w.series[name]) return null;
  const s = $("start").value, e = $("end").value, arr = w.series[name], x = [], y = [];
  for (let i = 0; i < w.dates.length; i++) { const d = w.dates[i]; if ((!s || d >= s) && (!e || d <= e)) { x.push(d); y.push(arr[i]); } }
  return { x, y };
}
function macroMeta(name) { const w = macroFrameI(); return (w && w.meta && w.meta[name]) || null; }

const MACRO_PANELS = [
  // ============================== INDIA (native) ==============================
  { id: "infl", region: "INDIA", tag: "INFLATION", src: "india", unit: "% YoY",
    title: "Inflation — CPI headline & core, Rural/Urban, vs WPI", source: "MOSPI eSankhyiki (CPI, official) · Commerce/OEA via data.gov.in (WPI)",
    series: [["CPI inflation — Combined (YoY)", "#111"], ["CPI Combined — Core ex food & fuel inflation (YoY, est.)", "#7b1fa2"], ["CPI inflation — Rural (YoY)", "#b3402f"], ["CPI inflation — Urban (YoY)", "#d99a2b"], ["WPI inflation (YoY)", "#1f77b4"]],
    what: "Year-on-year change in the All-India CPI — Combined (the headline the RBI targets), an estimated Core (ex food & fuel), Rural and Urban — and the WPI. CPI is MOSPI's official published inflation from eSankhyiki, current to the latest month; the core is a derived ex-food-&-fuel estimate (official granular weights aren't published, so it reproduces the General index to within ~0.5 index points); WPI is from OGD and lags (~Oct-2023).",
    why: "CPI-Combined is what the RBI targets (4% ±2%); the Rural/Urban split shows where price pressure sits; WPI captures producer-side pressure that often leads CPI." },
  { id: "wpicomp", region: "INDIA", tag: "WPI MIX", src: "india", unit: "index (2011-12=100)",
    title: "WPI by group — primary, fuel, manufactured", source: "Commerce/OEA, via data.gov.in",
    series: [["WPI — All commodities (index)", "#111"], ["WPI — Primary articles (index)", "#2ca02c"], ["WPI — Fuel & power (index)", "#b3402f"], ["WPI — Manufactured products (index)", "#1f77b4"]],
    what: "The Wholesale Price Index headline and its three component groups, as index levels (base 2011-12 = 100).",
    why: "Splitting WPI shows what's driving wholesale inflation — food/primary, energy, or core manufacturing — which maps to different policy responses." },
  { id: "inrates", region: "INDIA", tag: "RATES", src: "india", unit: "%",
    title: "Policy & market rates", source: "RBI (repo) · FBIL (yields)",
    series: [["RBI repo rate", "#b3402f", { step: true }], ["India 10Y G-sec yield", "#1f77b4"], ["91-day T-bill yield", "#2ca02c"], ["Call money rate (WACR)", "#9aa6b2"]],
    what: "The RBI repo rate (the policy rate, drawn as a step line — it only moves on MPC decisions) with the benchmark 10-year G-sec yield and the 91-day T-bill. Repo = RBI's official rate (via the BIS central-bank policy-rate dataset, sourced from RBI).",
    why: "How tight is money. The spread of the 10Y over the repo shows what the bond market expects from inflation and growth ahead; the T-bill tracks the repo closely at the short end." },
  { id: "incurve", region: "INDIA", tag: "G-SEC CURVE", src: "india", unit: "yield (%)",
    title: "India G-sec yield curve & the 10Y − 1Y slope", source: "FBIL par-yield curve",
    series: [["India 1Y G-sec yield", "#9aa6b2"], ["India 5Y G-sec yield", "#2ca02c"], ["India 10Y G-sec yield", "#1f77b4"], ["India 30Y G-sec yield", "#8c564b"]],
    curve: ["India 10Y G-sec yield", "India 1Y G-sec yield"], y2title: "slope (pp)",
    what: "The FBIL par-yield curve at the 1Y, 5Y, 10Y and 30Y points; the dashed line (right axis) is the 10Y-minus-1Y slope. Month-end sampled from 2023.",
    why: "The curve's steepness is the bond market's growth/inflation read — a flat or inverted front end signals tight policy; a steep long end signals fiscal-supply or inflation worries." },
  { id: "money", region: "INDIA", tag: "CREDIT", src: "india", unit: "₹ crore",
    title: "Bank credit, deposits & money supply (levels)", source: "RBI Weekly Statistical Supplement",
    series: [["SCB — Bank Credit (Rs crore)", "#b3402f"], ["SCB — Aggregate Deposits (Rs crore)", "#2ca02c"], ["Money supply M3 (Rs crore)", "#1f77b4"]],
    what: "Scheduled-commercial-bank bank credit and aggregate deposits, and broad money (M3), as ₹-crore levels (RBI weekly statistical supplement). Shown as levels — the WSS history pulled is not yet long enough for a clean 1-year YoY growth.",
    why: "Credit is the real-economy pulse — rising bank credit means firms/households are borrowing and investing; the credit-vs-deposit gap drives the credit-to-deposit ratio (see Derived signals); M3 is the broad-money backdrop." },
  { id: "reserves", region: "INDIA", tag: "RESERVES", src: "india", unit: "USD bn",
    title: "Foreign-exchange reserves — total & composition", source: "RBI Weekly Statistical Supplement",
    series: [["Forex reserves — Total (USD bn)", "#1f77b4"], ["Forex reserves — Foreign Currency Assets (USD bn)", "#2ca02c"], ["Forex reserves — Gold (USD bn)", "#d99a2b"]],
    what: "India's total foreign-exchange reserves and its two largest components — Foreign Currency Assets and Gold — in USD billion (RBI weekly statistical supplement).",
    why: "The war-chest that lets the RBI defend the rupee. Falling reserves during rupee weakness signal active intervention; a rising gold share signals reserve diversification." },
  { id: "trade", region: "INDIA", tag: "EXTERNAL", src: "india", unit: "USD mn", y2title: "trade balance (USD mn)",
    title: "External trade — exports, imports & balance", source: "Commerce Ministry, via data.gov.in",
    series: [["Merchandise exports", "#2ca02c"], ["Merchandise imports", "#b3402f"], ["Merchandise trade balance", "#1f77b4", { bar: true, axis: "y2" }]],
    what: "Monthly merchandise exports and imports (lines) and the trade balance (bars, right axis).",
    why: "A widening trade deficit pressures the current account and the rupee; the export trend tracks global demand for Indian goods." },
  { id: "activity", region: "INDIA", tag: "ACTIVITY", src: "india", unit: "index (2011-12=100)",
    title: "Industrial production (IIP)", source: "MOSPI, via data.gov.in",
    series: [["IIP — General", "#111"], ["IIP — Manufacturing", "#1f77b4"], ["IIP — Mining", "#8c564b"], ["IIP — Electricity", "#d99a2b"]],
    what: "Index of Industrial Production — the general index and its mining / manufacturing / electricity sub-indices (base 2011-12 = 100). OGD's copy runs to Feb-2023.",
    why: "A timely read on factory-floor output and the industrial cycle, months before GDP confirms it." },
  { id: "flows", region: "INDIA", tag: "FLOWS", src: "india", unit: "₹ crore", bars: true,
    title: "Institutional flows — FII vs DII", source: "NSE",
    series: [["FII/FPI net (cash)", "#1f77b4"], ["DII net (cash)", "#d99a2b"]],
    what: "Daily net cash-market buying by foreign (FII/FPI) and domestic (DII) institutions, in ₹ crore.",
    why: "Who is actually moving the market. FIIs drive momentum and the rupee; DIIs (mutual funds, insurers) increasingly absorb FII selling." },
  // ============================== GLOBAL (cross-asset) ==============================
  { id: "yields", region: "GLOBAL", tag: "US RATES", src: "world", unit: "yield (%)",
    title: "US Treasury yields & the curve (10Y − 3M)",
    series: [["US 13-week T-bill yield", "#9aa6b2"], ["US 5Y Treasury yield", "#2ca02c"], ["US 10Y Treasury yield", "#1f77b4"], ["US 30Y Treasury yield", "#8c564b"]],
    curve: ["US 10Y Treasury yield", "US 13-week T-bill yield"], y2title: "slope (pp)",
    what: "US Treasury yields across maturities; the dashed line (right axis) is the 10Y-minus-3M curve slope.",
    why: "The curve's shape is the market's growth/recession signal — a negative 10Y−3M (inverted) has preceded most US recessions, and US rates set the global cost of capital that India competes with." },
  { id: "fx", region: "GLOBAL", tag: "CURRENCY", src: "world", unit: "rebased (=100)", rebase: true,
    title: "The rupee & the dollar",
    series: [["USD / INR", "#b3402f"], ["US Dollar Index (DXY)", "#1f77b4"], ["EUR / INR", "#2ca02c"]],
    what: "USD/INR, the broad Dollar Index (DXY) and EUR/INR, rebased to 100 at the window start.",
    why: "A rising DXY (global dollar strength) usually pressures the rupee and emerging-market assets — watch them together." },
  { id: "commod", region: "GLOBAL", tag: "COMMODITIES", src: "world", unit: "rebased (=100)", rebase: true,
    title: "Commodities — gold, oil, copper",
    series: [["Gold", "#d4af37"], ["Crude Oil (Brent)", "#555"], ["Copper", "#b87333"]],
    what: "Gold, Brent crude and copper, rebased to 100.",
    why: "Gold = haven / real-rate gauge; oil = inflation & India's import bill; copper = global industrial demand ('Dr. Copper')." },
  { id: "vol", region: "GLOBAL", tag: "RISK", src: "world", unit: "index level",
    title: "Volatility — VIX & India VIX",
    series: [["CBOE Volatility Index (VIX)", "#b3402f"], ["India VIX", "#d99a2b"]],
    what: "US equity volatility (VIX) and India VIX.",
    why: "Spikes mark risk-off stress; the level shows how fearful or complacent markets are." },
  { id: "credit", region: "GLOBAL", tag: "CREDIT", src: "world", unit: "rebased (=100)", rebase: true,
    title: "Global credit — high-yield vs investment-grade",
    series: [["US High-Yield Credit (HYG)", "#b3402f"], ["US Investment-Grade Credit (LQD)", "#1f77b4"], ["EM USD Sovereign Bond (EMB)", "#2ca02c"]],
    what: "Total-return proxies for US high-yield (HYG), investment-grade (LQD) and EM sovereign (EMB) bonds.",
    why: "High-yield underperforming investment-grade signals widening credit spreads — an early global risk-off tell." },
  { id: "crypto", region: "GLOBAL", tag: "CRYPTO", src: "world", unit: "rebased (=100)", rebase: true,
    title: "Crypto — Bitcoin & Ethereum",
    series: [["Bitcoin", "#f7931a"], ["Ethereum", "#627eea"]],
    what: "Bitcoin and Ethereum, rebased to 100.",
    why: "A high-beta, round-the-clock gauge of global risk appetite." },
  // ============================== MARKET INTERNALS (NSE bhavcopy, derived) ==============================
  // src:"india" → these read the merged window.VISTAS_MACRO (the data layer appends them).
  // Series names match bhav_derived.py _SPEC friendly names EXACTLY.
  { id: "breadth", region: "MARKET INTERNALS", tag: "BREADTH", src: "india", unit: "% of names",
    title: "Market breadth — % above 50-DMA & 200-DMA", source: "NSE bhavcopy (derived, local)",
    series: [["% of names above their 50-DMA", "#1f77b4"], ["% of names above their 200-DMA", "#b3402f"]],
    what: "The share of liquid names trading above their own 50-day and 200-day moving averages, day by day.",
    why: "Breadth is the market's true health under the index headline — a rising index on falling breadth (fewer names participating) is a classic late-cycle warning." },
  { id: "adline", region: "MARKET INTERNALS", tag: "ADVANCE/DECLINE", src: "india", unit: "index / count", y2title: "new highs − lows",
    title: "Advance/Decline line & new highs vs lows", source: "NSE bhavcopy (derived, local)",
    series: [["Advance/Decline line (cumulative adv-dec)", "#111"], ["New 52-week highs", "#2ca02c", { axis: "y2", bar: true }], ["New 52-week lows", "#b3402f", { axis: "y2", bar: true }]],
    what: "The cumulative advance-minus-decline line (left axis) with the daily count of new 52-week highs and lows (right axis, bars).",
    why: "The A/D line confirms or diverges from price; a swelling new-lows count even as the index holds up is an early breakdown signal." },
  { id: "rangevol", region: "MARKET INTERNALS", tag: "RANGE VOL", src: "india", unit: "annualised vol",
    title: "Parkinson range volatility (median)", source: "NSE bhavcopy (derived, local)",
    series: [["Parkinson range vol (median, annualised)", "#8c564b"]],
    what: "The cross-sectional median Parkinson volatility — an annualised volatility estimate built from each name's high-low range (more efficient than close-to-close).",
    why: "A market-wide read on how violently names are swinging intraday; range-vol spikes often lead realized-vol and mark stress." },
  { id: "turnover", region: "MARKET INTERNALS", tag: "LIQUIDITY", src: "india", unit: "Rs cr",
    title: "Total market turnover", source: "NSE bhavcopy (derived, local)",
    series: [["Total market turnover (Rs cr)", "#1f77b4"]],
    what: "Total traded turnover across liquid names each day, in Rs crore.",
    why: "Turnover is participation and conviction — thinning turnover into a rally warns the move lacks fuel." },
  { id: "amihud", region: "MARKET INTERNALS", tag: "ILLIQUIDITY", src: "india", unit: "ret / Rs cr",
    title: "Amihud illiquidity (median)", source: "NSE bhavcopy (derived, local)",
    series: [["Amihud illiquidity (median)", "#b3402f"]],
    what: "The cross-sectional median Amihud illiquidity = |daily return| ÷ daily turnover (Rs cr) — how much price moves per crore traded.",
    why: "Rising Amihud means it costs more to move size — liquidity is drying up, which amplifies drawdowns; a stress gauge that leads volatility." },
  { id: "mcclellan", region: "MARKET INTERNALS", tag: "BREADTH MOMENTUM", src: "india", unit: "oscillator", y2title: "summation index",
    title: "McClellan Oscillator & Summation Index", source: "NSE bhavcopy (derived, local)",
    series: [["McClellan Oscillator (EMA19-EMA39 of RANA)", "#1f77b4"], ["McClellan Summation Index (cum. oscillator)", "#b3402f", { axis: "y2" }]],
    what: "Breadth momentum: the McClellan Oscillator (left axis) is the gap between a fast 19-day and slow 39-day EMA of ratio-adjusted net advances ((adv−dec)/(adv+dec)×1000); the Summation Index (right axis) is its running cumulative total.",
    why: "Oscillator above zero and rising = breadth is thrusting upward; a divergence (index makes a new high while the oscillator makes a lower high) is the classic breadth-exhaustion warning. The Summation Index frames the slow breadth cycle." },
  // ============================== DERIVED SIGNALS ==============================
  { id: "realrate", region: "DERIVED SIGNALS", tag: "REAL RATE", src: "india", unit: "%",
    title: "Real policy rate (repo − CPI)", source: "RBI (repo) · MOSPI (CPI), derived",
    series: [["Real policy rate (repo − CPI)", "#b3402f"]],
    what: "The RBI repo rate minus headline CPI inflation — the policy rate in real (inflation-adjusted) terms. (Note the U+2212 minus in the series name.)",
    why: "A negative real rate is loose money (the saver loses to inflation), a high positive real rate is genuinely tight — a cleaner read on the policy stance than the nominal repo alone." },
  { id: "cdratio", region: "DERIVED SIGNALS", tag: "BANKING", src: "india", unit: "%",
    title: "Credit-to-deposit ratio", source: "RBI, derived",
    series: [["Credit-to-deposit ratio (%)", "#1f77b4"]],
    what: "Scheduled-bank credit as a percentage of deposits — how much of the deposit base banks have lent out.",
    why: "A rising C/D ratio means banks are stretching their deposit base to lend (tighter funding, less headroom); a falling ratio means slack. A core gauge of system liquidity and lending appetite." },
];

function buildMacroDom() {
  const host = $("macro-panels"); if (!host || host.dataset.built) return;
  host.innerHTML = `<div class="fgrid">` + MACRO_PANELS.map((p) =>
    `<section class="panel fpanel" id="p-macro-${p.id}" data-region="${p.region}"><h2><span class="tag-sec">${p.tag}</span>${p.title}</h2>
      <details><summary>Definition · Method · Why</summary><p><b>What:</b> ${p.what}</p><p><b>Why:</b> ${p.why}</p>${p.source ? `<p class="src">Source: ${p.source}</p>` : ""}</details>
      <div class="seltog" id="tog-macro-${p.id}"></div>
      <div class="plot" id="plot-macro-${p.id}" style="height:320px"></div></section>`).join("") + `</div>`;
  host.dataset.built = "1";
}
function renderMacroPanel(p) {
  const traces = [];
  let needY2 = !!p.curve;
  const key = "macro-" + p.id;                    // per-panel series toggle state (CSEL)
  p.series.forEach((sdef) => {
    const nm = sdef[0], col = sdef[1], opts = sdef[2] || {};
    if (!shown(key, nm)) return;                  // isolate one / show any N (the "show" bar)
    const src = opts.src || p.src;
    const s = macroSlice(nm, src); if (!s || !s.y.some((v) => v !== null && v !== undefined)) return;
    let y = s.y;
    if (p.rebase) { const base = y.find((v) => v !== null && v !== undefined); y = base ? y.map((v) => (v !== null && v !== undefined) ? v / base * 100 : null) : y; }
    const isBar = p.bars || opts.bar;
    const tr = { name: nm, x: s.x, y };
    if (isBar) { tr.type = "bar"; tr.marker = { color: col }; }
    else { tr.type = "scatter"; tr.mode = "lines"; tr.line = { color: col, width: 1.8 }; if (opts.step) tr.line.shape = "hv"; tr.connectgaps = true; }
    if (opts.axis === "y2") { tr.yaxis = "y2"; needY2 = true; }
    traces.push(tr);
  });
  if (p.curve && shownPair(key, p.curve[0], p.curve[1])) {
    const a = macroSlice(p.curve[0], p.src), b = macroSlice(p.curve[1], p.src);
    if (a && b) { const y = a.y.map((v, i) => (v !== null && b.y[i] !== null && b.y[i] !== undefined) ? v - b.y[i] : null); traces.push({ type: "scatter", mode: "lines", name: "10Y − 3M slope", x: a.x, y, yaxis: "y2", line: { color: "#b3402f", width: 1.4, dash: "dash" } }); }
  }
  const el = $("plot-macro-" + p.id);
  if (!traces.length) { if (el) el.innerHTML = ""; return 0; }
  // Clip the x-axis to the span where THIS panel's series actually carry data. The macro
  // `dates` array is the UNION of every series (it runs back to 2000-01 because the RBI repo
  // rate does), so a panel whose series begin later — IIP 2012, WPI 2013, CPI 2014, the G-sec
  // curve 2018 — would otherwise paint a long empty band on the left. Find the first/last date
  // with a non-null y across all traces and pin the range there (Plotly's autorange would
  // include the leading null x-values and stretch the axis to 2000).
  let xlo = null, xhi = null;
  traces.forEach((t) => { const yy = t.y || []; for (let i = 0; i < yy.length; i++) { if (yy[i] !== null && yy[i] !== undefined) { const xv = t.x[i]; if (xlo === null || xv < xlo) xlo = xv; if (xhi === null || xv > xhi) xhi = xv; } } });
  const layout = { yaxis: { title: p.unit, gridcolor: "#dfe3e8" }, xaxis: { type: "date", gridcolor: "#dfe3e8" }, barmode: "group", legend: { orientation: "h" } };
  if (xlo && xhi && xlo !== xhi) layout.xaxis.range = [xlo, xhi];
  if (needY2) layout.yaxis2 = { overlaying: "y", side: "right", title: p.y2title || "", showgrid: false, zeroline: true, zerolinecolor: "#bcc3cc" };
  if (el) { Plotly.purge("plot-macro-" + p.id); Plotly.react("plot-macro-" + p.id, traces, baseLayout(layout), PCONF); attachYAutoscale("plot-macro-" + p.id); }
  return traces.length;
}
function macroPanelSeries(p) {       // [[name,colour],…] for the panel's series that actually have data
  const out = [];
  p.series.forEach((sd) => {
    const nm = sd[0], col = sd[1], src = (sd[2] && sd[2].src) || p.src;
    const s = macroSlice(nm, src);
    if (s && s.y.some((v) => v !== null && v !== undefined)) out.push([nm, col]);
  });
  return out;
}
function buildMacroToggle(p, items) {  // the per-panel "show" bar (isolate one / pick N), colours from the panel
  const el = $("tog-macro-" + p.id); if (!el) return;
  renderToggleBar(el, "macro-" + p.id, items || macroPanelSeries(p), () => renderMacroPanel(p));
}
// ── Analyst Consensus Flow (#46): per-stock ARM rolled up to the 11 analyst-desk sectors —
// EW history + FF (mcap-weighted) snapshot + the 4 ARM components + sector net-active fund flow.
// ── reusable TIME-NAVIGATION for snapshot plots (KV charting guideline: every snapshot-in-time plot must
// let you navigate across time). dateNavControl renders a slider + date label + "latest" into `host`;
// seriesValAt reads a [[date,val],…] series as-of a date. Used by the ARM histogram, the breadth screen,
// and the consensus snapshot — and the default pattern for any future snapshot plot.
let ARM_HIST_DATE = null;       // per-stock ARM histogram: selected as-of date (string) or null=latest
let ALLOC_SCREEN_IDX = null;    // allocator breakout screen: selected date index or null=latest
let CONS_SNAP_IDX = null;       // consensus cross-sector snapshot: selected month index or null=latest
function seriesValAt(series, dateStr) {
  if (!Array.isArray(series) || !series.length) return null;
  if (!dateStr) return series[series.length - 1][1];
  let v = null;
  for (let i = 0; i < series.length; i++) { if (series[i][0] <= dateStr) v = series[i][1]; else break; }
  return v;
}
function dateNavControl(host, dates, curIdx, onChange) {
  if (!host || !Array.isArray(dates) || !dates.length) { if (host) host.innerHTML = ""; return; }
  const i = (curIdx == null) ? dates.length - 1 : Math.max(0, Math.min(dates.length - 1, curIdx));
  host.innerHTML = `<span class="ab-ctllbl">As of</span>`
    + `<input type="range" class="rot-slider dn-sl" min="0" max="${dates.length - 1}" value="${i}" step="1">`
    + `<span class="dn-lbl">${fEsc(String(dates[i]))}</span>`
    + `<button type="button" class="fs-lvl dn-latest${i === dates.length - 1 ? " on" : ""}">latest</button>`;
  const sl = host.querySelector(".dn-sl"), lbl = host.querySelector(".dn-lbl"), lt = host.querySelector(".dn-latest");
  const fire = (k) => { if (lbl) lbl.textContent = String(dates[k]); if (lt) lt.classList.toggle("on", k === dates.length - 1); onChange(k); };
  if (sl) sl.addEventListener("input", () => fire(+sl.value));
  if (lt) lt.addEventListener("click", () => { if (sl) sl.value = String(dates.length - 1); fire(dates.length - 1); });
}

// Reads the baked window.VISTAS_CONSENSUS (no client recompute → no parity port). Renders into
// #consensus-cockpit at the top of the Macro tab.
let _consSel = null;
function _armColor(v) {
  if (v === null || v === undefined) return "#9aa6b2";
  if (v >= 55) return "#2e7d32"; if (v >= 52) return "#66a35f";
  if (v > 48) return "#9aa6b2"; if (v > 45) return "#d99a2b"; return "#b3402f";
}
function _consPlot(id, traces, layout) {
  const el = $(id); if (!el) return;
  if (!traces.length) { el.innerHTML = ""; return; }
  Plotly.purge(id); Plotly.react(id, traces, baseLayout(layout), PCONF); attachYAutoscale(id);
}
function renderConsensus() {
  const host = $("consensus-cockpit"); if (!host) return;
  const C = window.VISTAS_CONSENSUS;
  if (!C || !C.sectors || !C.sectors.length) { host.innerHTML = ""; return; }
  const sectors = C.sectors;                       // [{key,name}], includes _MARKET
  if (!_consSel || !sectors.some((s) => s.key === _consSel)) _consSel = sectors[0].key;
  const sel = _consSel, snapSel = (C.snap && C.snap[sel]) || {};
  const fmtFlow = (v) => (v === null || v === undefined) ? "—" : (v >= 0 ? "+" : "") + Math.round(v).toLocaleString() + " cr";

  const chips = sectors.map((s) =>
    `<button class="cchip${s.key === sel ? " on" : ""}" data-sec="${s.key}">${s.name}</button>`).join("");
  const stat = (lbl, val, cls) => `<div class="cstat"><span class="ck">${lbl}</span><span class="cv ${cls || ""}">${val}</span></div>`;
  const selName = (sectors.find((s) => s.key === sel) || {}).name || sel;
  host.innerHTML =
    `<section class="panel cpanel">
       <h2><span class="tag-sec">ANALYST CONSENSUS</span>Analyst Consensus Flow — where the street stands, by sector</h2>
       <details><summary>Definition · Method · Why</summary>
         <p><b>What:</b> Per-stock <b>ARM</b> (LSEG StarMine analyst-revision momentum, 0-100 — high = analysts revising estimates/recommendations UP) rolled up to the 11 analyst-desk sectors. <b>EW</b> = equal-weight mean (one vote per stock); <b>FF</b> = market-cap-weighted (the big names dominate). 50 = neutral; above = the sector is being net-upgraded by the street, below = net-downgraded.</p>
         <p><b>Method:</b> ${C.method || ""} ${C.ff_note || ""}</p>
         <p><b>Why:</b> This is the <i>street lens</i> of the market — whose estimates are rising, which driver (revenue / earnings / EBITDA / recommendation) is moving them, and whether real money (fund flow) agrees. Gaps between the EW line, the FF snapshot and the flow are the signal.</p>
         <p><b>Flow decomposition:</b> the smart-money bars split each sector's 3-month change in mutual-fund ownership (₹cr) into <b>price action</b> (the holdings simply rose/fell), <b>implied inflow</b> (funds received fresh money and deployed it pro-rata — no view change), and <b>net-active</b> (genuine reweighting beyond price drift — the <i>inflow-immune</i> conviction signal). The stacked total = gross ownership-value change; tick/untick the legend to isolate net-active. Net-active is the true smart-money read; the others are context.</p>
         <p class="src">Source: LSEG StarMine ARM (sector aggregates only) · ARM asof ${C.arm_asof || "—"} · flow asof ${C.flow_asof || "—"}. ARM IC is small (~0.03-0.045) and ~1-3 month horizon — a tilt, not a verdict.</p>
       </details>
       <div class="ab-ctlrow" id="cons-snap-dn"></div>
       <div class="plot" id="cons-snapshot" style="height:380px"></div>
     </section>
     <section class="panel cpanel">
       <div class="cchiprow">${chips}</div>
       <div class="cstatrow">
         ${stat("EW ARM (now)", num(snapSel.ew, 1), snapSel.ew >= 50 ? "pos" : "neg")}
         ${stat("FF-mcap ARM (now)", num(snapSel.ff, 1), snapSel.ff >= 50 ? "pos" : "neg")}
         ${stat("Coverage", (snapSel.coverage_pct == null ? "—" : snapSel.coverage_pct + "%") + " (" + (snapSel.coverage_n || 0) + "/" + (snapSel.n_sector || 0) + ")", "")}
       </div>
       <div class="cgrid3">
         <div><div class="ctitle">Consensus level — ARM (EW), ${selName}</div><div class="plot" id="cons-ew" style="height:260px"></div></div>
         <div><div class="ctitle">What's driving it — the 4 ARM components (EW)</div><div class="plot" id="cons-comp" style="height:260px"></div></div>
         <div><div class="ctitle">Smart money — flow decomposition (3M, ₹ cr): price · inflow · net-active</div><div class="plot" id="cons-flow" style="height:260px"></div></div>
       </div>
     </section>`;
  host.querySelectorAll(".cchip").forEach((b) => b.addEventListener("click", () => { _consSel = b.dataset.sec; renderConsensus(); }));

  // (1) cross-sector snapshot: EW (navigable across C.dates) vs FF (latest-only — no mcap history), 50 line
  const _cdates = C.dates || [];
  const ewAtK = (key, k) => { const s = (C.ew && C.ew[key]) || []; return (s[k] == null ? null : s[k]); };
  const drawConsSnap = (k) => {
    const li = _cdates.length - 1;
    const idx = (k == null) ? li : Math.max(0, Math.min(li, k));
    const atL = (idx === li) || li < 0;
    const ord = sectors.slice().sort((a, b) => (ewAtK(b.key, idx) || 0) - (ewAtK(a.key, idx) || 0));
    const yN = ord.map((s) => s.name);
    const ewV = ord.map((s) => ewAtK(s.key, idx));
    const tr = [{ type: "bar", orientation: "h", name: "ARM (EW)", y: yN, x: ewV, marker: { color: ewV.map(_armColor) } }];
    if (atL) tr.push({ type: "bar", orientation: "h", name: "ARM (FF-mcap, latest)", y: yN, x: ord.map((s) => (C.snap[s.key] || {}).ff), marker: { color: "#c3cad3" } });
    const allv = ewV.concat(atL ? ord.map((s) => (C.snap[s.key] || {}).ff) : []).filter((v) => v != null);
    const rngMax = Math.max(70, Math.ceil(((allv.length ? Math.max.apply(null, allv) : 60) + 5) / 5) * 5);
    _consPlot("cons-snapshot", tr,
      { barmode: "group", xaxis: { title: "ARM 0-100 (50 = neutral)" + (atL ? "" : " · EW as of " + (_cdates[idx] || "") + "; FF has no history"), gridcolor: "#dfe3e8", range: [0, rngMax] },
        yaxis: { automargin: true }, margin: { l: 8, r: 18, t: 8, b: 36 }, height: 380,
        shapes: [{ type: "line", x0: 50, x1: 50, y0: -0.5, y1: yN.length - 0.5, line: { color: "#5b6770", width: 1, dash: "dot" } }] });
  };
  { const dn = $("cons-snap-dn"); if (dn) dateNavControl(dn, _cdates, CONS_SNAP_IDX, (k) => { CONS_SNAP_IDX = k; drawConsSnap(k); }); }
  drawConsSnap(CONS_SNAP_IDX);

  // (2) EW ARM history for the selected sector + a 50 neutral reference line
  const ewSeries = (C.ew && C.ew[sel]) || [];
  const ewTr = { type: "scatter", mode: "lines", name: "ARM (EW)", x: C.dates, y: ewSeries, line: { color: "#1f77b4", width: 1.8 }, connectgaps: true };
  _consPlot("cons-ew", [ewTr],
    { yaxis: { title: "ARM 0-100", gridcolor: "#dfe3e8" }, xaxis: { type: "date", gridcolor: "#dfe3e8" },
      shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 50, y1: 50, line: { color: "#aab2bd", width: 1, dash: "dot" } }] });

  // (3) the 4 ARM components for the selected sector
  const ccol = { REVENUE: "#1f77b4", PREF_EARN: "#7b1fa2", SEC_EARN: "#2ca02c", REC: "#d99a2b" };
  const compTr = (C.comp_labels || []).map(([ck, lbl]) => {
    const ser = ((C.comp && C.comp[sel]) || {})[ck] || [];
    return { type: "scatter", mode: "lines", name: lbl, x: C.dates, y: ser, line: { color: ccol[ck] || "#888", width: 1.6 }, connectgaps: true };
  });
  _consPlot("cons-comp", compTr,
    { yaxis: { title: "ARM 0-100", gridcolor: "#dfe3e8" }, xaxis: { type: "date", gridcolor: "#dfe3e8" },
      shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 50, y1: 50, line: { color: "#aab2bd", width: 1, dash: "dot" } }] });

  // (4) sector fund-flow DECOMPOSITION (3M trailing ₹cr): price action · implied inflow · net-active.
  //     net-active = inflow-immune conviction (the smart-money signal). The stacked total = gross change
  //     in fund ownership value; use the legend to tick/untick and isolate net-active. (#net-active fix)
  const fdt = C.flow_dates || [];
  const _r1 = (x) => (x === null || x === undefined) ? null : Math.round(x * 10) / 10;
  const grossS = (C.flow_gross && C.flow_gross[sel]) || [];
  const padjS = (C.flow_price_adj && C.flow_price_adj[sel]) || [];
  const nactS = (C.flow_net_active && C.flow_net_active[sel]) || (C.flow && C.flow[sel]) || [];
  const priceS = grossS.map((g, i) => (g === null || g === undefined || padjS[i] === null || padjS[i] === undefined) ? null : _r1(g - padjS[i]));
  const inflowS = padjS.map((p, i) => (p === null || p === undefined || nactS[i] === null || nactS[i] === undefined) ? null : _r1(p - nactS[i]));
  const flowTr = [
    { type: "bar", name: "Net active (conviction)", x: fdt, y: nactS, marker: { color: "#1f9e89" } },
    { type: "bar", name: "Implied inflow", x: fdt, y: inflowS, marker: { color: "#d99a2b" } },
    { type: "bar", name: "Price action", x: fdt, y: priceS, marker: { color: "#9aa6b2" } },
  ];
  _consPlot("cons-flow", ((nactS.length || grossS.length) ? flowTr : []),
    { barmode: "relative", legend: { orientation: "h", y: -0.18, font: { size: 10 } },
      yaxis: { title: "₹ cr (3M net)", gridcolor: "#dfe3e8" }, xaxis: { type: "category", gridcolor: "#dfe3e8" } });
}

// ===================================================================================================
// ASSET ALLOCATOR TAB (market breadth) — reads the baked window.VISTAS_BREADTH (display-plane only,
// no client recompute → no analytics/parity port). Also HOSTS the relocated Analyst Consensus Flow.
// The whole tab is created in the DOM at init time (initAllocator) so only vistas.js is touched.
// Discipline: breadth is a COINCIDENT participation gauge, NOT a forward signal (meta.caveat) — said
// on the panel. Every data access is guarded; on any failure a "—" placeholder shows, never a throw.
// ===================================================================================================
let ALLOC_W = "1";          // breadth window key for new-high/low/nh-nl (1/3/5 years, see _abW)
let ALLOC_SEC = null;       // selected sector for the per-sector panel
let ALLOC_M = 50;           // the m% breakout/golden-cross screen threshold
let ALLOC_SCREEN_RULE = "breakout";   // "breakout" | "golden_cross"
let ALLOC_RP_HZ = "3";                // sector relative-performance horizon (years; "0" = MAX)
let _allocBuilt = false;

function _breadth() { return (typeof window !== "undefined") ? window.VISTAS_BREADTH : null; }
// the market{} new-high/low/nh-nl objects may be keyed by year ("1"/"3"/"5") OR by trading-day window
// ("252"/"756"/"1260"); accept either so the UI survives whichever the engine baked.
const _AB_YR2WIN = { "1": "252", "3": "756", "5": "1260" };
function _abPick(obj, ykey) {
  if (!obj || typeof obj !== "object") return null;
  if (Array.isArray(obj)) return obj;                          // already a flat series
  if (obj[ykey] != null) return obj[ykey];                     // keyed "1"/"3"/"5"
  const w = _AB_YR2WIN[ykey]; if (w != null && obj[w] != null) return obj[w];   // keyed "252"/"756"/"1260"
  // last resort: first available key
  const k = Object.keys(obj)[0]; return k ? obj[k] : null;
}
function _abWindows(m) {                                        // which year-keys actually exist in this deck
  const src = (m && (m.pct_new_high || m.nh_minus_nl)) || {};
  const out = [];
  ["1", "3", "5"].forEach((y) => {
    if (Array.isArray(src)) { if (y === "1") out.push(y); return; }
    if (src[y] != null || (_AB_YR2WIN[y] != null && src[_AB_YR2WIN[y]] != null)) out.push(y);
  });
  return out.length ? out : ["1"];
}
const _AB_WLBL = { "1": "52-week", "3": "3-year", "5": "5-year" };
function _abFmt(v, d) { return (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toFixed(d == null ? 1 : d) + "%"; }
// #47 cycle-position: where the latest value sits within its OWN history (0–100 %ile). Pure JS, display-only.
function _cyclePctile(arr) {
  if (!Array.isArray(arr) || arr.length < 8) return null;
  const clean = arr.filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  if (clean.length < 8) return null;
  const last = clean[clean.length - 1];
  const below = clean.filter((v) => v < last).length, eq = clean.filter((v) => v === last).length;
  return Math.round(100 * (below + 0.5 * eq) / clean.length);   // mid-rank %ile
}
function _abWLen(m) { const a = m && m.pct_above_200dma; const n = Array.isArray(a) ? a.filter((v) => v != null).length : 0; return n ? `${n}-point history` : "history"; }

// build the Asset Allocator tab + view in the DOM (idempotent). Relocates the consensus cockpit here.
function initAllocator() {
  if (typeof document === "undefined") return;
  const tabs = $("tabs");
  // 1) the tab button — placed right after the Macro tab (or appended if Macro isn't present)
  if (tabs && !tabs.querySelector('[data-view="allocator"]')) {
    const btn = document.createElement("button");
    btn.className = "tab"; btn.setAttribute("data-view", "allocator"); btn.textContent = "Asset Allocator";
    btn.addEventListener("click", () => { if (!btn.disabled) setView("allocator"); });
    const macroTab = tabs.querySelector('[data-view="macro"]');
    const note = $("tabnote");
    if (macroTab && macroTab.nextSibling) tabs.insertBefore(btn, macroTab.nextSibling);
    else if (note) tabs.insertBefore(btn, note);
    else tabs.appendChild(btn);
  }
  // 2) the view pane — appended into <main> after the Macro view (or at the end of <main>)
  if (!$("view-allocator")) {
    const main = document.querySelector("main");
    if (main) {
      const view = document.createElement("div");
      view.className = "view"; view.id = "view-allocator"; view.hidden = true;
      view.innerHTML =
        `<div class="measurebar">
           <span class="mlbl">Asset Allocator</span>
           <span class="measurenote">Market breadth — how broadly individual stocks are participating (new highs/lows, % above their 200/50-DMA, golden-cross). A <b>coincident participation gauge</b>, validated on our own total-return data: it tells you how broad the move you are <i>already in</i> is — <b>not</b> a forward buy/sell signal.</span>
         </div>
         <div id="alloc-body"></div>`;
      const macroView = $("view-macro");
      if (macroView && macroView.nextSibling) main.insertBefore(view, macroView.nextSibling);
      else main.appendChild(view);
      // relocate the existing Analyst Consensus Flow cockpit div from Macro into the allocator pane
      const cons = $("consensus-cockpit");
      if (cons) $("alloc-body").appendChild(cons);   // moved (not cloned) — Macro keeps no empty shell
    }
  }
  // 3) enable/disable the tab by data presence (breadth OR the relocated consensus)
  const btn = tabs && tabs.querySelector('[data-view="allocator"]');
  const cons = (typeof window !== "undefined") ? window.VISTAS_CONSENSUS : null;
  const has = !!(_breadth() || (cons && cons.sectors));
  if (btn) { btn.disabled = !has; btn.style.opacity = has ? "" : ".45"; btn.title = has ? "" : "No breadth / consensus data in this deck yet"; }
}

function renderAllocator() {
  const host = $("alloc-body"); if (!host) return;
  const B = _breadth();
  // ensure a stable scaffold (built once): breadth panels ABOVE the relocated consensus cockpit.
  if (!_allocBuilt) {
    const consEl = $("consensus-cockpit");
    const scaffold = document.createElement("div");
    scaffold.id = "alloc-breadth";
    if (consEl) host.insertBefore(scaffold, consEl); else host.appendChild(scaffold);
    _allocBuilt = true;
  }
  const wrap = $("alloc-breadth");
  if (wrap) {
    try { renderBreadth(B, wrap); }
    catch (e) { console.error("renderBreadth:", e); wrap.innerHTML = `<section class="panel"><div class="empty-note">Market-breadth panel unavailable in this deck.</div></section>`; }
  }
  // the relocated consensus cockpit (its own try/catch inside)
  try { renderConsensus(); } catch (e) { console.error("renderConsensus (allocator):", e); }
  afterPaint(() => viewPlotsResize("allocator"));
}

// ===== Ownership & Flow tab (#102) — the money-flow WATERFALL: AMC -> sector, every month's holding
// change split into price action / implied inflow / net-active (the conviction read), over time.
// Reads the baked window.VISTAS_WATERFALL (no client recompute -> no JS<->Python parity port).
// AGGREGATES only; "implied inflow" = deployment inferred from holdings, NOT raw AMFI subscriptions. =====
let _wfAmc = "__ALL__", _wfSector = "__ALL__", _wfHz = "0", _wfSnapIdx = null, _wfBuilt = false;
// the "chosen cell" the header + time-series plot follow, climbing the lattice when a field is null:
//   {amc:null,code:null,sector:null} = market · {amc} = an AMC · {amc,code} = a scheme · +sector = a leaf
let _wfFocus = { amc: null, code: null, sector: null };
let _wfTheme = null;    // selected NSE thematic index for the theme-lens chart
let _wfCrowdMode = "sector", _wfCrowdSec = null, _wfCrowdStk = null;   // cross-AMC crowding (P4b)
const _wfPivExp = {};   // pivot rowKey -> true (expanded), for the Excel-style drill-down
const _wfDrill = {};    // amc-slug -> drill payload | null (lazy-loaded per-AMC scheme×sector file)
const _wfCrowdCache = {}, _wfCrowdInflight = {};   // vst_id -> per-stock "who's trading it" payload (lazy)
const _WF_COL = { na: "#1f9e89", inflow: "#d99a2b", price: "#9aa6b2" };
function _wf() { return (typeof window !== "undefined") ? window.VISTAS_WATERFALL : null; }
function _wfNode(W, amc, sector) {
  if (!W) return null;
  if (amc === "__ALL__" && sector === "__ALL__") return W.market_total || null;
  if (amc === "__ALL__") return (W.sector_total || {})[sector] || null;
  const a = (W.cube || {})[amc]; if (!a) return null;
  if (sector === "__ALL__") return a.__total__ || null;
  return a[sector] || null;
}
// lazy-load an AMC's drill-down file (scheme -> sector history); cached, null on absent/failure. The
// pivot fetches it on first expand; the time-series plot reuses the cache for scheme-level cells.
async function ensureWfDrill(slug) {
  if (Object.prototype.hasOwnProperty.call(_wfDrill, slug)) return _wfDrill[slug];
  if (!slug || !LAZY) { _wfDrill[slug] = null; return null; }
  const b = await fetchJSON(LAZY.base + "ownership/" + lazyURL(slug) + ".json");
  _wfDrill[slug] = b || null;
  return _wfDrill[slug];
}
function _wfDrillFor(amc) {                 // sync: the cached drill payload for an AMC, or null
  const W = _wf(); if (!W || !amc) return null;
  const slug = (W.drill_index || {})[amc];
  return slug ? (_wfDrill[slug] || null) : null;
}
function _wfScheme(amc, code) {             // a scheme node from the cached drill file, or null
  const d = _wfDrillFor(amc); if (!d) return null;
  return (d.schemes || []).find((s) => s.code === code) || null;
}
// resolve the focused cell to its {gross,price,inflow,net_active,mv} arrays (inline cube for market /
// sector / AMC / AMC×sector; the lazy drill file for scheme / scheme×sector).
function _wfSeriesFor(f) {
  const W = _wf(); if (!W) return null;
  f = f || {};
  if (!f.amc) return _wfNode(W, "__ALL__", f.sector || "__ALL__");
  if (!f.code) return _wfNode(W, f.amc, f.sector || "__ALL__");
  const sch = _wfScheme(f.amc, f.code); if (!sch) return null;
  if (f.vst) {                                              // a stock leaf under scheme×sector (P4)
    const secNode = (sch.sectors || {})[f.sector]; if (!secNode) return null;
    return (secNode.stocks || []).find((s) => s.vst_id === f.vst) || null;
  }
  return f.sector ? ((sch.sectors || {})[f.sector] || null) : sch.total;
}
function _wfPlot(id, traces, layout) {
  const el = $(id); if (!el) return;
  if (!traces.length) { Plotly.purge(id); el.innerHTML = ""; return; }
  Plotly.purge(id); Plotly.react(id, traces, baseLayout(layout), PCONF); attachYAutoscale(id);
}
function _wfFmt(v) { return (v === null || v === undefined) ? "—" : (v >= 0 ? "+" : "") + Math.round(v).toLocaleString() + " cr"; }
function _wfScopeName() {
  const f = _wfFocus || {};
  if (!f.amc) return "All AMCs · " + (f.sector || "all sectors");
  let nm = f.amc;
  if (f.code) { const sch = _wfScheme(f.amc, f.code); nm += " · " + (sch ? sch.name : f.code); }
  if (f.sector) nm += " · " + f.sector;
  if (f.vst) {
    const sch = _wfScheme(f.amc, f.code), sec = sch && (sch.sectors || {})[f.sector];
    const st = sec && (sec.stocks || []).find((s) => s.vst_id === f.vst);
    nm += " · " + (st ? st.name : f.vst);
  }
  return nm;
}

// build the Ownership & Flow tab + view in the DOM (idempotent). Placed right after the Allocator tab.
function initOwnership() {
  if (typeof document === "undefined") return;
  const tabs = $("tabs");
  if (tabs && !tabs.querySelector('[data-view="ownership"]')) {
    const btn = document.createElement("button");
    btn.className = "tab"; btn.setAttribute("data-view", "ownership"); btn.textContent = "Ownership & Flow";
    btn.addEventListener("click", () => { if (!btn.disabled) setView("ownership"); });
    const allocTab = tabs.querySelector('[data-view="allocator"]');
    const note = $("tabnote");
    if (allocTab && allocTab.nextSibling) tabs.insertBefore(btn, allocTab.nextSibling);
    else if (note) tabs.insertBefore(btn, note);
    else tabs.appendChild(btn);
  }
  if (!$("view-ownership")) {
    const main = document.querySelector("main");
    if (main) {
      const view = document.createElement("div");
      view.className = "view"; view.id = "view-ownership"; view.hidden = true;
      view.innerHTML =
        `<div class="measurebar">
           <span class="mlbl">Ownership &amp; Flow</span>
           <span class="measurenote">The money chain — an <b>AMC</b> raises capital via its schemes, which <b>deploy</b> it into <b>sectors</b> and stocks. Each month's change in a holding is split into <b>price action</b> (moved with the market), <b>implied inflow</b> (fresh SIP/lump-sum money deployed pro-rata — no view change) and <b>net-active</b> (a genuine reweighting — the conviction / smart-money signal). Aggregates only; reconciles exactly. <b>Net-active is zero-sum within a fund</b> (an overweight is funded by an underweight), so any fund / AMC / market <i>total</i> nets to ~0 by construction — read the per-sector / stock breakdown, or the one-way <b>⇄ reshuffle</b> (Σ|net-active by sector|/2), not the total.</span>
         </div>
         <div id="own-body"></div>`;
      const allocView = $("view-allocator");
      if (allocView && allocView.nextSibling) main.insertBefore(view, allocView.nextSibling);
      else main.appendChild(view);
    }
  }
  const btn = tabs && tabs.querySelector('[data-view="ownership"]');
  const W = _wf();
  const has = !!(W && W.cube && W.months && W.months.length);
  if (btn) { btn.disabled = !has; btn.style.opacity = has ? "" : ".45"; btn.title = has ? "" : "No ownership/flow data in this deck yet"; }
}

function renderOwnership() {
  const host = $("own-body"); if (!host) return;
  const W = _wf();
  if (!W || !W.cube || !W.months || !W.months.length) {
    host.innerHTML = `<section class="panel"><div class="empty-note">No ownership &amp; flow data baked into this deck yet.</div></section>`;
    return;
  }
  const months = W.months;
  const amcs = W.amcs || Object.keys(W.cube);
  const sectors = W.sectors || Object.keys(W.sector_total || {});
  if (_wfSnapIdx == null) _wfSnapIdx = months.length - 1;
  if (!_wfBuilt) {
    const amcOpts = `<option value="__ALL__">All AMCs (market)</option>` + amcs.map((a) => `<option value="${attEsc(a)}">${fEsc(a)}</option>`).join("");
    const secOpts = `<option value="__ALL__">All sectors</option>` + sectors.map((s) => `<option value="${attEsc(s)}">${fEsc(s)}</option>`).join("");
    host.innerHTML =
      `<section class="panel cpanel">
         <h2><span class="tag-sec">OWNERSHIP &amp; FLOW</span>Money-flow waterfall — where the smart money is actually going</h2>
         <details><summary>Definition · Method · Why</summary>
           <p><b>What:</b> every month, each mutual-fund holding's rupee change is decomposed into three additive parts that sum to the gross change — <b>price action</b> = start value × the stock's return (the holding simply moved with the market, no decision); <b>implied inflow</b> = fresh scheme money deployed pro-rata across the book (every name rises together, still no per-name view); <b>net-active</b> = the genuine reweighting in weight-space (inflow-immune — the only part that reflects conviction).</p>
           <p><b>Method:</b> price_action = gross − price_adj · implied_inflow = price_adj − net_active · net_active = AUM·(1+R_p)·Δw_active. Rolled up the lattice stock → (AMC × sector) → AMC → sector → market — every level a reconciling group-by of the same cube (price + inflow + net-active = gross every month). Monthly disclosures; ${months.length} months to ${fEsc(months[months.length - 1])}.</p>
           <p><b>Why:</b> the headline "₹X cr bought" is mostly SIP money landing — only <b>net-active</b> can be a conviction signal. This separates the three so you can see where managers are <i>actually</i> tilting, by AMC and sector, over time. <b>Caveat:</b> "implied inflow" is deployment inferred from holdings, not raw AMFI subscription data.</p>
         </details>
         <div class="ab-ctlrow">
           <span class="ab-ctllbl">AMC</span>
           <select id="wf-amc" class="ab-sel" style="min-width:200px">${amcOpts}</select>
           <span class="ab-ctllbl">Sector</span>
           <select id="wf-sec" class="ab-sel" style="min-width:170px">${secOpts}</select>
           <span id="wf-hz" style="margin-left:10px">
             <button type="button" class="fs-lvl" data-hz="12">1Y</button>
             <button type="button" class="fs-lvl" data-hz="24">2Y</button>
             <button type="button" class="fs-lvl on" data-hz="0">MAX</button>
           </span>
         </div>
         <div id="wf-head" style="display:flex;flex-wrap:wrap;gap:16px;margin:10px 0"></div>
         <div id="plot-wf-decomp"></div>
         <div class="ab-ctlrow" id="wf-snap-dn" style="margin-top:10px"></div>
         <div id="wf-snap"></div>
       </section>` + _wfCrowdPanelHTML(W) + _wfThemePanelHTML(W);
    $("wf-amc").addEventListener("change", (e) => { _wfAmc = e.target.value; _wfOnScopeChange(); });
    $("wf-sec").addEventListener("change", (e) => { _wfSector = e.target.value; _wfOnScopeChange(); });
    const hz = $("wf-hz");
    if (hz) hz.querySelectorAll(".fs-lvl").forEach((b) => b.addEventListener("click", () => {
      hz.querySelectorAll(".fs-lvl").forEach((x) => x.classList.remove("on")); b.classList.add("on");
      _wfHz = b.dataset.hz; _wfDrawPlot(); _wfThemeChart(); _wfCrowdChart();
    }));
    const tsel = $("wf-theme-sel");
    if (tsel) tsel.addEventListener("change", (e) => { _wfTheme = e.target.value; _wfThemeDraw(); });
    const cmode = $("wf-crowd-mode");
    if (cmode) cmode.querySelectorAll(".fs-lvl").forEach((b) => b.addEventListener("click", () => {
      cmode.querySelectorAll(".fs-lvl").forEach((x) => x.classList.remove("on")); b.classList.add("on");
      _wfCrowdMode = b.dataset.mode;
      const sw = $("wf-crowd-sec-wrap"), tw = $("wf-crowd-stk-wrap");
      if (sw) sw.style.display = _wfCrowdMode === "sector" ? "" : "none";
      if (tw) tw.style.display = _wfCrowdMode === "stock" ? "" : "none";
      _wfCrowdDraw();
    }));
    const csec = $("wf-crowd-sec"); if (csec) csec.addEventListener("change", (e) => { _wfCrowdSec = e.target.value; _wfCrowdDraw(); });
    const cstk = $("wf-crowd-stk"); if (cstk) cstk.addEventListener("change", (e) => { _wfCrowdStk = e.target.value; _wfCrowdDraw(); });
    _wfBuilt = true;
  }
  _wfDraw();
  afterPaint(() => viewPlotsResize("ownership"));
}

function _wfDraw() { _wfDrawHead(); _wfDrawPlot(); _wfDrawSnap(); _wfThemeDraw(); _wfCrowdDraw(); }

// ── Net-active is ZERO-SUM within a fund (an overweight is funded by an underweight), so any TOTAL
// (market / AMC / scheme) nets to ~0 by construction. The meaningful aggregate is the one-way "active
// reshuffle" = Σ|net-active by sector|/2 (how much conviction money moved BETWEEN sectors) + the biggest
// +/- sector tilt. Signed net-active stays the right read at the sector & stock (non-total) rows.
function _wfMag(v) { return (v == null) ? "—" : Math.round(v).toLocaleString() + " cr"; }   // unsigned magnitude
function _wfSecArr(secMap, i) {              // {sector: node} -> [{sec, na}]; skips __total__
  if (!secMap) return null;
  const arr = [];
  Object.keys(secMap).forEach((sec) => {
    if (sec === "__total__") return;
    const nd = secMap[sec], na = (nd && nd.net_active) ? nd.net_active[i] : null;
    if (na != null) arr.push({ sec, na });
  });
  return arr;
}
function _wfReshuffleArr(arr) {              // [{sec,na}] -> {reshuffle, top, bot} or null
  if (!arr || !arr.length) return null;
  let sumAbs = 0, top = null, bot = null;
  arr.forEach(({ sec, na }) => {
    if (na == null) return;
    sumAbs += Math.abs(na);
    if (!top || na > top.na) top = { sec, na };
    if (!bot || na < bot.na) bot = { sec, na };
  });
  return { reshuffle: sumAbs / 2, top, bot };
}
// sector-breakdown reshuffle for a TOTAL focus (market / AMC / scheme); null if not a total or not loaded.
function _wfReshuffleFor(f, i) {
  const W = _wf(); f = f || {};
  let secMap = null;
  if (!f.amc) secMap = W.sector_total;                                  // market total
  else if (!f.code) secMap = (W.cube || {})[f.amc] || null;             // AMC total
  else if (!f.sector && !f.vst) { const s = _wfScheme(f.amc, f.code); secMap = s ? s.sectors : null; }  // scheme total
  return secMap ? _wfReshuffleArr(_wfSecArr(secMap, i)) : null;
}

function _wfDrawHead() {
  const W = _wf(), node = _wfSeriesFor(_wfFocus), host = $("wf-head"); if (!host) return;
  if (!node || !node.gross || !node.gross.length) { host.innerHTML = `<div class="empty-note">No flow for this selection.</div>`; return; }
  const i = node.gross.length - 1;
  const f = _wfFocus || {};
  const isTotal = !f.sector && !f.vst;        // market / AMC / scheme totals: net-active nets to ~0 by construction
  const stat = (lbl, val, col) => `<div class="cstat"><span class="ck">${lbl}</span><span class="cv" style="color:${col || ''}">${_wfFmt(val)}</span></div>`;
  let html =
    `<div class="cstat"><span class="ck">${fEsc(_wfScopeName())} · latest ${fEsc(W.months[i])}</span><span class="cv">gross ${_wfFmt(node.gross[i])}</span></div>`
    + (node.mv ? `<div class="cstat"><span class="ck">Ownership (priced)</span><span class="cv">${_wfFmt(node.mv[i])}</span></div>` : "")
    + stat("Price action", node.price[i], _WF_COL.price)
    + stat("Implied inflow", node.inflow[i], _WF_COL.inflow);
  const rs = isTotal ? _wfReshuffleFor(f, i) : null;
  if (rs && rs.reshuffle > 0) {                // a total -> show the one-way reshuffle + the biggest sector tilt
    const tilt = `<span style="color:${_WF_COL.na}">${_wfFmt(rs.top.na)} ${fEsc(rs.top.sec)}</span> · <span style="color:#b3402f">${_wfFmt(rs.bot.na)} ${fEsc(rs.bot.sec)}</span>`;
    html += `<div class="cstat" title="net-active is zero-sum within a book, so a total nets to ~0; this is the one-way magnitude Σ|net-active by sector|/2"><span class="ck">Active reshuffle (one-way)</span><span class="cv" style="color:${_WF_COL.na}">⇄ ${_wfMag(rs.reshuffle)}</span></div>`
          + `<div class="cstat"><span class="ck">Biggest tilt (by sector)</span><span class="cv" style="font-size:12px">${tilt}</span></div>`;
  } else {                                      // a sector / stock cell -> signed net-active is meaningful
    html += stat("Net-active (conviction)", node.net_active[i], node.net_active[i] >= 0 ? _WF_COL.na : "#b3402f");
  }
  host.innerHTML = html;
}

function _wfDrawPlot() {
  const W = _wf(), node = _wfSeriesFor(_wfFocus); if (!W) return;
  if (!node || !node.gross) { _wfPlot("plot-wf-decomp", [], {}); return; }
  const n = W.months.length;
  const back = parseInt(_wfHz, 10) || 0;
  const h0 = back > 0 ? Math.max(0, n - back) : 0;
  const x = W.months.slice(h0);
  const sl = (arr) => arr.slice(h0);
  const traces = [
    { type: "bar", x, y: sl(node.net_active), name: "Net-active", marker: { color: _WF_COL.na } },
    { type: "bar", x, y: sl(node.inflow), name: "Implied inflow", marker: { color: _WF_COL.inflow } },
    { type: "bar", x, y: sl(node.price), name: "Price action", marker: { color: _WF_COL.price } },
  ];
  _wfPlot("plot-wf-decomp", traces, {
    barmode: "relative", height: 360,
    title: { text: `Flow decomposition — ${_wfScopeName()} (₹ cr/month)`, font: { size: 13 } },
    legend: { orientation: "h" }, yaxis: { title: "₹ cr", zeroline: true }, margin: { t: 40 },
  });
}

// dropdown change: re-root the pivot at the chosen AMC, collapse drill state, refocus the plot there.
function _wfOnScopeChange() {
  _wfFocus = { amc: _wfAmc === "__ALL__" ? null : _wfAmc, code: null, sector: _wfSector === "__ALL__" ? null : _wfSector };
  for (const k in _wfPivExp) delete _wfPivExp[k];
  if (_wfFocus.amc) {                                  // warm the AMC's drill file so schemes show at once
    const slug = (_wf().drill_index || {})[_wfFocus.amc];
    if (slug && !_wfDrillFor(_wfFocus.amc)) ensureWfDrill(slug).then(() => _wfPivotRender());
  }
  _wfDraw();
}

function _wfDrawSnap() {
  const W = _wf();
  const dnHost = $("wf-snap-dn");
  if (dnHost) dateNavControl(dnHost, W.months, _wfSnapIdx, (k) => { _wfSnapIdx = k; _wfPivotRender(); _wfThemeTable(); _wfCrowdHead(); _wfCrowdTable(); });
  _wfPivotRender();
}

// the pivot rowKey of the currently-focused cell, for row highlighting.
function _wfFocusKey() {
  const f = _wfFocus || {};
  if (!f.amc) return null;
  if (!f.code) return "amc::" + f.amc;
  return "sch::" + f.amc + "::" + f.code + (f.sector ? "::" + f.sector : "") + (f.vst ? "::vst::" + f.vst : "");
}

// build the flat list of VISIBLE pivot rows from the expansion state, at the snapshot month `i`.
//   root = the AMC dropdown: All AMCs -> [AMC -> scheme -> sector]; a specific AMC -> [scheme -> sector].
function _wfPivotRows() {
  const W = _wf(), out = [];
  const i = (_wfSnapIdx == null) ? W.months.length - 1 : _wfSnapIdx;
  const at = (node) => node ? { mv: node.mv ? node.mv[i] : null, na: node.net_active[i], inf: node.inflow[i], pr: node.price[i], gr: node.gross[i] } : null;
  if (_wfAmc === "__ALL__") {
    const amcs = (W.amcs || Object.keys(W.cube || {})).slice();
    const amcRows = amcs.map((a) => {
      const v = at(((W.cube || {})[a] || {}).__total__);
      if (v) { const rs = _wfReshuffleArr(_wfSecArr((W.cube || {})[a], i)); v.resh = rs ? rs.reshuffle : null; v.isTotal = true; }
      return { a, v };
    }).filter((r) => r.v);
    amcRows.sort((p, q) => (q.v.resh || 0) - (p.v.resh || 0));   // totals: sort by one-way reshuffle (net-active≈0)
    for (const { a, v } of amcRows) {
      const key = "amc::" + a;
      out.push({ key, depth: 0, label: a, vals: v, expandable: true, expanded: !!_wfPivExp[key], focus: { amc: a, code: null, sector: null } });
      if (_wfPivExp[key]) _wfPushSchemes(out, a, 1, i, at);
    }
  } else {
    _wfPushSchemes(out, _wfAmc, 0, i, at);
  }
  return out;
}

// append an AMC's scheme rows (and, if expanded, their sector children); lazy-fetch the drill file if absent.
function _wfPushSchemes(out, amc, depth, i, at) {
  const d = _wfDrillFor(amc);
  if (!d) {
    const slug = (_wf().drill_index || {})[amc];
    if (slug) { ensureWfDrill(slug).then(() => _wfPivotRender()); out.push({ key: "load::" + amc, depth, label: "loading schemes…", placeholder: true }); }
    else out.push({ key: "none::" + amc, depth, label: "no scheme drill-down for this AMC", placeholder: true });
    return;
  }
  const schemes = (d.schemes || []).map((s) => {
    const v = at(s.total);
    if (v) { const rs = _wfReshuffleArr(_wfSecArr(s.sectors, i)); v.resh = rs ? rs.reshuffle : null; v.isTotal = true; }
    return { s, v };
  }).filter((r) => r.v);
  schemes.sort((p, q) => (q.v.resh || 0) - (p.v.resh || 0));   // scheme totals: sort by one-way reshuffle
  for (const { s, v } of schemes) {
    const key = "sch::" + amc + "::" + s.code;
    out.push({ key, depth, label: s.name, vals: v, expandable: true, expanded: !!_wfPivExp[key], focus: { amc, code: s.code, sector: null } });
    if (_wfPivExp[key]) {
      const secs = Object.keys(s.sectors || {}).map((sec) => ({ sec, v: at(s.sectors[sec]) })).filter((r) => r.v);
      secs.sort((p, q) => (q.v.na || 0) - (p.v.na || 0));
      for (const { sec, v: sv } of secs) {
        const secKey = key + "::" + sec;
        const stocks = (s.sectors[sec] || {}).stocks || [];
        out.push({ key: secKey, depth: depth + 1, label: sec, vals: sv, expandable: stocks.length > 0, expanded: !!_wfPivExp[secKey], focus: { amc, code: s.code, sector: sec } });
        if (_wfPivExp[secKey] && stocks.length) {              // P4 stock leaves (top holdings; may not sum to sector)
          const srows = stocks.map((st) => ({ st, v: at(st) })).filter((r) => r.v);
          srows.sort((p, q) => (q.v.na || 0) - (p.v.na || 0));
          for (const { st, v: sv2 } of srows) {
            out.push({ key: secKey + "::vst::" + st.vst_id, depth: depth + 2, label: st.name + (st.sym ? " (" + st.sym + ")" : ""), vals: sv2, expandable: false, focus: { amc, code: s.code, sector: sec, vst: st.vst_id } });
          }
        }
      }
    }
  }
}

// render the Excel-style pivot into #wf-snap; one delegated click handler toggles drill + focuses the cell.
function _wfPivotRender() {
  const W = _wf(), host = $("wf-snap"); if (!host) return;
  const i = (_wfSnapIdx == null) ? W.months.length - 1 : _wfSnapIdx;
  const ym = W.months[i];
  const rows = _wfPivotRows();
  const focusKey = _wfFocusKey();
  const naCol = (v) => (v >= 0 ? _WF_COL.na : "#b3402f");
  const caret = (r) => r.expandable ? `<span class="wf-caret">${r.expanded ? "▾" : "▸"}</span>` : `<span class="wf-caret wf-caret-none"></span>`;
  const body = rows.map((r) => {
    if (r.placeholder) return `<tr><td colspan="6" style="padding-left:${10 + r.depth * 18}px;color:#8a93a0;font-style:italic">${fEsc(r.label)}</td></tr>`;
    return `<tr class="wf-row${r.key === focusKey ? " wf-row-on" : ""}" data-key="${attEsc(r.key)}" data-focus="${attEsc(JSON.stringify(r.focus))}" data-exp="${r.expandable ? 1 : 0}">`
      + `<td style="padding-left:${6 + r.depth * 18}px">${caret(r)}${fEsc(r.label)}</td>`
      + `<td class="num">${r.vals.mv == null ? "—" : _wfFmt(r.vals.mv)}</td>`
      + (r.vals.isTotal
          ? `<td class="num" style="color:${_WF_COL.na}" title="net-active is zero-sum within a book, so this total is ~0; shown is the one-way reshuffle Σ|net-active by sector|/2">⇄ ${r.vals.resh == null ? "—" : _wfMag(r.vals.resh)}</td>`
          : `<td class="num" style="color:${naCol(r.vals.na)}">${_wfFmt(r.vals.na)}</td>`)
      + `<td class="num" style="color:${_WF_COL.inflow}">${_wfFmt(r.vals.inf)}</td>`
      + `<td class="num" style="color:${_WF_COL.price}">${_wfFmt(r.vals.pr)}</td>`
      + `<td class="num">${_wfFmt(r.vals.gr)}</td></tr>`;
  }).join("");
  const rootLbl = _wfAmc === "__ALL__" ? "market → AMC → scheme → sector" : `${_wfAmc} → scheme → sector`;
  host.innerHTML =
    `<div class="ab-screen-head"><b>Pivot — ${fEsc(rootLbl)}</b> · as of ${fEsc(ym)} — click a row to drill in &amp; chart it. <b>Totals</b> (AMC/scheme) show the one-way <b>⇄ reshuffle</b> — net-active is zero-sum within a book (an overweight is funded by an underweight), so a total nets to ~0; read the <b>per-sector / stock</b> rows (signed net-active) for the conviction tilt. Sorted by reshuffle (totals) / net-active (sector &amp; stock rows).</div>`
    + `<table class="gauge-tbl wf-pivot"><thead><tr><th>Name</th><th class="num">Ownership</th><th class="num" title="totals: ⇄ one-way reshuffle Σ|net-active by sector|/2 (net-active is zero-sum within a book → totals ≈ 0); sector &amp; stock rows: signed net-active (conviction)">Net-active ⇄</th><th class="num">Implied inflow</th><th class="num">Price action</th><th class="num">Gross</th></tr></thead><tbody>${body || `<tr><td colspan="6" class="empty-note">No flow this month.</td></tr>`}</tbody></table>`;
  if (!host.dataset.wfwired) {
    host.addEventListener("click", (e) => {
      const tr = e.target.closest("tr.wf-row"); if (!tr || !tr.dataset.focus) return;
      const key = tr.dataset.key;
      if (tr.dataset.exp === "1") _wfPivExp[key] = !_wfPivExp[key];
      try { _wfFocus = JSON.parse(tr.dataset.focus); } catch (_e) {}
      // if focusing/expanding into an AMC whose drill file isn't loaded, fetch then re-render
      if (_wfFocus.amc && !_wfDrillFor(_wfFocus.amc)) {
        const slug = (_wf().drill_index || {})[_wfFocus.amc];
        if (slug) ensureWfDrill(slug).then(() => { _wfDrawHead(); _wfDrawPlot(); _wfPivotRender(); });
      }
      _wfDrawHead(); _wfDrawPlot(); _wfPivotRender();
    });
    host.dataset.wfwired = "1";
  }
}

// ===== P4b cross-AMC crowding: for a chosen SECTOR or STOCK, which AMCs are tilting into/out of it
// (the inverse of the per-AMC drill-down). Sector = inline AMC×sector cube; stock = lazy per-stock file. =====
function _wfCrowdPanelHTML(W) {
  const sectors = (W && W.sectors) || [];
  const ci = (W && W.crowd_index) || [];
  if (!sectors.length && !ci.length) return "";
  if (_wfCrowdSec == null && sectors.length) _wfCrowdSec = sectors[0];
  if (_wfCrowdStk == null && ci.length) _wfCrowdStk = ci[0].vst_id;
  const secOpts = sectors.map((s) => `<option value="${attEsc(s)}"${s === _wfCrowdSec ? " selected" : ""}>${fEsc(s)}</option>`).join("");
  const stkOpts = ci.map((c) => `<option value="${attEsc(c.vst_id)}"${c.vst_id === _wfCrowdStk ? " selected" : ""}>${fEsc(c.name + (c.sym ? " (" + c.sym + ")" : ""))}</option>`).join("");
  return `<section class="panel cpanel">
    <h2><span class="tag-sec">CROWDING</span>Cross-AMC crowding — who's tilting into a stock or sector</h2>
    <details><summary>Definition · Method · Why</summary>
      <p><b>What:</b> for a chosen <b>sector</b> or <b>stock</b>, the fund houses (AMCs) ranked by their <b>net-active</b> (conviction) tilt into/out of it — the inverse of the per-AMC drill-down. Net-buying = AMCs adding weight; net-selling = trimming.</p>
      <p><b>Method:</b> sector = Σ over each AMC's holdings in that sector (the inline AMC×sector cube); stock = Σ over each AMC's schemes' holdings of that stock (a lazy per-stock file). Same three-way split; <b>Ownership</b> = MV held. <b>Why:</b> see whether the smart money is <i>crowding in</i> (many AMCs buying) or quietly distributing — and exactly who.</p>
      <p class="q-note">Stock coverage = stocks with ≥ ₹300 cr total mutual-fund ownership. Reconciles (price + inflow + net-active = gross).</p>
    </details>
    <div class="ab-ctlrow">
      <span class="fs-lvl-seg" id="wf-crowd-mode">
        <button type="button" class="fs-lvl on" data-mode="sector">By sector</button>
        <button type="button" class="fs-lvl" data-mode="stock">By stock</button>
      </span>
      <span id="wf-crowd-sec-wrap" style="margin-left:10px"><span class="ab-ctllbl">Sector</span> <select id="wf-crowd-sec" class="ab-sel" style="min-width:180px">${secOpts}</select></span>
      <span id="wf-crowd-stk-wrap" style="margin-left:10px;display:none"><span class="ab-ctllbl">Stock</span> <select id="wf-crowd-stk" class="ab-sel" style="min-width:240px">${stkOpts}</select></span>
    </div>
    <div id="wf-crowd-head" style="display:flex;flex-wrap:wrap;gap:16px;margin:10px 0"></div>
    <div id="plot-wf-crowd"></div>
    <div id="wf-crowd-tbl" style="margin-top:10px"></div>
  </section>`;
}

async function ensureCrowdStock(vid) {
  if (Object.prototype.hasOwnProperty.call(_wfCrowdCache, vid)) return _wfCrowdCache[vid];
  if (_wfCrowdInflight[vid]) return _wfCrowdInflight[vid];
  if (!vid || !LAZY) { _wfCrowdCache[vid] = null; return null; }
  _wfCrowdInflight[vid] = fetchJSON(LAZY.base + "ownership_stock/" + lazyURL(vid) + ".json").then((b) => {
    _wfCrowdCache[vid] = b || null; delete _wfCrowdInflight[vid]; return _wfCrowdCache[vid];
  });
  return _wfCrowdInflight[vid];
}

// resolve the current crowding selection -> {label, amcs:{amc:node}, agg:node}; {loading:true} while fetching.
function _wfCrowdSel() {
  const W = _wf(); if (!W) return null;
  if (_wfCrowdMode === "sector") {
    const sec = _wfCrowdSec; if (!sec) return null;
    const amcs = {};
    Object.keys(W.cube || {}).forEach((a) => { const n = (W.cube[a] || {})[sec]; if (n) amcs[a] = n; });
    return { label: sec, amcs, agg: (W.sector_total || {})[sec] || null };
  }
  const vid = _wfCrowdStk; if (!vid) return null;
  if (!Object.prototype.hasOwnProperty.call(_wfCrowdCache, vid)) { ensureCrowdStock(vid).then(() => _wfCrowdDraw()); return { loading: true }; }
  const d = _wfCrowdCache[vid]; if (!d) return null;
  const amcs = d.amcs || {}, ks = ["gross", "price", "inflow", "net_active", "mv"], n = W.months.length;
  const agg = {}; ks.forEach((k) => { agg[k] = new Array(n).fill(0); });
  Object.keys(amcs).forEach((a) => ks.forEach((k) => { const arr = amcs[a][k] || []; for (let i = 0; i < n; i++) agg[k][i] += (arr[i] || 0); }));
  return { label: d.name + (d.sym ? " (" + d.sym + ")" : ""), amcs, agg };
}

function _wfCrowdDraw() { _wfCrowdHead(); _wfCrowdChart(); _wfCrowdTable(); }

function _wfCrowdHead() {
  const host = $("wf-crowd-head"); if (!host) return;
  const W = _wf(), sel = _wfCrowdSel();
  if (!sel) { host.innerHTML = `<div class="empty-note">No crowding data for this selection.</div>`; return; }
  if (sel.loading) { host.innerHTML = `<div class="empty-note">loading…</div>`; return; }
  const i = (_wfSnapIdx == null) ? W.months.length - 1 : _wfSnapIdx;
  let buyers = 0, sellers = 0;
  Object.keys(sel.amcs).forEach((a) => { const v = sel.amcs[a].net_active[i]; if (v > 0) buyers++; else if (v < 0) sellers++; });
  const agg = sel.agg;
  host.innerHTML =
    `<div class="cstat"><span class="ck">${fEsc(sel.label)} · as of ${fEsc(W.months[i])}</span><span class="cv">${buyers} buying · ${sellers} selling</span></div>`
    + (agg ? `<div class="cstat"><span class="ck">Aggregate net-active</span><span class="cv" style="color:${agg.net_active[i] >= 0 ? _WF_COL.na : '#b3402f'}">${_wfFmt(agg.net_active[i])}</span></div>` : "")
    + (agg && agg.mv ? `<div class="cstat"><span class="ck">Total MF ownership</span><span class="cv">${_wfFmt(agg.mv[i])}</span></div>` : "");
}

function _wfCrowdChart() {
  const W = _wf(), sel = _wfCrowdSel(); if (!W) return;
  if (!sel || sel.loading || !sel.agg) { _wfPlot("plot-wf-crowd", [], {}); return; }
  const node = sel.agg, n = W.months.length, back = parseInt(_wfHz, 10) || 0, h0 = back > 0 ? Math.max(0, n - back) : 0;
  const x = W.months.slice(h0), sl = (arr) => arr.slice(h0);
  const traces = [
    { type: "bar", x, y: sl(node.net_active), name: "Net-active", marker: { color: _WF_COL.na } },
    { type: "bar", x, y: sl(node.inflow), name: "Implied inflow", marker: { color: _WF_COL.inflow } },
    { type: "bar", x, y: sl(node.price), name: "Price action", marker: { color: _WF_COL.price } },
  ];
  _wfPlot("plot-wf-crowd", traces, {
    barmode: "relative", height: 320,
    title: { text: `Aggregate flow — ${sel.label} (₹ cr/month)`, font: { size: 13 } },
    legend: { orientation: "h" }, yaxis: { title: "₹ cr", zeroline: true }, margin: { t: 40 },
  });
}

function _wfCrowdTable() {
  const W = _wf(), host = $("wf-crowd-tbl"); if (!host) return;
  const sel = _wfCrowdSel();
  if (!sel) { host.innerHTML = ""; return; }
  if (sel.loading) { host.innerHTML = `<div class="empty-note">loading…</div>`; return; }
  const i = (_wfSnapIdx == null) ? W.months.length - 1 : _wfSnapIdx;
  const naCol = (v) => (v >= 0 ? _WF_COL.na : "#b3402f");
  const rows = Object.keys(sel.amcs).map((a) => ({ a, n: sel.amcs[a] })).filter((r) => r.n);
  rows.sort((p, q) => (q.n.net_active[i] || 0) - (p.n.net_active[i] || 0));
  const tr = rows.map((r) => {
    const n = r.n;
    return `<tr><td>${fEsc(r.a)}</td>`
      + `<td class="num">${n.mv ? _wfFmt(n.mv[i]) : "—"}</td>`
      + `<td class="num" style="color:${naCol(n.net_active[i])}">${_wfFmt(n.net_active[i])}</td>`
      + `<td class="num" style="color:${_WF_COL.inflow}">${_wfFmt(n.inflow[i])}</td>`
      + `<td class="num" style="color:${_WF_COL.price}">${_wfFmt(n.price[i])}</td>`
      + `<td class="num">${_wfFmt(n.gross[i])}</td></tr>`;
  }).join("");
  host.innerHTML =
    `<div class="ab-screen-head"><b>AMCs · ${fEsc(sel.label)}</b> · as of ${fEsc(W.months[i])} — sorted by net-active (conviction)</div>`
    + `<table class="gauge-tbl wf-pivot"><thead><tr><th>AMC</th><th class="num">Ownership</th><th class="num">Net-active</th><th class="num">Implied inflow</th><th class="num">Price action</th><th class="num">Gross</th></tr></thead><tbody>${tr || `<tr><td colspan="6" class="empty-note">No holders this month.</td></tr>`}</tbody></table>`;
}

// ===== P4 theme lens: flow into NSE thematic indices (a PARALLEL, OVERLAPPING view — themes share
// stocks, so theme rows are NOT additive to the market total). Market-level decomposition + snapshot. =====
function _wfThemePanelHTML(W) {
  const themes = (W && W.themes) || [];
  if (!themes.length) return "";
  if (_wfTheme == null || themes.indexOf(_wfTheme) < 0) _wfTheme = themes[0];
  const opts = themes.map((t) => `<option value="${attEsc(t)}"${t === _wfTheme ? " selected" : ""}>${fEsc(t)}</option>`).join("");
  return `<section class="panel cpanel">
    <h2><span class="tag-sec">THEMES</span>Flow by NSE thematic index — a parallel lens</h2>
    <details><summary>Definition · Method · Why</summary>
      <p><b>What:</b> the same three-way flow decomposition (price action · implied inflow · net-active) summed over the funds' holdings of each <b>NSE thematic index</b>'s constituents — where the market's conviction is tilting across cross-sector themes (Consumption, Energy, PSU, Commodities, Infrastructure …).</p>
      <p><b>Method:</b> for each theme, Σ over the priced holdings of its constituent stocks, per month; constituent membership from niftyindices.com (committed locally). <b>Why:</b> themes cut across the macro-sector backbone and surface narratives (PSU, consumption, commodities) a pure sector view can miss.</p>
      <p class="q-note" style="color:#b3402f"><b>Caveat — NOT additive:</b> themes OVERLAP (a stock belongs to several), so theme rows do <i>not</i> sum to the market total — read each on its own. Coverage = NSE thematic indices whose constituents are publicly published (manufacturing/digital/EV are not, so they're absent).</p>
    </details>
    <div class="ab-ctlrow"><span class="ab-ctllbl">Theme</span><select id="wf-theme-sel" class="ab-sel" style="min-width:200px">${opts}</select></div>
    <div id="wf-theme-head" style="display:flex;flex-wrap:wrap;gap:16px;margin:10px 0"></div>
    <div id="plot-wf-theme"></div>
    <div id="wf-theme-tbl" style="margin-top:10px"></div>
  </section>`;
}

function _wfThemeNode() {
  const W = _wf(); if (!W || !W.theme_total || !_wfTheme) return null;
  return W.theme_total[_wfTheme] || null;
}

function _wfThemeDraw() { _wfThemeHead(); _wfThemeChart(); _wfThemeTable(); }

function _wfThemeHead() {
  const W = _wf(), host = $("wf-theme-head"); if (!host) return;
  const node = _wfThemeNode();
  if (!node || !node.gross || !node.gross.length) { host.innerHTML = ""; return; }
  const i = node.gross.length - 1;
  const stat = (lbl, val, col) => `<div class="cstat"><span class="ck">${lbl}</span><span class="cv" style="color:${col || ''}">${_wfFmt(val)}</span></div>`;
  host.innerHTML =
    `<div class="cstat"><span class="ck">${fEsc(_wfTheme)} · latest ${fEsc(W.months[i])}</span><span class="cv">gross ${_wfFmt(node.gross[i])}</span></div>`
    + (node.mv ? `<div class="cstat"><span class="ck">Ownership (priced)</span><span class="cv">${_wfFmt(node.mv[i])}</span></div>` : "")
    + stat("Price action", node.price[i], _WF_COL.price)
    + stat("Implied inflow", node.inflow[i], _WF_COL.inflow)
    + stat("Net-active (conviction)", node.net_active[i], node.net_active[i] >= 0 ? _WF_COL.na : "#b3402f");
}

function _wfThemeChart() {
  const W = _wf(), node = _wfThemeNode(); if (!W) return;
  if (!node || !node.gross) { _wfPlot("plot-wf-theme", [], {}); return; }
  const n = W.months.length, back = parseInt(_wfHz, 10) || 0;
  const h0 = back > 0 ? Math.max(0, n - back) : 0;
  const x = W.months.slice(h0), sl = (arr) => arr.slice(h0);
  const traces = [
    { type: "bar", x, y: sl(node.net_active), name: "Net-active", marker: { color: _WF_COL.na } },
    { type: "bar", x, y: sl(node.inflow), name: "Implied inflow", marker: { color: _WF_COL.inflow } },
    { type: "bar", x, y: sl(node.price), name: "Price action", marker: { color: _WF_COL.price } },
  ];
  _wfPlot("plot-wf-theme", traces, {
    barmode: "relative", height: 320,
    title: { text: `Flow into ${_wfTheme} (₹ cr/month)`, font: { size: 13 } },
    legend: { orientation: "h" }, yaxis: { title: "₹ cr", zeroline: true }, margin: { t: 40 },
  });
}

function _wfThemeTable() {
  const W = _wf(), host = $("wf-theme-tbl"); if (!host) return;
  const TT = (W && W.theme_total) || {}, themes = (W && W.themes) || [];
  if (!themes.length) { host.innerHTML = ""; return; }
  const i = (_wfSnapIdx == null) ? W.months.length - 1 : _wfSnapIdx;
  const ym = W.months[i];
  const naCol = (v) => (v >= 0 ? _WF_COL.na : "#b3402f");
  const rows = themes.map((t) => ({ t, n: TT[t] })).filter((r) => r.n);
  rows.sort((p, q) => (q.n.net_active[i] || 0) - (p.n.net_active[i] || 0));
  const tr = rows.map((r) => {
    const n = r.n;
    return `<tr class="wf-row${r.t === _wfTheme ? " wf-row-on" : ""}" data-theme="${attEsc(r.t)}">`
      + `<td>${fEsc(r.t)}</td>`
      + `<td class="num">${n.mv ? _wfFmt(n.mv[i]) : "—"}</td>`
      + `<td class="num" style="color:${naCol(n.net_active[i])}">${_wfFmt(n.net_active[i])}</td>`
      + `<td class="num" style="color:${_WF_COL.inflow}">${_wfFmt(n.inflow[i])}</td>`
      + `<td class="num" style="color:${_WF_COL.price}">${_wfFmt(n.price[i])}</td>`
      + `<td class="num">${_wfFmt(n.gross[i])}</td></tr>`;
  }).join("");
  host.innerHTML =
    `<div class="ab-screen-head"><b>NSE themes</b> · as of ${fEsc(ym)} — sorted by net-active. <span style="color:#b3402f">Overlapping — not additive.</span></div>`
    + `<table class="gauge-tbl wf-pivot"><thead><tr><th>Theme</th><th class="num">Ownership</th><th class="num">Net-active</th><th class="num">Implied inflow</th><th class="num">Price action</th><th class="num">Gross</th></tr></thead><tbody>${tr}</tbody></table>`;
  if (!host.dataset.wfwired) {
    host.addEventListener("click", (e) => {
      const trEl = e.target.closest("tr.wf-row"); if (!trEl || !trEl.dataset.theme) return;
      _wfTheme = trEl.dataset.theme;
      const sel = $("wf-theme-sel"); if (sel) sel.value = _wfTheme;
      _wfThemeDraw();
    });
    host.dataset.wfwired = "1";
  }
}

// build the whole breadth scaffold + draw every panel. Guarded; no Plotly key ever set to undefined.
function renderBreadth(B, wrap) {
  if (!B || !B.market || !B.dates) {
    wrap.innerHTML = `<section class="panel"><h2><span class="tag-sec">BREADTH</span>Market breadth</h2>`
      + `<div class="empty-note">No market-breadth data baked into this deck yet.</div></section>`;
    return;
  }
  const meta = B.market_meta || B.meta || {};
  const caveat = meta.caveat || "Descriptive / coincident participation gauge — not a forward signal.";
  const uni = meta.universe || "NSE-500", nsym = meta.n_symbols != null ? meta.n_symbols : "";
  const wins = _abWindows(B.market);
  if (!wins.includes(ALLOC_W)) ALLOC_W = wins[0];
  const winBtns = wins.map((y) => `<button type="button" class="ab-wbtn fs-lvl${y === ALLOC_W ? " on" : ""}" data-w="${y}">${_AB_WLBL[y]}</button>`).join("");

  // only build the static scaffold once (so toggles/inputs keep their state across re-renders)
  if (!wrap.dataset.built) {
    wrap.innerHTML =
      `<section class="panel">
         <h2><span class="tag-sec">BREADTH</span>Market breadth — participation under the index</h2>
         <details><summary>Definition · Method · Why</summary>
           <p><b>What:</b> instead of the index level, breadth counts <i>how many individual stocks</i> are doing a thing. <b>New-high %</b> = share of eligible stocks whose close is the highest over the trailing window (52-week / 3-year / 5-year). <b>New-low %</b> = the mirror. <b>NH−NL</b> = new-high% minus new-low% (the net new-high line). <b>%&gt;200-DMA / %&gt;50-DMA</b> = share above their 200/50-day moving average. <b>%golden-cross</b> = share whose 50-DMA ≥ 200-DMA.</p>
           <p><b>Method:</b> price-derived from our NSE total-return panel (universe = ${fEsc(String(uni))}${nsym ? `, ~${fEsc(String(nsym))} names` : ""}); a stock only counts toward a window once it has enough history (so a new listing can't "make a 5-year high"); the denominator shown ("X% of N") is the eligible count that day.</p>
           <p><b>Why:</b> a rising index carried by a few mega-caps while most stocks bleed is a classic late-cycle tell. <b>Headline = % above 200-DMA</b> (the smoothest participation gauge). ${fEsc(caveat)}</p>
         </details>
         <div class="ab-ctlrow">
           <span class="ab-ctllbl">New-high / low window</span>
           <span class="ab-wseg fs-lvl-seg" id="ab-wseg">${winBtns}</span>
           <span class="ab-linechk" id="ab-linechk"></span>
         </div>
         <div class="plot" id="plot-ab-market" style="height:430px"></div>
         <div class="statline" id="ab-market-stat"></div>
       </section>

       <section class="panel">
         <h2><span class="tag-sec">SECTORS</span>Per-sector breadth</h2>
         <div class="ab-ctlrow">
           <span class="ab-ctllbl">Sector</span>
           <select id="ab-sec-sel" class="ab-sel"></select>
           <span class="ab-ctllbl" style="margin-left:10px">Metric</span>
           <select id="ab-sec-metric" class="ab-sel">
             <option value="pct_above_200dma">% above 200-DMA</option>
             <option value="pct_above_50dma">% above 50-DMA</option>
             <option value="pct_golden_cross">% golden-cross</option>
             <option value="nh">% new-high (window)</option>
             <option value="nl">% new-low (window)</option>
             <option value="nhnl">NH − NL (window)</option>
           </select>
         </div>
         <div class="plot" id="plot-ab-sector" style="height:340px"></div>
         <div class="statline" id="ab-sector-stat"></div>
         <div class="ab-ctlrow" style="margin-top:12px">
           <span class="ab-ctllbl">Relative strength vs NIFTY 500</span>
           <span class="ab-rpseg fs-lvl-seg" id="ab-rp-hz">
             <button type="button" class="fs-lvl" data-hz="1">1Y</button>
             <button type="button" class="fs-lvl on" data-hz="3">3Y</button>
             <button type="button" class="fs-lvl" data-hz="5">5Y</button>
             <button type="button" class="fs-lvl" data-hz="0">MAX</button>
           </span>
         </div>
         <div class="plot" id="plot-ab-relperf" style="height:300px"></div>
         <div class="q-note" style="font-size:12px;margin-top:4px">Sector <b>EW</b> / <b>FF-mcap</b> total-return index ÷ NIFTY 500 TR, rebased to 100 at the window start (rising = leading the market); the dotted line is the <b>NIFTY 500 equal-weight vs cap-weight</b> (broad-vs-megacap breadth). Reconstructed from <i>current</i> index membership + fixed free-float weights — a leadership/breadth <b>context</b> view, increasingly biased over long horizons (read recent windows). Tick/untick lines in the legend.</div>
       </section>

       <section class="panel">
         <h2><span class="tag-sec">SCREEN</span>Sectors with ≥ m% of stocks broken out / golden-crossed</h2>
         <details><summary>Definition · Method · Why</summary>
           <p><b>What:</b> the literal allocator question — which sectors have at least <b>m%</b> of their eligible stocks currently at a new high (breakout) or in a golden-cross. <b>Why:</b> a high-breadth sector is one that is <i>already</i> participating broadly (coincident, not a forecast). Drill into a sector to see exactly which stocks qualify (where the deck carries the name lists).</p>
         </details>
         <div class="ab-ctlrow">
           <span class="ab-ctllbl">Rule</span>
           <span class="ab-ruleseg fs-lvl-seg" id="ab-ruleseg">
             <button type="button" class="fs-lvl on" data-rule="breakout">Broke out (52w high)</button>
             <button type="button" class="fs-lvl" data-rule="golden_cross">Golden-crossed</button>
           </span>
           <span class="ab-ctllbl" style="margin-left:10px">m ≥</span>
           <input type="number" id="ab-m" class="ab-minput" min="0" max="100" step="5" value="${ALLOC_M}"> %
         </div>
         <div class="ab-ctlrow" id="ab-screen-dn"></div>
         <div id="ab-screen-body"></div>
       </section>

       <section class="panel">
         <h2><span class="tag-sec">GLOBAL</span>Global breadth</h2>
         <div id="ab-global-body"></div>
       </section>`;
    wrap.dataset.built = "1";
    // wire the static controls ONCE
    const wseg = $("ab-wseg");
    if (wseg) wseg.querySelectorAll(".ab-wbtn").forEach((b) => b.addEventListener("click", () => {
      ALLOC_W = b.dataset.w; wseg.querySelectorAll(".ab-wbtn").forEach((x) => x.classList.toggle("on", x.dataset.w === ALLOC_W));
      _abDrawMarket(B); _abDrawSector(B); _abDrawScreen(B);
    }));
    const ssel = $("ab-sec-sel"); if (ssel) ssel.addEventListener("change", () => { ALLOC_SEC = ssel.value; _abDrawSector(B); _abDrawRelPerf(B); });
    const smet = $("ab-sec-metric"); if (smet) smet.addEventListener("change", () => _abDrawSector(B));
    const rphz = $("ab-rp-hz");
    if (rphz) rphz.querySelectorAll(".fs-lvl").forEach((b) => b.addEventListener("click", () => {
      ALLOC_RP_HZ = b.dataset.hz; rphz.querySelectorAll(".fs-lvl").forEach((x) => x.classList.toggle("on", x.dataset.hz === ALLOC_RP_HZ));
      _abDrawRelPerf(B);
    }));
    const ruleseg = $("ab-ruleseg");
    if (ruleseg) ruleseg.querySelectorAll(".fs-lvl").forEach((b) => b.addEventListener("click", () => {
      ALLOC_SCREEN_RULE = b.dataset.rule; ruleseg.querySelectorAll(".fs-lvl").forEach((x) => x.classList.toggle("on", x.dataset.rule === ALLOC_SCREEN_RULE));
      _abDrawScreen(B);
    }));
    const minp = $("ab-m"); if (minp) minp.addEventListener("input", () => { const v = parseFloat(minp.value); ALLOC_M = isNaN(v) ? 0 : v; _abDrawScreen(B); });
    const sdn = $("ab-screen-dn"); if (sdn) dateNavControl(sdn, B.dates, ALLOC_SCREEN_IDX, (k) => { ALLOC_SCREEN_IDX = k; _abDrawScreen(B); });
  }

  // line-toggle checkboxes for the market chart (rebuilt each render so labels reflect the window)
  const lineHost = $("ab-linechk");
  if (lineHost && !lineHost.dataset.built) {
    const opts = [
      ["nh", "New-high %", true], ["nl", "New-low %", false], ["nhnl", "NH − NL", false],
      ["a200", "% > 200-DMA", true], ["a50", "% > 50-DMA", false], ["gc", "% golden-cross", false],
    ];
    lineHost.innerHTML = `<span class="ab-ctllbl">Lines</span>` + opts.map(([k, lbl, on]) =>
      `<label class="ab-chk"><input type="checkbox" data-line="${k}"${on ? " checked" : ""}>${lbl}</label>`).join("");
    lineHost.querySelectorAll("input[type=checkbox]").forEach((cb) => cb.addEventListener("change", () => _abDrawMarket(B)));
    lineHost.dataset.built = "1";
  }

  // populate the sector selector (once), default to the first sector
  const ssel = $("ab-sec-sel");
  if (ssel && !ssel.dataset.filled) {
    const secs = B.sectors ? Object.keys(B.sectors).sort() : [];
    ssel.innerHTML = secs.map((s) => `<option value="${attEsc(s)}">${fEsc(s)}</option>`).join("");
    if (!ALLOC_SEC || !secs.includes(ALLOC_SEC)) ALLOC_SEC = secs[0] || null;
    if (ALLOC_SEC) ssel.value = ALLOC_SEC;
    ssel.dataset.filled = secs.length ? "1" : "";
  }

  _abDrawMarket(B);
  _abDrawSector(B);
  _abDrawRelPerf(B);
  _abDrawScreen(B);
  _abDrawGlobal(B);
}

// Sector relative strength vs NIFTY 500: EW + FF-mcap sector TR index ÷ NIFTY 500 TR (baked rel_ew/rel_ff,
// already rebased to 100 at 2000), here RE-REBASED to 100 at the chosen horizon's start so recent leadership
// is readable (the reconstruction's composition bias is small over recent windows, large over long ones).
// Plus the NIFTY 500 EW-vs-cap breadth line. Lines tick/untickable via the Plotly legend.
function _abDrawRelPerf(B) {
  const id = "plot-ab-relperf";
  const el = document.getElementById(id); if (!el) return;
  try {
    const dates = B.dates || [];
    const n = dates.length;
    const sec = ALLOC_SEC, sd = (B.sectors && B.sectors[sec]) || {};
    const hzYears = parseInt(ALLOC_RP_HZ, 10) || 0;
    const start = (hzYears <= 0) ? 0 : Math.max(0, n - hzYears * 12 - 1);
    const rebased = (arr) => {
      if (!Array.isArray(arr)) return null;
      let base = null;
      for (let i = start; i < n; i++) { if (arr[i] !== null && arr[i] !== undefined) { base = arr[i]; break; } }
      if (base === null || base === 0) return null;
      return arr.slice(start).map((v) => (v === null || v === undefined) ? null : Math.round(v / base * 1000) / 10);
    };
    const x = dates.slice(start);
    const ew = rebased(sd.rel_ew), ff = rebased(sd.rel_ff), mk = rebased(B.nifty500_ew_rel);
    const traces = [];
    if (ew) traces.push({ type: "scatter", mode: "lines", name: (sec || "Sector") + " — EW", x: x, y: ew, line: { color: "#1f77b4", width: 1.8 }, connectgaps: true });
    if (ff) traces.push({ type: "scatter", mode: "lines", name: (sec || "Sector") + " — FF-mcap", x: x, y: ff, line: { color: "#d62728", width: 1.8 }, connectgaps: true });
    if (mk) traces.push({ type: "scatter", mode: "lines", name: "NIFTY 500 EW vs cap", x: x, y: mk, line: { color: "#7f7f7f", width: 1.4, dash: "dot" }, connectgaps: true });
    if (!traces.length) { Plotly.purge(id); el.innerHTML = `<div class="empty-note">No relative-performance series for ${fEsc(String(sec || ""))}.</div>`; return; }
    Plotly.purge(id);
    Plotly.react(id, traces, baseLayout({
      yaxis: { title: "rel. to NIFTY 500 (=100 at start)", gridcolor: "#dfe3e8" },
      xaxis: { type: "date", gridcolor: "#dfe3e8" },
      legend: { orientation: "h", y: -0.2, font: { size: 10 } },
      shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: "#aab2bd", width: 1, dash: "dot" } }],
    }), PCONF);
    attachYAutoscale(id);
  } catch (e) { console.error("_abDrawRelPerf:", e); Plotly.purge(id); el.innerHTML = `<div class="empty-note">Relative strength unavailable.</div>`; }
}

// build the set of line traces for a given breadth bundle `m` (market or a single sector) honouring
// the line-checkbox state and the chosen window. Returns {traces, stat}.
function _abTraces(m, dates, opts) {
  opts = opts || {};
  const wantNH = opts.nh, wantNL = opts.nl, wantNHNL = opts.nhnl, wantA200 = opts.a200, wantA50 = opts.a50, wantGC = opts.gc;
  const traces = [];
  const y = ALLOC_W;
  const lastDef = (arr) => { if (!Array.isArray(arr)) return null; for (let i = arr.length - 1; i >= 0; i--) { const v = arr[i]; if (v !== null && v !== undefined && !Number.isNaN(v)) return v; } return null; };
  const add = (arr, name, color, dash) => {
    if (!Array.isArray(arr) || !arr.length) return null;
    const tr = { type: "scatter", mode: "lines", name, x: dates, y: arr, connectgaps: true, line: { color, width: 1.9 } };
    if (dash) tr.line.dash = dash;
    traces.push(tr); return lastDef(arr);
  };
  const wlbl = _AB_WLBL[y] || "";
  const nhLast = wantNH ? add(_abPick(m.pct_new_high, y), `New-high % (${wlbl})`, "#2e7d32") : null;
  const nlLast = wantNL ? add(_abPick(m.pct_new_low, y), `New-low % (${wlbl})`, "#b3402f") : null;
  const nhnlLast = wantNHNL ? add(_abPick(m.nh_minus_nl, y), `NH − NL (${wlbl})`, "#7b1fa2") : null;
  const a200Last = wantA200 ? add(m.pct_above_200dma, "% > 200-DMA", "#1f77b4") : null;
  const a50Last = wantA50 ? add(m.pct_above_50dma, "% > 50-DMA", "#17a2b8", "dot") : null;
  const gcLast = wantGC ? add(m.pct_golden_cross, "% golden-cross", "#d99a2b", "dash") : null;
  const nEl = lastDef(m.eligible_n);
  const bits = [];
  if (a200Last != null) bits.push(`% > 200-DMA: ${_abFmt(a200Last)}`);
  if (nhLast != null) bits.push(`new-high ${wlbl}: ${_abFmt(nhLast)}`);
  if (nlLast != null) bits.push(`new-low ${wlbl}: ${_abFmt(nlLast)}`);
  if (nhnlLast != null) bits.push(`NH−NL: ${_abFmt(nhnlLast)}`);
  if (gcLast != null) bits.push(`golden-cross: ${_abFmt(gcLast)}`);
  const stat = bits.join("  ·  ") + (nEl != null ? `   (of ${Math.round(nEl)} eligible names)` : "");
  return { traces, stat };
}

function _abDrawMarket(B) {
  const el = $("plot-ab-market"); if (!el) return;
  try {
    const m = B.market, dates = B.dates;
    const chk = (k) => { const cb = document.querySelector(`#ab-linechk input[data-line="${k}"]`); return cb ? cb.checked : false; };
    const { traces, stat } = _abTraces(m, dates, { nh: chk("nh"), nl: chk("nl"), nhnl: chk("nhnl"), a200: chk("a200"), a50: chk("a50"), gc: chk("gc") });
    if (!traces.length) { Plotly.purge("plot-ab-market"); el.innerHTML = `<div class="empty-note">Pick at least one line to plot.</div>`; const s = $("ab-market-stat"); if (s) s.textContent = ""; return; }
    Plotly.purge("plot-ab-market");   // clear prior plot/empty-note cleanly (innerHTML="" leaves stale Plotly state → blank on re-draw)
    Plotly.react("plot-ab-market", traces, baseLayout({
      yaxis: { title: "% of eligible stocks", gridcolor: "#dfe3e8", rangemode: "tozero" },
      xaxis: { gridcolor: "#dfe3e8", rangeslider: { thickness: 0.07 }, type: "date" },
    }), PCONF);
    attachYAutoscale("plot-ab-market");
    // #47 cycle-position readout for the headline % > 200-DMA, where its own history exists
    const cyc = _cyclePctile(m.pct_above_200dma);
    const cycTxt = (cyc != null) ? `  Cycle position: % > 200-DMA is at the ${cyc}ᵗʰ percentile of its own ${_abWLen(m)}.` : "";
    const s = $("ab-market-stat"); if (s) s.textContent = `Latest readings — ${stat}.${cycTxt}  Coincident participation gauge, not a forward signal.`;
  } catch (e) { console.error("_abDrawMarket:", e); Plotly.purge("plot-ab-market"); el.innerHTML = `<div class="empty-note">Market breadth chart unavailable.</div>`; }
}

function _abDrawSector(B) {
  const el = $("plot-ab-sector"); if (!el) return;
  try {
    const sec = ALLOC_SEC, m = B.sectors && sec ? B.sectors[sec] : null;
    const metSel = $("ab-sec-metric"); const metric = metSel ? metSel.value : "pct_above_200dma";
    if (!m) { Plotly.purge("plot-ab-sector"); el.innerHTML = `<div class="empty-note">No breadth for this sector.</div>`; const s = $("ab-sector-stat"); if (s) s.textContent = ""; return; }
    const y = ALLOC_W, wlbl = _AB_WLBL[y] || "";
    let arr = null, title = "", color = "#1f77b4";
    if (metric === "nh") { arr = _abPick(m.pct_new_high, y); title = `New-high % (${wlbl})`; color = "#2e7d32"; }
    else if (metric === "nl") { arr = _abPick(m.pct_new_low, y); title = `New-low % (${wlbl})`; color = "#b3402f"; }
    else if (metric === "nhnl") { arr = _abPick(m.nh_minus_nl, y); title = `NH − NL (${wlbl})`; color = "#7b1fa2"; }
    else if (metric === "pct_above_50dma") { arr = m.pct_above_50dma; title = "% > 50-DMA"; color = "#17a2b8"; }
    else if (metric === "pct_golden_cross") { arr = m.pct_golden_cross; title = "% golden-cross"; color = "#d99a2b"; }
    else { arr = m.pct_above_200dma; title = "% > 200-DMA"; color = "#1f77b4"; }
    if (!Array.isArray(arr) || !arr.length) { Plotly.purge("plot-ab-sector"); el.innerHTML = `<div class="empty-note">No “${fEsc(title)}” series for ${fEsc(sec)}.</div>`; const s = $("ab-sector-stat"); if (s) s.textContent = ""; return; }
    Plotly.purge("plot-ab-sector");   // clear prior plot/empty-note cleanly (innerHTML="" leaves stale Plotly state → blank on dropdown change)
    const tr = { type: "scatter", mode: "lines", name: `${sec} — ${title}`, x: B.dates, y: arr, connectgaps: true, line: { color, width: 2 } };
    Plotly.react("plot-ab-sector", [tr], baseLayout({
      yaxis: { title: "% of eligible stocks", gridcolor: "#dfe3e8", rangemode: (metric === "nhnl" ? "normal" : "tozero") },
      xaxis: { gridcolor: "#dfe3e8", type: "date" },
    }), PCONF);
    attachYAutoscale("plot-ab-sector");
    const lastDef = (a) => { for (let i = a.length - 1; i >= 0; i--) { const v = a[i]; if (v != null && !Number.isNaN(v)) return v; } return null; };
    const nEl = Array.isArray(m.eligible_n) ? lastDef(m.eligible_n) : null;
    const s = $("ab-sector-stat"); if (s) s.textContent = `${sec} · ${title}: latest ${_abFmt(lastDef(arr))}${nEl != null ? ` (of ${Math.round(nEl)} eligible)` : ""}.`;
  } catch (e) { console.error("_abDrawSector:", e); Plotly.purge("plot-ab-sector"); el.innerHTML = `<div class="empty-note">Sector breadth chart unavailable.</div>`; }
}

function _abDrawScreen(B) {
  const host = $("ab-screen-body"); if (!host) return;
  try {
    const rule = ALLOC_SCREEN_RULE, m = Math.max(0, Math.min(100, ALLOC_M));
    // prefer screen_current (rich, per-sector pcts); fall back to snapshot.sectors
    const sc = B.screen_current || null;
    const snapSecs = (B.snapshot && B.snapshot.sectors) ? B.snapshot.sectors : null;
    const snapBy = {}; if (snapSecs) snapSecs.forEach((r) => { if (r && r.sector != null) snapBy[r.sector] = r; });
    // pick the breakout pct for the active window from screen_current keys, else from snapshot
    const breakoutPct = (sec, row) => {
      if (row) {
        if (ALLOC_W === "5" && row.pct_breakout_5y != null) return row.pct_breakout_5y;
        if (ALLOC_W === "3" && row.pct_breakout_3y != null) return row.pct_breakout_3y;
        if (row.pct_breakout != null) return row.pct_breakout;
      }
      const s = snapBy[sec];
      if (s) { if (ALLOC_W === "5" && s.pct_new_high_5y != null) return s.pct_new_high_5y; if (ALLOC_W === "3" && s.pct_new_high_3y != null) return s.pct_new_high_3y; return s.pct_new_high_1y; }
      return null;
    };
    const gcPct = (sec, row) => (row && row.pct_golden_cross != null) ? row.pct_golden_cross : (snapBy[sec] ? snapBy[sec].pct_golden_cross : null);
    const a200Pct = (sec, row) => (row && row.pct_above_200dma != null) ? row.pct_above_200dma : (snapBy[sec] ? snapBy[sec].pct_above_200dma : null);
    const nOf = (sec, row) => (row && row.n != null) ? row.n : (snapBy[sec] ? snapBy[sec].n : null);
    const thinOf = (sec, row) => (row && row.thin != null) ? !!row.thin : (snapBy[sec] ? !!snapBy[sec].thin : false);

    // time-nav: latest uses screen_current (rich, with name lists); a past date is derived from the
    // per-sector breadth SERIES baked in B.sectors (no name drill-down at past dates — not baked).
    const _dates = B.dates || [];
    const _latestIdx = _dates.length - 1;
    const _idx = (ALLOC_SCREEN_IDX == null) ? _latestIdx : Math.max(0, Math.min(_latestIdx, ALLOC_SCREEN_IDX));
    const _atLatest = (_idx === _latestIdx) || _latestIdx < 0;
    const _asofLbl = (_dates.length && _idx >= 0) ? _dates[_idx] : "latest";
    let rows;
    if (_atLatest) {
      const secNames = sc ? Object.keys(sc) : (snapSecs ? snapSecs.map((r) => r.sector) : []);
      if (!secNames.length) { host.innerHTML = `<div class="empty-note">No per-sector snapshot in this deck.</div>`; return; }
      rows = secNames.map((sec) => {
        const row = sc ? sc[sec] : null;
        const pct = rule === "golden_cross" ? gcPct(sec, row) : breakoutPct(sec, row);
        return { sec, pct, n: nOf(sec, row), thin: thinOf(sec, row), bk: breakoutPct(sec, row), gc: gcPct(sec, row), a200: a200Pct(sec, row), snap: snapBy[sec] || null };
      }).filter((r) => r.pct != null && !r.thin);
    } else {
      const secObj = B.sectors || {};
      const pickAt = (arr) => (Array.isArray(arr) && arr[_idx] != null) ? arr[_idx] : null;
      rows = Object.keys(secObj).map((sec) => {
        const s = secObj[sec];
        const bk = (s && s.pct_new_high) ? pickAt(s.pct_new_high[ALLOC_W]) : null;
        const gc = s ? pickAt(s.pct_golden_cross) : null;
        const a200 = s ? pickAt(s.pct_above_200dma) : null;
        const n = s ? pickAt(s.eligible_n) : null;
        const pct = rule === "golden_cross" ? gc : bk;
        return { sec, pct, n, thin: false, bk, gc, a200, snap: null };
      }).filter((r) => r.pct != null);
      if (!rows.length) { host.innerHTML = `<div class="empty-note">No per-sector breadth on ${fEsc(String(_asofLbl))}.</div>`; return; }
    }
    rows.sort((a, b) => (b.pct || 0) - (a.pct || 0));
    const hit = rows.filter((r) => r.pct >= m);
    const wlbl = _AB_WLBL[ALLOC_W] || "";
    const ruleLbl = rule === "golden_cross" ? "golden-crossed" : `at a ${wlbl} high`;

    let h = `<div class="ab-screen-head">${hit.length} of ${rows.length} sectors have ≥ <b>${m}%</b> of stocks ${ruleLbl} <span class="namuted">(as of ${fEsc(String(_asofLbl))}${_atLatest ? "" : " — historical; drag to latest for name drill-downs"})</span>.</div>`;
    h += `<table class="gauge-tbl ab-screen-tbl"><thead><tr><th>Sector</th><th class="num">n</th>`
       + `<th class="num">% ${rule === "golden_cross" ? "golden-cross" : "breakout (" + wlbl + ")"}</th>`
       + `<th class="num">% golden-cross</th><th class="num">% &gt; 200-DMA</th><th></th></tr></thead><tbody>`;
    rows.forEach((r, ri) => {
      const on = r.pct >= m;
      const names = r.snap ? (rule === "golden_cross" ? r.snap.names_golden_cross : (r.snap.names_new_high_1y || r.snap.names_new_high)) : null;
      const drill = (Array.isArray(names) && names.length)
        ? `<button type="button" class="ab-drill" data-ri="${ri}">${names.length} names ▾</button>` : "";   // index-based id (escaping-safe)
      h += `<tr class="${on ? "ab-hit" : ""}">`
        + `<td>${fEsc(r.sec)}</td>`
        + `<td class="num">${r.n != null ? r.n : "—"}</td>`
        + `<td class="num ${on ? "pos" : ""}"><b>${_abFmt(r.pct)}</b></td>`
        + `<td class="num">${_abFmt(r.gc)}</td>`
        + `<td class="num">${_abFmt(r.a200)}</td>`
        + `<td>${drill}</td></tr>`;
      if (Array.isArray(names) && names.length) {
        h += `<tr class="ab-names" id="ab-names-${ri}" hidden><td colspan="6"><div class="ab-namelist">${names.map((nm) => `<span class="ab-name">${fEsc(nm)}</span>`).join("")}</div></td></tr>`;
      }
    });
    h += `</tbody></table>`;
    h += `<div class="q-note" style="font-size:12px;margin-top:6px">“% of n eligible” — a sector with too few eligible names is hidden (no breadth number). High breadth = the sector is <i>already</i> participating broadly; this is coincident, not a forecast.</div>`;
    host.innerHTML = h;
    host.querySelectorAll(".ab-drill").forEach((b) => b.addEventListener("click", () => {
      const row = document.getElementById("ab-names-" + b.dataset.ri);
      if (row) { row.hidden = !row.hidden; b.classList.toggle("on", !row.hidden); }
    }));
  } catch (e) { console.error("_abDrawScreen:", e); host.innerHTML = `<div class="empty-note">Sector screen unavailable.</div>`; }
}

function _abDrawGlobal(B) {
  const host = $("ab-global-body"); if (!host) return;
  try {
    const g = B.global || B.global_proxy || null;
    let lead = "";
    if (g && (g.n_above_200dma != null || g.above_200dma != null)) {
      const nA = (g.n_above_200dma != null) ? g.n_above_200dma : g.above_200dma;
      const nT = (g.n_total != null) ? g.n_total : g.total;
      const nH = (g.n_new_high != null) ? g.n_new_high : g.new_high;
      const parts = [];
      if (nA != null && nT != null) parts.push(`<b>${nA} of ${nT}</b> global equity indices are above their 200-DMA`);
      if (nH != null && nT != null) parts.push(`<b>${nH} of ${nT}</b> are at a 52-week high`);
      if (parts.length) lead = `<div class="ab-global-read">${parts.join(" · ")}.</div>`;
    }
    host.innerHTML = lead
      + `<div class="ab-note-box">True breadth of a global index/ETF = the % of its <i>members</i> at highs / above their 200-DMA. We don’t yet hold constituent / membership data — the world panel is index-<b>level</b> only — so ${lead ? "the figure above is a <b>level-proxy diffusion</b> across regional indices, not member breadth" : "member-level global breadth isn’t computable yet"}. Coming once constituent data lands (see SHOPPING_LIST items 1–4).</div>`;
  } catch (e) { console.error("_abDrawGlobal:", e); host.innerHTML = `<div class="ab-note-box">Global breadth: coming once constituent data lands.</div>`; }
}

async function renderMacro() {
  // (Analyst Consensus Flow moved to the Asset Allocator tab — it's an allocation lens; see renderAllocator.)
  buildMacroDom();
  if (LAZY && LAZY.world) {                 // hosted: fetch the world series the panels/snapshot need
    const need = new Set();
    MACRO_PANELS.forEach((p) => {
      (p.series || []).forEach((sd) => { const src = (sd[2] && sd[2].src) || p.src; if (src === "world") need.add(sd[0]); });
      if (p.curve && p.src === "world") p.curve.forEach((n) => need.add(n));
    });
    ["USD / INR", "US Dollar Index (DXY)", "Gold", "Crude Oil (Brent)", "CBOE Volatility Index (VIX)", "India VIX"].forEach((n) => need.add(n));
    await ensureWorldLoaded([...need]);
  }
  const wi = macroFrameI(), wo = worldFrameM();
  if (!wi && !wo) { if ($("macro-snap")) $("macro-snap").innerHTML = "<div class='empty-note'>No macro data in this deck — run “Pull India Macro.bat” and “Pull World Markets.bat”.</div>"; return; }
  // India-first snapshot strip; entries with no data skip. unit: "%"=points, "cr"=₹ crore, ""=level.
  const snap = [
    ["RBI repo rate", "Repo", "%", "india"], ["CPI inflation — Combined (YoY)", "CPI", "%", "india"],
    ["WPI inflation (YoY)", "WPI", "%", "india"], ["91-day T-bill yield", "T-bill 3M", "%", "india"],
    ["India 10Y G-sec yield", "India 10Y", "%", "india"],
    ["FII/FPI net (cash)", "FII net", "cr", "india"], ["DII net (cash)", "DII net", "cr", "india"],
    ["USD / INR", "USD/INR", "", "world"], ["US Dollar Index (DXY)", "DXY", "", "world"],
    ["Gold", "Gold", "", "world"], ["Crude Oil (Brent)", "Brent", "", "world"],
    ["CBOE Volatility Index (VIX)", "VIX", "", "world"], ["India VIX", "India VIX", "", "world"],
  ];
  if ($("macro-snap")) $("macro-snap").innerHTML = snap.map(([nm, lbl, unit, src]) => {
    const s = macroSlice(nm, src); if (!s) return "";
    const yv = s.y.filter((v) => v !== null && v !== undefined); if (!yv.length) return "";
    const last = yv[yv.length - 1], first = yv[0];
    const isPct = unit === "%", isCr = unit === "cr", isCr0 = unit === "cr0";
    let val, chg, chgStr;
    if (isPct) { val = last.toFixed(2) + "%"; chg = last - first; chgStr = (chg >= 0 ? "+" : "") + chg.toFixed(2) + "pp"; }
    else if (isCr || isCr0) { val = (isCr && last >= 0 ? "+" : "") + Math.round(last).toLocaleString(); chg = last - first; chgStr = (chg >= 0 ? "+" : "") + Math.round(chg).toLocaleString(); }
    else { val = last >= 1000 ? Math.round(last).toLocaleString() : last.toFixed(2); chg = (last / first - 1) * 100; chgStr = (chg >= 0 ? "+" : "") + chg.toFixed(1) + "%"; }
    const cls = chg >= 0 ? "pos" : "neg";
    return `<div class="msnap"><span class="mk">${lbl}</span><span class="mv">${val}</span><span class="mc ${cls}">${chgStr}</span></div>`;
  }).join("");
  // render panels; hide empties; build a region-grouped nav from what's visible
  const visible = [];
  MACRO_PANELS.forEach((p) => {
    const sec = $("p-macro-" + p.id);
    const items = macroPanelSeries(p);            // series that actually have data (toggle-independent)
    if (!items.length) { if (sec) sec.style.display = "none"; return; }
    if (sec) sec.style.display = "";
    buildMacroToggle(p, items);                   // per-panel "show" bar (isolate one / pick N / all)
    renderMacroPanel(p);                          // render honouring the per-panel hidden set
    visible.push(p);
  });
  const nav = $("macro-nav");
  if (nav) { let h = "", reg = ""; visible.forEach((p) => { if (p.region !== reg) { reg = p.region; h += `<span class="navgrp">${reg}</span>`; } h += `<a href="#p-macro-${p.id}">${p.tag}</a>`; }); nav.innerHTML = h; }
  afterPaint(() => viewPlotsResize("macro"));
}

// =================================================================== command palette · cross-links · deep-link
// One search box that finds ANY entity (index, stock, world instrument, fundamentals
// company) and jumps you to the right tab with it loaded — the "type-anything-GO" of a
// real terminal. Plus shareable URL hashes so any view is bookmarkable.
let CMDK_ENTS = [], CMDK_HITS = [], CMDK_SEL = 0;

// fuzzy descriptor for a palette entity: acronym from the friendly label, with the
// ticker/name + aliases + any extra (AMC/category) widening substring/typo matching.
function cmdkPrep(e) {
  if (e._fz === undefined) e._fz = fuzzyPrep(e.label || e.name, e.name + " " + (e.aliases ? e.aliases.join(" ") : "") + " " + (e.extra || ""));
  return e._fz;
}
function cmdkScore(e, q) { return q ? fuzzyScore(q, cmdkPrep(e)) : 1; }
function buildEntityIndex() {
  const ents = [], byName = {};
  ((CAT && CAT.indices) || []).forEach((o) => {
    if (byName[o.name]) return;
    const e = { name: o.name, label: o.label || "", group: o.group, kind: (o.group === "Stocks" ? "stock" : "series"), aliases: o.aliases || null };
    ents.push(e); byName[o.name] = e;
  });
  const manifest = fundManifest();
  const fund = manifest || FUND_DATA || {};
  Object.keys(fund).forEach((sym) => {
    const nm = manifest ? (fund[sym] || "") : ((fund[sym] && fund[sym].name) || "");
    if (byName[sym]) { byName[sym].hasFund = true; }
    else { const e = { name: sym, label: nm, group: "Company", kind: "company", hasFund: true }; ents.push(e); byName[sym] = e; }
  });
  const mf = macroFrameI();
  if (mf && mf.series) Object.keys(mf.series).forEach((nm) => {
    if (byName[nm]) return;
    const mm = (mf.meta && mf.meta[nm]) || {};
    const e = { name: nm, label: mm.source || "", group: "Macro" + (mm.group ? " · " + mm.group : ""), kind: "macro" };
    ents.push(e); byName[nm] = e;
  });
  CMDK_ENTS = ents;
}
function entityActions(e) {
  const acts = [];
  if (e.kind === "macro") { acts.push({ label: "View in Macro", run: () => gotoMacro() }); return acts; }
  if (e.hasFund || e.kind === "company") acts.push({ label: "Fundamentals", run: () => gotoFundamentals(e.name) });
  if (e.kind !== "company") acts.push({ label: "Add to chart", run: () => gotoPerformance([e.name]) });
  return acts;
}
function gotoMacro() { setView("macro"); }
function gotoPerformance(names) {
  if (typeof MS_T !== "undefined" && MS_T) names.forEach((n) => MS_T.add(n));
  setView("performance"); run();
}
function gotoFundamentals(sym) {
  const m = fundManifest();
  const avail = m ? m[sym] : (FUND_DATA && FUND_DATA[sym]);
  if (!avail) { toast("No fundamentals for " + sym + " yet.", "err"); return; }
  FUND_SYM = sym; if (FUND_SYM2 === sym) FUND_SYM2 = null;
  if (FUND_COMBO) FUND_COMBO.setValue(sym);
  setView("fundamentals");
}
function cmdkOpen() { buildEntityIndex(); const m = $("cmdk"); if (!m) return; m.hidden = false; const inp = $("cmdk-input"); if (inp) { inp.value = ""; inp.focus(); } cmdkRender(""); }
function cmdkClose() { const m = $("cmdk"); if (m) m.hidden = true; }
function cmdkRender(q) {
  q = (q || "").trim().toLowerCase();
  let arr = CMDK_ENTS.map((e) => [e, cmdkScore(e, q)]).filter((x) => x[1] > 0);
  if (q) arr.sort((a, b) => b[1] - a[1]);   // best match first
  CMDK_HITS = arr.slice(0, 60).map((x) => x[0]);
  CMDK_SEL = 0;
  const list = $("cmdk-list"); if (!list) return;
  list.innerHTML = CMDK_HITS.length ? CMDK_HITS.map((e, i) => {
    const acts = entityActions(e);
    const chips = acts.map((a, j) => `<span class="cmdk-act${j === 0 ? " prim" : ""}" data-i="${i}" data-a="${j}">${a.label}</span>`).join("");
    const sub = (e.label && e.label !== e.name) ? `<span class="cmdk-sub">${e.label}</span>` : `<span class="cmdk-sub"></span>`;
    const tag = e.group + ((e.hasFund && e.kind === "stock") ? " · fundamentals" : "");
    return `<div class="cmdk-row${i === 0 ? " sel" : ""}" data-i="${i}"><span class="cmdk-nm">${e.name}</span>${sub}<span class="cmdk-grp">${tag}</span>${chips}</div>`;
  }).join("") : `<div class="cmdk-empty">No match for “${q}”.</div>`;
  list.querySelectorAll(".cmdk-act").forEach((c) => c.addEventListener("mousedown", (ev) => { ev.preventDefault(); ev.stopPropagation(); const e = CMDK_HITS[+c.dataset.i]; const a = entityActions(e)[+c.dataset.a]; if (a) a.run(); cmdkClose(); }));
  list.querySelectorAll(".cmdk-row").forEach((r) => r.addEventListener("mousedown", (ev) => { if (ev.target.classList.contains("cmdk-act")) return; ev.preventDefault(); cmdkPick(+r.dataset.i); }));
}
function cmdkPick(i) { const e = CMDK_HITS[i]; if (!e) return; const acts = entityActions(e); if (acts.length) acts[0].run(); cmdkClose(); }
function cmdkMove(d) {
  if (!CMDK_HITS.length) return;
  CMDK_SEL = (CMDK_SEL + d + CMDK_HITS.length) % CMDK_HITS.length;
  const rows = $("cmdk-list").querySelectorAll(".cmdk-row");
  rows.forEach((r, i) => r.classList.toggle("sel", i === CMDK_SEL));
  if (rows[CMDK_SEL] && rows[CMDK_SEL].scrollIntoView) rows[CMDK_SEL].scrollIntoView({ block: "nearest" });
}
function initCmdk() {
  const openBtn = $("cmdk-open"); if (openBtn) openBtn.addEventListener("click", cmdkOpen);
  const inp = $("cmdk-input");
  if (inp) {
    inp.addEventListener("input", () => cmdkRender(inp.value));
    inp.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); cmdkMove(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); cmdkMove(-1); }
      else if (e.key === "Enter") { e.preventDefault(); cmdkPick(CMDK_SEL); }
      else if (e.key === "Escape") { e.preventDefault(); cmdkClose(); }
    });
  }
  const m = $("cmdk"); if (m) m.addEventListener("mousedown", (e) => { if (e.target === m) cmdkClose(); });
  document.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : "";
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); cmdkOpen(); }
    else if (e.key === "/" && tag !== "input" && tag !== "textarea" && tag !== "select") { e.preventDefault(); cmdkOpen(); }
  });
}

// ---- shareable deep-link hash (view + selection) ----
let HASH_APPLYING = false;
function writeHash() {
  if (HASH_APPLYING || typeof location === "undefined" || typeof history === "undefined") return;
  try {
    let h = VIEW;
    if (VIEW === "fundamentals" && FUND_SYM) h = "fundamentals=" + encodeURIComponent(FUND_SYM);
    else if (VIEW === "performance" && typeof MS_T !== "undefined" && MS_T) {
      const u = MS_T.value.map(encodeURIComponent).join(","), b = MS_B.value.map(encodeURIComponent).join(",");
      h = "performance" + ((u || b) ? ("?u=" + u + "&b=" + b) : "");
    }
    history.replaceState(null, "", "#" + h);
  } catch (e) {}
}
function _qparse(s) { const o = {}; (s || "").split("&").forEach((kv) => { const i = kv.indexOf("="); if (i > 0) o[kv.slice(0, i)] = kv.slice(i + 1); }); return o; }
function applyHash() {
  if (typeof location === "undefined") return;
  const raw = (location.hash || "").replace(/^#/, ""); if (!raw) return;
  HASH_APPLYING = true;
  try {
    if (raw.indexOf("fundamentals") === 0) {
      const sym = raw.indexOf("=") > 0 ? decodeURIComponent(raw.split("=")[1]) : null;
      const _m = fundManifest();           // lazy: data isn't loaded yet — match the manifest so the
      const known = sym && ((_m && _m[sym]) || (FUND_DATA && FUND_DATA[sym]));  // deep-link selection sticks
      if (known) { FUND_SYM = sym; if (FUND_COMBO) FUND_COMBO.setValue(sym); }
      setView("fundamentals");
    } else if (raw.indexOf("valuation") === 0) { setView("valuation"); }
    else if (raw.indexOf("allocator") === 0) { setView("allocator"); }
    else if (raw.indexOf("ownership") === 0) { setView("ownership"); }
    else if (raw.indexOf("macro") === 0) { setView("macro"); }
    else if (raw.indexOf("screen") === 0) { setView("screen"); }
    else if (raw.indexOf("performance") === 0) {
      const qi = raw.indexOf("?");
      if (qi >= 0 && typeof MS_T !== "undefined" && MS_T) {
        const p = _qparse(raw.slice(qi + 1));
        const avail = new Set(((CAT && CAT.indices) || []).map((o) => o.name));
        const dec = (s) => (s || "").split(",").filter(Boolean).map(decodeURIComponent).filter((n) => avail.has(n));
        const u = dec(p.u), b = dec(p.b);
        if (u.length) MS_T.setValue(u);
        if (b.length) MS_B.setValue(b);
      }
      setView("performance");
    }
  } catch (e) {}
  HASH_APPLYING = false;
}

window.addEventListener("DOMContentLoaded", init);
