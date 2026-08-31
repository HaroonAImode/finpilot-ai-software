"""Synthetic tests for the baseline diff logic (report_to_baseline,
diff_baselines) — the pure, I/O-free parts of runner.py. No real scan, no
golden dataset, no network."""
from golden_eval.runner import diff_baselines


def _baseline(**docs):
    return {"documents": docs}


class TestDiffBaselines:
    def test_unchanged_fields_are_reported_as_unchanged(self):
        before = _baseline(**{"a.jpg": {"vendor": "CORRECT"}})
        after = _baseline(**{"a.jpg": {"vendor": "CORRECT"}})
        diff = diff_baselines(before, after)
        assert diff["unchanged"] == ["a.jpg.vendor"]
        assert not diff["improvements"] and not diff["regressions"]

    def test_incorrect_to_correct_is_an_improvement(self):
        before = _baseline(**{"a.jpg": {"total": "INCORRECT"}})
        after = _baseline(**{"a.jpg": {"total": "CORRECT"}})
        diff = diff_baselines(before, after)
        assert diff["improvements"] == ["a.jpg.total"]

    def test_missing_to_correct_is_an_improvement(self):
        before = _baseline(**{"a.jpg": {"total": "MISSING"}})
        after = _baseline(**{"a.jpg": {"total": "CORRECT"}})
        diff = diff_baselines(before, after)
        assert diff["improvements"] == ["a.jpg.total"]

    def test_correct_to_missing_is_newly_missing(self):
        before = _baseline(**{"a.jpg": {"total": "CORRECT"}})
        after = _baseline(**{"a.jpg": {"total": "MISSING"}})
        diff = diff_baselines(before, after)
        assert diff["newly_missing"] == ["a.jpg.total"]

    def test_correct_to_incorrect_is_newly_incorrect(self):
        before = _baseline(**{"a.jpg": {"vendor": "CORRECT"}})
        after = _baseline(**{"a.jpg": {"vendor": "INCORRECT"}})
        diff = diff_baselines(before, after)
        assert diff["newly_incorrect"] == ["a.jpg.vendor"]

    def test_a_new_false_positive_is_flagged_regardless_of_prior_status(self):
        before = _baseline(**{"a.jpg": {"vendor": "MISSING"}})
        after = _baseline(**{"a.jpg": {"vendor": "FALSE_POSITIVE"}})
        diff = diff_baselines(before, after)
        assert diff["false_positives"] == ["a.jpg.vendor"]

    def test_resolving_a_false_positive_into_correct_is_an_improvement_not_a_regression(self):
        """Regression guard for a real bug found live in this evaluator's
        own diff logic: a FALSE_POSITIVE -> CORRECT transition (the exact
        shape of the vendor-clearing fix this file's history is named
        for) fell through every explicit branch into the generic
        "regressions" catch-all, because the improvement check only ever
        looked for INCORRECT/MISSING as the prior status. Leaving a false
        positive behind must never be reported as a regression."""
        before = _baseline(**{"a.jpg": {"vendor": "FALSE_POSITIVE"}})
        after = _baseline(**{"a.jpg": {"vendor": "CORRECT"}})
        diff = diff_baselines(before, after)
        assert diff["improvements"] == ["a.jpg.vendor"]
        assert diff["regressions"] == []

    def test_resolving_a_false_positive_into_an_ordinary_miss_is_still_an_improvement(self):
        """Even landing on an *ordinary* miss instead of a full correct
        answer is still strictly better than a confidently-wrong false
        positive — never scored as a regression."""
        before = _baseline(**{"a.jpg": {"total": "FALSE_POSITIVE"}})
        after = _baseline(**{"a.jpg": {"total": "MISSING"}})
        diff = diff_baselines(before, after)
        assert diff["improvements"] == ["a.jpg.total"]
        assert diff["regressions"] == []

    def test_a_document_only_in_one_baseline_is_still_compared_field_by_field(self):
        before = _baseline()
        after = _baseline(**{"new.jpg": {"vendor": "CORRECT"}})
        diff = diff_baselines(before, after)
        # No prior status to compare against ("None" vs "CORRECT") — not a
        # clean improvement/regression shape, so it lands in the
        # catch-all rather than being silently dropped.
        assert "new.jpg.vendor" in diff["regressions"] + diff["improvements"] + diff["unchanged"]
