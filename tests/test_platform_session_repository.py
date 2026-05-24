import pytest
from app.bot.session_state import parse_platform_session_from_text
from app.repositories.platform_session import (
    InMemoryPlatformSessionRepository,
    build_platform_session_repository,
)


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
