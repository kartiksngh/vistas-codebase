/* Runtime smoke-test of the OFFLINE deck (no browser).
 *
 * Drives the REAL static/vistas.js (init -> offline compute via vistas_analytics.js
 * -> renderAll for every panel) against the ACTUAL data+catalog embedded in the
 * generated deck, using a fake DOM + a Plotly stub. Catches the burned-lesson
 * class of bug: a runtime throw in a render function (which vistas.js swallows into
 * an "Analyze error" toast and a blank panel). Also exercises a re-selection +
 * weekly toggle to prove client-side recomputation works offline.
 *
 * Run:  node _deck_runtime_test.js [path-to-deck.html]
 */
const fs = require("fs"), vm = require("vm"), path = require("path");
const ROOT = __dirname;
const deckPath = process.argv[2] || path.join(ROOT, "output", "Vistas_Passive_Deck_latest.html");

// --- pull the embedded data+catalog out of the real deck (test the actual artifact).
// Boundaries are matched CR/LF-agnostically (Python text-mode write uses \r\n on Windows).
const deck = fs.readFileSync(deckPath, "utf8");
function slab(s, a, end) {
  const i = s.indexOf(a) + a.length;
  const j = s.indexOf(end, i);
  let v = s.slice(i, j).trim();
  if (v.endsWith(";")) v = v.slice(0, -1).trim();
  return v;
}
const isV2 = deck.indexOf("window.VISTAS_MEASURES=") !== -1;
let DATA, CATALOG, MEASURES = null, STOCKS = null, WORLD = null, MACRO = null, FUND = null, LAZY = null, FUND_MANIFEST = null, QUANT_MANIFEST = null, FUNDS_HOLDINGS_MANIFEST = null, FUNDS_ATTR_MANIFEST = null, CONSENSUS = null;
if (isV2) {                       // Terminal Deck v2: TR/PR + valuation measures (+ stocks + world + fundamentals)
  MEASURES = JSON.parse(slab(deck, "window.VISTAS_MEASURES=", "window.VISTAS_DATA="));
  CATALOG = JSON.parse(slab(deck, "window.VISTAS_CATALOG=", "window.VISTAS_VERSION="));
  DATA = MEASURES.TR;
  // embed order: MEASURES, DATA, [STOCKS], [WORLD], [MACRO], [FUNDAMENTALS], [MANIFEST], [LAZY], CATALOG, VERSION
  const hasWorld = deck.indexOf("window.VISTAS_WORLD=") !== -1;
  const hasMacro = deck.indexOf("window.VISTAS_MACRO=") !== -1;
  const hasFund = deck.indexOf("window.VISTAS_FUNDAMENTALS=") !== -1;
  const hasManifest = deck.indexOf("window.VISTAS_FUND_MANIFEST=") !== -1;
  const hasQuant = deck.indexOf("window.VISTAS_QUANT_MANIFEST=") !== -1;
  const hasFundsHold = deck.indexOf("window.VISTAS_FUNDS_HOLDINGS_MANIFEST=") !== -1;
  const hasFundsAttr = deck.indexOf("window.VISTAS_FUNDS_ATTR_MANIFEST=") !== -1;
  const hasLazy = deck.indexOf("window.VISTAS_LAZY=") !== -1;
  const afterFundsAttr = hasLazy ? "window.VISTAS_LAZY=" : "window.VISTAS_CATALOG=";    // boundary AFTER funds-attr manifest
  const afterFundsHold = hasFundsAttr ? "window.VISTAS_FUNDS_ATTR_MANIFEST=" : afterFundsAttr; // boundary AFTER funds-holdings manifest
  const afterQuant = hasFundsHold ? "window.VISTAS_FUNDS_HOLDINGS_MANIFEST=" : afterFundsHold;   // boundary AFTER quant manifest
  const afterManifest = hasQuant ? "window.VISTAS_QUANT_MANIFEST=" : afterQuant;        // boundary AFTER fund manifest
  const afterFund = hasManifest ? "window.VISTAS_FUND_MANIFEST=" : afterManifest;
  const afterMacro = hasFund ? "window.VISTAS_FUNDAMENTALS=" : afterFund;
  const afterWorld = hasMacro ? "window.VISTAS_MACRO=" : afterMacro;
  if (deck.indexOf("window.VISTAS_STOCKS=") !== -1) {
    const stkEnd = hasWorld ? "window.VISTAS_WORLD=" : afterWorld;
    try { STOCKS = JSON.parse(slab(deck, "window.VISTAS_STOCKS=", stkEnd)); } catch (e) {}
  }
  if (hasWorld) { try { WORLD = JSON.parse(slab(deck, "window.VISTAS_WORLD=", afterWorld)); } catch (e) {} }
  if (hasMacro) { try { MACRO = JSON.parse(slab(deck, "window.VISTAS_MACRO=", afterMacro)); } catch (e) {} }
  if (hasFund) { try { FUND = JSON.parse(slab(deck, "window.VISTAS_FUNDAMENTALS=", afterFund)); } catch (e) {} }
  if (hasManifest) { try { FUND_MANIFEST = JSON.parse(slab(deck, "window.VISTAS_FUND_MANIFEST=", afterManifest)); } catch (e) {} }
  if (hasQuant) { try { QUANT_MANIFEST = JSON.parse(slab(deck, "window.VISTAS_QUANT_MANIFEST=", afterQuant)); } catch (e) {} }
  if (hasFundsHold) { try { FUNDS_HOLDINGS_MANIFEST = JSON.parse(slab(deck, "window.VISTAS_FUNDS_HOLDINGS_MANIFEST=", afterFundsHold)); } catch (e) {} }
  if (hasFundsAttr) { try { FUNDS_ATTR_MANIFEST = JSON.parse(slab(deck, "window.VISTAS_FUNDS_ATTR_MANIFEST=", afterFundsAttr)); } catch (e) {} }
  if (hasLazy) { try { LAZY = JSON.parse(slab(deck, "window.VISTAS_LAZY=", "window.VISTAS_CATALOG=")); } catch (e) {} }
  // Analyst Consensus Flow (#46): embedded between market_flows and survivorship (then lazy/catalog).
  if (deck.indexOf("window.VISTAS_CONSENSUS=") !== -1) {
    const cEnd = deck.indexOf("window.VISTAS_SURVIVORSHIP=") !== -1 ? "window.VISTAS_SURVIVORSHIP="
               : (hasLazy ? "window.VISTAS_LAZY=" : "window.VISTAS_CATALOG=");
    try { CONSENSUS = JSON.parse(slab(deck, "window.VISTAS_CONSENSUS=", cEnd)); } catch (e) {}
  }
  console.log(`[deck v2] ${path.basename(deckPath)}: measures=${Object.keys(MEASURES).join(",")}, ` +
    `${Object.keys(DATA.series).length} TR indices, ${DATA.dates.length} dates` +
    (LAZY ? ` [LAZY hosted shell: ${(LAZY.indices && LAZY.indices.TR || []).length} idx + ${(LAZY.world || []).length} world fetchable]` : "") +
    (STOCKS ? `, ${Object.keys(STOCKS.series).length} stocks` : "") +
    (WORLD ? `, ${Object.keys(WORLD.series).length} world` : "") +
    (MACRO ? `, ${Object.keys(MACRO.series).length} macro` : "") +
    (FUND ? `, ${Object.keys(FUND).length} fundamentals` : "") +
    (FUND_MANIFEST ? `, ${Object.keys(FUND_MANIFEST).length} fund-manifest` : "") +
    (QUANT_MANIFEST ? `, ${Object.keys(QUANT_MANIFEST).length} quant-manifest` : ""));
} else {                          // legacy Passive v1: TR only
  DATA = JSON.parse(slab(deck, "window.VISTAS_DATA=", "window.VISTAS_CATALOG="));
  CATALOG = JSON.parse(slab(deck, "window.VISTAS_CATALOG=", "</script>"));
  console.log(`[deck v1] ${path.basename(deckPath)}: ${Object.keys(DATA.series).length} indices, ${DATA.dates.length} dates, ${CATALOG.indices.length} catalog entries`);
}

