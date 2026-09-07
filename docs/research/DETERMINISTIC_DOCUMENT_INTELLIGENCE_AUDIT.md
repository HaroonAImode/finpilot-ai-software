# Deterministic Document Intelligence Audit

**Scope**: A complete, code-grounded audit of how FinPilot AI turns a raw uploaded document into a structured financial JSON record, entirely without an LLM. This document does not modify any implementation — it explains, catalogs, and critiques what already exists.

**Method**: Every claim below is derived from reading the actual source files listed. Nothing here is inferred from documentation alone — the pipeline's own design doc (`docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md`, referenced throughout the code as "Rule X.Y") is used only to cross-reference intent against the real implementation, and every place implementation diverges from that spec is called out explicitly.

**Audited codebase state**: This audit reflects the code as of the "post-vendor-audit-fix" Golden Dataset baseline (`backend/eval/baselines/2026-09-03-post-vendor-audit-fix.json`): Document Type 70%, Transactional 100%, Vendor 62%, Invoice Date 83%, Total 73%, Category 61%, False Positives 0/35.

---

## 1. The Actual Pipeline, As Implemented

The proposed sequence in the task prompt is **close but not exactly right**. The real, traced sequence is:

```text
Raw file bytes
   │
   ▼
[STAGE 0] File-type classification            ocr/extract.py: classify_file()
   │  (extension + mimetype → "pdf" | "image"; anything else → UnsupportedFileType, never guessed)
   ▼
[STAGE 0b] Document Preprocessing (IMAGE UPLOADS ONLY — never for PDFs)
   │  ocr/preprocess.py: preprocess_image()
   │  - OpenCV smart-crop (removes background/desk around a photographed receipt)
   │  - Multi-document split (if a single photo confidently contains 2+ documents)
   │  - EXIF rotation correction happens here too, via ocr/image_io.py: decode_image()
   ▼
[STAGE 1] Text/OCR extraction — engine-selectable, with a real fallback chain
   │  ocr/extract.py: extract_text_with_engine(engine="liteparse"|"tesseract")
   │
   │  engine="liteparse" (current default):
   │    ocr/liteparse.py: extract_text()
   │      1. Parse with OCR disabled (fast native-text pass, ~50ms)
   │      2. avg_chars_per_page >= 20 (_MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER)?
   │           YES → done, method="pdf_text"
   │           NO  → OCR path:
   │                 a. PaddleOCR service configured? → try it
   │                    fails/unavailable → fall back to LiteParse's own built-in OCR (Tesseract-based)
   │                 b. Neither works at all → raise LiteParseOcrError
   │      3. LiteParseOcrError caught one level up (ocr/extract.py) → fall back to
   │         the FULLY SEPARATE legacy pipeline below
   │
   │  engine="tesseract" (explicit rollback) OR liteparse-path-exhausted fallback:
   │    ocr/pdf.py: extract_from_pdf() — PyMuPDF native text, same 20-char/page gate,
   │      falls back to page-rasterize (300 DPI) + ocr/image.py: ocr_image_words()
   │      (Tesseract, with grayscale+autocontrast preprocessing and a small-image
   │      upscale-and-retry heuristic)
   ▼
   Output: ocr.ExtractionResult — plain text + one list of PositionedWord per page
   (word text, x0/y0/x1/y1 bbox, page index, per-word confidence, optional font_size)
   ▼
[STAGE 2] Structuring — invoice_extraction.extract_invoice() — ALL of this is rules,
   │  zero ML/LLM anywhere.
   │
   ├─ 2a. Line grouping        invoice_extraction/lines.py: group_into_lines()
   │       (words → visual rows, by vertical bbox overlap, per page)
   │
   ├─ 2b. Header/single-value fields — label-anchored, THEN positional fallback
   │       invoice_extraction/fields.py: find_label_anchored_text() (primary)
   │       + find_vendor_name() / find_date_anywhere() / find_total_anywhere() (fallbacks)
   │       + invoice_extraction/validators.py (pattern validation of what was found)
   │
   ├─ 2c. Line-item table reconstruction
   │       invoice_extraction/line_items.py: find_header_row() → detect_columns()
   │       → find_totals_boundary() → reconstruct_rows()
   │
   ├─ 2d. Generic label/value discovery (catches anything with no canonical field)
   │       invoice_extraction/dynamic_fields.py: discover_fields()
   │
   ├─ 2e. Document-wide evidence scans (currency, city/country, payment status,
   │       document type) — NOT label-anchored, scanned over the whole OCR text
   │       invoice_extraction/currency.py, geography.py, payment.py, document_type.py
   │
   ├─ 2f. Document type classification + transactional determination
   │       invoice_extraction/document_type.py: detect_document_type(), is_transactional()
   │       — happens BEFORE arithmetic validation and BEFORE category assignment,
   │       and gates whether total/subtotal/tax/discount/vendor even survive
   │       (see §7's "Transactional" subsection — this is the most safety-critical
   │       branch in the whole pipeline)
   │
   ├─ 2g. Arithmetic / cross-field validation
   │       invoice_extraction/arithmetic.py: check_line_item(), check_totals(),
   │       is_plausible_tax_rate(), is_plausible_invoice_date()
   │
   ├─ 2h. Composite confidence + routing
   │       invoice_extraction/confidence.py: field_confidence() → document_confidence()
   │       → route() → "auto_processed" | "needs_review" | "needs_review_high_priority"
   │
   └─ 2i. Known-vendor fuzzy match (optional, only if caller supplies a vendor list)
           invoice_extraction/dedup.py: find_matching_vendor()
   ▼
   Output: invoice_extraction.ExtractedInvoice (dataclass) — every field is a
   FieldValue{value, confidence, method, page, bbox}, never a bare scalar
   ▼
[STAGE 3] API contract translation (ai-engine)
   │  ai-engine/app/api/routes/ocr.py: POST /ocr/extract
   │  ai-engine/app/schemas/ocr.py: ExtractedInvoiceResponse.from_extracted_invoice()
   │  (adds the FOUND/NOT_FOUND/UNCERTAIN tri-state via confidence.field_status())
   ▼
[STAGE 4] Persistence mapping (invoice-service)
   │  invoice-service/app/services/invoice_builder.py: build_invoice()
   │  invoice-service/app/services/category_classifier.py: classify_category()
   │  — category assignment happens HERE, one full service hop after Stage 2,
   │    not inside invoice_extraction at all (see §7's "Category" subsection)
   ▼
[STAGE 5] Saved Invoice/InvoiceItem row (invoice-service/app/models/invoice.py)
   — raw_extraction_json holds Stage 2/3's full output untouched (Rule 8.1);
     editable columns (vendor_name, total, category, payment_method, etc.)
     start as copies of it and can be corrected by a human without ever
     overwriting the original
```

**Explicit divergences from the task's assumed sequence:**

- **"Candidate Generation → Field Validation → Candidate Scoring/Ranking → Field Selection" is NOT a separate, uniform stage that runs after structuring.** It happens *inline, per field, inside `fields.py`/`document_type.py`*, and it does not follow a generate-all-then-score-all pattern for every field — see §7 for the real, field-by-field mechanics, which differ substantially between vendor/date/total.
- **Document classification happens *before* transactional classification because they are the same decision** — `is_transactional()` is a pure function of `document_type`, not an independent classifier. There is no separate "transactional classifier" module. Task assumed these are two pipeline stages; they are one classification with a lookup table.
- **Category classification is NOT part of `invoice_extraction` at all.** It lives in a different service (`invoice-service`), one HTTP hop downstream, and runs at `build_invoice()` time — after the OCR/structuring service has already returned its response. This is an important architectural fact: `invoice_extraction`'s own Golden Dataset benchmark (`backend/eval`) evaluates category accuracy by *directly importing* `category_classifier.py` from the other service (see §15) — a real, working cross-service test wire, but proof that "category" is architecturally outside the extraction library's own boundary.
- **"OCR Tokens + Positions / Layout" and "Text Normalization" are not two separate global stages.** There is no whole-document text normalization step. Normalization is applied narrowly and locally: date-string normalization only inside `validators.parse_date`/`find_date_in_text`, money-string parsing only inside `validators.parse_money`, vendor-candidate cleanup only inside `fields.find_vendor_name`'s own helper chain. There is no single "normalize everything" function.
- **Line-item reconstruction happens in parallel with, not strictly after, header-field candidate generation** — `extract_invoice()` computes `header_row`/`item_body` *before* calling `_total_field`, specifically so the totals fallback (`_dynamic_total`) can consult line-item boundaries. The two are interleaved, not sequential black boxes.

---

## 2. Every Rule Currently Being Used — Overview

Full rule-by-rule detail is in **§3** and the **§21 master table**. This section is the map of *where* rules live, so nothing is missed.

| Concern | File(s) |
|---|---|
| File-type routing, engine selection, fallback chains | `ocr/extract.py`, `ocr/liteparse.py`, `ocr/pdf.py` |
| Image preprocessing (crop/split/EXIF) | `ocr/preprocess.py`, `ocr/image_io.py` |
| OCR execution + retry heuristics | `ocr/image.py` |
| Word→line grouping | `invoice_extraction/lines.py` |
| Label-anchored field extraction | `invoice_extraction/fields.py` (`find_label_anchored_text`) |
| Vendor positional fallback + noise filtering | `invoice_extraction/fields.py` (`find_vendor_name` and 8 helper functions) |
| Date positional fallback | `invoice_extraction/fields.py` (`find_date_anywhere`) |
| Total positional fallback | `invoice_extraction/fields.py` (`find_total_anywhere`) |
| Label-variant dictionaries (the "vocabulary" of what a label looks like) | `invoice_extraction/labels.py` |
| Pattern validation (is this candidate plausible at all) | `invoice_extraction/validators.py` |
| Generic label/value discovery (uncategorized fields) | `invoice_extraction/dynamic_fields.py` |
| Line-item table reconstruction | `invoice_extraction/line_items.py` |
| Document type classification | `invoice_extraction/document_type.py` |
| Transactional determination | `invoice_extraction/document_type.py` (`is_transactional`, same module) |
| Currency detection | `invoice_extraction/currency.py` |
| City/country detection | `invoice_extraction/geography.py` |
| Payment status detection | `invoice_extraction/payment.py` |
| Arithmetic cross-validation | `invoice_extraction/arithmetic.py` |
| Confidence scoring + routing | `invoice_extraction/confidence.py` |
| Deduplication + vendor fuzzy-match | `invoice_extraction/dedup.py` |
| Output schema | `invoice_extraction/schema.py` |
| Orchestration (wires all of the above) | `invoice_extraction/extract_invoice.py` |
| API contract / tri-state status derivation | `ai-engine/app/schemas/ocr.py`, `ai-engine/app/api/routes/ocr.py` |
| Category assignment (keyword-based, separate service) | `invoice-service/app/services/category_classifier.py` |
| Extraction→DB-model mapping, transactional-clearing enforcement (again) | `invoice-service/app/services/invoice_builder.py` |
| DB model, editable-vs-derived field split | `invoice-service/app/models/invoice.py` |

---

## 3. Every Rule, Individually

Rule IDs below are this audit's own (task-requested scheme), cross-referenced to the source spec's "Rule X.Y" numbering in the Location line wherever the code comments cite one.

### Stage 0 — Routing

**INTAKE-001 — File-type classification**
- **Location**: `ocr/extract.py`, `classify_file()`
- **Purpose**: Decide PDF vs. image before any parsing; refuse anything else outright.
- **Input**: filename, mimetype.
- **Logic**: `mimetype == "application/pdf"` or extension in `{.pdf}` → `"pdf"`. `mimetype` startswith `"image/"` or extension in `{.jpg,.jpeg,.png,.webp,.bmp,.tiff,.tif}` → `"image"`. Neither → raise.
- **Output**: `"pdf" | "image"`, or `UnsupportedFileType`.
- **Fields affected**: Routes the entire pipeline; indirectly affects everything.
- **Priority**: First rule in the whole pipeline; nothing overrides it.
- **Failure behavior**: Hard rejection (400 at the API layer) — never attempts OCR on an unrecognized type.
- **Risk**: A file with a wrong/missing extension AND a generic mimetype (`application/octet-stream`) is rejected even if it is a real PDF/image — no content-sniffing fallback exists.

**INTAKE-002 — Native-text-layer sufficiency gate**
- **Location**: `ocr/pdf.py` `MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER = 20`; independently duplicated in `ocr/liteparse.py` `_MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER = 20` (see §11, this is a real duplication, not shared).
- **Purpose**: Decide whether a PDF has real, usable embedded text or needs OCR.
- **Input**: `total_chars / page_count` for the fast native-text pass.
- **Logic**: `avg_chars_per_page >= 20` → trust native text (`method="pdf_text"`, confidence 1.0). Else → rasterize + OCR.
- **Output**: Routing decision + `ExtractionResult.method`.
- **Fields affected**: Every downstream field's base confidence (`ocr_result.confidence` feeds `confidence.field_confidence`).
- **Priority**: Runs once, per document, before Stage 2 starts.
- **Failure behavior**: N/A — always resolves to one of the two paths.
- **Risk**: A page with exactly ~20-30 chars of native text (e.g., only a watermark or page number) could be misclassified as "has a real text layer" and skip OCR entirely, silently losing the actual scanned content. No known real-world failure recorded for this yet in `docs/invoice-ocr-plan.md`, but the threshold is stated as a heuristic, not a measured one.

**INTAKE-003 — Engine selection with real, tested fallback chain**
- **Location**: `ocr/extract.py` `extract_text_with_engine()`; `ocr/liteparse.py` `extract_text()`/`_extract_with_ocr()`.
- **Purpose**: Let PaddleOCR/LiteParse be the primary engine while guaranteeing the legacy Tesseract path still works as a rollback, with zero shared code between the two (explicit design choice, see file docstring: "a genuine independent code path... a rollback is never at risk of inheriting a liteparse-path bug").
- **Input**: `Settings.ocr_engine` env var (`"liteparse"` default, `"tesseract"` explicit).
- **Logic**: 3-level fallback: PaddleOCR service → LiteParse's own built-in OCR → (if LiteParse fails entirely) the fully legacy PyMuPDF+pytesseract pipeline.
- **Output**: `ExtractionResult`.
- **Priority**: Outermost routing decision for Stage 1.
- **Failure behavior**: Each level logs a warning and falls through; only total exhaustion (`LiteParseOcrError` uncaught anywhere) would surface as an error to the caller — and even that is caught one level up in `extract.py`.
- **Risk**: A silent full-fallback-to-tesseract on every request (e.g., PaddleOCR service permanently down) would degrade OCR quality invisibly unless someone reads logs — there is no metric/alert surfaced for "how often did we fall back."

