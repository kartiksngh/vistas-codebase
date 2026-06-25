# Digital AMC — Phase 0 (LIVE)

The first **visual, runnable** slice of the Agentic-AMC north star ([[AGENTIC_AMC]]). A paper firm of
agents — sector analysts, fund managers, a CIO — that read the terminal's real data, form grounded views,
pitch, escalate, and conclude. **Live:** <https://kartiksngh.github.io/vistas/digital-amc/>. Stamped
2026-06-26. Paper-money only; not advice.

## What it shows
- **11 sector analyst desks** (IT, Banking & Financials, Pharma, Consumer, Auto, Industrials, Energy,
  Metals, Realty/Infra, Telecom/Media, Diversified) — each with a stance, headline view, what it's working
  on, 3–5 stock pitches (with numeric evidence), and risks.
- **7 fund managers** (Flexi Cap, Large Cap, Mid Cap, Multi Cap, Value, Banking, Quant) — each with its
  REAL category skill stats (median IR, an exemplar fund's IR/IC/TE/verdict), a stance, positioning vs
  benchmark, pitches taken/declined, escalations, book tilt, and an investor-experience note.
- **CIO desk** — house view, the **3-lens market pulse** (Street=ARM vs Smart-money=flows vs Reward=price;
  the gaps are the signal), rulings on escalations, allocation across the 7 mandates, risk flags, summary,
  and the firm's bottom-line conclusion.
- **Communication log** — the full pitch → decision → escalation → ruling timeline (~69 messages).
- **Sector ARM pulse strip** — analyst-revision momentum by sector at a glance.

## Why it's defensible (not a toy)
Every view is **grounded in the terminal's real numbers** (ARM 0–100 revision momentum, net fund flows in
₹cr, PAT growth, real fund IR/IC) — agents **synthesize** the validated signals, they do **not** manufacture
alpha. Skill is framed by the **Fundamental Law** (IR = IC·√breadth·TC): analysts on IC, FMs on IR/TC, the
firm on breadth. The agents reason exactly this way in the output (e.g. *Wipro: funds distributed −₹4,986cr
overriding a decent ARM 61*; *HDFC Bank: flow ahead of revisions → hold, escalate the size*; CIO posture
*"confluence-only, breadth over heroes"*).

## How it's built (the pipeline)
1. **`vistas/amc_context.py`** — reads the baked `_screens/smart_vs_street.json` (per-stock sector/ARM/
   flows/growth/quadrant) + `funds_attribution/*.json` (per-fund IR/IC/verdict) → writes
   `output/_amc/context.json` + per-desk slice files `output/_amc/desks/`. No new fetch.
2. **The org workflow** (`scratchpad/amc_org.js`) — 19 agents: 11 analysts (read their desk file → DeskNote)
   → 7 FMs (read mandate + the pitch board → decisions/escalations) → CIO (synthesis). Structured-output
   schemas; default workflow agent (has Read). Returns the org object.
3. **`vistas/amc_site.py`** — renders the self-contained visual dashboard `output/_amc/site/index.html`.
4. **Publish** — mirror to `_pages/digital-amc/` + push (the Pages repo).

**Regenerate:** run (1), launch the workflow (2), extract its result → `output/_amc/org.json`, run (3),
copy to `_pages/digital-amc/` and push. (The agent step is LLM-driven, so it's a Workflow invocation, not a
pure script.)

## Phase-0 limits → next
- **Snapshot, manual cadence** — one as-of view (data 2026-05). NEXT: scheduled rounds (Cron) + a paper
  **book + blotter** so trades are recorded and P&L tracked.
- **No track record yet** — NEXT (P1): historical **walk-forward replay** (as-of past dates, no look-ahead)
  to score analyst IC / FM IR and start the learning loop ([[AGENTIC_AMC]] §10).
- **Charters are inline prompts** — NEXT: wire the **evolving knowledge inheritance** (the skills + memories:
  truths, refuted fallacies, pitfalls) into each agent's charter ([[AGENTIC_AMC]] "evolving knowledge base").
- **Valuation-cycle + the flow-decomposition (net-active) not yet in the desks** — fold in once #51 ships.

*Files:* `vistas/amc_context.py` · `vistas/amc_site.py` · `output/_amc/{context,org}.json` ·
`scratchpad/amc_org.js` (the org workflow). Design: [[AGENTIC_AMC]], evaluation backbone [[FUNDAMENTAL_LAW]].
