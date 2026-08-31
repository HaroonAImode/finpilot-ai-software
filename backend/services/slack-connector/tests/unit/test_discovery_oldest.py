"""walk_conversation_messages must not re-fetch its own cursor boundary.

Found by running two real incremental syncs back to back: every channel that
had files re-reported "1 message" and re-discovered the same files as new, even
though nothing had changed in Slack since the first run.

Cause: `conversations.history` was called with `inclusive=True` unconditionally.
That flag was harmless before Phase 1, when `oldest` was never supplied at all.
Once Phase 1 started passing `oldest=<newest message already processed>`, the
inclusive flag made Slack hand back that exact message again on every
subsequent sync — the boundary is meant to mean "already seen up to and
including this", not "start here again".
"""
from unittest.mock import AsyncMock

import pytest

from app.services.slack.discovery import DiscoveryService


def _service() -> DiscoveryService:
    service = DiscoveryService.__new__(DiscoveryService)
    service.client = AsyncMock()
    service._slack_call = AsyncMock(
        return_value={"messages": [], "response_metadata": {}}
    )
    return service


@pytest.mark.asyncio
async def test_oldest_is_requested_exclusive_of_the_boundary_message() -> None:
    service = _service()

    async for _ in service.walk_conversation_messages("C1", oldest="1700000000.000100"):
        pass

    call_kwargs = service._slack_call.call_args
    params = call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs.get("params")
    assert params["oldest"] == "1700000000.000100"
    assert params["inclusive"] is False, (
        "inclusive=True re-fetches the message at `oldest` on every incremental "
        "sync, which is the message the previous run already fully processed"
    )


@pytest.mark.asyncio
async def test_inclusive_is_false_even_without_a_cursor() -> None:
    """Slack ignores `inclusive` when no oldest/latest bound is set, so this is
    just consistency — but it must never silently flip back to True."""
    service = _service()

    async for _ in service.walk_conversation_messages("C1"):
        pass

    call_kwargs = service._slack_call.call_args
    params = call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs.get("params")
    assert params["inclusive"] is False
    assert "oldest" not in params


@pytest.mark.asyncio
async def test_a_message_exactly_at_the_cursor_is_not_returned_twice() -> None:
    """End-to-end shape of the bug: simulate Slack behaving correctly for an
    exclusive `oldest` (i.e. not re-sending the boundary message) and confirm
    the walk only yields what a real incremental run should see — nothing."""
    service = DiscoveryService.__new__(DiscoveryService)
    service.client = AsyncMock()

    async def fake_slack_call(method, params=None):
        # A correctly-behaving Slack API, given inclusive=False, returns no
        # messages when the only message in the channel is the boundary itself.
        assert params.get("inclusive") is False
        return {"messages": [], "response_metadata": {}}

    service._slack_call = fake_slack_call

    seen = [msg async for msg, _ in service.walk_conversation_messages("C1", oldest="1700000000.000100")]
    assert seen == []
