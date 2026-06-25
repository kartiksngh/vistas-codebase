/* Vistas — CLIENT-SIDE analytics (offline deck only).
 *
 * A faithful JavaScript port of vistas/analytics.py so a self-contained offline
 * HTML deck can recompute every panel in the browser (re-pick indices / window /
 * frequency) with NO server. It produces the SAME bundle shape analytics.analyze
 * returns, so static/vistas.js renders it unchanged.
 *
 * It is verified to match the Python engine numerically (parity harness:
 * _parity_check.* — JS vs Python on the same embedded data). If you change a
 * formula in analytics.py, mirror it here AND re-run the parity check, or the
 * offline numbers will drift from the live app.
 *
 * Conventions mirrored verbatim (see analytics.py provenance header):
 *   returns = simple pct-change (fill_method=None); weekly = W-FRI last;
 *   CAGR = (last/first)^(365/days)-1; vol = std·√ppy; Sharpe = (mean-rf)·ppy/vol;
 *   maxDD = min(level/cummax-1); ddof=1 for std/var/cov/corr; Gaussian KDE = scipy
 *   Scott's rule. ppy = 252 daily / 52 weekly. Rolling: min_periods = round(0.8·window)
 *   (any NaN in the window -> NaN), computed on the EXTENDED series then sliced to
 *   the window so they seed from the window start.
 */
