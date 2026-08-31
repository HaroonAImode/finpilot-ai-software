"""Turns (golden-dataset ground truth, actual pipeline output) into a
structured EvaluationReport. Pure logic, no I/O and no dependency on how
`actual` was produced (a live scan, a saved JSON, or a synthetic test
fixture) — this is what tests/test_evaluate.py exercises directly with
synthetic documents, independent of the real 35-document dataset.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from golden_eval.compare import (
    FieldResult, FieldStatus, compare_amount, compare_date, compare_exact, compare_text,
)
from golden_eval.golden_dataset import GoldenDocument

#: The six fields the benchmark spec asks for, in report order. Each maps
#: to the comparison function appropriate for its data shape (Rule 5 of the
#: spec: numeric fields must never be string-compared).
FIELD_COMPARATORS = {
    "document_type": compare_exact,
    "transactional": compare_exact,
    "vendor": compare_text,
    "invoice_date": compare_date,
    "total": compare_amount,
    "category": compare_text,
}


@dataclass(frozen=True)
class ActualDocument:
    """The pipeline's actual output for one document, in the shape
    evaluate() consumes — decoupled from whatever raw JSON schema a scan
    script happens to produce (see runner.py for the adapter)."""

    filename: str
    document_type: Optional[str] = None
    transactional: Optional[bool] = None
    vendor: Optional[str] = None
    invoice_date: Optional[str] = None
    total: Optional[float] = None
    category: Optional[str] = None
    review_status: Optional[str] = None


@dataclass(frozen=True)
class DocumentEvaluation:
    filename: str
    results: dict[str, FieldResult]
    review_status: Optional[str]
    #: True when this filename exists in `actual` but not in the golden
    #: dataset at all — a genuine benchmark-maintenance gap (a document was
    #: scanned that the golden dataset doesn't know about), reported
    #: separately rather than silently skipped or scored.
    unexpected_document: bool = False


@dataclass
class EvaluationReport:
    documents: list[DocumentEvaluation] = field(default_factory=list)
    #: Filenames present in the golden dataset with no corresponding
    #: actual result at all (the scan didn't produce output for them) —
    #: distinct from a MISSING *field*, this is a whole missing document.
    missing_documents: list[str] = field(default_factory=list)

    def field_counts(self, field_name: str) -> Counter:
        counts: Counter = Counter()
        for doc in self.documents:
            result = doc.results.get(field_name)
            if result is not None:
                counts[result.status] += 1
        return counts

    def accuracy(self, field_name: str) -> tuple[int, int]:
        """(correct, verifiable_total) — UNVERIFIABLE fields are excluded
        from both numbers entirely, per the spec's explicit "do not
        calculate accuracy against records whose ground truth is genuinely
        unknown" rule."""
        counts = self.field_counts(field_name)
        verifiable = sum(n for status, n in counts.items() if status != FieldStatus.UNVERIFIABLE)
        return counts[FieldStatus.CORRECT], verifiable

    def total_false_positives(self) -> int:
        return sum(self.field_counts(f)[FieldStatus.FALSE_POSITIVE] for f in FIELD_COMPARATORS)

    def review_status_counts(self) -> Counter:
        return Counter(doc.review_status for doc in self.documents if doc.review_status)


def evaluate(golden: list[GoldenDocument], actual: dict[str, ActualDocument]) -> EvaluationReport:
    report = EvaluationReport()
    golden_by_name = {g.filename: g for g in golden}

    for g in golden:
        a = actual.get(g.filename)
        if a is None:
            report.missing_documents.append(g.filename)
            continue
        results = {
            field_name: comparator(field_name, getattr(g, field_name), getattr(a, field_name))
            for field_name, comparator in FIELD_COMPARATORS.items()
        }
        report.documents.append(DocumentEvaluation(g.filename, results, a.review_status))

    for filename, a in actual.items():
        if filename not in golden_by_name:
            report.documents.append(
                DocumentEvaluation(filename, {}, a.review_status, unexpected_document=True)
            )

    return report
