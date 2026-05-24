from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC

import asyncpg  # type: ignore[import-untyped]

from app.bot.session_state import PlatformSession
from app.core.crypto import SecretCipher


class PlatformSessionRepository(ABC):
    @abstractmethod
    async def save(self, session: PlatformSession) -> PlatformSession:
        raise NotImplementedError

    @abstractmethod
    async def current(self) -> PlatformSession | None:
        raise NotImplementedError


class InMemoryPlatformSessionRepository(PlatformSessionRepository):
    def __init__(self) -> None:
        self._session: PlatformSession | None = None

    async def save(self, session: PlatformSession) -> PlatformSession:
        self._session = session
        return session

    async def current(self) -> PlatformSession | None:
        return self._session


class PostgresEncryptedPlatformSessionRepository(PlatformSessionRepository):
    def __init__(self, database_url: str, cipher: SecretCipher) -> None:
        self._database_url = database_url
        self._cipher = cipher
        self._pool: asyncpg.Pool | None = None

    async def save(self, session: PlatformSession) -> PlatformSession:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        await pool.execute(
            """
            insert into platform_sessions (
                id,
                access_token_encrypted,
                cf_bm_encrypted,
                updated_at
            )
            values (1, $1, $2, $3)
            on conflict (id) do update set
                access_token_encrypted = excluded.access_token_encrypted,
                cf_bm_encrypted = excluded.cf_bm_encrypted,
                updated_at = excluded.updated_at
            """,
            self._cipher.encrypt(session.access_token),
            self._cipher.encrypt(session.cf_bm),
            session.updated_at,
        )
        return session

    async def current(self) -> PlatformSession | None:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        row = await pool.fetchrow(
            """
            select access_token_encrypted, cf_bm_encrypted, updated_at
            from platform_sessions
            where id = 1
            """
        )
        if row is None:
            return None
        updated_at = row["updated_at"]
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return PlatformSession(
            access_token=self._cipher.decrypt(row["access_token_encrypted"]),
            cf_bm=self._cipher.decrypt(row["cf_bm_encrypted"]),
            updated_at=updated_at,
        )

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=3)
        return self._pool

    async def _ensure_schema(self, pool: asyncpg.Pool) -> None:
        await pool.execute(
            """
            create table if not exists platform_sessions (
                id integer primary key,
                access_token_encrypted text not null default '',
                cf_bm_encrypted text not null default '',
                updated_at timestamptz not null
            )
            """
        )


def build_platform_session_repository(
    *,
    database_url: str,
    encryption_key: str,
) -> PlatformSessionRepository:
    if database_url:
        if not encryption_key:
            raise RuntimeError(
                "SESSION_ENCRYPTION_KEY is required when DATABASE_URL is set. "
                "Generate key: python -m app.workers.generate_session_key"
            )
        return PostgresEncryptedPlatformSessionRepository(
            database_url=database_url,
            cipher=SecretCipher(encryption_key),
        )
    return InMemoryPlatformSessionRepository()
