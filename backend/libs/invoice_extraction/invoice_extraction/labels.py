"""Label-variant dictionaries — Rule 3.2 of
docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md. This is the
actual maintenance surface of a rules-based system: expect to grow these
lists as new vendor formats are seen in production, per the research doc's
own guidance, rather than treating this as a finished, closed list.

Kept as plain data (not code) deliberately, so extending coverage for a new
vendor's label wording never requires touching extraction logic.
"""

#: Header/single-value field label variants, matched case-insensitively.
#: Longer variants are checked before shorter ones at match time (fields.py)
#: so "invoice number" matches before the more generic "invoice".
LABEL_VARIANTS: dict[str, list[str]] = {
    "invoice_number": [
        "invoice number", "invoice no", "invoice #", "invoice#", "bill no", "bill #", "bill#",
        "inv#", "ref no",
    ],
    "invoice_date": [
        # "invoice dt"/"bill dt" (P1-C) — the same abbreviation convention
        # compact invoice templates already use for other labels ("inv#"
        # above); "date of invoice" — a full, unambiguous phrase. All three
        # are general English business-document vocabulary, not tied to any
        # one template. "issued" alone was considered and left out: without
        # "date" attached it is not reliably date-related at all (a
        # warranty, a document, a ticket can all be "issued").
        "date issued", "invoice date", "invoice dt", "bill date", "bill dt", "date of invoice", "dated", "date",
    ],
    "due_date": ["due date"],
    "total": [
        "grand total", "total due", "amount due", "balance due", "net payable", "net amount",
        "total amount", "total booking amount",
        # P1-D: "bill total" — the same general, template-agnostic
        # "[qualifier] total" vocabulary "grand total"/"total amount"
        # already cover, found live on a real hardware-store invoice.
        # Without this, "Bill Total: 900.00" only matched the bare "total"
        # token — the same weak tier a garbled same-line value from an
        # adjacent quantity column ("Total: 3 900.00", the line-item
        # table's own QTY bleeding onto the label's row) also lands in,
        # and the two tied within a hair of each other on document order
        # alone, letting the garbled one win by a razor-thin, sub-threshold
        # margin. A specific, unambiguous compound phrase like the others
        # in this list resolves it structurally rather than by chance.
        "bill total",
        # Found live on real Pakistani POS receipts (KFC-branded, at least):
        # printed as its own total line, no separate "Grand Total" label at
        # all. Written without the period on "Inc" deliberately — label
        # matching compares against each real word already stripped of
        # trailing punctuation (fields.py's own _clean), so a period here
        # would never match the cleaned "inc" a real "Inc." word becomes.
        "amount inc sales tax",
        "total",
    ],
    "subtotal": ["sub total", "subtotal"],
    "tax": ["sales tax", "gst", "vat", "tax"],
    "discount": ["discount"],
    "ntn": [
        "national tax number", "sales tax registration no", "tax registration no", "ntn no", "strn", "ntn",
    ],
    # P1-B: "supplier"/"merchant"/"billed by" added — the same universal,
    # country/industry-agnostic invoice-header vocabulary as the four
    # already here, not a document-specific addition. "company" was
    # considered and deliberately left out: it is short/generic enough to
    # plausibly label several different things on a real template (a
    # customer's own company field, a registration block), and no live
    # failure justifies the added false-positive risk.
    "vendor_label": ["bill from", "sold by", "billed by", "from", "vendor", "supplier", "merchant"],
    #: "billed to" / "customer name" are listed explicitly rather than left
    #: to the bare "to"/"customer" variants: variants are tried
    #: longest-first, so the specific two-word forms anchor on the real
    #: label instead of the stray "TO" inside "BILLED TO" (seen live on a
    #: real invoice, where matching the bare "to" made the *next column's*
    #: heading the customer's name).
    "customer_label": ["billed to", "customer name", "bill to", "ship to", "to", "customer"],
}

#: Line-item table column header keywords — Rule 4.3. Checked against
#: single words and adjacent-word bigrams (line_items.py), so multi-word
#: headers like "Unit Price" / "Hourly rate" are covered without needing a
#: separate phrase-matching pass.
LINE_ITEM_HEADER_KEYWORDS: dict[str, list[str]] = {
    "description": ["description", "item", "items", "particulars"],
    "qty": ["qty", "quantity", "hours"],
    "rate": ["rate", "unit price", "hourly rate", "price"],
    "amount": ["amount", "line total", "total", "subtotal", "sub total"],
}

#: Rule 4.5 — a row containing any of these marks the end of the line-item
#: region and the start of the totals section.
TOTALS_SECTION_KEYWORDS: list[str] = [
    "subtotal", "sub total", "tax", "gst", "sales tax", "discount", "total", "grand total", "amount due",
]

#: P1-C — a date-shaped line labeled with one of these phrases belongs to a
#: *different* field than invoice_date, never to it, regardless of how
#: clean or well-positioned the date on that line otherwise looks. General,
#: closed, universal invoice/receipt vocabulary — the exact "Invoice Date
#: vs Due Date" confusion the phase's own task brief calls out, plus the
#: other common date types a real invoice/statement can carry. Deliberately
#: NOT the bare "due"/"pay" a real business name could coincidentally start
#: with — every phrase here is a specific, unambiguous multi-word compound
#: (or, for "pay by", a fixed two-word idiom with no other business
#: meaning).
NON_INVOICE_DATE_LABEL_PHRASES: list[str] = [
    "due date", "payment due", "pay by",
    "order date", "delivery date", "payment date", "statement date", "transaction date",
]

#: P1-D — a monetary value labeled with one of these belongs to a
#: *different* financial field than the transaction total, never to it,
#: regardless of how cleanly the amount itself parses. Found live (the
#: exact same structural shape as NON_INVOICE_DATE_LABEL_PHRASES above): a
#: two-word "Sub Total: 900.00" line's own bare "total" token was matching
#: LABEL_VARIANTS["total"]'s own generic "total" variant, so
#: find_label_anchored_text(lines, "total") returned the *subtotal* ahead
#: of a real "Grand Total" line further down the page — confirmed via a
#: direct reproduction, not assumed. The identical risk exists for "Total
#: Tax"/"Total Discount" lines. General, closed, universal invoice/receipt
#: vocabulary, not any one document's — "cash"/"change"/"tip" are a
#: settlement record (how much was tendered/returned), never the invoice's
#: own transaction amount, the same distinction Rule 3.3 already draws
#: elsewhere for line-item vs totals-section rows.
NON_TOTAL_FIELD_LABEL_PHRASES: list[str] = [
    "sub total", "subtotal", "sales tax", "gst", "vat", "tax", "discount",
    "cash", "change", "amount paid", "payment received", "tip",
]

#: Payment status evidence — checked in order (first match wins), more
#: specific phrases before the generic single word they'd otherwise be
#: confused with ("paid in full" before bare "paid"). Only ever assigned
#: when one of these phrases is actually present on the document — never a
#: default (see docs/invoice-ocr-plan.md's receipt-robustness notes on why
#: "not detected" must never silently become "PENDING").
PAYMENT_STATUS_KEYWORDS: list[tuple[str, str]] = [
    (r"\bpartially\s*paid\b", "PARTIALLY_PAID"),
    (r"\bpaid\s*in\s*full\b", "PAID"),
    (r"\bfully\s*paid\b", "PAID"),
    (r"\bunpaid\b", "UNPAID"),
    (r"\bbalance\s*due\b", "DUE"),
    (r"\bamount\s*due\b", "DUE"),
    (r"\bpending\b", "PENDING"),
    (r"\bpaid\b", "PAID"),
]
