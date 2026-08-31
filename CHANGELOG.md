# FinPilot AI — Project Report

A running record of what was **added**, **fixed**, and **changed**, newest first.
Every feature or fix gets an entry here so the state of the project stays
traceable without digging through git history.

**Conventions used below**
- **Added** — new capability that did not exist before
- **Fixed** — something that was broken, with the symptom that gave it away
- **Changed** — existing behaviour or structure deliberately altered
- **Verified** — how it was actually proven, not just assumed

---

## 2026-09-05 — Live camera capture for the Invoice Scanner

Full detail: [`docs/superpowers/specs/2026-09-05-camera-capture-scanner-design.md`](docs/superpowers/specs/2026-09-05-camera-capture-scanner-design.md).
A new **Camera** option on `/app/scanner`, alongside Browse/WhatsApp/Slack/Email — opens a live
camera view with a real-time document-edge overlay, rejects blurry or incompletely-framed
captures on the spot with a specific reason before anything uploads, and — once confirmed with
just Category and Payment Method — sends the photo through the exact same `scanInvoice()` path
every other upload source already uses.

**Added** — `frontend/src/lib/document-detection.ts`: pure, unit-tested blur (variance of
Laplacian) and document-framing (quad-candidate scoring) quality checks, with no OpenCV.js/DOM
dependency.

**Added** — `frontend/src/lib/opencv-loader.ts` + `opencv-document-scanner.ts`: lazy-loaded
OpenCV.js-backed live document-edge detection, loaded only when the Camera dialog opens.

**Added** — `frontend/src/components/scanner/camera-capture-dialog.tsx`: the live view, overlay,
capture/retake/upload flow.

**Added** — a Vitest test harness for the frontend (previously none existed), used for this
feature's own pure-logic unit tests.

**Verified** — zero new backend surface: a camera-captured photo is indistinguishable from a
browsed file by the time it reaches `extract_documents_with_engine`/`preprocess.py`, which are
unchanged. Frontend unit suite: 14 passed. Type-check and lint clean. Manual camera verification
(spec's own Task 9 checklist) still needs a real device — not something this session could run
itself.

---

## 2026-09-05 — P1-E.1 Deterministic prose-text access for category classification

Full detail: [`docs/invoice-ocr-plan.md §25`](docs/invoice-ocr-plan.md). Closes P1-E's own disclosed
gap: the classifier had no access to a document's own prose OCR text, so category evidence stated only
as free text (a cash-voucher's "as Salary/ Stipend") never reached `classify_category`. No LLM, no
Golden Dataset hardcoding, no vendor→category mappings, no taxonomy change.

**Added** — a `raw_text` field threaded through all five hops of the AI-Engine→invoice-service schema
(`invoice_extraction.ExtractedInvoice` → `ExtractedInvoiceResponse` → `ExtractedInvoiceSchema` →
`classify_category`), `Optional[...] = None` at every hop so nothing predating this field breaks.

**Fixed** — a naive first design that added `raw_text` to the same haystack as vendor/items/filename
(all searched together) caused real regressions, live-verified and root-caused: `raw_text`'s own
mandatory FBR compliance boilerplate and blank invoice-template "Shipping" field label were each false-
matching a category via a bare keyword (`"fbr"`, `"shipping"` — both removed as the general bug, not
worked around); and a raw_text hit could override an already-correct structured answer, or confidently
resolve what P1-E's own report had already flagged as a genuinely unresolvable purchase-intent ambiguity.

**Changed** — `classify_category` now treats `raw_text` as **fallback-only**: consulted only when
structured evidence (vendor + items + filename) finds nothing at all, and even then, a fallback winner
backed by *only* weak (generic vendor-type) evidence is suppressed back to `None` rather than confidently
guessed — the same "missing is safer than wrong" principle every prior phase has held to. Mirrors the
structured-before-fallback precedence total extraction (P1-D) already established.

**Fixed** — the Golden Dataset benchmark harness (`runner.py`) never replicated `invoice_builder.py`'s
own "purchases only, transactional only" gate before calling `classify_category`, letting a minute
sheet's own administrative subject line reach the classifier via raw_text and produce a category a real
scan never would; added the same gate to the harness.

**Verified** — Golden Dataset: category accuracy **67% → 76%** (22/33 → 25/33), 3 documents fixed via
raw_text recovering strong evidence where vendor extraction had failed, 1 disclosed and unavoidable
regression (`43343WhatsApp…`/Coursera — raw_text's own strong "coursera" keyword confidently contradicts
this ledger's own idiosyncratic bookkeeping convention, already documented in `golden_dataset.py` as
unresolvable via any keyword rule), 0 new false positives, every other field byte-identical to the P1-E
baseline. Full suite: `invoice_extraction` 367/5 skipped, `ai-engine` 13, `invoice-service` **255** (up
from 243) — all passed, zero regressions outside the one disclosed case. P0 dangerous-total document
re-verified live: unchanged `needs_review` + both review flags.

---

## 2026-09-04 — P1-E Category candidate evidence & classification

Full detail: [`docs/invoice-ocr-plan.md §24`](docs/invoice-ocr-plan.md). Applies the shared Candidate/
`select_candidate` architecture to `category` (`invoice-service/app/services/category_classifier.py`),
including adding `invoice_extraction` as a real dependency of `invoice-service` to reuse it rather than
duplicate it. No LLM, no vendor→category hardcoding, no new taxonomy.

**Fixed** — a real rule-order bug: the old "first category in a fixed list with any keyword hit wins"
design meant a bakery vendor whose own item description said "Cake" always returned "Office
Entertainment", even though "cake" is itself a specific Employee-Care signal the fixed list never
reached. Keywords are now tiered strong (a specific occasion/product/service word) vs weak (a generic
vendor-type word), and the highest-scoring category wins — confirmed via a direct synthetic
reproduction, not assumed.

**Fixed** — word-boundary-aware matching, closing two real false-positive risks found by direct
construction: the old substring search let "rent" match inside "different"/"current"/"parent", and
"toilet" match inside "toiletries".

**Added** — "hardware store" keyword (Office Repair & Maintenance — fixed 3 real documents, all "Azeem
Electric & Hardware Store"), soft-drink brand names (Office Entertainment), "florist" (Employee Care).

**Removed** — the stale "papajohns" keyword, a workaround for a vendor-extraction bug P1-B already
fixed; confirmed dead code before removal via a dedicated regression test.

**Verified** — Golden Dataset: category accuracy **58% → 67%** (19/33 → 22/33), 3 documents fixed, 0
regressions, every other field byte-identical to the P1-D baseline. Two more real evidence signals
(soft-drink/cake) are correct and unit-tested but inert on their own two real documents due to an
unrelated line-item-table OCR quality issue (confirmed via a live extraction dump — not a category-logic
defect). Full suite: `invoice_extraction` 367/5 skipped, `ai-engine` 13, `invoice-service` 243 (up from
224), `backend/eval` 221 — all passed, zero regressions. P0 dangerous-total document re-verified live:
unchanged `needs_review` + both review flags — category has no path into financial safety validation.

---

## 2026-09-04 — P1-D Total candidate evidence & selection

Full technical detail: [`docs/invoice-ocr-plan.md §23`](docs/invoice-ocr-plan.md). Applies the P1
candidate/evidence/selection architecture to `total` — the most financially sensitive field this series
has touched, so every change was made with P0 safety as a hard constraint. Still 100% deterministic —
no LLM, no vendor hardcoding, no Golden Dataset overfitting, no P0 safety threshold changed.

**Fixed** — a real, confirmed bug (reproduced directly, not assumed): `LABEL_VARIANTS["total"]`'s own
bare "total" token matches the second word of "Sub Total:"/"Total Tax:"/"Total Discount:" lines, and
the old single-shot label lookup returned on the first line matching *any* total-type variant anywhere
in the document — so a subtotal, tax, or discount line printed before the real total could be returned
as if it were the grand total.

**Added** — `find_total_candidate_selection()`, generating a candidate for every total-shaped value in
the document (four label-strength tiers: strong/explicit labels like "Grand Total"/"Bill Total" >
the bare "total" token > the weaker Amount-Due/Balance-Due family (never assumed authoritative on its
own) > the dynamic-fields-discovery fallback), all judged by the same shared selection mechanism vendor
and invoice_date already use. New evidence: a large penalty for a value labeled with a *different*
financial field's own name (only when the match came via the bare "total" token, not a specific
compound phrase — an early self-introduced bug here was caught and fixed during this phase's own
implementation); a line-item-table-body exclusion; and arithmetic-consistency evidence (does this
candidate reconcile with subtotal+tax−discount, or the line-item sum) — a genuine, named, positive
signal, distinct from P0-B's own pre-existing `arithmetic_consistent` evidence key (a naming collision
between the two was caught and fixed before it could corrupt either).

**Fixed** — a real compatibility bug this phase's own new evidence exposed: P0-B's post-selection block
used to overwrite `total.evidence` outright rather than merge into it, which would have silently
discarded every new P1-D evidence key. Changed to merge; none of P0-B's own three evidence keys changed
name, value, or meaning.

**Verified** — Golden Dataset re-run against all 35 real documents: **total accuracy 73% → 85%** (24/33
→ 28/33 verifiable), 5 documents individually fixed and confirmed (including the original P0 dangerous-
total document — its `total` is now correctly `2,750.0`, not `277,000.0`, because this phase's
document-wide scan found the receipt's own real, separately-printed "Grand Total" line that the old
first-match lookup never reached; `subtotal`, out of scope for this phase, is unchanged and still wrong,
so the document still correctly routes to `needs_review` via `totals_arithmetic_mismatch`/
`total_magnitude_implausible` rather than the original `total_shares_source_with_another_field` — never
`auto_processed`, fully re-verified live). One regression reported honestly with its full nuance: a
receipt whose `total` moved from `2700.0` to `2701.0` — investigation found the receipt's own "Grand
Total"/"Paid Amount"/payment-gateway confirmation lines all independently agree on 2701, suggesting the
Golden Dataset's own ground-truth label of 2700 (the item's pre-fee sticker price) may itself be
imprecise, not that the extraction regressed. Zero changes to any other field (vendor/date/category/
document type/transactional all byte-for-byte identical to the prior baseline). Full suite:
`invoice_extraction` 367 passed/5 skipped (up from 345), `ai-engine` 13 passed, `invoice-service` 224
passed, `backend/eval` 221 passed, zero regressions.

---

## 2026-09-04 — P1-C Date candidate evidence & selection

Full technical detail: [`docs/invoice-ocr-plan.md §22`](docs/invoice-ocr-plan.md). Applies the P1
candidate/evidence/selection architecture to `invoice_date`, the way §21 applied it to vendor. Still
100% deterministic — no LLM, no vendor hardcoding, no Golden Dataset overfitting. Scope strictly
`invoice_date`; every other field untouched.

**Fixed** — a real, latent correctness bug found via code audit (not the Golden Dataset): the
`invoice_date` label vocabulary's bare "date" variant could match the literal word "date" inside a
*different* field's own label ("Due Date:", "Order Date:", "Payment Date:", ...), and the old
single-shot label lookup returned on the first line matching *any* variant, anywhere in the document —
meaning a due-date printed before the real invoice date could, in principle, have been silently returned
as the invoice date. None of the 35 Golden Dataset documents happens to trigger this ordering, but it is
a real, general risk for any future document.

