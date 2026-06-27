"""vistas/build_cache.py — content-addressed memoization for the terminal BUILD (#99, tier-2).

WHAT & WHY (first principles)
-----------------------------
The daily build re-bakes per-stock / per-fund analytics that mostly DON'T move day to day. A stock's
baked quant block is a PURE FUNCTION of its inputs (its price series, fundamentals, flows, ARM, …): if
those inputs are byte-identical to the last build, the baked output is byte-identical too. So
recomputing it is wasted work. This module lets a build STAGE skip an item whose INPUT FINGERPRINT is
unchanged and reuse the cached output instead.

This is MEMOIZATION, not a publish shortcut. The reused output is provably identical to what a full
recompute would produce (same inputs ⇒ same output), and the caller still ASSEMBLES the deck/shell
fresh from every per-item output every time. KV's standing rule — *every publish is a full rebuild,
never a bake-only shortcut* — is preserved: we never skip the assembly and never publish stale data;
we only avoid recomputing items whose inputs did not change.

THE FORCED-FULL BACKSTOP (the correctness guarantee). The gate is bypassed — every item recomputed —
whenever ANY of these hold, so a fingerprinting bug can never silently persist a stale output:
  • force_full=True  (CLI --force-full / env VISTAS_BUILD_FORCE_FULL=1),
  • the gate is not explicitly ENABLED (default OFF — the daily publish stays a full rebuild until KV
    opts in via VISTAS_BUILD_PARTITIONED=1 / enabled=True; see the wiring note below),
  • the cache file is missing or corrupt,
  • the VERSION TOKEN changed — the hash of the building code, so any formula change invalidates ALL
    items at once (no per-item staleness after an analytics edit),
  • the item was never fingerprinted before,
  • a periodic FULL refresh is due (default: every 7 days) — a belt-and-suspenders sweep that
    re-bakes everything from scratch regardless of fingerprints.

So the worst case of a missed-input bug is bounded to one code-change OR one week, and it is OFF for
publishes by default. Speed now, with a hard correctness floor.

★ INTEGRATION SAFETY ANALYSIS (#99 — read before wiring into the live builder)
------------------------------------------------------------------------------
Memoization is only correct for a stage whose per-item output is a PURE function of that item's OWN
inputs. Two classes of build stage exist in this terminal, and only one is safe to gate PER ITEM:

  • PURE PER-ITEM (safe to memoize per item): the per-stock PRICE files and per-INDEX files
    (deck.py §1, §2b) — each <SYM>.json is just that series, no dependency on other symbols. The
    per-company FUNDAMENTALS dump (deck.py §2) is also per-item. These are cheap, so the win is small.

  • CROSS-SECTIONAL (NOT safe to memoize per item): the per-stock QUANT bake (stock_intel.build_all
    → data/quant/<SYM>.json) bakes cross-sectional features — percentile RANKS vs the whole universe,
    sector relative-strength, smart-money quadrant. One stock's output depends on EVERY stock's data,
    so skipping an "unchanged" stock while its peers move would persist a STALE rank. Per-item gating
    here is unsound; do NOT do it.

  ⇒ The genuinely safe + useful partition is STAGE-LEVEL BY SOURCE CADENCE: skip re-baking a whole
    stage when NONE of its source feeds refreshed since the last build (fundamentals + benchmarks are
    weekly feeds — on a typical day they didn't move, so their entire bake can be reused byte-identical),
    while the daily price/quant stages always re-bake. This mirrors pipeline.py's existing cadence gate.
    A stage fingerprint = the tokens of its INPUT feed files; unchanged ⇒ reuse the whole output group.
    The forced-full backstop (below) bounds any mistake to one code-change / one week.

  STATUS: this engine is built + self-tested; the production wiring is correctness-sensitive (it must
  persist output dirs across builds and must classify each stage pure-vs-cross-sectional correctly), so
  it is left as a REVIEWED flip — enabled per-run via VISTAS_BUILD_PARTITIONED=1, default OFF so every
  publish stays a full rebuild until KV opts in and benchmarks it. No live-builder path is changed yet.

WIRING NOTE: a stage uses it like —
    bc = BuildCache("per_stock_quant", version_token=code_version(quant_module), enabled=PARTITIONED)
    for sym in universe:
        fp = bc.fingerprint([px_path(sym), fund_path(sym), flows_fp(sym), CONFIG_TOKEN])
        if bc.fresh(sym, fp, outputs=[out_path(sym)]):
            continue                                  # reuse the byte-identical cached output
        recompute(sym); bc.record(sym, fp, outputs=[out_path(sym)])
    bc.save(); log(bc.stats())
"""
import os
import json
import time
import hashlib
import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_STATE_DIR = os.path.join(_ROOT, "data", "_refresh")
FULL_EVERY_DAYS = 7                       # periodic forced-full sweep (correctness backstop)


