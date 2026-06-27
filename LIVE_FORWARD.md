# LIVE_FORWARD.md — the live-forward LLM cadence (digital-AMC)

> The historical track was built by the **deterministic rules-FM** (cheap, reproducible, no look-ahead —
> `amc_replay.py`). **Live-forward, the LLM FM agents take the decision seat.** This doc is the design;
> the engine is `vistas/amc_live.py`; the decision round is the `amc_rebalance` Workflow.

## North-star fit
Each pilot scheme's book (`amc_book/<AMC>/<SCHEME>/`) was walked forward to the **seam** (the latest data
date, currently 2026-06-25). From the seam ON, the per-scheme **LLM FM agent** makes each rebalance —
weighing ARM **and** flows **and** valuation **and** momentum **and** the analyst pitch board **and**
portfolio construction (what to trim vs add, concentration, conviction) — not a single-force ARM clone (KV
R3). A **deterministic guardrail** enforces the hard constraints, so the LLM *proposes* and the mandate
*disposes*: the agent can never breach the cap band, the liquidity cap, or the equity floor, and can never
trade a name we can't price as-of the date (no look-ahead, no fabricated tickers).

## The loop (one scheme, one rebalance date `asof`)
1. **ASSEMBLE (deterministic, no look-ahead).** `assemble_fm_context(reg_entry, asof)`:
   - **Inherited book** — the holdings the FM takes over (the rules-FM seam book, then the LLM's own book
     thereafter): name, weight, mark, play-type, unrealised P&L since entry.
   - **Candidate desk** — `point_in_time_universe(asof)` → `_attach_forces`/`score_universe`, trimmed to
     the top ~N by the desk's multi-force brain score **∪** all current holdings **∪** analyst-pitch names;
     each carries sector, mcap-bucket, **ARM**, 6m-1m momentum, value-yield, net-active flow, quad.
   - **Quant baseline** — the rules-FM target (`construct_targets`) as the reference the FM may deviate from.
   - **Mandate** (cap band / max-pos / max-sector / equity floor), **cash**, current **scorecard**.
2. **DECIDE (LLM).** The FM agent (persona + the inherited evolving-knowledge charter — Fundamental-Law lens,
   ARM IC~0.05-not-a-guarantee, flow decomposition, the TC long-only leak, no curve-fit) returns structured
   **TradeTickets**: per name `{action: ADD/TRIM/HOLD/EXIT/NEW, target_weight, play_type, rationale, thesis,
   falsification}`, plus a book `stance` and an `experience_note`. The CIO agent then reviews **across** desks
   (breadth, crowding, risk, large-size escalations) and may trim/veto.
3. **GUARDRAIL (deterministic).** `enforce_guardrails`: clip every target to `min(max_pos, liquidity_cap)`;
   enforce the sector cap; drop any name absent from the priceable as-of universe; renormalise to ≤ the
   equity target; if the result is below the **mandate equity floor**, top up from the quant baseline
   (`deploy_with_floor` math) so the book stays compliant. The LLM cannot manufacture an illiquid or
   out-of-mandate book.
4. **EXECUTE (deterministic).** `execute_live`: diff final targets vs the inherited book → BUY/SELL trades
   (qty at `price_asof`, 15 bps/side cost to cash) → write `blotter.jsonl` + `prereg.jsonl` (the pre-registered
   thesis + falsifier, for honest scoring later) + the new `book.json`.
5. **MARK (deterministic).** `fact_sheet(book, asof)` → daily CITI sheet; NAV updated.
6. **LEARN (LLM, at the NEXT rebalance).** The FM reviews the prior period's decisions vs outcomes (which
   theses were confirmed/falsified, attribution) and writes a **pre-registered** lesson to `lessons.jsonl`
   + a compact `fm_memory.md` that the next DECIDE step loads. Anti-hindsight: graded on whether the
   *pre-registered* thesis played out, not on a story told after the fact.

## Cadence (orchestration)
- **Daily MARK** (no LLM): `amc_daily_mark.py` marks all books to the latest close. Cron ~8pm IST (folds
  into `pipeline.py`).
- **Monthly REBALANCE** (LLM): the round = assemble (Python) → `amc_rebalance` Workflow (LLM FMs + CIO) →
  guardrail + execute + mark + learn (Python) → rebuild + publish the digital-AMC site. Cron on the 1st
  trading day of the month (fires a session that runs the round). One-click: `Run AMC Rebalance.bat`.

## Discipline (non-negotiable)
- **Paper-money only.** **No look-ahead** (every signal/price ≤ `asof`). **Synthesize validated signals,
  never manufacture alpha.** **Learning is pre-registered** (the thesis + falsifier are written BEFORE the
  outcome is known). **Licensing:** the LLM may *see* raw ARM to reason, but raw per-stock ARM is **never
  persisted** to `amc_book/` — rationales are stored qualitatively ("strong revision momentum", not "ARM 78")
  and a scrub guard strips any `ARM <n>` before write. (The published digital-AMC *site* may show ARM — ABSL
  sign-off — but the committed audit trail must not.)

## First round (this build)
Run the seam rebalance for all 4 pilots and **compare the LLM-FM target book vs the rules-FM target** on the
same desk/date (overlap, active share, the top deviations + the FM's reason) — a direct read on whether the
LLM adds judgment over the quant baseline. Then surface the live-forward decisions + theses on the site.