**Added** — `find_invoice_date_candidate_selection()`, generating a `Candidate` for every date-shaped
value in the document (not just the first), feeding into the same shared `select_candidate()` vendor
already uses. Three label tiers (genuine invoice-date label > bare "date" > unlabeled positional,
confidence-tiered like vendor's own); a large penalty for a value labeled with a different date field's
own name (closes the bug above, while still returning that value if it's the only date-shaped text in
the whole document — recall preserved); a new `is_day_month_ambiguous()` check (validators.py) flagging
a numeric slash/dash date whose day and month could each plausibly be either ("06/02/2026") — never used
to pick a different parse (that would need vendor-specific knowledge, explicitly forbidden), only to
discount confidence and let a clearer, unambiguous candidate elsewhere outscore it. A lone low-
confidence date candidate is now flagged the same way vendor's own P1-B fix flags one.

**Verified** — Golden Dataset re-run against all 35 real documents: **0 improvements, 0 regressions, 0
newly missing/incorrect, 0 new false positives — a byte-for-byte identical outcome on every field**.
Confirmed honest: a fresh, independent debug run of every one of the 5 remaining `invoice_date` failures
found none fall into a category candidate-selection can fix (2 OCR-corrupted date spellings, 2 documents
with no date-shaped text anywhere in their OCR output at all, 1 genuine day/month ambiguity already
documented in §17 as intentionally unresolved). The real, live-verified improvement on that last
document: confidence dropped from `0.917` to `0.642` and `evidence.ambiguous_format: true` now appears —
the field honestly signals uncertainty on a value that was previously reported with full, undiscounted
confidence, rather than any change to the (still-incorrect) value itself. Full suite:
`invoice_extraction` 345 passed/5 skipped (up from 313, +32 tests), `ai-engine` 13 passed,
`invoice-service` 224 passed, `backend/eval` 221 passed, zero regressions. The P0 dangerous-total
document was re-verified live post-fix: still `needs_review`, still flags
`total_shares_source_with_another_field`, still returns the unmodified `277000.0` — P0 safety fully
intact.

---

## 2026-09-04 — P1-B Vendor candidate evidence improvements

Full technical detail: [`docs/invoice-ocr-plan.md §21`](docs/invoice-ocr-plan.md). Uses the P1
candidate/evidence/selection architecture to actually improve vendor accuracy, the work §20 explicitly
deferred. Still 100% deterministic — no LLM, no vendor hardcoding, no Golden Dataset overfitting. Date,
total (beyond P0-B), category, and tax_rate extraction remain untouched.

**Added** — a real vendor-candidate debug script run against the actual failing documents through the
live LiteParse/PaddleOCR pipeline, so every fix below is grounded in an actual printed candidate list,
not a guess.

**Fixed** — six new general vendor-rejection/evidence signals, each traced to a real live failure: a
currency-amount stamp whose digits were partially OCR-corrupted past the old digit-ratio check
("RsS40"); a field label glued onto the *same* OCR line as the vendor name ("Express Mart ate:" — a
misread "Date:"); a street/building address returned as the vendor ahead of the real one; a standalone
document-type-only line ("Cash Receipt") with no name after it; an email address ("papajohns@
livepepper.com"); and a personal letter/email salutation ("Dear Muhammad,") that would otherwise win
once the email address above it is rejected. Also expanded the vendor label vocabulary
("supplier"/"merchant"/"billed by") and the customer/account-holder rejection list ("bill to"/"ship
to", reusing phrases already recognized elsewhere for the customer field).

**Added** — a lone vendor candidate that never cleared its own OCR-confidence floor (a garbled corner
stamp code that was the *only* thing OCR recovered, the real vendor name never extracted at all) is now
flagged `low_confidence` and its confidence discounted, the same way an ambiguous multi-candidate
selection already was — previously reported at full confidence, indistinguishable from a genuinely
clean single-candidate read.

**Verified** — Golden Dataset re-run against all 35 real documents: **vendor accuracy 62% → 71%**
(21/34 → 24/34 verifiable), 3 documents individually fixed and confirmed, 0 newly-incorrect vendors, 0
new false positives. One indirect side effect found and root-caused rather than chased: a category-
classifier keyword (`"papajohns"`) that existed *only* as a workaround for the very email-address bug
this phase fixes lost its match on one document, dropping that document's category to unclassified —
explicitly left unfixed as a category-classifier issue outside this phase's scope. Full suite:
`invoice_extraction` 313 passed/5 skipped (up from 290), `ai-engine` 13 passed, `invoice-service` 224
passed, zero regressions. The P0 dangerous-total document was re-verified live post-fix: still
`needs_review`, still flags `total_shares_source_with_another_field`, still returns the unmodified
`277000.0` — P0 safety fully intact. Ten remaining vendor failures classified honestly: real OCR
corruption (5 documents, not solvable by candidate selection), a recurring cross-document artifact that
is genuinely the correct vendor elsewhere in the same dataset (2 documents — hardcoding it would break
those), legitimate branch/address text glued onto an otherwise-correct vendor line (2 documents, left
unfixed to avoid a truncation-safety risk), and one document whose correct vendor text is absent from
its own OCR output entirely (1 document, now honestly low-confidence instead of confidently wrong).

---

## 2026-09-04 — P1 Unified deterministic candidate/evidence/selection architecture

Full technical detail: [`docs/invoice-ocr-plan.md §20`](docs/invoice-ocr-plan.md). Fixes the audit's
own §6 finding that vendor/total/document_type/category each independently reinvented "which
candidate wins." Still 100% deterministic — no LLM, no vendor hardcoding, no Golden Dataset
overfitting. Date, category, and tax_rate extraction are explicitly untouched this phase.

**Added** — `candidates.py`, a small shared module with zero field-specific logic: a `Candidate`
(value + method + page/bbox + a `evidence: dict[str, float]` of named, signed contributions — negative
evidence is just a negative number in the same dict, no separate penalty mechanism), a
`SelectionResult` (winner, `"accept"`/`"review"`/`"unknown"` status, margin, and every candidate
considered, sorted strongest-first for explainability), and `select_candidate()` — the one shared
function every field's own candidate list feeds into. A lone candidate is always accepted (nothing to
be ambiguous against); two or more require the top score to clear the runner-up by a caller-supplied
margin or the result is `"review"`, never an arbitrary pick. 14 isolated tests prove every property
before any real field uses it.

**Changed** — vendor extraction (`fields.py`) now builds `Candidate` objects and calls
`select_candidate()` instead of its old ad-hoc filter-then-first-acceptable-line logic. The scoring
constants were chosen specifically to reproduce the exact prior three-tier priority order (label-anchor
always wins; floor-clearing OCR confidence beats sub-floor; document order breaks ties) — proven, not
assumed, by all 46 pre-existing vendor tests passing unchanged. `find_vendor_name()`'s public 4-tuple
return contract is completely unchanged (a new `find_vendor_candidate_selection()` exposes the richer
result to callers that want it); `_vendor_field()` in `extract_invoice.py` now discounts confidence
`×0.7` and records `evidence["ambiguous"]`/`margin` on a thin-margin selection, mirroring P0-B's
"discount, never discard" precedent.

**Added** — two new genuine (not decorative) evidence signals: `font_size` (declared in the schema but
confirmed unpopulated by any current OCR/PDF path — inert on all 35 real documents today, will activate
automatically once a font-aware extraction path exists) and `known_vendor_match` (+0.3, a company's own
previously-seen vendor names now influence *which* candidate wins a genuine tie, not just a post-hoc
confidence boost on an already-finalized value — proven unable to override a real confidence gap).
Also formalized rejection of bank/account-holder-looking lines as vendor candidates, general label
vocabulary only.

**Verified** — full suite re-run: `invoice_extraction` 290 passed/5 skipped, `ai-engine` 13 passed,
`invoice-service` 224 passed, zero regressions. Golden Dataset re-run against all 35 real documents:
vendor accuracy unchanged at 62% (0 improvements, 0 regressions, 0 changed outcomes on any document for
this phase's own field); the only diff anywhere in the 35-document/6-field comparison is one document's
`document_type`/`invoice_date` shifting, traced to OCR-level non-determinism (a `paddleocr` container
restart between runs) rather than to this phase's code, which touches neither path. The P0 dangerous-
total document was re-verified live post-P1: still `needs_review`, still flags
`total_shares_source_with_another_field`, still returns the unmodified `277000.0` — P0 safety fully
intact.

---

## 2026-09-04 — P0 Financial Safety Hardening: quotation/PO exclusion + total plausibility

Full technical detail: [`docs/invoice-ocr-plan.md §19`](docs/invoice-ocr-plan.md) and
[`docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md`](docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md)
(the P0-1/P0-2/P0-3 findings this phase fixes). Still 100% deterministic — no LLM, no vendor
hardcoding, no Golden Dataset overfitting.

**Fixed** — `quotation` and `purchase_order` document types were recognized but not excluded from
financial transaction processing; only `minute_sheet`/`approval_request` were. A quotation's proposed
price or a PO's ordered amount could reach `total`, category assignment, and the Saved Records
cashbook as if it were a completed purchase. Both are now `transactional=False` — but, unlike an
internal memo, their own vendor/total/subtotal fields are never cleared, since they name a real
external counterparty and carry a real document-described amount. Exclusion from the cashbook is
achieved entirely through the existing `transaction_status` filtering invoice-service already applies
to every non-transactional type.

**Fixed** — a structurally-valid total with no cross-check available (no subtotal, no line items — the
common shape of a simple receipt) could reach `auto_processed` regardless of how implausible it was
relative to the rest of the document. Root-caused a real, live case: `subtotal` and `total` had both
resolved to the identical page+bbox — two label matchers anchored onto the same OCR-garbled region, so
the existing arithmetic check couldn't catch it (both fields were wrong by the same amount and agreed
with each other). Two new deterministic signals in `arithmetic.py` — a magnitude ratio against
subtotal/tax/discount or the line-item sum (`[0.2, 5.0]`, never an absolute ceiling — a legitimately
large invoice that reconciles with its own subtotal is never penalized for size alone), and a
same-source-location check across `total`/`subtotal`/`tax_amount`/`discount` — both wired into the
existing `arithmetic_ok`/`review_flags`/confidence routing rather than a new parallel system. A
flagged total is never modified or discarded; it's kept fully visible and routed to `needs_review`,
which already excludes it from every Saved Records/cashbook query until a human confirms it.

**Added** — `FieldValue.evidence` (optional, `None` for every untouched field) and
`ArithmeticValidation.magnitude_plausible`, mirrored through both `ai-engine`'s and invoice-service's
schemas — extending the existing per-field metadata structure, not a parallel one.

**Verified**: 29 new tests (`invoice_extraction`), covering the quotation/PO classification boundary,
the two new arithmetic functions in isolation, and a synthetic A–H case set (a normal reconciling
document, the dangerous ~100x-off case, two legitimately-large-but-consistent invoices, unresolved
ambiguity preserved unchanged, line-item-only consistency, an unrelated large monetary mention that
must not be confused for the total, an approval-memo amount that must not become a total) — all
synthetic, none tied to a real Golden Dataset document or filename. Full `invoice_extraction`/
`ai-engine`/`invoice-service` unit suites re-run, zero regressions. Golden Dataset benchmark re-run
against the real 35 documents — see the P0 implementation report for the full before/after.

---

## 2026-09-03 — Vendor extraction re-audit: leading stamps and mid-line NTN numbers fixed

Full root-cause table and before/after benchmark: [`docs/invoice-ocr-plan.md
§18`](docs/invoice-ocr-plan.md). Re-audited all 15 remaining vendor failures individually against the
post-date-fix baseline (56%) rather than assuming §14's existing rules were the last word.

### Fixed
- **A stamped reference/amount marker glued to the front of the vendor's own line** ("Rs1100 Express
  Mart", "R8270 Express Mart") — the whole line clears the existing digit-ratio reference-number check
  since the real vendor's letters dilute the combined ratio, so the noise token rode along untouched.
  Fixed by reusing that exact same digit-ratio test on just the line's own first word
  (`_strip_leading_reference_stamp`, `invoice_extraction/fields.py`), dropping it only when more than
  one word remains.
- **A Pakistani NTN (National Tax Number) registration marker glued mid-line onto the vendor name**
  ("KFC Bahia NTN#0819531-5 Pindl Phase-7 (0154)") — truncated at the fixed, government-mandated `NTN`
  marker (`_truncate_at_registration_number`), a string no real business name ever legitimately
  contains.

### Investigated and deliberately left unresolved
- **1 document**'s stamp ("RsS40") is only 40% digit (an extra corrupted letter outnumbers its real
  digits) — genuinely not digit-dominant, correctly not stripped rather than loosening the threshold to
  force it.
- **9 documents** are §14's already-catalogued structurally-hard cases — re-confirmed, not newly
  attempted.
- **1 document** needs a compound leading-*and*-trailing strip this audit found only one supporting
  example for — left unresolved rather than generalized from a single case.
- **1 document**'s trailing noise fragment ("ate:", a bled-in "Date:" label missing its own "D") isn't
  a recognizable word to truncate at.

### Verified
- Golden-dataset benchmark, before vs. after: **Vendor 56% -> 62%** (19/34 -> 21/34); Document Type,
  Transactional, Invoice Date, Total, and Category all byte-for-byte unchanged; False Positives stayed
  at 0.
- Programmatic diff: **2 improvements, 0 regressions, 0 newly missing, 0 newly incorrect, 0 new false
  positives, 208 unchanged** — exactly the 2 targeted documents' vendor fields, `INCORRECT -> CORRECT`.
- The NTN fix also fired on a 3rd document (genuinely removing the registration-number noise, "KFC
  Bahia NTN#..." -> "KFC Bahia"), but still scores `INCORRECT` against the golden dataset's own
  abbreviated ground truth ("KFC") — reported honestly as a ground-truth granularity question, not
  claimed as a benchmark win it didn't score.
- 6 new tests, every one built on a synthetic vendor ("Riverside Traders") unrelated to the real
  dataset, including an explicit guard that a digit-*containing* (not digit-*dominant*) name like
  "7-Eleven" survives unstripped. `invoice_extraction` 229 passed / 5 skipped (+6).
- **Golden-dataset safety**: no filename, vendor name, or amount from the 35 documents appears in
  either rule; both key purely on structure (digit ratio, a fixed regulatory marker) and apply
  identically to any future tenant's documents.

---

## 2026-09-03 — Invoice date extraction: ordinal-suffix noise and missing separators fixed

Full root cause, audit table, and before/after benchmark: [`docs/invoice-ocr-plan.md
§17`](docs/invoice-ocr-plan.md). §15's second-highest-impact finding, audited individually
document-by-document before any code changed — including the suspected day/month transposition, which
turned out to be a genuine ground-truth ambiguity (a KFC-specific month-first timestamp format) and was
deliberately **not** "fixed" by hardcoding a vendor-specific rule.

### Fixed
- **Ordinal-suffix OCR noise** ("30th" reading as `30" June` or `30t June`) is now stripped from a
  narrow, closed character set (`s`,`t`,`n`,`d`,`r`,`h`, quote-like marks) sitting between a 1-2 digit
  day and a month name — never a broad "any character" class, so it cannot misread a glued month name's
  own letters as noise.
- **Missing separators** between a date's day/month/year components ("17Jun 2026", "28 Jun2026",
  "04May2026") are re-inserted before format-matching, recovering fully-glued dates as well as
  single-boundary gaps.
- Both changes live entirely in `invoice_extraction/validators.py` (`parse_date()` and
  `_DATE_SUBSTRING_PATTERNS`) — no change to label-anchoring, candidate selection, confidence scoring,
  or document classification.

### Investigated and deliberately left unresolved
- **KFC day/month ambiguity** (`06/02/2026` reading as 6 Feb instead of 2 June) — proven, via the
  *other* KFC receipt in the dataset, to be that vendor's own month-first timestamp format. Correctly
  resolving this needs vendor-specific knowledge, which this audit's brief explicitly forbids
  hardcoding — left unresolved rather than guessed at.
- **2 documents** where OCR corrupted the month name's own spelling or a digit inside the year — no
  structural rule recovers text that wasn't legibly printed.
- **1 document** (`9897WhatsApp…`) — a genuinely different, harder bug: unrelated receipt columns merge
  onto the date's own row, separating the day from its month with unrelated text. Flagged as a
  structural line-grouping problem for a future pass, not attempted here.

### Verified
- Golden-dataset benchmark, before vs. after: **Invoice Date 60% -> 83%** (18/30 -> 25/30); Document
  Type, Transactional, Vendor, Total, and Category all byte-for-byte unchanged; False Positives stayed
  at 0.
- Programmatic diff (isolating this task's own contribution, against the intermediate post-vendor-fix
  baseline): **7 improvements, 0 regressions, 0 newly missing, 0 newly incorrect, 0 new false
  positives, 203 unchanged** — all 7 targeted documents' `invoice_date` moved `MISSING -> CORRECT` and
  nothing else changed anywhere in the 35-document dataset.
- 11 new tests, every one built on generic dates unrelated to the real dataset ("3rd April 2026",
  "14Aug 2026") — general-pattern regression guards. `invoice_extraction` 223 passed / 5 skipped (+11).
- **Golden-dataset safety**: no filename, vendor name, or exact date value from the 35 documents
  appears in the fix; the KFC case was explicitly left unresolved *because* fixing it would have
  required a vendor-specific rule.

---

## 2026-09-03 — Non-transactional vendor-field leakage fixed (golden-dataset false positives: 4 -> 0)

Full root cause, code change, and before/after benchmark: [`docs/invoice-ocr-plan.md
§16`](docs/invoice-ocr-plan.md). The single false-positive class §15's golden-dataset benchmark found —
implemented, not just documented, per that section's own recommendation.

### Fixed
- **`vendor_name` is now cleared on confirmed non-transactional documents**, the same way
  `total`/`subtotal`/`tax_amount`/`discount`/`tax_rate` already were (§12). A Minute Sheet's own
  letterhead ("STIXOR") was still being reported as if it were a purchase vendor — the one field §12's
  original clearing block forgot. One line added to the existing `if not transactional:` block in
  `extract_invoice.py`; `find_vendor_name` and its candidate-generation logic (§14) are untouched.

### Caught and fixed in the benchmark tool itself (not production)
- `diff_baselines` (`backend/eval/golden_eval/runner.py`) miscategorized a `FALSE_POSITIVE -> CORRECT`
  transition as a "regression" — the improvement check only recognised `INCORRECT`/`MISSING` as the
  prior status. Fixed to check "was this a false positive before" first, with its own regression test.

### Verified
- Golden-dataset benchmark, before vs. after (`backend/eval/baselines/2026-09-03-baseline.json` vs.
  `2026-09-03-post-vendor-clear-fix.json`): **False Positives 4 -> 0**; **Vendor accuracy 44% -> 56%**
  (15/34 -> 19/34, exactly the 4 targeted documents); every other field (Document Type 70%,
  Transactional 100%, Invoice Date 60%, Total 73%, Category 61%) byte-for-byte unchanged.
- Programmatic diff: **4 improvements, 0 regressions, 0 newly missing, 0 newly incorrect, 0 new false
  positives, 206 unchanged** fields across the full 35-document dataset.
- Every transactional document's vendor result is identical between the two baselines — confirms
  transactional vendor extraction was not altered in any way.
- 2 new tests (the real Minute Sheet fixture, plus a synthetic, unrelated "Northwind Traders" example
  proving the rule fires on `transactional is False` alone, never on any wording) plus a strengthened
  assertion on the existing transactional-invoice test. Full suites unchanged elsewhere:
  `invoice_extraction` 212 passed/5 skipped (+2), `ai-engine` 13 passed, `invoice-service` 215 passed.
- **Golden-dataset safety**: no filename, document, or vendor-specific rule added; no June value
  hardcoded; the fix applies identically to any future tenant's non-transactional document.
- Next-highest-impact target, per this same benchmark, unchanged from §15: **vendor extraction
  generally** (56%, still the weakest field) and **date extraction** (60%, one confirmed day/month
  transposition bug re-surfaced) — documented as candidates, neither implemented here.

---

## 2026-09-03 — Golden Dataset evaluation / benchmark framework (no production changes)

Full methodology, implementation, and baseline numbers: [`docs/invoice-ocr-plan.md
§15`](docs/invoice-ocr-plan.md). §11-§14 each wrote a throwaway scan script to check the pipeline
against the same 35 real documents; nothing was kept, so every change re-derived ground truth and
comparison logic from scratch. This is that infrastructure, built once and versioned: a new
`backend/eval/` package — never imported by, and never modifying, any production service or library.

### Added
- **`backend/eval/golden_eval/`** — a reusable evaluation harness: field-level comparison
  (`CORRECT`/`INCORRECT`/`MISSING`/`UNVERIFIABLE`/`FALSE_POSITIVE`, numeric- and date-aware, never
  string-based), pure aggregation logic independent of any I/O, and the 35-document golden dataset
  itself with a documented, auditable evidence trail per row (own raw-OCR reading, cross-referenced
  against `June Cash book..xlsx` by amount where that reliably confirms a value).
- **`cli.py`** — `python cli.py [--detail] [--from-json PATH] [--save-baseline PATH]
  [--compare-baseline PATH]`, scanning the real running `ai-engine` (same HTTP path a real upload
  takes) and the real `category_classifier`, never a reimplementation.
- **212 tests** — synthetic-data coverage of every comparison outcome (including the spec's own
  critical example: `Expected 500 / Actual UNKNOWN` scores `MISSING`, `Expected 500 / Actual 50` scores
  `INCORRECT` — never conflated) plus golden-dataset self-consistency checks.
- **A versioned baseline** (`backend/eval/baselines/2026-09-03-baseline.json`) for future
  before/after regression comparison.

### Verified — baseline established, nothing "fixed" (by design; see docs §15 for the full numbers)
- Document Type 70%, Transactional 100%, Vendor 44%, Invoice Date 60%, Total 73%, Category 61% —
  all accuracy percentages computed only over fields with reliably-established ground truth.
- **4 false positives found**, all the same shape: every confirmed non-transactional Minute Sheet
  (§12) correctly shows `total`/`category` absent, but `vendor_name` still returns the internal
  letterhead as if it were a real vendor — §12's field-clearing logic never reached `vendor_name`. Flagged
  as the next phase's highest-impact target, not fixed here.
- Several previously-known extraction bugs (a subtotal read as total, a quantity read as a total, a
  total two orders of magnitude off, a day/month date transposition) independently re-surfaced by the
  benchmark, confirming it measures real gaps rather than inventing new ones.

### Safety
- **Zero production files changed**: `invoice_extraction` 210 passed/5 skipped, `ai-engine` 13 passed,
  `invoice-service` 215 passed — the exact same counts as immediately before this task, cross-checked
  against file-modification timestamps.
- No filename-specific logic, no hardcoded vendor name/amount/date, and no Excel value appears in any
  comparison or aggregation code — every one of those lives only in `golden_dataset.py`, a data file
  nothing outside `backend/eval/` reads.

---

## 2026-09-02 — Vendor/entity extraction audit: structural fixes, not vendor-specific hacks

Full audit, root-cause table, and generalization case-by-case: [`docs/invoice-ocr-plan.md
§14`](docs/invoice-ocr-plan.md). Seven real vendor-extraction failures were inspected against their
own real positioned OCR output before any code changed. The June Cash Book Excel was read as ground
truth per the brief, but its "Particulars" column turned out to be mostly an expense description or
payee name, not a vendor ("Basket Muqeet", "Power Plug", "Ifra") — only usable as a spot-check for the
few rows (KFC, Papa Johns, Dominos) where it's unambiguously a business name.

### Fixed — four structural rules in `find_vendor_name` (`invoice_extraction/fields.py`)
- **Provider-credit and order-fulfillment phrases are no longer mistaken for a vendor** — a leading
  "Powered By X"/"Software By X" self-credit, a "Thank you, please visit again" footer, or a whole line
  matching a closed order-fulfillment vocabulary ("pickup", "delivery", "dine in"...) is now skipped.
- **A generic document-title word glued onto the vendor's own row is stripped** — "INVOICE The Florist
  by..." now reads as "The Florist by...", not the whole row including its own template's title.
- **A business name printed across two lines is now merged** — "Azeem" / "Electric & Hardware Store"
  reads as one name, when the second line is digit-free, comma-free, and clears a stricter confidence
  bar than a single candidate needs (merging trusts two OCR reads together, not one).
- **The last-resort fallback prefers the highest-confidence remaining candidate over the raw first
  line** when nothing clears the ordinary floor — content over position once quality is compromised.

### Caught and fixed before shipping (two real bugs, own test's own audit)
- A negative vertical gap between two real, genuinely-separate OCR bounding boxes (padding overlap)
  was wrongly rejecting a true continuation — fixed by clamping the gap to zero rather than requiring
  it to be positive.
- A digit-free, comma-bearing address fragment ("Hotel view square, Bahria Spring") passed every other
  check and was wrongly merged onto an unrelated name — fixed by rejecting any comma-bearing
  continuation candidate, since an address-list convention is near-universal and a business-name suffix
  essentially never uses one.

### Deliberately left unresolved, not forced
- **A bank-app payment screenshot** (Coursera via NayaPay) — the real merchant descriptor sits far
  below the search window; the account-holder/company name occupies the position a vendor normally
  would. Needs dedicated screenshot-layout detection, not a vendor-name heuristic — flagged as future
  work.
- **One dense POS receipt** (KFC) — the real name sits at 0.822 confidence, just under the floor;
  fixing it further would mean building a bespoke rule for this one receipt's unusually field-dense
  layout, which is exactly the overfitting this audit was told to avoid.
- **Two OCR-typo cases** (Express Mart → "Exuress Mar", The Florist → "Flerisi") — one dropped or
  substituted character away from matching; hardcoding either misspelling would be a document-specific
  hack.

### Verified
- Full 35-document rescan against the live rebuilt stack: **5 of 35 vendor_name values changed, all
  improvements** (3 Azeem invoices now read their full two-line name; the Florist invoice recovers its
  owner's name; the Bakeshop document's wrong-but-plausible guess became a wrong-but-obviously-a-
  placeholder guess — same or safer, not a regression).
- **Zero regressions**: every non-vendor field (`total`, `invoice_date`, `transactional`,
  `document_type`, `review_status`, assigned category) on all 35 documents is byte-for-byte identical
  to the pre-change baseline.
- **False-positive audit**: all 35 final `vendor_name` values read end-to-end — zero garbled merges,
  zero new corruption introduced anywhere.
- 13 new tests, every one built on synthetic vendor names unrelated to the real dataset — general-
  pattern regression guards, not encodings of the 35 documents. `invoice_extraction` 210 passed / 5
  skipped (+13 from 197).
- **Golden-dataset safety confirmed**: no filename-specific rules, no hardcoded June-document text or
  vendor names anywhere in the new logic, no hardcoded Excel values, nothing that restricts extraction
  to these 35 documents — every rule is a structural/layout signal applying identically to any tenant's
  any document.

---

## 2026-09-02 — Category classification audit: 15 uncategorized transactional documents inspected

Full document-by-document root-cause table: [`docs/invoice-ocr-plan.md §13`](docs/invoice-ocr-plan.md).
With §12 settling which documents are transactions at all, this pass asked the remaining question for
the 31 that are: of the 15 with no category, is that a keyword-table gap or something else? Explicit
goal was not "categorize more documents" but finding which failures a deterministic rule can honestly
fix — most turned out not to be.

### Fixed
- **`"papajohns"`** added alongside the existing `"papa johns"` keyword (Office Entertainment) — a
  correctly-extracted vendor was the merchant's own transaction email
  (`papajohns@livepepper.com`), whose run-together spelling never matched the space-separated keyword.
- **`"filling station"`** added alongside `"fuel station"` (Vehicle Running & Maintenance) — the
  standard Pakistani naming for a petrol pump; a correctly-extracted vendor ("FSO DHA Filling
  Station") matched neither existing keyword.

### Deliberately left `UNKNOWN` — 13 of 15, with reasons
- **9 documents** need a vendor-name or line-item extraction fix to recover (a POS-software credit
  line, a company watermark, an order-type field, or a reference stamp gets picked as "vendor" instead
  of the real business name) — out of scope for a classification-rule change; the correct signal never
  reaches the classifier at all, so no keyword addition helps. Includes all 3 Azeem Electric & Hardware
  Store invoices in the dataset — a keyword fix for this exact vendor was tried and reverted in an
  earlier pass for the same reason; this audit reaches the same conclusion independently.
- **2 documents** are STIXOR's own internal "Cash Receipt" voucher template (salary and petty-cash
  payments), where the category-relevant word ("Salary/Stipend", "cleaning") appears only in a prose
  sentence with no structured field — a real gap, but a new extraction feature, not a keyword fix;
  flagged for a future pass rather than built here.
- **2 documents** are one specific OCR misspelling away from an existing keyword ("Florist" →
  "Flerisi", "Mart" → "Mar") — matching the typo itself would be a document-specific hack with real
  collision risk elsewhere in the table, rejected on the same grounds §11 already established.
- **1 document** has genuinely unreadable OCR — no rule recovers text that was never extracted.

### Verified
- Full 35-document rescan against the live rebuilt stack: category coverage of the 31 transactional
  documents **16/31 → 18/31**, exactly the two targeted vendors.
- **Zero regressions**: every extracted field, `transactional` flag, `document_type`, and
  `review_status` on all 35 documents is byte-for-byte identical to the pre-change baseline.
- **False-positive audit**: all 18 categorized documents (16 pre-existing + 2 new) independently
  re-checked against the keyword table — 18/18 explainable by an actual keyword hit, 0 unexplained. No
  previously-assigned category changed.
- `invoice-service` 215 passed (+2, from 213). `invoice_extraction` 197 passed/5 skipped and
  `ai-engine` 13 passed, unchanged (neither package was touched this pass).

---

## 2026-09-02 — Non-transactional document handling: Minute Sheets stop being mis-typed as bills

Full design and audit trail: [`docs/invoice-ocr-plan.md §12`](docs/invoice-ocr-plan.md). The prior
audit (§11) found 4 documents it deliberately did not fix — one company's internal "Minute Sheet"
approval memos — because reading their total out of prose wording would have been a document-specific
hack. This entry addresses the real gap one layer up: those aren't malformed receipts, they are a
different *kind* of document, and the pipeline had no way to say so.

### Added
- **Document type vs. transaction status, as two separate questions.** A new `transaction_status`
  column (`transactional` | `non_transactional`) on `Invoice`, orthogonal to the existing `status`
  workflow — a confirmed non-transactional document is `transaction_status=non_transactional,
  status=validated`, reusing the existing `/validate` endpoint rather than adding a duplicate one.
  `classification_source` (`rule` | `user_override`) records whether a human corrected the call,
  without ever overwriting the original deterministic classification.
- **Two new document types**, `minute_sheet` and `approval_request`, detected by their own keyword
  patterns checked *before* the generic invoice/receipt/bill keywords — fixing a real bug where a
  Minute Sheet's own itemized "Bill-1(Legal)" line matched the generic `\bbill\b` keyword and typed
  the whole document as a bill at confidence 1.0 (confidently wrong, not merely unresolved).
  `detect_document_type()` now also returns a `reason` quoting the document's own matched wording, so
  a reviewer sees *why* something was classified a given way.
- **`amount_mentioned`**, a field wired to always stay separate from `total`: a non-transactional
  document's stated figure ("For approval of Rs. 22,875/-") is preserved and shown, but never occupies
  `total`, `subtotal`, or feeds a cashbook category — nothing extracted is thrown away, but nothing
  non-transactional is silently booked as a transaction either.
- **`POST /{id}/reject`** (soft-delete — a new `rejected` status; the document and its extraction are
  kept, never removed — this service has no delete endpoint by design) and **`POST /{id}/reclassify`**
  (the "Process as Transaction" / demote action; moves `amount_mentioned`↔`total` only when the target
  field is currently null, so a real extracted value is never overwritten by a guess).
- A dedicated **"Non-Transactional Documents"** area in the frontend — deliberately not a generic
  "Uncategorized" bucket — showing detected type, confidence, amount mentioned, the plain-English
  classification reason, and Confirm / Process as Transaction / Reject actions.

### Changed
- Routing for non-transactional documents bypasses `confidence.route()` entirely and always lands on
  `needs_review` — never `auto_processed` (a human should confirm the classification at least once),
  never `needs_review_high_priority` (nothing is actually broken). `no_total_found` no longer fires on
  these documents; a `non_transactional_document` flag does instead.
- The cashbook's own record query now explicitly filters `transactionStatus: transactional`, so a
  confirmed non-transactional document cannot leak into the cashbook as an "Uncategorized" row.

### Verified
- Full 35-document rescan against the live rebuilt stack: all 4 real Minute Sheets — previously typed
  `bill`, `no_total_found`, `needs_review_high_priority` — now correctly classify `minute_sheet`,
  `transactional=False`, keep their stated amounts as `amount_mentioned` (Rs. 22,875 / 3,180 / 20,180
  / 115,260), `total=None`, routed to ordinary `needs_review`.
- **Zero regressions**: every extracted field, review status, and assigned category on the other 31
  documents is byte-for-byte identical to the pre-feature baseline.
- **The one rule that matters most, checked explicitly on every document**: no transactional document
  carries a value in `amount_mentioned`; no non-transactional document carries a value in `total`.
- A live end-to-end run (real file → `/scan` → real Postgres row, a real signed JWT minted against the
  running container) additionally confirmed reclassification moves `amount_mentioned`/`total` exactly
  as designed, not just in synthetic tests.
- `invoice_extraction` 197 passed / 5 skipped (+5), `ai-engine` 13 passed, `invoice-service` 213
  passed (+10). `npx tsc --noEmit` — zero errors.

---

## 2026-09-02 — `no_total_found` root-cause audit: 12 failures inspected individually

Full document-by-document analysis: [`docs/invoice-ocr-plan.md §11`](docs/invoice-ocr-plan.md).
Each of the 12 remaining `no_total_found` documents was inspected against its own raw OCR text and
token positions before anything was changed — several turned out not to be extraction-rule problems.

### Fixed
- **Address punctuation counted as a money amount.** `_looks_like_a_specific_amount` treated any
  comma as a money marker, so a street address ("Plaza 229, Third floor… Phase 7,") added two
  phantom candidates next to the real one — making a genuinely unambiguous total look ambiguous and
  refuse to resolve. A comma must now be a real thousands separator (`\d,\d{3}`).
- **`dynamic_fields` was never consulted for `total`.** A real invoice printed `"BillTotal:"` glued
  with no space, which `LABEL_VARIANTS` can never match as the word "total" — but the generic
  label/value discovery pass had already read it correctly and nothing looked. `_total_field` now
  falls back to it, requiring exactly one total-shaped key (excluding subtotal/tax/discount).
- **`"Amount Inc. Sales Tax"`** — the actual printed total line on Pakistani POS (KFC) receipts,
  which carry no separate "Total" label — added to `LABEL_VARIANTS["total"]`.

### Caught and fixed before shipping
- The first pass introduced a **false positive**: a handwritten `"Rs. 500"` OCR'd as one merged token
  `"Rs.5_cleaning"`, whose `"Rs.5"` prefix was matched and reported as a confident total of **5.0 for
  a receipt actually worth 500** — and that wrong-but-present number moved the document *down* from
  high-priority to ordinary review, looking more trustworthy than no total at all. Verified against
  the original photo, not assumed. A token whose digits run into letters is now refused outright.

### Deliberately not fixed
- **4 documents** are one company's internal "Minute Sheet" memos whose real total appears only as
  prose ("For approval of Rs. 22,875/-"). Recognising that phrasing would help 4 documents in this
  sample but is one vendor's own wording, not a general invoice pattern — a document-specific hack.
- **4 documents** have genuinely unreadable totals (faded/garbled photos).
- **1 document** (fuel-pump receipt) has a `"Total"` label whose value isn't adjacent and may be
  outside the photo frame; the only other figures are per-litre prices. Guessing between them is
  exactly the "pick the largest number" behaviour this design refuses.

### Verified
- Full 35-document rescan against the live rebuilt container, before and after:
  `total` found **23/35 → 26/35**, `no_total_found` **12 → 9**, high-priority review **15 → 13**.
- **Zero regressions**: 0 previously-found totals lost, 0 changed value.
- False-positive audit on every newly-found total: one flagged case (`total` equals `subtotal`/`tax`)
  was confirmed **pre-existing** — those two fields were already wrong before this change on a badly
  garbled document; the newly-found total matches the receipt's own printed total line, and the
  arithmetic validator now raises `totals_arithmetic_mismatch` and holds it in review.
- `invoice_extraction` 185 passed (6 new), zero failures.

---

## 2026-09-02 — Auto-processing routing audit + multi-document split root cause

Design/rationale and full numbers: [`docs/invoice-ocr-plan.md §9-10`](docs/invoice-ocr-plan.md).

### Fixed
- `needs_review_high_priority` was 35/35 even after the field-extraction fixes below it in this file
  measurably improved accuracy — traced to `critical_fields_present` requiring `invoice_number`
  (found on 0/35 real receipts — these are informal receipts, not numbered invoices) for every
  document type alike. Now only held critical for `document_type == "invoice"`. **18/35 now
  auto_processed, 2/35 needs_review, 15/35 needs_review_high_priority** (down from 35/35) — measured
  on the same live-redeployed pipeline against the same 35 real receipts, zero threshold changes.
- `arithmetic_ok` (routing's other input) ignored `date_plausible`/`tax_rate_plausible` entirely,
  only ever checking `totals_pass` — found live while stress-testing the fix above: a document with a
  flagged implausible date would have been silently auto-processed. Both now fold into `arithmetic_ok`.
- `find_vendor_name`'s positional fallback could land on a native-PDF document's own "Date issued..."
  header line ahead of the real vendor — found live on the same stress test. Now also rejects a
  candidate line that is itself a recognizable date.
- `total` had no positional fallback at all (13/35 `no_total_found`, the largest single review flag) —
  a self-issued cash-receipt voucher states its amount in prose with no "Total:" label anywhere.
  Added `find_total_anywhere`, deliberately the most conservative of this pipeline's fallbacks: only
  a comma/decimal/currency-marked amount counts as a candidate (excludes CNIC/NTN/phone-shaped digit
  runs), and only fires when exactly one such candidate exists on the whole document — two or more is
  ambiguous and stays NOT_FOUND rather than guessing. **23/35 total now found** (was 22/35).
- Category: added "Layers Bakeshop" (bakery) coverage to Office Entertainment. A second candidate fix
  (a "hardware store" keyword) was checked against the real extraction result, found to be dead
  weight (vendor_name never captured the business-name continuation line it needed to match), and
  correctly not shipped — logged as a different real gap instead of a fix that looks complete but does
  nothing.
- **Multi-document split never triggered on any real photo** (reported previously as an open gap) —
  root-caused and fixed for the side-by-side case: added `_split_by_content_valley`, using Canny edge
  density (a receipt's content is edge-dense; the desk between two receipts is not) rather than
  brightness, which is what failed originally. Went through three rounds of real-photo-driven
  tightening after finding new single-document false positives each time (a header/table layout gap,
  a two-column invoice template's own gutter) — final version verified against all 35 real receipts
  (zero false splits) and the real 2-receipt photo (correct 2-way split, both halves visually
  confirmed complete). The other 2 harder multi-bill-testing photos (overlapping receipts, one badly
  faded) correctly still fall back to single-document behaviour — an honest, documented limitation,
  not claimed fixed.

### Verified
- All 35 real receipts re-scanned against the live, rebuilt `ai-engine`/`invoice-service` containers
  before and after every fix in this entry — every number above is measured, not estimated.
- `invoice_extraction` 179 passed (11 new), `ocr` 48 passed (7 new), `ai-engine` 13 passed,
  `invoice-service` 198 passed (1 new) — zero regressions across all four suites.
- The real 2-receipt multi-document photo split end-to-end through the live API
  (`documents_found: 2`); both resulting crops individually opened and visually confirmed complete,
  with nothing cut off, before trusting the result.

---

## 2026-09-02 — Real-dataset audit: field-extraction and orientation fixes

Design/rationale and full before/after numbers: [`docs/invoice-ocr-plan.md §8`](docs/invoice-ocr-plan.md).
Prompted by running the actual deployed pipeline (LiteParse + PaddleOCR, already-deployed from an
earlier pass) against two real datasets: `test-data/june-2026-petty-cash/receipts/` (35 real
petty-cash receipts) and `multi-bill-testing/` (3 real multi-document photos).

### Added
- `invoice_extraction/fields.py::find_date_anywhere` — a document-wide, confidence-gated positional
  fallback for `invoice_date` when no label is present at all (there was previously no fallback for
  this field whatsoever).
- `invoice_extraction/validators.py::find_date_in_text` — extracts a date-shaped substring from
  noisier text (parse_date requires the whole string to match), recovering a date OCR glued directly
  onto its own label or a trailing time with zero separator character.
- `ocr/image_io.py::decode_image` — one shared EXIF-orientation-correcting image decode, used at all
  three of this package's image-decode sites.
- `Office / Misc Supplies` category gained a generic general/convenience-store keyword group
  (lowest priority in the table, so a more specific category always wins first).

### Fixed
- `invoice_date` extraction: 0/35 real receipts found a date before this fix; 20/35 after.
- `vendor_name`'s no-label positional fallback picked a printed reference number or a handwritten
  filing annotation instead of the real business name on every one of 35 real receipts; the fallback
  now skips a reference-number-shaped or low-OCR-confidence candidate line. 0/35 recognizable vendor
  names before, 22/35 after.
- EXIF orientation was never applied anywhere in `backend/libs/ocr` — a rotated phone photo was
  silently OCR'd, CV-processed, and previewed sideways. Fixed at all three decode sites via one
  shared helper (fixing only the OCR path, and not the bounding-box preview render, would have
  desynced the preview image from the field boxes computed against it).
- `Express Mart` / `Falcon Cash & Carry` (real, common, correctly-extracted vendors in the test
  dataset) matched no category keyword at all and fell to Uncategorized regardless of what was
  purchased.

### Investigated, correctly left unchanged
- `'STIXOR'` looked like the same annotation-noise pattern the vendor fix above addresses, on 6/35
  receipts. Pulling the raw OCR text directly showed `info@stixor.com`/`www.stixor.com` printed on
  those same receipts — it is the genuine, correctly-extracted name of the company issuing what are
  actually internal salary/stipend cash-receipt vouchers, not purchase invoices. No bug; not changed.
  A different, real gap remains here (categorizing a self-issued salary voucher, whose meaningful
  fields are the recipient's name and "Salary", not a vendor) — logged as future work, not built.
- Rotation/deskew and perspective correction were evaluated and deliberately not built: no photo in
  either real test set showed evidence of needing either, and this session's own multi-document-split
  investigation (below) already showed real-photo CV segmentation on this kind of image is fragile —
  building a skew estimator speculatively risked rotating an already-correct receipt for unproven benefit.

### Verified
- Both real datasets re-run against the live, rebuilt `ai-engine`/`invoice-service` containers
  before and after — the 0/35→20/35 and 0/35→22/35 numbers above are measured, not estimated.
- `invoice_extraction`: 168 tests passing (12 new). `ocr`: 45 passing (4 new). `invoice-service`: 197
  passing (3 new) — including an explicit regression guard that a more specific category (e.g.
  "ABC Stationery Mart" → Stationary/Others) still wins over the new generic-store fallback, and that
  "Smart Watch Store" is not misread as a general store.
- **Multi-document split, tested against the real 3-photo set, did not trigger on any of them** —
  including the cleanest case (two clearly side-by-side receipts). Root cause confirmed by direct
  diagnostics: real-desk wood grain isn't reliably darker than receipt paper in grayscale, so Otsu's
  threshold merges both receipts and the gap into one contour. An HSV-saturation alternative was
  tried and visually confirmed to *not* fix this either — background/foreground separates fine, the
  two receipts do not separate from each other. Left open, documented, not silently claimed fixed.

---

## 2026-09-02 — Document Preprocessing ahead of LiteParse/OCR

Design/rationale: [`docs/invoice-ocr-plan.md §7`](docs/invoice-ocr-plan.md).
Not deployed — locally tested only, per this session's standing constraint.

### Added
- **Document Preprocessing stage** (`backend/libs/ocr/ocr/preprocess.py`) —
  OpenCV-based, no ML model: detects a document's boundary in a phone-
  camera photo and crops to it with a safety margin (removing excess
  desk/background), and detects when a single photo confidently contains
  **two or more separate documents**, splitting each out as its own image.
  The one rule every threshold in the module enforces: when detection is
  not confident, the original image passes through completely untouched —
  no lost invoice content, ever, in exchange for occasionally leaving
  croppable background in place.
- **`ocr.extract_documents_with_engine()`** — a new function (the existing
  `extract_text_with_engine` is completely unmodified) that runs
  preprocessing for an image upload, then extracts each resulting document
  independently. Returns 1 result for the overwhelming common case
  (whether or not a crop happened), 2+ only for a confidently-split photo.
  PDFs bypass preprocessing entirely — a PDF page is already a single,
  clean, born-digital document.
- **AI Engine's `/ocr/extract`** now returns an additive
  `additional_documents` field (empty by default) — one full extraction
  plus the actual cropped image (base64) per extra document a photo
  contained.
- **Invoice Service's `POST /invoices/scan`** fans `additional_documents`
  out into a genuine extra `AIJob`/`Invoice` per split — a first-class,
  editable, categorizable record on Saved Records, not an attachment
  nested inside the primary invoice — with the same file-hash dedup and
  best-effort-per-item error handling the primary upload already has.
  `ScanJobResponse` gains `additional_invoice_ids: list[UUID] = []`.
- **`test-data/june-2026-petty-cash/receipts/`** — a fixture folder for
  validating this (and the category classifier) against a real, known-
  correct petty-cash cashbook once the real receipts are added there.

### Fixed
- A real bug caught by its own test: the appendix-image decode step for a
  corrupt/unreadable cropped image wasn't guarded (only the S3 fetch was)
  — would have crashed preprocessing over one bad candidate instead of
  skipping it. Caught before it ever ran against real data.

### Verified
- `backend/libs/ocr`: 41 tests passing (was 26) — `test_preprocess.py`
  (11, against real OpenCV: single-document crop, safety padding, near-
  full-frame no-op, two-document split with ordering/non-overlap, and two
  distinct conservative-fallback shapes) + `test_extract_documents.py` (4,
  mocked, orchestration only).
- `ai-engine`: 13 tests passing (was 9) — real end-to-end PDF path
  unaffected (`additional_documents == []`), plus mocked multi-document
  response-shape tests.
- `invoice-service`: 190 tests passing (was 186) — one extra invoice per
  split, storage-failure isolation, and re-scan dedup for a split document.
- Frontend: `tsc --noEmit` and `npm run build` both clean; Scanner page
  surfaces a toast when a photo splits into multiple invoices.

### Explicitly deferred
- Real-world threshold tuning. Every confidence/area/overlap threshold is
  a reasoned starting point verified against synthetic test images, not
  yet a real phone-camera photo. That validation pass is what the new
  `test-data/` folder exists for, once the real receipts are added there.

## 2026-09-01 — Saved Records redesigned as a categorized cashbook

Design spec: [`docs/superpowers/specs/2026-09-01-saved-records-cashbook-design.md`](docs/superpowers/specs/2026-09-01-saved-records-cashbook-design.md).
Not deployed — locally tested only, per this session's standing constraint.

### Added
- **`Invoice.category`** (Invoice Service) — a suggested-not-enforced
  cashbook category (Office Entertainment, Employee Care, Office Repair
  & Maintenance, Salaries & Wages, etc.), modeled directly on a real
  petty-cash cashbook the user provided as the reference. Nullable,
  free-text, same "suggestion list" convention as HR Service's
  departments/roles and Vendors Service's categories.
- **`GET /invoices/options`** — the suggested category list.
- **`GET /invoices/categories/summary`** — one SQL `GROUP BY` aggregate
  returning `{category, count, total}` per category, scoped to purchase
  invoices that are validated or sent to accounting. This is the single
  source every total shown on Saved Records and in the PDF report reads
  from — never a client-side re-sum of a paginated row list.
- **`GET /invoices/categories/report.pdf`** — a company-branded PDF per
  category (or every category, if none is given): an entries table with
  an accurate totals row, followed by a labeled appendix page per entry
  showing its actual receipt/invoice image (the original bytes if it's
  an image, a rendered first page via AI Engine if it's a PDF). A
  missing or corrupt source image is skipped, logged, and never fails
  the whole report — including a real bug caught by its own test:
  the image-decode step wasn't guarded, only the S3 fetch was, so a
  corrupt file would have crashed the entire PDF instead of just
  skipping that one entry's appendix page.
- **`/app/records` rebuilt as a split workspace**, replacing the old
  full-width, ungrouped, all-columns-editable grid: a compact,
  category-grouped list on the left (small thumbnail, particulars,
  date, amount per row; category headers show the accurate count/total
  and a Download-PDF button) and the selected record's full document
  preview plus an editable detail form on the right. Hovering a row's
  thumbnail shows an instant larger preview; clicking a row loads the
  full document into the right pane. Nothing selected shows a
  spend-by-category overview instead of a blank panel.
- Two new small frontend components (`components/records/`):
  `InvoiceThumbnail` (page-0 render, cached, Tooltip-based hover
  preview) and `InvoiceDocumentPreview` (image/PDF preview via the
  existing content-streaming endpoint) — a dedicated pair rather than
  reusing the Documents page's own `DocumentPreviewBody`, which resolves
  its fetcher through that page's own source registry; registering
  "invoice" there would have grown `/app/documents` an unwanted column.

### Changed
- Bulk multi-row inline editing (the old grid's "Save N changed rows")
  is gone — the new list+detail layout edits one selected record at a
  time. A deliberate trade-off for the cleaner split view, not an
  oversight (see the design spec §3).

### Verified
- `backend/services/invoice-service`: 158/158 tests passing (was 142) —
  new coverage for category persistence, the summary aggregate
  (multi-category, Uncategorized bucketing, excluding non-purchase/
  non-saved-status rows), and the PDF endpoint (real-PDF byte
  signature, the Uncategorized bucket, omitted-category "everything"
  report, and the corrupt-source-image degrade-gracefully path that
  caught the bug above).
- Frontend: `tsc --noEmit` and `npm run build` both clean.

## 2026-08-29 — Frontend audit, Invoice Generator branding, Analytics wired to real data

A full-site pass: every remaining page checked against `@/lib/data` dummy
imports, the architecture report's service table cross-checked against
what's actually built, and every backend service's migrations verified
present and consistent. Not deployed — locally tested only, per this
session's standing constraint.

### Added
- **Invoice Generator logo, placement, and four PDF/preview templates.**
  Settings Service's `Company` profile gained `logo_url` (widened to
  `Text` — it now holds a `data:` URI, not a hosted file; this service
  still has no object storage of its own), `logo_placement`
  (left/center/right), and `invoice_template`
  (classic/modern/midnight/minimal — one dark, three distinct light
  looks). The Invoice Generator page gained a logo uploader, a placement
  selector, and four clickable template thumbnails below the large
  preview — clicking one restyles the live preview immediately and
  persists the choice. `invoice-service`'s `sales_pdf.py` (ReportLab)
  reads the same three fields from Settings Service, best-effort, so the
  **downloaded PDF matches what the preview showed**, not just a
  decorative front-end mockup — a company's chosen look survives all the
  way to the real document, and an unreachable Settings Service still
  produces a correct, plain-classic PDF rather than failing the download.
- **`/app/analytics` runs on real data.** Every chart and KPI now reads
  from Transactions/Invoice/Vendors/HR/Procurement Service — the last
  page still importing from `@/lib/data` besides the two genuinely
  unbuilt ones (AI Assistant, the notifications dropdown — see Known
  gaps below). Two panels with no real backing data were replaced with
  real substitutes rather than faked: "Revenue Sources" (no
  sales-channel concept) → **Payroll by Department**; "Revenue Forecast"
  (no forecasting model exists) → **Procurement Pipeline**. The four KPI
  cards' month-over-month deltas are computed from the same trend data
  the charts already show, not invented.
- **Documents page toolbar** moved onto a `surface` card with a softer
  filled search field — the one page whose filter row sat directly on
  the page background instead of the card convention every other page
  uses; a small consistency fix, not a redesign.

### Verified — locally only (no Docker build, no deploy)
- **Frontend dummy-data audit**: grepped every route for `@/lib/data` —
  only `app.assistant.tsx` (AI Chat, never built) and `app-shell.tsx`'s
  notifications dropdown (no Notifications service/endpoint anywhere in
  the architecture report ever built) remain. Both are genuine, known
  gaps, not oversights.
- **Architecture/database audit**: every service in the architecture
  report's own table is built except Reports' original port (reassigned,
  not missing), the WhatsApp connector, and AI Engine's Chat/Insights
  halves. Every service has its alembic migrations present and
  consistent with its SQLAlchemy models (confirmed by each service's own
  test suite building that exact schema on SQLite). No port collisions
  across `docker-compose.yml`'s 30 services.
- **invoice-service**: 142/142 tests passing (was 130 — 12 new for the
  four templates × logo placements and the branding hand-off's
  degrade-on-failure behaviour).
- **settings-service**: 23/23 tests passing (was 17 — 6 new for the
  widened `logo_url` and the two new branding fields).
- Full backend re-run: **359 tests passing** across all 8 services
  (reports-service 40, settings-service 23, procurement-service 44,
  hr-service 25, transactions-service 37, invoice-service 142, gateway
  47, vendors-service 41).
- Frontend `tsc --noEmit` clean across the whole project.

### Known gaps (unchanged by this pass, flagged explicitly)
- **AI Chat Assistant and AI Insights** (architecture report §5.8, parts
  2-3) were never built — `/app/assistant` and the dashboard's insight
  cards still show illustrative content. AI Engine's OCR half is real;
  this is the part that isn't.
- **No Notifications service** — the header's notification dropdown has
  nothing real to read from anywhere in this codebase.
- **WhatsApp connector** remains a stub button, as in every prior phase.

---

## 2026-08-29 — Reports Service: P&L, Cash Flow, Tax Summary, Sales, Purchases, Balance Sheet

Full design in
[`docs/superpowers/specs/2026-08-29-reports-service-design.md`](docs/superpowers/specs/2026-08-29-reports-service-design.md).
Not deployed in this pass — locally tested only.

### Added
- **New service: `reports-service`**, reassigned to port 8014 — architecture
  report §5.9 originally named 8008, since taken by PaddleOCR. Two
  deliberate departures from the architecture text, both documented in
  full in the design spec: generation is **synchronous** (nothing here is
  OCR-slow, so no RabbitMQ/Celery job was built for it), and **Balance
  Sheet is a simplified cash-position snapshot**, not a true one — this
  codebase tracks no fixed assets, receivables/payables, loans, or equity
  to report on, and fabricating zero-value line items for those would be
  a false financial claim rather than an honest gap.
- **`/app/reports` runs on real data**: six report cards (Profit & Loss,
  Balance Sheet, Cash Flow, Tax Summary, Sales, Purchases), each with its
  own period inputs, a Generate button showing the first few real summary
  figures inline, and working PDF (ReportLab) / Excel (openpyxl)
  downloads that stay disabled until a report actually exists.
- **One generic report shape** (title, period label, summary lines, an
  optional detail table, notes) drives both renderers regardless of
  report type — a report type only needs to become this shape, not know
  how to lay out a page. Rendered **on demand** from a stored JSON
  payload at download time, not pre-rendered to a file — no MinIO/S3
  integration was needed for this service at all.
- **`GET /invoices/sales/summary`** gained optional `date_from`/`date_to`
  (filtered against the same resolved bucket-date the monthly chart
  already uses) and `customer_limit` (default 5, unchanged for existing
  callers); **`GET /invoices/vendor-spend`** gained the same date-range
  filtering. Both are backward-compatible — every pre-existing test for
  either endpoint still passes unchanged.

### Verified — locally only (no Docker build, no deploy)
- **40/40** new `reports-service` tests (computation correctness per
  report type, route-level creation/listing/tenancy/502-on-failure, and
  PDF/Excel byte-signature smoke tests).
- **130/130** `invoice-service` tests (was 125 — 5 new for the date-range
  additions).
- **47/47** `gateway` tests (was 45 — the new `/api/v1/reports` route).
- **17/17** `settings-service`, **44/44** `procurement-service`, **25/25**
  `hr-service`, **37/37** `transactions-service` — re-run as a sanity
  check, untouched by this phase.
- Frontend `tsc --noEmit` clean.

---

## 2026-08-29 — Settings Service: company profile, tax, AI automation

Full design in
[`docs/superpowers/specs/2026-08-29-settings-service-design.md`](docs/superpowers/specs/2026-08-29-settings-service-design.md).
Not deployed in this pass — locally tested only.

### Added
- **New service: `settings-service` (port 8009)** — Company profile, Tax
  configuration, and AI Automation toggles (architecture report §5.10),
  each a lazily-created, company-scoped singleton (no signup-time
  provisioning step needed from Auth Service — the first `GET` or `PUT`
  creates the row with sensible defaults, including tax's 18% GST).
- **`/app/settings`'s Company tab is real now.** The dashed-border "not
  built yet" placeholder — which has said since the Documents/Vendors
  phase that this would happen and that editing would stay disabled
  until it did — is replaced with two independently-saved forms:
  Business Details (NTN, address, city, industry, phone, contact email)
  and Tax Configuration (GST rate, withholding tax rate, filer status).
- **The AI Automation tab's five switches are real, persisted toggles**
  now — save immediately on flip, survive a reload. Each one's
  description states plainly whether it currently gates any real
  pipeline behaviour; most don't yet (see the design spec §3) — a stored,
  honestly-labelled preference beats either a fabricated effect or no
  toggle at all.

### Verified — locally only (no Docker build, no deploy)
- **17/17** new `settings-service` tests.
- **45/45** `gateway` tests (was 44 — the new `/api/v1/settings` route).
- **44/44** `procurement-service`, **25/25** `hr-service`, **37/37**
  `transactions-service`, **125/125** `invoice-service` — re-run as a
  sanity check, untouched by this phase.
- Frontend `tsc --noEmit` clean.

---

## 2026-08-29 — Procurement Service: requests, orders, vendor comparison

Full design in
[`docs/superpowers/specs/2026-08-29-procurement-service-design.md`](docs/superpowers/specs/2026-08-29-procurement-service-design.md).
Not deployed in this pass — locally tested only.

### Added
- **New service: `procurement-service` (port 8005)** — purchase requests
  (with approve/reject), purchase orders (creatable from an approved
  request or standalone, with a delivery-status lifecycle), and vendor
  quote comparison (architecture report §5.6).
- **`/app/procurement` runs on real data**: stat cards, a "New Request"
  dialog, inline approve/reject, a "New Order" dialog, an inline
  delivery-status selector per order, an "Add Quote" dialog, and inline
  quote scoring. Clicking a request now actually selects it
  (`?request=<id>`, same convention as Revenue Manager's `?invoice=<id>`)
  and drives both the timeline and vendor-comparison panels — the old
  dummy page implied that relationship without any way to act on it.
- **The request-lifecycle timeline is derived, not authored.** Four steps
  computed from the request's own status plus any linked purchase order's
  status — submitted, approval decision, order issued, delivered — so it
  can never say something different from what actually happened.
- **"Delayed" is a computed dashboard stat**, not a stored status: an
  order that's still `in_transit` past its `expected_delivery` date,
  checked fresh on every read. `PurchaseRequestStatus` correspondingly
  dropped "Delayed"/"Cancelled" from the architecture report's original
  four-value list — both describe a delivery problem, which only exists
  once an order does; see the design spec §3.
- **Vendor quotes are scoped to the specific request they're comparing
  options for**, not a company-wide unscoped list — and `score` is a
  human's own 0-100 judgement call, not a formula this codebase would
  have to defend as an algorithmic opinion.

### Verified — locally only (no Docker build, no deploy)
- **44/44** new `procurement-service` tests.
- **44/44** `gateway` tests (was 43 — the new `/api/v1/procurement` route).
- **25/25** `hr-service`, **37/37** `transactions-service`, **125/125**
  `invoice-service` — re-run as a sanity check, untouched by this phase.
- Frontend `tsc --noEmit` clean.

---

## 2026-08-29 — HR Service: employees and payroll processing

Full design in
[`docs/superpowers/specs/2026-08-29-hr-service-design.md`](docs/superpowers/specs/2026-08-29-hr-service-design.md).
Not deployed in this pass — locally tested only.

### Added
- **New service: `hr-service` (port 8004)** — employee CRUD and payroll
  processing (architecture report §5.5). `Employee` holds each person's
  *current* recurring salary/bonus/deductions; `PayrollRecord` is an
  immutable per-month snapshot of what was actually paid, so a raise this
  month can never quietly rewrite what last month's payslip said.
- **`/app/employees` runs on real data**: summary cards, an "Add Employee"
  dialog, a "Process Payroll" button, a searchable salary sheet, and the
  payroll-by-department chart, all real.
- **`POST /employees/payroll/process`** pays every active employee not
  already covered this calendar month — safely idempotent per employee,
  and correctly handles a new hire added mid-month as its own, separately
  booked batch rather than either double-paying anyone or dropping the
  new hire's pay from the books.
- **Processing payroll books a real expense.** `POST /expenses/book-from-
  payroll` (new, `transactions-service`) records an approved
  `category="Salaries"` Expense per processing batch. This means the
  Dashboard's existing "Employee Salaries" KPI — which the Transactions
  Service phase could only derive from the Expense ledger's "Salaries"
  category, since no payroll system existed yet — now reflects real
  payroll data with **zero changes to Transactions Service or the
  Dashboard**.

### Verified — locally only (no Docker build, no deploy)
- **25/25** new `hr-service` tests.
- **37/37** `transactions-service` tests (was 33 — 4 new for
  `book-from-payroll`).
- **43/43** `gateway` tests (was 42 — the new `/api/v1/employees` route).
- Frontend `tsc --noEmit` clean.

---

## 2026-08-29 — Transactions Service: expense ledger and real Dashboard KPIs

Full design in
[`docs/superpowers/specs/2026-08-29-transactions-service-design.md`](docs/superpowers/specs/2026-08-29-transactions-service-design.md)
— read its §1/§3 before assuming this matches the architecture report's
original Transactions Service scope; it deliberately doesn't (no Revenue
table). Not deployed in this pass — locally tested only, per explicit
instruction for this phase.

### Added
- **New service: `transactions-service` (port 8003)** — an Expense ledger
  (manual entries + an approve/reject workflow) and the Dashboard's
  KPI/trend endpoints. Revenue is read from Invoice Service's existing
  scanned-sales summary rather than duplicated into a second table here —
  same "derive, don't duplicate" reasoning `vendors-service` already
  applies to vendor spend.
- **`/app/expenses` runs on real data**: summary cards, category donut,
  the revenue-vs-expenses trend, a filterable/searchable ledger, an "Add
  Expense" dialog, and inline approve/reject on pending rows.
- **`/app` (Dashboard) runs on real data**: every KPI and chart now reads
  from Transactions Service, Invoice Service, or Vendors Service — nothing
  left reads the old `@/lib/data` dummy arrays. Two panels with no real
  backing data were replaced rather than faked: "Revenue Sources" (no
  sales-channel concept exists) became **Top Customers**; "Upcoming
  Payments" (no payables/due-date model exists) became **Pending
  Approvals**. KPI cards no longer show a vs-last-month delta arrow — no
  historical baseline is stored, and a fabricated percentage would be
  worse than none.
- **A purchase invoice's "Send to Accounting" now really does book an
  expense.** That endpoint's own docstring has promised this "once
  Transactions Service exists" since before the service existed — it now
  calls out (best-effort; see Known limitations) to create a matching,
  approved Expense record.
- **`GET /invoices/status-summary`** (`invoice-service`) — named in the
  architecture report's own endpoint list, never built until the
  Dashboard's Invoice Status chart needed it.
- **`SalesSummaryTotals.today_revenue`** (`invoice-service`) — additive
  field so the Dashboard can show day-level revenue out of what was
  otherwise a monthly-bucketed summary.

### Fixed
- **`app-shell.tsx`'s `SearchField`** was a purely decorative, uncontrolled
  input on every page that used it — no `value`/`onChange` existed at all.
  Extended with both as optional props (every existing call site is
  unaffected) so Expenses' vendor search could be wired without forking
  the component.
- **Gateway's `.env.example`** was missing `DOCUMENTS_SERVICE_URL` and
  `VENDORS_SERVICE_URL` even though both services have routed through it
  since their own phases — a real pre-existing gap, noticed while adding
  `TRANSACTIONS_SERVICE_URL` alongside them.

### Verified — locally only (no Docker build, no deploy)
- **33/33** new `transactions-service` tests.
- **125/125** `invoice-service` tests (includes 11 new tests for
  status-summary, the booking hook, and `today_revenue`).
- **42/42** `gateway` tests (routing-table updates for the two new prefixes).
- **41/41** `vendors-service` tests, run as a sanity check — untouched by
  this phase.
- Frontend `tsc --noEmit` clean across the whole project.
- **Noticed, not fixed here**: `slack-connector` and `email-connector` both
  currently have pre-existing, unrelated failures in their `test_sync_start*`
  suites (window-days/cursor-reset tests). Neither service's code was
  touched in this phase; flagged as a separate follow-up rather than
  silently left unexplained.

---

## 2026-08-27 — Revenue Manager: scan-to-revenue

Full design spec in
[`docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md`](docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md)
(§11 has the as-built implementation notes). Not live/Docker-verified in
this pass — see that section before relying on it in the running app.

### Added
- **`/app/revenue` runs on real scanned sales instead of dummy data.**
  Mirrors the AI Invoice Scanner exactly: the same four upload paths
  (Browse, WhatsApp toast-stub, Slack, Email), the same bounding-box
  document preview, an editable extracted-fields panel, a line-item table
  with arithmetic-check icons, a Scanned Sales list, and real KPI/chart
  data (Monthly Revenue, Top Customers) — built from `getSalesSummary()`
  rather than the page's old hardcoded arrays.
- **`POST /invoices/sales/scan`** (`invoice-service`) — the same AI
  Engine call and rules-based extraction the purchase Scanner uses,
  aimed at `type=sale`. Plus `GET .../scan/{job_id}`, `GET .../scanned`
  (excludes both purchase invoices and Invoice-Generator-authored sales),
  `GET .../summary` (monthly revenue, top 5 customers, pending-review
  count — aggregated in Python for the same SQLite-test-suite reason
  `unlinked_vendor_groups` already established).
- **This does not replace the Invoice Generator.** Authored sales
  (`POST /invoices/sales/`, typed by hand) and scanned sales
  (`POST /invoices/sales/scan`, uploaded) are two creation paths into the
  same `type=sale` row, discriminated by `extraction_source` (`"generated"`
  vs `"pdf_text"`/`"ocr"`) — deliberately one ledger, not a parallel table.
- **Slack and Email connectors can target either scanner.** `POST
  /files/{id}/send-to-scanner` gained an optional `target: "purchase" |
  "sale"` form field (default `"purchase"` — every existing caller is
  unaffected); the frontend sheets gained a matching `scanTarget` prop
  that also switches the button label and toast wording to "Send to
  Revenue".
- **`build_invoice()` gained an `invoice_type` parameter** (default
  purchase). On a sale, `customer_name` is sourced from the dedicated
  extracted field, falling back to `vendor_name` — the rules engine
  reports a sale's counterparty under that key regardless of which party
  it actually found, so the Customer field's confidence/bounding-box
  still read from `vendor_name` even though its editable value is
  `customer_name`.

### Fixed
- **A pre-existing gap, found while wiring this feature**:
  `frontend/src/lib/invoice-service.ts`'s `Invoice` type — the purchase
  Scanner's own type, reused here since the detail endpoint is already
  type-agnostic — was simply missing `customer_name`, despite the backend
  schema carrying it since the Invoice Generator phase. Nothing had read
  it from that type before. Fixed as a one-line addition, not a
  Revenue-Manager-specific type.
- **email-connector had zero test coverage for `scanner_bridge.py` or its
  `send-to-scanner` route at all** — confirmed by grep, not assumed.
  Since this work touches that exact function, full coverage was added
  rather than only the two new-behavior cases, mirroring slack-connector's
  own equivalents test-for-test.

### Verified — locally only (no Docker rebuild, no live stack)
- **699 tests pass** across the full backend sweep (was 653): +25
  `test_sales_scanner_route.py`, +5 `TestSaleInvoiceType` in
  `test_invoice_builder.py`, +5 slack-connector (2 bridge, 3 route), +11
  email-connector (8 bridge — a from-scratch file, 3 route).
- **Frontend `tsc --noEmit` clean** across the whole project, including
  the rewritten route, the new shared `sale-extraction-fields.tsx`, and
  both updated `*-files-sheet.tsx` components.
- **Not yet done**: an actual scan of a real sales document through the
  running stack, a real Slack/Email "Send to Revenue" round-trip, and
  confirming the charts populate from a live summary call. Flagged in the
  spec's §11.3, not silently skipped.

---

## 2026-08-26 — Documents Service, Vendors Service, Invoice Generator, Records grid

Four services' worth of work, plus two pre-existing bugs found by running things
rather than reading them. Full reasoning in
[`docs/documents-and-vendors-plan.md`](docs/documents-and-vendors-plan.md).

### Added
- **Vendors Service (8006)** — architecture report §5.7. `GET/POST /vendors`,
  `PUT /vendors/{id}`, `GET /vendors/{id}/invoices`, `GET /vendors/top`, plus
  `/vendors/options` for suggested categories and payment terms. Gives suppliers
  a stable identity so invoice matching stops relying on free-text OCR output.
  **`/app/vendors` now runs on it** instead of `data.ts` — search, status
  filter, and an Add Vendor dialog. The page honours `spend_unavailable`:
  spend reads "Unavailable" and the header total is omitted rather than
  summing fabricated zeros when Invoice Service is unreachable.
- **Documents Service (8013)** — the browser-upload document library: the fourth
  source on the Documents page, alongside Slack, Email and (planned) WhatsApp.
  Upload, list, preview, re-categorise, soft-delete, hand off to the scanner.
- **Invoice Generator (sales invoices)** — `POST/GET /invoices/sales/`,
  `GET /invoices/sales/{id}`, `GET /invoices/sales/{id}/pdf`. Completes
  architecture report §5.3's other half.
- **Bank/Cash payment method** on invoices, chosen manually after review — the
  rules engine never infers it.
- **`/app/invoices` now runs on the real Invoice Generator** instead of a
  local-state, print-only mock. Save computes authoritative totals server-side;
  the preview then switches to those numbers rather than the client's own
  arithmetic. PDF download follows the same authenticated-blob pattern as
  document previews, since a plain link to `/pdf` carries no bearer token.
  Editing a saved sales invoice isn't built here — the existing Records grid
  already edits any invoice by id, purchase or sale.
- **Saved Records is now an editable grid** — ten inline-editable columns,
  dirty-row highlighting, per-row Save/Revert plus "Save N changed rows".
- **`invoice.vendor_id`** and `GET /invoices/vendor-spend`, the aggregate that
  lets Vendors Service derive spend in one cross-service call rather than N+1.
- **Vendor Reconciliation** (`/app/vendor-reconciliation`, own nav item) —
  the accounting workflow that closes the loop the scanner opens: every scan
  writes a raw `vendor_name`, never a `vendor_id`. Real invoice data (not
  invented cases) surfaced what this needed to handle: `GET
  /invoices/vendor-groups` groups unlinked purchase invoices by their exact
  raw name — three invoices sharing one bad OCR string get resolved in one
  action, not three. `GET /vendors/reconciliation/queue` layers this
  company's own best-guess matches on top, scored by stdlib `difflib`
  (rules-based, no LLM, consistent with the OCR pipeline's own extraction
  rules) — never applied automatically, since a wrong auto-link silently
  misattributes spend to the wrong vendor. Three actions per raw name: link
  to an existing vendor (a suggestion or a searchable picker), create a new
  vendor and link in one step, or mark it **not a vendor** — real data
  included OCR fallback strings ("Receipt", "INVOICE Invoice #: …", a
  personal name) that aren't suppliers at all, so an "ignore" list
  (reversible — `GET/DELETE /vendors/reconciliation/ignored`) is part of the
  feature, not an afterthought. The queue itself is fatal (502), not
  degraded, on a downstream failure — same reasoning as
  `/vendors/{id}/invoices`: an empty result would tell the accountant
  "nothing left to reconcile," which is not a claim to make when the
  service simply couldn't ask.
- **`shared.auth.internal_service_headers`** — one implementation of
  service-to-service auth, replacing what would have been three copies.
- **Documents can now be tagged "Other" and deleted, for every source.**
  Slack and Email pull in everything shared in a channel or mailbox —
  university assignments, design files, bank statements — not just
  invoices; confirmed live (57 real email attachments, a handful actually
  financial). Browser uploads already had an `other` category and soft
  delete; Slack and Email had neither. Added the same soft-delete pattern
  (`deleted_at`, `DELETE /files/{id}`) to both, confirmed neither
  connector's upsert-on-resync touches that column, so a deleted file
  stays deleted rather than reappearing next sync. Also fixed a real
  conflation: browser-upload's explicit `other` was rendering under the
  same `"uncategorized"` label Slack/Email show when their rules engine
  simply couldn't guess — now a distinct `"Other"`, so "a person said
  this isn't financial" is never shown identically to "the classifier
  gave up." A new delete-confirmation dialog (previously: none, for any
  source — the Trash icon deleted immediately) shows a real rendered
  preview of the document, not just its filename, reusing the same
  preview engine as the full document viewer rather than a second copy
  of it. Full design reasoning in `docs/documents-and-vendors-plan.md` §4.

### Fixed
- **Every connector's "send to scanner" was broken in Docker.** They sent only
  `X-Company-ID`, but Invoice Service runs `TRUST_COMPANY_HEADER=false`
  (correctly — behind the Gateway that header comes from *verified* JWT claims).
  With no end-user token to forward, every hand-off got a flat **401**. Found by
  running it; the unit tests passed throughout because they patch the bridge.
  All three callers now mint a short-lived `role="service"` token.
- **Connectors' 30-second scanner timeout** cut off scans that would have
  succeeded — OCR takes *minutes* on a CPU-constrained host. Now a configurable
  240s.
- **`AI_ENGINE_TIMEOUT_SECONDS` 60 → 240** in Invoice Service, same reason:
  real uploads were failing outright with a 502.
- **Migration `20260825_01` crashed on a real run** —
  `UndefinedObjectError: type "invoice_payment_method" does not exist`.
  `sa.Enum` only auto-creates its PostgreSQL type as a side effect of
  `create_table`; a standalone `add_column` gets no such event. Every migration
  since creates its enum types explicitly.
- **Line-item extraction lost whole tables.** A real Pakistani invoice's
  "PAYMENT SUMMARY" has *"Total Booking Amount"* as its first data row, so a
  whole-row totals-keyword match put the boundary immediately after the header
  and discarded every row. A totals keyword inside the *description* column is
  now part of that item's description.
- **Negative amounts lost their sign** — `-1,000` parsed as `1000.0`, turning an
  advance already paid into an amount owed. Accounting-style `(1,000)` also
  handled.
- **Currency was detected but never shown.** Amounts and labels now read
  "Subtotal (PKR)" using the currency actually found on that document.
- **Bounding boxes only appeared while hovering**, so an untouched page looked
  empty. Every located region now draws faintly by default.
- **`InvoiceListItem.filename` was declared non-null**, so listing any sales
  invoice raised a `ValidationError`. Caught by tests before deployment.

### Changed
- **`GET /invoices/` now defaults to `type=purchase`** so generated sales
  invoices never appear in the Scanner's list, which is about documents that
  were *received*. `?type=all` returns both.
- **Scan-only columns are nullable** (`raw_extraction_json`, `filename`, `size`,
  `s3_key`, `file_hash`). A generated invoice has no source document. Sentinels
  were rejected: a fake `{}` extraction is indistinguishable from a real scan
  that found nothing, which Rule 8.1's audit trail depends on telling apart.
- **Status is editable from the Records grid, but only `validated ⇄
  sent_to_accounting`**, and only on an already-validated invoice. The grid is
  not a back door past the extraction lifecycle.
- **`/app/scanner-v2` removed** — its bounding-box overlay was ported into the
  main Scanner, so it had become a strict subset.
- **Architecture report's status note rewritten.** It still claimed only three
  services existed and that Invoice Service / AI Engine were "planned, not
  built". Also records that blueprint port 8008 (Reports) now collides with
  PaddleOCR's.

### Verified — live, against the running stack
- **Documents Service**: byte-exact round trip (3526 in / 3526 out); soft delete
  confirmed to hide the document *and* 404 it by id while the row survives in
  Postgres and the S3 object is untouched; a real invoice created through the
  scanner hand-off, recorded back on the document row.
- **Invoice Generator**: totals `2×9800 + 6×3200 = 38,800`, `+17% = 45,396`; a
  real 2,440-byte PDF starting with `%PDF-1.4`; list separation (11 purchase,
  1 sales).
- **Records status guard**: `200 / 200 / 409 / 422 / 200` across
  validated→sent, sent→validated, needs_review→sent (blocked), out-of-range
  status (rejected), and an ordinary edit on an unreviewed invoice (allowed).
- **Vendors cross-service derivation**: linking one invoice made spend appear as
  `110.67`, derived live from Invoice Service — never stored.
- **The degradation asymmetry, tested by actually stopping Invoice Service**:
  the vendor list still returns 200 but flags `spend_unavailable: true` rather
  than reporting a real vendor's spend as a confident `0.00`; the invoice proxy
  returns **502** rather than an empty list, because "no invoices" is a factual
  claim we cannot make when we could not ask.
- **Vendor Reconciliation, against this project's actual invoice data** (14
  purchase invoices, 13 unlinked, 7 distinct raw names): grouping correctly
  collapsed 3 invoices sharing one bad OCR string into a single group; ignoring
  "Receipt" and a billing-cycle fallback string removed them from the queue,
  restoring one brought it back; create-and-link on "ARGENTO NEUE" produced a
  real vendor with `invoice_count: 1` and live-derived spend; stopping Invoice
  Service turned the queue **502** rather than a false "nothing to reconcile".
- **Document soft-delete, against synthetic disposable rows** (not the
  account's real documents — neither connector has an undelete):
  create → visible → `DELETE` → `204` → `404` on every route that resolves
  through the scoped lookup → gone from the list, for both Slack and
  Email; test rows hard-deleted after. **The frontend delete flow against
  the account's actual 57 real email attachments**: Delete opened the
  confirmation with a working 445×354 image preview fetched via `blob:`
  URL, Cancel closed it with no request sent, and a real confirm on one of
  this session's own test uploads produced the `DELETE` call, a "Document
  removed" toast, and the row disappearing from the list.

**646 tests pass**: invoice-service 85, documents-service 12, vendors-service 41,
gateway 41, slack-connector 115, email-connector 177, invoice_extraction 149,
ocr 26 (+ 20 skipped). Frontend typechecks clean.

### Known issues
- Slack and Email connector suites also show failures from
  `ModuleNotFoundError: No module named 'celery'` — a gap in the local dev venv,
  unrelated to this work. Everything else in both suites passes.
- **Docker Desktop's build cache is unreliable on this machine.** `COPY`
  repeatedly reported `CACHED` despite changed files, twice shipping an image
  missing new code — once silently, where a `PUT` returned 200 and ignored the
  field instead of the intended 409. `--no-cache` is the workaround; verifying
  code is actually *inside* the image is the habit.
- ~~Vendor backfill is not done~~ — **now built** as Vendor Reconciliation,
  above. The real queue on this data (14 purchase invoices, 13 unlinked)
  turned up something worth recording: several raw `vendor_name` values were
  never vendors at all (scanner fallback strings, a personal name from a
  misread field), which is exactly why "ignore" had to be a first-class,
  reversible action rather than assuming every name needs a vendor record.

## 2026-08-20 — Email Connector Phase 2: real mailbox sync, 57 attachments imported live

### Added
- **Message sync** — `EmailMessage`, `EmailAttachment`, `EmailSyncJob` models; a Gmail
  client covering search-based backfill, `history.list` incremental sync, message detail,
  and attachment fetch; a Celery worker (`email-connector-worker`) mirroring Slack's.
- **`POST /sync/`, `GET /sync/{id}`, `GET /files/`, `GET /files/{id}/content`,
  `PATCH /files/{id}/category`** — same contract as the Slack connector's.
- **Attachment filtering** before anything is downloaded (size floor, extension
  allow/deny), and rule-based categorisation reusing Slack's `rules.yaml` unchanged.
- **Token refresh**, pulled forward from the plan's Phase 4 because a sync genuinely
  cannot run without it: Gmail access tokens last ~1 hour, so a long backfill would
  otherwise die mid-run. A failed refresh marks the account `needs_reauth` rather than
  retrying forever.

### Verified live — 85 messages scanned, 57 attachments discovered, 57 downloaded, 0 failed
Against the real connected mailbox, end to end: Gmail search → message detail → attachment
bytes → MinIO → categorised rows in Postgres (Invoices 6, Receipts 2, Reports 14,
Images 34, …). Token refresh also exercised for real — a later run shows the
`oauth2.googleapis.com/token` call succeeding mid-sync.

### Four real bugs this live run caught, none of which unit tests would have
Every one was found by running against a real mailbox, and each now has a regression test:

1. **`provider_attachment_id` was `VARCHAR(256)`.** Gmail's attachmentId routinely runs
   300-400+ characters. Every insert failed with `StringDataRightTruncationError`.
2. **A flush error poisoned the whole session.** The per-message `except` caught the error,
   but SQLAlchemy refuses every subsequent statement on that session until a rollback — so
   the *caller's* next commit raised `PendingRollbackError`, crashed the task, and left the
   job stuck showing "running" forever with no error ever recorded. Now rolls back and
   re-records, at both the per-message and per-commit level.
3. **Gmail's attachmentId was used as the S3 key prefix.** MinIO rejects any `/`-separated
   key segment over ~255 bytes (`XMinioInvalidObjectName`), so all 57 attachments failed to
   store. Keyed on our own row UUID instead.
4. **Gmail's attachmentId is *ephemeral*** — regenerated on every `messages.get` for the
   same unchanged attachment. It was the dedup key, so the second sync inserted a complete
   duplicate set: 114 rows for 57 real attachments, one file carrying 12 distinct ids across
   12 runs. Dedup now keys on the attachment's **position in the message's MIME tree**,
   which is stable because a received message is immutable. Migration `20260821_03` also
   cleans up the duplicates the broken key created.

107 tests pass.

### Not yet built
Sender allow/deny lists and the "needs review" tray (the plan's §5 highest-value filtering
work), the `send-to-scanner` hand-off to Invoice Service, and any frontend surfacing of
synced email attachments — the Documents page still shows Email as connected-but-empty.

---

## 2026-08-20 — Email Connector: real Gmail account connected end-to-end, Connected Apps UI

### Added
- **Connected Apps card for Email** (`frontend/src/components/email/connected-apps-card.tsx`),
  same shape as Slack's but deliberately smaller — connect/status/disconnect only, since
  message sync isn't built. Wired into Settings → Connected Apps alongside Slack's card.
- **`frontend/src/lib/email-connector.ts`** — the connect/status/disconnect client, and its
  own access-token getter wired into `AuthProvider` (mirrors slack-connector.ts's pattern
  rather than sharing a module, matching the backend's own per-connector isolation).
- Documents page's Email column now reads real connection status instead of showing "Not
  built yet". A connected-but-no-sync-capability source (no `fetchDocuments`) now renders an
  honest "connected — pulling documents in isn't built yet" state in `SourceColumn` rather
  than silently showing a permanent loading skeleton (a real gap `enabled: false` queries
  would otherwise leave: `isPending` never resolves to `false` if a query never runs).
- Sync picker's date window options changed to Last 24 hours / Last week / Last month / All
  time (was 3/6/12 months), per direct request.

### Verified — real Gmail account connected, live
The full OAuth round-trip now works end-to-end through the actual FinPilot web app: **Connect
Gmail → Google consent → redirected back connected**, using a real Google Cloud OAuth client
(the user created one; test-user access required one extra step in Google Cloud Console since
`gmail.readonly` is a restricted scope). Confirmed directly in the database afterwards:
`status=active`, correct `company_id`, all three requested scopes present, both
`access_token_encrypted` (420 chars) and `refresh_token_encrypted` (228 chars) non-empty, and
`token_expires_at` exactly one hour after `connected_at`. This closes the one gap the previous
entry flagged as untested.

One debugging note worth keeping: my own first few live-test attempts (scripting `/connect`
via `curl`, then separately opening the authorize URL) failed with "Invalid or expired OAuth
state" every time — not a product bug. The CSRF nonce cookie `/connect` sets has to land in
the *same browser* that later hits `/callback`; `curl` and a browser are different cookie
jars, so a script-minted URL can never complete this flow. The real fix was exactly this
UI: once the user's own logged-in browser called `/connect` itself, the cookie and the
callback request were naturally in the same place, and it worked on the first real attempt.

---

## 2026-08-20 — Email Connector Phase 1: Gmail OAuth foundation

### Added
- **Email Connector service (port 8011)**, built to the identical shape as the Slack
  connector: `EmailAccount` model, Fernet-encrypted token storage, `company_id` tenancy
  via the shared JWT lib, `connect` / `callback` / `status` / `account` (disconnect) routes.
- **Google OAuth 2.0** — `gmail.readonly` + `openid email` scopes, `access_type=offline` +
  `prompt=consent` forced on every authorization so a refresh token is always returned (see
  "new logic" below).
- **Gateway routing** for `/api/v1/email/*`, with `/callback` public (same CSRF-cookie
  pattern as Slack's) and every other path requiring a verified JWT.
- `docker-compose.yml`: `email-connector-db`, `email-connector`, and an
  `email-connector-files` MinIO bucket.
- Docs: `docs/email-connector-plan.md` promoted to active status with the agreed
  Gmail-first sequence; architecture report gets a status callout distinguishing what's
  actually built from the target design (RabbitMQ/Invoice Service/AI Engine are documented,
  not built — see report's new preface note).

### New logic with no Slack equivalent
Slack's bot token never expires; Gmail's access token does (~1 hour). Two behaviours this
introduces:
- The callback **refuses to store an account with no refresh token**, rather than let it
  silently die an hour after connecting — Google only omits one when
  `access_type=offline`/`prompt=consent` weren't both forced, so this should never trigger
  in practice, but failing loudly beats a mysteriously-broken connector.
- **Disconnecting hard-deletes the row** (tokens included) instead of flipping a status
  flag the way Slack's does — a mailbox holds more sensitive material than a Slack
  workspace, so `docs/email-connector-plan.md` §9 calls for deletion to actually delete.

### Verified
- 41 new tests (config, tenancy, JWT resolution, Gmail OAuth URL/exchange/refresh with
  `httpx.AsyncClient` mocked at the boundary, and route-level callback tests covering the
  missing-refresh-token guard, upsert-on-reconnect, and the cross-company-Google-account
  conflict). Gateway gains 4 more for the new route. Slack's 118 still pass — no regression.
- Live end-to-end through Docker: migration applied cleanly
  (`create email_account table`), `/health` responds, and `/api/v1/email/connect` /
  `/status` work correctly through the Gateway with a real minted JWT — an unauthenticated
  request 401s, the OAuth `/callback` path is reachable without one (400 for missing
  params, not 401), and `/connect` returns a well-formed Google consent URL with the
  correct scopes. The actual Google consent screen round-trip is untested — that needs a
  real Google Cloud OAuth client, which is a user action, not a code one.

### Not yet built (next phase)
Message sync, `historyId` incremental cursor, attachment discovery/download to MinIO,
categorisation, and the `scanner_bridge.py`-style hand-off to Invoice Service. Frontend
"Email — Coming Soon" stays as-is until then.

---

## 2026-08-19 — API Gateway: one front door, one place that verifies tokens

### Added
- **API Gateway on port 8000** (architecture §5.1/§19). Every browser request now
  enters here. It verifies the JWT, injects `X-User-ID` / `X-Company-ID` /
  `X-User-Role`, routes by path prefix, rate limits per IP and per user, and tags
  each request with a `trace_id` that is logged, forwarded upstream, and returned
  to the client.
- The frontend dev proxy now sends **all** of `/api/v1` to the Gateway rather
  than to individual services.

### The rule everything else depends on
Identity headers are **stripped from the incoming request and rewritten from
verified claims**. Downstream services trust `X-Company-ID` completely, so
forwarding a client-supplied one would let any caller read another company's
documents by adding a header. The strip is case-insensitive (HTTP headers are)
and applies to unauthenticated routes too, so nothing can be smuggled through
the open auth paths.

### Found while wiring it up
The Slack OAuth flow uses **browser navigations**, which cannot carry a bearer
token. Behind the Gateway both ends of the flow would have returned 401 and
connecting Slack would have become impossible. Two fixes:

- **`/slack/callback` is public at the Gateway.** Slack redirects the user's
  browser there, so no token can be attached. Its protection is the encrypted,
  10-minute-TTL `state` plus the httpOnly nonce cookie — which is what OAuth uses
  in place of a bearer token. Matched *exactly*, never by prefix, so
  `/callback/anything` stays protected.
- **`/slack/connect` now returns the consent URL as JSON** instead of a 302. The
  route is company-scoped and so needs the token; the frontend fetches it
  authenticated, then navigates. Redirecting would have forced the route public
  and lost the company scoping.

### Changed
- The connector runs with `TRUST_COMPANY_HEADER=false` behind the Gateway, and
  still verifies the forwarded token itself. Architecture §19 says only the
  Gateway verifies; verifying twice costs microseconds and removes a whole class
  of "a service got exposed by accident" bug, so this is a deliberate deviation.

### Verified
- 30 gateway tests — including that a client-supplied `X-Company-ID` is replaced
  by the verified one, in every capitalisation; that `set-cookie` and
  `content-disposition` survive the round trip (sessions and inline PDF preview
  depend on them); and that only the exact callback path is public.
- 120 tests across all four suites; frontend typecheck clean.

### Not yet verified
Docker Desktop was unresponsive, so the composed stack has not been run with the
Gateway in front. The routing, header and auth logic are covered by tests, but
signup → login → connect Slack over real HTTP through the Gateway is untested.

---

## 2026-08-19 — Accounts: Auth Service, login/signup, JWT-verified tenancy

Your team can now create real accounts, and Slack connections belong to a
company rather than to whoever happens to send a header.

### Added
- **Auth Service** on port 8001 (architecture §5.2) — signup, login, refresh,
  logout, `/me`. Own database (`auth_db`), Argon2id passwords, 15-minute access
  tokens, 7-day refresh tokens.
- **Shared library** `backend/libs/shared` (architecture §9) — JWT creation and
  verification in one place, so the Gateway and every service use identical
  logic instead of drifting copies.
- **`/login` and `/signup` pages**, an auth provider, a guard on `/app`, and a
  user menu with sign-out replacing the hardcoded "AK" avatar.

### Changed
- **The Slack connector now verifies a JWT** instead of trusting an
  `X-Company-ID` header anyone could set. Resolution order: verified token →
  header (only when `TRUST_COMPANY_HEADER=true`) → `DEFAULT_COMPANY_ID`. A
  malformed token is rejected rather than falling through to the dev path.
- Both dev fallbacks are **refused in production**: the service will not start
  with `APP_ENV=production` and `TRUST_COMPANY_HEADER=true`.
- Both service images now build from `backend/` so they can install
  `libs/shared`.

### Security decisions worth knowing
- **Refresh tokens rotate on every use.** Presenting an already-revoked token is
  the signature of a stolen session being replayed after the real user
  refreshed, so it revokes *every* session for that user, not just that request.
- **Refresh tokens are stored only as SHA-256 digests**, so a database leak
  yields no working sessions. Argon2 would add nothing — the values are ours and
  unguessable — while making every refresh needlessly slow.
- **Login failures are indistinguishable.** Unknown email and wrong password
  return identical text, and the unknown case still runs a password hash, so
  registered addresses cannot be enumerated by text *or* by timing.
- **The access token lives in memory, never localStorage.** A token in
  localStorage is readable by any script, turning one XSS bug into a stolen
  session that outlives the tab.
- **Rate limiting fails open** if Redis is down, and logs loudly: locking every
  user out of a financial platform is worse than a briefly unthrottled login.

### Fixed
- The SQLite test run caught a bug Postgres would have hidden: asyncpg returns
  timezone-aware datetimes while SQLite returns naive ones, so the refresh
  expiry comparison raised `TypeError` anywhere except production.

### Verified
- 18 auth tests, 17 JWT tests (including `alg=none`, tampered claims, wrong key,
  refresh-token-as-access-token), 55 connector tests, frontend typecheck clean.
- **Cross-service check with the real configured secrets:** a token minted by
  the Auth Service resolves to the correct company in the connector, and a
  caller adding an `X-Company-ID` header to override their own token is ignored.

### Still open
- **No Gateway yet.** Architecture §19 has the Gateway verify JWTs and inject
  headers for downstream services. Until it exists, each service verifies the
  token itself — a deliberate, documented deviation.
- Login and signup have not been exercised against a live database; Docker
  Desktop was down at the time. Backend logic is covered by tests.

---

## 2026-08-19 — Bulk actions, scanner bridge hardening

### Added
- **Multi-select on the Documents page.** Checkbox per document, select-all per
  column, and an action bar for sending several documents to the AI Scanner or
  recategorising them in one go.
- **Email connector build plan** at [`docs/email-connector-plan.md`](docs/email-connector-plan.md)
  — connection options compared (Gmail OAuth / Microsoft Graph / IMAP /
  forwarding address), the filtering problem, token refresh, privacy, effort
  estimates, and what to research first.

### Fixed
- **Unreachable Invoice Service produced an opaque 500.** `httpx.ConnectError`
  and timeouts were never caught in the scanner bridge. Since the Invoice
  Service isn't built yet, that is the path *every* send takes today — and bulk
  sending would have turned it into twenty opaque 500s. Now raises a clear error
  naming the URL that failed, and distinguishes a timeout (file still saved,
  safe to retry) from a refused connection.

### Notes on the approach
- Bulk requests run **one at a time**, not `Promise.all`. The connectors sit
  behind Slack's rate limiter, and firing twenty forwards at once is the reliable
  way to get throttled. Sequential also allows honest progress reporting and
  lets the batch continue past an individual failure.
- Partial results are reported as partial ("8 succeeded, 2 failed — names"), and
  only documents that actually succeeded are cleared from the selection, so
  failures stay selected and ready to retry.

### Verified
- Backend suite 45 passed; frontend typecheck clean.
- Each new test confirmed to fail when its guard is removed.

---

## 2026-08-19 — Documents page, inline preview

### Added
- **`/app/documents` page.** One column per connector (Slack, Email, WhatsApp),
  so every document FinPilot has collected is visible in a single view instead
  of being buried in Settings. Search and category filters apply across all
  columns. Added to the sidebar as **Documents**.
- **Inline document preview.** A Preview button on every file opens the document
  inside FinPilot instead of bouncing to Slack. PDFs render in the browser's own
  viewer (scroll, zoom, text selection, search), images render directly, and
  unsupported types offer a download rather than a dead pane. Available from both
  the Documents page and the Slack files sheet.
- **Source registry (`frontend/src/lib/documents.ts`).** A `UnifiedDocument`
  shape every connector maps into, plus a registry the Documents page renders
  from. Adding the Email connector later means writing one adapter and flipping
  its status — no page changes.

### Changed
- Email and WhatsApp columns are declared **planned** rather than hidden. They
  show what each will collect once built. No fake documents are invented for
  sources that do not exist yet.

### Notes on the approach
- Preview streams through the connector's own `/files/{id}/content` endpoint
  rather than a signed S3 URL, so previewing stays behind the company-scoping
  check and no storage URL is exposed to the browser.
- Each column owns its own queries, so one connector being down or slow does not
  block the others from rendering.

### Verified
- `tsc --noEmit` clean.
- `/files/{id}/content` confirmed by curl to return `HTTP 200`,
  `content-type: application/pdf`, `content-disposition: inline`, and real PDF
  bytes (`%PDF-1.4`, 46,226 bytes).

---

## 2026-08-19 — Three sync bugs found by running a real sync

All three passed unit tests, typecheck, and offline validation. They only
appeared when a real sync ran against a live Slack workspace.

### Fixed
1. **Sync counters never moved.** A sync that successfully stored 23 files to S3
   reported `files_downloaded: 0, files_failed: 0`. `_download_file` updated the
   `File` rows but held no `SyncJob` reference, and the `update_sync_job_stats`
   helper that would have corrected it was never called. Counters now increment
   in `_download_file`, matching how `files_discovered` already worked.
2. **Re-syncing duplicated every file.** `persist_file` always INSERTed, unlike
   the `get_or_create_*` helpers beside it. A second sync over 23 files left 46
   rows, and the browser listed every file twice. It now looks up by
   `(installation_id, slack_file_id)` and updates — while refusing to overwrite a
   category a person set by hand (`manual_override`).
3. **Sync errors were silently discarded.** `SyncJob.errors` was a plain `ARRAY`
   column, so SQLAlchemy never noticed `errors.append(...)` and dropped it at
   flush. A sync that hit `not_in_channel` and `channel_not_found` reported
   `errors: []`, leaving the user staring at "8/10 conversations" with no hint
   that the fix is to invite the bot to the channel. Now wrapped in
   `MutableList`.

### Changed
- Removed the dead `update_sync_job_stats` helper. It counted every `File` row
  for the installation — cumulative across all syncs — which contradicts the
  per-sync meaning of the other counters. Wiring it up would have made a second
  sync report the first sync's downloads.

### Verified
- Proven in one database: sync #2 (pre-fix) reported 0 downloaded and grew the
  table to 46 rows; sync #3 (post-fix) reported 23 downloaded and held at 23.
- Each new test was confirmed to **fail when its fix is reverted**, so the tests
  genuinely cover the bugs rather than merely passing.
- Full backend suite: 42 passed.

---

## 2026-08-19 — First live Slack connection

### Added
- Slack OAuth completed end to end against a real workspace. Workspace
  *Slack Connector Testing* connected with all 10 scopes, bot token encrypted at
  rest, and a real sync pulled **23 files (43 MiB)** into MinIO.

### Fixed
- **`TOKEN_ENCRYPTION_KEY` failed at runtime, not startup.** An unusable key let
  the service boot and only exploded on the first OAuth attempt with a 500. It
  now fails fast at startup with a message naming the exact command that
  generates a valid key.
- **`SLACK_REDIRECT_URI` still pointed at the old prototype** (`:8000/auth/slack/callback`).
  Slack sent the browser there after approval, producing
  `ERR_CONNECTION_REFUSED`. Corrected to `:8010/api/v1/slack/callback`.

### Changed
- `.gitignore` now ignores **all** `.env` variants, not just `.env`. A bare
  `.env` rule left `.env.local`, `.env.production`, and hand-made backups fully
  committable — hit while fixing the redirect URI, when a `.env.bak` holding real
  Slack credentials showed up as untracked rather than ignored.

### Known limitations
- 2 of 10 conversations are skipped: one private channel the bot was never
  invited to (`not_in_channel`) and one DM (`channel_not_found`). These are Slack
  permission boundaries, not defects — and thanks to the errors fix above, they
  are now reported instead of silently swallowed.

---

## 2026-08-18 — Repository restructure

### Changed
- **Frontend moved to `frontend/`.** The React app previously sat at the repo
  root; it now lives beside `backend/`, matching section 6 of the architecture
  report. All 93 files moved with `git mv`, preserving history. Configs use
  relative paths and moved together, so no config edits were needed.
  *Dev workflow: run `bun install` / `bun dev` from `frontend/` now.*

### Fixed
- **Removed 39 corrupted stray files** from `backend/`. Four had mangled names
  where path separators had been flattened
  (`appservicescategorization__init__.py` instead of
  `app/services/categorization/__init__.py`). All were untracked partial
  duplicates; the correct versions were already committed.

### Added
- Real `README.md` (replacing the original frontend-only design prompt), a
  service README, and API contract docs.

---

## 2026-08-18 — Slack connector service

The full connector, ported from a standalone prototype into this repo as
`backend/services/slack-connector` and reshaped for FinPilot's multi-tenant
model. Built as 18 reviewed tasks; every task was code-reviewed before landing.

### Added
- FastAPI service on port **8010**, all routes under `/api/v1/slack/*`.
- Slack OAuth (connect / callback / status / disconnect) with an encrypted,
  TTL-bounded `state` parameter carrying the company id.
- Conversation and file discovery, rule-based categorisation, S3/MinIO storage
  with SHA-256 verification, and a Celery worker for background syncs.
- **Multi-tenancy throughout**: every row carries `company_id` and every query is
  scoped by it.
- Send-to-Scanner bridge so an invoice found in Slack can be pushed into the
  existing OCR pipeline.
- Docker Compose stack: Postgres, Redis, MinIO, API, and worker.
- Connected Apps tab in Settings, plus a synced-files browser.

### Fixed during review
- **Raw S3 keys were being returned to the browser** in every file response,
  contradicting the design's signed-URL-only rule. Field removed from the
  response schema.
- Model/migration type mismatch that would have broken against real Postgres.
- `category_source` inconsistency (`manual` vs `manual_override`) carried over
  from the prototype.

### Security posture
- No Slack token, signing secret, or S3 key is ever logged, returned in a
  response, or placed in a browser-visible URL.
- Cross-tenant file access returns a generic 404 — it never reveals that a
  document belongs to another company.
- OAuth `state` is encrypted with a 10-minute TTL; the CSRF nonce is compared in
  constant time.

---

## Current state

| Area | Status |
|---|---|
| Slack connector | Working end to end against a live workspace |
| Documents page | Slack column live; Email and WhatsApp columns show planned state |
| Backend tests | 42 passing |
| Frontend typecheck | Clean |
| Email connector | Not started |
| WhatsApp connector | Not started |
| Auth service / API Gateway | Not started — connector uses an interim `X-Company-ID` header shim |

### Interim shims to remove later
- **`X-Company-ID` header** (`app/core/tenancy.py`) resolves the tenant, falling
  back to `DEFAULT_COMPANY_ID`, because there is no JWT-issuing Gateway yet. The
  header is not cryptographically verified. This is deliberate and documented —
  it must be replaced with verified JWT claims once the Auth Service exists.
