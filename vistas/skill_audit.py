"""
skill_audit.py — the before/after migration audit generator (spec D.3.1, L699-732)
-> output/SKILL_MIGRATION_AUDIT.md.

WHAT THIS PRODUCES
------------------
The human-review deliverable for the "audit before publish" gate (D.3.2 step 4): a side-by-side
SEBI-category table contrasting the LIVE legacy verdict mix against the NEW posterior-tag mix, plus
the explicit list of funds that MOVED — especially old-"skilled" -> new-"unproven/likely_unskilled"
(the ones the rails caught), each annotated with WHY (fee, factor, or FDR). KV reads this (and the
SKILL_POSTERIOR_VALIDATION.md from skill_validate) BEFORE anything goes live.

THE BEFORE (LIVE today, verified from data/funds_attribution/_manifest.json, n=740 equity schemes):
    skilled 152 (21%) · ahead-but-not-yet-significant 369 (50%) · insufficient 117 (16%) ·
    lagging 97 (13%) · good-selector-weak-sizer 4 (1%) · index-like 1 (0%).
    Among the 249 RESOLVED (skilled+lagging): 61% "skilled" — too cheap (GROSS, un-factor-deflated,
    no fee subtraction, no multiple-testing correction).

THE AFTER (the 5-state posterior tag): read from each fund's `skill` block IF a posterior=True build
exists; otherwise this audit emits a clearly-labelled ON-THE-FLY PREVIEW tag computed from the same
walk-forward IR-axis posterior as skill_validate (full history as the "as-of" estimate). The preview is
flagged in the report and is NOT a claim — the real after-mix comes from the rails rebuild.

THE THREE RAILS each shrink "skilled" (the WHY for each moved fund): (1) fee subtraction (~0.9-1.8%/yr)
wipes the marginal gross-"skilled"; (2) factor deflation (only ~55% of gross excess typically survives
MKT/SMB/HML/WML/QMJ); (3) book-level FDR q=0.10 across 740 removes the residual luck tail.

* This audit is READ-ONLY over the funds_attribution JSONs (legacy `verdict` keys + the new `skill`
block once a posterior=True build exists). It WRITES one report .md to output/ — no publish, no live
touch, no build lock, no consumer-default flip.

--------------------------------------------------------------------------------------------------
SHARED DATA CONTRACT (locked in SKILL_ENGINE_BUILD.md)
--------------------------------------------------------------------------------------------------
audit() return dict (-> output/SKILL_MIGRATION_AUDIT.md):
    {
      "before": {"<verdict>": {"count":int,"share":float}, ...},
      "after":  {"<tag>": {"count":int,"share":float}, ...},
      "by_category": {"<sebi_category>": {
          "before": {"<verdict>":int,...}, "after": {"<tag>":int,...}, "n":int }, ...},
      "moved": [ {"navindia_code":str,"name":str,"category":str,"from":"skilled",
                  "to":"unproven","why":"factor"|"fee"|"fdr"|"factor+fee", "detail":str}, ... ],
      "headline": str,   # the "1-in-5 -> ~1-in-15" migration sentence
    }
"""
from __future__ import annotations

import json
import math
import os

import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _p(*parts):
    return os.path.join(_ROOT, *parts)


OUT_MD = "output/SKILL_MIGRATION_AUDIT.md"
ATTRIB_DIR = _p("data", "funds_attribution")   # source of both legacy verdicts and the new skill blocks
MANIFEST = _p("data", "funds_attribution", "_manifest.json")

# Legacy verdict -> the 5-state tag family it MAPS to under the new scheme, for the before/after contrast.
# (Not a 1:1 — a legacy verdict spreads across the new tags by the posterior; this is the COARSE mapping
#  used only to label which legacy bucket a fund came from in the by-category table.)
_TAG_ORDER = ["skilled", "likely_skilled", "unproven", "likely_unskilled", "lagging",
              "insufficient_history", "index-like"]


