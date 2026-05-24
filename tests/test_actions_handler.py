from datetime import UTC, datetime, timedelta

import pytest
from app.bot.handlers.actions import refresh_session_cache_for_run, validate_session_for_run
from app.bot.session_state import PlatformSession


class FakeSessionRepository:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.save_calls = 0

    async def save(self, session: PlatformSession) -> PlatformSession:
        self.save_calls += 1
        if self.should_fail:
            raise RuntimeError("save failed")
        return session


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


@pytest.mark.asyncio
async def test_refresh_session_cache_for_run_saves_session() -> None:
    session = PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    repository = FakeSessionRepository()

    await refresh_session_cache_for_run(
        user_id=1,
        platform_session_repository=repository,  # type: ignore[arg-type]
        session=session,
    )

    assert repository.save_calls == 1


@pytest.mark.asyncio
async def test_refresh_session_cache_for_run_handles_repository_error() -> None:
    session = PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    repository = FakeSessionRepository(should_fail=True)

    await refresh_session_cache_for_run(
        user_id=1,
        platform_session_repository=repository,  # type: ignore[arg-type]
        session=session,
    )

    assert repository.save_calls == 1
