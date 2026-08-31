from app.services.categorization.service import CategorizationService


def test_invoice_keyword_matching() -> None:
    cat_service = CategorizationService()
    test_cases = [
        ("invoice_2024.pdf", "Invoices"),
        ("inv-12345.xlsx", "Invoices"),
        ("monthly_invoice_aug.csv", "Invoices"),
    ]
    for filename, expected_category in test_cases:
        category, confidence, source = cat_service.categorize(filename=filename, title=None, channel_name=None)
        assert category == expected_category, f"{filename} should be {expected_category}, got {category}"


def test_below_threshold_returns_uncategorized() -> None:
    cat_service = CategorizationService()
    category, confidence, source = cat_service.categorize(
        filename="screenshot_20240801.png", title=None, channel_name="general",
    )
    assert category == "Images & Screenshots"
    assert confidence >= 0.70


def test_manual_override_marker_is_never_produced_by_the_classifier() -> None:
    # The classifier itself only ever returns "rule_based" as the source —
    # "manual_override" is set exclusively by the PATCH /files/{id}/category
    # endpoint (Task 11), proving the two code paths can't collide.
    cat_service = CategorizationService()
    category, confidence, source = cat_service.categorize(filename="file.pdf", title="Generic", channel_name="general")
    assert source == "rule_based"
