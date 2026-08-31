"""The browser-upload document library — upload, list, preview, categorise,
soft-delete, and the scanner hand-off.

Storage and the Invoice Service bridge are both patched: this suite is about
this service's own behaviour (tenancy scoping, soft-delete semantics, status
recording), not about MinIO or another service being reachable.
"""
import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import Document, DocumentCategory, ScannerStatus
from app.models.base import Base
from app.services.scanner_bridge import ScannerBridgeError

COMPANY_ID = uuid.uuid4()
OTHER_COMPANY_ID = uuid.uuid4()


@pytest.fixture
def db_session_factory():
    import asyncio

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_setup())
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def client(db_session_factory):
    async def _db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_company_id] = lambda: COMPANY_ID
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def storage():
    """Patches StorageManager everywhere it is constructed, so no test
    touches a real object store."""
    with patch("app.api.routes.documents.StorageManager") as factory:
        instance = MagicMock()
        instance.store_bytes = AsyncMock(return_value=("some-key/file.pdf", "a" * 64))
        instance.s3_client.get_object.return_value = {"Body": io.BytesIO(b"file bytes")}
        factory.return_value = instance
        yield instance


def _upload(client, filename="invoice.pdf", content=b"pdf bytes", **kwargs):
    return client.post(
        "/api/v1/documents/upload",
        files={"file": (filename, content, "application/pdf")},
        **kwargs,
    )


async def _seed(db_session_factory, **overrides) -> uuid.UUID:
    document = Document(
        id=uuid.uuid4(), company_id=overrides.pop("company_id", COMPANY_ID),
        filename="seeded.pdf", mimetype="application/pdf", size=10,
        s3_key="k/seeded.pdf", file_hash="b" * 64,
        category=DocumentCategory.other, scanner_status=ScannerStatus.not_sent,
        **overrides,
    )
    async with db_session_factory() as session:
        session.add(document)
        await session.commit()
    return document.id


class TestUpload:
    def test_a_file_is_stored_and_returned(self, client, storage) -> None:
        response = _upload(client)
        assert response.status_code == 201
        body = response.json()
        assert body["filename"] == "invoice.pdf"
        assert body["category"] == "other"
        # Not scanned on upload — that is a separate, explicit step, because
        # most uploads never become an invoice.
        assert body["scanner_status"] == "not_sent"
        assert body["invoice_id"] is None

    def test_the_s3_key_is_derived_from_our_own_row_id(self, client, storage) -> None:
        """Never from the upload itself: the Email connector hit real MinIO
        rejections (`XMinioInvalidObjectName`) keying on a provider id."""
        response = _upload(client)
        key_id = storage.store_bytes.await_args.args[0]
        assert key_id == response.json()["id"]

    def test_an_empty_file_is_rejected(self, client, storage) -> None:
        assert _upload(client, content=b"").status_code == 400

    def test_an_unknown_category_is_rejected(self, client, storage) -> None:
        response = client.post(
            "/api/v1/documents/upload?category=nonsense",
            files={"file": ("a.pdf", b"x", "application/pdf")},
        )
        assert response.status_code == 400


class TestList:
    def test_only_this_company_s_documents_are_listed(self, client, db_session_factory) -> None:
        import asyncio

        asyncio.get_event_loop().run_until_complete(_seed(db_session_factory))
        asyncio.get_event_loop().run_until_complete(
            _seed(db_session_factory, company_id=OTHER_COMPANY_ID)
        )

        body = client.get("/api/v1/documents/").json()
        assert body["total"] == 1

    def test_a_soft_deleted_document_is_not_listed(self, client, db_session_factory) -> None:
        import asyncio
        from datetime import datetime, timezone

        asyncio.get_event_loop().run_until_complete(
            _seed(db_session_factory, deleted_at=datetime.now(timezone.utc))
        )
        body = client.get("/api/v1/documents/").json()
        assert body["total"] == 0
        assert body["documents"] == []


class TestSoftDelete:
    def test_delete_hides_the_document_without_removing_the_stored_object(
        self, client, db_session_factory, storage,
    ) -> None:
        """The object staying in S3 is the point: an invoice scanned from
        this document must not lose its source file."""
        import asyncio

        document_id = asyncio.get_event_loop().run_until_complete(_seed(db_session_factory))

        assert client.delete(f"/api/v1/documents/{document_id}").status_code == 204
        assert client.get("/api/v1/documents/").json()["total"] == 0
        storage.s3_client.delete_object.assert_not_called()

    def test_a_deleted_document_is_no_longer_addressable_by_id(
        self, client, db_session_factory, storage,
    ) -> None:
        """"Deleted" must mean gone, not merely hidden from the list —
        otherwise the bytes stay fetchable by anyone holding the id."""
        import asyncio

        document_id = asyncio.get_event_loop().run_until_complete(_seed(db_session_factory))
        client.delete(f"/api/v1/documents/{document_id}")

        assert client.get(f"/api/v1/documents/{document_id}/content").status_code == 404
        assert client.delete(f"/api/v1/documents/{document_id}").status_code == 404

    def test_another_company_s_document_cannot_be_deleted(
        self, client, db_session_factory, storage,
    ) -> None:
        import asyncio

        document_id = asyncio.get_event_loop().run_until_complete(
            _seed(db_session_factory, company_id=OTHER_COMPANY_ID)
        )
        assert client.delete(f"/api/v1/documents/{document_id}").status_code == 404


class TestCategory:
    def test_a_category_can_be_corrected(self, client, db_session_factory) -> None:
        import asyncio

        document_id = asyncio.get_event_loop().run_until_complete(_seed(db_session_factory))
        response = client.patch(
            f"/api/v1/documents/{document_id}/category", json={"category": "invoices"},
        )
        assert response.status_code == 200
        assert response.json()["category"] == "invoices"


class TestSendToScanner:
    def test_a_successful_hand_off_records_the_invoice_on_the_document(
        self, client, db_session_factory, storage,
    ) -> None:
        import asyncio

        document_id = asyncio.get_event_loop().run_until_complete(_seed(db_session_factory))
        invoice_id = uuid.uuid4()

        with patch("app.api.routes.documents.send_to_scanner", AsyncMock(return_value=invoice_id)):
            response = client.post(f"/api/v1/documents/{document_id}/send-to-scanner")

        assert response.status_code == 200
        assert response.json()["scanner_status"] == "sent"
        assert response.json()["invoice_id"] == str(invoice_id)

        # Recorded on the row, so the page can show the file already became an
        # invoice instead of inviting the user to send it again.
        assert client.get("/api/v1/documents/").json()["documents"][0]["scanner_status"] == "sent"

    def test_a_failed_hand_off_is_recorded_not_silently_swallowed(
        self, client, db_session_factory, storage,
    ) -> None:
        import asyncio

        document_id = asyncio.get_event_loop().run_until_complete(_seed(db_session_factory))

        with patch(
            "app.api.routes.documents.send_to_scanner",
            AsyncMock(side_effect=ScannerBridgeError("Invoice Service is down")),
        ):
            response = client.post(f"/api/v1/documents/{document_id}/send-to-scanner")

        assert response.status_code == 502
        assert "Invoice Service is down" in response.json()["detail"]
        # The document survives the failure and shows why, so it can be retried.
        assert client.get("/api/v1/documents/").json()["documents"][0]["scanner_status"] == "failed"