# =============================================================================================== #
#  BEFORE — the verified live legacy mix
# =============================================================================================== #
def load_before(attrib_dir: str = ATTRIB_DIR) -> dict:
    """Read the LIVE legacy verdict mix from _manifest.json (per fund: verdict, sebi_category). Returns
    {"counts":{verdict:n}, "by_cat":{cat:{verdict:n}}, "recs":{code:{name,category,verdict}}, "n":int}.
    Read-only."""
    mpath = os.path.join(os.path.dirname(attrib_dir), os.path.basename(attrib_dir), "_manifest.json") \
        if os.path.isdir(attrib_dir) else MANIFEST
    if not os.path.isfile(mpath):
        mpath = MANIFEST
    m = json.load(open(mpath, encoding="utf-8"))
    counts: dict = {}
    by_cat: dict = {}
    recs: dict = {}
    for code, info in m.items():
        v = info.get("verdict", "undefined")
        cat = info.get("category", "_unknown")
        counts[v] = counts.get(v, 0) + 1
        by_cat.setdefault(cat, {})[v] = by_cat.setdefault(cat, {}).get(v, 0) + 1
        recs[str(code)] = {"name": info.get("name", ""), "category": cat, "verdict": v,
                           "t_stat": info.get("t_stat"), "n_months": info.get("n_months")}
    return {"counts": counts, "by_cat": by_cat, "recs": recs, "n": len(m)}


# =============================================================================================== #
#  AFTER — the new posterior-tag mix (from the live `skill` block, or an on-the-fly preview)
# =============================================================================================== #
def _tag_from_posterior(p_skilled: float, lo90: float, hi90: float, passes_fdr: bool,
                        n_months: int, tracking_error: float | None) -> str:
    """The 5-state tag ladder (spec D.1.3) — mirrors skill_posterior.skill_tag. Most-specific first."""
    if tracking_error is not None and np.isfinite(tracking_error) and tracking_error < 0.02:
        return "index-like"
    if n_months is not None and n_months < 12:
        return "insufficient_history"
    if not np.isfinite(p_skilled):
        return "unproven"
    if p_skilled >= 0.90 and lo90 > 0 and passes_fdr:
        return "skilled"
    if p_skilled >= 0.70:
        return "likely_skilled"
    if p_skilled >= 0.40:
        return "unproven"
    if p_skilled > 0.10:
        return "likely_unskilled"
    if p_skilled <= 0.10 and hi90 < 0:
        return "lagging"
    return "likely_unskilled"


def load_after(attrib_dir: str = ATTRIB_DIR, allow_preview: bool = True) -> dict:
    """Read the NEW posterior-tag mix. If a posterior=True build exists (per-fund `skill` block in the
    JSONs), read it directly. Otherwise — when allow_preview — compute an ON-THE-FLY PREVIEW tag from the
    same walk-forward IR-axis posterior the validator uses (full history as the as-of estimate), and stamp
    `source="on-the-fly-preview"` so the report is honest that the real rails (fee/factor/FDR) have NOT
    yet run. Returns {"counts":{tag:n}, "by_cat":{cat:{tag:n}}, "recs":{code:{tag,p_skilled,rails...}},
    "source":str, "n":int}. Read-only."""
    # 1) try the live skill block
    recs: dict = {}
    have_live = False
    if os.path.isdir(attrib_dir):
        for fn in os.listdir(attrib_dir):
            if not fn.endswith(".json") or fn.startswith("_"):
                continue
            try:
                d = json.load(open(os.path.join(attrib_dir, fn), encoding="utf-8"))
            except Exception:
                continue
            sk = d.get("skill")
            if not sk:
                continue
            have_live = True
            post = sk.get("posterior", {})
            rails = sk.get("rails", {})
            recs[str(d.get("navindia_code", fn[:-5]))] = {
                "tag": sk.get("tag", "unproven"),
                "p_skilled": post.get("p_skilled"),
                "post_best": post.get("best"),
                "factor_alpha_share": rails.get("factor_alpha_share"),
                "fee_adjusted": rails.get("fee_adjusted"),
                "passes_fdr": rails.get("passes_fdr"),
            }
    source = "live-skill-block"
    if not have_live:
        if not allow_preview:
            return {"counts": {}, "by_cat": {}, "recs": {}, "source": "ABSENT", "n": 0}
        recs = _preview_tags()
        source = "on-the-fly-preview"

    counts: dict = {}
    by_cat: dict = {}
    before = load_before(attrib_dir)["recs"]
    for code, r in recs.items():
        tag = r["tag"]
        cat = before.get(code, {}).get("category", "_unknown")
        r["category"] = cat
        counts[tag] = counts.get(tag, 0) + 1
        by_cat.setdefault(cat, {})[tag] = by_cat.setdefault(cat, {}).get(tag, 0) + 1
    return {"counts": counts, "by_cat": by_cat, "recs": recs, "source": source, "n": len(recs)}


