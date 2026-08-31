"""I/O layer: scans the 35 real documents through the actual running
pipeline (same HTTP path a real upload takes), adapts the result into
`ActualDocument`s, prints the report the benchmark spec asks for, and
saves/loads/diffs baseline snapshots for regression tracking.

Deliberately kept separate from evaluate.py (pure logic, no I/O) so the
comparison/aggregation code is testable with zero network or filesystem
dependency — see tests/test_evaluate.py.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import httpx

from golden_eval.compare import FieldStatus
from golden_eval.evaluate import ActualDocument, EvaluationReport, FIELD_COMPARATORS, evaluate
from golden_eval.golden_dataset import GOLDEN_DATASET

REPO_ROOT = Path(__file__).resolve().parents[3]
RECEIPTS_DIR = REPO_ROOT / "test-data" / "june-2026-petty-cash" / "receipts"
AI_ENGINE_URL = "http://localhost:8007/api/v1/ai/ocr/extract"
CATEGORY_CLASSIFIER_PATH = (
    REPO_ROOT / "backend" / "services" / "invoice-service" / "app" / "services" / "category_classifier.py"
)


def _load_category_classifier():
    """Imports the real invoice-service module directly from source — the
    exact same import-by-path trick this dataset's earlier scan scripts
    already used, reusing the production classifier rather than
    reimplementing or duplicating its logic here."""
    spec = importlib.util.spec_from_file_location("category_classifier", CATEGORY_CLASSIFIER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.classify_category


def _fv(field: object) -> object:
    """Unwraps this pipeline's own `{value, confidence, method, ...}`
    field-result shape down to its bare value; passes any already-bare
    scalar through unchanged."""
    return field.get("value") if isinstance(field, dict) else field


def _to_actual_document(filename: str, record: dict, classify_category) -> ActualDocument:
    vendor = _fv(record.get("vendor_name"))
    item_descriptions = [_fv(li.get("description")) for li in (record.get("line_items") or [])]
    # P1-E.1 bug fix: this runner had never applied invoice_builder.py's own
    # "purchases only, transactional only" gate before calling
    # classify_category (category=None unconditionally otherwise) — harmless
    # while classify_category only ever saw vendor_name/item_descriptions
    # (both correctly cleared to ABSENT on a non-transactional document, so
    # there was nothing for it to match), but raw_text is never cleared this
    # way, and a minute sheet's own administrative wording ("Subject: Repair
    # & Maintenance...") could reach classify_category's own raw_text
    # fallback and produce a category a real scan (through invoice_builder.
    # py) never would. Mirrors that real gate exactly, not a new rule.
    category = (
        classify_category(
            vendor_name=vendor, item_descriptions=item_descriptions, filename=filename,
            # raw_text is a top-level key on AI Engine's own response
            # (ExtractedInvoiceResponse.raw_text), not a {value, ...}-shaped
            # field — no _fv() unwrapping needed.
            raw_text=record.get("raw_text"),
        )
        if record.get("transactional") else None
    )
    return ActualDocument(
        filename=filename,
        document_type=_fv(record.get("document_type")),
        transactional=record.get("transactional"),
        vendor=vendor,
        invoice_date=_fv(record.get("invoice_date")),
        total=_fv(record.get("total")),
        category=category,
        review_status=record.get("review_status"),
    )


def scan_documents(*, receipts_dir: Path = RECEIPTS_DIR, ai_engine_url: str = AI_ENGINE_URL) -> dict[str, ActualDocument]:
    """Scans every file in `receipts_dir` through the real, running
    ai-engine — the same `/ocr/extract` endpoint a real Scanner upload
    hits — then runs the real `category_classifier.classify_category` on
    the result, exactly as `invoice_builder.py` does at scan time. No part
    of the pipeline is reimplemented or mocked."""
    classify_category = _load_category_classifier()
    results: dict[str, ActualDocument] = {}
    paths = sorted(p for p in receipts_dir.glob("*") if p.is_file())
    # Generous: a real OCR pass through the full pipeline has been observed
    # taking well over 10 minutes under sustained host resource pressure
    # (PaddleOCR is CPU-bound with no GPU acceleration in this
    # environment, and shares the host with everything else running) — a
    # benchmark run timing out partway through is worse than a slow one
    # that finishes.
    with httpx.Client(timeout=1200) as client:
        for path in paths:
            print(f"scanning {path.name}...", flush=True)
            response = client.post(
                ai_engine_url, files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
            )
            response.raise_for_status()
            results[path.name] = _to_actual_document(path.name, response.json(), classify_category)
    return results


def load_scan_json(path: Path) -> dict[str, ActualDocument]:
    """Loads a previously-saved raw scan (the same flat-list-of-records
    shape earlier ad hoc scan scripts in this project produced, each
    record carrying its own `_file` key) — lets the evaluator re-run
    instantly against an already-scanned result during iteration, without
    needing the ai-engine stack up every time."""
    classify_category = _load_category_classifier()
    records = json.loads(path.read_text(encoding="utf-8"))
    return {r["_file"]: _to_actual_document(r["_file"], r, classify_category) for r in records}


def run_evaluation(actual: dict[str, ActualDocument]) -> EvaluationReport:
    return evaluate(GOLDEN_DATASET, actual)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

_REPORT_FIELDS = [
    ("document_type", "Document Type"),
    ("transactional", "Transactional"),
    ("vendor", "Vendor"),
    ("invoice_date", "Invoice Date"),
    ("total", "Total"),
    ("category", "Category"),
]


def format_summary(report: EvaluationReport) -> str:
    lines = ["Golden Dataset Evaluation", "-" * 40, "", f"Documents evaluated: {len(report.documents)}", ""]
    for field_name, label in _REPORT_FIELDS:
        correct, verifiable = report.accuracy(field_name)
        pct = f"{correct / verifiable * 100:.0f}%" if verifiable else "n/a"
        counts = report.field_counts(field_name)
        unverifiable = counts[FieldStatus.UNVERIFIABLE]
        lines.append(f"{label}:")
        lines.append(f"  Correct: {correct}/{verifiable} verifiable")
        lines.append(f"  Accuracy: {pct}")
        if unverifiable:
            lines.append(f"  Unverifiable (excluded above): {unverifiable}")
        lines.append("")

    lines.append(f"False Positives:\n  {report.total_false_positives()}\n")
    review_counts = report.review_status_counts()
    lines.append(f"Needs Review:\n  {review_counts.get('needs_review', 0) + review_counts.get('needs_review_high_priority', 0)}\n")
    lines.append(f"Auto Processed:\n  {review_counts.get('auto_processed', 0)}\n")

    if report.missing_documents:
        lines.append(f"Golden-dataset documents with no scan result at all: {len(report.missing_documents)}")
        for name in report.missing_documents:
            lines.append(f"  - {name}")
        lines.append("")

    unexpected = [d.filename for d in report.documents if d.unexpected_document]
    if unexpected:
        lines.append(f"Scanned documents not in the golden dataset: {len(unexpected)}")
        for name in unexpected:
            lines.append(f"  - {name}")
        lines.append("")

    return "\n".join(lines)


def format_detail_table(report: EvaluationReport) -> str:
    rows = []
    for doc in sorted(report.documents, key=lambda d: d.filename):
        if doc.unexpected_document:
            rows.append(f"{doc.filename}\n  (not in golden dataset — EXTRA)\n")
            continue
        rows.append(doc.filename)
        for field_name, label in _REPORT_FIELDS:
            result = doc.results[field_name]
            rows.append(f"  {label}: expected={result.expected!r} actual={result.actual!r} -> {result.status.value}")
        rows.append("")
    return "\n".join(rows)


# --------------------------------------------------------------------------
# Baseline save / load / diff
# --------------------------------------------------------------------------

def report_to_baseline(report: EvaluationReport) -> dict:
    """A compact, versionable snapshot: per-document per-field status only
    (not full expected/actual payloads) — enough to detect any change in
    outcome on the next run, small enough to diff cleanly in a PR."""
    return {
        "documents": {
            doc.filename: {f: r.status.value for f, r in doc.results.items()}
            for doc in report.documents if not doc.unexpected_document
        },
        "missing_documents": report.missing_documents,
    }


def save_baseline(report: EvaluationReport, path: Path) -> None:
    path.write_text(json.dumps(report_to_baseline(report), indent=2, sort_keys=True), encoding="utf-8")


def load_baseline(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def diff_baselines(before: dict, after: dict) -> dict:
    """improvements / regressions / newly_missing / newly_incorrect /
    false_positives / unchanged — the exact categories the benchmark spec
    asks a BEFORE/AFTER comparison to surface. Each entry is
    "filename.field"."""
    result = {
        "improvements": [], "regressions": [], "newly_missing": [], "newly_incorrect": [],
        "false_positives": [], "unchanged": [],
    }
    correct, missing, incorrect, false_positive = (
        FieldStatus.CORRECT.value, FieldStatus.MISSING.value, FieldStatus.INCORRECT.value,
        FieldStatus.FALSE_POSITIVE.value,
    )
    before_docs, after_docs = before.get("documents", {}), after.get("documents", {})
    for filename in sorted(set(before_docs) | set(after_docs)):
        before_fields = before_docs.get(filename, {})
        after_fields = after_docs.get(filename, {})
        for field_name in sorted(set(before_fields) | set(after_fields)):
            b, a = before_fields.get(field_name), after_fields.get(field_name)
            key = f"{filename}.{field_name}"
            if b == a:
                result["unchanged"].append(key)
            # A new false positive is the single most severe outcome a
            # change can introduce — flagged first and unconditionally,
            # regardless of what the field's prior status was.
            elif a == false_positive:
                result["false_positives"].append(key)
            # Leaving a false positive behind is always an improvement,
            # even when the new status isn't a full CORRECT — a false
            # positive (confidently, dangerously wrong) is strictly worse
            # than an ordinary MISSING or INCORRECT, so resolving one is
            # never anything but progress. Checked before the plain
            # CORRECT-vs-{INCORRECT,MISSING} improvement case below so a
            # FALSE_POSITIVE -> CORRECT transition (this fix's own exact
            # shape) is never miscategorized as a "regression" by falling
            # through every other branch.
            elif b == false_positive:
                result["improvements"].append(key)
            elif a == correct and b in (incorrect, missing):
                result["improvements"].append(key)
            elif a == missing and b == correct:
                result["newly_missing"].append(key)
            elif a == incorrect and b == correct:
                result["newly_incorrect"].append(key)
            else:
                result["regressions"].append(key)
    return result


def format_diff(diff: dict) -> str:
    lines = ["Golden Dataset Regression Comparison", "-" * 40, ""]
    for label, key in [
        ("Improvements", "improvements"), ("Regressions", "regressions"),
        ("Newly missing", "newly_missing"), ("Newly incorrect", "newly_incorrect"),
        ("New false positives", "false_positives"), ("Unchanged", "unchanged"),
    ]:
        items = diff[key]
        lines.append(f"{label}: {len(items)}")
        if key != "unchanged":
            for item in items:
                lines.append(f"  - {item}")
    return "\n".join(lines)
