# Rule-Based Invoice/Receipt Structuring — Research Findings & Recommended Rules
### For: FinPilot AI Engine — Invoice Scanner (PyMuPDF + Tesseract, no-LLM structuring layer)
### Compiled from: `Enterprise_Knowledge_Intelligence_Research.md`, `AI_Document_Intelligence_Research.md`, and targeted web research
### Scope: Rules only — no new tool/library recommendations (stack is fixed: PyMuPDF + Tesseract)

---

## 0. HONEST REALITY CHECK (read this first)

Before the rules — one finding needs to be stated plainly because it directly affects your "100% perfect on invoices" requirement.

Across every source reviewed (both uploaded research docs and current industry write-ups on invoice parsing), the same ceiling shows up repeatedly:

> A well-built rules-based extractor (regex + positional heuristics + template matching) reliably handles **~70–80% of invoices cleanly** in a supplier base that's reasonably consistent. The remaining 20–30% — new vendor layouts, wrapped line-item descriptions, multi-page tables, merged cells, unusual label wording — is where pure rules break, "one commit away from throwing my laptop out the window" being the common developer experience.

Two research sources independently confirm this same 70-80% number for rules-only systems, and both note the failure concentrates in **line items**, not header fields (vendor, invoice number, date, total are comparatively easy).