def _preview_tags() -> dict:
    """Compute a PREVIEW posterior tag per fund from the full-history IR-axis posterior (same machinery as
    skill_validate — the walk-forward predictor evaluated at the LAST month). No rails yet, so passes_fdr
    is approximated by a book-level Benjamini-Hochberg over the per-fund one-sided p-values, and
    factor_alpha_share is left None (factor deflation is a separate rail module). Stamped as preview."""
    from vistas import skill_validate as sv
    attrib = sv._load_attrib_ts()
    # one decision date = the latest month present across funds (full history as-of)
    last_idx = max((sv._ym_to_idx(x["ym"]) for info in attrib.values() for x in info["ts"]
                    if x.get("A") is not None), default=None)
    if last_idx is None:
        return {}
    # per-fund IR + se at the as-of date; collect by category for the (full-sample, as-of) prior
    ir_by_cat: dict = {}
    pf: dict = {}
    for code, info in attrib.items():
        A = np.array([float(x["A"]) for x in info["ts"]
                      if x.get("A") is not None and sv._ym_to_idx(x["ym"]) <= last_idx], dtype=float)
        if len(A) < sv.MIN_MONTHS_AT_T:
            pf[code] = {"short": True, "n": len(A)}
            continue
        ir, se, T = sv._ir_at_t(A)
        if not np.isfinite(ir):
            continue
        cat = info["category"] or "_unknown"
        ir_by_cat.setdefault(cat, []).append((ir, se))
        pf[code] = {"ir": ir, "se": se, "n": T, "cat": cat}
    prior = sv._walk_forward_prior(ir_by_cat)

    # first pass: posterior + one-sided p-value (1 - p_skilled) for the FDR rail
    # The posterior runs on the NET %/yr axis (NOT the gross IR axis): on the gross IR axis almost every
    # surviving fund has IR>0, so P(IR>0)~1 is uninformative. The spec's posterior is on net-of-fee,
    # factor-deflated annual active where theta*=0 is meaningful. This PREVIEW applies the two DOWNWARD
    # rails the spec documents (both ONLY ever lower skill), as a flagged approximation until the real
    # skill_factors / skill_rails modules run:
    #   net_pct/yr ~= (posterior IR -> %/yr via omega) * FACTOR_ALPHA_SHARE - TER_category_median
    # omega (tracking-error scale) turns an IR into an annual active: active = IR * TE; we use the
    # spec's OMEGA_ACTIVE as the universe TE. FACTOR_SHARE 0.55 = spec's "~55% of gross excess survives
    # MKT/SMB/HML/WML/QMJ". TER = a coarse category-median proxy (no SEBI/AMFI TER feed yet).
    OMEGA = 0.06            # spec skill_posterior.OMEGA_ACTIVE (universe active-risk scale)
    FACTOR_SHARE = 0.55     # spec D.3.1 "only ~55% of gross excess survives factor deflation"
    TER_PROXY = 0.0125      # category-median regular-plan equity TER proxy (~1.25%/yr) - FLAGGED
    tmp = {}
    pvals = []
    for code, d in pf.items():
        if d.get("short"):
            tmp[code] = {"tag": "insufficient_history", "p_skilled": np.nan, "n": d["n"]}
            continue
        mu, tau = prior.get(d["cat"], prior["_universe"])
        # posterior on the IR axis (mean + sd), then map mean & band to the NET %/yr axis with the rails
        best_ir, _p_ir, lo_ir, hi_ir = sv._posterior_at_t(d["ir"], d["se"], mu, tau)
        if not np.isfinite(best_ir):
            continue
        sd_ir = (hi_ir - best_ir) / sv.CI90_Z if np.isfinite(hi_ir) else np.nan
        # gross %/yr = IR * omega; net = gross * factor_share - TER  (downward rails)
        def _net(ir):
            return ir * OMEGA * FACTOR_SHARE - TER_PROXY
        best = _net(best_ir)
        sd_net = abs(sd_ir * OMEGA * FACTOR_SHARE) if np.isfinite(sd_ir) and sd_ir > 0 else np.nan
        if not (np.isfinite(best) and np.isfinite(sd_net) and sd_net > 0):
            continue
        p = sv._norm_cdf(best / sd_net)                 # P(net active > 0)
        lo90 = best - sv.CI90_Z * sd_net
        hi90 = best + sv.CI90_Z * sd_net
        tmp[code] = {"best": best, "p_skilled": p, "lo90": lo90, "hi90": hi90, "n": d["n"],
                     "factor_share": FACTOR_SHARE, "ter": TER_PROXY}
        pvals.append((code, 1.0 - p))
    # book-level Benjamini-Hochberg at q=0.10 (the FDR rail, on the net-axis posterior p-values)
    passes = _bh_fdr({c: pv for c, pv in pvals}, q=0.10)

    recs = {}
    for code, d in tmp.items():
        if "tag" in d:   # the insufficient_history short-history funds
            recs[code] = {"tag": d["tag"], "p_skilled": np.nan, "post_best": None,
                          "factor_alpha_share": None, "fee_adjusted": False, "passes_fdr": False,
                          "n_months": d["n"]}
            continue
        tag = _tag_from_posterior(d["p_skilled"], d["lo90"], d["hi90"], passes.get(code, False),
                                  d["n"], None)
        recs[code] = {"tag": tag, "p_skilled": d["p_skilled"], "post_best": d["best"],
                      "factor_alpha_share": d["factor_share"], "fee_adjusted": True,
                      "passes_fdr": passes.get(code, False), "n_months": d["n"]}
    return recs


