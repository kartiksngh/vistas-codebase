# Autoresearch ANALYST track — pre-registration (tag: analyst-jun30)

Start: 2026-06-30 ~03:58 IST. Branch: autoresearch/analyst-jun30 (isolated worktree).
Budget: <5h wall-clock. Mutable file: vistas/mesh_research.py — ONLY a new `desk_signal()`
assembly function (the analyst pitch score). FROZEN (never edited): evaluate, _ic_block,
build_panel, _assemble_wide, _valuation_wide, arm_baseline (the IC convention + data plumbing),
and vistas/arm_backtest.py, vistas/mesh_backtest.py.

## OBJECTIVE (minimise)
−IC_deflated of the desk pitch ranking. IC = monthly cross-sectional Spearman rank corr between
the ex-ante pitch score(t) and realised forward TR return(t->t+h) [the frozen evaluate() convention].
Headline horizon = 6m (ARM's natural peak per arm_backtest). Deflated = after the gauntlet (§3) + FDR.
Fama-MacBeth t = mean_IC / (std_IC/sqrt(n_months)) = IC*sqrt(BR*years) where BR≈12/yr scored months.
Secondary: decorrelation from ARM-alone (a signal that just echoes ARM adds no breadth).

## SEALED HOLDOUT (chosen BEFORE any trial; the loop NEVER evaluates on it)
HOLDOUT ERA = 2023-07-01 .. 2026-06-30 (most recent ~3y). All trials evaluate ONLY on
VALIDATION = 2013-01-01 .. 2023-06-30. The champion is one-shot tested on the holdout at the very end.
Implementation: validation IC restricts the monthly cross-sections to month_end <= 2023-06-30
(forward returns may extend into the holdout — that is the standard t->t+h overlap, acceptable, as the
SCORING DATE stays in-sample; the holdout test restricts SCORING dates to >= 2023-07-01).

## GAUNTLET (a champion must clear ALL applicable; honest GATED where data missing)
rand   : beats >=10k random signals of same shape on percentile, >=5y windows, all-starts (signal_navtest OWED -> GATED unless built)
wf     : walk-forward, forecast strictly predates return (a_t before r_{t+1}); total return; dead names retained. (structural: holds by construction of evaluate)
single : beats the single best component (ARM_LEVEL on the SAME rows) — beats_arm_6m AND beats best of {flow,breadth,mom,value} alone
era    : IC same sign across the 4 calendar eras (within validation: 2013-16, 2017-20, 2021-23H1)
plateau: IC flat across a neighbourhood of every free parameter (not a spike). THE main anti-overfit test.
luck   : tilt-matched block bootstrap of mean IC clears its bar (block≈3, >=2000x), t>1.96
fdr    : Benjamini-Hochberg across all session trials; champion survives FDR<=10%
tilt   : lift attributable to IC, not an unintended beta/size tilt. (size proxy = mcap_cr; check IC after size-neutralising)
fee    : net-of-fee — GATED (no TER data); stamped, never silent pass.

## DEAD ENDS (pre-known, do not re-walk)
- equal-weight blend z(flow)+z(dbreadth)+z(dARM_3m) DILUTES ARM (IC 0.071->0.054). [mesh_backtest proven]
- herding does NOT predict forward returns (refuted live).
- signals are WEAK: holding-rank IC split-half persistence ~0.097. No miracle is available.
- validated win to aim at: ARM+momentum+value orthogonalised ~ +0.037 OOS IC@6m. Small, plateau-robust.

## BASELINE
The current desk signal = the equal-weight blend (self_validate's CONVICTION_ADD_blend). To be measured.

## TRIALS (appended below, one block each, BEFORE editing)

---
## BASELINE MEASURED (validation 2013-01..2023-06, n=119 mo)
ew_blend IC@6m=0.0563 obj=-0.0563. ARM(same rows)=0.0708. **best single = mom_6m 0.1056** (>> ARM).
singles: mom_6m .106, mom_12m .099, arm_level .071, dbreadth .053, flow .045, value_z .041,
arm_trend_3m .030, quality .029. gate single:FAIL (blend < best single). Per-trial T ~2.8s.
KEY READ: the desk has been blending WEAK forces (flow/breadth/arm_trend) and IGNORING the
strongest validated force, MOMENTUM. The dilution lesson = weak legs drag strong ones down.

## TRIAL BATCH 1 — assemble from the STRONG validated forces, orthogonalised
P1. mode=weighted, weights={mom_6m:1}. Hypothesis: the single strongest force is the honest
    reference the composite must beat. Mechanism: 6m price momentum is a separately validated
    cross-sectional force (Jegadeesh-Titman); on this universe it out-ICs ARM. Expect IC~0.106,
    obj~-0.106. Pure IC; size-tilt risk (momentum can load on smid) -> tilt guard must clear.
P2. mode=weighted, weights={mom_6m:1, arm_level:1}. Hypothesis: ARM LEVEL adds INDEPENDENT
    info to momentum (analyst revisions vs price trend are different mechanisms). Expect a small
    lift over P1 IF arm_level decorrelates from momentum. Risk: equal-weighting a weaker leg (ARM
    .071) into a stronger (mom .106) DILUTES (the proven trap) -> may LOSE to P1.
P3. mode=weighted, orth=arm, weights={mom_6m:1, arm_level:1}. Same as P2 but momentum is
    orthogonalised vs ARM first (residual momentum), so the two legs are decorrelated and the sum
    is not double-counting. Mechanism: orthogonalisation is the validated fix for dilution. Expect
    >= max(P1,P2) IF the two carry independent IC. Pure IC.
P4. mode=weighted, orth=arm, weights={mom_6m:1, arm_level:1, value_z:1}. Add value (Ambit
    sign-replication: value x revision). Mechanism: cheap + improving-revisions + momentum is the
    classic 3-force blend the charter names. Risk: value is weak (.041) -> may dilute. Plateau will
    judge if value earns its place.
P5. mode=weighted, orth=arm, weights={mom_6m:2, arm_level:1}. Weight momentum 2x (it is ~1.5x
    the IC of ARM). Mechanism: weight by signal strength, not equal. Expect >= P3. Plateau check
    on the 2x will follow (1.5x, 2.5x).
P6. mode=weighted, weights={mom_6m:1, mom_12m:1}. Two momentum horizons (a smoother momentum).
    Mechanism: averaging two horizons of the same force reduces timing noise. Expect ~mom alone,
    maybe slightly higher t. Watch: they are highly correlated -> little breadth gain.
P7. mode=weighted, orth=arm, weights={mom_6m:1, arm_trend_3m:1}. Momentum + the analyst-revision
    DIRECTION (change, the "edge" quantity) rather than the level. Mechanism: change>level per the
    house law. arm_trend alone is weak (.030) though -> likely dilutes; test the law honestly.
P8. mode=weighted, orth=arm, weights={mom_6m:1, arm_level:1, dbreadth:1}. Add the breadth force.
    breadth alone .053. Test whether smart-money breadth adds beyond price+analyst.

Expected winner family: P3/P5 (momentum + ARM-level, orthogonalised, momentum-tilted). Every kept
champion must (a) beat the best single force, (b) era-stable, (c) survive size-neutralisation (tilt),
(d) clear the luck bar, AND (e) be plateau-robust across its weights.

---
## ★ SEALED HOLDOUT CHANGED (operator update 2026-06-30, applied BEFORE any champion sealed)
OLD holdout = 2023-07..2026-06 (last 3y). NEW holdout = **2021-01-01 .. 2026-06-30 (last ~5y)**.
VALIDATION (the loop's only evaluation window) = month_end <= 2020-12-31. For holdings-based legs
(flow/breadth) validation is 2013-04..2020-12 = ~93 months; for price/ARM legs it is longer.
Reason: last-3y is one homogeneous smid-momentum bull -> a tilt-disguised champion could pass it.
The 5y vault includes the 2022 rate-shock drawdown = a harsher, more honest OOS exam.
ALL P-trials are RE-EVALUATED on this corrected window (the earlier P1-P8 run used the old <=2023-06
boundary and is discarded as mis-windowed). Era stability now uses val sub-eras 2013-16/2017-18/2019-20.

## TRIAL BATCH 2 — DEEP-DIVE on P6 (mom_6m+mom_12m), the only single-beat candidate
SKEPTIC'S CONCERN: P6 beats best-single by a hair (.118 vs .112). mom_6m & mom_12m are ~the same
force (high corr) -> "beating the best single by averaging two correlated copies" may give ~zero
breadth (sqrt(BR)~1) and be noise-averaging, not independent info. Also momentum may be a size/
illiquidity tilt; mcap_cr is only a fund-MV proxy (weak size control).
P9.  PLATEAU on weight ratio: {mom_6m:1,mom_12m:1} vs {2,1},{1,2},{3,1},{1,3}. Mechanism: a true
     mechanism is FLAT across the blend ratio; a spike = curve-fit. Expect IC ~flat near .115-.119.
P10. Is the uplift real? compare P6 IC to mom_6m-alone and mom_12m-alone IC on the SAME 80 months
     (already have: .108/.~ vs .118). Decompose: per-month is P6 > mom_6m consistently?
P11. mom_3m horizon family: {mom_6m:1,mom_12m:1} plus a 3m leg -> does a 3rd horizon keep lifting
     (real multi-horizon structure) or dilute (just 6m matters)? Tests the "term-structure of
     momentum" idea honestly. (need a mom_3m column -> derive inline in a one-off, NOT in frozen panel)
P12. SIZE/tilt hard check: split the universe into mcap terciles, is P6 IC positive in the LARGE
     tercile (not just smid)? If IC only exists in smallcaps it is a size/illiquidity tilt, not alpha.
EXPECTED: P6 is a modest, plateau-robust momentum composite. The HONEST verdict may be that the
"composite" adds ~nothing over mom_6m (breadth~1) -> then the defensible champion is simply a
WELL-SPECIFIED momentum signal, and the lesson is "the desk's flow/breadth/ARM blend was the wrong
mutable; momentum is the validated workhorse this universe rewards". Either way: small, defensible.

---
## DEEP-DIVE RESULTS + CHAMPION + HOLDOUT VERDICT
P6 deep-dive: leg corr mom6~mom12 = 0.72 (NOT independent); P6 beats mom6 only 51% of months
(+0.008 IC) -> the composite is noise-AVERAGING, not independent breadth. BUT it is the only
assembly that beats its best single component in aggregate IC, and:
  - SIZE-TERCILE IC (val): small 0.114 / mid 0.104 / LARGE 0.091 -> survives in large caps, so it
    is NOT a pure size/illiquidity tilt (size-neutralised IC 0.102, 86% retained).
  - PLATEAU (P9): blend ratio 3:1..1:3 all IC 0.116-0.118 (flat = mechanism).
  - HORIZON PLATEAU (P11): {6,9,12}m blends all 0.114-0.118; mom_3m (0.085) DILUTES; no term structure.
  - FDR (Benjamini-Hochberg q=0.10, 13 trials): champion p=1.3e-10 << threshold -> a real discovery.

CHAMPION = z(mom_6m)+z(mom_12m), equal weight (commit 8812812).
  VALIDATION (<=2020-12, n=80): IC@6m 0.118, FM t 7.4, eras 0.107/0.139/0.112 (3/3 pos),
    size-neutral 0.102, boot frac_pos 1.0 t 4.62, beats best single 0.112.
  ★ SEALED HOLDOUT (2021-01..2026-06, n=59, NEVER seen, incl 2022 rate-shock): IC@6m 0.111,
    FM t 8.94, eras 0.116/0.111/0.099 (3/3 pos), size-neutral 0.107, boot frac_pos 1.0 t 6.18,
    STILL beats best single 0.107. VERDICT: HELD (same sign, ~same magnitude, plateau/decomp clean)
    -> CANDIDATE TO PROMOTE. (Old ew_blend on the same holdout COLLAPSED to IC 0.043, single:FAIL.)

GATED (honest holes, never silent passes):
  rand: signal_navtest.py (the all-starts x >=10k NAV bar) was OWED and NOT built this session
        (time) -> the literal NAV bar is GATED; the champion is validated on the IC-beat metric +
        block-bootstrap luck bar, NOT the NAV bar. Do not claim it passed the NAV bar.
  fee : net-of-fee GATED (no TER data). A 6m-momentum desk tilt implies turnover; real costs unmodelled.