def _sha(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x1f")                 # unit separator so ("a","b") != ("ab",)
    return h.hexdigest()


def file_token(path):
    """A cheap, robust per-file fingerprint = size + mtime_ns. (Content-hash optional via hash_file.)
    A missing file tokenises to 'absent' so an input appearing/disappearing flips the fingerprint."""
    try:
        st = os.stat(path)
        return f"{st.st_size}:{st.st_mtime_ns}"
    except Exception:
        return "absent"


def hash_file(path, _buf=1 << 20):
    """Full content sha256 of a file (use where mtime is unreliable). 'absent' if missing."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                b = f.read(_buf)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except Exception:
        return "absent"


def code_version(*modules_or_paths):
    """A VERSION TOKEN over the building code — the content hash of each given module file (or path).
    Pass the analytics/bake modules so ANY formula edit changes the token and forces a full rebuild."""
    toks = []
    for m in modules_or_paths:
        p = getattr(m, "__file__", None) or m
        toks.append(hash_file(p))
    return _sha(*toks)


class BuildCache:
    """One stage's fingerprint cache. State persists in data/_refresh/_build_fp_<stage>.json.

    enabled=False (the default) ⇒ EVERY `fresh()` returns False (full rebuild) — the safe production
    default; the daily publish stays a complete rebuild until the partitioned path is explicitly turned
    on. Turn on per-run with enabled=True (e.g. VISTAS_BUILD_PARTITIONED=1) for a fast dev/iteration build."""

    def __init__(self, stage, version_token="", enabled=False, force_full=False,
                 full_every_days=FULL_EVERY_DAYS, today=None):
        self.stage = stage
        self.version = str(version_token or "")
        self.enabled = bool(enabled) and os.environ.get("VISTAS_BUILD_PARTITIONED", "") != "0"
        self.path = os.path.join(_STATE_DIR, f"_build_fp_{_safe(stage)}.json")
        env_force = os.environ.get("VISTAS_BUILD_FORCE_FULL", "") == "1"
        self.force = bool(force_full) or env_force
        self.hits = self.misses = 0
        self.reason = ""
        self._today = today or datetime.date.today().isoformat()

        self.state = self._load()
        meta = self.state.get("_meta", {}) if isinstance(self.state, dict) else {}
        # ── forced-full triggers (any ⇒ rebuild everything this run) ──
        if not self.enabled:
            self.force = True; self.reason = self.reason or "gate-disabled (default full rebuild)"
        if not self.state:
            self.force = True; self.reason = self.reason or "no/corrupt cache"
        if self.version and meta.get("version") != self.version:
            self.force = True; self.reason = self.reason or "code-version-changed"
        lf = meta.get("last_full")
        if lf:
            try:
                if (datetime.date.fromisoformat(self._today)
                        - datetime.date.fromisoformat(lf)).days >= full_every_days:
                    self.force = True; self.reason = self.reason or f"periodic-full (≥{full_every_days}d)"
            except Exception:
                self.force = True; self.reason = self.reason or "bad last_full"
        else:
            self.force = True; self.reason = self.reason or "no last_full"
        if self.force:
            # start a clean items map; remember WHEN we last did a full sweep
            self._items = {}
            self._last_full = self._today
        else:
            self._items = dict(self.state.get("items", {}))
            self._last_full = lf

    # ── fingerprint helpers ──
    def fingerprint(self, inputs):
        """Hash a list of inputs into one token. Each input is either a file PATH (tokenised by
        size+mtime) or a plain str/number (a config value, hashed verbatim)."""
        toks = []
        for x in inputs:
            if isinstance(x, str) and (os.path.sep in x or x.endswith((".json", ".csv", ".parquet"))) and os.path.exists(x):
                toks.append("F:" + file_token(x))
            else:
                toks.append("V:" + str(x))
        return _sha(self.version, *toks)

    # ── the gate ──
    def fresh(self, key, input_fp, outputs=()):
        """True ⇒ the item is unchanged AND its outputs still exist ⇒ the caller may SKIP recompute.
        False ⇒ recompute (and call record() after). Always False when forced-full / disabled."""
        if self.force:
            return False
        rec = self._items.get(str(key))
        if not rec or rec.get("fp") != input_fp:
            return False
        for o in outputs:
            if not os.path.exists(o):
                return False
        self.hits += 1
        return True

    def record(self, key, input_fp, outputs=()):
        """Record an item's fingerprint after recomputing it."""
        self._items[str(key)] = {"fp": input_fp, "ts": self._today, "outputs": list(outputs)}
        self.misses += 1

    def save(self):
        try:
            os.makedirs(_STATE_DIR, exist_ok=True)
            doc = {"_meta": {"stage": self.stage, "version": self.version,
                             "last_full": self._last_full, "saved": _now_iso(),
                             "enabled": self.enabled, "forced_full_this_run": self.force,
                             "reason": self.reason}, "items": self._items}
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(doc, f)
        except Exception:
            pass

    def stats(self):
        total = self.hits + self.misses
        return {"stage": self.stage, "enabled": self.enabled, "forced_full": self.force,
                "reason": self.reason, "hits": self.hits, "misses": self.misses,
                "reuse_pct": round(100.0 * self.hits / total, 1) if total else 0.0,
                "n_items": len(self._items)}

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}


