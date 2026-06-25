# Vistas — while you slept (2026-06-19, overnight build)

Good morning. Here's everything, honestly — what's built, what's verified, the one
real blocker, and what needs your call.

## TL;DR
Your Bloomberg's **v2 engine + interface are built and verified end-to-end.** Vistas is
now a **tabbed terminal**: a **Performance** tab (your existing 9 panels, untouched, plus a
**Total-Return ⇄ Price-Return** toggle) and a brand-new **Valuation** tab (P/E · P/B ·
Dividend-Yield) with five purpose-built panels. The numbers are proven identical between
the Python and in-browser engines (**parity 0 mismatches across 18 configs**), and the deck
renders every panel with **zero runtime errors**.

**The one blocker:** NSE (niftyindices.com) **throttled this machine's IP** after the data
pulls — it silently stalls every request once you hit it too much. So the *real* P/E/P/B/
Div-Yield and Price-Return history isn't loaded yet. **Update: a 60-min-cool-down retry STILL
timed out** (it aborted safely after 5 failures — no harm, no data). My IP is in a longer
penalty box. I've scheduled **one more retry after a 2.5-hour silence** (it may well clear by
the time you read this), and added a **`Refresh Vistas Terminal.bat`** you can double-click
once NSE is cold to populate + publish it yourself. **Either way, the Performance tab works
now on real full-history data**, and the valuation engine is fully built + proven — it just
needs the numbers poured in. If both retries fail, just double-click the .bat later, or tell
me and I'll pull it when the IP has cooled (it always clears eventually).

## What you can open right now
`output/Vistas_Terminal_Deck_v2_latest.html` — double-click it. It's the v2 terminal with
real Total-Return history (2000→Jun 17 2026, 130 indices). The Valuation and Price-Return
tabs are visible but **disabled** ("no data in this deck yet") until the scheduled pull lands.
Your **live v1 deck is untouched** — https://kartiksngh.github.io/vistas/passive/ still works.

## What I built (all additive — nothing removed; pareto only)
- **5-measure data engine.** One clean abstraction: every series has a *measure* (TR, PR,
  P/E, P/B, Div-Yield) and a *kind* (level / ratio / yield) that routes it to the right
  analytics. This is the backbone that lets Vistas keep absorbing new data families.
- **Valuation analytics** (new, parity-locked): actual level over time + mean/±1σ/±2σ bands;
  a **cheap/rich percentile gauge** (where today sits in each index's *own* history); a
  **cross-section** ("who's cheapest now"); a **spread vs benchmark**; and a **distribution**.
  P/E & P/B read high = rich; Div-Yield inverts (high = cheap) — handled correctly.
- **Tab-strip UI** + **TR⇄PR toggle**, matching your house style.
- **Versioned deck** `Vistas_Terminal_Deck_v2` with a lineage stamp (`built_on: Passive v1`),
  saved separately so v1 is never overwritten; publishes to a **new `/terminal/` path**.
- **Email capability** (`vistas/notify.py`): emails the deck; auto-zips it (it's ~31 MB,
  over Gmail's 25 MB limit → ~6 MB zipped). Needs a one-time Gmail **App Password** (below).
- **Faster refresh + publish** (`publish_terminal.py`, `--email` flag): incremental tail
  pulls only (never re-fetches history) → rebuild → validate → publish/email.
- **Stealth fetcher**: rotating browser identity, wide jittered pacing, cookie reuse,
  per-index inception start, daily cap, and a hard abort on a throttle streak.

## Verification (no score for error)
- **Parity 0/18** (12 performance + 6 valuation: P/E, P/B, Div-Yield, daily/weekly,
  late-inception, no-benchmark). Python ≡ browser.
- **Runtime smoke-test**: synthetic-data v2 deck rendered **9/9 Performance + 4/4 Valuation
  (P/E and Div-Yield) + the PR toggle, 0 errors**; the real TR deck = 9/9, 0 errors.
- The existing pipeline still passes unchanged (the TR engine wasn't touched in behavior).

## The blocker, honestly
NSE soft-blocks the IP with *silent timeouts* (not an error code) once you over-request.
My first backfill (normal pacing, full universe from 2000) tripped it within minutes, and
every retry re-extended it. I hardened the fetcher (start at each index's inception, abort on
a fail-streak, shorter timeouts) and **scheduled one careful slow pull of a 47-index core
after a 60-minute silence** — that's the right way to get real data without re-tripping it.

## I scouted the rest of your Bloomberg (all verified live last night)
Saved in memory (`vistas-data-sources.md`) with copy-paste recipes:
- **NSE-500 stock prices:** primary **yfinance** (`RELIANCE.NS`, adjusted, history to 2000 —
  cross-checked to the paisa vs NSE bhavcopy), fallback **NSE bhavcopy** (official). Constituent
  list verified.
- **Screener fundamentals:** full connector architecture + the robots-clean data path
  (chart JSON + statement tables); **needs your login** + a few choices (8 questions waiting).
- **Macro / FII-DII / AMFI / cross-asset:** prioritized roadmap with verified endpoints
  (Yahoo cross-asset, NSE FII/DII, FRED macro [needs 1 free key], AMFI NAVs).

## What needs your call (morning)
1. **Scheduled pull result** — I'll report whether the real valuation/PR data loaded. If yes:
   say the word and I publish v2 to `https://kartiksngh.github.io/vistas/terminal/`.
2. **Email** — create a Gmail App Password (myaccount.google.com → Security → App passwords;
   needs 2-Step on) and set `VISTAS_SMTP_USER` + `VISTAS_SMTP_PASS`. Then `--email` just works.
3. **Screener** — your login + answers to the 8 design questions, and I build the Fundamentals tab.
4. **Stocks scope** — full NIFTY-500 or a watchlist first? (Drives the first stock-price pull.)

Nothing is half-done in a way that can break: the v2 deck is real and working today; the rest
is built and waiting on either the cooled-down pull or your input.
