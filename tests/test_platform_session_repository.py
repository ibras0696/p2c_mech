from datetime import UTC, datetime

import pytest
from app.bot.session_state import PlatformSession, parse_platform_session_from_text
from app.repositories.platform_session import (
    CachedPlatformSessionRepository,
    InMemoryPlatformSessionRepository,
    PlatformSessionCache,
    PlatformSessionRepository,
    build_platform_session_repository,
)


class FakePrimaryRepository(PlatformSessionRepository):
    def __init__(self, stored: PlatformSession | None) -> None:
        self._stored = stored
        self.current_calls = 0
        self.save_calls = 0

    async def save(self, session: PlatformSession) -> PlatformSession:
        self.save_calls += 1
        self._stored = session
        return session

    async def current(self) -> PlatformSession | None:
        self.current_calls += 1
        return self._stored


class FakeSessionCache(PlatformSessionCache):
    def __init__(self, cached: PlatformSession | None = None) -> None:
        self._cached = cached
        self.get_calls = 0
        self.set_calls = 0

    async def get(self) -> PlatformSession | None:
        self.get_calls += 1
        return self._cached

    async def set(self, session: PlatformSession) -> None:
        self.set_calls += 1
        self._cached = session


class BrokenSessionCache(PlatformSessionCache):
    async def get(self) -> PlatformSession | None:
        raise RuntimeError("cache down")

    async def set(self, session: PlatformSession) -> None:
        del session
        raise RuntimeError("cache down")


@pytest.mark.asyncio
async def test_in_memory_platform_session_repository() -> None:
    repository = InMemoryPlatformSessionRepository()
    session = parse_platform_session_from_text("access_token=abc; __cf_bm=cf")

    await repository.save(session)
    stored = await repository.current()

    assert stored == session


def test_build_platform_session_repository_requires_key_with_database_url() -> None:
    with pytest.raises(RuntimeError):
        build_platform_session_repository(
            database_url="postgresql://user:pass@localhost:5432/db",
            encryption_key="",
        )


@pytest.mark.asyncio
async def test_cached_repository_uses_cache_hit_without_db_read() -> None:
    session = PlatformSession(
        access_token="token",
        cf_bm="cf",
        updated_at=datetime.now(UTC),
    )
    primary = FakePrimaryRepository(stored=None)
    cache = FakeSessionCache(cached=session)
    repository = CachedPlatformSessionRepository(primary=primary, cache=cache)

    loaded = await repository.current()

    assert loaded == session
    assert cache.get_calls == 1
    assert primary.current_calls == 0


@pytest.mark.asyncio
async def test_cached_repository_reads_db_on_cache_miss_and_backfills_cache() -> None:
    session = PlatformSession(
        access_token="token",
        cf_bm="cf",
        updated_at=datetime.now(UTC),
    )
    primary = FakePrimaryRepository(stored=session)
    cache = FakeSessionCache(cached=None)
    repository = CachedPlatformSessionRepository(primary=primary, cache=cache)

    loaded = await repository.current()

    assert loaded == session
    assert primary.current_calls == 1
    assert cache.set_calls == 1


@pytest.mark.asyncio
async def test_cached_repository_falls_back_to_db_when_cache_fails() -> None:
    session = PlatformSession(
        access_token="token",
        cf_bm="cf",
        updated_at=datetime.now(UTC),
    )
    primary = FakePrimaryRepository(stored=session)
    repository = CachedPlatformSessionRepository(primary=primary, cache=BrokenSessionCache())

    loaded = await repository.current()

    assert loaded == session
    assert primary.current_calls == 1
