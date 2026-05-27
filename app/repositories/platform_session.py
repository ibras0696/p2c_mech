from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

import asyncpg  # type: ignore[import-untyped]
from redis import asyncio as redis_asyncio  # type: ignore[import-untyped]

from app.bot.session_state import PlatformSession
from app.core.crypto import SecretCipher
from app.core.logging import get_logger

logger = get_logger(__name__)


class PlatformSessionCache(ABC):
    @abstractmethod
    async def get(self) -> PlatformSession | None:
        raise NotImplementedError

    @abstractmethod
    async def set(self, session: PlatformSession) -> None:
        raise NotImplementedError


class PlatformSessionRepository(ABC):
    @abstractmethod
    async def save_for_user(self, user_id: int, session: PlatformSession) -> PlatformSession:
        raise NotImplementedError

    @abstractmethod
    async def current_for_user(self, user_id: int) -> PlatformSession | None:
        raise NotImplementedError

    async def save(self, session: PlatformSession) -> PlatformSession:
        return await self.save_for_user(0, session)

    async def current(self) -> PlatformSession | None:
        return await self.current_for_user(0)


class InMemoryPlatformSessionRepository(PlatformSessionRepository):
    def __init__(self) -> None:
        self._sessions: dict[int, PlatformSession] = {}

    async def save_for_user(self, user_id: int, session: PlatformSession) -> PlatformSession:
        self._sessions[user_id] = session
        return session

    async def current_for_user(self, user_id: int) -> PlatformSession | None:
        return self._sessions.get(user_id)


class RedisEncryptedPlatformSessionCache(PlatformSessionCache):
    def __init__(
        self,
        *,
        cipher: SecretCipher,
        ttl_seconds: int,
        redis_url: str = "",
        redis_host: str = "redis",
        redis_port: int = 6379,
        redis_db: int = 0,
        redis_password: str = "",
        key_prefix: str = "p2c:platform_session",
    ) -> None:
        self._cipher = cipher
        self._ttl_seconds = max(ttl_seconds, 60)
        self._key_prefix = key_prefix
        if redis_url:
            self._client = redis_asyncio.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        else:
            self._client = redis_asyncio.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                password=redis_password or None,
                encoding="utf-8",
                decode_responses=True,
            )

    async def get_for_user(self, user_id: int) -> PlatformSession | None:
        payload = await self._client.hgetall(self._key(user_id))
        if not payload:
            return None
        access_token_encrypted = payload.get("access_token_encrypted", "")
        cf_bm_encrypted = payload.get("cf_bm_encrypted", "")
        updated_at_raw = payload.get("updated_at", "")
        if not updated_at_raw:
            return None
        updated_at = datetime.fromisoformat(updated_at_raw)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return PlatformSession(
            access_token=self._cipher.decrypt(access_token_encrypted),
            cf_bm=self._cipher.decrypt(cf_bm_encrypted),
            updated_at=updated_at,
        )

    async def set_for_user(self, user_id: int, session: PlatformSession) -> None:
        pipe = self._client.pipeline(transaction=True)
        pipe.hset(
            self._key(user_id),
            mapping={
                "access_token_encrypted": self._cipher.encrypt(session.access_token),
                "cf_bm_encrypted": self._cipher.encrypt(session.cf_bm),
                "updated_at": session.updated_at.isoformat(),
            },
        )
        pipe.expire(self._key(user_id), self._ttl_seconds)
        await pipe.execute()

    async def get(self) -> PlatformSession | None:
        return await self.get_for_user(0)

    async def set(self, session: PlatformSession) -> None:
        await self.set_for_user(0, session)

    def _key(self, user_id: int) -> str:
        return f"{self._key_prefix}:{user_id}"


