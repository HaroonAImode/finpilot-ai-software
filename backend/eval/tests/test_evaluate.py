"""Synthetic tests for evaluate() and EvaluationReport — verifies the
aggregate report is built correctly from field-level results, independent
of the real 35-document golden dataset (which these tests never touch)."""
from golden_eval.compare import ABSENT, UNKNOWN, FieldStatus
from golden_eval.evaluate import ActualDocument, evaluate
from golden_eval.golden_dataset import GoldenDocument


def _golden(filename, **overrides):
    defaults = dict(
        document_type="receipt", transactional=True, vendor="Riverside Traders",
        invoice_date="2026-06-18", total=500.0, category="Office / Misc Supplies",
        evidence="synthetic test fixture",
    )
    defaults.update(overrides)
    return GoldenDocument(filename=filename, **defaults)


def _actual(filename, **overrides):
    defaults = dict(
        document_type="receipt", transactional=True, vendor="Riverside Traders",
        invoice_date="2026-06-18", total=500.0, category="Office / Misc Supplies",
        review_status="auto_processed",
    )
    defaults.update(overrides)
    return ActualDocument(filename=filename, **defaults)


class TestAccuracyExcludesUnverifiable:
    def test_a_fully_correct_document_scores_correct_on_every_field(self):
        golden = [_golden("a.jpg")]
        actual = {"a.jpg": _actual("a.jpg")}
        report = evaluate(golden, actual)
        for field_name in ("document_type", "transactional", "vendor", "invoice_date", "total", "category"):
            correct, verifiable = report.accuracy(field_name)
            assert (correct, verifiable) == (1, 1)

    def test_unknown_ground_truth_is_excluded_from_the_denominator(self):
        golden = [_golden("a.jpg", vendor=UNKNOWN), _golden("b.jpg")]
        actual = {"a.jpg": _actual("a.jpg", vendor="Anything"), "b.jpg": _actual("b.jpg")}
        report = evaluate(golden, actual)
        correct, verifiable = report.accuracy("vendor")
        assert (correct, verifiable) == (1, 1)  # only b.jpg counts

    def test_all_unverifiable_gives_zero_over_zero_not_a_crash(self):
        golden = [_golden("a.jpg", vendor=UNKNOWN)]
        actual = {"a.jpg": _actual("a.jpg", vendor="Anything")}
        report = evaluate(golden, actual)
        assert report.accuracy("vendor") == (0, 0)


class TestFalsePositiveAggregation:
    def test_false_positives_are_counted_across_documents_and_fields(self):
        golden = [
            _golden("minute_sheet.jpg", total=ABSENT, category=ABSENT),
            _golden("normal.jpg"),
        ]
        actual = {
            "minute_sheet.jpg": _actual("minute_sheet.jpg", total=22875.0, category="Office / Misc Supplies"),
            "normal.jpg": _actual("normal.jpg"),
        }
        report = evaluate(golden, actual)
        assert report.total_false_positives() == 2

    def test_correctly_absent_field_is_not_a_false_positive(self):
        golden = [_golden("minute_sheet.jpg", total=ABSENT)]
        actual = {"minute_sheet.jpg": _actual("minute_sheet.jpg", total=None)}
        report = evaluate(golden, actual)
        assert report.total_false_positives() == 0
        assert report.field_counts("total")[FieldStatus.CORRECT] == 1


class TestMissingDocumentsAndUnexpectedDocuments:
    def test_a_golden_document_with_no_actual_result_is_reported_as_missing(self):
        golden = [_golden("never_scanned.jpg")]
        report = evaluate(golden, {})
        assert report.missing_documents == ["never_scanned.jpg"]
        assert report.documents == []

    def test_an_actual_result_not_in_the_golden_dataset_is_flagged_not_silently_dropped(self):
        golden = [_golden("a.jpg")]
        actual = {"a.jpg": _actual("a.jpg"), "surprise.jpg": _actual("surprise.jpg")}
        report = evaluate(golden, actual)
        unexpected = [d for d in report.documents if d.unexpected_document]
        assert [d.filename for d in unexpected] == ["surprise.jpg"]


class TestRegressionScenario:
    """The exact BEFORE/AFTER shape the benchmark spec asks the evaluator
    to support — two evaluation runs of the same golden dataset, diffed."""

    def test_a_field_that_was_correct_and_becomes_incorrect_is_visible_in_both_reports(self):
        golden = [_golden("a.jpg", vendor="Azeem Electric & Hardware Store")]
        before = evaluate(golden, {"a.jpg": _actual("a.jpg", vendor="Azeem Electric & Hardware Store")})
        after = evaluate(golden, {"a.jpg": _actual("a.jpg", vendor="Azeem")})

        before_status = before.documents[0].results["vendor"].status
        after_status = after.documents[0].results["vendor"].status
        assert before_status == FieldStatus.CORRECT
        assert after_status == FieldStatus.INCORRECT

    def test_review_status_counts_are_available_for_the_auto_processing_summary(self):
        golden = [_golden("a.jpg"), _golden("b.jpg")]
        actual = {
            "a.jpg": _actual("a.jpg", review_status="auto_processed"),
            "b.jpg": _actual("b.jpg", review_status="needs_review"),
        }
        report = evaluate(golden, actual)
        counts = report.review_status_counts()
        assert counts["auto_processed"] == 1
        assert counts["needs_review"] == 1