def _safe(s):
    return "".join(c if c.isalnum() else "_" for c in str(s)).strip("_")


def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ───────────────────────────────────────────────────────── self-test
def _selftest():
    import tempfile
    d = tempfile.mkdtemp()
    a = os.path.join(d, "a.csv")
    out = os.path.join(d, "a.out.json")
    open(a, "w").write("x")
    open(out, "w").write("baked")
    global _STATE_DIR
    _STATE_DIR = d

    # run 1: enabled, but first-ever → forced full (no last_full) → everything a miss
    bc = BuildCache("t", version_token="v1", enabled=True, today="2026-06-28")
    fp = bc.fingerprint([a, "cfg=1"])
    assert bc.fresh("a", fp, [out]) is False, "first run must be a miss (forced full)"
    bc.record("a", fp, [out]); bc.save()
    assert bc.force and bc.reason, f"first run must force full (empty cache): {bc.reason!r}"

    # run 2: same inputs, same version, within the week → HIT (reuse)
    bc2 = BuildCache("t", version_token="v1", enabled=True, today="2026-06-29")
    fp2 = bc2.fingerprint([a, "cfg=1"])
    assert fp2 == fp, "fingerprint must be stable for identical inputs"
    assert not bc2.force, f"run2 should NOT force full: {bc2.reason}"
    assert bc2.fresh("a", fp2, [out]) is True, "unchanged item must be FRESH (reused)"

    # run 3: input file changed → MISS
    time.sleep(0.01); open(a, "w").write("y")
    os.utime(a, None)
    bc3 = BuildCache("t", version_token="v1", enabled=True, today="2026-06-29")
    fp3 = bc3.fingerprint([a, "cfg=1"])
    assert fp3 != fp, "changed input must change the fingerprint"
    assert bc3.fresh("a", fp3, [out]) is False, "changed input must MISS"

    # run 4: code VERSION changed → forced full (all miss) even if inputs identical
    open(a, "w").write("x"); os.utime(a, None)
    bc4 = BuildCache("t", version_token="v2", enabled=True, today="2026-06-29")
    assert bc4.force and "code-version-changed" in bc4.reason, "version bump must force full"
    assert bc4.fresh("a", bc4.fingerprint([a, "cfg=1"]), [out]) is False
    bc4.record("a", bc4.fingerprint([a, "cfg=1"]), [out]); bc4.save()

    # run 5: periodic full backstop — >7d since last_full → forced full
    bc5 = BuildCache("t", version_token="v2", enabled=True, today="2026-07-20")
    assert bc5.force and "periodic-full" in bc5.reason, f"periodic backstop must trigger: {bc5.reason}"

    # run 6: gate DISABLED (production default) → always miss (full rebuild)
    bc6 = BuildCache("t", version_token="v2", enabled=False, today="2026-06-29")
    assert bc6.force and "gate-disabled" in bc6.reason
    assert bc6.fresh("a", bc6.fingerprint([a, "cfg=1"]), [out]) is False
    print("build_cache self-test: PASS")


if __name__ == "__main__":
    _selftest()
