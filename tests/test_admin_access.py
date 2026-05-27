import pytest
from app.repositories.admin_registry import InMemoryAdminRegistryRepository
from app.services.admin_access import AdminAccessService


@pytest.mark.asyncio
async def test_bootstrap_owners_is_idempotent() -> None:
    repository = InMemoryAdminRegistryRepository()
    service = AdminAccessService(repository=repository)

    await service.bootstrap_owners({1})
    await service.bootstrap_owners({1})

    users = await service.list_active()
    assert len(users) == 1
    assert users[0].user_id == 1
    assert await service.is_owner(1) is True


@pytest.mark.asyncio
async def test_admin_access_deactivate_revokes_permissions() -> None:
    repository = InMemoryAdminRegistryRepository()
    service = AdminAccessService(repository=repository)
    await service.bootstrap_owners({1})
    await service.add_admin(actor_user_id=1, target_user_id=2)

    assert await service.is_allowed(2) is True
    await service.remove_admin(target_user_id=2)
    assert await service.is_allowed(2) is False
    assert await service.is_allowed(9999) is False
    assert await service.is_owner(2) is False


@pytest.mark.asyncio
async def test_admin_add_reactivates_existing_inactive_user() -> None:
    repository = InMemoryAdminRegistryRepository()
    service = AdminAccessService(repository=repository)
    await service.bootstrap_owners({1})
    await service.add_admin(actor_user_id=1, target_user_id=2)
    await service.remove_admin(target_user_id=2)

    restored = await service.add_admin(actor_user_id=1, target_user_id=2)
    assert restored.user_id == 2
    assert await service.is_allowed(2) is True
