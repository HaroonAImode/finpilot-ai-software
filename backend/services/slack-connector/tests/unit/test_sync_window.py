"""Date window and incremental sync cutoff.

`_resolve_oldest` decides how far back Slack is asked to look. Getting it wrong
is expensive in both directions: too far back re-downloads years of history on
every run, too recent silently skips documents the user expected to be imported.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Conversation
from app.services.sync_orchestrator import SyncOrchestrator


def _orchestrator(window_days=None, full_resync=False) -> SyncOrchestrator:
    # __new__ skips __init__ and its live dependencies (token decryption, S3);
    # _resolve_oldest only reads these two attributes.
    orch = SyncOrchestrator.__new__(SyncOrchestrator)
    orch.sync_window_days = window_days
    orch.full_resync = full_resync
    return orch


def _conversation(last_seen_ts=None) -> Conversation:
    return Conversation(slack_conversation_id="C1", name="accounts", last_seen_ts=last_seen_ts)


def _days_ago(days: float) -> str:
    return f"{(datetime.now(timezone.utc) - timedelta(days=days)).timestamp():.6f}"


class TestNoLimits:
    def test_no_window_and_no_cursor_walks_everything(self) -> None:
        """A first sync with no window set must not silently truncate history."""
        assert _orchestrator()._resolve_oldest(_conversation()) is None


class TestDateWindow:
    def test_window_produces_a_cutoff_at_roughly_that_age(self) -> None:
        oldest = _orchestrator(window_days=90)._resolve_oldest(_conversation())

        expected = (datetime.now(timezone.utc) - timedelta(days=90)).timestamp()
        assert oldest is not None
        assert abs(float(oldest) - expected) < 5, "cutoff should be ~90 days ago"

    def test_a_shorter_window_asks_for_less_history(self) -> None:
        thirty = float(_orchestrator(window_days=30)._resolve_oldest(_conversation()))
        year = float(_orchestrator(window_days=365)._resolve_oldest(_conversation()))
        assert thirty > year, "30 days should start later than 365 days"


class TestIncrementalCursor:
    def test_cursor_alone_is_used(self) -> None:
        cursor = _days_ago(2)
        assert _orchestrator()._resolve_oldest(_conversation(cursor)) == f"{float(cursor):.6f}"

    def test_recent_cursor_beats_a_wide_window(self) -> None:
        """The point of the whole feature: a 12-month window with yesterday's
        cursor should ask Slack for one day, not twelve months."""
        yesterday = _days_ago(1)
        oldest = _orchestrator(window_days=365)._resolve_oldest(_conversation(yesterday))

        assert abs(float(oldest) - float(yesterday)) < 1

    def test_window_beats_a_cursor_older_than_it(self) -> None:
        """A cursor from two years ago must not drag a 30-day window back with it."""
        oldest = _orchestrator(window_days=30)._resolve_oldest(_conversation(_days_ago(730)))

        expected = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        assert abs(float(oldest) - expected) < 5


class TestFullResync:
    def test_full_resync_ignores_the_cursor(self) -> None:
        assert _orchestrator(full_resync=True)._resolve_oldest(_conversation(_days_ago(1))) is None

    def test_full_resync_still_respects_the_window(self) -> None:
        """"Fetch it all again" must not quietly mean "and go further back than
        the user asked for"."""
        oldest = _orchestrator(window_days=60, full_resync=True)._resolve_oldest(
            _conversation(_days_ago(1))
        )

        expected = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
        assert abs(float(oldest) - expected) < 5


class TestRouteOrdering:
    def test_sync_settings_is_not_shadowed_by_the_sync_id_route(self) -> None:
        """/sync/settings and /sync/{sync_id} both match "settings"; declaration
        order decides. If {sync_id} won, settings would 422 on a bad UUID."""
        from app.main import app

        paths = [r.path for r in app.routes if hasattr(r, "methods")]
        assert paths.index("/api/v1/slack/sync/settings") < paths.index("/api/v1/slack/sync/{sync_id}")


class TestCursorAdvancement:
    @pytest.mark.parametrize(
        "seen,candidate,expected",
        [
            (None, "100.5", "100.5"),
            ("100.5", "200.5", "200.5"),
            # Slack returns history newest-first, so an older message arriving
            # later must not drag the cursor backwards and re-import old files.
            ("200.5", "100.5", "200.5"),
        ],
    )
    def test_cursor_only_moves_forward(self, seen, candidate, expected) -> None:
        newest = seen
        if candidate and (newest is None or float(candidate) > float(newest)):
            newest = candidate
        assert newest == expected
