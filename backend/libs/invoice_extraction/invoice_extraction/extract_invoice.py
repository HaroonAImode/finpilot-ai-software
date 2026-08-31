"""Top-level orchestrator: wires label-anchored field extraction (fields.py),
line-item table reconstruction (line_items.py), arithmetic cross-validation
(arithmetic.py), and composite confidence scoring (confidence.py) into one
ExtractedInvoice. This is Stage 2 of docs/invoice-ocr-plan.md's pipeline —
takes `ocr.ExtractionResult` (Stage 1's output) in, no AI/LLM call anywhere.
"""
from dataclasses import replace
from datetime import date
from typing import Optional

from ocr import ExtractionResult, PositionedWord

from invoice_extraction import arithmetic, confidence
from invoice_extraction.candidates import Candidate
from invoice_extraction.currency import detect_currency
from invoice_extraction.document_type import (
    EXTERNAL_NON_TRANSACTIONAL_TYPES, detect_document_type, find_approval_amount_text, is_transactional,
)
from invoice_extraction.dynamic_fields import discover_fields
from invoice_extraction.fields import (
    date_candidate_cleared_confidence_floor, dynamic_total_candidate_evidence, find_invoice_date_candidate_selection,
    find_label_anchored_text, find_total_candidate_selection, find_vendor_candidate_selection,
    total_candidate_cleared_confidence_floor, vendor_candidate_cleared_confidence_floor,
)
from invoice_extraction.geography import detect_city, detect_country
from invoice_extraction.line_items import detect_columns, find_header_row, find_totals_boundary, reconstruct_rows
from invoice_extraction.lines import group_into_lines
from invoice_extraction.payment import detect_payment_status
from invoice_extraction.schema import ArithmeticValidation, ExtractedInvoice, FieldValue, LineItem, not_found
from invoice_extraction.validators import (
    find_date_in_text, is_plausible_invoice_number, is_plausible_ntn, parse_money,
)


def _text_field(lines, field_name: str, base_conf: float, validator) -> FieldValue:
    found = find_label_anchored_text(lines, field_name)
    if not found:
        return not_found()
    text, page, bbox = found
    value = text.strip()
    if not validator(value):
        return FieldValue(value=None, confidence=base_conf * 0.3, method="label_anchor", page=page, bbox=bbox)
    return FieldValue(value=value, confidence=base_conf, method="label_anchor+pattern", page=page, bbox=bbox)


def _comparable(value: object) -> str:
    """A normalised form for "is this the same value?" comparisons across
    the parsed canonical fields and the raw printed text the dynamic-field
    scan returns. Numbers compare numerically (so 93.0 and "$93.00" match),
    everything else case-insensitively as text."""
    if isinstance(value, (int, float)):
        return f"num:{float(value)}"
    text = str(value).strip()
    money = parse_money(text)
    if money is not None and any(c.isdigit() for c in text) and not any(c.isalpha() for c in text):
        return f"num:{float(money)}"
    return text.lower()


def _money_field(lines, field_name: str, base_conf: float, skip_lines: set[int] | None = None) -> FieldValue:
    found = find_label_anchored_text(lines, field_name, skip_lines=skip_lines)
    if not found:
        return not_found()
    text, page, bbox = found
    amount = parse_money(text)
    if amount is None:
        return FieldValue(value=None, confidence=base_conf * 0.3, method="label_anchor", page=page, bbox=bbox)
    return FieldValue(value=amount, confidence=base_conf, method="label_anchor+pattern", page=page, bbox=bbox)


#: A discovered field's key counts as total-shaped if it contains one of
#: these markers — deliberately substring checks (dynamic_fields.py's own
#: _key_for collapses a glued OCR word like "BillTotal:" straight to
#: "billtotal", one token, no underscore, so an exact-match label list the
#: way LABEL_VARIANTS uses one would never fire here) — while explicitly
#: excluding subtotal/tax/discount, which also contain "total"-adjacent
#: substrings but are never themselves the grand total.
_TOTAL_LIKE_KEY_MARKERS = ("total", "amountdue", "amount_due", "balancedue", "balance_due", "payable")
_NOT_TOTAL_KEY_MARKERS = ("subtotal", "sub_total", "tax", "discount")


