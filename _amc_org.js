export const meta = {
  name: 'digital-amc-org',
  description: 'Run the Digital AMC: sector analysts -> fund managers -> CIO, grounded in real desk data',
  phases: [
    { title: 'Analysts', detail: 'one agent per sector desk, reads its coverage, pitches FMs' },
    { title: 'Fund Managers', detail: 'one per mandate, takes/declines pitches, escalates' },
    { title: 'CIO', detail: 'synthesis: house view, 3-lens pulse, rulings, allocation, risk' },
  ],
}

const DESK = 'C:/Users/Administrator/Documents/Projects/Vistas/output/_amc/desks'

const DISC = [
  'You are a member of the Vistas Digital AMC — a PAPER-trading research firm (no real money).',
  'RULES: (1) Ground every view ONLY in the data given — cite actual numbers (ARM 0-100, net fund flow Rs cr, PAT YoY %, IR, IC).',
  '(2) You SYNTHESIZE signals into judgment — never invent data or claim alpha the data does not support.',
  '(3) ARM = analyst revision-momentum (0-100): high = analysts upgrading; it is a SMALL tilt (IC ~0.03-0.045), a 1-6 month clock, NOT a per-name guarantee.',
  '(4) Net flow = mutual funds actively buying(+)/selling(-) a stock. Quadrant: 1=analysts+funds both positive, 2=analysts ahead, 3=funds ahead, 4=both weak.',
  '(5) Be concise, specific, numeric, and defensible. No hype.',
].join(' ')

const ANALYSTS = [
  ['Technology', 'IT & Technology'], ['Financials', 'Banking & Financials'],
  ['Healthcare', 'Pharma & Healthcare'], ['Consumer', 'Consumer'],
  ['Auto', 'Auto & Mobility'], ['Industrials', 'Industrials & Capital Goods'],
  ['Energy', 'Energy & Power'], ['Materials', 'Metals & Materials'],
  ['RealtyInfra', 'Realty & Infrastructure'], ['TelecomMedia', 'Telecom & Media'],
  ['Diversified', 'Diversified & Special Situations'],
]
const FMS = [
  // Core Equity
  ['largecap', 'Large Cap'], ['largemid', 'Large & Mid Cap'], ['midcap', 'Mid Cap'],
  ['smallcap', 'Small Cap'], ['multicap', 'Multi Cap'], ['flexicap', 'Flexi Cap'],
  // Strategy
  ['value', 'Value & Contra'], ['focused', 'Focused'], ['elss', 'ELSS (Tax Saver)'], ['divyield', 'Dividend Yield'],
  // Thematic
  ['banking', 'Banking & Financials'], ['pharma', 'Pharma & Healthcare'], ['tech', 'Technology & Digital'],
  ['consumption', 'Consumption'], ['infra', 'Infrastructure'],
  // Hybrid & Asset Allocation
  ['baf', 'Balanced Advantage'], ['agghybrid', 'Aggressive Hybrid'], ['multiasset', 'Multi-Asset'], ['eqsavings', 'Equity Savings'],
  // Quant
  ['quant', 'Quant / Systematic'],
]
const FM_NAMES = FMS.map(([, n]) => n).join(', ')

const DESKNOTE = {
  type: 'object', additionalProperties: false,
  required: ['stance', 'headline', 'working_on', 'pitches', 'risks'],
  properties: {
    stance: { type: 'string', enum: ['bullish', 'constructive', 'neutral', 'cautious', 'bearish'] },
    headline: { type: 'string' },
    working_on: { type: 'array', items: { type: 'string' }, maxItems: 5 },
    pitches: {
      type: 'array', maxItems: 5, items: {
        type: 'object', additionalProperties: false,
        required: ['stock', 'action', 'thesis', 'evidence', 'horizon', 'conviction', 'to'],
        properties: {
          stock: { type: 'string' },
          action: { type: 'string', enum: ['accumulate', 'add', 'hold', 'reduce', 'avoid', 'watch'] },
          thesis: { type: 'string' }, evidence: { type: 'string' },
          horizon: { type: 'string', enum: ['immediate', '1-3M', '3-6M', '6-12M', '>1Y'] },
          conviction: { type: 'string', enum: ['low', 'medium', 'high'] },
          to: { type: 'array', items: { type: 'string' } },
        },
      },
    },
    risks: { type: 'array', items: { type: 'string' }, maxItems: 4 },
  },
}

