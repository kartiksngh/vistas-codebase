export const meta = {
  name: 'amc-live-rebalance',
  description: 'Live-forward AMC rebalance: per-scheme LLM FM agents read their desk file and propose target weights; a CIO agent reviews the firm-wide book. Returns {decisions, cio} for the deterministic guardrail+execute step.',
  phases: [
    { title: 'FM decisions', detail: 'one FM agent per pilot scheme reads its desk → TradeTickets' },
    { title: 'CIO review', detail: 'firm-level review across the FM books' },
  ],
}

// args = { asof: "YYYY-MM-DD", schemes: [ { slug, scheme, amc, category, fm_key, path, n_candidates, n_held, aum_cr } ] }
// The FM agents READ their desk file (path) directly (they have the Read tool). They PROPOSE genuine
// conviction target weights; a deterministic guardrail downstream enforces mandate/liquidity/floor, so the
// FM does NOT need to satisfy caps exactly — it expresses a view. Paper-only. No look-ahead (the desk is
// already point-in-time as-of `asof`). Never invent tickers — use only `sym`s present in the desk candidates.

// args may arrive as a parsed object OR (harness quirk) as a JSON string — handle both.
const ARGS = (typeof args === 'string') ? JSON.parse(args) : (args || {})
const A = ARGS.asof || null
const SCHEMES = Array.isArray(ARGS.schemes) ? ARGS.schemes : []
if (!SCHEMES.length) throw new Error('amc-rebalance: no schemes in args (asof=' + A + ') — check the args payload')

const TICKET_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['stance', 'book_thesis', 'experience_note', 'tickets'],
  properties: {
    stance: { type: 'string', description: 'one-line overall positioning (e.g. "risk-on, overweight financials")' },
    book_thesis: { type: 'string', description: 'one-paragraph thesis for the whole book this rebalance' },
    experience_note: { type: 'string', description: 'what you are watching / what would change your mind (pre-registered, anti-hindsight)' },
    tickets: {
      type: 'array',
      description: 'your target book. One entry per name you want to HOLD at a non-zero weight, plus explicit EXITs at target_pct 0. Weights are pre-guardrail conviction; they need not sum to 100.',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['sym', 'action', 'target_pct', 'play_type', 'rationale', 'thesis', 'falsification'],
        properties: {
          sym: { type: 'string', description: 'ticker — MUST be a sym present in the desk candidates' },
          action: { type: 'string', enum: ['NEW', 'ADD', 'HOLD', 'TRIM', 'EXIT'] },
          target_pct: { type: 'number', description: 'target weight % of AUM (0 to exit)' },
          play_type: { type: 'string', enum: ['structural', 'cyclical', 'tactical'] },
          rationale: { type: 'string', description: 'one line — why this trade now' },
          thesis: { type: 'string', description: 'the bet, in one or two sentences' },
          falsification: { type: 'string', description: 'what observable would prove this thesis WRONG' },
        },
      },
    },
  },
}

const CIO_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['firm_view', 'risk_flags', 'cross_scheme_notes'],
  properties: {
    firm_view: { type: 'string', description: 'one-paragraph house view across the pilot books this round' },
    risk_flags: { type: 'array', items: { type: 'string' }, description: 'concentration / crowding / mandate-drift / macro risks spotted across the books' },
    cross_scheme_notes: { type: 'array', items: { type: 'string' }, description: 'where schemes agree/disagree, shared bets, breadth of the firm' },
  },
}

