"""Sanity checks on the golden dataset itself — catches an authoring
mistake (a typo'd category, a missing file, an internally inconsistent
non-transactional row) before it silently skews the benchmark."""
from pathlib import Path

import pytest

from golden_eval.compare import ABSENT, UNKNOWN
from golden_eval.golden_dataset import GOLDEN_DATASET

RECEIPTS_DIR = Path(__file__).resolve().parents[3] / "test-data" / "june-2026-petty-cash" / "receipts"

#: Mirrors app/models/invoice.py's SUGGESTED_CATEGORIES — duplicated here
#: deliberately rather than imported, so this test fails loudly (a
#: mismatch) rather than silently importing invoice-service's app package
#: (which would drag a live-service dependency into a benchmark fixture).
SUGGESTED_CATEGORIES = (
    "Office Entertainment", "Employee Care", "Office / Misc Supplies", "Stationary / Others",
    "Employee Training & Education", "Legal & Professional", "Office Repair & Maintenance",
    "Vehicle Running & Maintenance", "Salary",
    "Utilities", "Rent", "Marketing", "Raw Material", "Fuel & Transport", "Travel", "Other",
)

#: Mirrors invoice_extraction/document_type.py's DocumentType literal.
KNOWN_DOCUMENT_TYPES = (
    "invoice", "receipt", "bill", "quotation", "credit_note", "purchase_order",
    "minute_sheet", "approval_request",
)


def test_exactly_35_documents_no_duplicates():
    assert len(GOLDEN_DATASET) == 35
    filenames = [g.filename for g in GOLDEN_DATASET]
    assert len(filenames) == len(set(filenames))


@pytest.mark.parametrize("golden", GOLDEN_DATASET, ids=lambda g: g.filename)
def test_every_filename_exists_on_disk(golden):
    assert (RECEIPTS_DIR / golden.filename).is_file(), f"{golden.filename} not found in {RECEIPTS_DIR}"


@pytest.mark.parametrize("golden", GOLDEN_DATASET, ids=lambda g: g.filename)
def test_every_document_has_evidence(golden):
    assert golden.evidence and golden.evidence.strip(), f"{golden.filename} has no evidence recorded"


@pytest.mark.parametrize("golden", GOLDEN_DATASET, ids=lambda g: g.filename)
def test_category_is_a_real_suggested_category_or_a_sentinel(golden):
    if golden.category in (UNKNOWN, ABSENT):
        return
    assert golden.category in SUGGESTED_CATEGORIES, f"{golden.filename}: {golden.category!r} is not a real category"


@pytest.mark.parametrize("golden", GOLDEN_DATASET, ids=lambda g: g.filename)
def test_document_type_is_recognized_or_a_sentinel(golden):
    if golden.document_type is UNKNOWN:
        return
    assert golden.document_type in KNOWN_DOCUMENT_TYPES, f"{golden.filename}: unrecognized document_type {golden.document_type!r}"


@pytest.mark.parametrize("golden", GOLDEN_DATASET, ids=lambda g: g.filename)
def test_non_transactional_documents_have_absent_financial_fields(golden):
    """A document marked definitively non-transactional must have
    vendor/total/category all ABSENT, never a real value or UNKNOWN — this
    is the single safety-critical invariant docs/invoice-ocr-plan.md §12
    exists to protect: a non-transactional amount must never look like a
    real transaction value."""
    if golden.transactional is not False:
        return
    assert golden.vendor == ABSENT, f"{golden.filename}: non-transactional but vendor is not ABSENT"
    assert golden.total == ABSENT, f"{golden.filename}: non-transactional but total is not ABSENT"
    assert golden.category == ABSENT, f"{golden.filename}: non-transactional but category is not ABSENT"


def test_at_least_one_document_of_each_sentinel_shape_exists():
    """Guards against the golden dataset silently drifting to "everything
    verifiable" or "everything unknown" — both would make the benchmark
    numbers meaningless without anyone noticing."""
    has_unknown = any(UNKNOWN in (g.vendor, g.invoice_date, g.total, g.category, g.document_type) for g in GOLDEN_DATASET)
    has_absent = any(g.vendor == ABSENT for g in GOLDEN_DATASET)
    has_real_value = any(isinstance(g.total, float) for g in GOLDEN_DATASET)
    assert has_unknown, "expected at least one UNKNOWN field somewhere in the golden dataset"
    assert has_absent, "expected at least one ABSENT field (a non-transactional document)"
    assert has_real_value, "expected at least one real, known total value"
