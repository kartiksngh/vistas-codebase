# Fuzzy search — build spec  ✅ DONE & LIVE (2026-06-30)

> **SHIPPED.** Built per this spec, adversarial-reviewed (a NO-GO short-token-leak regression caught + fixed),
> re-verified (node unit test + headless `_pup_fuzzy.js`, 0 errors), published live, source backed up (`eca16b7`).
> Implementation lives in `static/vistas.js` (`fuzzyScore` & friends, just above `class MultiSelect`). Regression
> harness = `_pup_fuzzy.js`. See the in-repo `MEMORY.md` RESUME for the full record. Spec kept for provenance.

> State-of-the-art typo-tolerant search across all terminal entities. Resume here after `/compact`.
> Pure display-layer JS in `static/vistas.js` — no analytics, **no parity port needed**. Then build → probe → publish.

## Goal / acceptance test cases (must ALL pass)
1. **`absl flexi`** → finds **Aditya Birla Sun Life Flexi Cap Fund** (acronym token `absl` + word token `flexi`). Currently returns nothing.
2. **`koatq qiant`** → finds **Kotak Quant** (typos: `koatq`→kotak, `qiant`→quant). Edit-distance tolerance.
3. **`hdfc smallcap`**, **`icici pru tech`**, **`sbi blue`** → the obvious fund/stock. (acronym + partial word)
4. Don't regress: exact ticker (`TCS`), exact substring (`reliance`), acronym-prefix still match and rank top.
5. Ranking: the best (closest) match is #1; results ordered by score; cap ~50-100.

## Why it fails today
Every matcher is **substring `.includes()` only**, no tokenization / no edit distance / no acronym-token match:
- `MultiSelect._match(o, q)` — `static/vistas.js` ~L598 (universe picker: name/label/aliases + acronym `.startsWith`).
- `ComboBox._match(it, q)` — ~L1558 (single-select: sym/name `.includes` + acronym prefix).
- The **command palette** (`cmdk`) + `buildEntityIndex()` (indexes indices+stocks+world+fundamentals companies; ~2500 entities; name/label/acronym).
- `absl` isn't a substring of "Aditya Birla Sun Life" (it's the ACRONYM); a multi-token query where one token is an acronym and another a word isn't handled; typos never match.

## Algorithm — a self-contained lightweight fuzzy scorer (no deps; offline-safe)
Add ONE shared helper `fuzzyScore(query, target)` and route all matchers through it (keep a fast-path: if `query.includes` substring or acronym-prefix, short-circuit high score).

```
fuzzyScore(query, target):
  q = norm(query); t = norm(target)          # lowercase, strip punctuation/extra space
  if !q: return 1
  qTokens = q.split(/\s+/)
  # target fields to match a token against: each word of t, the full t, and t's ACRONYM (first letter of each word)
  tWords = t.split(/\s+/); tAcr = tWords.map(w=>w[0]).join("")
  perToken(qt):
     best = 0
     # 1 exact substring of full target -> 1.0 ; prefix of a word -> 0.95 ; substring of a word -> 0.85
     # 2 ACRONYM: qt is a prefix of tAcr (e.g. "absl" ⊂ "abslfcf") -> 0.9 ; qt == subsequence-of-firstletters -> 0.8
     # 3 EDIT DISTANCE (Damerau-Levenshtein) of qt vs each tWord and vs tAcr:
     #     score = 1 - dist/max(len) ; accept only if dist <= ceil(len(qt)/4)+1  (≈1 typo per 4 chars +1)
     return max over the above
  # ALL query tokens must clear a floor (≈0.55) -> AND semantics; total = avg(perToken) + small bonus if tokens matched in order
  if any qt with perToken(qt) < FLOOR: return 0
  return avg(perToken) (+ order/contiguity bonus)
```
- **Damerau-Levenshtein** (handles transpositions `koatq`→kotak) — compact DP, cap length ~24, early-exit if min-row > threshold. (Plain Levenshtein acceptable if DL is fiddly, but DL catches transposition typos better.)
- **Acronym handling is the key for `absl flexi`**: build the target acronym from word-initials; a query token that's a prefix of (or fuzzily matches) the acronym scores high; remaining tokens match words. So `absl`→acronym hit + `flexi`→word hit ⇒ match.
- **Index richer fields** so there's more to match: clean display name (after the display-name-normalize task), AMC/fund-house, category, ticker, existing aliases, and the computed acronym. Precompute per-entity `{norm, words, acr}` once in `buildEntityIndex` / the picker `setItems` (memoize — don't recompute per keystroke; ~2500 entities × per-keystroke DL must stay snappy: short-circuit substring first, only DL the survivors).

## Wiring
- New helpers near the top utils: `fuzzyNorm(s)`, `damerauLev(a,b,cap)`, `fuzzyScore(q,t)`, `fuzzyRank(q, items, getFields) -> sorted [{item,score}]`.
- `MultiSelect._match` / `ComboBox._match`: replace the boolean `.includes` chain with `fuzzyScore(q, fieldsJoined) > 0` (or score each field, take max); in the list render, **sort by score desc** then slice.
- Command palette: replace its filter with `fuzzyRank` over `buildEntityIndex()`; keep keyboard nav.
- Keep the substring/prefix fast-path so common exact queries stay instant and rank top.

## Verify → publish
- Extend a headless probe (or `_pup_*`/the cmdk path) to type the 5 test queries and assert the expected entity is in the top results, 0 throws. `node --check static/vistas.js`.
- JS change → needs a **full rebuild** (`publish_terminal.py --no-fetch`) to re-inline `vistas.js`; then `--no-rebuild` to push. (Compose with the display-name-normalize + NAV-attach-audit tasks into ONE build if doing them together.)
- Adversarial pre-publish review workflow (like the last publish) before the irreversible push.
```
```
## The other two queued tasks (do in the same build if convenient)
- **Display-name normalize:** strip trailing plan tokens ("- Regular (G)", "- Direct - Growth", "(G)", "(IDCW)") but PRESERVE category words ("Equity Savings", "Regular Savings"); algorithm = pop trailing plan-tokens until a content word. Apply at `funds_attribution` manifest (L449) + rec scheme_name (L278) + `funds_bridge` holdonly (L76). Feeds cleaner names INTO the search index.
- **NAV-attach audit:** ~459 holdings schemes don't name-match a Direct NAV; audit which genuinely have one (better fuzzy name match / the rename name-set) and wire — for the vs-real-NAV surface only (funds_nav; deck.py L789-792; vistas.js L188). Read-only audit first; wire only if it adds a visible surface.
