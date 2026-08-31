# Invoice OCR — Build Plan & Research Guide

**Status:** ✅ active — this replaces the OCR mechanism `FinPilot_AI_Backend_Architecture_Report
(3).md` §5.8/§11 originally described ("call OpenAI/Anthropic directly with the uploaded file").
That correction is the whole point of this doc — see §1 for why, §2 for what replaces it.
**Target:** the "Invoice OCR" responsibility already assigned to **AI Engine Service** (architecture
report §5.8, port **8007**) — this doc does not change *which* service owns OCR, only *how* it
extracts text before an LLM ever sees the document. See §5 for what actually gets built first.

---

## 1. Why the original "upload the file straight to a vision LLM" plan is being replaced

The architecture report's original flow (§11): Invoice Service uploads a file, AI Engine "calls
OpenAI/Anthropic with structured prompt" against that file directly, and trusts whatever JSON comes
back — confidence score included. That is a vision-LLM-as-OCR design, and it has real problems for
this specific use case:

- **Accuracy on dense tabular data.** Vision-capable LLMs are well documented to drop, merge, or
  invent rows and digits on invoice-style tables (multiple line items, tax breakdowns, totals) — the
  exact content this platform's numbers depend on. A hallucinated `9800` instead of `9080` in a
  vendor's per-unit rate does not fail loudly; it just books a wrong number.
- **No audit trail.** A vision call is opaque — there is no intermediate artifact to point at when a
  human asks "why did the scanner read this vendor name wrong?" other than "the model said so."
- **Cost and latency.** Every scan sends a full image (up to 20MB, per the existing spec) to a
  premium vision model, on every single invoice a company uploads. That is the expensive, slow path
  by construction, not the cheap one.
- **Provider lock-in for no reason.** Vision capability ties the whole scanner to a small set of
  models (GPT-4o-class, Claude-with-vision). Text extraction + a *text-only* LLM call for structuring
  works with any provider, including cheaper/faster/local models later, without touching the
  pipeline.
- **The self-reported confidence score is not trustworthy.** Asking the same model that might have
  hallucinated a number to also grade its own confidence in that number is circular. §3 below
  replaces this with something actually measurable.

None of this means "don't use an LLM" — an LLM is still the right tool for turning messy extracted
text into structured JSON (vendor name normalization, line-item parsing, inferring which number is
the tax rate vs. an item quantity). It means the LLM should work from **text that dedicated OCR
already extracted**, not from raw pixels.

## 2. The replacement: text extraction first, LLM structuring second

Two stages, not one:

```
file (PDF / image / WhatsApp screenshot)
        │
        ▼
┌───────────────────────┐
│  Stage 1: Extraction   │   deterministic, local, no API call, no cost per page
│  PyMuPDF + Tesseract   │
└───────────────────────┘
        │
        ▼
   plain text + positioned words (bounding boxes) + an OCR confidence score
        │
        ▼
┌───────────────────────┐
│  Stage 2: Structuring  │   NO LLM — deterministic rules: label-anchored field
│  rules engine, no AI   │   extraction, geometric table reconstruction, arithmetic
│  call at all           │   cross-validation. See §2a — this replaced the LLM design.
└───────────────────────┘
        │
        ▼
   ExtractedInvoiceData + a *combined* confidence score (§3)
        │
        ▼
   confidence-gated routing: auto-process / needs-review / needs-review (high priority)
```