const analyticsJs = fs.readFileSync(path.join(ROOT, "static", "vistas_analytics.js"), "utf8");
const appJs = fs.readFileSync(path.join(ROOT, "static", "vistas.js"), "utf8");

// --------------------------------------------------------------- fake DOM
const toasts = [];
const plots = {};            // id -> { fn, traces, layout }
function CL() { const s = new Set(); return { add: (x) => s.add(x), remove: (x) => s.delete(x), toggle: (x, on) => (on === undefined ? (s.has(x) ? s.delete(x) : s.add(x)) : (on ? s.add(x) : s.delete(x))), contains: (x) => s.has(x) }; }
const SEGS = {
  "alpha-seg": [["alpha", true], ["beta", false]],
  "risk-seg": [["drawdown", true], ["vol", false], ["sharpe", false], ["corr", false], ["relstrength", false]],
  "presets": [["1", false], ["3", false], ["5", false], ["10", false], ["0", true]],
};
function segButtons(id) {
  return (SEGS[id] || []).map(([k, active]) => ({
    dataset: id === "presets" ? { y: k } : { k }, classList: (() => { const c = CL(); if (active) c.add("active"); return c; })(),
    addEventListener() {},
  }));
}
const cache = {};
function FakeEl(id) {
  const el = {
    id, _html: "", value: "", min: "", max: "", checked: false, textContent: "", dataset: {}, style: {}, classList: CL(),
    set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html; },
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {}, hasAttribute() { return false; }, select() {}, blur() {},
    addEventListener() {}, removeEventListener() {}, set onchange(f) {}, get onchange() { return null; },
    appendChild(child) { if (this.id === "toast" && child && child._html !== undefined) toasts.push(child._html); },
    remove() {}, focus() {}, click() {}, getContext() { return {}; },
    querySelector(sel) {
      if (sel === "button.active") { const b = segButtons(this.id).find((x) => x.classList.contains("active")); return b || null; }
      return (this["_q_" + sel] || (this["_q_" + sel] = FakeEl(this.id + sel)));
    },
    querySelectorAll(sel) { if (sel === "button") return segButtons(this.id); return []; },
  };
  return el;
}
let domReady = null;
const onDOM = (type, fn) => { if (type === "DOMContentLoaded") domReady = fn; };
const document = {
  getElementById: (id) => cache[id] || (cache[id] = FakeEl(id)),
  createElement: () => FakeEl("x"),
  querySelectorAll: () => [],
  querySelector: () => null,
  addEventListener: onDOM,
  removeEventListener() {},
};