class CachedPlatformSessionRepository(PlatformSessionRepository):
    def __init__(
        self,
        *,
        primary: PlatformSessionRepository,
        cache: PlatformSessionCache,
    ) -> None:
        self._primary = primary
        self._cache = cache

    async def save_for_user(self, user_id: int, session: PlatformSession) -> PlatformSession:
        stored = await self._primary.save_for_user(user_id, session)
        try:
            if isinstance(self._cache, RedisEncryptedPlatformSessionCache):
                await self._cache.set_for_user(user_id, stored)
            else:
                await self._cache.set(stored)
        except Exception as exc:
            logger.warning("platform_session_cache_set_failed error=%s", type(exc).__name__)
        return stored

    async def current_for_user(self, user_id: int) -> PlatformSession | None:
        try:
            if isinstance(self._cache, RedisEncryptedPlatformSessionCache):
                cached = await self._cache.get_for_user(user_id)
            else:
                cached = await self._cache.get()
        except Exception as exc:
            logger.warning("platform_session_cache_get_failed error=%s", type(exc).__name__)
        else:
            if cached is not None:
                logger.debug("platform_session_cache_hit")
                return cached
            logger.info("platform_session_cache_miss")
        stored = await self._primary.current_for_user(user_id)
        if stored is None:
            return None
        try:
            if isinstance(self._cache, RedisEncryptedPlatformSessionCache):
                await self._cache.set_for_user(user_id, stored)
            else:
                await self._cache.set(stored)
        except Exception as exc:
            logger.warning("platform_session_cache_set_failed error=%s", type(exc).__name__)
        return stored


class PostgresEncryptedPlatformSessionRepository(PlatformSessionRepository):
    def __init__(self, database_url: str, cipher: SecretCipher) -> None:
        self._database_url = database_url
        self._cipher = cipher
        self._pool: asyncpg.Pool | None = None
        self._schema_ready = False

    async def save_for_user(self, user_id: int, session: PlatformSession) -> PlatformSession:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        await pool.execute(
            """
            insert into platform_sessions (
                user_id,
                access_token_encrypted,
                cf_bm_encrypted,
                updated_at
            )
            values ($1, $2, $3, $4)
            on conflict (user_id) do update set
                access_token_encrypted = excluded.access_token_encrypted,
                cf_bm_encrypted = excluded.cf_bm_encrypted,
                updated_at = excluded.updated_at
            """,
            user_id,
            self._cipher.encrypt(session.access_token),
            self._cipher.encrypt(session.cf_bm),
            session.updated_at,
        )
        return session

    async def current_for_user(self, user_id: int) -> PlatformSession | None:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        row = await pool.fetchrow(
            """
            select access_token_encrypted, cf_bm_encrypted, updated_at
            from platform_sessions
            where user_id = $1
            """
            ,
            user_id,
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
        if self._schema_ready:
            return
        await pool.execute(
            """
            create table if not exists platform_sessions (
                user_id bigint primary key,
                access_token_encrypted text not null default '',
                cf_bm_encrypted text not null default '',
                updated_at timestamptz not null
            )
            """
        )
        await pool.execute(
            """
            alter table platform_sessions
            add column if not exists user_id bigint
            """
        )
        await pool.execute(
            """
            update platform_sessions
            set user_id = 0
            where user_id is null
            """
        )
        await pool.execute(
            """
            create unique index if not exists idx_platform_sessions_user_id
            on platform_sessions (user_id)
            """
        )
        self._schema_ready = True


def build_platform_session_repository(
    *,
    database_url: str,
    encryption_key: str,
    redis_host: str = "redis",
    redis_port: int = 6379,
    redis_db: int = 0,
    redis_password: str = "",
    redis_url: str = "",
    session_cache_ttl_seconds: int = 900,
) -> PlatformSessionRepository:
    if database_url:
        if not encryption_key:
            raise RuntimeError(
                "SESSION_ENCRYPTION_KEY is required when DATABASE_URL is set. "
                "Generate key: python -m app.workers.generate_session_key"
            )
        cipher = SecretCipher(encryption_key)
        primary = PostgresEncryptedPlatformSessionRepository(
            database_url=database_url,
            cipher=cipher,
        )
        cache = RedisEncryptedPlatformSessionCache(
            cipher=cipher,
            ttl_seconds=session_cache_ttl_seconds,
            redis_url=redis_url,
            redis_host=redis_host,
            redis_port=redis_port,
            redis_db=redis_db,
            redis_password=redis_password,
        )
        return CachedPlatformSessionRepository(primary=primary, cache=cache)
    return InMemoryPlatformSessionRepository()
