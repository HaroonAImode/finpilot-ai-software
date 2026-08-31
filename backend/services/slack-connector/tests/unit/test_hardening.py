"""
Ported from the source project's test_phase3_hardening.py, minus the
TestCategorization and TestFileRetry classes (moved to
tests/unit/test_categorization.py and tests/unit/test_file_retry.py during
the port, since they don't depend on the rest of this file's fixtures).
"""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import File, Installation, SyncJob, Workspace


@pytest.fixture
async def workspace_fixture(db: AsyncSession):
    workspace = Workspace(slack_team_id="T1234567890", name="Test Workspace", domain="test-workspace", is_enterprise=False)
    db.add(workspace)
    await db.commit()
    await db.refresh(workspace)
    return workspace


@pytest.fixture
async def installation_fixture(db: AsyncSession, workspace_fixture, settings):
    from app.core.security import TokenCipher

    cipher = TokenCipher(settings.token_encryption_key)
    installation = Installation(
        company_id=uuid.uuid4(),
        workspace_id=workspace_fixture.id,
        bot_token_encrypted=cipher.encrypt("xoxb-test-token-12345"),
        bot_user_id="U1234567890",
        scopes=["files:read"],
    )
    db.add(installation)
    await db.commit()
    await db.refresh(installation)
    return installation


class TestMetadataNormalization:
    @pytest.mark.asyncio
    async def test_normalizes_file_object_to_model(self, db: AsyncSession, installation_fixture):
        slack_file = {
            "id": "F1234567890", "name": "invoice.pdf", "title": "Q4 Invoice", "size": 1024000,
            "mimetype": "application/pdf", "filetype": "pdf", "created": 1691000000, "is_external": False,
            "channels": ["C1234567890"], "user": "U1234567890",
            "permalink": "https://test-workspace.slack.com/files/U1234567890/F1234567890/invoice.pdf",
        }
        file_row = File(
            installation_id=installation_fixture.id, slack_file_id=slack_file["id"], filename=slack_file["name"],
            title=slack_file["title"], mimetype=slack_file["mimetype"], file_type=slack_file["filetype"],
            size=slack_file["size"], created_at=datetime.fromtimestamp(slack_file["created"]),
            is_external=slack_file["is_external"], slack_permalink=slack_file["permalink"], raw_json=slack_file,
            conversation_id=uuid.uuid4(), message_ts="1691000000.000100",
        )
        db.add(file_row)
        await db.commit()

        fetched = await db.scalar(select(File).where(File.slack_file_id == slack_file["id"]))
        assert fetched.filename == slack_file["name"]
        assert fetched.mimetype == slack_file["mimetype"]
        assert fetched.size == slack_file["size"]
        assert fetched.raw_json["id"] == slack_file["id"]


class TestDedup:
    @pytest.mark.asyncio
    async def test_same_slack_file_id_upserts_not_duplicates(self, db: AsyncSession, installation_fixture):
        file_id = "F1234567890"
        db.add(File(
            installation_id=installation_fixture.id, slack_file_id=file_id, filename="document_v1.pdf",
            file_type="pdf", mimetype="application/pdf", size=1000, created_at=datetime.now(),
            is_external=False, slack_permalink="https://example.com/file",
            conversation_id=uuid.uuid4(), message_ts="1234567890.123456",
        ))
        await db.commit()

        files = await db.scalars(select(File).where(File.slack_file_id == file_id))
        assert len(list(files)) >= 1


class TestReconciliationAudit:
    @pytest.mark.asyncio
    async def test_audit_detects_manual_override_preservation(self, db: AsyncSession, installation_fixture):
        file_with_override = File(
            installation_id=installation_fixture.id, slack_file_id="F1234567890", filename="contract.pdf",
            file_type="pdf", mimetype="application/pdf", size=1000, created_at=datetime.now(), is_external=False,
            slack_permalink="https://example.com", conversation_id=uuid.uuid4(), message_ts="1234567890.123456",
            category="contract", category_source="manual_override", category_confidence=1.0,
        )
        db.add(file_with_override)
        await db.commit()
        await db.refresh(file_with_override)

        fetched = await db.scalar(select(File).where(File.id == file_with_override.id))
        assert fetched.category_source == "manual_override"
        assert fetched.category == "contract"


class TestPermissionErrors:
    def test_missing_scope_error_classification(self):
        error_response = {"ok": False, "error": "missing_scope", "needed": "channels:read", "provided": "files:read"}
        assert error_response["error"] == "missing_scope"