def _looks_like_a_total_key(key: str) -> bool:
    if any(marker in key for marker in _NOT_TOTAL_KEY_MARKERS):
        return False
    return any(marker in key for marker in _TOTAL_LIKE_KEY_MARKERS)


def _dynamic_total_candidate(
    discovered: list, subtotal: Optional[float], tax: Optional[float], discount: Optional[float],
    line_items_amount_sum: Optional[float],
) -> Optional[Candidate]:
    """Last-resort candidate: a field the generic label/value discovery
    pass (dynamic_fields.py) already found and correctly parsed, whose own
    label just isn't one of LABEL_VARIANTS["total"]'s known variants. Found
    live: a real invoice printed "BillTotal:" (glued, no space) with the
    actual total right next to it — discover_fields already read it
    correctly, it just never got consulted for the canonical `total` field
    at all. Deliberately requires *exactly one* matching discovered field,
    the same conservative standard find_total_anywhere holds itself to —
    two candidates is ambiguous, not a reason to guess.

    Builds a Candidate (not a FieldValue) so it can be merged into the same
    candidate list fields.py's own label/positional total candidates
    populate — see find_total_candidates' own docstring for why this
    candidate is built here rather than in fields.py itself."""
    matches = [f for f in discovered if _looks_like_a_total_key(f.key)]
    if len(matches) != 1:
        return None
    amount = parse_money(matches[0].value)
    if amount is None:
        return None
    reference = arithmetic.total_reference_amount(subtotal, tax, discount, line_items_amount_sum)
    return Candidate(
        field_name="total", value=matches[0].value, method="positional_fallback",
        page=matches[0].page, bbox=matches[0].bbox,
        evidence=dynamic_total_candidate_evidence(amount, reference),
    )


def _total_field(
    lines, base_conf: float, skip_lines: set[int] | None = None, discovered: list | None = None,
    subtotal: Optional[float] = None, tax: Optional[float] = None, discount: Optional[float] = None,
    line_items_amount_sum: Optional[float] = None, item_body: Optional[range] = None,
) -> FieldValue:
    """P1-D (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md
    §6): uses find_total_candidate_selection (fields.py) rather than the
    old three-tier waterfall (a plain label lookup, else find_total_
    anywhere, else _dynamic_total — each tier returning on its own first
    success). A structural bug this closes, confirmed via direct
    reproduction: LABEL_VARIANTS["total"]'s own bare "total" variant
    matches the *second word* of "Sub Total:"/"Total Tax:"/"Total
    Discount:" lines, so the old label lookup — which stops at the first
    line matching any "total" variant, anywhere in the document — could
    return a subtotal, a tax, or a discount value as if it were the grand
    total.

    Every total-shaped candidate anywhere in the document (four label
    tiers, scored by how authoritative the label wording itself is —
    strong/explicit, bare "total", or the weaker Amount-Due/Balance-Due
    family Phase 7 calls out — plus the dynamic-discovery fallback built
    above, plus the pre-existing ultra-conservative unlabeled positional
    match) is judged by the exact same select_candidate() vendor and
    invoice_date already use. Arithmetic consistency against subtotal/tax/
    discount (or the line-item sum) is itself one more piece of evidence,
    never a substitute for P0-B's own post-selection magnitude/source-
    collision validation, which still runs unconditionally on whatever
    wins here — see extract_invoice()'s own P0-B block, unchanged, just
    downstream of this function."""
    reference_context = (subtotal, tax, discount, line_items_amount_sum)
    dynamic = _dynamic_total_candidate(discovered, *reference_context) if discovered else None
    result = find_total_candidate_selection(
        lines, skip_lines, subtotal=subtotal, tax=tax, discount=discount,
        line_items_amount_sum=line_items_amount_sum, item_body=item_body,
        extra_candidates=[dynamic] if dynamic is not None else None,
    )
    if result.winner is None:
        return not_found()
    winner = result.winner
    amount = parse_money(winner.value)
    if amount is None:
        return not_found()
    evidence = dict(winner.evidence)
    ambiguous = result.status == "review"
    low_confidence = not total_candidate_cleared_confidence_floor(winner)
    evidence["ambiguous"] = ambiguous
    evidence["low_confidence"] = low_confidence
    if result.margin is not None:
        evidence["margin"] = result.margin
    confidence = base_conf * 0.7 if (ambiguous or low_confidence) else base_conf
    return FieldValue(
        value=amount, confidence=confidence, method=winner.method, page=winner.page, bbox=winner.bbox,
        evidence=evidence,
    )


