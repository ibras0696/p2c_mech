from datetime import UTC, datetime, timedelta

from app.bot.handlers.actions import validate_session_for_run
from app.bot.session_state import PlatformSession


def test_validate_session_for_run_requires_session() -> None:
    assert validate_session_for_run(None) is not None


def test_validate_session_for_run_requires_access_token() -> None:
    session = PlatformSession(access_token="", cf_bm="cf", updated_at=datetime.now(UTC))
    assert validate_session_for_run(session) is not None


def test_validate_session_for_run_requires_cf_bm() -> None:
    session = PlatformSession(access_token="token", cf_bm="", updated_at=datetime.now(UTC))
    assert validate_session_for_run(session) is not None


def test_validate_session_for_run_accepts_complete_session() -> None:
    session = PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    assert validate_session_for_run(session) is None


def test_validate_session_for_run_rejects_whitespace_tokens() -> None:
    session = PlatformSession(access_token="  ", cf_bm="  ", updated_at=datetime.now(UTC))
    assert validate_session_for_run(session) is not None


def test_validate_session_for_run_rejects_stale_session() -> None:
    session = PlatformSession(
        access_token="token",
        cf_bm="cf",
        updated_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert validate_session_for_run(session) is not None