function fmPrompt(s) {
  return [
    `You are the fund manager of "${s.scheme}" (${s.amc}), an Indian ${s.category} mutual fund, running a PAPER book as-of ${A}.`,
    `Read your decision desk file (a JSON dossier) at this exact path and base every decision ONLY on it:`,
    `  ${s.path}`,
    `The desk contains: your mandate (equity floor/ceiling, per-name cap, per-sector cap, holding-count band, market-cap buckets), your current book (holdings + weights + P&L + cash), your track record, the QUANT BASELINE (the deterministic rules-FM target — your reference, deviate where you have a real view), and the candidate universe with per-name signals (arm, z_mom, z_val, z_arm, brain_score, net_flow_cr, held_pct, px) plus a signal_legend explaining each.`,
    ``,
    `Decide your target book for this rebalance:`,
    `- Use ONLY syms that appear in the desk's "candidates" list — never invent a ticker.`,
    `- Express genuine conviction weights. A deterministic guardrail will AFTERWARDS clip to mandate/liquidity caps and top up to the equity floor, so you do not need to hit the caps exactly — but stay broadly within the mandate's spirit (respect the holding-count band and don't grossly exceed per-name/sector caps).`,
    `- Treat ARM as a SMALL ~1-6 month analyst-revision tilt (IC ~0.05), never a guarantee; synthesize it WITH momentum, valuation, flows and your book — do not chase a single signal.`,
    `- LEARN FROM YOUR TRACK RECORD: if the desk has a "last_round_review" (your previous round graded vs your benchmark — hit rate, average active return, conviction-IC = whether you sized winners bigger, and your best/worst calls), use it honestly: keep what worked, cut the theses the market refuted, and do NOT re-chase a name your own falsifier flagged. If it is null, this is an early round with nothing to grade yet.`,
    `- For every ticket give a one-line rationale, a thesis, and a FALSIFICATION (what would prove you wrong). These are pre-registered for honest scoring — no hindsight.`,
    `- In all WRITTEN text (book_thesis / experience_note / rationale / thesis / falsification), describe analyst-revision strength QUALITATIVELY ("strong / rising / falling revisions") — do NOT quote a numeric ARM or StarMine score; the saved audit trail must not carry licensed values.`,
    `- Include explicit EXIT tickets (target_pct 0) for held names you are dropping.`,
    `Paper-money only. Be honest and defensible; you synthesize validated signals, you do not manufacture alpha.`,
    `Return the structured object.`,
  ].join('\n')
}

phase('FM decisions')
const fmResults = await parallel(SCHEMES.map((s) => () =>
  agent(fmPrompt(s), { label: `FM:${s.slug}`, phase: 'FM decisions', schema: TICKET_SCHEMA })
    .then((r) => ({ slug: s.slug, scheme: s.scheme, category: s.category, out: r }))
))

const decisions = {}
const fmBrief = []
for (const r of fmResults) {
  if (!r || !r.out) continue
  decisions[r.slug] = {
    stance: r.out.stance, book_thesis: r.out.book_thesis, experience_note: r.out.experience_note,
    tickets: r.out.tickets || [],
  }
  const longs = (r.out.tickets || []).filter((t) => (t.target_pct || 0) > 0)
  const top = longs.slice().sort((a, b) => (b.target_pct || 0) - (a.target_pct || 0)).slice(0, 8)
  fmBrief.push(`### ${r.scheme} (${r.category}) — ${r.out.stance}\n` +
    `${r.out.book_thesis}\nTop targets: ` +
    top.map((t) => `${t.sym} ${t.target_pct}% [${t.play_type}/${t.action}]`).join(', ') +
    `\n(${longs.length} long names, ${(r.out.tickets || []).length} tickets total)`)
}

phase('CIO review')
let cio = null
if (fmBrief.length) {
  cio = await agent(
    [
      `You are the CIO of a paper-trading multi-scheme AMC, reviewing this round's FM decisions as-of ${A}.`,
      `Each FM has independently set a target book within its mandate. Below are their stances and top targets.`,
      `Give a firm-level review: the house view across the books, the cross-scheme risks (concentration in shared names, crowding, mandate drift, macro), and where the schemes agree or diverge (firm breadth — de-correlated desks are healthy).`,
      `Do not re-pick stocks; assess the FIRM. Be concise and honest.`,
      ``,
      fmBrief.join('\n\n'),
    ].join('\n'),
    { label: 'CIO', phase: 'CIO review', schema: CIO_SCHEMA }
  )
}

return { asof: A, decisions, cio }