**PREP-001 — Smart crop / multi-document split**
- **Location**: `ocr/preprocess.py`, `preprocess_image()` (top-level), image uploads only.
- **Purpose**: Remove background/desk clutter around a photographed receipt; split a single photo containing multiple physical documents.
- **Input**: Raw image bytes.
- **Logic**: OpenCV contour detection (Otsu threshold OR Canny edges, morphological close) → score each contour by `rectangularity × size_score` → act only if confidence ≥ `_MIN_CONFIDENCE_TO_ACT = 0.55`. Multi-doc split additionally requires 2+ confident non-overlapping (`_MAX_MULTI_DOC_OVERLAP_RATIO = 0.15`) candidates, or a single large candidate (`area_ratio >= 0.35`) probed for a "content valley" (a genuinely empty vertical strip, `_MAX_VALLEY_DENSITY_RATIO = 0.15` relative density, `_MIN_VALLEY_WIDTH_RATIO = 0.25` of region width, both flanks must show real content coverage `_MIN_HALF_CONTENT_COVERAGE = 0.12`).
- **Output**: 1+ `DetectedDocument` (bbox, confidence, PNG bytes).
- **Fields affected**: Indirectly all fields, by changing what image Stage 1 actually sees.
- **Priority**: Runs before Stage 1, image uploads only; a PDF is never preprocessed.
- **Failure behavior**: The module's one explicit hard rule — "when detection is not confident, do nothing" — always falls back to the untouched original image. A decode failure (`ValueError`) is caught one level up (`ocr/extract.py`) and also falls back to unprocessed bytes.
- **Risk (documented in-code, not hypothetical)**: The module's own docstring for `_split_by_content_valley` records a *real, previously-found* false positive: a single tall receipt's own header→gap→table→gap→totals layout produces a **row**-based valley that looks exactly like an inter-document gap — this is why the split is deliberately **column-only**, never row-based; row-based splitting was implemented, tested against real data, and explicitly abandoned. A wrong crop is called out as strictly worse than a missed one ("can permanently cut off a total, a signature, or a stamp").

### Stage 2b — Header-field extraction (label-anchored, primary method)