def _bh_fdr(pvals: dict, q: float = 0.10) -> dict:
    """Benjamini-Hochberg: which one-sided p-values survive FDR control at level q across the whole book.
    Returns {code: bool passes}. (The real Rail-3 lives in skill_rails; this is the audit-preview port.)"""
    items = sorted(((c, p) for c, p in pvals.items() if np.isfinite(p)), key=lambda kv: kv[1])
    n = len(items)
    if n == 0:
        return {}
    thresh_rank = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= (i / n) * q:
            thresh_rank = i
    cutoff = items[thresh_rank - 1][1] if thresh_rank > 0 else -1.0
    return {c: (p <= cutoff) for c, p in items}


# =============================================================================================== #
#  MOVE CLASSIFICATION
# =============================================================================================== #
# Coarse map: which legacy verdict is "the same side" as which new tag (so a fund whose label is the
# SAME side counts as unchanged; a cross-side change is a MOVE we attribute).
_LEGACY_TO_SIDE = {
    "skilled": "skilled",
    "ahead but not yet significant": "ahead",
    "good selector, weak sizer": "ahead",
    "lagging benchmark": "lagging",
    "insufficient history": "insufficient",
    "index-like": "index-like",
    "undefined": "ahead",
}
_TAG_TO_SIDE = {
    "skilled": "skilled", "likely_skilled": "ahead", "unproven": "ahead",
    "likely_unskilled": "lagging", "lagging": "lagging",
    "insufficient_history": "insufficient", "index-like": "index-like",
}


def classify_move(before_rec: dict, after_rec: dict) -> dict | None:
    """For a fund whose label changed side, attribute the cause from its rail flags:
        factor : factor_alpha_share low (most of gross excess was a tilt)
        fee    : fee subtraction flipped it
        fdr    : passes_fdr=False knocked it out of the published "skilled" list
        combos : "factor+fee" etc.
    Returns the locked `moved` entry {navindia_code,name,category,from,to,why,detail}, or None if the
    label is unchanged. Emphasis case: old-"skilled" -> new-"unproven/likely_unskilled"."""
    lv = before_rec.get("verdict", "undefined")
    tag = after_rec.get("tag", "unproven")
    from_side = _LEGACY_TO_SIDE.get(lv, "ahead")
    to_side = _TAG_TO_SIDE.get(tag, "ahead")
    if from_side == to_side:
        return None
    # attribute WHY (only meaningful when the live rail flags exist; preview leaves them None)
    why = []
    fas = after_rec.get("factor_alpha_share")
    if fas is not None and np.isfinite(fas) and fas < 0.6:
        why.append("factor")
    if after_rec.get("fee_adjusted") and after_rec.get("post_best") is not None \
            and np.isfinite(after_rec.get("post_best", np.nan)) and after_rec["post_best"] <= 0:
        why.append("fee")
    if after_rec.get("passes_fdr") is False and lv == "skilled":
        why.append("fdr")
    if not why:
        # preview (no live rails) or a posterior-driven demotion: attribute to the posterior bar itself
        why.append("posterior")
    detail = (f"legacy '{lv}' -> tag '{tag}'"
              + (f", p_skilled={after_rec.get('p_skilled'):.2f}" if after_rec.get("p_skilled") is not None
                 and np.isfinite(after_rec.get("p_skilled", np.nan)) else ""))
    return {"navindia_code": before_rec.get("code", ""), "name": before_rec.get("name", ""),
            "category": before_rec.get("category", ""), "from": lv, "to": tag,
            "why": "+".join(why), "detail": detail}


