from __future__ import annotations

from app.repositories.admin_registry import AdminRegistryRepository, AdminRole, AdminUser


class AdminAccessService:
    def __init__(self, *, repository: AdminRegistryRepository) -> None:
        self._repository = repository

    async def bootstrap_owners(self, owner_ids: set[int]) -> None:
        await self._repository.bootstrap_owners(owner_ids)

    async def is_allowed(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        user = await self._repository.get_user(user_id=user_id)
        return bool(user is not None and user.is_active)

    async def is_owner(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        user = await self._repository.get_user(user_id=user_id)
        return bool(user is not None and user.is_active and user.role == AdminRole.OWNER)

    async def add_admin(self, *, actor_user_id: int, target_user_id: int) -> AdminUser:
        return await self._repository.upsert_user(
            user_id=target_user_id,
            role=AdminRole.ADMIN,
            created_by=actor_user_id,
            is_active=True,
        )

    async def remove_admin(self, *, target_user_id: int) -> None:
        await self._repository.deactivate_user(user_id=target_user_id)

    async def list_active(self) -> list[AdminUser]:
        return await self._repository.list_active_users()

    async def get_user(self, *, user_id: int) -> AdminUser | None:
        return await self._repository.get_user(user_id=user_id)
