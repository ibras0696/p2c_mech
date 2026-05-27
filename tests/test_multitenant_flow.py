from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from app.bot.session_state import PlatformSession
from app.bot.state import ActiveOrder
from app.core.config import Settings
from app.repositories.active_orders import InMemoryActiveOrderRepository
from app.repositories.admin_registry import InMemoryAdminRegistryRepository
from app.repositories.agent_preferences import InMemoryAgentPreferencesRepository
from app.repositories.platform_session import InMemoryPlatformSessionRepository
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager


class FakeBot:
    async def send_photo(self, **kwargs) -> None:  # noqa: ANN003
        del kwargs

    async def send_message(self, **kwargs) -> None:  # noqa: ANN003
        del kwargs


def make_settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            platform_base_url="https://app.send.tg",
            platform_ws_url="wss://app.send.tg/internal/v1/p2c-socket/?EIO=4&transport=websocket",
            platform_claim_from_snapshot=False,
            platform_force_ipv4=True,
            platform_take_health_enabled=False,
            platform_take_health_interval_seconds=5,
            runtime_idle_ttl_seconds=900,
            runtime_cleanup_interval_seconds=60,
            redis_url="",
            redis_host="localhost",
            redis_port=6379,
            redis_db=0,
            redis_password="",
        ),
    )


@pytest.mark.asyncio
async def test_owner_adds_admin_and_users_work_isolated() -> None:
    access = AdminAccessService(repository=InMemoryAdminRegistryRepository())
    await access.bootstrap_owners({10})
    await access.add_admin(actor_user_id=10, target_user_id=20)

    assert await access.is_owner(10) is True
    assert await access.is_allowed(20) is True

    manager = AgentRuntimeManager(
        settings=make_settings(),
        bot=cast(object, FakeBot()),
        preferences_repository=InMemoryAgentPreferencesRepository(),
        platform_session_repository=InMemoryPlatformSessionRepository(),
        active_order_repository=InMemoryActiveOrderRepository(),
    )
    try:
        owner_runtime = await manager.get_or_create(10)
        admin_runtime = await manager.get_or_create(20)

        await owner_runtime.session_repository.save(
            PlatformSession(access_token="owner-token", cf_bm="owner-cf", updated_at=datetime.now(UTC))
        )
        await admin_runtime.session_repository.save(
            PlatformSession(access_token="admin-token", cf_bm="admin-cf", updated_at=datetime.now(UTC))
        )

        owner_runtime.state.upsert_active_order(
            ActiveOrder(id="p-owner", amount="100", currency="RUB", direction="P2C")
        )
        admin_runtime.state.upsert_active_order(
            ActiveOrder(id="p-admin", amount="200", currency="RUB", direction="P2C")
        )

        owner_snapshot = await manager.snapshot(10)
        admin_snapshot = await manager.snapshot(20)
        assert [order.id for order in owner_snapshot.active_orders] == ["p-owner"]
        assert [order.id for order in admin_snapshot.active_orders] == ["p-admin"]
    finally:
        await manager.stop()