# =============================================================================================== #
#  THE AUDIT
# =============================================================================================== #
def audit(attrib_dir: str = ATTRIB_DIR, outpath: str = OUT_MD, allow_preview: bool = True) -> dict:
    """Build the full before/after audit: the population mix, the per-SEBI-category side-by-side table,
    and the moved-funds list with WHY. Write output/SKILL_MIGRATION_AUDIT.md and return the locked
    audit() dict. WRITES one report .md only — no publish, no live touch, no lock."""
    before = load_before(attrib_dir)
    after = load_after(attrib_dir, allow_preview=allow_preview)

    def _mix(counts, n):
        return {k: {"count": v, "share": round(v / n, 3) if n else 0.0}
                for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}

    before_mix = _mix(before["counts"], before["n"])
    after_mix = _mix(after["counts"], after["n"]) if after["n"] else {}

    # per-category side-by-side
    by_category = {}
    for cat in sorted(set(before["by_cat"]) | set(after.get("by_cat", {}))):
        by_category[cat] = {
            "before": before["by_cat"].get(cat, {}),
            "after": after.get("by_cat", {}).get(cat, {}),
            "n": sum(before["by_cat"].get(cat, {}).values()),
        }

    # moved funds (only computable where the after-tag exists for that fund)
    moved = []
    for code, brec in before["recs"].items():
        arec = after["recs"].get(code)
        if not arec:
            continue
        brec2 = dict(brec); brec2["code"] = code
        mv = classify_move(brec2, arec)
        if mv is not None:
            moved.append(mv)
    # emphasis: old-skilled -> demoted, sorted first
    moved.sort(key=lambda m: (0 if m["from"] == "skilled" else 1, m["category"]))

    skilled_before = before["counts"].get("skilled", 0)
    skilled_after = after["counts"].get("skilled", 0)
    headline = (f"\"skilled\" {skilled_before}/{before['n']} (1-in-{round(before['n']/max(1,skilled_before))}) "
                f"-> {skilled_after}/{after['n']} "
                f"(1-in-{round(after['n']/max(1,skilled_after))})" if after["n"] else
                f"\"skilled\" {skilled_before}/{before['n']} today; after-mix pending the rails rebuild.")

    result = {"before": before_mix, "after": after_mix, "by_category": by_category,
              "moved": moved, "headline": headline, "after_source": after["source"]}
    _write_report(result, before, after, outpath)
    return result