const FMNOTE = {
  type: 'object', additionalProperties: false,
  required: ['stance', 'positioning', 'pitches_taken', 'pitches_declined', 'escalations', 'book_tilt', 'experience_note'],
  properties: {
    stance: { type: 'string', enum: ['risk-on', 'balanced', 'defensive'] },
    positioning: { type: 'string' },
    pitches_taken: {
      type: 'array', maxItems: 6, items: {
        type: 'object', additionalProperties: false, required: ['stock', 'from', 'why'],
        properties: { stock: { type: 'string' }, from: { type: 'string' }, why: { type: 'string' }, size: { type: 'string', enum: ['starter', 'core', 'overweight'] } },
      },
    },
    pitches_declined: {
      type: 'array', maxItems: 4, items: {
        type: 'object', additionalProperties: false, required: ['stock', 'why_not'],
        properties: { stock: { type: 'string' }, from: { type: 'string' }, why_not: { type: 'string' } },
      },
    },
    escalations: {
      type: 'array', maxItems: 3, items: {
        type: 'object', additionalProperties: false, required: ['topic', 'reason', 'ask'],
        properties: { topic: { type: 'string' }, reason: { type: 'string', enum: ['conflict', 'large-size', 'risk-limit', 'mandate'] }, ask: { type: 'string' } },
      },
    },
    book_tilt: { type: 'array', items: { type: 'string' }, maxItems: 5 },
    experience_note: { type: 'string' },
  },
}

const CIONOTE = {
  type: 'object', additionalProperties: false,
  required: ['house_view', 'market_pulse', 'rulings', 'allocation', 'risk_flags', 'summary', 'conclusion'],
  properties: {
    house_view: { type: 'string' },
    market_pulse: {
      type: 'object', additionalProperties: false, required: ['street', 'smart_money', 'reward', 'gaps'],
      properties: { street: { type: 'string' }, smart_money: { type: 'string' }, reward: { type: 'string' }, gaps: { type: 'string' } },
    },
    rulings: {
      type: 'array', maxItems: 6, items: {
        type: 'object', additionalProperties: false, required: ['on', 'ruling', 'rationale'],
        properties: { on: { type: 'string' }, ruling: { type: 'string' }, rationale: { type: 'string' } },
      },
    },
    allocation: {
      type: 'array', items: {
        type: 'object', additionalProperties: false, required: ['mandate', 'stance', 'note'],
        properties: { mandate: { type: 'string' }, stance: { type: 'string' }, note: { type: 'string' } },
      },
    },
    risk_flags: { type: 'array', items: { type: 'string' }, maxItems: 6 },
    summary: { type: 'string' }, conclusion: { type: 'string' },
  },
}

// ---------- Stage 1: analysts ----------
phase('Analysts')
const analystNotes = (await parallel(ANALYSTS.map(([key, name]) => () =>
  agent(
    `${DISC}\n\nYou are the ${name} sector analyst. Use the Read tool to read your coverage data at ${DESK}/analyst_${key}.json . ` +
    `It has arm_ew/arm_ff (equal- and float-mcap-weighted sector ARM), coverage_n, recommending_n, quadrants, and stock lists ` +
    `(top_by_mcap, arm_leaders, arm_laggards, flow_accumulated, flow_distributed, growth_leaders) — each stock with sym/name/mcap_cr/arm/flow_3m/pat_yoy/quadrant/fii_chg. ` +
    `Write your morning desk note: your current sector STANCE, a 1-2 sentence headline view, 2-4 things you are WORKING ON, your top 3-5 stock PITCHES ` +
    `(each with a crisp thesis + numeric EVIDENCE citing the actual ARM/flow/growth numbers, a horizon and conviction, routed via "to" to the relevant fund-manager desks by EXACT name ` +
    `[${FM_NAMES}] and/or "CIO" for large or contentious calls — route to the desks whose mandate actually fits the stock, e.g. a small-cap name to Small Cap/Mid Cap, a bank to Banking & Financials/Large Cap, a defensive compounder to Flexi Cap/Large Cap), and key RISKS. Be specific and numeric.`,
    { label: `analyst:${key}`, phase: 'Analysts', schema: DESKNOTE }
  ).then(r => r && ({ key, name, ...r }))
))).filter(Boolean)