// --------------------------------------------------------------- Plotly + globals stub
// The stub stores traces, but it ALSO mimics the one cleanData check that bit us: real Plotly
// does `'line' in trace.marker` (and similar), so a trace with an OWN key (marker/line/fill/
// transforms) explicitly set to `undefined` throws "Cannot use 'in' operator …" and blanks the
// panel. The pure-store stub never caught that (burned 2026-06-22) — validate it here.
function _validateTraces(id, data) {
  (data || []).forEach((t, i) => {
    if (t === undefined || t === null) throw new Error(`Plotly[${id}]: data[${i}] is ${t} (cleanData throws)`);
    ["marker", "line", "fill", "transforms", "error_x", "error_y"].forEach((k) => {
      if (Object.prototype.hasOwnProperty.call(t, k) && t[k] === undefined)
        throw new Error(`Plotly[${id}]: data[${i}].${k} === undefined — real cleanData throws "Cannot use 'in' operator to search for 'line' in undefined". Build the trace with only the keys that apply (don't set marker/line to undefined).`);
    });
  });
}
const Plotly = {
  react(id, traces, layout) { _validateTraces(id, traces); plots[id] = { fn: "react", traces, layout }; },
  newPlot(id, traces, layout) { _validateTraces(id, traces); plots[id] = { fn: "newPlot", traces, layout }; },
  purge(id) { delete plots[id]; },
};
// HOSTED shell (LAZY present) legitimately fetches per-symbol files; serve them off disk so
// the lazy fetch+merge+compute path is validated end-to-end. The SINGLE-FILE deck (no LAZY)
// must NEVER fetch — keep the throwing stub there (the burned-lesson guard).
const siteDir = path.dirname(deckPath);
let FETCHES = 0;
function fakeFetch(url) {
  FETCHES++;
  return new Promise((resolve) => {
    try {
      // a real server decodes the request path ONCE; the on-disk filename is the decoded form
      // (= Python quote(name), with literal %xx). The client double-encodes, so decode once here.
      const fp = path.join(siteDir, decodeURIComponent(String(url)));
      if (!fs.existsSync(fp)) { resolve({ ok: false, status: 404, json: () => Promise.resolve(null) }); return; }
      const txt = fs.readFileSync(fp, "utf8");
      resolve({ ok: true, status: 200, json: () => Promise.resolve(JSON.parse(txt)) });
    } catch (e) { resolve({ ok: false, status: 500, json: () => Promise.resolve(null) }); }
  });
}
const sandbox = {
  document, Plotly, console,
  window: null, VistasAnalytics: null, VISTAS_DATA: DATA, VISTAS_CATALOG: CATALOG, VISTAS_MEASURES: MEASURES, VISTAS_STOCKS: STOCKS, VISTAS_WORLD: WORLD, VISTAS_MACRO: MACRO, VISTAS_FUNDAMENTALS: FUND, VISTAS_LAZY: LAZY, VISTAS_FUND_MANIFEST: FUND_MANIFEST, VISTAS_QUANT: null, VISTAS_QUANT_MANIFEST: QUANT_MANIFEST, VISTAS_FUNDS_HOLDINGS_MANIFEST: FUNDS_HOLDINGS_MANIFEST, VISTAS_FUNDS_ATTR_MANIFEST: FUNDS_ATTR_MANIFEST, VISTAS_CONSENSUS: CONSENSUS,
  setTimeout: (f) => { if (typeof f === "function") f(); return 0; }, clearTimeout() {},
  Blob: function () {}, URL: { createObjectURL: () => "" },
  prompt: () => null, confirm: () => true,
  fetch: LAZY ? fakeFetch : () => { throw new Error("fetch called in OFFLINE deck!"); },
  addEventListener: onDOM, removeEventListener() {},     // vistas.js binds DOMContentLoaded on window
  Math, JSON, Object, Array, Number, String, Date, isFinite, isNaN, parseFloat, parseInt, Set, Map,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

// load analytics port then the app (both attach to window)
try { vm.runInContext(analyticsJs, sandbox, { filename: "vistas_analytics.js" }); }
catch (e) { console.log("analytics LOAD THREW:", String(e.message).slice(0, 300)); process.exit(2); }
try { vm.runInContext(appJs, sandbox, { filename: "vistas.js" }); }
catch (e) { console.log("app LOAD THREW:", String(e.message).slice(0, 300)); process.exit(2); }

(async () => {
  // fire DOMContentLoaded -> init() -> run() -> renderAll()
  if (!domReady) { console.log("FAIL: no DOMContentLoaded handler registered"); process.exit(2); }
  try { await domReady(); } catch (e) { console.log("init() THREW:", String(e.message).slice(0, 300)); process.exit(2); }
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));   // flush async microtasks

  const PANELS = ["plot-gp", "plot-comp", "plot-alpha", "plot-risk", "plot-corrmat", "plot-capture", "plot-cy", "plot-cy-alpha", "plot-month"];
  function report(tag, requireAll = true) {
    const errs = toasts.filter((t) => /error/i.test(t));
    const missing = PANELS.filter((p) => !plots[p]);
    const dens = Object.keys(plots).filter((k) => /^dret_|^dalp_/.test(k));
    console.log(`\n[${tag}]`);
    console.log("  panels rendered :", PANELS.filter((p) => plots[p]).length + "/" + PANELS.length, missing.length ? "(MISSING: " + missing.join(",") + ")" : "");
    console.log("  density charts  :", dens.length);
    console.log("  error toasts    :", errs.length, errs.length ? "-> " + errs.slice(0, 3).join(" | ") : "");
    // sanity on GP: each NAV trace y-length must equal #dates
    const gp = plots["plot-gp"];
    if (gp) {
      const x = gp.traces[0] && gp.traces[0].x ? gp.traces[0].x.length : 0;
      const bad = gp.traces.filter((tr) => (tr.y || []).length !== x);
      console.log("  GP traces       :", gp.traces.length, "x-len", x, bad.length ? "(LEN MISMATCH!)" : "(lengths OK)");
    }
    // default load now has NO default tickers (benchmark-only by design) -> the RELATIVE panels
    // (alpha/corr/capture/cy-alpha) legitimately can't draw; require only 0 errors + GP there.
    return errs.length === 0 && (requireAll ? missing.length === 0 : !!gp);
  }
  let ok = report("default render (benchmark-only by design — no default tickers)", false);

  // re-selection + weekly toggle -> forces a fresh client-side recompute
  toasts.length = 0; for (const k in plots) delete plots[k];
  try {
    vm.runInContext('MS_T.setValue(["NIFTY 50","NIFTY MIDCAP 100","NIFTY BANK"]); MS_B.setValue(["NIFTY 500"]); ' +
      '$("freq").value="weekly"; $("rollwin").value="3Y"; $("alphatype").value="jensen"; run();', sandbox);
  } catch (e) { console.log("RESELECT THREW:", e.message); ok = false; }
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
  ok = report("re-selection (weekly · 3Y · Jensen · 3 indices)") && ok;

  // ---- v2: Valuation tab + Price-Return toggle ----
  if (isV2 && MEASURES && (MEASURES.PE || MEASURES.PB || MEASURES.DY)) {
    const VAL_PANELS = ["plot-vallevel", "plot-valxsec", "plot-valspread", "plot-valdist"];
    function reportVal(tag) {
      const errs = toasts.filter((t) => /error/i.test(t));
      const missing = VAL_PANELS.filter((p) => !plots[p]);
      const gauge = (cache["val-gauge"] && cache["val-gauge"]._html) ? "yes" : "no";
      console.log(`\n[${tag}]`);
      console.log("  valuation panels:", VAL_PANELS.filter((p) => plots[p]).length + "/" + VAL_PANELS.length, missing.length ? "(MISSING: " + missing.join(",") + ")" : "");
      console.log("  gauge table     :", gauge, "  error toasts:", errs.length, errs.length ? "-> " + errs.slice(0, 3).join(" | ") : "");
      return errs.length === 0 && missing.length === 0 && gauge === "yes";
    }
    toasts.length = 0; for (const k in plots) delete plots[k];
    try { vm.runInContext('MS_T.setValue(["NIFTY 50","NIFTY MIDCAP 100","NIFTY BANK"]); MS_B.setValue(["NIFTY 500"]); $("freq").value="daily"; $("rollwin").value="1Y"; VAL_MEASURE="PE"; run();', sandbox); }
    catch (e) { console.log("VAL run THREW:", e.message); ok = false; }
    for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r));
    try { vm.runInContext('setView("valuation");', sandbox); } catch (e) { console.log("setView THREW:", e.message); ok = false; }
    for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
    ok = reportVal("Valuation tab · P/E (3 indices vs NIFTY 500)") && ok;

    toasts.length = 0; for (const k in plots) delete plots[k];
    try { vm.runInContext('VAL_MEASURE="DY"; runValuation(true);', sandbox); } catch (e) { console.log("VAL DY THREW:", e.message); ok = false; }
    for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
    ok = reportVal("Valuation tab · Div Yield (yield polarity)") && ok;
  }
  if (isV2 && MEASURES && MEASURES.PR) {
    toasts.length = 0; for (const k in plots) delete plots[k];
    try { vm.runInContext('setView("performance"); PERF_MEASURE="PR"; run();', sandbox); } catch (e) { console.log("PR TOGGLE THREW:", e.message); ok = false; }
    for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r));
    ok = report("Performance · Price-Return toggle") && ok;
  }
  if (isV2 && STOCKS && Object.keys(STOCKS.series).length) {
    const stk = Object.keys(STOCKS.series)[0];
    toasts.length = 0; for (const k in plots) delete plots[k];
    try { vm.runInContext(`setView("performance"); PERF_MEASURE="TR"; MS_T.setValue(["${stk}","NIFTY 50"]); MS_B.setValue(["NIFTY 500"]); run();`, sandbox); }
    catch (e) { console.log("STOCK MIX THREW:", e.message); ok = false; }
    for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r));
    ok = report(`Performance · stock+index mix (${stk} + NIFTY 50 vs NIFTY 500)`) && ok;
  }
  if (isV2 && WORLD && Object.keys(WORLD.series).length) {
    const w = Object.keys(WORLD.series)[0];
    toasts.length = 0; for (const k in plots) delete plots[k];
    try { vm.runInContext(`setView("performance"); PERF_MEASURE="TR"; MS_T.setValue([${JSON.stringify(w)},"NIFTY 50"]); MS_B.setValue(["NIFTY 500"]); run();`, sandbox); }
    catch (e) { console.log("WORLD MIX THREW:", e.message); ok = false; }
    for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r));
    ok = report(`Performance · world+index mix (${w} + NIFTY 50 vs NIFTY 500)`) && ok;
  }

  // ---- v2: Prices view — active MF NAV (lazy funds_nav) selectable next to indices ----
  if (isV2 && LAZY && LAZY.funds && LAZY.funds.length) {
    const fund = LAZY.funds[0];
    toasts.length = 0; for (const k in plots) delete plots[k];
    try { vm.runInContext(`setView("performance"); PERF_MEASURE="TR"; MS_T.setValue([${JSON.stringify(fund)}]); MS_B.setValue(["NIFTY 500"]); run();`, sandbox); }
    catch (e) { console.log("FUND MIX THREW:", e.message); ok = false; }
    for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r));
    ok = report(`Prices · MF NAV (${fund.slice(0, 38)}… vs NIFTY 500)`, false) && ok;
    const gp = plots["plot-gp"];                 // the fund NAV must actually resolve into the GP
    const key = fund.slice(0, 18);
    const got = !!(gp && gp.traces && gp.traces.some((t) => String(t.name || "").indexOf(key) >= 0));
    console.log("  MF NAV series resolved into GP:", got ? "yes" : "NO");
    if (!got) ok = false;
  }

  // ---- v2: Fundamentals tab (Screener) — single + compare ----
  if (isV2 && ((FUND && Object.keys(FUND).length) || (FUND_MANIFEST && Object.keys(FUND_MANIFEST).length))) {
    const fsyms = (FUND && Object.keys(FUND).length ? Object.keys(FUND) : Object.keys(FUND_MANIFEST)).sort();
    const sym = fsyms[0];
    // current Fundamentals panel ids (the A–J rewrite) — 16 plot panels; some can be legitimately empty
    const FPANELS = ["plot-fund-price", "plot-fund-pe", "plot-fund-eps", "plot-fund-growthlvl", "plot-fund-growthyoy", "plot-fund-margins", "plot-fund-returns", "plot-fund-dupont3", "plot-fund-dupont5", "plot-fund-cashflow", "plot-fund-earnq", "plot-fund-balance", "plot-fund-leverage", "plot-fund-quality", "plot-fund-cycle", "plot-fund-share"];
    const reportFund = (tag) => {
      const errs = toasts.filter((t) => /error/i.test(t));
      const present = FPANELS.filter((p) => plots[p]);
      const missing = FPANELS.filter((p) => !plots[p]);   // some panels can be legitimately empty (e.g. a bank has no inventory days)
      const hdr = (cache["fund-header"] && cache["fund-header"]._html) ? "yes" : "no";
      const tbl = (cache["fund-table"] && cache["fund-table"]._html) ? "yes" : "no";
      console.log(`\n[${tag}]`);
      console.log("  fundamentals panels:", present.length + "/" + FPANELS.length, missing.length ? "(empty/none: " + missing.join(",") + ")" : "");
      console.log("  header:", hdr, " table:", tbl, " error toasts:", errs.length, errs.length ? "-> " + errs.slice(0, 3).join(" | ") : "");
      return errs.length === 0 && hdr === "yes" && present.length >= 12;   // no throws + most of the 16 panels draw
    };
    toasts.length = 0; for (const k in plots) delete plots[k];
    // lazy shell: renderFundamentals() awaits ensureFundamentals(FUND_SYM) -> per-symbol fetch; flush enough microtasks
    try { vm.runInContext(`FUND_SYM=${JSON.stringify(sym)}; FUND_SYM2=null; setView("fundamentals"); renderFundamentals();`, sandbox); }
    catch (e) { console.log("FUND TAB THREW:", e.message); ok = false; }
    for (let i = 0; i < 16; i++) await new Promise((r) => setImmediate(r));
    ok = reportFund(`Fundamentals · single (${sym})`) && ok;

    if (fsyms.length > 1) {
      const sym2 = fsyms[1];
      toasts.length = 0; for (const k in plots) delete plots[k];
      try { vm.runInContext(`FUND_SYM=${JSON.stringify(sym)}; FUND_SYM2=${JSON.stringify(sym2)}; FUND_STMT="balance_sheet"; renderFundamentals();`, sandbox); }
      catch (e) { console.log("FUND COMPARE THREW:", e.message); ok = false; }
      for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
      ok = reportFund(`Fundamentals · compare (${sym} vs ${sym2}) + Balance-Sheet table`) && ok;
    }
  }

  // ---- v2: Quant & MI tab (per-stock cockpit; reuses stock_intel.py per-symbol JSON) ----
  if (isV2 && QUANT_MANIFEST && Object.keys(QUANT_MANIFEST).length) {
    const qsyms = Object.keys(QUANT_MANIFEST).sort();
    const qsym = qsyms[0];
    const QPANELS = ["plot-quant-returns", "plot-quant-rs", "plot-quant-own"];   // some may be legit empty (no overlap / no shareholding)
    const reportQuant = (tag) => {
      const errs = toasts.filter((t) => /error/i.test(t));
      const present = QPANELS.filter((p) => plots[p]);
      const bodyHtml = (cache["quant-body"] && cache["quant-body"]._html) || "";
      const hasSnap = /Research snapshot/.test(bodyHtml);
      console.log(`\n[${tag}]`);
      console.log("  quant charts:", present.length + "/" + QPANELS.length, "  body chars:", bodyHtml.length, "  snapshot:", hasSnap ? "yes" : "no", "  error toasts:", errs.length, errs.length ? "-> " + errs.slice(0, 3).join(" | ") : "");
      return errs.length === 0 && bodyHtml.length > 500 && hasSnap && present.length >= 1;   // no throws + cards render + ≥1 chart
    };
    toasts.length = 0; for (const k in plots) delete plots[k];
    // lazy shell: renderQuant() awaits ensureQuant(QUANT_SYM) -> per-symbol fetch from data/quant/<SYM>.json
    try { vm.runInContext(`setView("quant"); QUANT_SYM=${JSON.stringify(qsym)}; renderQuant();`, sandbox); }
    catch (e) { console.log("QUANT TAB THREW:", e.message); ok = false; }
    for (let i = 0; i < 16; i++) await new Promise((r) => setImmediate(r));
    ok = reportQuant(`Quant & MI · ${qsym}`) && ok;

    if (qsyms.length > 1) {
      const qsym2 = qsyms[1];
      toasts.length = 0; for (const k in plots) delete plots[k];
      try { vm.runInContext(`QUANT_SYM=${JSON.stringify(qsym2)}; renderQuant();`, sandbox); }
      catch (e) { console.log("QUANT RE-SELECT THREW:", e.message); ok = false; }
      for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r));
      ok = reportQuant(`Quant & MI · re-select (${qsym2})`) && ok;
    }
  }

  // ---- v2: Funds tab (mutual-fund portfolio holdings; reuses funds_portfolio.py per-scheme JSON) ----
  if (isV2 && FUNDS_HOLDINGS_MANIFEST && Object.keys(FUNDS_HOLDINGS_MANIFEST).length) {
    const fkeys = Object.keys(FUNDS_HOLDINGS_MANIFEST).sort();
    const fkey = fkeys[0];
    const reportFunds = (tag) => {
      const errs = toasts.filter((t) => /error/i.test(t));
      const body = (cache["funds-body"] && cache["funds-body"]._html) || "";
      const hasAsset = !!plots["plot-funds-asset"];
      console.log(`\n[${tag}]`);
      console.log("  funds body chars:", body.length, "  asset chart:", hasAsset ? "yes" : "no", "  error toasts:", errs.length, errs.length ? "-> " + errs.slice(0, 3).join(" | ") : "");
      return errs.length === 0 && body.length > 400 && hasAsset;
    };
    toasts.length = 0; for (const k in plots) delete plots[k];
    try { vm.runInContext(`setView("funds"); FUNDS_SYM=${JSON.stringify(fkey)}; renderFunds();`, sandbox); }
    catch (e) { console.log("FUNDS TAB THREW:", e.message); ok = false; }
    for (let i = 0; i < 16; i++) await new Promise((r) => setImmediate(r));
    ok = reportFunds(`Funds · ${fkey}`) && ok;

    if (fkeys.length > 1) {
      const fkey2 = fkeys[1];
      toasts.length = 0; for (const k in plots) delete plots[k];
      try { vm.runInContext(`FUNDS_SYM=${JSON.stringify(fkey2)}; renderFunds();`, sandbox); }
      catch (e) { console.log("FUNDS RE-SELECT THREW:", e.message); ok = false; }
      for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r));
      ok = reportFunds(`Funds · re-select (${fkey2})`) && ok;
    }
  }

  // ---- v2: Fund Skill tab (holdings-based manager attribution; funds_attribution.py per-scheme JSON) ----
  if (isV2 && FUNDS_ATTR_MANIFEST && Object.keys(FUNDS_ATTR_MANIFEST).length) {
    const skeys = Object.keys(FUNDS_ATTR_MANIFEST).sort();
    const skey = skeys[0];
    const reportSkill = (tag) => {
      const errs = toasts.filter((t) => /error/i.test(t));
      const body = (cache["fundskill-body"] && cache["fundskill-body"]._html) || "";
      const hasCum = !!plots["plot-fs-cum"];
      console.log(`\n[${tag}]`);
      console.log("  fundskill body chars:", body.length, "  growth chart:", hasCum ? "yes" : "no", "  error toasts:", errs.length, errs.length ? "-> " + errs.slice(0, 3).join(" | ") : "");
      return errs.length === 0 && body.length > 400 && hasCum;
    };
    toasts.length = 0; for (const k in plots) delete plots[k];
    try { vm.runInContext(`setView("fundskill"); FUNDSKILL_SYM=${JSON.stringify(skey)}; renderFundSkill();`, sandbox); }
    catch (e) { console.log("FUND SKILL TAB THREW:", e.message); ok = false; }
    for (let i = 0; i < 18; i++) await new Promise((r) => setImmediate(r));
    ok = reportSkill(`Fund Skill · ${skey}`) && ok;
  }

  // ---- command palette: build the entity index, search, and "GO" navigation ----
  if (isV2) {
    try {
      vm.runInContext('buildEntityIndex(); cmdkRender("nifty");', sandbox);
      const nEnts = vm.runInContext("CMDK_ENTS.length", sandbox);
      const nHits = vm.runInContext("CMDK_HITS.length", sandbox);
      vm.runInContext("if (CMDK_HITS.length) cmdkPick(0);", sandbox);   // navigate — must not throw
      console.log(`\n[command palette] indexed ${nEnts} entities · "nifty" -> ${nHits} hits · GO ok`);
      if (!nEnts || !nHits) { console.log("  (palette index/hits unexpectedly empty)"); ok = false; }
    } catch (e) { console.log("CMDK THREW:", e.message); ok = false; }
  }

  // ---- Macro tab (India-native + global cross-asset) ----
  const worldAvail = (WORLD && Object.keys(WORLD.series).length) || (LAZY && LAZY.world && LAZY.world.length);
  if (isV2 && (worldAvail || (MACRO && Object.keys(MACRO.series).length))) {
    toasts.length = 0; for (const k in plots) delete plots[k];
    try { vm.runInContext('setView("macro");', sandbox); }
    catch (e) { console.log("MACRO THREW:", e.message); ok = false; }
    for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r));   // lazy: world fetched on tab open
    const GLOBAL = ["plot-macro-yields", "plot-macro-fx", "plot-macro-commod", "plot-macro-vol", "plot-macro-credit", "plot-macro-crypto"];
    const INDIA = ["plot-macro-infl", "plot-macro-wpicomp", "plot-macro-inrates", "plot-macro-incurve", "plot-macro-money", "plot-macro-reserves", "plot-macro-trade", "plot-macro-activity", "plot-macro-flows"];
    const gp = GLOBAL.filter((p) => plots[p]), ip = INDIA.filter((p) => plots[p]);
    const snap = (cache["macro-snap"] && cache["macro-snap"]._html) ? "yes" : "no";
    const errs = toasts.filter((t) => /error/i.test(t));
    console.log(`\n[Macro tab] global ${gp.length}/${GLOBAL.length} · india ${ip.length}/${INDIA.length} (${ip.map((p) => p.replace("plot-macro-", "")).join(",") || "-"}) · snapshot ${snap} · errors ${errs.length}`);
    if (errs.length) { console.log("  (macro errors)"); ok = false; }
    if (worldAvail && gp.length < 5) { console.log("  (global macro underpopulated)"); ok = false; }
    // x-axis clip regression guard (burned bug): the macro `dates` array runs back to 2000
    // (the RBI repo rate), but the IIP panel's series start 2012 and the inflation panel's
    // earliest (WPI) starts 2013. Their x-range must be clipped to the data span, NOT left to
    // autorange across the full union (which painted a long empty band on the left). Assert
    // range[0] is well after 2000 for any of these panels that rendered.
    [["plot-macro-activity", "2011"], ["plot-macro-infl", "2011"]].forEach(([id, floor]) => {
      if (!plots[id]) return;
      const r = plots[id].layout && plots[id].layout.xaxis && plots[id].layout.xaxis.range;
      if (!r) { console.log(`  (x-clip MISSING on ${id} — empty left band would show)`); ok = false; }
      else if (String(r[0]) < floor) { console.log(`  (x-clip too wide on ${id}: starts ${r[0]}, want > ${floor})`); ok = false; }
      else console.log(`  [x-clip] ${id.replace("plot-macro-", "")}: ${r[0]} -> ${r[1]} (left band removed)`);
    });
  }

  if (LAZY) {
    console.log(`\n[lazy hosting] ${FETCHES} on-demand fetches served from disk (indices/world/stocks/fundamentals)`);
    if (!FETCHES) { console.log("  (LAZY shell but nothing was fetched — lazy path not exercised)"); ok = false; }
  }
  console.log("\n" + (ok ? "PASS: deck renders every panel with no runtime errors." : "FAIL: see above."));
  process.exit(ok ? 0 : 1);
})();
