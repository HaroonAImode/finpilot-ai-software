"""Pure-function parts of the Gmail client: pulling the small header set out
of a message resource, and walking the MIME part tree to find real
attachments (as opposed to the message body itself, or nested multipart
containers)."""
from app.services.gmail.client import extract_headers, iter_attachment_parts


class TestExtractHeaders:
    def test_pulls_only_the_wanted_headers(self) -> None:
        message = {
            "payload": {
                "headers": [
                    {"name": "From", "value": "Ayesha Khan <ayesha@vendor.com>"},
                    {"name": "Subject", "value": "Invoice #1841"},
                    {"name": "To", "value": "billing@company.com"},
                    {"name": "Date", "value": "Wed, 19 Aug 2026 10:00:00 +0500"},
                    {"name": "X-Spam-Score", "value": "0.1"},
                    {"name": "Received", "value": "from mail.vendor.com ..."},
                ]
            }
        }

        headers = extract_headers(message)

        assert headers == {
            "From": "Ayesha Khan <ayesha@vendor.com>",
            "Subject": "Invoice #1841",
            "To": "billing@company.com",
            "Date": "Wed, 19 Aug 2026 10:00:00 +0500",
        }

    def test_missing_payload_or_headers_returns_empty_not_raises(self) -> None:
        assert extract_headers({}) == {}
        assert extract_headers({"payload": {}}) == {}


class TestIterAttachmentParts:
    def test_a_flat_attachment_part_is_found(self) -> None:
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"size": 120}},
                {
                    "filename": "invoice.pdf", "mimeType": "application/pdf",
                    "body": {"attachmentId": "ATT1", "size": 45000},
                },
            ],
        }

        parts = list(iter_attachment_parts(payload))

        assert len(parts) == 1
        assert parts[0]["filename"] == "invoice.pdf"

    def test_nested_multipart_related_is_walked(self) -> None:
        """multipart/mixed containing multipart/related containing the real
        attachment — a common shape for HTML emails with inline images plus
        a real attachment."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/related",
                    "parts": [
                        {"mimeType": "text/html", "body": {"size": 500}},
                        {
                            "filename": "logo.png", "mimeType": "image/png",
                            "body": {"attachmentId": "ATT_INLINE", "size": 2000},
                        },
                    ],
                },
                {
                    "filename": "receipt.jpg", "mimeType": "image/jpeg",
                    "body": {"attachmentId": "ATT2", "size": 80000},
                },
            ],
        }

        filenames = {part["filename"] for part in iter_attachment_parts(payload)}

        assert filenames == {"logo.png", "receipt.jpg"}

    def test_the_message_body_itself_is_never_yielded(self) -> None:
        """The plain-text/HTML body parts have no filename — that absence is
        the entire signal used to tell them apart from a real attachment."""
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"size": 300}},
                {"mimeType": "text/html", "body": {"size": 600}},
            ],
        }

        assert list(iter_attachment_parts(payload)) == []

    def test_a_part_with_a_filename_but_no_attachment_id_is_skipped(self) -> None:
        """Small inline content can carry a filename-like name without an
        attachmentId (the bytes are inline in body.data instead) — nothing
        to fetch, so it must not be treated as a downloadable attachment."""
        payload = {"filename": "signature.txt", "body": {"data": "aGVsbG8=", "size": 5}}

        assert list(iter_attachment_parts(payload)) == []

    def test_parts_come_back_in_document_order(self) -> None:
        """Load-bearing, not cosmetic: the caller numbers attachments by
        position to build the dedup key (Gmail's own attachmentId is
        ephemeral and unusable for that). If sibling order flipped, a
        re-sync would renumber every attachment and orphan its row."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"filename": "first.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "A1", "size": 1000}},
                {"filename": "second.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "A2", "size": 2000}},
                {"filename": "third.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "A3", "size": 3000}},
            ],
        }

        filenames = [part["filename"] for part in iter_attachment_parts(payload)]

        assert filenames == ["first.pdf", "second.pdf", "third.pdf"]

    def test_document_order_holds_across_nesting(self) -> None:
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/related",
                    "parts": [
                        {"filename": "nested-a.png", "mimeType": "image/png", "body": {"attachmentId": "N1", "size": 1000}},
                        {"filename": "nested-b.png", "mimeType": "image/png", "body": {"attachmentId": "N2", "size": 2000}},
                    ],
                },
                {"filename": "top-level.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "T1", "size": 3000}},
            ],
        }

        filenames = [part["filename"] for part in iter_attachment_parts(payload)]

        assert filenames == ["nested-a.png", "nested-b.png", "top-level.pdf"]

    def test_single_part_message_with_no_nested_parts_list(self) -> None:
        payload = {
            "filename": "statement.xlsx", "mimeType": "application/vnd.ms-excel",
            "body": {"attachmentId": "ATT3", "size": 12000},
        }

        parts = list(iter_attachment_parts(payload))
        assert len(parts) == 1