def _date_field(lines, base_conf: float) -> FieldValue:
    """P1-C (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md §6):
    uses find_invoice_date_candidate_selection rather than the old
    "first label match, else first positional match" pair
    (find_label_anchored_text + find_date_anywhere), each of which stopped
    at its own first hit and so could never compare an invoice-date-labeled
    value against a due-date-labeled one elsewhere in the same document.

    Mirrors _vendor_field's own two independent uncertainty signals:
    `ambiguous` (a thin-margin selection — two candidates close enough that
    picking one over the other wasn't confident) and `low_confidence` (the
    winner's own evidence never cleared its tier's reliability floor on its
    own terms, lone-candidate or not). A third, date-specific signal is
    layered on top: `ambiguous_format` — the winning text itself is a
    genuinely ambiguous numeric date (validators.is_day_month_ambiguous,
    "06/02/2026") where this pipeline's day-first-preferred parsing had to
    make an unverifiable choice. All three discount confidence the same
    way (×0.7) rather than compounding — the value itself is always
    returned unchanged (recall preserved); only how much this field's own
    status can be trusted is affected."""
    result = find_invoice_date_candidate_selection(lines)
    if result.winner is None:
        return not_found()
    winner = result.winner
    parsed = find_date_in_text(winner.value)
    if parsed is None:
        return not_found()
    evidence = dict(winner.evidence)
    ambiguous = result.status == "review"
    low_confidence = not date_candidate_cleared_confidence_floor(winner)
    ambiguous_format = "day_month_ambiguous" in winner.evidence
    evidence["ambiguous"] = ambiguous
    evidence["low_confidence"] = low_confidence
    evidence["ambiguous_format"] = ambiguous_format
    if result.margin is not None:
        evidence["margin"] = result.margin
    confidence = base_conf * 0.7 if (ambiguous or low_confidence or ambiguous_format) else base_conf
    return FieldValue(
        value=parsed, confidence=confidence, method=winner.method, page=winner.page, bbox=winner.bbox,
        evidence=evidence,
    )


def _customer_field(lines, base_conf: float) -> FieldValue:
    """Label-anchored only ("Bill To"/"To"/"Customer") — no positional
    fallback exists for a customer name the way vendor has one, since
    there's no reliable "customer is conventionally here" position
    convention. Absent on a walk-in retail receipt is the common, correct
    outcome, not a failure."""
    found = find_label_anchored_text(lines, "customer_label")
    if not found:
        return not_found()
    text, page, bbox = found
    return FieldValue(value=text.strip(), confidence=base_conf, method="label_anchor+pattern", page=page, bbox=bbox)


