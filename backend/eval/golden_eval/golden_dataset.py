"""The Golden Dataset: manually-verified expected values for the 35 real
documents in test-data/june-2026-petty-cash/receipts/, cross-referenced
against `June Cash book..xlsx` (the `June, 2026` sheet) wherever that sheet
reliably establishes the answer.

METHODOLOGY — read this before trusting or extending any row below.

`vendor`, `invoice_date`, and `total` are anchored on what each document
itself literally prints — independently re-read from a fresh raw-OCR pass
over all 35 documents as part of this audit (LiteParse+PaddleOCR,
`ocr.liteparse.extract_text`), never copied from any prior pipeline run.
Copying the pipeline's own output as "ground truth" would make the
benchmark circular — it would only ever confirm that the system agrees
with itself. The Excel is used to (a) independently corroborate a
document's total/date when OCR alone leaves it ambiguous (matched by
amount, since amounts are specific enough to rarely collide — noted per
row below whenever this is how a value was confirmed), and (b) establish
`category`, which is fundamentally a bookkeeping judgement the actual
human accountant made — the Excel's own category *section* a row falls
under is the single best available ground truth for that field.

`category` is deliberately NOT re-derived from `vendor` — three real cases
below (the two "Farewell cake" bakery purchases and the florist's "Basket")
are filed under "Employee Care" in the ledger despite matching a bakery/
gift keyword that a keyword classifier would otherwise place under "Office
Entertainment" — the ledger recorded *why* the purchase was made, which no
vendor-name keyword can recover. These are genuine, fair classifier gaps
this benchmark exists to surface, not benchmark errors to "fix" by
re-deriving category from vendor after the fact.

`document_type` ground truth reflects each document's real-world nature
(an itemised POS till slip is a "receipt" whether or not the word
"receipt" is printed on it), not merely whichever word happens to appear —
matching this project's own `detect_document_type()` fallback design
(a line-item table + a total, with no type-naming keyword at all, already
resolves to "receipt" at low confidence; see document_type.py). A document
too garbled or ambiguous to confidently assign a real-world type to is
marked UNKNOWN rather than guessed.

A recurring, disclosed pattern: several Express Mart receipts print a date
1-2 days earlier than the Excel's own recorded date for the same amount
(the ledger likely records when an expense was reimbursed/logged, not the
purchase date) — `invoice_date` ground truth always takes the *document's
own* printed date in these cases, per the "vendor/date/total are anchored
on what's printed" rule above, with the Excel cross-reference noted for
context, not substituted in.

Four documents are STIXOR's own internal "Minute Sheet" approval memos —
confirmed on inspection to be monthly rollup approvals for each Excel
category section (their own itemised amounts sum to that section's Excel
total). These are `transactional=False`; `vendor`, `total`, and `category`
are all ABSENT by design (see docs/invoice-ocr-plan.md §12) — a real
expectation, not an unknown.

CRITICAL GENERALIZATION RULE: this file is an evaluation fixture, not
training data. Nothing here is imported by, or referenced from, any
production code path (backend/services/*, backend/libs/*) — grep finds
zero references outside backend/eval/. Every value below describes what
IS true of these 35 specific documents; none of it is a rule the pipeline
follows or should follow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from golden_eval.compare import ABSENT, UNKNOWN

__all__ = ["GoldenDocument", "GOLDEN_DATASET", "UNKNOWN", "ABSENT"]


@dataclass(frozen=True)
class GoldenDocument:
    filename: str
    document_type: Any
    transactional: Any
    vendor: Any
    invoice_date: Any
    total: Any
    category: Any
    #: How this row's ground truth was established — required on every
    #: entry so a reviewer can audit the audit. Cites the raw OCR reading,
    #: the matching Excel row (by amount), or both.
    evidence: str
    #: The exact matching Excel row, when one exists, quoted verbatim for
    #: traceability. None when no Excel row could be reliably matched.
    excel_row: Optional[str] = None


GOLDEN_DATASET: list[GoldenDocument] = [
    GoldenDocument(
        "1WhatsApp Image 2026-09-02 at 9.28.03 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Layers Bakeshop",
        invoice_date="2026-06-29", total=2200.0, category="Employee Care",
        evidence=(
            "Own OCR: 'Nutella 2.5 LBS Rs2200.00 ... Total 2200.00 ... Date:29/06/2026'. "
            "Vendor line 'Layers Bakeshop' printed clearly. Category is 'Employee Care', not "
            "'Office Entertainment', per the matching Excel row (a farewell gift, not a routine snack)."
        ),
        excel_row="row 24: 2026-06-29, 'Techsnetial and Shahid Farewell cake', Rs.2200",
    ),
    GoldenDocument(
        "2121WhatsApp Image 2026-09-02 at 9.28.11 AM.jpeg",
        document_type="receipt", transactional=True, vendor="FSO DHA Filling Station",
        invoice_date=UNKNOWN, total=1000.0, category="Vehicle Running & Maintenance",
        evidence=(
            "Own OCR: 'FSO DHA Filling Station ... Cash Memo ... PETROL 2.07 1000' (single line item, "
            "its amount is the receipt's own total). Document's own 'Date' field printed with no legible "
            "value; Excel corroborates the amount on 2026-06-09 but the document's own date cannot be "
            "independently confirmed, so invoice_date is UNKNOWN per this file's own anchoring rule."
        ),
        excel_row="row 68: 2026-06-09, 'Petrol', Rs.1000",
    ),
    GoldenDocument(
        "223WhatsApp Image 2026-09-02 at 9.28.11 AM.jpeg",
        document_type="invoice", transactional=True, vendor="Azeem Electric & Hardware Store",
        invoice_date="2026-06-18", total=400.0, category="Office Repair & Maintenance",
        evidence=(
            "Own OCR: 'INVOICE ... Bill#85408 18/06/2026 ... Bill Total: 400.00', vendor 'Azeem / "
            "Electric & Hardware Store' printed across two lines. Excel logs this a day later (6/19) — "
            "the document's own printed date wins per this file's anchoring rule."
        ),
        excel_row="row 61: 2026-06-19, 'Toilet repair Commond button', Rs.400",
    ),
    GoldenDocument(
        "2322WhatsApp Image 2026-09-02 at 9.28.08 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-16", total=1100.0, category="Office / Misc Supplies",
        evidence="Own OCR: 'Express Mart ... 16 Jun 2026 ... Grand Total 1,100.00' (Floor Mat + water refill).",
        excel_row="row 36: 2026-06-16, 'Floor Mat +Water', Rs.1100",
    ),
    GoldenDocument(
        "322WhatsApp Image 2026-09-02 at 9.28.11 AM.jpeg",
        document_type="receipt", transactional=True, vendor="STIXOR",
        invoice_date="2026-06-30", total=6000.0, category="Salary",
        evidence=(
            "Own OCR: 'STIXOR ... Cash Receipt ... Mr/Ms Ifra Jamil ... received amount Rs. 6,000/- as "
            "Salary/ Stipend. Date: 30T June 202Y'. An internal salary disbursement, not a purchase — "
            "'vendor' is the issuing entity (STIXOR itself), the only name printed on the voucher."
        ),
        excel_row="row 73: 'Ifra', Rs.6000 (Salary section; no date recorded in the ledger)",
    ),
    GoldenDocument(
        "323232WhatsApp Image 2026-09-02 at 9.28.08 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-18", total=270.0, category="Office / Misc Supplies",
        evidence="Own OCR: 'Express Mart ... 18 Jun 2026 ... Grand Total 270.00' (water refill).",
        excel_row="row 37: 2026-06-18, 'Water', Rs.270",
    ),
    GoldenDocument(
        "3232WhatsApp Image 2026-09-02 at 9.28.11 AM.jpeg",
        document_type="invoice", transactional=True, vendor="Azeem Electric & Hardware Store",
        invoice_date="2026-06-18", total=900.0, category="Office Repair & Maintenance",
        evidence=(
            "Own OCR: 'INVOICE ... Bill#85395 18/06/2026 ... Bill Total: 900.00' (connection pipe, "
            "mirror nut). Same 1-day date pattern as the other Azeem invoices; document's own date wins."
        ),
        excel_row="row 60: 2026-06-19, 'Toilet repair 3 pipes', Rs.900",
    ),
    GoldenDocument(
        "34323WhatsApp Image 2026-09-02 at 9.28.10 AM.jpeg",
        document_type="receipt", transactional=True, vendor="STIXOR",
        invoice_date="2026-06-23", total=500.0, category="Office / Misc Supplies",
        evidence=(
            "Own OCR: 'STIXOR ... Cash Receipt ... received amount Rs.5_cleaning ... Date: 23une 2a26' — "
            "the amount is OCR-merged into one token (a known, previously root-caused OCR-quality issue; "
            "see docs/invoice-ocr-plan.md §11), but the Excel independently confirms the true amount is "
            "Rs.500 for a cleaning payment on the same date, which is accepted as the amount ground truth "
            "here since the document's own OCR cannot resolve it cleanly on its own."
        ),
        excel_row="row 39: 2026-06-23, 'Cleaning', Rs.500",
    ),
    GoldenDocument(
        "34343WhatsApp Image 2026-09-02 at 9.28.08 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-15", total=270.0, category="Office / Misc Supplies",
        evidence="Own OCR: 'Express Mart ... 15 Jun 2026 ... Grand Total 270.00' (water refill).",
        excel_row="row 35: 2026-06-15, 'Water', Rs.270",
    ),
    GoldenDocument(
        "3WhatsApp Image 2026-09-02 at 9.23.39 AM.jpeg",
        document_type="receipt", transactional=True, vendor="KFC",
        invoice_date="2026-06-30", total=20160.0, category="Office Entertainment",
        evidence="Own OCR: 'KFC ... Plckup/Delivery 06/30/2026 ... Amount Inc. Sales Tax 20160'.",
        excel_row="row 18: 2026-06-30, 'KFC', Rs.20160",
    ),
    GoldenDocument(
        "3WhatsApp Image 2026-09-02 at 9.28.03 AM.jpeg",
        document_type="minute_sheet", transactional=False, vendor=ABSENT,
        invoice_date="2026-02-28", total=ABSENT, category=ABSENT,
        evidence=(
            "Own OCR: 'STIXOR ... MINUTE SHEET ... For approval of Rs. 22,875/- ... by CEO ... 28th Feb "
            "2026'. An internal expense-approval memo, not a purchase — see docs/invoice-ocr-plan.md §12 "
            "for why vendor/total/category are correctly absent, never guessed, on this document type. "
            "The memo's own reference date (28 Feb 2026) is what invoice_date should read, regardless of "
            "which month's batch the photo was filed under."
        ),
        excel_row=None,
    ),
    GoldenDocument(
        "43343WhatsApp Image 2026-09-02 at 9.28.10 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Coursera",
        invoice_date="2026-05-04", total=5961.79, category="Office / Misc Supplies",
        evidence=(
            "Own OCR: a NayaPay transaction screenshot — 'coursera COURSERA*666427057... 04May2026 "
            "... Total Amount Rs. 5,961.79'. Category is 'Office / Misc Supplies' per the Excel row's "
            "actual section placement, not 'Employee Training & Education' as a keyword match on "
            "'coursera' alone would suggest — the ledger's own filing is the ground truth for category."
        ),
        excel_row="row 43: 2026-05-04, 'Anam Coursera', Rs.5960 (ledger rounds off the bank fee)",
    ),
    GoldenDocument(
        "4434343WhatsApp Image 2026-09-02 at 9.28.10 AM.jpeg",
        document_type="minute_sheet", transactional=False, vendor=ABSENT,
        invoice_date="2026-06-30", total=ABSENT, category=ABSENT,
        evidence=(
            "Own OCR: 'MINUTE SHEET ... Subject: Repair & Maintenance / Bike Running ... For approval of "
            "Rs. 3,180/- ... 30th June 2026'."
        ),
        excel_row=None,
    ),
    GoldenDocument(
        "454WhatsApp Image 2026-09-02 at 9.28.07 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date=UNKNOWN, total=270.0, category="Office / Misc Supplies",
        evidence=(
            "Own OCR: 'Express Mart ... Grand Total 270.00' — clearly a water refill, but the date line "
            "is fully garbled with no legible day/month. Rs.270 water refills recur on 9 different Excel "
            "dates, so the amount alone cannot disambiguate which one this is — invoice_date is UNKNOWN. "
            "Category is still reliable: every Rs.270 Express Mart water refill in this dataset falls "
            "under the same category regardless of the exact date."
        ),
        excel_row=None,
    ),
    GoldenDocument(
        "5454WhatsApp Image 2026-09-02 at 9.28.09 AM.jpeg",
        document_type=UNKNOWN, transactional=True, vendor="Minhas Pipes & Fittings",
        invoice_date=UNKNOWN, total=UNKNOWN, category=UNKNOWN,
        evidence=(
            "Own OCR: severely fragmented ('MINHAS ... PIPES & FITTIN ... An Underground Hot & Cold "
            "Water Sup[ply] ... Gujranwala Pakistan ... www.minhaspipe.com') — the business name is "
            "legible via the domain name, but no total or date survives the fragmentation and no Excel "
            "row could be reliably matched (no clearly readable amount to match against). transactional "
            "is inferred True from context (a commercial plumbing-supplier document in the petty-cash "
            "batch, no approval/memo wording); document_type is UNKNOWN since even the document's basic "
            "shape (till receipt vs. product label) isn't confidently readable."
        ),
        excel_row=None,
    ),
    GoldenDocument(
        "5WhatsApp Image 2026-09-02 at 9.24.13 AM.jpeg",
        document_type="receipt", transactional=True, vendor="KFC",
        invoice_date="2026-06-09", total=19410.0, category="Office Entertainment",
        evidence=(
            "Own OCR: 'Rs 19,410 ... KFC ...' — heavily garbled throughout, but the vendor and the "
            "stamped amount are legible and the amount is a specific, unlikely-to-collide match against "
            "the ledger; the document's own date field is too garbled to read, so the Excel's date is "
            "accepted here as corroborated via the strong amount match, not substituted blindly."
        ),
        excel_row="row 14: 2026-06-09, 'KFC', Rs.19410",
    ),
    GoldenDocument(
        "5WhatsApp Image 2026-09-02 at 9.28.04 9AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-05", total=270.0, category="Office / Misc Supplies",
        evidence="Own OCR: 'Express Mart ... 05 Jun 2026 ... Grand Total 270.00'.",
        excel_row="row 30: 2026-06-05, 'Water', Rs.270",
    ),
    GoldenDocument(
        "5WhatsApp Image 2026-09-02 at 9.28.04 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-04", total=270.0, category="Office / Misc Supplies",
        evidence="Own OCR: 'Express Mart ... 04 Jun 2026 ... Grand Total 270.00'.",
        excel_row="row 29: 2026-06-04, 'Water', Rs.270",
    ),
    GoldenDocument(
        "655443WhatsApp Image 2026-09-02 at 9.28.09 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-30", total=5800.0, category="Office / Misc Supplies",
        evidence=(
            "Own OCR: 'Express Mart ... 30 Jun 2026 ... Grand Total 5,800.00' — a larger grocery-style "
            "basket (air freshener, insect killer, tissue, tea, water refill)."
        ),
        excel_row="row 42: 2026-06-30, 'Grocery', Rs.5800",
    ),
    GoldenDocument(
        "676WhatsApp Image 2026-09-02 at 9.28.07 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-09", total=270.0, category="Office / Misc Supplies",
        evidence=(
            "Own OCR: 'Express Mart ... 09 Jun 2026 ... Grand Total 270.00'. The Excel has two 6/9 water "
            "entries (Rs.540 and Rs.270) — this one's amount matches the Rs.270 row specifically."
        ),
        excel_row="row 33: 2026-06-09, 'Water', Rs.270",
    ),
    GoldenDocument(
        "6WhatsApp Image 2026-09-02 at 9.23.39 AM.jpeg",
        document_type="invoice", transactional=True, vendor="The Florist by Aimen Tahir",
        invoice_date="2026-06-03", total=6800.0, category="Employee Care",
        evidence=(
            "Own OCR: 'INVOICE ... The Florist by Aimen Tahir ... INVOICE DATE 3 June 2026 ... Basket "
            "Rs6,800 ... Grand Total 6,800' (business name legible in full at the invoice's own footer, "
            "correcting the header's own OCR typo 'Flerisi'). A gift basket bought for a departing "
            "colleague — category is 'Employee Care' per the ledger, not a florist-keyword guess."
        ),
        excel_row="row 21: 2026-06-03, 'Basket Muqeet', Rs.6800",
    ),
    GoldenDocument(
        "6WhatsApp Image 2026-09-02 at 9.24.14 AM.jpeg",
        document_type=UNKNOWN, transactional=UNKNOWN, vendor=UNKNOWN,
        invoice_date=UNKNOWN, total=UNKNOWN, category=UNKNOWN,
        evidence=(
            "Own OCR: near-total garbling ('HFUA', 'TEM', scattered numeric fragments, 'N:070181', "
            "'N:08015') — a stamped 'Rs20420' is visible, coincidentally equal to the Papa John's amount "
            "elsewhere in this dataset, but nothing else on the document corroborates that connection, so "
            "assuming it IS that transaction would be inventing evidence. Even whether this is a genuine "
            "transaction document at all cannot be confidently established from what survives OCR — the "
            "one document in this dataset left fully UNKNOWN across every field, including transactional."
        ),
        excel_row=None,
    ),
    GoldenDocument(
        "6r44343WhatsApp Image 2026-09-02 at 9.28.10 AM.jpeg",
        document_type="invoice", transactional=True, vendor="Azeem Electric & Hardware Store",
        invoice_date="2026-06-15", total=880.0, category="Office Repair & Maintenance",
        evidence="Own OCR: 'INVOICE ... Bill#85106 15/06/2026 ... BillTotal: 880.00' (power plug, three pin, power handle).",
        excel_row="row 59: 2026-06-15, 'Power Plug', Rs.880",
    ),
    GoldenDocument(
        "71WhatsApp Image 2026-09-02 at 9.28.03 AM.jpeg",
        document_type="minute_sheet", transactional=False, vendor=ABSENT,
        invoice_date="2026-06-30", total=ABSENT, category=ABSENT,
        evidence=(
            "Own OCR: 'MINUTE SHEET ... Subject: Office Supplies/Stationary/Others ... For approval of "
            "Rs. 20,180/- ... 30th June 2026'. Its own itemised list (Rs.270 x4, 540, 1100, 2750, 500, "
            "50, 1320, 5800, 5960) sums to the Excel's own 'Office /Misc Supplies' section total — this "
            "is the section's own monthly rollup approval, not a purchase in its own right."
        ),
        excel_row=None,
    ),
    GoldenDocument(
        "7676WhatsApp Image 2026-09-02 at 9.28.08 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-17", total=2750.0, category="Office / Misc Supplies",
        evidence=(
            "Own OCR: 'Express Mart ... 17 Jun 2026 ... Grand Total 2,750.00' (water refill, Nescafe, "
            "sugar). Excel logs this 2 days later (6/19); the document's own printed date wins."
        ),
        excel_row="row 38: 2026-06-19, 'Coffee and Water', Rs.2750",
    ),
    GoldenDocument(
        "878WhatsApp Image 2026-09-02 at 9.28.07 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-08", total=270.0, category="Office / Misc Supplies",
        evidence="Own OCR: 'Express Mart ... 08 Jun 2026 ... Grand Total 270.00'.",
        excel_row="row 31: 2026-06-08, 'Water', Rs.270",
    ),
    GoldenDocument(
        "9733WhatsApp Image 2026-09-02 at 9.28.09 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-28", total=1320.0, category="Office / Misc Supplies",
        evidence=(
            "Own OCR: '28 Jun 2026 ... Grand Total 1,320.00' (water refill + hand towel). The vendor "
            "line itself OCR-reads as 'Exuress Mar' on this specific document, but the real business — "
            "verified against every other receipt from this same shop in the dataset (identical address/"
            "phone/NTN) — is Express Mart; ground truth reflects the real vendor, not this document's own "
            "OCR error."
        ),
        excel_row="row 41: 2026-06-28, 'Water', Rs.1320",
    ),
    GoldenDocument(
        "9897WhatsApp Image 2026-09-02 at 9.28.07 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-09", total=540.0, category="Office / Misc Supplies",
        evidence="Own OCR: 'Express Mart ... 09 Jun 2026 ... Grand Total 540.00' — the other 6/9 water entry.",
        excel_row="row 32: 2026-06-09, 'Water', Rs.540",
    ),
    GoldenDocument(
        "WhatsApp Image 2026-09-02 at 9.23.38 AM.jpeg",
        document_type="minute_sheet", transactional=False, vendor=ABSENT,
        invoice_date="2026-06-30", total=ABSENT, category=ABSENT,
        evidence=(
            "Own OCR: 'MINUTE SHEET ... Subject Office Entertainment / Employee Care ... For approval of "
            "Rs. 115,260/- ... 30th June 2026'. Its own itemised list (20050+19410+800+20420+20320+20160 "
            "= 101,160 Office Entertainment, plus 6800+2400+2700+2200 = 14,100 Employee Care) sums to "
            "exactly the combined Excel totals for both sections that month."
        ),
        excel_row=None,
    ),
    GoldenDocument(
        "WhatsApp Image 2026-09-02 at 9.23sa.38 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Falcon Cash & Carry",
        invoice_date="2026-06-16", total=800.0, category="Office Entertainment",
        evidence="Own OCR: 'Falcon Cash & Carry ... 16 Jun 2026 ... Grand Total 800.00' (Sprite, Mirinda, Pepsi).",
        excel_row="row 15: 2026-06-16, 'Drinks', Rs.800",
    ),
    GoldenDocument(
        "WhatsApp Image 2026-09-02 at 9.28.02 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Layers Bakeshop",
        invoice_date="2026-06-18", total=2700.0, category="Employee Care",
        evidence=(
            "Own OCR: 'Layers Bakeshop ... Parisian 7-layers Cake ... Total 2700.00 ... "
            "Date:18/06/2026'. This photo's OCR also trails into a fragment of the adjacent Nutella "
            "receipt (the same purchase captured as its own separate document elsewhere in this "
            "dataset); ground truth here covers only this document's own primary (first) receipt. "
            "Category is 'Employee Care' (a farewell cake), not a bakery-keyword 'Office Entertainment'."
        ),
        excel_row="row 23: 2026-06-19, 'Anam Farewell cake', Rs.2700",
    ),
    GoldenDocument(
        "WhatsApp Image 2026-09-02 at 9.28.03 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Layers Bakeshop",
        invoice_date="2026-06-11", total=2400.0, category="Employee Care",
        evidence=(
            "Own OCR: 'Layers Bakeshop ... Ferrero Classic 2.5 LBS ... Total 2400.00 ... "
            "Date:11/06/2026'. Category is 'Employee Care' per the ledger (another farewell cake)."
        ),
        excel_row="row 22: 2026-06-11, 'Haseeb Farewell Cake', Rs.2400",
    ),
    GoldenDocument(
        "WhatsApp Image 2026-09-02 at 9.28.04 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Express Mart",
        invoice_date="2026-06-02", total=270.0, category="Office / Misc Supplies",
        evidence="Own OCR: 'Express Mart ... 02 Jun 2026 ... Grand Total 270.00'.",
        excel_row="row 28: 2026-06-02, 'Water', Rs.270",
    ),
    GoldenDocument(
        "WhatsApp Image 22026-09-02 at 9.23.38 AM.jpeg",
        document_type="receipt", transactional=True, vendor="KFC",
        invoice_date="2026-06-02", total=20050.0, category="Office Entertainment",
        evidence=(
            "Own OCR: 'KFC ... Booked At 06/02/2026 ... Amount Inc. Sales Tax 20050 ... SubTotal 20050'. "
            "A corner stamp misreads as 'Rs29050'; the two clean body figures agree on 20050."
        ),
        excel_row="row 13: 2026-06-02, 'KFC', Rs.20050",
    ),
    GoldenDocument(
        "aaWhatsApp Image 2026-09-02 at 9.23.39 AM.jpeg",
        document_type="receipt", transactional=True, vendor="Papa John's",
        invoice_date=UNKNOWN, total=20420.0, category="Office Entertainment",
        evidence=(
            "Own OCR: vendor 'papajohns@livepepper.com' and 'Total: Rs.20420.00' both clearly legible. "
            "The only visible timestamp ('2026/09/01 13:41') is plainly a receipt-fetch/generation time, "
            "not the June order date — no legible June date survives on the document itself, so "
            "invoice_date is UNKNOWN rather than silently substituting the Excel's date."
        ),
        excel_row="row 16: 2026-06-16, 'Papa Johns', Rs.20420",
    ),
]