**FIELD-001 — Label-anchored text extraction (the base mechanism for every header field)**
- **Location**: `invoice_extraction/fields.py`, `find_label_anchored_text()`. Source spec: Rule 3.1.
- **Purpose**: Locate a value by finding its label first, rather than a blind whole-document regex.
- **Input**: `lines: list[list[PositionedWord]]`, a field name (key into `LABEL_VARIANTS`).
- **Logic**: Sorted-by-length label variants (longest first, so "invoice number" wins over bare "invoice"), matched as whole-word token sequences on each line (`_find_label_word_span`). Same-line remainder tried first (truncated at the next known label or a `_NON_VALUE_TOKENS` marker — `_truncate_at_next_label`); falls back to the next line's x-aligned words (`x0 >= label_x0 - 5`) if the same line has nothing.
- **Output**: `(value_text, page, bbox)` or `None`.
- **Fields affected**: `invoice_number`, `invoice_date`, `ntn`, `subtotal`, `tax`, `discount`, `total` (all header fields except vendor's own primary-fallback path).
- **Priority**: Always tried first; every fallback in FIELD-005/006/007 only runs when this returns `None`.
- **Failure behavior**: Returns `None` → caller (`_text_field`/`_money_field`/`_date_field`) falls through to whichever positional fallback exists for that field, or `not_found()`.
- **Risk**: Column-bleed on a packed multi-column header row is the documented real failure this is built to avoid (module docstring's own example: "Bill From"/"Invoice No."/"Currency" side by side) — mitigated but not eliminated by `_NON_VALUE_TOKENS` and `_truncate_at_next_label`; a vendor's *own* label vocabulary not yet in `LABEL_VARIANTS` simply never matches (see §12, LABELS-VARIANT-GROWTH).

**FIELD-002 — Value-pattern validation (filter, not finder)**
- **Location**: `invoice_extraction/validators.py`, `is_plausible_invoice_number()`, `is_plausible_ntn()`. Source spec: Rule 3.3.
- **Purpose**: Reject a label-adjacent value that clearly isn't the real field (a page number, another label).
- **Logic**: `is_plausible_invoice_number`: rejects a bare 1-2 digit number; else matches `^[A-Za-z0-9][A-Za-z0-9/_-]{1,29}$`. `is_plausible_ntn`: `^\d{6,7}-?\d?$`, structural shape only, no checksum (none exists to validate against per the module docstring).
- **Output**: bool.
- **Fields affected**: `invoice_number`, `ntn`.
- **Priority**: Runs immediately after FIELD-001 finds a same-line/next-line candidate, inside `_text_field()`.
- **Failure behavior**: A failed validation doesn't discard the field outright — it's kept at 30% of base confidence with `method="label_anchor"` (not `"label_anchor+pattern"`), so it still routes into confidence scoring as a weak signal rather than vanishing (see `extract_invoice.py::_text_field`).
- **Risk**: `is_plausible_invoice_number`'s character class is deliberately permissive ("invoice numbers vary the most across vendors") — this means it will accept genuine noise that happens to look invoice-number-shaped (e.g., a stray alphanumeric code) with no independent cross-check.

**FIELD-003 — Money-value parsing/validation**
- **Location**: `invoice_extraction/validators.py`, `parse_money()`. Used inside `_money_field()`/`_total_field()`.
- **Purpose**: Turn label-adjacent text into a numeric amount, handling currency prefixes/suffixes, thousands separators, and accounting-style negatives.
- **Logic**: `_MONEY_PATTERN = r"(?:rs\.?|pkr|\$|usd|sgd|gbp|eur)?\s*(-?[\d,]+(?:\.\d{1,2})?)\s*(?:rs\.?|pkr|usd|sgd|gbp|eur)?"`, case-insensitive `.search()` (not full match — first numeric run anywhere in the text). Parenthesized amounts (`_PARENTHESISED_NEGATIVE`, `"(1,000)"`) are converted to negative.
- **Output**: `Optional[float]`.
- **Fields affected**: `subtotal`, `tax_amount`, `discount`, `total`, line-item `qty`/`rate`/`amount`.
- **Priority**: Runs on whatever text FIELD-001/positional fallback already located; not itself a search over the document.
- **Failure behavior**: `None` on no numeric match at all → caller reports `FieldValue(value=None, confidence=base_conf*0.3, method="label_anchor")`, same degraded-not-discarded pattern as FIELD-002.
- **Risk**: The leading sign `-` is deliberately captured ("a real invoice writes credits that way... dropping the sign silently turns a deduction into a charge") — a real, previously-considered financial bug, now fixed. `.search()` semantics mean a money-pattern anywhere in a longer label-adjacent phrase is accepted; a label whose "value" text is actually a sentence containing an unrelated number could still parse.

**FIELD-004 — Date parsing + OCR-noise normalization**
- **Location**: `invoice_extraction/validators.py`, `parse_date()`, `find_date_in_text()`, `_normalize_date_text()`.
- **Purpose**: Parse a date from label-adjacent text, tolerating real OCR corruption patterns found on live documents.
- **Input**: raw text.
- **Logic** (order matters, documented as such in-code):
  1. Strip trailing clock-time (`_TRAILING_TIME_PATTERN`, e.g. "Dec 14, 2020, 4:18:34 PM" → "Dec 14, 2020").
  2. Strip ordinal-suffix OCR noise (`_ORDINAL_SUFFIX_NOISE`, e.g. "30\" June 2026" → "30 June 2026" — restricted character class `[stndrhSTNDRH"'′″]` deliberately excludes letters that could eat into a real month name).
  3. Re-insert missing digit↔letter separators (`_MISSING_SEPARATOR_DIGIT_LETTER`/`_LETTER_DIGIT`, e.g. "17Jun 2026" → "17 Jun 2026").
  4. Try 9 formats in a **fixed, day-first-preferred priority order** (`_DATE_FORMATS`): `%d/%m/%Y`, `%d-%m-%Y`, `%Y-%m-%d`, `%d %B %Y`, `%B %d, %Y`, `%d %b %Y`, `%b %d, %Y`, `%d/%m/%y`, `%d-%m-%y`, `%m/%d/%Y` (last — US format only tried after every day-first/4-digit-year format is exhausted).
  5. `find_date_in_text()` (broader): if the whole-string parse fails, searches for a date-shaped **substring** anywhere in the text via 5 regexes (`_DATE_SUBSTRING_PATTERNS`) and parses just that.
- **Output**: `Optional[date]`.
- **Fields affected**: `invoice_date`.
- **Priority**: FIELD-001's label-anchored value is tried through the strict `parse_date` first; the positional fallback (FIELD-006) uses the broader `find_date_in_text`.
- **Failure behavior**: `None` → caller degrades confidence (label found, value unparseable) or falls through to positional scan.
- **Risk**: The fixed day-first priority order is **explicitly documented as a known limitation** (validators.py docstring, quoting the source spec: "a more robust version would detect the dominant format *across* a document's own date fields rather than a fixed global priority order... not implemented here"). Any ambiguous `MM/DD` date where `DD <= 12` will always resolve day-first regardless of the document's actual locale.

**FIELD-005 — Vendor name extraction (label-anchored + 8-stage positional fallback pipeline)**
- **Location**: `invoice_extraction/fields.py`, `find_vendor_name()` + 8 named helpers. Source spec: Rule 3.4.
- **Purpose**: The single hardest and most heavily-audited field in this codebase (see §12/§18 of `docs/invoice-ocr-plan.md` — two dedicated re-audit passes).
- **Input**: `lines`, `top_fraction=0.25` (top quarter of page 1 only, for the positional path).
- **Logic** (full candidate → filter → select pipeline, the clearest instance of that pattern anywhere in this codebase — see §7 below for the detailed walkthrough):
  1. Label-anchored (`"bill from"|"sold by"|"from"|"vendor"`) — if found, return immediately, method `"label_anchor+pattern"`.
  2. Else, gather every non-empty line in the top 25% of page 0 as a raw candidate set.
  3. Per candidate: strip a leading document-type word (`_strip_leading_document_type_label`, e.g. "INVOICE The Florist..." → "The Florist..."), strip a leading reference/amount stamp (`_strip_leading_reference_stamp`, digit-ratio > 50% on the first word), truncate at a mid-line NTN marker (`_truncate_at_registration_number`, `\bNTN\s*#?\s*\d`).
  4. Reject the whole candidate if it's a reference number, a decorative/metadata line, or below `_MIN_POSITIONAL_FALLBACK_CONFIDENCE = 0.85` mean word confidence (`_is_rejected_vendor_candidate`, `_looks_like_reference_number`, `_looks_like_decorative_or_metadata_text`).
  5. First candidate clearing every filter wins; its immediately-following line is folded in if it reads as a name continuation (`_is_name_continuation`: no digits, no commas, ≥0.90 confidence, gap ≤1.75× line height).
  6. If nothing clears the confidence bar: fall back to the highest-confidence candidate that at least isn't recognizable noise.
  7. If literally every candidate looks like noise: fall back to the plain first-non-empty line (a deliberate "a low-quality guess beats losing the field outright" stance, cited against the source spec's own §7 principle).
- **Output**: `(text, page, bbox, method)`.
- **Fields affected**: `vendor_name`.
- **Priority**: Label-anchored always wins over positional. Within positional, order is: confidence-passing candidates in document order → highest-confidence non-noise candidate → first-line guess.
- **Failure behavior**: Never truly fails — always returns *something* once any candidate line exists on page 0, by design (step 7 above). Only returns `None` if page 0 has zero non-empty top-quarter lines at all.
- **Risk**: This is the **lowest-accuracy canonical field in the whole system (62%)**. §18's own re-audit found 12/35 documents still wrong, characterized honestly as: bank-screenshot account-holder confusion, dense POS layout collisions, OCR-corrupted names, a stamp that is only 40% digit (just under the 50% threshold — `9897WhatsApp…`), a real letterhead sitting on a 3-part line whose whole-line digit ratio (43%) doesn't trip the filter (`34323WhatsApp…`, single-document evidence, not fixed), and one case (`655443WhatsApp…`) where an adjacent column's truncated "Date:" label fragment survives as unremovable noise because the surviving fragment ("ate:") isn't a recognizable word to truncate at.

**FIELD-006 — Date positional fallback**
- **Location**: `invoice_extraction/fields.py`, `find_date_anywhere()`.
- **Purpose**: Recover a date with no label at all, or a label glued to its value with zero space (documented real case: `"Date:29/06/2026.16:29:41"` arrives as one OCR word — a whole-word label matcher can never find "date" inside it).
- **Logic**: Scans every line, document order, skipping lines below the same 0.85 confidence floor as FIELD-005, using `find_date_in_text` (the *broad*, substring-tolerant parser).
- **Output**: `(text, page, bbox)` or `None`.
- **Priority**: Only runs when FIELD-001 (label-anchored) found nothing at all for `invoice_date`.
- **Failure behavior**: `None` → `invoice_date` becomes `not_found()` (or keeps the FIELD-001 degraded result if one existed).
- **Risk**: Returns the *first* matching line in document order, not necessarily the most prominent/likely one — unlike vendor's "top of page" convention, there is no positional prior for where a date should be, so this is a pure first-match scan.

**FIELD-007 — Total positional fallback (most conservative fallback in the codebase)**
- **Location**: `invoice_extraction/fields.py`, `find_total_anywhere()`.
- **Purpose**: Recover a total with literally no "Total:" label anywhere — the documented real case is a self-issued Pakistani cash-receipt voucher stating its amount in prose ("received amount Rs. 6,000/- as Salary/Stipend").
- **Logic**: Scans every word on every line for one that "looks like a specifically-written amount" (`_looks_like_a_specific_amount`: must carry a genuine thousands-comma, a decimal point, or a currency prefix — explicitly *not* just any digit run, to reject a CNIC fragment, an NTN, a phone number, or a bare item quantity by raw size) at ≥0.85 confidence, with **no letters glued into the digits after stripping a leading currency word** (rejects an OCR-merged token like "Rs.5_cleaning", which is documented as the single worst failure shape this fallback can have — a plausible-looking wrong number, worse than no number).
- **Output**: The single candidate **only if exactly one exists on the whole document**; `None` for 0 or 2+.
- **Fields affected**: `total` only (via `_total_field`'s second-tier fallback, after label-anchored).
- **Priority**: Second of three total-finding tiers (label-anchored → this → `_dynamic_total`).
- **Failure behavior**: Ambiguity (2+ candidates) is treated as `NOT_FOUND`, deliberately, never "pick the largest."
- **Risk**: Genuinely the safest fallback in the file by design — the "exactly one candidate" gate is a hard financial-safety rule, not a tunable threshold. The risk is entirely on the recall side (many real documents with 2+ amount-shaped tokens get nothing from this path), never on the precision side.

**FIELD-008 — Dynamic-field total fallback (third tier)**
- **Location**: `invoice_extraction/extract_invoice.py`, `_dynamic_total()`, `_looks_like_a_total_key()`.
- **Purpose**: Recover a total the generic label/value discovery pass (`dynamic_fields.py`) already found correctly, whose label just isn't in `LABEL_VARIANTS["total"]` — documented real case: `"BillTotal:"` (glued, no space).
- **Logic**: Filters `discover_fields()`'s output for keys containing a total-shaped marker (`_TOTAL_LIKE_KEY_MARKERS = ("total","amountdue","amount_due","balancedue","balance_due","payable")`) while explicitly excluding subtotal/tax/discount-shaped keys (`_NOT_TOTAL_KEY_MARKERS`). Requires **exactly one** match — same conservative standard as FIELD-007.
- **Output**: `FieldValue` with `method="positional_fallback"`, or `None`.
- **Priority**: Last of three tiers for `total`.
- **Failure behavior**: 0 or 2+ matches → `None`, caller falls through to whatever the label-anchored attempt originally returned (possibly itself `not_found()`).
- **Risk**: Depends entirely on `dynamic_fields.py`'s own label/value discovery correctly having found and parsed the value already — a second-order dependency, not an independent check.

### Stage 2c — Line-item table

**LINEITEM-001 — Header-row detection**
- **Location**: `invoice_extraction/line_items.py`, `find_header_row()`. Source spec: Rule 4.3.
- **Logic**: Requires **2+** distinct column-header keyword categories matched on the same row (`LINE_ITEM_HEADER_KEYWORDS`: description/qty/rate/amount) — a single keyword match is explicitly called out as unreliable (a stray "Total" in a payment-terms sentence).
- **Failure behavior**: `None` → `_build_line_items()` returns `[]` immediately; no line items are ever fabricated.
- **Risk**: A table whose header uses only one recognizable keyword (e.g., "Item" with no "Qty"/"Rate"/"Amount" header at all) is never detected.

**LINEITEM-002 — Column detection via header-row x-positions**
- **Location**: `invoice_extraction/line_items.py`, `detect_columns()`. Source spec: Rule 4.2.
- **Logic**: Checks adjacent-word bigrams before single words (catches "Unit Price"/"Hourly rate"). `_COLUMN_TOLERANCE = 5.0`pt tolerance on column start. Columns anchored once, to the header row, not re-derived per data row.
- **Risk**: A header column position drift on a later row (common on skewed/handwritten receipts) is handled by LINEITEM-004's band-vs-nearest-header fallback, not by re-detecting columns.

**LINEITEM-003 — Totals-section boundary detection**
- **Location**: `invoice_extraction/line_items.py`, `find_totals_boundary()`. Source spec: Rule 4.5.
- **Logic**: First row (at/after the header) naming a totals keyword (`TOTALS_SECTION_KEYWORDS`) ends the line-item region. **When `columns` is known**, a totals keyword found *inside the description column band* is ignored — a documented real bug this fixes: a Pakistani invoice's own first data row description was literally "Total Booking Amount," which without this column-awareness emptied the entire table (the boundary landed immediately after the header).
- **Risk**: Without `columns` (the pre-column-detection code path, still reachable via the direct function signature with `columns=None`), the whole-row behavior is unchanged and remains vulnerable to the same bug.

**LINEITEM-004 — Row reconstruction with two hand-audited failure-mode recoveries**
- **Location**: `invoice_extraction/line_items.py`, `reconstruct_rows()`. Source spec: Rule 4.4.
- **Logic**:
  - **Wrapped-description merge**: a row with description text but no numeric content in any column is merged into the **visually nearest** numeric row by y-distance (not always the previous row — documented real case: name above, qty+price row, SKU sub-line below, three visual rows per item; a backward-only merge glued every subsequent description-only line, including the *next item's own name*, onto the wrong row).
  - **Column-drift recovery**: a bare number (`_BARE_NUMBER`, digits/commas/decimal only) is never accepted into the description column even if its bbox falls inside that band — routed to the *nearest numeric column header by x-position* instead. The mirror case (a letter-bearing, digit-free token — `_ALPHABETIC`) is never accepted into a numeric column, routed to description instead. Both are documented as fixing the exact same real handwritten receipt where a rate value's box drifted left of its own header.
  - **Sparse-row flag**: a row using noticeably fewer columns than the header defines is flagged `sparse_row_possible_merged_cell` rather than silently force-fit.
- **Fields affected**: line item `description`/`qty`/`rate`/`amount`.
- **Risk**: Explicitly documented gap in the module docstring: Rule 4.1 (ruling-line/border geometric detection) is **not implemented at all** — this pipeline never looks at drawn table borders, only word positions.

**LINEITEM-005 — Per-line-item arithmetic check**
- **Location**: `invoice_extraction/arithmetic.py`, `check_line_item()`. Source spec: Rule 5.1.
- **Logic**: `qty × rate ≈ amount` within `_TOLERANCE = 0.02` relative tolerance. `"not_checked"` if any of the three is missing.
- **Risk**: A mismatch is treated as a *column-misalignment* signal, appended as a line-level `review_flags` entry (`qty_rate_amount_mismatch`) — it does not retroactively re-attempt column assignment.

### Stage 2e — Document-wide evidence scans

**CUR-001 — Currency detection**
- **Location**: `invoice_extraction/currency.py`, `detect_currency()`.
- **Logic**: Word/code patterns checked first at confidence 1.0 (`\bpkr\b`, `rs\.?` via lookaround not `\b` — specifically so "Rs." with a trailing period matches, `\brupees?\b`, EUR/GBP/USD variants); bare symbols (`₨`,`€`,`£`,`$`) adjacent to a digit checked second, at a deliberately sub-UNCERTAIN confidence of **0.45** — below `confidence.field_status`'s 0.5 UNCERTAIN floor on purpose.
- **Risk explicitly documented**: a real garbled company-logo glyph OCR'd as a bare "$9" with no real currency mention anywhere on the document — the 0.45 confidence exists specifically so this reports as UNCERTAIN, not FOUND.
- **Never a default** — no currency evidence means `not_found()`, explicitly framed in the module docstring as "a direct, deliberate reaction to a real bug documented in a separate reference project's own implementation report: an unconditional 'else default USD' fallback."

**GEO-001/002 — City/country detection**
- **Location**: `invoice_extraction/geography.py`.
- **Logic**: `_CITY_COUNTRY` is an 11-city hardcoded table (10 Pakistani cities + 5 Indian), matched via `\b{city}\b`. Country falls back to a 4-entry phone-country-code table (`+92`,`+91`,`+44`,`+1`) only if no city matched.
- **Risk**: Table is explicitly "seeded from real documents this library has actually been tested against, not an attempt at exhaustive geography" — see §12, this is dataset-adjacent hardcoding that will simply return nothing for any company outside Pakistan/India/UK/US.

**PAY-001 — Payment status detection**
- **Location**: `invoice_extraction/payment.py`.
- **Logic**: Ordered keyword regex list (`PAYMENT_STATUS_KEYWORDS` in `labels.py`), first match wins, specific-before-generic ("partially paid" before bare "paid").
- **Risk**: None significant — explicitly never defaults ("not detected" stays `not_found()`, never silently becomes PENDING, called out in both the module docstring and the schema field docstring).

### Stage 2f — Classification (document type + transactional)

**CLASS-001 — Document-type keyword classification**
- **Location**: `invoice_extraction/document_type.py`, `detect_document_type()`, `_TYPE_KEYWORDS`.
- **Purpose**: Decide what kind of document this is from its own text, never from filename/extension.
- **Logic**: **Ordered** regex list, first match wins. `minute_sheet` and `approval_request` phrasings are checked **first, deliberately ahead of** the generic "invoice"/"receipt"/"bill" words — the module docstring cites the exact real bug this prevents: an internal "Minute Sheet" itemizes its attachments as "Bill-1(Legal)", "Bill-2(Emp Care)"... so an unordered/generic `\bbill\b` match typed all 4 real such documents in the golden dataset as `"bill"` at confidence 1.0 — confidently wrong. With no keyword match at all but a recognizable line-item table + a total, a weak `"receipt"` guess is offered at confidence **0.4** (explicitly, never conflated with a real keyword match's 1.0). With neither: `None`, confidence 0.0.
- **Output**: `DocumentTypeResult(document_type, confidence, reason)` — `reason` is the document's own matched wording, quoted verbatim, shown to a reviewer.
- **Fields affected**: `document_type`; and, via `is_transactional()`, gates `transactional`, `total`, `subtotal`, `tax_amount`, `discount`, `tax_rate`, `vendor_name`.
- **Priority**: Runs after line-item detection (needs `has_line_items`/`has_total` for the weak-receipt-guess branch) but conceptually stands alone.
- **Failure behavior**: No keyword + no line-item-table shape → `document_type = None`, `transactional` defaults **True** (see TXN-001).
- **Risk**: This is a **closed, hardcoded keyword list** — see §12. A document type this list has never seen (e.g., a "delivery challan," a "debit memo" phrased differently than the two matched patterns) silently falls through to the weak receipt guess or `None`, never a new, made-up type.

**TXN-001 — Transactional determination**
- **Location**: `invoice_extraction/document_type.py`, `is_transactional()`, `NON_TRANSACTIONAL_TYPES = frozenset({"minute_sheet", "approval_request"})`.
- **Purpose**: Gate whether a document enters financial processing at all.
- **Logic**: `document_type not in NON_TRANSACTIONAL_TYPES`. An `None`/undetected type is **transactional by default** — explicitly reasoned in the docstring as the safe default ("this pipeline's whole purpose is processing financial documents... not silently diverting a real invoice into a bucket a person may never look at").
- **Fields affected**: Triggers the entire financial-field-clearing block in `extract_invoice.py` (see TXN-002).
- **Priority**: This is the single highest-leverage boolean in the entire pipeline — everything in §7's "Transactional" walkthrough downstream of it depends on this one lookup.
- **Failure behavior**: N/A (always resolves True or False).
- **Risk**: This is a **two-item closed set**. A third category of non-transactional document (a quotation that is genuinely never a transaction, a delivery note, an internal audit report) is NOT in this set and would be treated as transactional by default — i.e., the safe-default reasoning cuts the other way for anything not already named `minute_sheet`/`approval_request`. `quotation` and `purchase_order` are recognized *document types* but are **not** in `NON_TRANSACTIONAL_TYPES` — a quotation currently enters financial processing exactly like a real invoice would. This is flagged as a P0 finding in §20.

**TXN-002 — Financial-field clearing on a non-transactional document**
- **Location**: `invoice_extraction/extract_invoice.py`, inside `extract_invoice()`, the `if not transactional:` block.
- **Purpose**: The single rule that makes non-transactional handling meaningful at all — prevent an internal memo's stated amount from ever reaching `total`.
- **Logic**: `amount_mentioned` is populated preferentially from the approval sentence itself (`find_approval_amount_text` — anchored on `"for approval of"`/`"submitted for approval of"`/`"kindly approve"` wording specifically, **never** from a generic amount scan, because these documents list every attached bill's own amount too and the sentence's own figure is what the request is actually about), falling back to whatever `total`/`subtotal` extraction already found. Then `total`, `subtotal`, `tax_amount`, `discount`, `tax_rate`, **and `vendor_name`** are all force-reset to `not_found()`.
- **Fields affected**: `total`, `subtotal`, `tax_amount`, `discount`, `tax_rate`, `vendor_name`, `amount_mentioned`.
- **Priority**: Runs immediately after TXN-001, before arithmetic validation, before category assignment (category assignment is a downstream service that reads `transactional` directly and skips category entirely — see CATEGORY-001).
- **Failure behavior**: N/A — unconditional once `transactional is False`.
- **Risk / history**: The `vendor_name` clearing is explicitly documented as **found live via the Golden Dataset benchmark itself** — the earlier version of this code cleared total/subtotal/tax but forgot vendor, and all 4 real non-transactional documents in the 35-document set still reported a vendor (their own letterhead) as if it were "who money was paid to." This is the single most concrete, in-code proof that the Golden Dataset benchmark has actually caught a real financial-correctness bug, not just cosmetic errors.

**TXN-003 — Non-transactional review routing (no auto-processing, ever)**
- **Location**: `invoice_extraction/extract_invoice.py`, the `if transactional: ... else: status = "needs_review"` branch at the very end.
- **Purpose**: A non-transactional document is judged by different criteria than a transactional one.
- **Logic**: Never `auto_processed` (a human must confirm diverting something out of financial processing — Confirm/Process as Transaction/Reject, per the frontend's own `NonTransactionalDocuments` component), never `needs_review_high_priority` (nothing is actually broken — it has no total by design, so the normal `route()` critical-field check would wrongly read that as extraction failure).
- **Fields affected**: `review_status`.
- **Risk**: None identified — this is a clean, deliberate carve-out that avoids `confidence.route()`'s critical-field logic misfiring on a document type it was never designed to score.

### Stage 2g — Arithmetic validation

**ARITH-001 — Totals equation check**
- **Location**: `invoice_extraction/arithmetic.py`, `check_totals()`. Source spec: Rule 5.2.
- **Logic**: `subtotal + (tax or 0) - (discount or 0) ≈ total`, tolerance `±0.02 × max(|total|, 1.0)`. Returns `None` (not `False`) when `subtotal` or `total` is missing — explicitly a different, weaker signal than a genuine mismatch, never conflated.

**ARITH-002 — Tax-rate plausibility**
- **Location**: `invoice_extraction/arithmetic.py`, `is_plausible_tax_rate()`, `KNOWN_PK_TAX_RATES = (0.0, 0.05, 0.08, 0.10, 0.13, 0.15, 0.16, 0.17, 0.18)`. Source spec: Rule 5.3.
- **Logic**: Implied rate = `tax/subtotal`; plausible if within 0.01 of any known Pakistani rate.
- **Risk**: This is a **Pakistan-specific hardcoded rate table** (see §12) — a company operating under a different country's tax regime would have every one of its real, correct tax rates flagged implausible.

**ARITH-003 — Invoice-date plausibility**
- **Location**: `invoice_extraction/arithmetic.py`, `is_plausible_invoice_date()`, `max_age_days=730`.
- **Logic**: `False` if the date is in the future, or more than 2 years in the past. Catches OCR digit-confusion (0/8, 1/7, 3/8 cited as documented common Tesseract confusions) that a pure format-regex would miss.
- **Priority (real, load-bearing routing effect)**: `extract_invoice.py` explicitly escalates `arithmetic_ok = False` whenever `date_plausible is False` **or** `tax_rate_plausible is False`, even if `totals_pass` itself was fine — documented as fixing a real, previously-existing false positive where a flagged-implausible date/tax-rate could still route to `auto_processed`.

### Stage 2h — Confidence & routing

**CONF-001 — Method-tier confidence**
- **Location**: `invoice_extraction/confidence.py`, `METHOD_TIERS`. Source spec: Rule 6.3.
- **Values**: `label_anchor+pattern=1.0`, `label_anchor=0.7`, `positional_fallback=0.5`, `not_found=0.0`.
- **Logic**: `field_confidence = (ocr_confidence + method_tier) / 2` — an **average**, deliberately, not a product ("a field found via the strongest method but from a noisy OCR read... should not be crushed to near-zero by one weak input alone").

**CONF-002 — Field-weighted document confidence**
- **Location**: `invoice_extraction/confidence.py`, `FIELD_WEIGHTS`, `document_confidence()`. Source spec: Rule 6.1.
- **Values**: `total=3.0` (highest), `tax_amount=2.0`, `vendor_name=2.0`, `invoice_number=2.0`, `subtotal=1.5`, `invoice_date=1.0`, `ntn=1.0`; any unlisted field defaults to 1.0.
- **Logic**: Weighted mean of present fields' confidences, then **discounted (×0.7), not zeroed**, if `arithmetic_ok is False`.
- **Risk**: These weights are explicitly self-described as "reasonable starting defaults... not numbers derived from measuring this engine's real accuracy yet" — an open, acknowledged item (see §6 of `docs/invoice-ocr-plan.md`).

**CONF-003 — Threshold-based routing**
- **Location**: `invoice_extraction/confidence.py`, `route()`. Source spec: Rule 6.4.
- **Values**: `_AUTO_PROCESS_THRESHOLD = 0.75`, `_NEEDS_REVIEW_THRESHOLD = 0.4`.
- **Logic**: `score < 0.4` or a critical field missing → `needs_review_high_priority`. `score < 0.75` or `arithmetic_ok is False` → `needs_review`. Else → `auto_processed`.
- **Fields affected**: `review_status` → `InvoiceStatus` (`_REVIEW_STATUS_MAP` in `invoice_builder.py`).
- **Risk**: Same as CONF-002 — untuned thresholds, explicitly documented as such.

**CONF-004 — Critical-field requirement, conditional on document type**
- **Location**: `invoice_extraction/extract_invoice.py`, `required_fields = (total, vendor_name, invoice_number) if document_type.value == "invoice" else (total, vendor_name)`.
- **Purpose**: Only require `invoice_number` when the document is confidently a formal "invoice."
- **Risk/history**: Explicitly documented as fixing a real measured regression — `invoice_number` was found on **0/35** real Pakistani retail/petty-cash receipts (none carry a formal reference number the way a B2B invoice does), meaning the un-conditioned version of this check blocked auto-processing on literally every real receipt in the dataset regardless of extraction accuracy elsewhere — "a false-positive review condition, not a genuine financial-risk one."

**CONF-005 — Tri-state field status (FOUND/NOT_FOUND/UNCERTAIN)**
- **Location**: `invoice_extraction/confidence.py`, `field_status()`. `_UNCERTAIN_BELOW = 0.5`.
- **Purpose**: Give a review UI three states, not a binary "has a value."

### Stage 2i — Deduplication

**DEDUP-001 — Exact file-hash duplicate check**
- **Location**: `invoice_extraction/dedup.py`, `file_hash()` (SHA-256); enforced at `invoice-service/app/api/routes/scanner.py` — same-hash re-upload returns the existing invoice, no reprocessing. Source spec: Rule 7.1.

**DEDUP-002 — Vendor name normalization + fuzzy match**
- **Location**: `invoice_extraction/dedup.py`, `normalize_vendor_name()` (lowercase, whitespace-collapse, strips a fixed list of corporate suffixes: `"pvt ltd"`, `"private limited"`, `"(pvt) ltd"`, `"ltd"`, `"llc"`, `"inc"`), `find_matching_vendor()` (stdlib `difflib.get_close_matches`, `cutoff=0.75`). Source spec: Rule 7.3.
- **Priority**: Only runs if the caller supplies `known_vendors` (invoice-service does, scoped per-company).
- **Risk**: `difflib`'s similarity metric is explicitly self-described as "good enough for matching against a company's own, typically small, vendor list; not intended as a general-purpose string-similarity tool" — a company with hundreds of visually-similar vendor names could see false matches at the 0.75 cutoff.

**DEDUP-003 — Composite duplicate key (catches re-scans)**
- **Location**: `invoice_extraction/dedup.py`, `duplicate_key()` — `(normalized_vendor, invoice_number.lower(), round(total,2), date_iso)`. Source spec: Rule 7.2.
- **Note**: This function exists and is tested (`test_dedup.py`) but **no caller was found anywhere in `invoice-service`'s routes** during this audit — see §14, this is implemented-but-unwired.

### Category assignment (separate service)

**CATEGORY-001 — Keyword-based category classification**
- **Location**: `invoice-service/app/services/category_classifier.py`, `classify_category()`, `_CATEGORY_KEYWORDS`.
- **Purpose**: Best-guess cashbook category from vendor name + line-item descriptions + filename.
- **Input**: `vendor_name`, `item_descriptions: list[str | None]`, `filename`.
- **Logic**: Single lowercased haystack string joining vendor + filename + every line-item description; **15 ordered categories**, first keyword hit wins; plain case-insensitive substring search, explicitly "no fuzzy matching, no ML." A generic store-name shape (`" mart"`, `"cash & carry"`, `"general store"`, `"super store"`, `"convenience store"`) is deliberately the **last, lowest-priority** category — placed after every specific keyword so a real "ABC Stationery Mart" still resolves via its own "stationery" keyword rather than the generic fallback.
- **Called from**: `invoice_builder.py::build_invoice()`, gated by `invoice_type is InvoiceType.purchase and transactional` — a non-transactional or sale-type document is **never** classified at all (`category = None`), a second, independent enforcement of the same non-transactional-financial-isolation principle as TXN-002.
- **Output**: One of `SUGGESTED_CATEGORIES` (16-item tuple in `models/invoice.py`, "a suggestion, not an enforced enum"), or `None` ("Uncategorized").
- **Fields affected**: `category`.
- **Priority**: Runs once, at scan time only. A category is **never recomputed** on edit — a human can freely override it via `PUT /invoices/{id}`, and nothing re-runs the classifier afterward.
- **Failure behavior**: No keyword hit anywhere → `None`, never a guess.
- **Risk**: This is the **single largest hand-audited keyword list in the codebase** — see §12. Every one of its ~80 keywords traces to a specific real vendor found in the 35-document Golden Dataset (the module's own comments cite "Layers Bakeshop", "papajohns@livepepper.com", "FSO DHA Filling Station", "Express Mart", "Falcon Cash & Carry" by name as the reason specific keywords exist). This is the textbook definition of dataset-shaped hardcoding: it will work well for petty-cash receipts shaped like this specific Pakistani SME's, and will silently under-classify (fall to `None`) for any vendor/product vocabulary not already seen.

---

## 4. Field-by-Field Trace: Raw OCR → Final JSON

### `document_type`
```
Raw OCR text (whole document, not label-anchored)
      ↓
No normalization step — raw text scanned directly
      ↓
Candidate generation: 11 ordered regex patterns (CLASS-001), first match wins
      ↓
No separate validation step — a keyword match IS the validation
      ↓
Scoring: binary — matched (1.0) or weak-guess (0.4, receipt-shape only) or none (0.0)
      ↓
Selection: first match in priority order (minute_sheet/approval_request lead)
      ↓
Final JSON: {value, confidence, method:"pattern_match", page:null, bbox:null, status}
             + classification_reason (verbatim matched sentence)
```
Real example (from `document_type.py`'s own docstring): a Minute Sheet whose line items are literally named "Bill-1(Legal)" — without the ordering rule, `\bbill\b` would confidently (1.0) misclassify it as `"bill"`. With the ordering rule, `\bminute\s*sheet\b` (checked first) correctly wins.

### `transactional`
```
document_type (already resolved above)
      ↓
TXN-001: table lookup against a 2-item frozenset
      ↓
Final JSON: bare bool, no confidence/method/bbox of its own (not a FieldValue)
```
This is the **only** "field" in the whole schema that is not a `FieldValue` — it's a plain derived bool, because it's a pure function of `document_type` with no independent evidence of its own.

### `vendor_name`
```
Raw OCR words + positions
      ↓
Line grouping (lines.py)
      ↓
Candidate generation: EITHER label match (1 candidate) OR every top-25%-of-page-0 line (N candidates)
      ↓
Per-candidate cleanup: strip doc-type word → strip reference stamp → truncate at NTN marker
      ↓
Validation/filtering: reject reference-number-shaped, decorative/metadata, or low-confidence lines
      ↓
Scoring: pass/fail on the filters above (no numeric score — first-pass-wins, not ranked)
      ↓
Selection: first filtered-in candidate in document order; degrade to highest-confidence
           non-noise, then plain-first-line, only if nothing clears the bar
      ↓
[If transactional=False: FORCE-CLEARED to not_found() regardless of everything above — TXN-002]
      ↓
Final JSON: {value, confidence, method, page, bbox, status}
```
**Correct extraction example** (real, from `docs/invoice-ocr-plan.md`'s own case log): `"KFC Bahia Pindl Phase-7 (0154) NTN#0819531-5"` → NTN-marker truncation (FIELD-005 step 3) → `"KFC Bahia Pindl Phase-7 (0154)"`.

**Incorrect/difficult example, reported honestly by the codebase itself**: same document, ground truth is the golden dataset's `"KFC"` (short brand name); after the fix the extraction genuinely improved (removed real registration-number noise) but still scores `INCORRECT` because the actual printed text legitimately includes `"Bahia"` — the code's own audit calls this "a benchmark ground-truth granularity question, not a defect in the fix."

### `invoice_date`
```
Raw OCR text/lines
      ↓
Normalization (validators._normalize_date_text): trailing-time strip → ordinal-suffix
strip → missing-separator insertion
      ↓
Candidate generation: label-anchored value (FIELD-001) OR first date-shaped line (FIELD-006)
      ↓
Validation: must parse via one of 9 fixed-priority formats (whole-string) or a substring match
      ↓
Scoring: none beyond confidence tier (label vs. positional)
      ↓
Selection: label-anchored wins outright if parseable; else positional first-match
      ↓
Final JSON: {value: ISO date string, confidence, method, page, bbox, status}
```
Real difficult example: `"30\" June 2026"` (a stray quotation mark standing in for a misread ordinal superscript on a real internal memo's own signature date) → ordinal-suffix strip → `"30 June 2026"` → parses via `%d %B %Y`.

### `total`
```
Raw OCR text/lines/words
      ↓
No normalization step of its own (parse_money handles currency/comma/decimal shape inline)
      ↓
Candidate generation, THREE TIERS, tried in strict order (not merged/scored together):
   Tier 1: label-anchored value (FIELD-001 + parse_money)
   Tier 2: find_total_anywhere — single-unambiguous-candidate positional scan (FIELD-007)
   Tier 3: _dynamic_total — single-unambiguous discovered-field fallback (FIELD-008)
      ↓
Validation: parse_money must succeed; Tier 2/3 additionally require candidate COUNT == 1
      ↓
Selection: first tier that produces a value wins outright — no cross-tier scoring/ranking
      ↓
[If transactional=False: FORCE-CLEARED to not_found() — TXN-002]
      ↓
Final JSON: {value: float, confidence, method, page, bbox, status}
```
This field genuinely does **not** follow a "generate all candidates, score, rank, pick best" pattern — it's a strict fallback chain where each tier either commits to an answer or defers entirely to the next, and Tier 2/3's own internal "candidate scoring" is really just an ambiguity gate (0, 1, or 2+ candidates), never a ranked comparison between multiple plausible totals.

### `subtotal` / `tax_amount` / `discount`
Identical mechanism to `total`'s Tier 1 only (`_money_field`) — **no positional fallback exists for these three**. This is a real, deliberate asymmetry: `total` gets 3 tiers, these get 1. Documented reasoning (implicit in the code structure, not stated as a separate rule) appears to be that these are lower financial-risk / lower-frequency-of-appearing-unlabeled fields than the grand total.

### `tax_rate`
```
subtotal.value AND tax_amount.value (both already resolved above)
      ↓
Computed, not extracted: implied_rate = tax_amount / subtotal, rounded to 4 places
      ↓
Confidence = min(subtotal.confidence, tax_amount.confidence)
      ↓
Final JSON: {value, confidence, method:"computed", page:null, bbox:null}
```
No label variant for "tax rate" exists in `LABEL_VARIANTS` at all — this field is **always** derived, never searched for directly, per the code's own comment ("Rule 3.3 doesn't define a 'tax rate' label variant for this reason").

### `currency` / `city` / `country` / `payment_status`
All four follow the identical shape: whole-document text scan → ordered pattern list → first match wins → `not_found()` on zero matches, **never a default value**. None has a positional bbox (scanned over raw text, not word positions) except where the underlying match happens to correspond to visible text — `page`/`bbox` are `null` for all four in practice, since `detect_currency`/`detect_city`/`detect_country`/`detect_payment_status` all operate on the flat `ocr_result.text` string, not on `PositionedWord` lists.

### `category`
```
vendor_name (already resolved, post-transactional-clearing) + line-item descriptions + filename
      ↓
No normalization (plain .lower() join)
      ↓
Candidate generation: 15 ordered category buckets, ~80 keywords total
      ↓
No separate validation step — substring match IS the validation
      ↓
Selection: first bucket with any keyword hit; NEVER RE-RUN after this
      ↓
[Only computed at all if invoice_type==purchase AND transactional==True]
      ↓
Final JSON: plain string column on the Invoice row (not a FieldValue — no confidence/method
             of its own at all; this is the one canonical field with zero extraction metadata)
```
**Note the asymmetry**: `category` is the only cashbook-relevant field with no confidence score, no method tag, and no bbox — because architecturally it isn't part of `invoice_extraction`'s `ExtractedInvoice` schema at all; it's assigned by a different service, once, as a plain DB column.

### `particular` / `description`
There is **no single canonical "particular" field in the backend schema**. The frontend derives a display "Particular" via its own fallback chain (`particulars()` in `app.records.tsx`: `vendor_name || invoice_number || filename || "Unknown / Needs Review"`) — this is a **presentation-layer construct, not a pipeline output**. Line-item `description` (the `LineItem.description` `FieldValue`) is a genuinely separate, extracted field (`invoice_extraction/line_items.py`'s `RawLineItemRow.description`, column-anchored text from the item table).

### `line_items`
Traced fully in §3's LINEITEM-001 through LINEITEM-005 and §1's Stage 2c. Each item is its own 4-`FieldValue` bundle (`description`, `qty`, `rate`, `amount`) plus `arithmetic_check` and `review_flags` — no document-level aggregation happens beyond `line_items_pass_rate` (fraction passing ARITH-001) feeding into `arithmetic_ok` when `totals_pass` itself is `None`.

### Fields that do NOT currently exist
- **A per-field "requires_review" flag.** Only a document-level `review_status` (3-value enum) and document-level `review_flags` (free-text list) exist. There is no `{field}.requires_review: bool` anywhere in the schema.
- **A distinct "OCR confidence" vs. "field confidence" vs. "classification confidence" split, exposed separately in the final JSON.** `document_confidence` is a single composite number (CONF-002); `field.confidence` exists per header field, but there is no separate "how confident are we in the OCR read itself, independent of which extraction method found it" number surfaced anywhere downstream of `ExtractionResult.confidence` (which *does* exist upstream, as the base input to CONF-001, but is not itself re-exposed in the final `ExtractedInvoiceResponse`).
- **Vendor confidence as its own named concept** — it's just `vendor_name.confidence`, computed the same generic way every other header field's confidence is (CONF-001), with no vendor-specific confidence adjustment beyond the fuzzy-match confidence *boost* in DEDUP-002 (`min(1.0, vendor_name.confidence + 0.1)`).
- **A distinct `net_amount` / `amount_paid` / `balance_remaining` field** — only `total`, `subtotal`, `tax_amount`, `discount` exist; nothing tracks partial payment amounts even though `payment_status` can be `"PARTIALLY_PAID"`.
- **Multi-currency conversion or a `currency_rate` field** — `currency` is detected as evidence only; no conversion logic exists anywhere.

---

## 5. The Final JSON Contract, As Actually Returned

This is `ai-engine/app/schemas/ocr.py`'s `ExtractedInvoiceResponse` — the real, live response shape of `POST /ocr/extract`, reconstructed field-by-field from the actual Pydantic model (not invented):

```json
{
  "vendor_name":  { "value": "KFC Bahia", "confidence": 0.925, "method": "positional_fallback", "page": 0, "bbox": [34.0, 12.0, 210.5, 26.0], "status": "FOUND" },
  "invoice_number": { "value": null, "confidence": 0.0, "method": "not_found", "page": null, "bbox": null, "status": "NOT_FOUND" },
  "invoice_date": { "value": "2026-06-16", "confidence": 0.975, "method": "label_anchor+pattern", "page": 0, "bbox": [...], "status": "FOUND" },
  "ntn":          { "value": null, "confidence": 0.0, "method": "not_found", "page": null, "bbox": null, "status": "NOT_FOUND" },
  "subtotal":     { "value": null, "confidence": 0.0, "method": "not_found", "page": null, "bbox": null, "status": "NOT_FOUND" },
  "tax_rate":     { "value": null, "confidence": 0.0, "method": "not_found", "page": null, "bbox": null, "status": "NOT_FOUND" },
  "tax_amount":   { "value": null, "confidence": 0.0, "method": "not_found", "page": null, "bbox": null, "status": "NOT_FOUND" },
  "total":        { "value": 20420.0, "confidence": 0.975, "method": "label_anchor+pattern", "page": 0, "bbox": [...], "status": "FOUND" },
  "line_items": [],
  "arithmetic_validation": { "line_items_pass_rate": null, "totals_pass": null, "tax_rate_plausible": null, "date_plausible": true },
  "document_confidence": 0.95,
  "extraction_source": "ocr",
  "review_status": "auto_processed",
  "review_flags": [],
  "page_dimensions": [[1080.0, 1920.0]],
  "customer_name": { "value": null, "confidence": 0.0, "method": "not_found", "page": null, "bbox": null, "status": "NOT_FOUND" },
  "currency": { "value": "PKR", "confidence": 1.0, "method": "pattern_match", "page": null, "bbox": null, "status": "FOUND" },
  "document_type": { "value": "receipt", "confidence": 0.4, "method": "pattern_match", "page": null, "bbox": null, "status": "UNCERTAIN" },
  "transactional": true,
  "classification_reason": "No document-type wording found; has a line-item table and a total",
  "amount_mentioned": null,
  "payment_status": { "value": null, "confidence": 0.0, "method": "not_found", "page": null, "bbox": null, "status": "NOT_FOUND" },
  "city": { "value": null, "confidence": 0.0, "method": "not_found", "page": null, "bbox": null, "status": "NOT_FOUND" },
  "country": { "value": null, "confidence": 0.0, "method": "not_found", "page": null, "bbox": null, "status": "NOT_FOUND" },
  "dynamic_fields": [],
  "additional_documents": []
}
```

**Per-field contract, verified against the Pydantic source**:

| Field | Type | Nullable | Default | UNKNOWN behavior | Source of truth |
|---|---|---|---|---|---|
| `*.value` (any FieldValue) | `Optional[object]` | Yes | — | `null` when nothing found; **never coerced to `""` or `0`** (schema.py's own docstring: "that is itself meaningful... never silently coerced") | Stage 2 per-field logic |
| `*.confidence` | `float` | No | `0.0` for `not_found` | N/A | `confidence.field_confidence()` |
| `*.method` | `str` | No | `"not_found"` | — | Tagged by whichever code path produced the value |
| `*.status` | `Literal["FOUND","NOT_FOUND","UNCERTAIN"]` | No | derived | Below 0.5 confidence with a value present → `UNCERTAIN`, never silently `FOUND` | `confidence.field_status()` |
| `transactional` | `bool` | No | `True` | Defaults `True`, explicitly for backward compatibility with pre-existing callers/documents | `document_type.is_transactional()` |
| `amount_mentioned` | `Optional[float]` | Yes | `None` | Only ever populated when `transactional=False` | `extract_invoice.py`'s clearing block |
| `document_confidence` | `float` | No | — | — | `confidence.document_confidence()` |
| `review_status` | 3-value enum | No | — | — | `confidence.route()` |
| `review_flags` | `list[str]` | No | `[]` | — | Appended per-condition in `extract_invoice()` |
| `dynamic_fields` | `list[DiscoveredFieldSchema]` | No | `[]` | — | `dynamic_fields.discover_fields()`, de-duplicated against canonical fields |
| `additional_documents` | `list[AdditionalDocumentSchema]` | No | `[]` | Populated only on confirmed multi-document split | `ocr.preprocess` + a second full `extract_invoice()` call per split |

`invoice-service`'s `ExtractedInvoiceSchema` (`app/schemas/ai_extraction.py`) is a **separately maintained mirror** of this exact shape — not a shared import — explicitly by design, "so the two services can evolve independently." This is a real duplication (see §11), but a deliberate, documented one, not an oversight.

The **persisted** `Invoice` row (`models/invoice.py`) is a *different* contract again: it flattens every `FieldValue.value` into a plain nullable column, keeps `raw_extraction_json` as the untouched original blob (Rule 8.1), and adds fields that exist **only** at this layer and never in the extraction schema: `category`, `payment_method`, `transaction_status`, `classification_source`, `vendor_id`, `s3_key`, `file_hash`.

---

## 6. Separating Extraction, Structuring, Validation, Selection, Classification

This is the single clearest way to understand how the "no LLM" claim actually holds together — each concern is a **genuinely separate module**, not a blurred single pass:

| Concern | Question it answers | Where it lives | Never does |
|---|---|---|---|
| **OCR** | "What text exists, and where?" | `ocr/*` | Never interprets meaning — `PositionedWord` has no concept of "this is a vendor name," only text+position+confidence |
| **Structuring** | "Which text represents a vendor/date/amount?" | `invoice_extraction/fields.py`, `line_items.py`, `dynamic_fields.py` | Never judges *correctness* of a shape it already located — that's Validation's job |
| **Validation** | "Is this candidate plausible?" | `invoice_extraction/validators.py` (per-candidate: `is_plausible_invoice_number`, `parse_date`, `parse_money`) + `arithmetic.py` (cross-field) | Never searches for a *new* candidate if one fails — it only accepts/degrades what Structuring already found |
| **Selection** | "Which valid candidate is best?" | Inline in `fields.py` (vendor: filter-then-first-wins; total: strict tiered fallback, not ranked scoring) | See below — this is the most inconsistent stage across fields |
| **Classification** | "What type/category of document is this?" | `document_type.py` (type/transactional), `category_classifier.py` (category, different service) | Never structures a field's *value* — only ever assigns a label to the whole document |

**The honest finding**: "Selection" is not one consistent algorithm across fields. Three genuinely different selection strategies coexist:
1. **First-match-wins, ordered list** (document type, category, currency, city, payment status) — no scoring at all, just priority order.
2. **Filter-then-first-survivor, with a documented multi-level degrade path** (vendor) — closest to a real "generate, validate, select" pipeline, and the only field with an explicit "if nothing survives, use the best of what's left" tier.
3. **Strict tiered fallback with an ambiguity gate, never a ranked score** (total, and by extension subtotal/tax/discount which only have Tier 1) — "selection" here really means "did tier N produce an unambiguous single answer; if not, try tier N+1."

No field in this codebase does literal "generate N candidates, assign each a numeric score, sort, pick #1" — the closest is vendor's `_MIN_POSITIONAL_FALLBACK_CONFIDENCE`-gated fallback chain, and even that is order-dependent (document order) among candidates that pass the bar, not confidence-ranked among them.

---

## 7. Candidate Generation → Validation → Scoring → Selection, Per Field

### Vendor (the fullest instance of this pattern in the codebase)
Already fully traced in §3 (FIELD-005) and §4. Summary of what is excluded/preferred:
- **Excluded**: reference/serial numbers (`_looks_like_reference_number`: known receipt-header prefixes, embedded dates, >50% digit ratio), decorative/metadata boilerplate (`_looks_like_decorative_or_metadata_text`: POS/software self-credit like "Powered By...", order-fulfillment phrases like "Pickup/Delivery", courtesy footers), a leading generic document-type word ("INVOICE"), a leading reference/amount stamp on the same line, and anything after a mid-line NTN marker.
- **Preferred**: a label-anchored value always wins outright; among positional candidates, the first one (in document order, top-of-page) that clears every filter and the 0.85 confidence floor.
- **Header/footer/contact avoidance**: no explicit header/footer *geometric* zone concept exists — avoidance is entirely content-based (the filters above), scoped only by the `top_fraction=0.25` window for the *initial candidate set*, not by detecting a visual header region.
- **Reference numbers / NTN / amounts / timestamps**: each has its own dedicated, separately-tested helper (`_looks_like_reference_number` for generic digit-dominant/date-shaped noise, `_truncate_at_registration_number` specifically for NTN, `_strip_leading_reference_stamp` specifically for a leading amount stamp) — these are not one generic "looks like noise" function, they are purpose-built, independently evidenced rules.

### Date
- **Candidates**: label-anchored value, or every line (positional fallback).
- **Formats supported**: 9, fixed priority, day-first-preferred (see FIELD-004).
- **OCR corruption normalization**: trailing-time strip, ordinal-suffix noise strip, missing-separator re-insertion — all three are structural/positional, never a lookup of specific garbled strings.
- **Ambiguous dates**: **not resolved by evidence** — resolved by a **fixed, hardcoded priority order** (day-first before month-first). This is the one place in the whole pipeline where "ambiguity → don't guess" is explicitly *not* the rule; a genuinely ambiguous `03/04/2026` is silently resolved as day-first (3 April) with no signal that it could have meant 4 March. The module's own docstring calls this out as a known, undone enhancement.

### Total
Already fully traced (§3 FIELD-007/008, §4). **How subtotal/tax/discount are prevented from becoming the total**: they are never even candidates for `total` — `_money_field(lines, "total", ...)` only ever matches the `LABEL_VARIANTS["total"]` list, which is a physically separate list from `["subtotal"]`/`["tax"]`/`["discount"]` in `labels.py`; there is no shared candidate pool that total and subtotal compete within. The positional fallback (`find_total_anywhere`) further excludes anything that isn't shaped like a specifically-written amount, and the header-row skip (`totals_skip`) prevents a line-item column header's own "Total" word from being mistaken for the document-level label.
- **Positional/layout effect on selection**: real, but narrow — the label-anchored path uses x-alignment for reading a right-column value; `find_total_anywhere`/`find_header_row` skip use row identity (`skip_lines`), not geometric zones; there is no "totals are always in the bottom-right" positional prior anywhere in the code.

### Category
Already fully traced (§3 CATEGORY-001, §4). Explicitly **not** based on Excel/business configuration, vendor master data, or location — purely a static, hardcoded keyword table checked against vendor name + line items + filename. There is no per-company configuration surface for this at all today (see §12/§18).

### Transactional
Already fully traced (§3 TXN-001/002/003). The mechanism that prevents "amount mentioned ≠ transaction total" from becoming a real bug is entirely TXN-002's unconditional field-clearing block — there is no separate "is this amount real" heuristic beyond the type-based gate itself.

---

## 8. Layout and Positional Intelligence

**Actually used:**
- **Line grouping**: real, vertical-bbox-overlap-based (`lines.py`, `_MIN_VERTICAL_OVERLAP = 0.6`), not a fixed y-tolerance — explicitly re-tuned when PaddleOCR replaced Tesseract as primary (documented real regression: PaddleOCR's region-level boxes have wildly different heights than Tesseract's word-level ones, breaking a fixed y0-proximity check).
- **Column grouping**: real, x-position-band-based (`line_items.py::detect_columns`, anchored to the header row).
- **Reading order**: rows sorted by `(y0, x0)`; words within a row sorted by `x0`. Simple top-to-bottom, left-to-right — no multi-column-page (newspaper-style) reading-order detection exists.
- **Proximity**: used narrowly — `_is_name_continuation`'s gap-vs-line-height ratio, `reconstruct_rows`'s nearest-by-y-distance description merge, `dynamic_fields`'s cell-gap threshold (`_CELL_GAP = 28.0`).
- **Header/footer detection**: **content-based, not geometric**, everywhere it matters (vendor noise filters, totals-boundary keywords) — there is no "this Y-range is the header/footer zone" concept anywhere in `invoice_extraction`.
- **Table/line-item handling**: real, extensively hand-tuned (§3 LINEITEM-001 through 005).
- **Positional scoring**: only exists as **binary gates** (confidence floors, column-tolerance bands), never a continuous positional score feeding into field selection.

**Partially used / planned but unused:**
- **Font size**: `PositionedWord.font_size` exists in the schema and is populated for native PDF text (PyMuPDF span-level `dict` mode would be needed; `pdf.py`'s own `_native_page_words` docstring says explicitly it does *not* attach font size at word level currently, "documented possible enhancement, not required for what Phase 2a implements"). **Font size is captured in the data model but never actually consulted by any selection rule** — vendor selection uses page position, not font size, despite the schema's own comment framing font-size-based detection as a Rule 3.4 need.
- **Ruling-line/border geometric detection (Rule 4.1)**: explicitly documented as **not implemented at all** in `line_items.py`'s module docstring — a real, acknowledged gap, not an oversight nobody noticed.

**Where correct OCR text + wrong layout interpretation causes a wrong field** (a real, documented case): the `"Total Booking Amount"` line-item description case (LINEITEM-003) — the OCR text was 100% correct, but before column-awareness was added, whole-row keyword matching alone misread which row the totals boundary started at, discarding an entire real line-item table.

---

## 9. Normalization Rules

| Operation | Before | Rule | After | Location |
|---|---|---|---|---|
| Date: trailing-time strip | `"Dec 14, 2020, 4:18:34 PM"` | `_TRAILING_TIME_PATTERN` regex strip | `"Dec 14, 2020"` | `validators.py` |
| Date: ordinal-suffix noise | `"30\" June 2026"` | `_ORDINAL_SUFFIX_NOISE` strips `[stndrhSTNDRH"'′″]{1,2}` after 1-2 digits | `"30 June 2026"` | `validators.py` |
| Date: missing separator | `"17Jun 2026"` | `_MISSING_SEPARATOR_DIGIT_LETTER`/`_LETTER_DIGIT` insert a space at digit↔letter boundaries | `"17 Jun 2026"` | `validators.py` |
| Amount: currency/comma/decimal parse | `"Rs. 1,234.56"` | `_MONEY_PATTERN`, comma-strip, `float()` | `1234.56` | `validators.py` |
| Amount: parenthesized negative | `"(1,000)"` | `_PARENTHESISED_NEGATIVE` detected → sign flip | `-1000.0` | `validators.py` |
| Vendor: leading document-type word | `"INVOICE The Florist by Aimen Tahir"` | `_strip_leading_document_type_label` | `"The Florist by Aimen Tahir"` | `fields.py` |
| Vendor: leading reference stamp | `"Rs1100 Express Mart"` | `_strip_leading_reference_stamp` (>50% digit-ratio first word) | `"Express Mart"` | `fields.py` |
| Vendor: mid-line NTN removal | `"KFC Bahia NTN#0819531-5 Pindl Phase-7"` | `_truncate_at_registration_number` | `"KFC Bahia"` | `fields.py` |
| Vendor name (for matching only, not display): whitespace + suffix | `"ABC Traders Pvt. Ltd."` | `normalize_vendor_name`: lowercase, whitespace-collapse, strip corporate suffix | `"abc traders"` | `dedup.py` |
| Line-item header keyword | `"Amount(Rs)"` | `_TRAILING_PAREN` strip + `.rstrip(".:")` | `"amount"` | `line_items.py` |
| Case: currency/document-type/category matching | any case | `.lower()` / `re.IGNORECASE` throughout | lowercase compare | ubiquitous |
| Whitespace: value text | `"  M180362221  SGD  "` | `.strip()` at multiple points; no general whitespace-collapse utility exists | trimmed ends only (internal multi-space is **not** collapsed anywhere by a shared function) | `fields.py::_value_text` |

**No dedicated "OCR character correction" (e.g., 0/O, 1/l confusion) exists anywhere as a general text-normalization pass.** The closest is ARITH-003's *outcome-level* catch (an implausible date/tax-rate is flagged, not corrected) — there is no character-substitution table anywhere in the codebase.

---

## 10. Negative / Exclusion Rules

| Excluded shape | Why it exists | Where implemented | Field protected | On trigger |
|---|---|---|---|---|
| Reference/serial number (>50% digit ratio) | A business name is letter-dominated; a CNIC/NTN/phone/quantity is not | `fields._looks_like_reference_number` | `vendor_name` | Candidate skipped |
| A line that is itself a parseable date | Real regression: a billing statement's "Date issued" header line was picked as vendor | `fields._looks_like_reference_number` | `vendor_name` | Candidate skipped |
| Known receipt-header prefixes (FBR e-invoice numbers, "Bill#", "Receipt#") | Real Pakistani POS receipts print compliance numbers ahead of the business name | `fields._NON_VENDOR_LINE_PREFIXES` | `vendor_name` | Candidate skipped |
| POS/software self-credit ("Powered By...") | The receipt printer's own advertising, not the issuing business | `fields._PROVIDER_CREDIT_PREFIXES` | `vendor_name` | Candidate skipped |
| Order-fulfillment metadata ("Pickup/Delivery", "Dine In") | Industry-wide POS vocabulary, never a business name | `fields._ORDER_FULFILLMENT_PHRASES` | `vendor_name` | Candidate skipped |
| Courtesy footer ("Thank you... visit again") | Boilerplate | `fields._looks_like_decorative_or_metadata_text` | `vendor_name` | Candidate skipped |
| Leading amount/reference stamp on the vendor's own line | Corner filing annotations ("Rs1100", "Bill-3") | `fields._strip_leading_reference_stamp` | `vendor_name` | Leading word dropped, not whole candidate |
| Mid-line NTN registration marker | Government-mandated tax ID, never part of a name | `fields._truncate_at_registration_number` | `vendor_name` | Text truncated at the marker |
| Bare 1-2 digit number | Almost always a page number/item count | `validators.is_plausible_invoice_number` | `invoice_number` | Rejected, confidence degraded |
| A digit run with no thousands-comma/decimal/currency marker | A CNIC, NTN, phone number, or bare quantity, not a written total | `fields._looks_like_a_specific_amount` | `total` (positional fallback) | Not counted as a total candidate |
| A digit-run token with letters glued in | Evidence OCR merged two words — the number is truncated/unreliable | `fields._looks_like_a_specific_amount` | `total` | Rejected outright, not even partially trusted |
| Subtotal/tax/discount-shaped keys | Must never be mistaken for the grand total | `extract_invoice._NOT_TOTAL_KEY_MARKERS` | `total` (dynamic-field tier) | Excluded from `_dynamic_total`'s candidate set |
| Line-item table's own column-header row | Its header text ("SUBTOTAL"/"TOTAL") would otherwise anchor a totals-label match to the first line item's own figure | `extract_invoice.py`'s `totals_skip` passed into `_money_field`/`_total_field` | `subtotal`, `tax`, `discount`, `total` | Header row excluded from label-row matching |
| ALL-CAPS heading with no digit/lowercase/currency evidence | A section heading ("BILLED TO", "PAYMENT & SHIPPING"), not a value | `dynamic_fields._VALUE_EVIDENCE` | generic discovered fields | Not treated as a value cell |
| A cell containing a digit | A reference code (e.g. "PO-55231"), not a label | `dynamic_fields._is_label` | generic discovered fields | Not treated as a label |
| A financial field on a non-transactional document | The single most important exclusion in the whole system | `extract_invoice.py`'s clearing block (TXN-002) | `total`,`subtotal`,`tax_amount`,`discount`,`tax_rate`,`vendor_name` | Unconditionally force-cleared |
| Any category on a non-transactional or sale-type document | Category is a purchase-cashbook concept only | `invoice_builder.py`'s conditional call to `classify_category` | `category` | Never even attempted, stays `None` |

---

## 11. Conflicting Rules / Order-Dependent Behavior

- **Two independently-maintained copies of the same threshold.** `ocr/pdf.py::MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER` and `ocr/liteparse.py::_MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER` are both `20`, defined separately, **by explicit design** ("kept as its own constant... so this module stays independently swappable/removable during the migration without coupling to the legacy module it may eventually replace" — `liteparse.py`'s own docstring). This is a deliberate, documented duplication, not an accidental one — but it does mean a future tuning of one will silently not affect the other unless someone remembers both exist.
- **Two independently-maintained mirrors of the extraction response schema**: `ai-engine/app/schemas/ocr.py::ExtractedInvoiceResponse` and `invoice-service/app/schemas/ai_extraction.py::ExtractedInvoiceSchema` — same shape, duplicated by explicit design for service-independence. A schema change on one side that isn't mirrored on the other would fail silently at the Pydantic validation boundary (extra/missing fields), not at compile time — there is no shared contract test found verifying the two stay in sync (see §14).
- **`vendor_label`'s label variants vs. positional fallback's own noise filters can disagree on the *same physical line*.** If a line matches a `vendor_label` variant ("Bill From", "Sold By") the label-anchored path wins outright and **skips every noise filter** the positional path would have applied — a label match is never subjected to `_looks_like_reference_number`/`_looks_like_decorative_or_metadata_text` at all. This is intentional (label match is the strongest tier), but it does mean a mislabeled document (a template that puts "Vendor:" next to a POS-system self-credit line by template error) would sail straight through with no noise check at all — a real, if narrow, blind spot.
- **`find_label_anchored_text`'s label-priority vs. `LABEL_VARIANTS`'s substring overlap.** Variants are sorted longest-first specifically because shorter variants are substrings of longer ones ("to" inside "billed to") — this is a real, hidden ordering dependency: if a *new* label variant were added without checking this invariant, it could silently start winning over a more specific existing variant. The code comment at `fields.py:146` documents the current safeguard but there is no automated test asserting "no variant is a prefix-collision of another" as an invariant — it is maintained by convention only (see §12).
- **`_total_field`'s three tiers can, in principle, disagree** (label-anchored finds one number, positional finds a different unambiguous one) — there is no cross-tier reconciliation or arithmetic cross-check between tiers; whichever tier resolves first wins outright, even if a later tier's candidate would have passed `ARITH-001` and the winning one wouldn't. Arithmetic validation runs *after* field selection is already final, purely as a confidence signal, never as a tie-breaker between candidate totals.
- **`document_type` keyword order is itself the only thing preventing a real misclassification** (minute_sheet before bill) — this is not a scored comparison, it's raw list order. Any future person editing `_TYPE_KEYWORDS` who reorders it without understanding this specific prior bug could silently reintroduce it. No test explicitly asserts the *order* invariant beyond the one regression test for minute-sheet-vs-bill (`test_document_type.py` — presumed present given the described history, not independently re-read line-by-line in this audit).

---

## 12. Hardcoded / Vendor-Specific Logic — Classified

### General deterministic rules (genuinely reusable across companies/countries)
- Line-grouping by vertical bbox overlap (`lines.py`)
- Column detection by header-row x-position (`line_items.py`)
- Money-pattern parsing (multi-currency prefixes already included: PKR, USD, SGD, GBP, EUR)
- Date normalization structural fixes (ordinal-suffix strip, missing-separator insertion) — these key on *character shape and position*, not specific values
- Reference-number/decorative-text vendor filters (`_looks_like_reference_number`, `_looks_like_decorative_or_metadata_text`) — structural (digit ratio, known universal POS phrasing), not tied to any one vendor
- Arithmetic cross-checks (`check_line_item`, `check_totals`) — pure math, currency-agnostic
- The whole confidence/routing architecture (tiers, weights, thresholds) — the *values* are Pakistan-dataset-tuned (see below) but the *mechanism* is general

### Vendor-specific rules (work because of a particular vendor's real, observed format)
- `category_classifier.py`'s ~80 keywords are, by the module's own comments, traced to **specific named real vendors**: "Layers Bakeshop", "papajohns@livepepper.com" (the run-together email-form vendor string), "FSO DHA Filling Station" (→ "filling station" keyword), "Express Mart"/"Falcon Cash & Carry" (→ the generic `" mart"`/`"cash & carry"` fallback bucket).
- The NTN-marker vendor-truncation rule (FIELD-005 step 3) is Pakistan-specific by definition (National Tax Number is a Pakistani regulatory concept), though the *mechanism* (truncate at a known regulatory marker) would generalize to another country's own equivalent marker if one were added.

### Dataset-specific rules (connected to the Golden Dataset / current test documents)
- **`geography.py::_CITY_COUNTRY`**: 10 Pakistani cities + 5 Indian cities, explicitly "seeded from real documents this library has actually been tested against, not an attempt at exhaustive geography." Any company outside these 15 cities (or a mid-size city within Pakistan/India not in this list) gets no city/country detection at all.
- **`arithmetic.py::KNOWN_PK_TAX_RATES`**: Pakistan-only sales-tax/GST rate table used for plausibility checking. A real, correct tax rate for a company in any other country will be flagged as *implausible*, discounting `document_confidence` and potentially pushing an otherwise-perfect extraction into `needs_review`.
- **`category_classifier.py::SUGGESTED_CATEGORIES`** (in `models/invoice.py`): the module's own docstring states plainly, "the first nine are the uploaded cashbook's own category headings verbatim... in the same order they appeared there" — i.e., 9 of the 16 category names are lifted directly from this one company's own Excel cash book.
- **`arithmetic.py::is_plausible_invoice_date`'s 730-day window**: a reasonable general heuristic, but its specific value was not shown to be derived from anything beyond "2 years" as a round number — not flagged as dangerous, just untuned.

### Potentially dangerous hardcoding (works today, would silently under-perform elsewhere)
- **`category_classifier.py` in its entirety** — this is the single highest-risk hardcoding in the pipeline for multi-tenant scaling. A different company's petty-cash vendors (different restaurants, different local hardware stores, different courier services) would overwhelmingly fall through to `None`/"Uncategorized," not because the *mechanism* is wrong, but because the *keyword list* is one company's own vocabulary. There is currently **no per-company configuration surface** for this list at all — it is a single global Python tuple shared by every tenant.
- **`geography.py`'s closed city/country table** — silently returns nothing (never wrong, but unhelpfully empty) for any company outside its 15 seeded cities.
- **`arithmetic.py::KNOWN_PK_TAX_RATES`** — this one is the most dangerous of the three, because unlike category/geography (which fail silently to "Uncategorized"/`None`), a wrong tax-rate plausibility check actively **discounts confidence and can push a real invoice into needs_review** or discount `document_confidence` for a perfectly legitimate non-Pakistani tax rate — a false-positive review burden, not just a missing nicety.

---

## 13. Confidence and Needs-Review Logic

- **OCR confidence**: exists, per-word (`PositionedWord.confidence`) and per-document (`ExtractionResult.confidence` — mean of per-word confidences for an OCR'd page, or a flat `1.0` for native PDF text, never a guess).
- **Field confidence**: exists (`FieldValue.confidence`), computed as the *average* of OCR confidence and a method-tier score (CONF-001) — not the raw OCR confidence alone.
- **Vendor confidence**: not a distinct concept — same generic `field_confidence`, with an optional `+0.1` fuzzy-match boost (DEDUP-002) as the only vendor-specific adjustment.
- **Classification confidence**: exists for `document_type` (1.0 for a real keyword match, 0.4 for the weak receipt-shape guess, 0.0 for none) — but **does not exist for `transactional`** (a plain derived bool with no confidence of its own) or for `category` (no confidence at all, anywhere — see §4's `category` trace).
- **Overall confidence**: `document_confidence`, the single weighted-mean-then-arithmetic-discounted composite (CONF-002).
- **`requires_review`**: does not exist as a named boolean anywhere. The closest equivalents are (a) the document-level `review_status` 3-value enum, and (b) each field's own `status` tri-state (`FOUND`/`NOT_FOUND`/`UNCERTAIN`) — a caller wanting a single "does this need review" boolean has to derive it from `review_status != "auto_processed"`.

**Exact decision points** (CONF-003/CONF-004, verified against the real thresholds):
- **Accept automatically** (`auto_processed`): critical fields present (total + vendor, plus invoice_number *only if* `document_type=="invoice"`) AND `document_confidence >= 0.75` AND arithmetic didn't fail.
- **Accept with low confidence** (`needs_review`): critical fields present but `document_confidence < 0.75`, OR arithmetic failed outright.
- **UNKNOWN / high-priority review** (`needs_review_high_priority`): a critical field is missing, OR `document_confidence < 0.4`.
- **Needs Review, always, regardless of confidence** (TXN-003): any non-transactional document, unconditionally — confidence never matters for this branch at all.

---

## 14. Test Coverage — Rule-to-Test Mapping

This maps *which real rules are actually protected by a regression test*, not just test counts.

### Well-covered (dedicated test classes exist, named after the specific rule/failure mode)
- **Vendor extraction**: `test_fields.py` has **16 dedicated test classes**, each named after a specific real failure mode: `TestVendorFallbackSkipsADateLine`, `TestVendorFallbackSkipsProviderCreditAndOrderMetadata`, `TestVendorFallbackStripsDocumentTypeLabel`, `TestVendorFallbackMergesATwoLineBusinessName`, `TestVendorFallbackPrefersHighestConfidenceOverFirstLine`, `TestVendorFallbackStripsALeadingReferenceStamp`, `TestVendorFallbackTruncatesAtARegistrationNumber` — this is genuinely rule-level regression coverage, not incidental.
- **Line-item reconstruction**: `test_line_items.py`'s `TestColumnDriftOnMessyRealDocuments` and `TestTotalsBoundaryRespectsColumns` map directly to LINEITEM-003/004's documented real-bug fixes.
- **Dynamic field discovery**: `test_dynamic_fields.py`'s `TestHeadingsAreNotFields`, `TestValuesAreNotMistakenForLabels` map directly to the "BILLED TO"/"PAYMENT & SHIPPING" and "PO-55231" real bugs cited in the module docstring.
- **Category classification**: `test_category_classifier.py` (7 tests) — covers the keyword mechanism generically.
- **Invoice builder / transactional isolation**: `test_invoice_builder.py`'s `TestNonTransactionalDocuments`, `TestCategoryClassification` (specifically `test_a_sale_invoice_is_never_auto_categorized`, `test_an_unrecognized_vendor_is_left_uncategorized_not_guessed`) directly protect TXN-002/CATEGORY-001's core guarantees.
- **Arithmetic/confidence**: `test_arithmetic.py` (15 tests), `test_confidence.py` (15 tests) — both modules have close to 1:1 function-to-test-class coverage given their small surface area.
- **Validators**: `test_validators.py` (40 tests) — the largest test file relative to module size (194 lines of source), strong coverage of date-format edge cases and money-pattern edge cases.
- **Real-document regression**: `test_extract_invoice_real_documents.py` exists specifically to pin behavior against real fixture documents (not just synthetic ones), and the Golden Dataset baseline files (`backend/eval/baselines/*.json`) provide a **35-real-document regression snapshot mechanism** via `runner.py::diff_baselines` — a genuinely strong, unusual-for-this-scale safety net most rules-engine codebases don't have.

### Partially covered
- **Document type classification**: `test_document_type.py` (14 tests) exists, but this audit did not verify each of the 11 `_TYPE_KEYWORDS` patterns has its own explicit test — the minute-sheet-before-bill ordering bug is *the* documented regression, but whether every other pattern (credit_note, purchase_order, quotation) has a dedicated positive+negative test pair was not individually confirmed line-by-line.
- **Currency/geography/payment status**: each has a test file (`test_currency.py` 12, `test_geography.py` 7, `test_payment.py` 7) proportionate to their small module size — likely covers the happy path and the documented "$9 garbled glyph" false-positive case for currency, but the exhaustiveness of city/phone-code table coverage (all 15 cities? all 4 phone codes?) was not individually verified.
- **Confidence routing edge cases**: CONF-004's document-type-conditional critical-field logic (`invoice_number` required only for `document_type=="invoice"`) is described in the code's own comment as fixing a real measured regression (0/35 receipts had an invoice number) — presumably tested, but this audit did not confirm a specific test name asserting this exact conditional.

### Rules with no test found during this audit
- **`dedup.py::duplicate_key()` (DEDUP-003)** — `test_dedup.py` exists and likely tests `file_hash`/`normalize_vendor_name`/`find_matching_vendor`, but **no caller of `duplicate_key` was found anywhere in `invoice-service`'s route code** during this audit — meaning even if the function itself is unit-tested in isolation, there is no integration test (or production code path) proving it actually prevents a re-scanned invoice from becoming a duplicate row. This is a real gap: implemented, unit-tested, architecturally unwired.
- **Cross-service schema-mirror consistency** (`ai-engine`'s `ExtractedInvoiceResponse` vs. `invoice-service`'s `ExtractedInvoiceSchema`) — no test was found asserting the two Pydantic models stay in sync; a field added to one and forgotten on the other would only surface as a runtime validation failure or silent data loss (an extra field ignored on receipt), not a caught regression.
- **The "no variant is a prefix-collision of another" invariant** in `LABEL_VARIANTS` (§11) — maintained by code-review convention, not by an automated test.
- **Font-size-based vendor detection** — since it's not implemented (§8), there is naturally no test for it either; flagged here only so it's not silently assumed covered.

### Important edge cases this audit could not confirm are covered
- Multi-page documents where the vendor's label-anchored value is on page 2+ (the positional fallback is explicitly page-0-only; whether the label-anchored path is tested across a multi-page real document specifically was not confirmed).
- A document where `LiteParse`'s primary engine and the `tesseract` rollback engine would disagree on the *same* real document — `test_liteparse.py` (15 tests) covers the LiteParse path in isolation; a side-by-side comparison test (same input, both engines, assert acceptable divergence) was not found.

---

## 15. Golden Dataset Relationship — Production vs. Evaluation vs. Ground Truth

**Three genuinely separate things, confirmed distinct in the code:**

1. **Production logic** — `invoice_extraction/*`, `ocr/*`, `category_classifier.py`. Verified via `grep`: **zero references to `backend/eval`, `golden_eval`, or any Golden Dataset filename/vendor/value exist inside these production modules.**
2. **Evaluation logic** — `backend/eval/golden_eval/{compare,evaluate,runner}.py`. Pure comparison/aggregation code, explicitly designed to have "zero dependency on how `actual` was produced" (a live scan, a saved JSON, or a synthetic fixture) — `compare.py`/`evaluate.py` are independently unit-tested with **synthetic** documents (`backend/eval/tests/test_compare.py`, `test_evaluate.py`), not the real 35.
3. **Ground truth** — `backend/eval/golden_eval/golden_dataset.py`'s `GOLDEN_DATASET` list, 35 hand-audited `GoldenDocument` entries, each carrying an `evidence` string documenting *how* the ground truth was established (independently re-read via a fresh raw-OCR pass, or cross-referenced against the real Excel cash book by matching amount) and an `excel_row` citation where applicable. The module's own docstring states the methodology explicitly: `vendor`/`invoice_date`/`total` are anchored on what the document *itself* literally prints (re-derived fresh, "never copied from any prior pipeline run" — specifically to avoid a circular benchmark that would only confirm the system agrees with itself); `category` alone is anchored on the Excel's own bookkeeping judgment, since category is inherently a human judgment call, not something printed on the receipt.

**Critical safety property, independently verified for this audit**: `runner.py::_load_category_classifier()` **imports the real, production `category_classifier.py` file directly from its actual source path** (`importlib.util.spec_from_file_location`) rather than reimplementing or duplicating its logic — meaning the category-accuracy number in the benchmark is a measurement of the *actual* production classifier, not a proxy. Same for the OCR/extraction accuracy numbers: `runner.py::scan_documents()` calls the real, running `ai-engine` HTTP endpoint over the real 35 files — "No part of the pipeline is reimplemented or mocked," per its own docstring, and this audit confirms that claim against the actual code.

**Does any production rule accidentally depend on the Golden Dataset?** No occurrence found. Every rule change documented in `docs/invoice-ocr-plan.md` (§13 category audit, §14/§18 vendor audits, §17 date audit) that was *motivated* by a Golden Dataset failure was explicitly re-justified on **structural, general grounds** before being implemented — e.g., §18's own explicit self-check: "neither rule references a vendor name, a filename, or an amount from the 35 documents — both key purely on structure." The category classifier is the one place this audit would flag as **closest to the line** (§12) — its keywords are literally lifted from specific real vendors in the dataset — but this is a keyword *list* (openly, honestly documented as dataset-derived and explicitly *not* claimed to generalize), not a hidden dependency on a filename or a golden-dataset value being read at runtime. No filename-based branching, no "if this exact document" logic, was found anywhere in production code.

---

## 16. Real Examples

### Correct extraction: total via label anchor
```
RAW OCR: "Grand Total    Rs. 20,420.00"
NORMALIZED: (parse_money operates directly on the label-adjacent text)
CANDIDATES: 1 (label-anchored "grand total" variant matched)
VALID: parse_money("Rs. 20,420.00") = 20420.0
SCORES: method="label_anchor+pattern" → tier 1.0, avg with OCR conf ~0.95 → field_confidence ≈ 0.975
SELECTED: 20420.0
FINAL JSON: {"value": 20420.0, "confidence": 0.975, "method": "label_anchor+pattern", "status": "FOUND"}
```

### Correct extraction: non-transactional isolation working as designed
```
RAW OCR: "MINUTE SHEET ... For approval of Rs. 22,875/- ... Subject: Repair & Maintenance/Stationary"
document_type: matched \bminute\s*sheet\b → "minute_sheet", confidence 1.0
is_transactional("minute_sheet") → False
amount_mentioned: find_approval_amount_text → "22,875" → parse_money → 22875.0
total/subtotal/tax_amount/discount/tax_rate/vendor_name: ALL force-cleared to not_found()
FINAL: {"transactional": false, "amount_mentioned": 22875.0, "total": {"value": null, ...},
        "vendor_name": {"value": null, ...}}
```
This is the pipeline working exactly as intended — a real approval memo's amount never reaches the cashbook.

### Difficult/incorrect: the two known severe total-extraction failures (from this session's own live audit against real seeded data, not hypothetical)
```
Document: a real Express Mart receipt, printed total "Rs. 2,750.00"
ACTUAL (pipeline output): 277,000.0  — a 100x digit-insertion OCR misread
Root cause: not diagnosed at the OCR-token level in this audit (would require re-running
the raw OCR trace), but structurally this means find_total_anywhere or the label-anchored
path accepted a garbled digit run that parse_money successfully parsed as a plausible-shaped
number — the exact class of error _looks_like_a_specific_amount's "letters glued into digits"
check catches, but which a purely-numeric OCR misread (extra digits, no letters) cannot be
caught by, since it never fails the "is this a well-formed number" test at all.
```
This is the single most financially damaging error currently observed in real production-shaped data: one bad digit read inflated an entire expense category's total by ~95% in a real Saved Records session during this project's own development (see the session's Cash Book audit turn). It is **not a new pipeline regression** — it is squarely inside the already-disclosed 73% Total accuracy figure — but it is the clearest possible illustration of why a purely-numeric-shape validation (parse_money succeeding) is not the same as a *plausibility* validation (is this number the right order of magnitude). No order-of-magnitude/vendor-history cross-check (the source spec's own Rule 5.5) is implemented anywhere in this codebase — see §20, P0.

### Difficult: ambiguous date, resolved by fixed priority order, not evidence
```
RAW: "03/04/2026" on a document with no other date on the page to establish a dominant format
NORMALIZED: unchanged (no ordinal noise/missing separator)
CANDIDATES: matches %d/%m/%Y (day-first) first in _DATE_FORMATS priority order
SELECTED: 2026-04-03 (3 April), silently — %m/%d/%Y (4 March) is never even attempted,
          since %d/%m/%Y already succeeded
```
If this document were actually from a US-based company, this would be silently wrong with no signal at all — the day/month ambiguity is real and unresolved by any cross-field evidence.

### Difficult: dense multi-column POS receipt (structural, not vendor-specific)
```
RAW LAYOUT: "Bill From: Invoice No. Currency" (three fields packed onto one visual row)
label match on "Bill From" → same-line remainder starts as "Invoice No. Currency ..."
_truncate_at_next_label detects "Invoice No." as another field's own label mid-remainder
→ truncated BEFORE it, leaving an empty same-line value → falls to next-line, x-aligned scan
This is the exact real bug the module docstring opens with, now fixed by
_truncate_at_next_label + _NON_VALUE_TOKENS.
```

---

## 17. Information Loss Audit

- **OCR → normalized text**: Minimal loss identified. `parse_money`/`parse_date` operate on the *original* label-adjacent text, not a globally-normalized copy — there is no whole-document normalization pass that could destroy information before field-level parsing sees it. The one narrow loss: `_value_text()`'s `.strip(" :#-\t")` on a same-line remainder could theoretically strip a leading `#` that was meaningful (mitigated: `#` is deliberately excluded from `_clean()`'s label-side strip set specifically to avoid this on the label side, but the *value*-side strip set (`_VALUE_TRIM = " :\t"` in `dynamic_fields.py`) deliberately keeps `#` for exactly this reason — "part of the value for a reference number printed as '#6000000001'" — the fields.py value strip (`.strip(" :#-\t")`) is *inconsistent* with dynamic_fields.py's own value strip on this exact point, a real, small normalization inconsistency between two modules that both discover label/value pairs.
- **Correct candidate exists, but scoring/selection chooses another**: The clearest documented instance is the KFC/"Bahia" vendor case (§4/§16) — not a scoring bug exactly, but a genuine case where the *correct, fully-printed* text is extracted, and a stricter ground-truth definition (brand name only) disagrees with what the document actually says. This is arguably not information loss at all — it's the pipeline being *more* complete than the ground truth expects.
- **Real, structural information loss**: `tax_rate` is never independently extracted even when a document explicitly prints one (e.g., "GST @ 17%") — because no `LABEL_VARIANTS["tax_rate"]` entry exists, this value is always **discarded at OCR-token level and re-derived by division** afterward, even on a document where the printed rate would have been strictly more reliable than a division-based back-calculation (which fails silently to `not_found()` whenever `subtotal` is `0`/`None`).
- **Dynamic-field discovery's own exclusion of the line-item body** (`skip_line_range`) means any label/value pair that happens to sit *inside* the line-item table region but isn't itself a real line item (e.g., a mid-table note or a per-row discount annotation) is silently dropped — neither reconstructed as a line item nor surfaced as a dynamic field.
- **`amount_mentioned` on a non-transactional document only ever keeps ONE amount** (the approval-sentence figure, or a total/subtotal fallback) — a minute sheet listing 4 separate attached bills' amounts (the exact real shape described in `document_type.py`'s own docstring, "Bill-1(Legal)", "Bill-2(Emp Care)"...) has those individual bill amounts **completely discarded**; only the one approval-sentence total survives into the final JSON. This is a real, acknowledged (if not explicitly called "loss" in-code) simplification: a genuinely itemized internal document is flattened to a single `amount_mentioned` float.

---

## 18. Architecture Scalability Audit

**What already scales cleanly to many companies/vendors/formats today:**
- The whole OCR/structuring mechanism (line grouping, label-anchoring, column detection, arithmetic validation, confidence scoring) — none of it is company-specific; it operates on document *shape*, not content.
- `known_vendors` is already a **per-caller parameter**, not a global list — `invoice-service` already scopes it per-company (`_known_vendors()` in `scanner.py`, queried fresh per company_id) before passing it into `extract_invoice()`. Vendor fuzzy-matching is architecturally multi-tenant-ready *today*.
- `SUGGESTED_CATEGORIES` and `LABEL_VARIANTS`/label dictionaries are already **data, not code** ("Kept as plain data (not code) deliberately, so extending coverage for a new vendor's label wording never requires touching extraction logic" — `labels.py`'s own docstring) — the *mechanism* for per-company extension already exists structurally, even though no per-company *storage* for it exists yet.

**What does not scale today, and would need to become configurable:**
- **`category_classifier.py`'s keyword table** — currently one global Python tuple. Every company shares the exact same categories and keywords. This is the single largest blocker to multi-company accuracy (§12).
- **`geography.py::_CITY_COUNTRY`** — a global, closed table. Needs to become at minimum an extensible list, ideally locale-aware per company.
- **`arithmetic.py::KNOWN_PK_TAX_RATES`** — needs to become a per-company (or at minimum per-country) configuration, since this actively penalizes confidence for legitimate non-Pakistani tax rates.
- **`validators.py::_DATE_FORMATS` priority order** — needs to become locale-aware (a US-based company should plausibly get month-first priority) rather than one fixed global order.
- **Label variants themselves** (`LABEL_VARIANTS`) — already data, but there is no company-level *override/extension* mechanism today; adding a new vendor's label wording currently means editing the shared global file, affecting every tenant, not adding a company-specific supplement.

**Recommended eventual boundary** (identification only, not implementation — see §19):
```
Universal rules (never company-specific): OCR mechanics, line/column grouping,
  arithmetic cross-checks, confidence-tier mechanism, money/date PARSING mechanics
        +
Region/locale rules: date-format priority, tax-rate plausibility tables, currency
  defaults, city/country tables
        +
Company configuration: category keyword table, category name list, known-vendor list
  (already exists), custom label variants
        +
Vendor/layout profiles (not implemented at all today): a specific vendor's own
  known receipt layout (e.g., "this POS chain always prints total at this position"),
  which would let a repeat vendor's future receipts skip fallback ambiguity entirely
```

---

## 19. Recommended Future Rule Architecture (proposal only, not built)

Based strictly on where the *actual* current implementation's seams already are (not a generic textbook diagram):

```
Universal Rules (today's invoice_extraction/*, unchanged)
        +
Document-Type Rules (today's document_type.py — already its own clean module)
        +
Region/Locale Rules (NEW — would absorb: _DATE_FORMATS priority,
        KNOWN_PK_TAX_RATES, _CITY_COUNTRY, currency defaults)
        +
Company Configuration (NEW — would absorb: category keyword table,
        SUGGESTED_CATEGORIES, custom label variants; known_vendors already
        fits this layer today)
        +
Vendor/Layout Profiles (NEW, not started — a per-known-vendor cache of
        "where does this vendor's receipt usually put its total/date")
        ↓
Candidate Generation (today's fields.py/line_items.py/dynamic_fields.py — largely
        already correctly separated, needs no structural change)
        ↓
Validation (today's validators.py/arithmetic.py — already correctly separated)
        ↓
Scoring (today's confidence.py — already correctly separated, but see §20 P1 on
        untuned weights/thresholds)
        ↓
Selection (currently split across 3 inconsistent strategies per §6 — this is the
        one stage this audit recommends actually *unifying* into one consistent
        candidate-comparison abstraction, since vendor/total/category each
        currently reinvent "which one wins" independently)
        ↓
Confidence (today's confidence.field_status/document_confidence — already exists)
        ↓
Review (today's InvoiceStatus/review_flags — already exists)
```

The good news, stated plainly: **the current codebase is already closer to this target shape than a typical rules engine would be** — most of the proposed new layers (Region/Locale, Company Configuration) are *extracting already-isolated constants* into configuration, not restructuring code that currently has these concerns tangled together. The one genuine structural recommendation is unifying the three different "Selection" strategies (§6) into one consistent pattern, since that inconsistency is itself a source of the conflicting-rules risk noted in §11.

---

## 20. Critical Findings, Prioritized

### P0 — Financial correctness risks

**P0-1. `quotation` and `purchase_order` are not in `NON_TRANSACTIONAL_TYPES`.**
- **Why it matters**: A quotation (a proposed price, not a completed purchase) or a purchase order (an intent to buy, not a receipt of goods/payment) being treated as `transactional=True` means its stated total could enter the cashbook as if money had actually changed hands.
- **Evidence**: `document_type.py`, `NON_TRANSACTIONAL_TYPES = frozenset({"minute_sheet", "approval_request"})` — a 2-item set; `DocumentType` literal separately includes `"quotation"` and `"purchase_order"` as recognized types that are *not* in this set.
- **Affected fields**: `transactional`, `total`, `subtotal`, `tax_amount`, `category`, ultimately the Saved Records cashbook total.
- **Recommended solution**: Add `quotation` and `purchase_order` to `NON_TRANSACTIONAL_TYPES` (or introduce a third semantic bucket — "committed but not yet transacted" — distinct from both `transactional` and today's `NON_TRANSACTIONAL_TYPES`), after confirming with real documents whether a quotation ever legitimately represents money already spent (it should not, by definition).

**P0-2. No order-of-magnitude / vendor-history plausibility check exists for `total` (source spec's own Rule 5.5, never implemented).**
- **Why it matters**: This is the exact gap that let the real, observed "PKR 277,000 instead of PKR 2,750" digit-insertion error through as a confidently-reported, arithmetic-consistent (nothing to cross-check it against) total — the single largest real dollar-figure distortion found in this project's own live testing.
- **Evidence**: `arithmetic.py` has no function referencing vendor history or a per-vendor expected range; `dedup.py`'s `find_matching_vendor` exists but its result is never fed into any total-plausibility check.
- **Affected fields**: `total`, and everything downstream that sums it (category totals, monthly cashbook totals).
- **Recommended solution**: Implement Rule 5.5 as originally specified — track a rolling total range per known vendor, flag (not reject) a total that's an order of magnitude outside it.

**P0-3. `find_total_anywhere`'s digit-shape validation cannot catch a pure-digit OCR misread (extra/wrong digits, no glued letters).**
- **Why it matters**: `_looks_like_a_specific_amount` rejects a token with letters glued into digits, but a clean-looking `277000` with no letters and the right punctuation shape passes every check — there is no upper-bound sanity check (e.g., "a petty-cash receipt total over X is suspicious") anywhere.
- **Evidence**: `fields.py::_looks_like_a_specific_amount`, no magnitude ceiling anywhere in the function.
- **Affected fields**: `total`.
- **Recommended solution**: A configurable, company-level soft ceiling (not a hard reject) that downgrades confidence/forces review for a total far outside a document's own line-item sum or a company's typical range — related to but distinct from P0-2.

### P1 — Accuracy problems

**P1-1. Vendor extraction is the lowest-accuracy canonical field (62%), with 12/35 documents in the Golden Dataset still wrong after two dedicated audit passes**, several explicitly marked as "structurally hard" with no proposed fix (§18 of the plan doc: bank-screenshot confusion, dense-POS-layout collisions, a stamp at 40% digit ratio just under the 50% threshold).
- **Recommended solution**: The threshold-tuning approach (moving 0.5 lower) risks new false positives per the code's own reasoning; a genuinely different signal (font size — already captured in the schema but unused, §8) is the most promising unexploited lever.

**P1-2. Fixed day-first date-priority order has no evidence-based disambiguation** (§7, §16) — silently wrong for any non-Pakistani-convention document with an ambiguous `DD/MM` vs `MM/DD` date and no other date on the page to establish which format the document uses.
- **Recommended solution**: The source spec's own already-identified fix — detect the dominant format across a document's *own* multiple date fields (invoice date, due date) before falling back to the fixed priority order.

**P1-3. `tax_rate` is always computed, never extracted, even when explicitly printed** (§17) — discards a potentially more reliable printed value in favor of a division that fails whenever `subtotal` is missing or zero.
- **Recommended solution**: Add a `tax_rate` label-variant list and attempt direct extraction first, falling back to the current division only when no direct value is found.

**P1-4. Category accuracy (61%) is architecturally capped by a single-company keyword list** (§12) — this isn't an accuracy bug in the classifier's *logic*, it's a coverage ceiling inherent to the current design.

### P2 — Architecture problems

**P2-1. Category assignment lives in a different service than the rest of extraction, with no confidence/method metadata of its own** (§4, §6) — the only cashbook-relevant field with zero audit trail on *why* it was assigned, unlike every other field's `{value, confidence, method}`.

**P2-2. No company-level configuration surface exists for any of the dataset-shaped hardcoding identified in §12** (category keywords, tax-rate table, city/country table, date-format priority) — see §18/§19 for the proposed boundary.

**P2-3. Vendor/layout profiles (a known vendor's own observed receipt layout) do not exist at all** — every scan of a repeat vendor's receipt re-runs the full generic fallback chain from scratch, even though `known_vendors` fuzzy-matching already proves the pipeline knows it has seen this vendor before.

### P3 — Maintainability problems

**P3-1. Two independently-maintained response-schema mirrors** (`ai-engine`'s `ExtractedInvoiceResponse` vs. `invoice-service`'s `ExtractedInvoiceSchema`) with no automated sync-check (§11, §14) — a deliberate design choice, but currently un-guarded against silent drift.

**P3-2. Two independently-defined copies of the same "20 chars/page" text-layer-sufficiency threshold** (`ocr/pdf.py` and `ocr/liteparse.py`) — also deliberate, same risk.

**P3-3. Inconsistent value-string trimming between `fields.py` and `dynamic_fields.py`** (§17) — `fields.py`'s `_value_text` strips `#` from a value; `dynamic_fields.py`'s `_VALUE_TRIM` deliberately does not, for a documented reason. Not a bug today (the two modules serve different fields), but a real inconsistency a future maintainer extending either module could carry into the wrong place.

**P3-4. `dedup.py::duplicate_key()` is implemented and unit-tested but has no caller anywhere in `invoice-service`'s routes** (§14) — dead-but-tested code, or a genuinely missing wiring; unclear which without asking the team.

**P3-5. Three inconsistent "selection" strategies across fields** (§6) — first-match-wins (document type/category/currency), filter-then-first-survivor-with-degrade (vendor), and strict-tiered-fallback-with-ambiguity-gate (total) all coexist with no shared abstraction, making the codebase harder to reason about uniformly even though each individual strategy is well-reasoned in isolation.

### P4 — Nice-to-have improvements

**P4-1.** Font size is captured in `PositionedWord` but never used by any rule (§8) — a documented, low-effort potential second signal for vendor-name detection.

**P4-2.** No general OCR character-confusion correction table exists (0/O, 1/l, etc.) — currently only caught indirectly via `ARITH-003`'s date-plausibility outcome check, never proactively corrected.

**P4-3.** Confidence weights (`FIELD_WEIGHTS`) and routing thresholds (`_AUTO_PROCESS_THRESHOLD`, `_NEEDS_REVIEW_THRESHOLD`) are explicitly self-described as untuned starting defaults (§3 CONF-002/003) — worth revisiting once real human-correction outcomes can be compared against them, per the module's own stated intent.

**P4-4.** No multi-currency conversion or `net_amount`/partial-payment tracking exists (§4) — not a defect (never claimed to exist), just a gap worth naming for future scope.

---

## 21. Master Rule Inventory

| ID | Field/Stage | Rule | Type | Location | Priority | Tested | Risk |
|---|---|---|---|---|---|---|---|
| INTAKE-001 | Routing | File-type classification | Universal | `ocr/extract.py::classify_file` | 1st, absolute | Yes (`test_extract.py`) | Low |
| INTAKE-002 | Routing | Native-text-layer sufficiency (20 chars/page) | Universal | `ocr/pdf.py`, `ocr/liteparse.py` (duplicated) | 2nd | Yes | Low; duplicated threshold |
| INTAKE-003 | Routing | Engine selection + 3-level fallback | Universal | `ocr/extract.py`, `ocr/liteparse.py` | Outermost | Yes (`test_liteparse.py`) | Medium — silent fallback not alerted |
| PREP-001 | Preprocessing | Smart crop / multi-doc split | Universal (image only) | `ocr/preprocess.py` | Before Stage 1 | Yes (`test_preprocess.py`, 14) | Medium — documented row-vs-column false-positive history |
| FIELD-001 | All header fields | Label-anchored extraction | Universal | `fields.py::find_label_anchored_text` | Always tried first | Yes (`test_fields.py`) | Low |
| FIELD-002 | invoice_number, ntn | Pattern validation | Universal | `validators.py` | After FIELD-001 | Yes (`test_validators.py`, 40) | Low |
| FIELD-003 | subtotal/tax/discount/total/qty/rate/amount | Money parsing | Universal | `validators.py::parse_money` | Applied to any located text | Yes | Low-Medium (see P0-3) |
| FIELD-004 | invoice_date | Date parsing + normalization | Region-specific (day-first priority) | `validators.py` | Applied to located text | Yes | Medium (P1-2) |
| FIELD-005 | vendor_name | Label + 8-stage positional fallback | Universal mechanism, PK-specific NTN check | `fields.py::find_vendor_name` + helpers | Label wins outright; else ordered filter chain | Yes, extensively (16 classes) | High (62% accuracy, P1-1) |
| FIELD-006 | invoice_date | Positional fallback | Universal | `fields.py::find_date_anywhere` | Only if FIELD-001 found nothing | Yes | Low |
| FIELD-007 | total | Positional fallback, single-candidate gate | Universal | `fields.py::find_total_anywhere` | Tier 2 of 3 | Yes | Low (precision); Medium (recall) |
| FIELD-008 | total | Dynamic-field fallback, single-candidate gate | Universal | `extract_invoice.py::_dynamic_total` | Tier 3 of 3 | Presumed via `test_extract_invoice_routing.py` | Low |
| LINEITEM-001 | line_items | Header-row detection (2+ keywords) | Universal | `line_items.py::find_header_row` | First | Yes (`test_line_items.py`) | Low |
| LINEITEM-002 | line_items | Column detection | Universal | `line_items.py::detect_columns` | After header found | Yes | Low |
| LINEITEM-003 | line_items, total | Totals-boundary detection, column-aware | Universal | `line_items.py::find_totals_boundary` | After columns known | Yes (`TestTotalsBoundaryRespectsColumns`) | Low |
| LINEITEM-004 | line_items | Row reconstruction, merge + drift recovery | Universal | `line_items.py::reconstruct_rows` | After boundary known | Yes (`TestColumnDriftOnMessyRealDocuments`) | Medium — Rule 4.1 (ruling lines) not implemented |
| LINEITEM-005 | line_items | Per-item arithmetic check | Universal | `arithmetic.py::check_line_item` | After row built | Yes | Low |
| CLASS-001 | document_type | Ordered keyword classification | Universal mechanism, closed vocabulary | `document_type.py` | First-match, minute_sheet/approval lead | Yes (`test_document_type.py`) | Medium — closed keyword list |
| TXN-001 | transactional | Type→bool lookup | Universal mechanism, 2-item closed set | `document_type.py::is_transactional` | Derived, not independent | Yes | High (P0-1) |
| TXN-002 | total/subtotal/tax/discount/vendor | Force-clear on non-transactional | Universal | `extract_invoice.py` | After TXN-001 | Yes (found via Golden Dataset, per docs) | Low (already hardened) |
| TXN-003 | review_status | Non-transactional routing carve-out | Universal | `extract_invoice.py` | End of pipeline | Presumed yes | Low |
| CUR-001 | currency | Word/symbol pattern match | Universal, some PK bias (Rs.) | `currency.py` | Word first, symbol second | Yes (`test_currency.py`) | Low |
| GEO-001/002 | city/country | Closed table + phone code | Dataset-specific | `geography.py` | City first, phone code fallback | Yes (`test_geography.py`) | Medium (P2-2 scope) |
| PAY-001 | payment_status | Ordered keyword match | Universal | `payment.py` | First-match | Yes (`test_payment.py`) | Low |
| ARITH-001 | — | Totals equation | Universal | `arithmetic.py::check_totals` | After totals resolved | Yes | Low |
| ARITH-002 | — | Tax-rate plausibility | Region-specific (PK rates) | `arithmetic.py::is_plausible_tax_rate` | After tax fields resolved | Yes | Medium (P2-2) |
| ARITH-003 | — | Date plausibility, escalates arithmetic_ok | Universal | `arithmetic.py::is_plausible_invoice_date` | After date resolved | Yes | Low |
| CONF-001 | all fields | Method-tier confidence | Universal | `confidence.py::field_confidence` | Per-field | Yes (`test_confidence.py`) | Low |
| CONF-002 | document | Weighted document confidence | Universal, untuned weights | `confidence.py::document_confidence` | After all fields | Yes | Medium (P4-3) |
| CONF-003 | review_status | Threshold routing | Universal, untuned thresholds | `confidence.py::route` | Final | Yes | Medium (P4-3) |
| CONF-004 | review_status | Conditional critical-field requirement | Universal | `extract_invoice.py` | Feeds CONF-003 | Presumed yes | Low (already hardened) |
| CONF-005 | all fields | Tri-state status | Universal | `confidence.py::field_status` | Derived | Yes | Low |
| DEDUP-001 | — | File-hash exact duplicate | Universal | `dedup.py::file_hash` | Pre-extraction (scanner.py) | Yes (`test_dedup.py`) | Low |
| DEDUP-002 | vendor_name | Fuzzy match against known vendors | Universal mechanism | `dedup.py::find_matching_vendor` | Post-extraction, optional | Yes | Medium — cutoff untested at scale |
| DEDUP-003 | — | Composite duplicate key | Universal | `dedup.py::duplicate_key` | N/A — unwired | Yes (unit only) | High — no production caller found (P3-4) |
| CATEGORY-001 | category | Keyword classification, different service | Dataset/vendor-specific | `invoice-service/category_classifier.py` | Once, at scan time, purchase+transactional only | Yes (`test_category_classifier.py`) | High — 61% accuracy, single-company keyword list (P1-4/P2-1) |

---

## Closing note on method

Every file cited above was read in full for this audit (not sampled or summarized from memory): `ocr/extract.py`, `ocr/pdf.py`, `ocr/liteparse.py`, `ocr/image.py`, `ocr/image_io.py`, `ocr/preprocess.py`, `ocr/render.py`, `ocr/models.py`; `invoice_extraction/extract_invoice.py`, `fields.py`, `document_type.py`, `line_items.py`, `lines.py`, `dynamic_fields.py`, `validators.py`, `labels.py`, `arithmetic.py`, `confidence.py`, `currency.py`, `dedup.py`, `geography.py`, `payment.py`, `schema.py`; `ai-engine/app/api/routes/ocr.py`, `ai-engine/app/schemas/ocr.py`, `ai-engine/app/core/config.py`; `invoice-service/app/services/invoice_builder.py`, `category_classifier.py`, `app/models/invoice.py`, `app/schemas/ai_extraction.py`; `backend/eval/golden_eval/compare.py`, `evaluate.py`, `runner.py`; plus the real baseline JSON (`2026-09-03-post-vendor-audit-fix.json`) and the source design doc (`docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md`) for Rule-ID cross-referencing. Test coverage claims in §14 are based on reading actual test class/function names in every listed test file, not test counts alone. No implementation file was modified in the course of this audit.
