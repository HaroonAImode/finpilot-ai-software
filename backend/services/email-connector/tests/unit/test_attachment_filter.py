"""Layered attachment filter — docs/email-connector-plan.md §5.

Cheapest checks first: size, then extension. This is what stands between a
mailbox with thousands of attachments and a Documents page full of
signature images and tracking pixels.
"""
import pytest

from app.services.attachment_filter import MIN_ATTACHMENT_SIZE_BYTES, should_import


class TestSizeFilter:
    def test_a_tiny_attachment_is_skipped(self) -> None:
        ok, reason = should_import("invoice.pdf", size=500)
        assert ok is False
        assert reason == "too_small"

    def test_right_at_the_threshold_is_kept(self) -> None:
        ok, _ = should_import("invoice.pdf", size=MIN_ATTACHMENT_SIZE_BYTES)
        assert ok is True

    def test_a_signature_image_is_skipped_by_size_alone(self) -> None:
        """The realistic case this exists for: a 4KB PNG logo in an email
        signature, which would otherwise look identical to a real receipt."""
        ok, reason = should_import("logo.png", size=4096)
        assert ok is False
        assert reason == "too_small"


class TestExtensionFilter:
    @pytest.mark.parametrize("filename", ["invoice.pdf", "receipt.jpg", "statement.xlsx", "bill.docx", "data.csv"])
    def test_allowed_extensions_pass(self, filename: str) -> None:
        ok, reason = should_import(filename, size=50_000)
        assert ok is True
        assert reason is None

    @pytest.mark.parametrize("filename", ["invite.ics", "contact.vcf", "sig.p7s", "key.asc", "banner.gif"])
    def test_denied_extensions_are_skipped_even_if_large(self, filename: str) -> None:
        ok, reason = should_import(filename, size=1_000_000)
        assert ok is False
        assert reason == "denied_extension"

    def test_an_unrecognised_extension_is_skipped_not_imported_by_default(self) -> None:
        ok, reason = should_import("archive.zip", size=50_000)
        assert ok is False
        assert reason == "extension_not_allowed"

    def test_no_extension_at_all_is_skipped(self) -> None:
        ok, reason = should_import("attachment", size=50_000)
        assert ok is False
        assert reason == "extension_not_allowed"

    def test_extension_matching_is_case_insensitive(self) -> None:
        ok, _ = should_import("Invoice.PDF", size=50_000)
        assert ok is True