**Correction, 2026-08-21**: Stage 2 was originally planned as a text-only LLM call. It is now a
**fully rules-based structuring engine — no LLM/AI call anywhere in this pipeline.** This is a
deliberate, researched decision, not a stopgap — see §2a for the reasoning, the source research, and
the honest accuracy ceiling this approach has (important: it is not 100%, and no rules-only system
in the research reviewed claims to be — see §2a's reality check before assuming otherwise).

### Stage 1 — text extraction

Routing decision, made per file:

| Input | Tool | Why |
|---|---|---|
| PDF with a real text layer (the common case for anything vendor-generated/emailed — this is also exactly what Email Connector already syncs in) | **PyMuPDF** (`fitz`), `page.get_text()` per page | Exact — this is not OCR at all, it's reading the text that's already in the file. Free, instant, no error rate. |
| PDF that is actually a scanned image with no text layer (a photocopy someone scanned to PDF) | **PyMuPDF to render pages → Tesseract per page** | `page.get_pixmap(dpi=300)` rasterizes each page, then it's treated exactly like the image case below. |
| Image (JPG/PNG/WEBP — including WhatsApp screenshots and phone photos of a paper receipt) | **Tesseract** (via `pytesseract`), after light preprocessing | Phone-camera photos are the messiest input this platform accepts (skew, glare, low light) — preprocessing before OCR matters more here than for a clean digital PDF. |

**Detecting "this PDF has no real text layer"**: after `page.get_text()`, check extracted character
count relative to page area / a flat minimum-characters threshold. A page with a real text layer
returns hundreds of characters; a scanned image rendered to PDF returns ~0. This is a cheap,
reliable heuristic — no need for anything fancier.

**Image preprocessing before Tesseract** (Pillow to start; add OpenCV only if real test documents
show it's needed — see §6's open question): grayscale conversion, contrast/threshold normalization,
and deskew. Tesseract's accuracy drops sharply on tilted, low-contrast, or noisy input, and "photo of
a receipt sent over WhatsApp" is exactly that kind of input in practice, unlike a clean vendor PDF.

**Tesseract itself**: the open-source OCR engine (`tesseract-ocr/tesseract` on GitHub), driven from
Python via the `pytesseract` wrapper (`madmaze/pytesseract`) — both are well-established, no need to
source a different repo. The one operational detail worth flagging clearly: `pytesseract` is a thin
wrapper that shells out to the **system** `tesseract` binary — it does not bundle the OCR engine
itself. Whatever container runs this needs `apt-get install -y tesseract-ocr tesseract-ocr-eng` (or
the equivalent) in its Dockerfile, or every OCR call fails at runtime with "tesseract is not
installed" despite `pip install` having succeeded cleanly. Easy to miss, guaranteed to be hit once if
it isn't documented — so it's documented here.

### Stage 2 — structured extraction: rules-based, no LLM

## 2a. Why rules instead of an LLM, and the honest accuracy ceiling this has

Full research and the complete rule set live in **`docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md`**
(compiled from `docs/research/Enterprise_Knowledge_Intelligence_Research.md`,
`docs/research/AI_Document_Intelligence_Research.md`, plus targeted web research on production
rules-based invoice parsing). This section is the short version; that doc is the actual reference —
read it before touching `backend/libs/invoice_extraction/`'s code.

**Reality check, stated plainly because it matters for what "done" means here**: across every source
reviewed, a well-built rules-based extractor (label-anchored regex + positional/geometric heuristics)
reliably handles **~70-80% of invoices cleanly**. The remaining 20-30% — new vendor layouts, wrapped
line-item descriptions, multi-page tables, merged cells — is where pure rules structurally break, and
two independent research sources agree the failure concentrates in **line items specifically**, not
header fields (vendor, invoice number, date, total are comparatively tractable with rules).

**What this means concretely, and why it is not a contradiction of wanting this done "properly" or
"professionally"**: no serious rules-based invoice tool (the research reviewed Rossum, Reducto's
schema mode, invoice2data-style template systems) claims 100% from rules alone. Every one of them
ships the same pattern instead — **rules extract, a composite confidence score identifies what it's
unsure about, low-confidence fields route to a human review queue, and the human's correction is the
actual source of truth for anything the rules couldn't get right.** That queue is this project's
existing `EmailAttachment`/`Invoice` "Needs Review" status, not new UX to invent. High accuracy on
*booked accounting numbers* comes from this rules+review combination, not from rules being clever
enough to never need a human — treating that as the target (rather than "97% auto-processed cleanly,
3% correctly caught and routed for a 10-second human confirmation") would mean either overclaiming
accuracy or silently shipping wrong numbers into someone's books. Neither is acceptable for a
financial product, so the review queue is treated as core to "properly implemented," not a fallback
that means the rules failed.

**The engine, in one sentence**: label-anchored extraction with per-field label-variant dictionaries
for header fields (vendor, invoice number, date, NTN, totals), geometric row/column reconstruction
(ruling-line detection first, gap-based clustering with header-keyword anchoring as fallback) for
line-item tables, arithmetic cross-validation (`qty × rate ≈ amount`, `subtotal + tax ≈ total`) as
the cheapest and strongest confidence signal available, and a composite per-field-then-per-document
confidence score that drives auto-process / needs-review / needs-review-high-priority routing. Full
rule numbering (1.1 through 8.4) is in the research doc — this plan doesn't duplicate it.

**Where the extraction engine lives**: `backend/libs/invoice_extraction/` — a second domain library
alongside `backend/libs/ocr/` (Stage 1) rather than folded into it, since "read the text" and
"understand what the text means" are genuinely different concerns with different failure modes and
different test strategies (Stage 1's tests are "was this text read correctly"; Stage 2's are "was
this field assigned correctly, and did we correctly flag it when we weren't sure").

**Kept for later, not abandoned**: the plain-text output from Stage 1 that this pipeline no longer
sends to an LLM for structuring is still exactly the right input for a future RAG-based chat feature
(the AI Engine's separate "AI Chat Assistant" responsibility, architecture report §5.8) — nothing
about dropping the LLM from *this* pipeline forecloses using one elsewhere for a different job.

## 3. Confidence scoring — fully rules-based (research report §6)

Replaces the original single self-reported LLM number with a **composite, computed score** built
from signals that are each independently checkable, not asserted:

1. **OCR-source confidence** — near-maximum for a native PDF text layer (PyMuPDF's extraction is a
   deterministic parse, not probabilistic recognition — the *characters* are correct by construction;
   this is a separate dimension from whether they were assigned to the right *field*). Tesseract's own
   per-word confidence (from `image_to_data`, not `image_to_string`) aggregated per field for anything
   OCR'd — minimum-across-words for critical numeric fields, since one misread digit in a total is
   enough to make the whole field wrong.
2. **Extraction-method confidence** — a field found via a strong label anchor plus a passed
   value-pattern validation scores higher than one found via a positional fallback guess (e.g. "the
   largest number on the page" as a last-resort total). Which specific rule matched is tracked per
   field, not discarded.
3. **Arithmetic consistency** — zero-cost, deterministic: does `sum(line_item.qty × rate)` agree with
   the stated subtotal? Does `subtotal + tax ≈ total`? Is the implied tax rate a plausible one for
   Pakistan's sales tax/GST rather than an arbitrary computed value? A mismatch is a strong signal
   something was misread — no model call needed to know that, and it is confirmed in the research as
   one of the two or three most important validation signals in every serious system reviewed.

These combine into per-field confidence first, then a weighted document-level score (critical fields
— total, tax, vendor, invoice number — weigh more than a payment-terms note), which drives the
routing described in §2a. Research report §6.1-6.4 has the full weighting/tiering rules.

## 4. Libraries and system dependencies

| Package | Role | Notes |
|---|---|---|
| `pymupdf` (import name `fitz`) | PDF text extraction + page rasterization | Pure wheel, no system dependency, fast. |
| `pytesseract` | Python wrapper around the `tesseract` CLI | **Requires the system `tesseract-ocr` binary** — add to the Dockerfile, not just `requirements`/`pyproject`. |
| `Pillow` | Image preprocessing (grayscale, contrast, deskew) | Already a common transitive dependency across this codebase's services. |
| `opencv-python-headless` | *Maybe* — more robust deskew/denoise | Only add if Pillow-level preprocessing proves insufficient against real sample documents (§6). Don't pull in a heavier dependency speculatively. |

No AI/LLM SDK (`openai`, `anthropic`, etc.) is a dependency of this pipeline at all — deliberately,
per §2a. If a future RAG chat feature needs one, that is a separate library with its own dependency
list, not this one.

## 5. What actually gets built first

Per the same "prove it works in isolation before wiring a whole service around it" discipline this
project has used for every other phase (see docs/email-connector-plan.md's live-verification
history — every real bug found there was found by running the thing, not by review alone):

**Phase 1** ✅ built: `backend/libs/ocr/` — a standalone, unit-tested **OCR extraction module** (Stage
1: file in, plain text + *positioned words* (bounding boxes, needed by Phase 2a's table
reconstruction — Rule 4.2) + confidence out). No HTTP service, no queue, no AI call — a pure library,
mirroring `backend/libs/shared`'s own shape, deliberately kept out of `shared` since this is domain
logic (invoice documents), not cross-cutting infrastructure. `extract_text(content, filename,
mimetype) -> ExtractionResult` is the entire public surface (`ocr/__init__.py`).

Genuinely verified, not just written: the native-PDF-text path (PyMuPDF) is fully tested with real
generated PDFs *and* with real sample invoices. The Tesseract-OCR path (scanned PDFs, images) is
implemented and unit tested the same way, but those specific tests skip cleanly on this machine — the
`tesseract` system binary isn't installed here and installing it needs admin rights this environment
doesn't have (`choco install tesseract` failed on a permissions error writing to
`C:\ProgramData\chocolatey`). Two ways to close that gap: run `choco install tesseract -y` yourself
from an elevated PowerShell, or just proceed — the eventual AI Engine Dockerfile installs
`tesseract-ocr` via `apt-get` the same way this plan's §4 already flags as required, and the OCR-path
tests will run for real the moment that container builds, no code changes needed either way.

**Phase 2a** ✅ built: `backend/libs/invoice_extraction/` — the rules-based structuring engine (§2a),
as its own standalone library (label dictionaries, value validators, label-anchored field extraction,
geometric line-item table reconstruction, arithmetic validation, composite confidence scoring, dedup
helpers), tested the same "prove it in isolation" way as every other phase — 77 tests, including
against two real sample invoices, not just synthetic ones.

**What live-testing against real documents actually found, honestly**: two genuine bugs, both fixed
and now regression-tested (`tests/test_extract_invoice_real_documents.py`) —

1. A real invoice's "Bill From" / "Invoice No." / "Currency" header fields land on *one visual row*
   once grouped by y-position, because the document uses a multi-column layout. The first version of
   label-anchored extraction took the whole same-line remainder as the value, so vendor-name
   extraction returned `"INVOICE NO. CURRENCY"` — a different column's label text — instead of the
   actual vendor name. Fixed by matching at word level with x-position awareness, truncating a
   same-line/next-line value at the next recognized label or a bare header noun, rather than treating
   a joined text string as one opaque blob.
2. A real invoice's "Total" label and its "$100.00" value sit ~124pt apart on the same row (a
   right-aligned totals column) — a naive "reject same-line values past a small gap" heuristic (the
   first fix attempt) rejected this as "too far to be the value" and returned nothing. Fixed by
   dropping gap-distance as the same-line acceptance signal entirely and using label/non-value-token
   boundaries instead (see fields.py's module docstring for the full reasoning on why gap distance
   works for the *next-line* case but not the *same-line* one).

**What still doesn't extract well, kept as an honest example rather than papered over**: a Shopify
subscription billing statement (a real downloaded sample, not invented) has no explicit "vendor" or
"invoice number" label at all in the traditional sense — its closest analog, `"Bill #565151287"`, is
tokenized by PyMuPDF as one glued word with no space, which the label matcher doesn't currently
recover a value from. The system's response to this is *correct*, not broken: `total`/`subtotal`
(which do have clear labels) are still extracted correctly, and the missing critical fields correctly
route the document to `needs_review_high_priority` rather than guessing. This is kept as a committed
test fixture specifically because it demonstrates the research report's own stated ceiling
(§0 — "the remaining 20-30%... is where pure rules break") on a real document, not a synthetic
worst-case invented to look rigorous.

No HTTP service, no queue yet — same "prove it in isolation" reasoning as Phase 1.

**Phase 2b** ✅ built and live-verified: `backend/services/ai-engine/` (port 8007) — `POST
/api/v1/ai/ocr/extract`, a multipart file upload wired straight to Phase 1 (`ocr.extract_text`) then
Phase 2a (`invoice_extraction.extract_invoice`), returning the full `ExtractedInvoiceResponse` JSON.
Deliberately not RabbitMQ yet — this service has nothing to consume from until Invoice Service
(Phase 3) exists (architecture report §11b's own note that Invoice Service/AI Engine/RabbitMQ are
target-state, not built), and it needs no database either: it is fully stateless, no persistence, no
company/tenancy context, matching Phase 1/2a's own "prove it in isolation" scope. Not registered
through the Gateway — an internal endpoint other backend services call directly, the same pattern
email-connector/slack-connector already use for `INVOICE_SERVICE_URL`.

Live-verified against the real running container (not just the local venv), both extraction paths:
the real `tax_invoice_milestone.pdf` fixture through the native-PDF-text path (identical, correct
result to local testing — vendor `"muhammad haroon"`, total `100.0`), and — for the first time in
this whole build, since Tesseract couldn't be installed on the Windows dev host without admin rights
— a synthetic scanned-receipt image through the **real Tesseract OCR path**, installed via `apt-get`
in the Dockerfile exactly as §4 called for. It worked cleanly: vendor, invoice number, date, and
total all extracted correctly with 0.95+ per-field confidence, `document_confidence` 0.98,
`review_status: "auto_processed"`. This is the first genuine end-to-end confirmation that the
OCR-path code (unit-tested with skips locally, never actually run) works for real.

**Phase 3** ✅ built and live-verified: `backend/services/invoice-service/` (port 8002) — the AI
Invoice Scanner (`POST /invoices/scan`, `GET /invoices/scan/{job_id}`) and Invoice CRUD (list/filter,
detail, correct, validate, send-to-accounting, stream the original file) from architecture report
§5.3, wired to Phase 2b's AI Engine over plain HTTP. 34 backend tests + 4 new Gateway routing tests,
all passing, plus a real end-to-end run through the actual Docker stack:

- `POST /api/v1/invoices/scan` through the **Gateway** (JWT verified, `X-Company-ID` injected) →
  Invoice Service → AI Engine → back, using the same `tax_invoice_milestone.pdf` real sample from
  Phase 2a — extracted vendor/date/total correctly, `needs_review_high_priority` (invoice number
  wasn't found, matching the exact known result from Phase 2a's own live testing of this document).
- **A human correction** (`PUT /invoices/{id}` setting the invoice number the rules engine missed)
  saved correctly, and the invoice's `raw_extraction_json` still shows the original
  `invoice_number: null` afterward — confirmed directly in Postgres, not just asserted in a test —
  which is Rule 8.1's audit-trail requirement working for real, not just on paper.
- **Rule 7.1 dedup, live**: re-uploading the identical file returned the same invoice (now showing
  the correction and its updated status) rather than creating a duplicate — confirmed via `select
  count(*) from invoice` staying at 1 while a second `ai_job` row was still recorded for the repeat
  scan attempt.
- The full status lifecycle (`needs_review_high_priority` → `validated` → `sent_to_accounting`) and
  its guard (send-to-accounting refuses a still-`needs_review` invoice with a 409) both verified live,
  along with streaming the original file back byte-for-byte (107,657 bytes both ways).

Not yet built: the Invoice Generator (architecture report §5.3's *sales*-invoice half — a separate
feature, generating new invoices rather than processing uploaded ones).

### Phase 3 frontend — the Scanner UI, wired for real

`frontend/src/routes/app.scanner.tsx` was a fully static mock (hardcoded `extractedInvoice`/
`recentInvoices` from `lib/data.ts`, every button a `toast.success(...)` stub) before this session —
now every part of it is wired to the real pipeline via `frontend/src/lib/invoice-service.ts`:

- Upload (drag-and-drop or file picker) → `POST /invoices/scan` → the result is selected automatically
- A real, status-filterable list (`GET /invoices`) replaces the old static "Upload History" table
- Selecting a row loads the real extracted fields into an editable review form; **Save** (`PUT`),
  **Validate** (`POST /validate`), and **Send to Accounting** (`POST /send-to-accounting`) all call
  the real endpoints and update the UI from the real response, not local-only state
- The original uploaded file renders next to the extracted fields (`GET /invoices/{id}/content`) —
  Rule 8.2's "show exactly what was extracted from where," at the document level

**Per-field confidence, closing a gap found during live use**: the API originally exposed only one
document-level `document_confidence` number — real per-field confidence (computed by the rules
engine, Rule 6, and already stored in `raw_extraction_json`) never reached the UI, so there was no way
to tell "the AI filled this in and is sure" apart from "this is blank because nothing was found."
Fixed by adding `Invoice.field_confidence` (a model property deriving a flat `{field: confidence}`
map from `raw_extraction_json`, excluding any field whose value is `None` — a field the engine never
found gets no entry at all, not a fake `0%`) and exposing it on `InvoiceResponse`. The Scanner UI now
shows a confidence badge (`Sparkles` icon + percentage, color-coded) next to every auto-filled header
field, "Not found — fill manually" next to one the engine didn't find, and a pass/fail icon per line
item from `arithmetic_check` (Rule 5.1). This is the whole point made concrete: a human should be able
to look at the form and immediately tell which values the system is confident about versus which need
their own eyes — not just "some fields have values."

Live-verified the same way as the backend: reloaded the real running app, selected the real scanned
invoice, and confirmed via DOM inspection that `field_confidence` (`{"vendor_name": 1.0,
"invoice_date": 1.0, "total": 1.0}` for this real document — `invoice_number`/`ntn`/`subtotal`/
`tax_rate`/`tax_amount` correctly absent) renders as exactly the right badges in exactly the right
places, including that a field corrected by a human *after* the scan (invoice_number) still correctly
shows "Not found — fill manually," since `field_confidence` reflects what the engine originally found,
never what a human typed in afterward.

### Phase 4 — Grounded review UI (a second, additive Scanner experience)

**Where this came from.** A separate local project, `INVOICE_DOCUMENT_AI_TESTING`, was built purely
to experiment with and validate document-AI/OCR techniques outside the official codebase. Its own
implementation report recommended porting one specific, proven pattern from it: a document preview
with a live bounding-box overlay next to the extracted form, so a reviewer can see exactly *where on
the document* each field came from, not just its value. Everything else about that testing project —
its Gemini/OpenAI "AI Understanding" stage, SQLite persistence, local-disk storage, its own FastAPI
service shape, its canonical/dynamic field split, its own frontend — was explicitly **not** ported.
The label-anchored pipeline already built in Phases 1-3 is already more precise and deterministic
than that project's regex/LLM-based field extraction, and porting an LLM stage into this pipeline
would directly contradict §2a's "no LLM in this pipeline" decision. So Phase 4 is narrow: reuse
everything already built, add only what was structurally missing — page dimensions and a
rendered-page image — to make bounding-box grounding possible, and ship it as a **second, isolated
Scanner route** (`/app/scanner-v2`, navigation label "Invoice Scanner (Preview)") rather than
touching the existing one.

**What was actually missing.** `invoice_extraction/fields.py` was *already* attaching `page`/`bbox` to
every header field it found (confirmed by reading the code, not assumed) — that data just never left
`raw_extraction_json` to reach the API or UI. The one genuine gap was **page dimensions**: nothing in
the pipeline recorded a page's own width/height, which a bounding-box overlay needs to scale a
PDF-point or OCR-pixel bbox onto a rendered preview image of arbitrary size. That, plus "no way to
render a page as an image at all" (the existing `GET /invoices/{id}/content` route only streams the
raw original file), were the two real additions this phase made:

- `ocr.ExtractionResult.page_dimensions: list[tuple[float, float]]` — one `(width, height)` per page,
  in the *same native coordinate space* every bbox on that page already uses: PDF points
  (`page.rect.width/height`) for a `pdf_text` page, rasterized pixel dimensions for an `ocr` page.
  Populated in `ocr/pdf.py` (both branches) and `ocr/extract.py`'s direct-image branch; carried
  through `invoice_extraction.ExtractedInvoice.page_dimensions` (pure passthrough,
  `extract_invoice.py`) and `ai-engine`'s `ExtractedInvoiceResponse`.
- `ocr/render.py`'s `render_page_png()` — rasterizes one page to PNG, reusing `pdf.py`'s exact same
  `_RENDER_DPI` rasterization call for a PDF, or passing an image upload straight through. Exposed as
  a new stateless `POST /api/v1/ai/ocr/render-page` on AI Engine (same file/router as `/extract`, same
  error-handling shape), proxied by a new `GET /invoices/{id}/page/{page_number}` on Invoice Service
  (fetches the original bytes from S3, forwards to AI Engine, streams the PNG back — the same
  call-out-rather-than-embed-OCR pattern the scan route already uses).
- `Invoice.field_locations` and `Invoice.page_dimensions` — two new read-only `@property`s on the
  model, exposed on `InvoiceResponse`, built exactly like the existing `field_confidence` property:
  derived from `raw_extraction_json`, never the raw blob itself (Phase 7's "don't expose internal
  pipeline JSON to the normal user" rule), and a field with no location data simply has no entry
  rather than a fabricated one. **No DB migration** — both are computed from the existing JSON column.

**Why this needed no LLM, no new service, no new database.** The rules engine's own field extraction
was already correct and already carried bbox/page data; Phase 4 only had to *surface* it and add the
missing page-size/render capability. Tesseract was already properly Dockerized (`ai-engine`'s
Dockerfile installs it via `apt-get`) — no new system dependency either.

**The frontend.** `frontend/src/components/invoices/bounding-box-overlay.tsx` fetches one rendered
page and draws a single highlighted box for whichever field is currently hovered/focused in the form,
scaled live from the `<img>`'s own rendered size against `page_dimensions` — the same
resolution-independent technique the reference project's report documented (native bbox + native page
size + client-measured container size, no DPI bookkeeping needed downstream). `frontend/src/routes/
app.scanner-v2.tsx` is a full parallel route reusing every hook, mutation, and form-field convention
from `app.scanner.tsx` verbatim (upload, list, Save/Validate/Send-to-Accounting) — the only structural
difference is the left panel and the hover-to-locate wiring on each header field. An invoice scanned
*before* this phase shipped has `page_dimensions == []` (its `raw_extraction_json` predates the field)
and the v2 UI shows a plain "grounded preview isn't available — re-scan to enable it" message instead
of attempting to render a scale-less overlay, rather than erroring.

**A real bug found and fixed during live verification, not just written and trusted**: the first
version of `BoundingBoxOverlay` rendered its `<img>` with `object-contain` inside a fixed `aspect-[4/3]`
box. For any page whose real aspect ratio isn't 4:3 (every invoice, since they're portrait-oriented),
`object-contain` letterboxes the image — the `<img>` element's own `clientWidth`/`clientHeight` stayed
at the full box size while the *visible* page content only occupied a narrower centered region inside
it. The scaling math (correctly written) was scaling against the wrong reference size, so every box's
Y-axis silently computed to `top: 0px; height: 0px` while X stayed correct (the box's width happened
to match the box's own width, not the image's). Live DOM inspection against the real API response
(exact known bbox `[59.53, 146.86, 156.40, 158.27]` on a `[595.28, 841.89]`-point page) caught this —
the fix removes the fixed-aspect box entirely and lets the `<img>` render at its natural aspect ratio,
so its client size *is* the actual content size the scaling math assumes. Confirmed correct afterward
by hovering multiple fields and checking the rendered box's inline style against hand-computed
expected pixel offsets (e.g. vendor name: expected `top≈102.1, height≈7.93` → observed
`top: 102.051px, height: 7.92647px`).

**Verification status.** Live-verified end-to-end against the real running Docker stack for the
native-PDF-text path (`tax_invoice_milestone.pdf`): scan → correct `page_dimensions`/`field_locations`
in the API response → correct box position for multiple fields, pixel-checked against hand-computed
expected values as above → existing `/app/scanner` route regression-checked as unaffected. The
Tesseract/OCR path was live-scanned successfully (a synthetic scanned-style image, 95-98% per-field
confidence, fields populated correctly) but the box-alignment check on that specific path was
interrupted before completion; it is **verified by code inspection instead** for now: `ocr/image.py`'s
`_preprocess()` (grayscale + threshold binarization) never resizes the image, so Tesseract's returned
word coordinates are in the exact same pixel space as the `image.width/height` recorded as
`page_dimensions`, which is in turn the exact same pixel space `render_page_png()` produces (identical
rasterization call, identical `_RENDER_DPI`, for both a rasterized-PDF page and a direct image upload)
— there is no point in the pipeline where these three could diverge. A live re-confirmation of this
specific path (plus a multi-page document's page-switcher) is still worth doing against the real
Docker stack before treating Phase 4 as fully closed.

### Phase 5 — Receipt/bill robustness (treating OCR as evidence, not truth)

**Where this came from.** Live-testing the deployed Scanner against 4 real
photographed local-shop receipts/bills (not clean company invoices) surfaced
poor results. Traced all 4 through the actual pipeline locally before
changing anything (Tesseract installed via winget, no Docker needed) — the
real root cause was upstream of extraction entirely:

- `ocr/image.py`'s `_preprocess()` — a hard global threshold at pixel value
  180 — produced **zero OCR words on 3 of 4 real photographed receipts**.
  Real-photo lighting (uneven, shadowed, phone-camera) pushes large regions
  entirely black or entirely white under a fixed cutoff. Empirically tested
  6 preprocessing variants across all 4 real images: grayscale +
  auto-contrast strictly matched or beat the old approach on every one.
  Fixed by replacing the fixed threshold with `PIL.ImageOps.autocontrast`,
  plus an adaptive second OCR pass (2x upscale) for genuinely small source
  images (<700px shorter side) — triggered only when needed, confirmed via
  the same 4 real images that upscaling *hurts* an already-reasonably-sized
  photo, so it's not applied unconditionally. Result: 0 → 22-40 recovered
  words per receipt on the 3 previously-blank documents.
- `invoice_extraction/validators.py`'s `_DATE_FORMATS` had no 2-digit-year
  format — both Pakistani receipts tested use `"20/8/26"` style dates.
  Confirmed empirically `%Y` correctly rejects `"26"` (no silent
  wrong-year risk); added `%d/%m/%y`/`%d-%m-%y` after the 4-digit-year
  formats, so a genuine 4-digit year is never misread as 2-digit.
- `LABEL_VARIANTS["customer_label"]` existed but was never wired into
  `extract_invoice.py` — now produces `Invoice.customer_name` (still
  label-anchored only, no positional fallback, since there's no reliable
  "customer is conventionally here" convention the way vendor has one).

**New fields, all deterministic (no LLM), all `not_found`/`None` when
there's no evidence — never a guessed default:**

- `currency` (`invoice_extraction/currency.py`) — word/code matches
  (PKR/Rs/Rupees/USD/EUR/GBP) at confidence 1.0; a bare symbol ($/€/£/₨)
  only counts as evidence when adjacent to a digit, and is reported at a
  deliberately lower confidence (below the UNCERTAIN threshold) even then —
  a real false positive was found live (a garbled company-logo mark OCR'd
  as `"$9"` on a document actually priced in Rs) that no amount of regex
  tightening fully eliminates, so the honest fix is surfacing it as
  uncertain rather than fact, not pretending the pattern match is perfect.
- `document_type` (`invoice_extraction/document_type.py`) — keyword
  evidence (invoice/receipt/bill/quotation/credit_note/purchase_order) at
  confidence 1.0; a line-item table + total with no keyword at all gets a
  weak `"receipt"` guess at explicitly low confidence, never asserted as
  fact. Determined from document text, never from filename/extension.
- `payment_status` (`invoice_extraction/payment.py`, label list in
  `labels.py`) — PAID/UNPAID/PARTIALLY_PAID/DUE/PENDING only from direct
  textual evidence; "not detected" stays `not_found`, never silently
  becomes PENDING.
- `city`/`country` (`invoice_extraction/geography.py`) — a small, growable
  city→country table (seeded from the real documents actually tested:
  Pakistani and Indian cities) plus phone-country-code fallback (`+92` →
  Pakistan). Never defaults to any country.
- **FOUND/NOT_FOUND/UNCERTAIN status** (`confidence.field_status`) — a
  field is NOT_FOUND when absent, UNCERTAIN when present but below a
  confidence floor, FOUND otherwise. Exposed on every field in the API
  response (`FieldValueResponse.status`) alongside the existing
  value/confidence, additive — no existing consumer's shape changed.

All new fields flow through the same additive-field discipline already
established for `page_dimensions`/`field_locations`: new optional
dataclass/Pydantic fields with safe defaults, a new `Invoice.extracted_fields`
computed property (mirroring `field_confidence`) exposing only fields that
were actually found, no DB migration (nothing here is yet human-correctable
via `PUT` — that's a natural follow-up, not built in this pass).

**Explicitly not done, stated honestly:** handwriting recognition itself
(Tesseract cannot reliably read cursive script; two of the four real test
receipts are handwriting-heavy and remain genuinely hard — the system now
correctly reports "not found" on those fields instead of guessing, but
doesn't newly make the handwriting readable); perspective correction/deskew;
full layout/region segmentation; an LLM/VLM integration (deliberately not
added — consistent with §2a's standing no-LLM decision).

**Regression discipline**: 206 tests passing across all four touched
packages (`ocr` 26, `invoice_extraction` 124, `ai-engine` 9,
`invoice-service` 47) — up from the pre-existing 145, all pre-existing
tests still passing unchanged, plus new tests for every new module and a
dedicated real-receipt regression suite (checked-in copies of the actual 4
test documents, not synthetic recreations) guarding both the preprocessing
fix and the two confirmed currency false-positive cases specifically.

### Phase 6 — LiteParse + PaddleOCR migration (still no LLM)

**Why.** Phase 5's receipt-robustness pass fixed the OCR-preprocessing bug
that was destroying real photos, but Tesseract itself remained the accuracy
ceiling. The user asked for meaningfully better real-world accuracy with
**no LLM anywhere** — confirmed via research (not assumed) that this is
achievable: **PaddleOCR** is a deep-learning OCR *model*, not a generative
LLM (it detects and reads what's actually on the page — it cannot
hallucinate a field the way an LLM given an image can), and **LiteParse**
(`liteparse` on PyPI, by LlamaIndex) is a genuinely real, local-only,
non-cloud document parser explicitly positioned as the free/local
alternative to their own paid LlamaParse product. Both were verified live —
installed locally, inspected, and run against this project's own real
sample documents — not taken on faith from documentation, consistent with
this whole doc's discipline.

**Architecture**: `LiteParse` is now the primary Stage 1 engine
(`backend/libs/ocr/ocr/liteparse.py`), calling **PaddleOCR** (a new
internal-only microservice, `backend/services/paddleocr/`) as its OCR
backend via LiteParse's own **documented** external-OCR-server contract
(`OCR_API_SPEC.md` in `run-llama/liteparse` — `POST /ocr`, file+language in,
`{results: [{text, bbox, confidence, polygon}]}` out — this project's
`paddleocr` service implements that contract exactly, nothing invented). If
PaddleOCR is unreachable, LiteParse falls back to its own built-in OCR
(confirmed live: Tesseract-based under the hood); if LiteParse itself fails
outright, the caller falls back one more level to the fully original
PyMuPDF+pytesseract pipeline. **`OCR_ENGINE=tesseract` is a one-variable
rollback** to that exact original pipeline, untouched, always available.

**PyMuPDF status**: retained, not removed. `ocr/pdf.py`'s native-text
extraction is superseded by LiteParse for the default engine, but
`ocr/render.py`'s `render_page_png()` (the Scanner-v2 bounding-box preview
feature) still uses PyMuPDF directly — a rasterization concern LiteParse
doesn't replace — and the whole `pdf.py`/`image.py` pipeline stays fully
intact as the `tesseract` rollback path's actual implementation.

**Real findings from live verification (not assumed from docs):**

1. **LiteParse's own `complexity.needs_ocr` heuristic misfires on this
   project's real invoices.** Flagged a genuinely clean sample invoice as
   `needs_ocr=True` (reason: `sparse-text`) purely because invoices have a
   lot of whitespace relative to page area — triggering an unnecessary
   ~18-second OCR pass on a document with a perfect text layer. Fixed by
   routing with this project's own proven avg-chars-per-page check instead
   (the same one `pdf.py` already used) — confirmed the same document now
   resolves in ~50ms.
2. **LiteParse's TextItem shape differs between its two extraction paths** —
   span-level with a nested word list for native text, but already
   word-level (empty nested list) for OCR'd text. A naive "read `.words`"
   flatten silently produced zero positioned words on the OCR path despite
   `.text` having real content; fixed to handle both shapes.
3. **A real API-contract gap**: routing straight into LiteParse without
   this project's own file-type check first turned "you uploaded a .docx"
   from a clean 400 into an opaque 502 (LiteParse raises its own
   `ParseError`, which the route wasn't written to expect). Fixed by
   validating the file type before ever calling LiteParse — same
   `classify_file`, same contract as before.
4. **PP-OCRv6 (PaddleOCR's own current default) crashes on CPU inference
   with oneDNN enabled** on this project's Windows dev host —
   `NotImplementedError: ConvertPirAttribute2RuntimeAttribute` (a
   PaddlePaddle 3.3.1 PIR-compiler/oneDNN incompatibility). Fixed by
   disabling oneDNN (`enable_mkldnn=False`) — but this has **only been
   confirmed necessary on Windows**; the actual Linux container target
   needs its own verification before ever assuming `True` is safe there.
5. **With oneDNN disabled, the default "medium" model tier took 50-70+
   seconds per image** on this CPU — unacceptable for a synchronous
   request. Explicitly requesting the **PP-OCRv5 mobile** detection+
   recognition tier instead brought that down to **~8-13 seconds** for
   comparable real-world accuracy on this project's own test receipts — a
   deliberate, measured model-size/latency tradeoff, not a default left to
   chance.
6. **LiteParse's request-hedging (`ocr_hedge_delays_ms`) compounds cost
   against a consistently-slow-but-working OCR server** — fired repeated
   duplicate OCR calls (5 for one logical request, observed live) while
   waiting on the "medium" tier's slow responses, turning ~1 minute into
   several. Disabled (`ocr_hedge_delays_ms=[]`) — hedging is the right tool
   for a flaky service, not a reliably-slow one.
7. **PaddleOCR's `.predict()` rejects raw bytes outright** ("Only
   `numpy.ndarray` and `str` are supported") — the paddleocr service
   decodes via PIL first. Also confirmed live that **PIL's native RGB
   channel order detects more real text than OpenCV's native BGR** on this
   project's own sample receipt (RGB caught a stylized vendor-name/logo
   region BGR missed entirely) — used deliberately, not just for
   convenience.

**Real accuracy comparison**, all 4 receipts from Phase 5's regression
fixtures, Tesseract (old) vs LiteParse+PaddleOCR (new), same documents:

| Receipt | Tesseract confidence | New confidence | Notable difference |
|---|---|---|---|
| SMS Traders (heavy handwriting) | 52.0% | 86.8% | Vendor name "SMS TRADERS" and all 3 phone numbers correctly read; Tesseract found neither |
| YZ Paint & Hardware | 69.2% | 90.3% | Exact line item "Hathori Small", qty/rate/amount, and every table header correct |
| Guest Check (hardest — 387×516px) | 42.3% | 82.1% | 3x more words recovered; total "$36.36" recognizably correct |
| Samsuddin Siddiqui invoice | 52.2% | 82.4% | Fewer but far more reliable words (Tesseract's higher count was mostly noise) |

**Dependencies** (all version-pinned — LiteParse especially, given it went
PyPI 2.0.0b2 → 2.13.0 in ~5 months): `liteparse==2.13.0` added to
`backend/libs/ocr`; `paddleocr==3.7.0` + `paddlepaddle==3.3.1` added to the
new `backend/services/paddleocr` service only (kept out of `ocr`/`ai-engine`
directly — a heavy deep-learning stack has no business being a dependency
of every service that happens to import the OCR library).

**Docker**: new `paddleocr` service in `docker-compose.yml`; its Dockerfile
bakes the PP-OCRv5 mobile models into the image at build time (confirmed
live this is a real, non-trivial download — no runtime/first-request
download, satisfying the "clone and run" requirement). `ai-engine` gets
`PADDLEOCR_SERVICE_URL=http://paddleocr:8008` and depends on it starting.

**Verification status**: fully verified **locally** (Tesseract + LiteParse
+ PaddleOCR all installed on this dev host, no Docker) — all existing
pytest suites green throughout, new tests added for every finding above
(regression guards, not just happy-path), and the real-document comparison
table above run against a real running PaddleOCR HTTP server, not an
in-process shortcut. **Docker build/compose verification is explicitly
deferred** — the Dockerfile's model-baking step and the Linux-oneDNN
question (finding #4 above) are the two things that most need confirming
there before this is trusted in production.

## 6. Open questions to resolve during Phase 1, not before

- **Urdu-language documents.** The architecture doc's Pakistani-SME context means some real vendor
  invoices may be in Urdu, not English. Tesseract supports it (`tesseract-ocr-urd` language pack), but
  it is not assumed as MVP scope here — add it if Phase 1's real test documents show it's actually
  needed, rather than installing every language pack speculatively.
- **Whether Pillow-only preprocessing is enough**, or real phone-camera receipt photos need
  OpenCV-level deskew/denoise. Only answerable by testing against real sample documents, not by
  guessing up front.
- **Exact confidence-combination formula** (§3) — depends on measuring real OCR/arithmetic/LLM
  agreement rates once Phase 1 has run against enough real documents to have numbers worth tuning
  against.

---

## Correction recorded against the wider architecture report

`FinPilot_AI_Backend_Architecture_Report (3).md` §5.8 ("Calls AI model (OpenAI/Anthropic) with
structured prompt"), §11 (the step-by-step scanner flow showing the raw file going straight to
OpenAI/Anthropic), and §14's tech stack table ("AI: OpenAI GPT-4o or Anthropic Claude — Invoice OCR +
chat assistant") all describe the vision-LLM-as-OCR design this doc replaces. Those sections are
being corrected in place to point here rather than left contradicting this plan — see the inline
notes added at each location. The AI chat assistant and AI insights responsibilities in §5.8 are
unaffected; only the OCR mechanism changes.