**What this means for you, concretely:**
- Header/single-value fields (invoice number, date, NTN, totals) → rules can get you very close to 100% if you follow the extraction hierarchy in Section 3.
- Line-item tables (multi-row, multi-column, wrapped text, cross-page) → rules alone will not hit 100% across an unbounded set of vendor formats. This is a structural limitation of geometry/regex-based parsing, not a tuning problem — the research is unanimous on this point.
- **The 100% perfect requirement is achievable in production only in combination with a confidence-gated human review queue** — not by making the rules smarter. Every serious rules-based invoice tool in the research (Rossum, Reducto's schema mode, invoice2data-style template systems) ships with exactly this pattern: rules extract, low-confidence fields get routed to a person, the person's correction becomes ground truth. This is a workflow/UX decision, not a new tool — it fits your existing "Needs Review" status in the Invoice CRUD API.

The rest of this document gives you the rules to maximize the rules-only tier's coverage (push that 70-80% baseline as high as it will go) and the confidence-scoring rules to correctly identify the remainder for review, rather than silently shipping wrong numbers into accounting records.

---

## 1. INTAKE & ROUTING RULES

**Rule 1.1 — Format-first routing (no exceptions):**
```
1. Check file signature (magic bytes), not file extension — a renamed .jpg claiming to be .pdf must route correctly
2. If PDF → attempt PyMuPDF text-layer extraction FIRST, always, before considering OCR
3. If PyMuPDF returns a text layer → run Rule 1.2 (quality check) before trusting it
4. If PyMuPDF returns empty/near-empty text → it's a scanned/image-based PDF → render each page to an image (PyMuPDF pixmap, 300 DPI minimum) → route to Tesseract
5. If input is a raw image (JPG/PNG/WhatsApp screenshot) → route straight to Tesseract, but ALWAYS run the preprocessing rules in Section 2 first — never run Tesseract on a raw unprocessed photo
```

**Rule 1.2 — Text-layer quality gate (critical, commonly skipped):**
A PDF having a text layer does not mean the text layer is trustworthy. Before accepting PyMuPDF's output as final, check:
- Character garbling: does the extracted string contain a disproportionate ratio of non-alphanumeric/control characters? (common with broken font encodings/subsetted fonts) → if so, treat as if no text layer exists and fall through to the OCR path
- Suspiciously short text relative to page size/visible content → same fallback
- This single check prevents a known failure class: PDFs that *look* like they have text but are actually corrupted embedded fonts producing gibberish.

**Rule 1.3 — WhatsApp screenshot special case:**
WhatsApp-compressed images have specific, predictable degradation: heavy JPEG compression artifacts, downscaled resolution, and often a screenshot of a screenshot (photo of a phone screen). Flag these for the strongest preprocessing tier (Section 2) and expect materially lower OCR confidence — do not apply the same confidence thresholds you'd use for a clean scanned PDF.

---

## 2. IMAGE PREPROCESSING RULES (before Tesseract runs)

Tesseract's accuracy is disproportionately dependent on preprocessing quality — this is the single highest-leverage rule set available to you at zero marginal cost, and it is confirmed across every OCR source in the research as the difference between "poor" and "competitive with commercial" Tesseract accuracy.

**Mandatory preprocessing pipeline, in this order:**
1. **Deskew** — detect rotation angle (via Hough transform on text lines or Tesseract's own OSD/orientation detection) and correct before OCR. Skewed text is one of the largest accuracy killers.
2. **Binarization** — convert to black/white with adaptive thresholding (not a single global threshold — adaptive handles uneven lighting/shadows on phone photos far better).
3. **Noise removal** — despeckle / remove salt-and-pepper noise common in low-quality scans and compressed images.
4. **Shadow/glare removal** — especially relevant for phone-photographed receipts (thermal paper receipts photographed under indoor lighting are a known worst-case).
5. **Contrast normalization** — especially for faded thermal-paper receipts, which fade within months and are one of the most common "high priority, hardest to read" document types in accounting workflows.
6. **Upscaling low-resolution images** — if resolution is below a practical DPI threshold for reliable character recognition, upscale before OCR rather than after; Tesseract's accuracy drops sharply below a certain effective character height.
7. **Border/background removal** — crop out phone camera background, table edges, fingers, etc. — Tesseract's page segmentation gets confused by contiguous non-document content.

**Rule 2.1 — Tesseract configuration, not just default call:**
- Always use `image_to_data()` output (TSV/dict with per-word bounding boxes and confidence), never `image_to_string()` alone — you cannot rebuild any structure from a flat text blob, and structure is the entire point of this exercise.
- Select the correct Page Segmentation Mode (PSM) deliberately per document type rather than relying on default auto-detection — receipts (narrow, single-column, many short lines) behave very differently from full-page invoices (multi-column, tables) under Tesseract's layout analysis. Test and pin a PSM mode per document class rather than trusting auto-detection blindly.
- Capture and propagate the per-word confidence score — this feeds directly into Section 6 (confidence scoring) and must not be discarded after OCR.

---

## 3. FIELD EXTRACTION HIERARCHY (header/single-value fields)

This is the extraction order that maximizes accuracy on the "easy" fields (vendor, invoice number, date, NTN/tax ID, totals) — these are the fields where rules-based approaches get closest to the 100% you need.

**Rule 3.1 — Label-anchored extraction (primary method, not regex-on-whole-page):**
Don't run a regex against the entire document blindly. Instead:
1. Search for a known label token ("Invoice #", "Invoice No", "Bill To", "Total", "Total Due", "Amount Due", "NTN", "Tax Registration No", "Date", "Due Date") using a **label variant list** per field (see 3.2), not a single fixed string.
2. Once a label is located, extract the value from its spatial or textual neighborhood: the token(s) immediately following it on the same line, OR (for scanned/image documents where you have bounding boxes) the nearest text block to the right of or below the label's bounding box within a defined proximity radius.
3. This label-anchored approach is confirmed in the research as materially more robust than blind whole-document regex, because it survives layout variation as long as the label wording is covered by your variant list — the failure mode shifts from "layout changed" to "vendor used a label we haven't seen," which is a smaller, more maintainable problem.

**Rule 3.2 — Maintain a label-variant dictionary per field, not single strings:**
Each field should map to a list of acceptable label variants, e.g.:
- Invoice number: "Invoice #", "Invoice No", "Invoice Number", "Bill No", "Inv#", "Ref No"
- Total: "Total", "Total Due", "Amount Due", "Grand Total", "Balance Due", "Net Payable"
- Tax ID (Pakistan-specific for your SME context): "NTN", "NTN No", "National Tax Number", "STRN", "Sales Tax Registration No"
- Date: "Date", "Invoice Date", "Bill Date", "Dated"
This dictionary is the actual maintenance surface of a rules-based system — expect to keep expanding it as new vendor formats appear, and log every field that falls back to "not found" so the label list can be grown from real failures rather than guessed upfront.

**Rule 3.3 — Value-pattern validation, not extraction (regex as a filter, not a finder):**
Once a candidate value is located near a label, validate it against a value-shape pattern before accepting it — this catches the common failure where the label-adjacent text isn't actually the value (e.g., a page number or another label got grabbed instead):
- Dates: validate against multiple common formats (DD/MM/YYYY, DD-MM-YYYY, Month DD YYYY, YYYY-MM-DD) — do not assume one date format across all vendors; detect which format a given document uses from its date fields collectively rather than per-field.
- Monetary amounts: validate as a numeric pattern allowing thousands separators, decimal points, and an optional currency symbol/code prefix or suffix — handle both `1,234.56` and `1234.56` and `Rs. 1,234` and `PKR 1,234.00` formats given your Pakistani SME context.
- Invoice numbers: typically alphanumeric with optional separators (`/`, `-`) — validate length/character-class plausibility rather than a rigid single format, since these vary the most across vendors.
- NTN (Pakistan): validate against the known structural format (numeric, fixed digit count with an optional check digit/dash) rather than accepting any adjacent number.

**Rule 3.4 — Vendor/company name extraction:**
- Vendor name is typically the most prominent text block in the top portion of the document (largest font size, or first non-empty text block, or logo-adjacent text) — use font-size metadata from PyMuPDF (`span["size"]`) as a primary signal when available (native PDFs only; not available from Tesseract OCR on images).
- Cross-validate the extracted vendor name against your existing Vendor table (`vendors` service) — a fuzzy string match against known vendors is one of the highest-value, lowest-effort accuracy boosts available to you, since it turns "read an unfamiliar name perfectly" into "match against a small known list," which is a fundamentally easier problem. This also directly supports deduplication (see Section 7).

---

## 4. LINE-ITEM / TABLE RECONSTRUCTION RULES

This is the hardest part and the primary source of accuracy loss in every source reviewed. Apply these rules in order of reliability (most reliable first):

**Rule 4.1 — Ruling-line detection first (most reliable, use when available):**
If the document has visible table borders/gridlines, detect them geometrically (horizontal/vertical line detection on the rendered page image) before falling back to gap-based clustering. A table with actual ruling lines gives you ground-truth cell boundaries — don't discard this signal in favor of guesswork when it's present.

**Rule 4.2 — Gap-based column detection (for borderless tables):**
When no ruling lines exist:
1. Collect all word bounding boxes on the page (from PyMuPDF spans for native PDFs, or Tesseract `image_to_data` for scanned/image documents).
2. **Row clustering**: group words whose y-coordinates fall within a small tolerance band of each other — this is your row boundary.
3. **Column clustering**: within the table region specifically (not the whole page), analyze the x-coordinate gaps between adjacent words across many rows simultaneously — a gap that's consistently wider than the median inter-word gap across multiple rows indicates a column boundary. Do this across the whole table region at once, not row-by-row, because a single row's spacing is unreliable but the aggregate across all rows is stable.
4. Anchor columns to the table header row specifically — locate the header row first (look for the row containing the highest concentration of known column-label keywords: "Description", "Qty", "Quantity", "Rate", "Price", "Unit Price", "Amount", "Total") and use its x-positions as the authoritative column boundaries for all rows below it, rather than re-deriving columns independently per row.

**Rule 4.3 — Header-row keyword anchoring (critical — do this before generic clustering):**
Locate the line-item table's header row by searching for a row containing multiple known column-header keywords together (not just one — a single keyword match is unreliable, but 2+ matching keywords in the same row is a strong table-header signal): "Description"/"Item", "Qty"/"Quantity", "Rate"/"Unit Price"/"Price", "Amount"/"Total"/"Line Total". Everything below this row and above the totals section (Rule 4.5) is the line-item region.

**Rule 4.4 — Known, unavoidable failure modes for this approach (flag, don't silently ignore):**
These are documented across multiple sources as the specific ways geometry-based table reconstruction breaks — build detection for these cases so they route to review rather than producing silently wrong data:
- **Wrapped/multi-line item descriptions**: a single line item's description spans two visual lines but should be one logical row. Detect this by checking whether a "row" (per Rule 4.2) is missing values in the Qty/Rate/Amount columns — if a row has text only in the description column and no numeric values, it's very likely a continuation of the row above, not a new item. Merge it rather than treating it as a separate line item.
- **Merged cells** (e.g., a discount row spanning what would normally be multiple columns): flag rows where far fewer columns have content than the header row defines — this is a strong review-flag signal, not a case to force-fit into the standard column structure.
- **Quantity/price column drift**: OCR column misalignment is a well-documented failure where a value visually meant for one column lands under the adjacent column instead. Sanity-check with Rule 5 (arithmetic validation) — `qty × rate ≈ amount` per row is your best defense here, and a row that fails this check should be flagged even if extraction "succeeded" structurally.
- **Cross-page tables**: a table starting on page N and continuing on page N+1 needs explicit handling — do not treat page N+1's continuation rows as a separate table. Detect continuation by checking whether page N+1's first content region lacks a header row and instead starts directly with item-shaped rows, and by checking whether the running subtotal only appears once (at the true end of the table, not per-page).

**Rule 4.5 — Totals-section boundary detection:**
The line-item region ends and the totals section begins at the first row containing label keywords ("Subtotal", "Sub Total", "Tax", "GST", "Sales Tax", "Discount", "Total", "Grand Total", "Amount Due"). Treat this boundary explicitly — don't let totals-section rows leak into the line-item array, and don't let the last line item get accidentally absorbed into totals parsing.

---

## 5. ARITHMETIC / CROSS-FIELD VALIDATION RULES

This is your single best confidence signal and costs nothing extra to compute — it's confirmed in the research as a core layer of every serious rules-based extraction system, independent of whether an LLM is involved downstream.

**Rule 5.1 — Line-item level check:**
For every extracted line item: `quantity × rate ≈ amount` (within a small rounding tolerance, e.g. ±0.01–0.02 to account for rounding conventions). A mismatch here is a strong signal that column values were misaligned during extraction (Rule 4.4) — flag the specific row, not just the document.

**Rule 5.2 — Document-total level check:**
`sum(line item amounts) ≈ subtotal`, and `subtotal + tax − discount ≈ total` (adapt the exact formula to however your tax/discount fields are structured). This is the check your own system already planned to use ("arithmetic consistency check") — the research strongly reinforces this as the correct approach and one of the two or three most important validation signals available to a rules-based system.

**Rule 5.3 — Tax rate plausibility check:**
If a tax amount and subtotal are both extracted, compute the implied tax rate (`tax / subtotal`) and check it against a small set of known valid rates for your jurisdiction (Pakistan sales tax/GST rates) rather than accepting any computed value — an implausible rate (e.g., 3.7%, when only specific standard rates are valid) is a strong signal that either the tax or subtotal field was misread.

**Rule 5.4 — Date plausibility check:**
Reject or flag dates that are implausible in context: invoice date in the future, invoice date more than some threshold (e.g., 2+ years) in the past, or due date before invoice date. These catch OCR digit-confusion errors (0/8, 1/7, 3/8 are common Tesseract confusions) that a pure format-regex check would miss.

**Rule 5.5 — Cross-check against your own Vendor/history data:**
If the extracted vendor matches a known vendor (Rule 3.4), compare the extracted total against that vendor's historical invoice amount range. A total wildly outside the vendor's normal range (order of magnitude different) is worth a soft confidence penalty even if internally arithmetic-consistent — this catches errors that pass every internal check but are still wrong (e.g., a misread decimal point that shifts the total by 10x or 100x, which passes `subtotal+tax=total` if the same error occurs in both fields, but a full order-of-magnitude mismatch is unlikely for a known repeat vendor).

---

## 6. CONFIDENCE SCORING RULES

Your original plan ("OCR confidence + arithmetic consistency + LLM confidence") is the right shape — here's how to construct it fully rules-based, since the LLM-confidence component is being removed:

**Rule 6.1 — Composite confidence, computed per-field AND per-document:**
Don't just produce one document-level number. Compute confidence per extracted field first (using 6.2–6.4), then aggregate:
- Document-level confidence = weighted combination of: (a) mean of per-field confidences, weighted toward the fields accounting cares most about (total, tax, vendor, invoice number should weigh more than, say, a payment-terms note), (b) the arithmetic validation pass/fail rate from Section 5, (c) the text-layer-vs-OCR source flag (native PDF text-layer extractions should carry inherently higher base confidence than any OCR path, since PyMuPDF's direct text extraction has no character-recognition error at all).

**Rule 6.2 — OCR-source confidence component:**
- Native PDF text layer (PyMuPDF, no OCR involved) → treat as near-maximum base confidence for the *characters themselves* (MuPDF extraction is a deterministic parse, not probabilistic recognition) — but this does NOT mean the *field mapping* is correct, only that the text is correct. Keep these as separate confidence dimensions: "text was read correctly" vs. "text was assigned to the right field."
- Tesseract OCR path → use the actual per-word confidence scores from `image_to_data`, aggregated per field (e.g., average or minimum confidence across the words that make up the extracted value — minimum is safer for critical numeric fields, since one badly-read digit in a total is enough to make the whole field wrong).

**Rule 6.3 — Extraction-method confidence component:**
A field found via a strong label anchor + passed value-pattern validation (Rule 3.3) should score higher than a field found via a weaker fallback strategy (e.g., "took the largest number on the page" as a last-resort total guess). Track *which* rule matched and assign a confidence tier accordingly — label-anchored + pattern-validated > label-anchored only > positional/fallback heuristic only.

**Rule 6.4 — Threshold-based routing (this replaces the LLM step in your original flow):**
- High confidence (all critical fields found via strong rules + arithmetic checks pass) → auto-process, status = "Processed."
- Medium confidence (some fields found via weaker fallback rules, or a minor arithmetic mismatch within tolerance) → status = "Needs Review," surfaced to the user with the specific field(s) flagged, not the whole document — this is far better UX than a blanket "please recheck everything."
- Low confidence (critical field missing entirely, e.g., no total found, or arithmetic check fails badly, or OCR confidence on core fields is below threshold) → status = "Needs Review" with high priority, and consider **not** auto-creating the Invoice record at all until a human confirms — this protects your accounting data integrity, which matters more here than in a general-purpose RAG system.

This threshold-routing pattern is exactly how every rules-based commercial invoice tool covered in the research operates internally (extract with rules/ML → score confidence → route low-confidence fields to a human validation queue) — it is standard practice, not a compromise.

---

## 7. DEDUPLICATION & DATA-INTEGRITY RULES

Directly relevant to your accounting use case (duplicate invoice submission is a real, common problem):

**Rule 7.1 — Duplicate detection via file hash first (cheapest, exact match):**
SHA-256 hash of the uploaded file — if it matches a previously processed file, flag as duplicate before spending any extraction effort.

**Rule 7.2 — Duplicate detection via extracted-field match (catches re-scans/re-photos of the same physical invoice):**
Match on the combination of (vendor + invoice number + total + date) — if all four match an existing record even though the file hash differs (e.g., someone re-photographed the same invoice, or received it via both email and WhatsApp), flag as a likely duplicate for review rather than silently creating a second invoice record.

**Rule 7.3 — Vendor/customer normalization before matching:**
Normalize extracted vendor names (case-folding, whitespace collapse, common suffix normalization like "Pvt Ltd" / "Private Limited" / "(Pvt) Ltd") before running fuzzy matching against your vendor table (Rule 3.4) — this materially improves match rate for both vendor linking and duplicate detection.

---

## 8. OUTPUT STRUCTURE RULES (JSON schema for the extracted invoice)

**Rule 8.1 — Separate "raw extraction" from "validated/final" data:**
Store both: (a) the raw field values exactly as extracted (for audit/debugging — critical when a human corrects a field, you want to know what the rules originally produced), and (b) the current/corrected value after any human review edit. Never overwrite the raw extraction — this is what lets you improve your label-variant dictionaries and rules over time by mining actual failures (directly supports Rule 3.2's maintenance loop).

**Rule 8.2 — Per-field metadata, not just values:**
For every extracted field, store alongside the value: confidence score, extraction method used (which rule matched), and — where available — the source bounding box/page number (needed for your "show exactly what was extracted from where" review UI, and standard practice across every document-intelligence source reviewed for building trust in extracted data).

**Rule 8.3 — Recommended field structure (aligned to your existing Invoice/InvoiceItem models):**
```
{
  "vendor_name": {"value": ..., "confidence": ..., "method": "label_anchor+vendor_match"},
  "invoice_number": {"value": ..., "confidence": ..., "method": "label_anchor+pattern"},
  "invoice_date": {"value": ..., "confidence": ..., "method": "label_anchor+date_pattern"},
  "ntn": {"value": ..., "confidence": ..., "method": "label_anchor+ntn_pattern"},
  "line_items": [
    {"description": ..., "qty": ..., "rate": ..., "amount": ..., "row_confidence": ..., "arithmetic_check": "pass"}
  ],
  "subtotal": {"value": ..., "confidence": ...},
  "tax_rate": {"value": ..., "confidence": ...},
  "tax_amount": {"value": ..., "confidence": ...},
  "total": {"value": ..., "confidence": ...},
  "document_confidence": ...,
  "arithmetic_validation": {"line_items_pass": true/false, "totals_pass": true/false},
  "extraction_source": "pymupdf_text_layer | tesseract_ocr",
  "review_flags": ["line_item_row_3_qty_rate_mismatch", "tax_rate_implausible"]
}
```

**Rule 8.4 — Markdown output (if/when a human-readable version is needed, e.g. for the review UI or an audit export):**
Use Markdown tables specifically for the line-item section (not a flat text dump) — this preserves row/column structure in a form that's both human-readable and still trivially re-parseable if needed later. Keep header fields as a simple key-value list above the table, matching the same field grouping as the JSON schema above so the two output forms stay in sync.

---

## 9. SOURCE SUMMARY (what this report drew from)

- `Enterprise_Knowledge_Intelligence_Research.md` — Document Intelligence Layer stages (Classification → OCR → Layout → Specialized Extraction → Entity/NLP → Validation), specifically the rules for structure-aware processing, key-value pair extraction, table structure handling, and the validation/trust-scoring pattern (Stages 3–7, 14).
- `AI_Document_Intelligence_Research.md` — Tesseract OCR capability/limitation profile (Section 25), PyMuPDF capability/limitation profile (Section 6), the OCR capability comparison table, and the "Entity Extraction" checklist for invoice/receipt-specific fields.
- Independent web research on rule-based invoice parsing in production (regex/template-based extraction layers, the documented ~70–80% rules-only ceiling, label-anchored extraction patterns, and the standard rules→confidence→human-review-queue architecture used across commercial and open-source invoice tools).

*The `FinPilot_AI_Backend_Architecture_Report` was reviewed for system context (Invoice Service, AI Engine, existing OCR routing plan, and CRUD/status model) to keep every rule above compatible with your existing architecture — no backend architecture changes are proposed in this report, per your request.*