var VistasAnalytics = (function () {
  "use strict";

  const PPY = { daily: 252.0, weekly: 52.0 };
  // mirror of analytics.MAX_WINDOW_GAP_DAYS — in-window calendar gap above this = non-continuous series
  const MAX_WINDOW_GAP_DAYS = 90;
  const WINDOW_PERIODS = {
    daily:  { "1M": 21, "3M": 63, "6M": 126, "1Y": 252, "2Y": 504, "3Y": 756, "5Y": 1260 },
    weekly: { "1M": 4,  "3M": 13, "6M": 26,  "1Y": 52,  "2Y": 104, "3Y": 156, "5Y": 260 },
  };
  const DIST_HORIZONS = {
    daily:  [["Monthly", 21, false], ["Quarterly", 63, false], ["Yearly", 252, false], ["3Y CAGR", 756, true]],
    weekly: [["Monthly", 4, false], ["Quarterly", 13, false], ["Yearly", 52, false], ["3Y CAGR", 156, true]],
  };
  const MIN_DENS_PTS = 12;
  const DAY = 86400000;

  // ------------------------------------------------------------- numeric helpers
  const isNum = (x) => x !== null && x !== undefined && typeof x === "number" && isFinite(x);
  const cl = (x) => (x === null || x === undefined || !isFinite(x)) ? null : x;

  function avg(a) { let s = 0; for (const x of a) s += x; return s / a.length; }
  function stdPop(a) { const m = avg(a); let s = 0; for (const x of a) { const d = x - m; s += d * d; } return Math.sqrt(s / a.length); }
  function var1(a) { if (a.length < 2) return NaN; const m = avg(a); let s = 0; for (const x of a) { const d = x - m; s += d * d; } return s / (a.length - 1); }
  function std1(a) { const v = var1(a); return v >= 0 ? Math.sqrt(v) : NaN; }
  function cov1(x, y) { if (x.length < 2) return NaN; const mx = avg(x), my = avg(y); let s = 0; for (let i = 0; i < x.length; i++) s += (x[i] - mx) * (y[i] - my); return s / (x.length - 1); }
  function corr1(x, y) { const c = cov1(x, y), sx = std1(x), sy = std1(y); return (sx > 0 && sy > 0) ? c / (sx * sy) : NaN; }
  function aMin(a) { let m = Infinity; for (const x of a) if (x < m) m = x; return m; }
  function aMax(a) { let m = -Infinity; for (const x of a) if (x > m) m = x; return m; }
  function linspace(a, b, n) { const out = new Array(n); const step = (b - a) / (n - 1); for (let i = 0; i < n; i++) out[i] = a + step * i; return out; }

  // ------------------------------------------------------------- date helpers
  function parse(s) { const p = s.split("-"); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }
  function fmt(ms) { const d = new Date(ms); const y = d.getUTCFullYear(); const m = String(d.getUTCMonth() + 1).padStart(2, "0"); const da = String(d.getUTCDate()).padStart(2, "0"); return `${y}-${m}-${da}`; }
  // W-FRI bin label = the Friday on/after the date (getUTCDay: Fri=5).
  function fridayLabel(ms) { const wd = new Date(ms).getUTCDay(); const add = ((5 - wd) % 7 + 7) % 7; return ms + add * DAY; }

  // returns (pandas pct_change, fill_method=None): r[0]=null; null where cur/prev missing.
  function pctchg(arr) {
    const n = arr.length, out = new Array(n).fill(null);
    for (let i = 1; i < n; i++) { const c = arr[i], p = arr[i - 1]; if (isNum(c) && isNum(p) && p !== 0) { const v = c / p - 1; out[i] = isFinite(v) ? v : null; } }
    return out;
  }
  // trailing-h return on a level/return array: arr[i]/arr[i-h]-1 (null where missing).
  function shiftRet(arr, h) {
    const n = arr.length, out = new Array(n).fill(null);
    for (let i = h; i < n; i++) { const c = arr[i], p = arr[i - h]; if (isNum(c) && isNum(p) && p !== 0) { const v = c / p - 1; out[i] = isFinite(v) ? v : null; } }
    return out;
  }
  function annualizeArr(arr, ppy, h) {
    return arr.map((v) => { if (!isNum(v)) return null; const base = 1 + v; if (base <= 0) return null; const r = Math.pow(base, ppy / h) - 1; return isFinite(r) ? r : null; });
  }

  // ------------------------------------------------------------- rolling (min_periods = round(0.8w), ddof=1)
  // Mirrors pandas rolling(w, min_periods=mp): a window yields a value once it holds >= mp
  // non-null observations, and the stat is taken over THOSE observations (divide by the live
  // count, not w). This is the parity twin of analytics.py's min_periods fix — without it a
  // single calendar-gap NaN (yfinance stock vs NSE index) voided whole windows and fragmented
  // rolling beta/vol/Sharpe/correlation.
  function _mp(w) { return Math.max(2, Math.round(w * 0.8)); }
  // single series -> {mean, std, var} (computed over the non-null count in the window).
  function rollStats(x, w) {
    const n = x.length, mp = _mp(w);
    const mean = new Array(n).fill(null), std = new Array(n).fill(null), vr = new Array(n).fill(null);
    let s = 0, ss = 0, cnt = 0;
    for (let i = 0; i < n; i++) {
      const xi = x[i]; if (isNum(xi)) { s += xi; ss += xi * xi; cnt++; }
      if (i >= w) { const xo = x[i - w]; if (isNum(xo)) { s -= xo; ss -= xo * xo; cnt--; } }
      if (cnt >= mp) { const m = s / cnt; mean[i] = m; if (cnt >= 2) { let v = (ss - cnt * m * m) / (cnt - 1); if (v < 0) v = 0; vr[i] = v; std[i] = Math.sqrt(v); } }
    }
    return { mean, std, var: vr };
  }
  // pair -> {cov, corr} (pairwise-complete; computed over rows where BOTH are non-null).
  function rollCovCorr(x, y, w) {
    const n = x.length, mp = _mp(w);
    const cov = new Array(n).fill(null), corr = new Array(n).fill(null);
    let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0, cnt = 0;
    for (let i = 0; i < n; i++) {
      const a = x[i], b = y[i], ok = isNum(a) && isNum(b);
      if (ok) { sx += a; sy += b; sxx += a * a; syy += b * b; sxy += a * b; cnt++; }
      if (i >= w) { const ao = x[i - w], bo = y[i - w], oko = isNum(ao) && isNum(bo); if (oko) { sx -= ao; sy -= bo; sxx -= ao * ao; syy -= bo * bo; sxy -= ao * bo; cnt--; } }
      if (cnt >= mp && cnt >= 2) {
        const mx = sx / cnt, my = sy / cnt;
        const cxy = (sxy - cnt * mx * my) / (cnt - 1);
        let vx = (sxx - cnt * mx * mx) / (cnt - 1), vy = (syy - cnt * my * my) / (cnt - 1);
        if (vx < 0) vx = 0; if (vy < 0) vy = 0;
        cov[i] = cxy;
        corr[i] = (vx > 0 && vy > 0) ? cxy / Math.sqrt(vx * vy) : null;
      }
    }
    return { cov, corr };
  }

  // ------------------------------------------------------------- CAGR & per-series stats
  function cagr(level, idx) {
    const v = [], t = [];
    for (let i = 0; i < level.length; i++) if (isNum(level[i])) { v.push(level[i]); t.push(idx[i]); }
    if (v.length < 2) return NaN;
    const days = (t[t.length - 1] - t[0]) / DAY;
    if (days <= 0 || v[0] <= 0) return NaN;
    return Math.pow(v[v.length - 1] / v[0], 365.0 / days) - 1.0;
  }

  function statsFor(level, ret, idx, ppy, rf) {
    const lv = [], lt = [];
    for (let i = 0; i < level.length; i++) if (isNum(level[i])) { lv.push(level[i]); lt.push(idx[i]); }
    const rv = ret.filter(isNum);
    const keys = ["total_return", "cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "best_1y", "worst_1y", "n_obs", "start", "end"];
    if (lv.length < 2 || rv.length < 2) { const o = {}; keys.forEach((k) => o[k] = null); return o; }
    const rfp = rf / ppy;
    const mean = avg(rv), sd = std1(rv);
    let dsum = 0; for (const x of rv) { const m = Math.min(x - rfp, 0); dsum += m * m; } const downside = Math.sqrt(dsum / rv.length);
    let cmax = -Infinity, maxdd = 0; for (const x of lv) { if (x > cmax) cmax = x; const dd = x / cmax - 1; if (dd < maxdd) maxdd = dd; }
    const cg = cagr(level, idx);
    const total = lv[lv.length - 1] / lv[0] - 1;
    const vol = sd * Math.sqrt(ppy);
    const sharpe = sd > 0 ? (mean - rfp) * ppy / (sd * Math.sqrt(ppy)) : NaN;
    const sortino = downside > 0 ? (mean - rfp) * ppy / (downside * Math.sqrt(ppy)) : NaN;
    const calmar = maxdd < 0 ? cg / Math.abs(maxdd) : NaN;
    const w1y = Math.trunc(ppy);
    let best = NaN, worst = NaN, any = false;
    for (let i = w1y; i < lv.length; i++) { const r = lv[i] / lv[i - w1y] - 1; if (isFinite(r)) { if (!any) { best = r; worst = r; any = true; } else { if (r > best) best = r; if (r < worst) worst = r; } } }
    return {
      total_return: cl(total), cagr: cl(cg), vol: cl(vol), sharpe: cl(sharpe), sortino: cl(sortino),
      maxdd: cl(maxdd), calmar: cl(calmar), best_1y: cl(best), worst_1y: cl(worst),
      n_obs: rv.length, start: fmt(lt[0]), end: fmt(lt[lt.length - 1]),
    };
  }

  function pairMetrics(rs, rb, lvls, lvlb, idx, ppy, rf) {
    const out = { alpha: null, beta: null, up_capture: null, down_capture: null, capture_ratio: null, tracking_error: null, info_ratio: null, corr: null };
    out.alpha = cl(cagr(lvls, idx) - cagr(lvlb, idx));
    const xs = [], ys = [];
    for (let i = 0; i < rs.length; i++) if (isNum(rs[i]) && isNum(rb[i])) { xs.push(rs[i]); ys.push(rb[i]); }
    if (xs.length < 2) return out;
    const varb = var1(ys);
    out.beta = varb > 0 ? cl(cov1(xs, ys) / varb) : null;
    let upb = 0, dnb = 0, ups = 0, dns = 0;
    for (let i = 0; i < ys.length; i++) { if (ys[i] > 0) { upb += ys[i]; ups += xs[i]; } else if (ys[i] < 0) { dnb += ys[i]; dns += xs[i]; } }
    out.up_capture = upb !== 0 ? cl(ups / upb) : null;
    out.down_capture = dnb !== 0 ? cl(dns / dnb) : null;
    if (out.up_capture !== null && out.down_capture !== null && out.down_capture !== 0) out.capture_ratio = cl(out.up_capture / out.down_capture);
    const diff = xs.map((x, i) => x - ys[i]);
    const te = std1(diff) * Math.sqrt(ppy);
    out.tracking_error = cl(te);
    out.info_ratio = te > 0 ? cl(avg(diff) * ppy / te) : null;
    out.corr = cl(corr1(xs, ys));
    return out;
  }

  // ------------------------------------------------------------- weekly resample (W-FRI last)
  function resampleWeekly(idx, cols, names) {
    const outIdx = [], outCols = {}; names.forEach((c) => outCols[c] = []);
    let curFri = null, cur = null;
    const flush = () => {
      if (cur === null) return;
      let any = false; for (const c of names) if (isNum(cur[c])) { any = true; break; }
      if (any) { outIdx.push(curFri); for (const c of names) outCols[c].push(isNum(cur[c]) ? cur[c] : null); }
    };
    for (let i = 0; i < idx.length; i++) {
      const fri = fridayLabel(idx[i]);
      if (fri !== curFri) { flush(); curFri = fri; cur = {}; for (const c of names) cur[c] = null; }
      for (const c of names) { const v = cols[c][i]; if (isNum(v)) cur[c] = v; }  // last non-null wins
    }
    flush();
    return { idx: outIdx, cols: outCols };
  }

  // ------------------------------------------------------------- calendar / monthly / distributions
  function hit(arr) {
    const v = arr.filter((x) => x !== null && x !== undefined);
    const n = v.length;
    if (!n) return { pos: null, neg: null, n: 0 };
    return { pos: v.filter((x) => x > 0).length / n, neg: v.filter((x) => x < 0).length / n, n };
  }

  function calendarYear(pxw, idx, cols, tickers, benchmarks) {
    const bench0 = benchmarks.length ? benchmarks[0] : null;
    const yset = new Set();
    const ylast = {}; cols.forEach((c) => ylast[c] = {});
    for (let i = 0; i < idx.length; i++) {
      const y = new Date(idx[i]).getUTCFullYear(); yset.add(y);
      for (const c of cols) { const v = pxw[c][i]; if (isNum(v)) ylast[c][y] = v; }
    }
    const yearsSorted = [...yset].sort((a, b) => a - b);
    const fd = new Date(idx[0]);
    const partial = (fd.getUTCMonth() + 1) !== 1 || fd.getUTCDate() > 5;

    // base timeline = [start row] + [Dec-31 of each year]; dedup by date keep-last so a
    // window starting EXACTLY on a year-end collapses into that year-end row — mirrors
    // pandas concat([start_row, ye]).duplicated(keep='last') (no spurious leading year).
    const entries = [{ ms: idx[0], year: null }];
    yearsSorted.forEach((y) => entries.push({ ms: Date.UTC(y, 11, 31), year: y }));
    entries.sort((a, b) => a.ms - b.ms);
    const tl = [];
    for (let i = 0; i < entries.length; i++) { if (i + 1 < entries.length && entries[i + 1].ms === entries[i].ms) continue; tl.push(entries[i]); }
    const valAt = (c, e) => e.year === null ? pxw[c][0] : ((e.year in ylast[c]) ? ylast[c][e.year] : null);
    const retYears = tl.slice(1).map((e) => e.year);

    const seriesAll = {};
    cols.forEach((c) => {
      const out = [];
      for (let k = 1; k < tl.length; k++) { const prev = valAt(c, tl[k - 1]), cur = valAt(c, tl[k]); out.push((isNum(prev) && isNum(cur) && prev !== 0) ? cl(cur / prev - 1) : null); }
      seriesAll[c] = out;
    });
    // dropna(how='all') across the return rows
    const keep = [];
    for (let k = 0; k < retYears.length; k++) { let any = false; for (const c of cols) if (seriesAll[c][k] !== null) { any = true; break; } if (any) keep.push(k); }
    const labels = keep.map((k) => String(retYears[k]));
    if (labels.length && partial) labels[0] = labels[0] + "*";
    const series = {}; cols.forEach((c) => series[c] = keep.map((k) => seriesAll[c][k]));
    const alpha = {};
    if (bench0 !== null) {
      for (const t of tickers) {
        alpha[`${t}|${bench0}`] = series[t].map((a, i) => { const b = series[bench0][i]; return (isNum(a) && isNum(b)) ? cl(a - b) : null; });
      }
    }
    const stats_return = {}; cols.forEach((c) => stats_return[c] = hit(series[c]));
    const stats_alpha = {}; Object.keys(alpha).forEach((k) => stats_alpha[k] = hit(alpha[k]));
    return { years: labels, series, alpha, primary_benchmark: bench0, stats_return, stats_alpha };
  }

  function monthlyHeatmap(pxw, idx, cols) {
    const out = {};
    for (const t of cols) {
      const mlast = {};
      for (let i = 0; i < idx.length; i++) { const d = new Date(idx[i]); const key = d.getUTCFullYear() * 12 + d.getUTCMonth(); const v = pxw[t][i]; if (isNum(v)) mlast[key] = v; }
      const keysPresent = Object.keys(mlast).map(Number);
      if (!keysPresent.length) continue;
      const startK = Math.min(...keysPresent), endK = Math.max(...keysPresent);
      const seq = []; for (let k = startK; k <= endK; k++) seq.push(k in mlast ? mlast[k] : null);
      const rows = {};
      for (let j = 1; j < seq.length; j++) {
        const prev = seq[j - 1], cur = seq[j];
        if (isNum(prev) && isNum(cur) && prev !== 0) { const kk = startK + j; const y = Math.floor(kk / 12), mo = kk % 12; if (!(y in rows)) rows[y] = new Array(12).fill(null); rows[y][mo] = cl(cur / prev - 1); }
      }
      const ys = Object.keys(rows).map(Number).sort((a, b) => a - b);
      if (!ys.length) continue;
      out[t] = { years: ys, z: ys.map((y) => rows[y]) };
    }
    return out;
  }

  function density(vals) {
    const v = vals.filter((x) => isFinite(x));
    if (v.length < MIN_DENS_PTS) return null;
    const sd0 = stdPop(v);
    if (sd0 < 1e-9) return null;
    const lo = aMin(v), hi = aMax(v);
    const pad = hi > lo ? (hi - lo) * 0.08 : Math.abs(lo) * 0.1 + 1e-6;
    const grid = linspace(lo - pad, hi + pad, 120);
    const n = v.length;
    const factor = Math.pow(n, -1 / 5);          // scipy gaussian_kde Scott's rule (1-D)
    const cov = var1(v) * factor * factor;        // sample var (ddof=1) * factor^2
    if (!(cov > 0)) return null;
    const norm = Math.sqrt(2 * Math.PI * cov) * n;
    const y = grid.map((g) => { let s = 0; for (const xi of v) { const d = g - xi; s += Math.exp(-0.5 * d * d / cov); } return s / norm; });
    return { x: grid.map(cl), y: y.map(cl), mean: cl(avg(v)), std: cl(sd0), n: v.length };
  }

  function distributions(pxw, idx, cols, tickers, benchmarks, freq, ppy) {
    const horizons = DIST_HORIZONS[freq] || DIST_HORIZONS.daily;
    const bench0 = benchmarks.length ? benchmarks[0] : null;
    const ret = {}, alp = {}, availR = [], availA = [];
    const n = idx.length;
    const units = {}; horizons.forEach(([lbl, h, ann]) => units[lbl] = ann ? "cagr" : "cumulative");
    for (const [label, h, ann] of horizons) {
      if (n - h < MIN_DENS_PTS) continue;
      const d = {};
      for (const c of cols) { let arr = shiftRet(pxw[c], h); if (ann) arr = annualizeArr(arr, ppy, h); const dens = density(arr.filter(isNum)); if (dens) d[c] = dens; }
      if (Object.keys(d).length) { ret[label] = d; availR.push(label); }
      if (bench0 !== null) {
        const da = {};
        for (const t of tickers) {
          let rs = shiftRet(pxw[t], h), rb = shiftRet(pxw[bench0], h);
          if (ann) { rs = annualizeArr(rs, ppy, h); rb = annualizeArr(rb, ppy, h); }
          const diff = []; for (let i = 0; i < rs.length; i++) if (isNum(rs[i]) && isNum(rb[i])) diff.push(rs[i] - rb[i]);
          const dens = density(diff); if (dens) da[t] = dens;
        }
        if (Object.keys(da).length) { alp[label] = da; availA.push(label); }
      }
    }
    return { return: ret, alpha: alp, horizons_return: availR, horizons_alpha: availA, primary_benchmark: bench0, units };
  }

  // ------------------------------------------------------------- entrypoint
  function analyze(DATA, body) {
    const freq = (body.freq in PPY) ? body.freq : "daily";
    const ppy = PPY[freq];
    const rf = +(body.rf_annual || 0) || 0;
    const rfp = rf / ppy;

    const tickers = (body.tickers || []).filter((t) => DATA.series[t]);
    const benchmarks = (body.benchmarks || []).filter((b) => DATA.series[b]);
    const all_cols = []; { const seen = new Set(); for (const c of [...tickers, ...benchmarks]) if (!seen.has(c)) { seen.add(c); all_cols.push(c); } }
    if (!all_cols.length) return { error: "No valid series selected." };

    const endMs = body.end ? parse(body.end) : null;
    const startMs = body.start ? parse(body.start) : null;

    // WINDOWED CONTINUITY GATE (calendar-based) — mirrors analytics.py analyze(): a series whose
    // IN-WINDOW observations contain a calendar gap > MAX_WINDOW_GAP_DAYS is not a continuous series
    // over this window (e.g. dormant-then-relisted stock); exclude it from the comparison (the data
    // itself is untouched) and report it in meta.excluded_noncontinuous. No-op for clean series.
    const excluded_noncont = [];
    {
      let wsGate = startMs;
      if (wsGate === null) {
        for (let i = 0; i < DATA.dates.length; i++) {
          if (all_cols.some((c) => isNum(DATA.series[c][i]))) { wsGate = parse(DATA.dates[i]); break; }
        }
      }
      const GAPMS = MAX_WINDOW_GAP_DAYS * 86400000;
      for (const c of all_cols) {
        let prev = null, bad = false;
        for (let i = 0; i < DATA.dates.length; i++) {
          const ms = parse(DATA.dates[i]);
          if (ms < wsGate) continue;
          if (endMs !== null && ms > endMs) break;
          if (!isNum(DATA.series[c][i])) continue;
          if (prev !== null && (ms - prev) > GAPMS) { bad = true; break; }
          prev = ms;
        }
        if (bad) excluded_noncont.push(c);
      }
      if (excluded_noncont.length) {
        const ex = new Set(excluded_noncont);
        for (let k = tickers.length - 1; k >= 0; k--) if (ex.has(tickers[k])) tickers.splice(k, 1);
        for (let k = benchmarks.length - 1; k >= 0; k--) if (ex.has(benchmarks[k])) benchmarks.splice(k, 1);
        for (let k = all_cols.length - 1; k >= 0; k--) if (ex.has(all_cols[k])) all_cols.splice(k, 1);
      }
      if (!all_cols.length) return { error: "The selected series are non-continuous over this window (a multi-month trading gap) — pick a shorter, continuous window.", excluded_noncontinuous: excluded_noncont };
    }

    // daily working slice: date<=end, drop rows where ALL selected cols null
    const dIdx = [], dCols = {}; all_cols.forEach((c) => dCols[c] = []);
    for (let i = 0; i < DATA.dates.length; i++) {
      const ms = parse(DATA.dates[i]);
      if (endMs !== null && ms > endMs) continue;
      let any = false; const vals = all_cols.map((c) => { const v = DATA.series[c][i]; const ok = isNum(v); if (ok) any = true; return ok ? v : null; });
      if (!any) continue;
      dIdx.push(ms); all_cols.forEach((c, k) => dCols[c].push(vals[k]));
    }
    if (!dIdx.length) return { error: "No data in the selected window for those indices." };

    // resample (extended frame pxe)
    let pIdx, pCols;
    if (freq === "weekly") { const r = resampleWeekly(dIdx, dCols, all_cols); pIdx = r.idx; pCols = r.cols; }
    else { pIdx = dIdx; pCols = dCols; }
    const rete = {}; all_cols.forEach((c) => rete[c] = pctchg(pCols[c]));

    // FAIR cross-series comparison: anchor at the LATEST date where EVERY selected
    // series has data (the common overlap) within the requested window — mirrors
    // analytics.py. Rebasing series with different inception dates to 100 on
    // different days is apples-to-oranges (fake outliers / mis-stated alpha).
    const wsReq = startMs !== null ? startMs : pIdx[0];
    if (!pIdx.some((ms) => ms >= wsReq)) return { error: "No data in the selected window." };
    let wStart = -1;
    for (let i = 0; i < pIdx.length; i++) {
      if (pIdx[i] < wsReq) continue;
      let allPresent = true;
      for (const c of all_cols) { if (!isNum(pCols[c][i])) { allPresent = false; break; } }
      if (allPresent) { wStart = i; break; }
    }
    if (wStart < 0) return { error: "The selected series have no overlapping dates in this window." };
    const wsEff = pIdx[wStart];
    const wIdx = pIdx.slice(wStart);
    const pxw = {}; all_cols.forEach((c) => pxw[c] = pCols[c].slice(wStart));
    const retw = {}; all_cols.forEach((c) => retw[c] = pctchg(pxw[c]));
    const dates = wIdx.map(fmt);
    const sl = (full) => full.slice(wStart).map(cl);
    const inceptOf = (c) => { const a = dCols[c]; for (let i = 0; i < a.length; i++) if (isNum(a[i])) return fmt(dIdx[i]); return null; };

    // levels / raw
    const levels = {}, raw = {};
    for (const c of all_cols) {
      const s = pxw[c]; let base = null; for (const v of s) if (isNum(v)) { base = v; break; }
      levels[c] = s.map((v) => (base && isNum(v)) ? cl(v / base * 100) : null);
      raw[c] = s.map((v) => cl(v));
    }

    const bench0 = benchmarks.length ? benchmarks[0] : null;

    // per-series stats
    const stats = [];
    for (const c of all_cols) {
      const row = { name: c, is_benchmark: benchmarks.includes(c) && !tickers.includes(c) };
      Object.assign(row, statsFor(pxw[c], retw[c], wIdx, ppy, rf));
      row.alpha_vs_primary = (bench0 !== null && c !== bench0) ? cl(cagr(pxw[c], wIdx) - cagr(pxw[bench0], wIdx)) : null;
      row.inception = inceptOf(c);
      stats.push(row);
    }

    // pairs
    const pairs = [];
    for (const t of tickers) for (const b of benchmarks) {
      const m = pairMetrics(retw[t], retw[b], pxw[t], pxw[b], wIdx, ppy, rf);
      m.name = t; m.benchmark = b; pairs.push(m);
    }

    // rolling (on extended frame, then sliced)
    const w = (WINDOW_PERIODS[freq] && WINDOW_PERIODS[freq][body.rolling_window]) || WINDOW_PERIODS[freq]["1Y"];
    const rstat = {}; all_cols.forEach((c) => rstat[c] = rollStats(rete[c], w));
    const rolling = { alpha: {}, beta: {}, vol: {}, sharpe: {}, drawdown: {}, corr: {}, relstrength: {} };
    // single-series rolling stats for ALL selected series (benchmark drawdown etc.)
    for (const t of all_cols) {
      rolling.vol[t] = sl(rstat[t].std.map((s) => isNum(s) ? s * Math.sqrt(ppy) : null));
      rolling.sharpe[t] = sl(rstat[t].std.map((s, i) => { const sd = isNum(s) ? s * Math.sqrt(ppy) : null; const m = rstat[t].mean[i]; return (sd && sd > 0 && isNum(m)) ? (m - rfp) * ppy / sd : null; }));
      // window-relative underwater
      let cmax = -Infinity; rolling.drawdown[t] = pxw[t].map((v) => { if (!isNum(v)) return null; if (v > cmax) cmax = v; return cl(v / cmax - 1); });
    }
    const annualize_alpha = w >= ppy;
    const alphaType = body.alpha_type === "jensen" ? "jensen" : "excess";
    for (const t of tickers) for (const b of benchmarks) {
      const key = `${t}|${b}`;
      const cc = rollCovCorr(rete[t], rete[b], w);
      const vb = rstat[b].var;
      if (alphaType === "jensen") {
        const beta = cc.cov.map((cov, i) => { const vv = vb[i]; return (isNum(cov) && isNum(vv) && vv !== 0) ? cov / vv : null; });
        const mt = rstat[t].mean, mb = rstat[b].mean;
        const aPer = beta.map((be, i) => (isNum(be) && isNum(mt[i]) && isNum(mb[i])) ? ((mt[i] - rfp) - be * (mb[i] - rfp)) : null);
        rolling.alpha[key] = sl(aPer.map((a) => isNum(a) ? a * ppy : null));
        rolling.beta[key] = sl(beta);
      } else {
        let trs = shiftRet(pCols[t], w), trb = shiftRet(pCols[b], w);
        if (annualize_alpha) { trs = annualizeArr(trs, ppy, w); trb = annualizeArr(trb, ppy, w); }
        rolling.alpha[key] = sl(trs.map((a, i) => (isNum(a) && isNum(trb[i])) ? a - trb[i] : null));
        rolling.beta[key] = sl(cc.cov.map((cov, i) => { const vv = vb[i]; return (isNum(cov) && isNum(vv) && vv !== 0) ? cov / vv : null; }));
      }
      rolling.corr[key] = sl(cc.corr);
      // relative strength (window-rebased to 100)
      const ratio = pxw[t].map((v, i) => (isNum(v) && isNum(pxw[b][i]) && pxw[b][i] !== 0) ? v / pxw[b][i] : null);
      let rbase = null; for (const v of ratio) if (isNum(v)) { rbase = v; break; }
      rolling.relstrength[key] = ratio.map((v) => (rbase && isNum(v)) ? cl(v / rbase * 100) : null);
    }
    rolling.alpha_annualized = !!(annualize_alpha || alphaType === "jensen");

    // correlation matrix (all selected, window returns)
    const cmZ = all_cols.map((r) => all_cols.map((c) => {
      const xs = [], ys = []; const a = retw[r], bb = retw[c];
      for (let i = 0; i < a.length; i++) if (isNum(a[i]) && isNum(bb[i])) { xs.push(a[i]); ys.push(bb[i]); }
      if (xs.length < 2) return null;
      if (r === c) return var1(xs) > 0 ? 1.0 : null;     // pandas forces the diagonal to exactly 1.0
      return cl(corr1(xs, ys));
    }));

    const cyCols = tickers.length ? all_cols : all_cols;
    const calendar_year = tickers.length
      ? calendarYear(pxw, wIdx, all_cols, tickers, benchmarks)
      : calendarYear(pxw, wIdx, all_cols, all_cols, []);

    return {
      meta: {
        freq, ppy, rolling_window: body.rolling_window || "1Y", rolling_periods: w,
        alpha_type: alphaType, rf_annual: rf, tickers, benchmarks,
        start: dates[0] || null, end: dates[dates.length - 1] || null, n_obs: dates.length,
        requested_start: fmt(wsReq), common_start: fmt(wsEff), truncated: wsEff > wsReq,
        excluded_noncontinuous: excluded_noncont,
      },
      dates, levels, raw_levels: raw, stats, pairs, rolling,
      calendar_year,
      monthly: monthlyHeatmap(pxw, wIdx, all_cols),
      distribution: distributions(pxw, wIdx, all_cols, tickers, benchmarks, freq, ppy),
      corr_matrix: { labels: all_cols, z: cmZ },
    };
  }

  // ------------------------------------------------------------- valuation (ratio/yield)
  // Mirror of analytics.valuation_analyze. A valuation ratio/yield is NOT compounding
  // wealth, so CAGR/Sharpe/drawdown don't apply; read level / percentile-vs-own-history /
  // z / bands / cross-section / spread. NO rebasing. percentile = (#vals<=current)/n.
  function median(a) {
    const v = a.filter(isNum).slice().sort((p, q) => p - q);
    const n = v.length; if (!n) return NaN;
    return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
  }
  function percentileOf(vals, x) {
    const v = vals.filter(isNum);
    if (!v.length || !isNum(x)) return NaN;
    let c = 0; for (const z of v) if (z <= x) c++;
    return c / v.length * 100;
  }
  function cheapRich(pct, kind) {
    if (!isNum(pct)) return "—";
    const hi = kind === "yield" ? "cheap" : "rich", lo = kind === "yield" ? "rich" : "cheap";
    if (pct >= 80) return hi; if (pct <= 20) return lo; return "mid";
  }

  function valuationAnalyze(DATA, body) {
    const freq = (body.freq in PPY) ? body.freq : "daily";
    const measure = body.measure || "PE", kind = body.kind || "ratio";
    const tickers = (body.tickers || []).filter((t) => DATA.series[t]);
    const benchmarks = (body.benchmarks || []).filter((b) => DATA.series[b]);
    const all_cols = []; { const seen = new Set(); for (const c of [...tickers, ...benchmarks]) if (!seen.has(c)) { seen.add(c); all_cols.push(c); } }
    if (!all_cols.length) return { error: `No selected series has ${measure} data.` };

    const endMs = body.end ? parse(body.end) : null;
    const startMs = body.start ? parse(body.start) : null;

    // all rows <= end (keep all-null; dropna(how='all') AFTER resample) — mirrors Python
    const aIdx = [], aCols = {}; all_cols.forEach((c) => aCols[c] = []);
    for (let i = 0; i < DATA.dates.length; i++) {
      const ms = parse(DATA.dates[i]);
      if (endMs !== null && ms > endMs) continue;
      aIdx.push(ms);
      all_cols.forEach((c) => { const v = DATA.series[c][i]; aCols[c].push(isNum(v) ? v : null); });
    }
    let rIdx, rCols;
    if (freq === "weekly") { const r = resampleWeekly(aIdx, aCols, all_cols); rIdx = r.idx; rCols = r.cols; }
    else { rIdx = aIdx; rCols = aCols; }
    const keepRows = [];
    for (let i = 0; i < rIdx.length; i++) { let any = false; for (const c of all_cols) if (isNum(rCols[c][i])) { any = true; break; } if (any) keepRows.push(i); }
    const wsReq = startMs !== null ? startMs : (keepRows.length ? rIdx[keepRows[0]] : null);
    if (wsReq === null) return { error: `No ${measure} data.` };
    const widx = keepRows.filter((i) => rIdx[i] >= wsReq);
    if (!widx.length) return { error: `No ${measure} data in the selected window.` };
    const dates = widx.map((i) => fmt(rIdx[i]));
    const series = {}; all_cols.forEach((c) => series[c] = widx.map((i) => cl(rCols[c][i])));
    const bench0 = benchmarks.length ? benchmarks[0] : null;

    const stats = [], bands = {}, xsec = [], dist = {};
    for (const c of all_cols) {
      const arr = [], adates = [];
      widx.forEach((i) => { const v = rCols[c][i]; if (isNum(v)) { arr.push(v); adates.push(rIdx[i]); } });
      if (arr.length < 1) { stats.push({ name: c, current: null, mean: null, median: null, std: null, min: null, max: null, zscore: null, percentile: null, cheap_rich: "—", n_obs: 0, start: null, end: null }); continue; }
      const cur = arr[arr.length - 1], mean = avg(arr), sd = arr.length > 1 ? std1(arr) : NaN;
      // MIN-HISTORY GUARD (parity with vistas/analytics.py:533): below 8 obs a percentile is fabricated
      // (e.g. 100 off 2-3 points), so suppress it and the cheap/rich label (cheapRich(NaN) -> "—").
      let pctile = percentileOf(arr, cur);
      if (arr.length < 8) pctile = NaN;
      const z = (sd && sd > 0) ? (cur - mean) / sd : NaN, cr = cheapRich(pctile, kind);
      stats.push({ name: c, current: cl(cur), mean: cl(mean), median: cl(median(arr)), std: cl(sd), min: cl(aMin(arr)), max: cl(aMax(arr)), zscore: cl(z), percentile: cl(pctile), cheap_rich: cr, n_obs: arr.length, start: fmt(adates[0]), end: fmt(adates[adates.length - 1]) });
      bands[c] = { mean: cl(mean), sd1_lo: cl(mean - sd), sd1_hi: cl(mean + sd), sd2_lo: cl(mean - 2 * sd), sd2_hi: cl(mean + 2 * sd) };
      xsec.push({ name: c, value: cl(cur), percentile: cl(pctile), zscore: cl(z), cheap_rich: cr, date: fmt(adates[adates.length - 1]), is_benchmark: benchmarks.includes(c) && !tickers.includes(c) });
      const d = density(arr); if (d) dist[c] = d;
    }
    xsec.sort((p, q) => {
      const pn = p.value === null, qn = q.value === null;
      if (pn !== qn) return pn ? 1 : -1;
      if (!pn && p.value !== q.value) return q.value - p.value;
      return p.name < q.name ? -1 : (p.name > q.name ? 1 : 0);
    });

    let spread = null;
    if (bench0 !== null) {
      const sp_series = {}, sp_stats = {};
      for (const t of tickers) {
        const diff = widx.map((i) => { const a = rCols[t][i], b = rCols[bench0][i]; return (isNum(a) && isNum(b)) ? a - b : null; });
        sp_series[t] = diff.map(cl);
        const dd = diff.filter(isNum);
        if (dd.length) { const last = dd[dd.length - 1]; sp_stats[t] = { current: cl(last), mean: cl(avg(dd)), percentile: cl(percentileOf(dd, last)) }; }
        else sp_stats[t] = { current: null, mean: null, percentile: null };
      }
      spread = { primary: bench0, series: sp_series, stats: sp_stats };
    }

    return {
      meta: { measure, kind, freq, tickers, benchmarks, primary_benchmark: bench0, start: dates[0] || null, end: dates[dates.length - 1] || null, n_obs: dates.length, requested_start: fmt(wsReq) },
      dates, series, stats, bands, cross_section: { rows: xsec }, spread, distribution: dist,
    };
  }

  return { analyze, valuationAnalyze, _internals: { pctchg, rollStats, rollCovCorr, resampleWeekly, cagr, density, median, percentileOf } };
})();

// Expose in the browser (window) AND under Node (module.exports), for the parity harness.
if (typeof window !== "undefined") window.VistasAnalytics = VistasAnalytics;
if (typeof module !== "undefined" && module.exports) module.exports = VistasAnalytics;