def _write_report(result, before, after, outpath):
    op = outpath if os.path.isabs(outpath) else _p(*outpath.split("/"))
    os.makedirs(os.path.dirname(op), exist_ok=True)
    src = after["source"]
    L = []
    L.append("# Skill-Engine Migration Audit — before / after\n")
    L.append(f"**Headline:** {result['headline']}\n")
    if src != "live-skill-block":
        L.append("> **AFTER source = `" + src + "`.** No `posterior=True` build exists yet, so the "
                 "after-tags here are an **on-the-fly PREVIEW** from the walk-forward IR-axis posterior "
                 "(skill_validate machinery), mapped onto the spec's NET-of-fee, factor-deflated %/yr axis "
                 "with the documented DOWNWARD rails as flagged APPROXIMATIONS: factor_alpha_share=0.55 "
                 "(spec '~55% of gross excess survives MKT/SMB/HML/WML/QMJ'), TER=1.25%/yr "
                 "category-median proxy, and a book-level Benjamini-Hochberg FDR (q=0.10) on the net-axis "
                 "p-values. These are PLACEHOLDERS for the real per-fund factor regression (skill_factors) "
                 "and the real SEBI/AMFI TER feed — every one only ever LOWERS skill. This is a structural "
                 "preview that demonstrates the rails-catch DIRECTION, NOT the confirmed after-mix; the "
                 "real numbers come from the rails rebuild + the pre-registered OOS validation. The "
                 "preview is still over-generous on `skilled` (the IR-axis SE under-states per-fund noise "
                 "until Component A's high-breadth signal replaces it).\n")
    L.append("## BEFORE — live legacy verdict mix (verified from `_manifest.json`)")
    L.append(f"_n = {before['n']} equity schemes._\n")
    L.append("| Verdict | Count | Share |")
    L.append("|---|---:|---:|")
    for k, v in result["before"].items():
        L.append(f"| {k} | {v['count']} | {v['share'] * 100:.0f}% |")
    L.append("")
    if result["after"]:
        L.append(f"## AFTER — new 5-state posterior tag mix  _(source: {src}; n={after['n']})_")
        L.append("| Tag | Count | Share |")
        L.append("|---|---:|---:|")
        for k in _TAG_ORDER:
            if k in result["after"]:
                v = result["after"][k]
                L.append(f"| {k} | {v['count']} | {v['share'] * 100:.0f}% |")
        for k, v in result["after"].items():
            if k not in _TAG_ORDER:
                L.append(f"| {k} | {v['count']} | {v['share'] * 100:.0f}% |")
        L.append("")

    L.append("## Per-SEBI-category — old verdict vs new tag")
    L.append("| Category | n | Old (top verdicts) | New (top tags) |")
    L.append("|---|---:|---|---|")
    for cat, blk in sorted(result["by_category"].items(), key=lambda kv: -kv[1]["n"]):
        ob = ", ".join(f"{k}:{v}" for k, v in sorted(blk["before"].items(), key=lambda kv: -kv[1])[:3])
        na = ", ".join(f"{k}:{v}" for k, v in sorted(blk["after"].items(), key=lambda kv: -kv[1])[:3]) or "—"
        L.append(f"| {cat} | {blk['n']} | {ob} | {na} |")
    L.append("")

    # moved funds — emphasise old-skilled demotions
    demoted = [m for m in result["moved"] if m["from"] == "skilled"
               and m["to"] in ("unproven", "likely_unskilled", "lagging", "insufficient_history")]
    L.append(f"## Funds that MOVED  _(total {len(result['moved'])}; old-\"skilled\" demoted: {len(demoted)})_")
    if result["moved"]:
        L.append("| navindia | Name | Category | From | To | Why | Detail |")
        L.append("|---|---|---|---|---|---|---|")
        for m in result["moved"][:120]:
            nm = (m["name"] or "")[:38]
            L.append(f"| {m['navindia_code']} | {nm} | {m['category']} | {m['from']} | {m['to']} | "
                     f"{m['why']} | {m['detail']} |")
        if len(result["moved"]) > 120:
            L.append(f"\n_…and {len(result['moved']) - 120} more (truncated for the smoke report)._")
    else:
        L.append("_No moves computed (no after-tags available)._")
    L.append("")
    L.append("\n## Honesty (the three rails each only ever LOWER skill)")
    L.append("1. **fee subtraction** (~0.9-1.8%/yr category-median TER proxy) wipes marginal gross-skilled.")
    L.append("2. **factor deflation** (only ~55% of gross excess typically survives MKT/SMB/HML/WML/QMJ) "
             "removes size/value/momentum tilts masquerading as selection.")
    L.append("3. **book-level FDR q=0.10** across the whole panel removes the residual luck tail.")
    L.append("\n_READ-ONLY over the funds_attribution JSONs. WRITES one report .md. No publish, no git, "
             "no build lock, no consumer-default flip._\n")
    with open(op, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return op


if __name__ == "__main__":
    r = audit()
    print(json.dumps({"headline": r["headline"], "after_source": r["after_source"],
                      "n_before": sum(v["count"] for v in r["before"].values()),
                      "n_after": sum(v["count"] for v in r["after"].values()),
                      "n_moved": len(r["moved"])}, indent=1))