def _vendor_field(lines, base_conf: float, known_vendors: Optional[list[str]] = None) -> FieldValue:
    """P1 (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md §6/
    §11): uses find_vendor_candidate_selection rather than the plain
    find_vendor_name, so this can surface the P1 candidate-selection
    architecture's own evidence/ambiguity status on the field — the exact
    "why did this candidate win?" the audit asked for — rather than only
    the winning text. `find_vendor_name` itself is unaffected and still
    used everywhere else that only wants the plain 4-tuple.

    A thin-margin (REVIEW) selection still returns its winner unchanged —
    vendor's own long-established "never lose the field outright" stance
    is not overturned here — but is marked ambiguous in `evidence` and
    discounted the same way P0-B's total-magnitude check already
    discounts a suspicious value: the number/text itself is never altered,
    only how much this field's own status can be trusted is.

    P1-B: a *lone* candidate that never cleared its own confidence floor is
    discounted the same way — select_candidate's unconditional lone-
    candidate accept (by design, see its own docstring) only ever answers
    "did this beat a rival", never "is this trustworthy on its own terms".
    Found live: a garbled corner stamp ("BiQ-13") was the only thing OCR
    recovered in the header region on a receipt whose real vendor name
    apparently was never extracted at all — reporting that at full
    confidence would be indistinguishable from a genuinely clean, confident
    single-candidate read."""
    result = find_vendor_candidate_selection(lines, known_vendors=known_vendors)
    if result.winner is None:
        return not_found()
    winner = result.winner
    evidence = dict(winner.evidence)
    ambiguous = result.status == "review"
    low_confidence = not vendor_candidate_cleared_confidence_floor(winner)
    evidence["ambiguous"] = ambiguous
    evidence["low_confidence"] = low_confidence
    if result.margin is not None:
        evidence["margin"] = result.margin
    confidence = base_conf * 0.7 if (ambiguous or low_confidence) else base_conf
    return FieldValue(
        value=winner.value.strip(), confidence=confidence, method=winner.method,
        page=winner.page, bbox=winner.bbox, evidence=evidence,
    )


def _build_line_items(lines, page_right_edge: float, base_conf: float) -> list[LineItem]:
    header_idx = find_header_row(lines)
    if header_idx is None:
        return []
    # Columns first: the totals boundary needs to know where the description
    # column is, so a line item whose own description contains a totals word
    # ("Total Booking Amount") isn't mistaken for the totals section.
    columns = detect_columns(lines[header_idx], page_right_edge)
    if not columns:
        return []
    totals_idx = find_totals_boundary(lines, header_idx + 1, columns)
    raw_rows = reconstruct_rows(lines[header_idx + 1:totals_idx], columns)

    items: list[LineItem] = []
    for raw in raw_rows:
        qty = parse_money(raw.qty_text) if raw.qty_text else None
        rate = parse_money(raw.rate_text) if raw.rate_text else None
        amount = parse_money(raw.amount_text) if raw.amount_text else None
        check = arithmetic.check_line_item(qty, rate, amount)
        flags = list(raw.review_flags)
        if check == "fail":
            flags.append("qty_rate_amount_mismatch")

        method = "label_anchor+pattern" if columns else "positional_fallback"
        items.append(
            LineItem(
                description=FieldValue(value=raw.description or None, confidence=base_conf, method=method),
                qty=FieldValue(value=qty, confidence=base_conf, method=method) if qty is not None else not_found(),
                rate=FieldValue(value=rate, confidence=base_conf, method=method) if rate is not None else not_found(),
                amount=FieldValue(value=amount, confidence=base_conf, method=method) if amount is not None else not_found(),
                arithmetic_check=check,
                review_flags=flags,
            )
        )
    return items


