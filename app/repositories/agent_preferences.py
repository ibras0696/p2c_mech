from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg  # type: ignore[import-untyped]

DEFAULT_PROFILE_ID = 0


@dataclass
class AgentPreferences:
    active_limit: int
    min_amount: Decimal
    max_amount: Decimal
    updated_at: datetime


class AgentPreferencesRepository(ABC):
    @abstractmethod
    async def save_for_user(self, user_id: int, preferences: AgentPreferences) -> AgentPreferences:
        raise NotImplementedError

    @abstractmethod
    async def current_for_user(self, user_id: int) -> AgentPreferences | None:
        raise NotImplementedError

    async def save(self, preferences: AgentPreferences) -> AgentPreferences:
        return await self.save_for_user(DEFAULT_PROFILE_ID, preferences)

    async def current(self) -> AgentPreferences | None:
        return await self.current_for_user(DEFAULT_PROFILE_ID)


class InMemoryAgentPreferencesRepository(AgentPreferencesRepository):
    def __init__(self) -> None:
        self._preferences_by_user: dict[int, AgentPreferences] = {}

    async def save_for_user(self, user_id: int, preferences: AgentPreferences) -> AgentPreferences:
        self._preferences_by_user[user_id] = preferences
        return preferences

    async def current_for_user(self, user_id: int) -> AgentPreferences | None:
        return self._preferences_by_user.get(user_id)


class PostgresAgentPreferencesRepository(AgentPreferencesRepository):
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def save_for_user(self, user_id: int, preferences: AgentPreferences) -> AgentPreferences:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        await pool.execute(
            """
            insert into agent_user_preferences (
                user_id,
                active_limit,
                min_amount,
                max_amount,
                updated_at
            )
            values ($1, $2, $3, $4, $5)
            on conflict (user_id) do update set
                active_limit = excluded.active_limit,
                min_amount = excluded.min_amount,
                max_amount = excluded.max_amount,
                updated_at = excluded.updated_at
            """,
            user_id,
            preferences.active_limit,
            str(preferences.min_amount),
            str(preferences.max_amount),
            preferences.updated_at,
        )
        return preferences

    async def current_for_user(self, user_id: int) -> AgentPreferences | None:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        row = await pool.fetchrow(
            """
            select active_limit, min_amount, max_amount, updated_at
            from agent_user_preferences
            where user_id = $1
            """,
            user_id,
        )
        if row is None:
            return None
        updated_at = row["updated_at"]
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return AgentPreferences(
            active_limit=int(row["active_limit"]),
            min_amount=Decimal(row["min_amount"]),
            max_amount=Decimal(row["max_amount"]),
            updated_at=updated_at,
        )

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=3)
        return self._pool

    async def _ensure_schema(self, pool: asyncpg.Pool) -> None:
        await pool.execute(
            """
            create table if not exists agent_user_preferences (
                user_id bigint primary key,
                active_limit integer not null,
                min_amount text not null,
                max_amount text not null,
                updated_at timestamptz not null
            )
            """
        )


def build_agent_preferences_repository(*, database_url: str) -> AgentPreferencesRepository:
    if database_url:
        return PostgresAgentPreferencesRepository(database_url)
    return InMemoryAgentPreferencesRepository()
