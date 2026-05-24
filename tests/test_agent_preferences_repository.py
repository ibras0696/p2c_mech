from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.repositories.agent_preferences import (
    AgentPreferences,
    InMemoryAgentPreferencesRepository,
)


@pytest.mark.asyncio
async def test_in_memory_agent_preferences_repository_roundtrip_by_user() -> None:
    repository = InMemoryAgentPreferencesRepository()
    user_id = 1033560490
    payload = AgentPreferences(
        active_limit=3,
        min_amount=Decimal("100"),
        max_amount=Decimal("500"),
        updated_at=datetime.now(UTC),
    )

    await repository.save_for_user(user_id, payload)
    stored = await repository.current_for_user(user_id)

    assert stored == payload


@pytest.mark.asyncio
async def test_in_memory_agent_preferences_is_isolated_between_users() -> None:
    repository = InMemoryAgentPreferencesRepository()
    first = AgentPreferences(
        active_limit=1,
        min_amount=Decimal("10"),
        max_amount=Decimal("50"),
        updated_at=datetime.now(UTC),
    )
    second = AgentPreferences(
        active_limit=5,
        min_amount=Decimal("500"),
        max_amount=Decimal("1000"),
        updated_at=datetime.now(UTC),
    )

    await repository.save_for_user(1, first)
    await repository.save_for_user(2, second)

    assert await repository.current_for_user(1) == first
    assert await repository.current_for_user(2) == second