// assemble the pitch board (analyst -> FM)
const board = analystNotes.flatMap(n => (n.pitches || []).map(p => ({
  stock: p.stock, action: p.action, thesis: p.thesis, horizon: p.horizon,
  conviction: p.conviction, from: n.name, to: p.to,
})))
const boardStr = board.map((p, i) => `${i + 1}. [${p.from}] ${p.action.toUpperCase()} ${p.stock} (${p.conviction}/${p.horizon}) -> ${(p.to || []).join(',')}: ${p.thesis}`).join('\n')
log(`Analysts done: ${analystNotes.length} desks, ${board.length} pitches on the board`)

// ---------- Stage 2: fund managers ----------
phase('Fund Managers')
const fmNotes = (await parallel(FMS.map(([key, name]) => () =>
  agent(
    `${DISC}\n\nYou are the ${name} fund manager at the Vistas Digital AMC. Use the Read tool to read your mandate + REAL skill stats at ${DESK}/fm_${key}.json ` +
    `(median_ir for your category, benchmark, and exemplar funds with info_ratio/ic_mean/ic_t/tracking_error/excess_cagr/hit_rate/verdict). ` +
    `Your job: deliver CONSISTENT category-relative outperformance with a GOOD investor experience and a QUALITY, mandate-compliant book. ` +
    `Remember the Fundamental Law: your information ratio = IC x sqrt(breadth) x transfer-coefficient — a high IR with low/negative revealed IC means the edge is NOT stock selection; respect mandate constraints (cap band, tracking error). ` +
    `\n\nThis morning's analyst pitch board:\n${boardStr}\n\n` +
    `Decide: your STANCE, how your book is POSITIONED vs benchmark, which pitches you TAKE (stock, from, why, size) — prefer those routed to you and fitting your mandate — which you DECLINE (why not), ` +
    `any ESCALATIONS to the CIO (ONLY for genuine conflict, large sizing, a risk limit, or a mandate question), your BOOK TILTS, and an investor-EXPERIENCE note (consistency/drawdown). Be specific and numeric.`,
    { label: `fm:${key}`, phase: 'Fund Managers', schema: FMNOTE }
  ).then(r => r && ({ key, name, ...r }))
))).filter(Boolean)

const escalations = fmNotes.flatMap(f => (f.escalations || []).map(e => ({ from: f.name, ...e })))
log(`Fund managers done: ${fmNotes.length} desks, ${escalations.length} escalations to the CIO`)

// ---------- Stage 3: CIO ----------
phase('CIO')
const notesSummary = analystNotes.map(n => `- ${n.name}: [${n.stance}] ${n.headline}`).join('\n')
const fmsSummary = fmNotes.map(f => `- ${f.name}: [${f.stance}] ${f.positioning} | tilts: ${(f.book_tilt || []).join(', ')}`).join('\n')
const escStr = escalations.length
  ? escalations.map((e, i) => `${i + 1}. [from ${e.from}] (${e.reason}) ${e.topic} — asks: ${e.ask}`).join('\n')
  : '(none this session)'

const cio = await agent(
  `${DISC}\n\nYou are the CIO of the Vistas Digital AMC. Use the Read tool to read the market summary at ${DESK}/market.json ` +
  `(per-sector ARM table arm_ew/arm_ff + each FM category's median IR).\n\n` +
  `Analyst desk headlines + stances:\n${notesSummary}\n\nFund-manager stances + tilts:\n${fmsSummary}\n\nEscalations raised to you:\n${escStr}\n\n` +
  `Produce: the HOUSE VIEW; the 3-LENS MARKET PULSE (street = what analysts/ARM say, smart_money = what fund flows say, reward = price/quadrant behaviour, and the GAPS between the lenses = the real signal); ` +
  `your RULINGS on the escalations (and any cross-desk conflict); ALLOCATION stance across the firm's divisions (Core Equity, Strategy, Thematic, Hybrid & Asset Allocation, Quant) and the notable mandates within them; ` +
  `RISK FLAGS (crowding/fragility — agreement of LEVEL across forces = crowding, agreement of CHANGE = conviction); a firm SUMMARY; and the firm's bottom-line CONCLUSION with the top actionables. Be decisive and defensible.`,
  { label: 'cio', phase: 'CIO', schema: CIONOTE }
)

return {
  data_asof: '2026-05',
  analysts: analystNotes,
  pitch_board: board,
  fund_managers: fmNotes,
  escalations,
  cio,
}
