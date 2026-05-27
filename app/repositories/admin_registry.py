from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import asyncpg  # type: ignore[import-untyped]


class AdminRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"


@dataclass(frozen=True)
class AdminUser:
    user_id: int
    role: AdminRole
    is_active: bool
    created_by: int
    created_at: datetime


class AdminRegistryRepository(ABC):
    @abstractmethod
    async def upsert_user(
        self,
        *,
        user_id: int,
        role: AdminRole,
        created_by: int,
        is_active: bool = True,
    ) -> AdminUser:
        raise NotImplementedError

    @abstractmethod
    async def deactivate_user(self, *, user_id: int) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_user(self, *, user_id: int) -> AdminUser | None:
        raise NotImplementedError

    @abstractmethod
    async def list_active_users(self) -> list[AdminUser]:
        raise NotImplementedError

    async def bootstrap_owners(self, owner_ids: set[int]) -> None:
        for owner_id in owner_ids:
            await self.upsert_user(
                user_id=owner_id,
                role=AdminRole.OWNER,
                created_by=owner_id,
                is_active=True,
            )


class InMemoryAdminRegistryRepository(AdminRegistryRepository):
    def __init__(self) -> None:
        self._users: dict[int, AdminUser] = {}

    async def upsert_user(
        self,
        *,
        user_id: int,
        role: AdminRole,
        created_by: int,
        is_active: bool = True,
    ) -> AdminUser:
        payload = AdminUser(
            user_id=user_id,
            role=role,
            is_active=is_active,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
        self._users[user_id] = payload
        return payload

    async def deactivate_user(self, *, user_id: int) -> None:
        current = self._users.get(user_id)
        if current is None:
            return
        self._users[user_id] = AdminUser(
            user_id=current.user_id,
            role=current.role,
            is_active=False,
            created_by=current.created_by,
            created_at=current.created_at,
        )

    async def get_user(self, *, user_id: int) -> AdminUser | None:
        return self._users.get(user_id)

    async def list_active_users(self) -> list[AdminUser]:
        return sorted(
            [item for item in self._users.values() if item.is_active],
            key=lambda item: (item.role.value, item.user_id),
        )


class PostgresAdminRegistryRepository(AdminRegistryRepository):
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def upsert_user(
        self,
        *,
        user_id: int,
        role: AdminRole,
        created_by: int,
        is_active: bool = True,
    ) -> AdminUser:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        row = await pool.fetchrow(
            """
            insert into admin_registry (
                user_id,
                role,
                is_active,
                created_by,
                created_at
            )
            values ($1, $2, $3, $4, now())
            on conflict (user_id) do update set
                role = excluded.role,
                is_active = excluded.is_active
            returning user_id, role, is_active, created_by, created_at
            """,
            user_id,
            role.value,
            is_active,
            created_by,
        )
        return _map_admin_user(row)

    async def deactivate_user(self, *, user_id: int) -> None:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        await pool.execute(
            """
            update admin_registry
            set is_active = false
            where user_id = $1
            """,
            user_id,
        )

    async def get_user(self, *, user_id: int) -> AdminUser | None:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        row = await pool.fetchrow(
            """
            select user_id, role, is_active, created_by, created_at
            from admin_registry
            where user_id = $1
            """,
            user_id,
        )
        if row is None:
            return None
        return _map_admin_user(row)

    async def list_active_users(self) -> list[AdminUser]:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        rows = await pool.fetch(
            """
            select user_id, role, is_active, created_by, created_at
            from admin_registry
            where is_active = true
            order by role asc, user_id asc
            """
        )
        return [_map_admin_user(row) for row in rows]

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=3)
        return self._pool

    async def _ensure_schema(self, pool: asyncpg.Pool) -> None:
        await pool.execute(
            """
            create table if not exists admin_registry (
                user_id bigint primary key,
                role text not null,
                is_active boolean not null default true,
                created_by bigint not null,
                created_at timestamptz not null default now()
            )
            """
        )
        await pool.execute(
            """
            create index if not exists idx_admin_registry_is_active
            on admin_registry (is_active)
            """
        )


def _map_admin_user(row: asyncpg.Record) -> AdminUser:
    created_at = row["created_at"]
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    role_raw = str(row["role"]).lower()
    role = AdminRole.OWNER if role_raw == AdminRole.OWNER.value else AdminRole.ADMIN
    return AdminUser(
        user_id=int(row["user_id"]),
        role=role,
        is_active=bool(row["is_active"]),
        created_by=int(row["created_by"]),
        created_at=created_at,
    )


def build_admin_registry_repository(*, database_url: str) -> AdminRegistryRepository:
    if database_url:
        return PostgresAdminRegistryRepository(database_url)
    return InMemoryAdminRegistryRepository()

