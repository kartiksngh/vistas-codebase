"""
scheme_identity.py — the (interim) scheme-identity layer for the fund pipeline.

WHY (first principles)
----------------------
`navindia_code` is a Capitaline vendor-internal scheme ID — non-standard AND not stable across feed
boundaries. The 2026-06-24 bedrock audit proved that at the 2025-07->2025-08 feed boundary, exactly
TWO real schemes were RE-CODED: the old code froze at 2025-07 and the recent months got a NEW code,
so one fund appeared TWICE in attribution (the long "skilled" history stranded on the old code, the
3-month fragment under the new code). This module folds those split codes back together.

It also gates out non-equity-skill noise the audit surfaced: the equity-skill universe should not
score arbitrage funds (market-neutral — excess-vs-benchmark is meaningless) nor one-month ingestion
fragments. Applying `canonical_code` BEFORE grouping merges the split histories; `in_skill_universe`
decides whether a scheme is scored.

This is the INTERIM layer. The full spine (#48) resolves every navindia_code -> AMFI scheme code (the
public standard) + ISIN; when that lands, CODE_ALIAS becomes a derived artifact of the AMFI mapping.
"""
from __future__ import annotations

# navindia_code -> canonical navindia_code. Seeded from the 2026-06-24 adversarial bedrock audit,
# which EXHAUSTIVELY proved these are the only two re-code splits (boundary 2025-07->2025-08,
# top-15 vst_id Jaccard 0.875, identical name + sebi_category). successor -> long-history canonical.
CODE_ALIAS = {
    "262": "1223",     # Kotak Large Cap Fund (IDCW): 3-month Aug-Oct'25 fragment -> 147-month history
    "456": "10291",    # Canara Robeco ELSS Tax Saver (IDCW): same boundary re-code
}

# Categories outside the equity-SELECTION-skill universe (the holdings-vs-benchmark attribution is not
# meaningful for them). They can still appear in the Funds (holdings) tab — just not the skill leaderboard.
EXCLUDE_CATEGORIES = {"Arbitrage Fund"}

# Below this many months a scheme is an ingestion fragment / too short to score (the audit found 32
# single-month Arbitrage codes that briefly appeared only in the 2025-08 feed).
MIN_MONTHS_UNIVERSE = 6


def canonical_code(code) -> str:
    """Map a navindia_code to its canonical scheme code (folds re-code splits)."""
    return CODE_ALIAS.get(str(code), str(code))


def apply_canonical(df, col: str = "navindia_code"):
    """Remap the code column in-place-ish (returns df) so split fragments fold into the canonical scheme.
    Call this on the holdings frame BEFORE any per-scheme groupby."""
    df = df.copy()
    df[col] = df[col].map(canonical_code)
    return df


def in_skill_universe(sebi_category: str, n_months: int) -> bool:
    """Is this scheme eligible for the equity-skill leaderboard?"""
    if str(sebi_category) in EXCLUDE_CATEGORIES:
        return False
    if n_months is None or n_months < MIN_MONTHS_UNIVERSE:
        return False
    return True