def extract_invoice(
    ocr_result: ExtractionResult,
    known_vendors: Optional[list[str]] = None,
    today: Optional[date] = None,
) -> ExtractedInvoice:
    """The whole no-LLM Stage 2 pipeline in one call. `known_vendors` is
    optional — pass the caller's own vendor list to get Rule 3.4/7.3's
    fuzzy-match confidence boost; omitted, vendor extraction still works,
    just without that cross-check.
    """
    today = today or date.today()
    base_conf = ocr_result.confidence  # Rule 6.2's OCR-source component

    lines: list[list[PositionedWord]] = []
    page_right_edge = 0.0
    for page_words in ocr_result.words_by_page:
        page_lines = group_into_lines(page_words)
        lines.extend(page_lines)
        if page_words:
            page_right_edge = max(page_right_edge, max(w.x1 for w in page_words))

    vendor_name = _vendor_field(lines, base_conf, known_vendors)
    customer_name = _customer_field(lines, base_conf)
    invoice_number = _text_field(lines, "invoice_number", base_conf, is_plausible_invoice_number)
    invoice_date = _date_field(lines, base_conf)
    ntn = _text_field(lines, "ntn", base_conf, is_plausible_ntn)
    # The line-item table's header row is excluded from *label* matching for
    # the totals fields: its column headers ("Subtotal", "Total", "Amount")
    # are the very same words as the totals-section labels, and it appears
    # earlier on the page, so a first-match lookup would anchor there and
    # read the first line item's figure as the document's subtotal.
    header_row = find_header_row(lines)
    totals_skip = {header_row} if header_row is not None else None

    # Computed here, ahead of `total`, specifically so _total_field's
    # dynamic-fields fallback can consult it — see that function's own
    # docstring. Re-filtered against canonical_values further down for the
    # response's own `dynamic_fields`, rather than calling discover_fields
    # a second time.
    item_body = (
        range(
            header_row,
            find_totals_boundary(lines, header_row + 1, detect_columns(lines[header_row], page_right_edge)),
        )
        if header_row is not None else None
    )
    raw_discovered_fields = discover_fields(lines, base_conf, item_body)

    subtotal = _money_field(lines, "subtotal", base_conf, totals_skip)
    tax_amount = _money_field(lines, "tax", base_conf, totals_skip)
    discount = _money_field(lines, "discount", base_conf, totals_skip)
    # P1-D: line_items (and its own amount sum) moved ahead of `total` —
    # unchanged in what it computes, only in when, so find_total_candidate_
    # selection's own arithmetic-consistency evidence ("does this candidate
    # reconcile with subtotal+tax-discount, or the line-item sum") can
    # consult it during *selection*, not only in P0-B's own post-selection
    # validation further below, which still runs unconditionally on
    # whatever total_field's selection actually returns.
    line_items = _build_line_items(lines, page_right_edge, base_conf)
    line_item_amounts = [item.amount.value for item in line_items if item.amount.value is not None]
    line_items_amount_sum = sum(line_item_amounts) if line_item_amounts else None
    total = _total_field(
        lines, base_conf, totals_skip, raw_discovered_fields,
        subtotal=subtotal.value, tax=tax_amount.value, discount=discount.value,
        line_items_amount_sum=line_items_amount_sum, item_body=item_body,
    )

    # tax_rate has no label of its own in most real invoices seen (Rule 3.3
    # doesn't define a "tax rate" label variant for this reason) — derived
    # from subtotal/tax_amount when both are present, rather than searched
    # for directly.
    if subtotal.value and tax_amount.value is not None:
        implied_rate = tax_amount.value / subtotal.value
        tax_rate = FieldValue(value=round(implied_rate, 4), confidence=min(subtotal.confidence, tax_amount.confidence), method="computed")
    else:
        tax_rate = not_found()

    # Document-wide evidence scans (currency, geography, document type,
    # payment status) — deliberately run over the whole OCR text rather
    # than being label-anchored the way header fields are, since none of
    # these has a reliable single "label: value" position the way an
    # invoice number or a date does; each of these modules already refuses
    # to guess when its own evidence is absent (see their own docstrings).
    currency = detect_currency(ocr_result.text)
    city = detect_city(ocr_result.text)
    country_value = detect_country(ocr_result.text, city)
    city_field = FieldValue(value=city, confidence=1.0, method="pattern_match") if city else not_found()
    country_field = FieldValue(value=country_value, confidence=1.0, method="pattern_match") if country_value else not_found()
    payment_status = detect_payment_status(ocr_result.text)

    canonical_values = {
        _comparable(f.value)
        for f in (vendor_name, customer_name, invoice_number, invoice_date, ntn, subtotal, tax_amount, total)
        if f.value is not None
    }
    dynamic_fields = [
        # A discovered field restating a canonical one adds nothing — the
        # canonical copy is already parsed, validated and editable. Compared
        # numerically where possible, since the canonical side holds a
        # parsed float (93.0) and the discovered side the printed text
        # ("$93.00") — the same value in two representations. Reuses
        # raw_discovered_fields (computed earlier, ahead of `total`) rather
        # than calling discover_fields a second time.
        f for f in raw_discovered_fields
        if _comparable(f.value) not in canonical_values
    ]

    detected = detect_document_type(
        ocr_result.text, has_line_items=bool(line_items), has_total=total.value is not None,
    )
    document_type = (
        FieldValue(value=detected.document_type, confidence=detected.confidence, method="pattern_match")
        if detected.document_type else not_found()
    )

    # The one data rule that makes non-transactional handling worth having:
    # an internal approval memo states amounts, and those amounts are worth
    # keeping — but as something the document *mentions*, never as a
    # transaction total. Moving it out of `total` here (rather than letting
    # it sit there flagged) is what stops it reaching category assignment,
    # arithmetic validation, spend aggregates and the accounting hand-off,
    # all of which read `total` and none of which should count a memo.
    transactional = is_transactional(detected.document_type)
    amount_mentioned: Optional[float] = None
    # P0-A (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md P0-1):
    # a quotation/purchase order is excluded from every cashbook query
    # purely via `transactional=False` below — its own fields are never
    # cleared, because unlike an internal memo it names a real vendor and a
    # real quoted/ordered total that legitimately describes the document
    # itself. See EXTERNAL_NON_TRANSACTIONAL_TYPES's own docstring.
    if not transactional and detected.document_type not in EXTERNAL_NON_TRANSACTIONAL_TYPES:
        # Preferred source is the approval sentence itself ("For approval of
        # Rs. 22,875/-") — on these documents that figure is the subject of
        # the request, while any total-shaped value found elsewhere is more
        # likely one of the individual bills it lists. Falls back to
        # whatever total/subtotal extraction did find, so the amount is kept
        # either way rather than discarded with the transactional fields.
        approval_text = find_approval_amount_text(ocr_result.text)
        amount_mentioned = parse_money(approval_text) if approval_text else None
        if amount_mentioned is None:
            amount_mentioned = total.value if total.value is not None else subtotal.value
        total = not_found()
        subtotal = not_found()
        tax_amount = not_found()
        discount = not_found()
        tax_rate = not_found()
        # `vendor_name` is a purchase-transaction concept — the positional
        # fallback (fields.find_vendor_name) has no notion of "vendor
        # doesn't apply here" and simply returns whatever prominent text
        # sits at the top of the page, which on an internal memo is its own
        # letterhead, not a business being purchased from. Found live via
        # the golden-dataset benchmark (docs/invoice-ocr-plan.md §15): all
        # 4 confirmed non-transactional documents in the real dataset
        # correctly cleared total/subtotal/tax_amount above but still
        # reported a "vendor" — the one field this block forgot, and the
        # only field the benchmark found producing false positives. Cleared
        # here for the same reason the financial fields above are: nothing
        # downstream (category assignment, the cashbook, a vendor-spend
        # aggregate) should ever read a memo's own letterhead as if it were
        # who money was paid to.
        vendor_name = not_found()

    line_item_checks = [item.arithmetic_check for item in line_items if item.arithmetic_check != "not_checked"]
    line_items_pass_rate = (
        sum(1 for c in line_item_checks if c == "pass") / len(line_item_checks) if line_item_checks else None
    )
    totals_pass = arithmetic.check_totals(subtotal.value, tax_amount.value, discount.value, total.value)
    tax_rate_plausible = arithmetic.is_plausible_tax_rate(subtotal.value, tax_amount.value)
    date_plausible = arithmetic.is_plausible_invoice_date(invoice_date.value, today)

    # P0-B (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md
    # P0-2/P0-3): `totals_pass` above only ever fires when a `subtotal` was
    # also extracted — the real, live failure this exists to close is a
    # document with NO subtotal at all (the overwhelming common shape of a
    # simple POS receipt), where a structurally-valid-looking total has
    # nothing to be cross-checked against and sails through untested. Falls
    # back to the line-item sum only when no subtotal exists; None (never a
    # guessed "plausible") when neither exists at all — same "nothing to
    # say" convention check_totals/is_plausible_tax_rate already use.
    # (line_item_amounts/line_items_amount_sum: P1-D moved this computation
    # ahead of `total` — see that call site's own comment — reused here
    # unchanged rather than recomputed a second time.)
    total_reference = arithmetic.total_reference_amount(
        subtotal.value, tax_amount.value, discount.value, line_items_amount_sum,
    )
    magnitude_plausible = arithmetic.is_plausible_total_magnitude(total.value, total_reference)
    # A second, independent P0-B signal found live investigating the exact
    # real failure this audit's P0-2 finding is named for: `subtotal` and
    # `total` had resolved to the *identical* page+bbox — two different
    # label matchers had anchored onto the same OCR-garbled text region, so
    # both fields carried the same wrong number and agreed with each other
    # by construction, defeating the ratio check above entirely (a false
    # "consistent" reading when the shared source is itself the defect).
    # Never a legitimate document shape: a real receipt always prints two
    # conceptually different fields as two separate lines, even when their
    # values happen to coincide (no tax charged, so subtotal == total).
    total_shares_source = total.value is not None and any(
        arithmetic.shares_source_location(total.page, total.bbox, other.page, other.bbox)
        for other in (subtotal, tax_amount, discount)
    )
    if magnitude_plausible is False or total_shares_source:
        # The value itself is never modified or discarded here — "never
        # silently repair OCR" — only ever flagged. A person reviewing this
        # invoice still sees the exact number the pipeline extracted and
        # can correct or confirm it; what changes is that it can no longer
        # reach auto_processed (see arithmetic_ok below) or Saved Records'
        # cashbook totals without a human looking at it first (a
        # needs_review invoice is excluded from every cashbook query until
        # validated — invoice-service's own status filter, unchanged here).
        total = replace(total, confidence=total.confidence * 0.5)
    # P1-D compatibility fix: this used to overwrite `total.evidence`
    # outright, which would have silently discarded _total_field's own new
    # per-candidate evidence (ambiguous/low_confidence/
    # candidate_reconciles_with_reference/etc.) the moment this P0-B block
    # ran — merging preserves both without changing any of these three
    # keys' own values, position, or meaning.
    total = replace(total, evidence={
        **(total.evidence or {}),
        "arithmetic_consistent": totals_pass,
        "within_document_magnitude": magnitude_plausible,
        "shares_source_with_another_field": total_shares_source,
    })

    arithmetic_ok = totals_pass
    if arithmetic_ok is None and line_items_pass_rate is not None:
        arithmetic_ok = line_items_pass_rate >= 0.9
    # A known-implausible date or tax rate is exactly as disqualifying for
    # auto-processing as a totals mismatch is — found live as a real,
    # would-be false positive: `arithmetic_ok` previously only ever looked
    # at `totals_pass`, so a document with a flagged `invoice_date_
    # implausible` (or `tax_rate_implausible`) review flag could still
    # route to auto_processed once critical fields were otherwise present,
    # silently carrying a known-wrong value into the ledger. Escalating
    # either False signal here means the review_flags a caller already
    # sees and the routing decision can never disagree with each other.
    # `magnitude_plausible is False` and `total_shares_source` (P0-B) join
    # the same escalation for the same reason.
    if date_plausible is False or tax_rate_plausible is False or magnitude_plausible is False or total_shares_source:
        arithmetic_ok = False

    review_flags: list[str] = []
    # "No total found" is a defect on a purchase document and simply not
    # applicable to an internal memo — flagging one would tell a reviewer
    # something is broken when nothing is.
    if total.value is None and transactional:
        review_flags.append("no_total_found")
    if not transactional:
        review_flags.append("non_transactional_document")
    if totals_pass is False:
        review_flags.append("totals_arithmetic_mismatch")
    if tax_rate_plausible is False:
        review_flags.append("tax_rate_implausible")
    if date_plausible is False:
        review_flags.append("invoice_date_implausible")
    if magnitude_plausible is False:
        review_flags.append("total_magnitude_implausible")
    if total_shares_source:
        review_flags.append("total_shares_source_with_another_field")
    if document_type.value == "invoice" and invoice_number.value is None:
        # The one remaining case where a missing field silently forces
        # needs_review_high_priority (see critical_present below) with no
        # visible signal in review_flags explaining why — found live during
        # a routing-logic audit: a reviewer had no way to tell "this was
        # blocked on a technicality" apart from "this extraction is bad."
        review_flags.append("invoice_number_required_but_missing")

    if known_vendors and vendor_name.value:
        from invoice_extraction.dedup import find_matching_vendor

        matched = find_matching_vendor(vendor_name.value, known_vendors)
        if matched:
            vendor_name = FieldValue(
                value=matched, confidence=min(1.0, vendor_name.confidence + 0.1),
                method=f"{vendor_name.method}+vendor_match", page=vendor_name.page, bbox=vendor_name.bbox,
            )

    field_confidences = {
        name: confidence.field_confidence(base_conf, field.method)
        for name, field in {
            "vendor_name": vendor_name, "invoice_number": invoice_number, "invoice_date": invoice_date,
            "ntn": ntn, "subtotal": subtotal, "tax_amount": tax_amount, "total": total,
        }.items()
        if field.value is not None
    }
    doc_confidence = confidence.document_confidence(field_confidences, arithmetic_ok)
    # `invoice_number` is only held out as critical for a document
    # confidently classified as a formal "invoice" — real evidence from a
    # 35-real-receipt dataset found `invoice_number` FOUND on 0/35 informal
    # retail/petty-cash receipts (none of these carry a formal reference
    # number the way a B2B invoice does), which meant this check blocked
    # auto-processing on literally every document regardless of how
    # accurate everything else was: a false-positive review condition, not
    # a genuine financial-risk one, for anything that isn't an invoice. For
    # a document_type=="invoice", the stricter bar stays — a real invoice
    # missing its own reference number is still a genuine signal worth
    # holding back on.
    required_fields = (
        (total, vendor_name, invoice_number) if document_type.value == "invoice" else (total, vendor_name)
    )
    critical_present = all(f.value is not None for f in required_fields)
    if transactional:
        status = confidence.route(doc_confidence, critical_present, arithmetic_ok)
    else:
        # A non-transactional document is not judged by transactional
        # criteria — it has no total by design, so route() would read that
        # as a critical field missing and escalate it to high priority as
        # though extraction had failed. It always goes to ordinary review
        # instead: never auto_processed, because diverting a document out
        # of financial processing is a decision a person should confirm
        # (Confirm / Process as Transaction / Reject), and never
        # high-priority, because nothing is actually broken.
        status = "needs_review"

    return ExtractedInvoice(
        vendor_name=vendor_name, invoice_number=invoice_number, invoice_date=invoice_date, ntn=ntn,
        subtotal=subtotal, tax_rate=tax_rate, tax_amount=tax_amount, total=total,
        line_items=line_items,
        arithmetic_validation=ArithmeticValidation(
            line_items_pass_rate=line_items_pass_rate, totals_pass=totals_pass,
            tax_rate_plausible=tax_rate_plausible, date_plausible=date_plausible,
            magnitude_plausible=magnitude_plausible,
        ),
        document_confidence=doc_confidence,
        extraction_source=ocr_result.method,
        review_status=status,
        review_flags=review_flags,
        page_dimensions=ocr_result.page_dimensions,
        customer_name=customer_name,
        currency=currency,
        document_type=document_type,
        classification_reason=detected.reason,
        transactional=transactional,
        amount_mentioned=amount_mentioned,
        payment_status=payment_status,
        city=city_field,
        country=country_field,
        dynamic_fields=dynamic_fields,
        raw_text=ocr_result.text,
    )
