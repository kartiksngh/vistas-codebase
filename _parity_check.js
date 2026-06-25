/* Parity harness (2/2): run the JS port on the SAME embedded data + configs and
 * diff every field against the Python analyze() bundles dumped by _parity_dump.py.
 * Run:  node _parity_check.js   (after  python _parity_dump.py) */
const fs = require("fs");
const path = require("path");
const VA = require("./static/vistas_analytics.js");

const OUT = path.join(__dirname, "_parity");
const CONFIGS = JSON.parse(fs.readFileSync(path.join(OUT, "configs.json")));
const _DS = {};
const ds = (name) => _DS[name] || (_DS[name] = JSON.parse(fs.readFileSync(path.join(OUT, `dataset_${name}.json`))));

const ATOL = 1e-7, RTOL = 1e-6;
let worst = { diff: 0, path: "", a: null, b: null };
let mismatches = [];

function close(a, b) {
  if (a === null || a === undefined) return b === null || b === undefined;
  if (b === null || b === undefined) return false;
  if (typeof a === "number" && typeof b === "number") {
    if (a === b) return true;
    const d = Math.abs(a - b);
    return d <= ATOL || d <= RTOL * Math.max(Math.abs(a), Math.abs(b));
  }
  return a === b;
}

function walk(a, b, p) {
  if (Array.isArray(a)) {
    if (!Array.isArray(b)) { mismatches.push(`${p}: type (array vs ${typeof b})`); return; }
    if (a.length !== b.length) { mismatches.push(`${p}: length ${a.length} vs ${b.length}`); return; }
    for (let i = 0; i < a.length; i++) walk(a[i], b[i], `${p}[${i}]`);
  } else if (a && typeof a === "object") {
    if (!b || typeof b !== "object") { mismatches.push(`${p}: type (object vs ${typeof b})`); return; }
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) {
      if (!(k in a)) { mismatches.push(`${p}.${k}: missing in JS`); continue; }
      if (!(k in b)) { mismatches.push(`${p}.${k}: missing in PY`); continue; }
      walk(a[k], b[k], `${p}.${k}`);
    }
  } else {
    if (!close(a, b)) {
      mismatches.push(`${p}: JS=${a} PY=${b}`);
      if (typeof a === "number" && typeof b === "number") {
        const d = Math.abs(a - b);
        if (d > worst.diff) worst = { diff: d, path: p, a, b };
      }
    }
  }
}

let totalMismatch = 0;
for (let i = 0; i < CONFIGS.length; i++) {
  const py = JSON.parse(fs.readFileSync(path.join(OUT, `py_${i}.json`)));
  const js = VA.analyze(ds(CONFIGS[i]._dataset || "main"), CONFIGS[i]);
  mismatches = [];
  walk(js, py, `cfg${i}`);
  totalMismatch += mismatches.length;
  const tag = mismatches.length === 0 ? "OK " : "DIFF";
  console.log(`[${tag}] cfg${i} (${CONFIGS[i].freq}/${CONFIGS[i].alpha_type}/${CONFIGS[i].rolling_window}, ` +
    `${CONFIGS[i].tickers.length}t+${CONFIGS[i].benchmarks.length}b) : ${mismatches.length} mismatches`);
  for (const m of mismatches.slice(0, 8)) console.log("        " + m);
  if (mismatches.length > 8) console.log(`        … +${mismatches.length - 8} more`);
}

// ===== valuation parity =====
const VAL_CONFIGS = JSON.parse(fs.readFileSync(path.join(OUT, "val_configs.json")));
const valDs = JSON.parse(fs.readFileSync(path.join(OUT, "dataset_val.json")));
for (let i = 0; i < VAL_CONFIGS.length; i++) {
  const py = JSON.parse(fs.readFileSync(path.join(OUT, `val_py_${i}.json`)));
  const js = VA.valuationAnalyze(valDs, VAL_CONFIGS[i]);
  mismatches = [];
  walk(js, py, `val${i}`);
  totalMismatch += mismatches.length;
  const tag = mismatches.length === 0 ? "OK " : "DIFF";
  console.log(`[${tag}] val${i} (${VAL_CONFIGS[i].measure}/${VAL_CONFIGS[i].kind}/${VAL_CONFIGS[i].freq}, ` +
    `${VAL_CONFIGS[i].tickers.length}t+${VAL_CONFIGS[i].benchmarks.length}b) : ${mismatches.length} mismatches`);
  for (const m of mismatches.slice(0, 8)) console.log("        " + m);
  if (mismatches.length > 8) console.log(`        … +${mismatches.length - 8} more`);
}

console.log("-----------------------------------------------------------");
console.log(`TOTAL mismatches: ${totalMismatch}`);
console.log(`worst numeric diff: ${worst.diff.toExponential(3)} at ${worst.path} (JS=${worst.a} PY=${worst.b})`);
process.exit(totalMismatch === 0 ? 0 : 1);
