"""Sync job counter behaviour.

Regression cover for a bug found by running a real sync: 23 files downloaded
successfully to S3, every File row had downloaded=True, yet the SyncJob reported
files_downloaded=0 and files_failed=0. _download_file updated the File rows but
had no reference to the SyncJob, so the job's counters never moved — the UI's
progress line and completion toast read from those counters.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import File, SyncJob
from app.services.sync_orchestrator import SyncOrchestrator


def _orchestrator(download_result=("s3/key", "hash"), verified=True, raises=None):
    orch = SyncOrchestrator.__new__(SyncOrchestrator)  # skip __init__ and its live deps
    manager = MagicMock()
    if raises is not None:
        manager.download_and_store = AsyncMock(side_effect=raises)
    else:
        manager.download_and_store = AsyncMock(return_value=download_result)
    manager.verify_s3_file = AsyncMock(return_value=verified)
    orch.download_manager = manager
    orch.bot_token = "xoxb-test"
    return orch


def _file() -> File:
    return File(slack_file_id="F1", filename="invoice.pdf")


RECORD = {"is_external": False, "url_private_download": "https://files.slack.com/x"}


@pytest.mark.asyncio
async def test_successful_download_increments_files_downloaded() -> None:
    job = SyncJob(files_downloaded=0, files_failed=0)
    file_obj = _file()

    await _orchestrator()._download_file(file_obj, RECORD, job)

    assert file_obj.downloaded is True
    assert job.files_downloaded == 1
    assert job.files_failed == 0


@pytest.mark.asyncio
async def test_counters_accumulate_across_files() -> None:
    job = SyncJob(files_downloaded=0, files_failed=0)
    orch = _orchestrator()

    for _ in range(3):
        await orch._download_file(_file(), RECORD, job)

    assert job.files_downloaded == 3


@pytest.mark.asyncio
async def test_failed_download_increments_files_failed_not_downloaded() -> None:
    job = SyncJob(files_downloaded=0, files_failed=0)
    file_obj = _file()

    await _orchestrator(raises=RuntimeError("connection reset"))._download_file(file_obj, RECORD, job)

    assert file_obj.download_failed is True
    assert job.files_failed == 1
    assert job.files_downloaded == 0


@pytest.mark.asyncio
async def test_failed_s3_verification_counts_as_failed() -> None:
    job = SyncJob(files_downloaded=0, files_failed=0)
    file_obj = _file()

    await _orchestrator(verified=False)._download_file(file_obj, RECORD, job)

    assert file_obj.download_failed is True
    assert file_obj.download_error == "S3 verification failed"
    assert job.files_failed == 1
    assert job.files_downloaded == 0


@pytest.mark.asyncio
async def test_missing_download_url_counts_as_failed() -> None:
    job = SyncJob(files_downloaded=0, files_failed=0)
    file_obj = _file()

    await _orchestrator()._download_file(file_obj, {"is_external": False}, job)

    assert file_obj.download_failed is True
    assert job.files_failed == 1
    assert job.files_downloaded == 0
