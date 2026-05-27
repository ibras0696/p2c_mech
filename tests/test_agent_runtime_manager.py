from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest
from app.bot.session_state import PlatformSession
from app.bot.state import ActiveOrder
from app.core.config import Settings
from app.repositories.active_orders import InMemoryActiveOrderRepository
from app.repositories.agent_preferences import InMemoryAgentPreferencesRepository
from app.repositories.platform_session import InMemoryPlatformSessionRepository
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
            runtime_idle_ttl_seconds=1,
            runtime_cleanup_interval_seconds=1,
            redis_url="",
            redis_host="localhost",
            redis_port=6379,
            redis_db=0,
            redis_password="",
        ),
    )


@pytest.mark.asyncio
async def test_runtime_manager_isolates_sessions_and_preferences() -> None:
    manager = AgentRuntimeManager(
        settings=make_settings(),
        bot=cast(object, FakeBot()),
        preferences_repository=InMemoryAgentPreferencesRepository(),
        platform_session_repository=InMemoryPlatformSessionRepository(),
        active_order_repository=InMemoryActiveOrderRepository(),
    )
    manager._redis = None  # type: ignore[attr-defined]
    try:
        runtime_a = await manager.get_or_create(1001)
        runtime_b = await manager.get_or_create(1002)
        await runtime_a.session_repository.save(
            PlatformSession(access_token="a", cf_bm="cf-a", updated_at=datetime.now(UTC))
        )
        await runtime_b.session_repository.save(
            PlatformSession(access_token="b", cf_bm="cf-b", updated_at=datetime.now(UTC))
        )
        session_a = await runtime_a.session_repository.current()
        session_b = await runtime_b.session_repository.current()
        assert session_a is not None
        assert session_b is not None
        assert session_a.access_token == "a"
        assert session_b.access_token == "b"

        await manager.set_limit(1001, 3)
        await manager.set_limit(1002, 1)
        await manager.set_amount_filter(1001, Decimal("10"), Decimal("50"))
        await manager.set_amount_filter(1002, Decimal("100"), Decimal("500"))

        snap_a = await manager.snapshot(1001)
        snap_b = await manager.snapshot(1002)
        assert snap_a.active_limit == 3
        assert snap_b.active_limit == 1
        assert snap_a.min_amount == Decimal("10")
        assert snap_b.min_amount == Decimal("100")
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_runtime_manager_callback_dedupe_is_per_user() -> None:
    manager = AgentRuntimeManager(
        settings=make_settings(),
        bot=cast(object, FakeBot()),
        preferences_repository=InMemoryAgentPreferencesRepository(),
        platform_session_repository=InMemoryPlatformSessionRepository(),
        active_order_repository=InMemoryActiveOrderRepository(),
    )
    manager._redis = None  # type: ignore[attr-defined]
    try:
        first = await manager.is_callback_duplicate(user_id=1, message_id=11, callback_data="agent:run")
        second = await manager.is_callback_duplicate(user_id=1, message_id=11, callback_data="agent:run")
        third = await manager.is_callback_duplicate(user_id=2, message_id=11, callback_data="agent:run")

        assert first is False
        assert second is True
        assert third is False
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_runtime_cleanup_removes_only_idle_paused_empty_runtime() -> None:
    manager = AgentRuntimeManager(
        settings=make_settings(),
        bot=cast(object, FakeBot()),
        preferences_repository=InMemoryAgentPreferencesRepository(),
        platform_session_repository=InMemoryPlatformSessionRepository(),
        active_order_repository=InMemoryActiveOrderRepository(),
    )
    try:
        idle_paused = await manager.get_or_create(1)
        waiting_runtime = await manager.get_or_create(2)
        paused_with_order = await manager.get_or_create(3)

        await manager.run(2)
        paused_with_order.state.upsert_active_order(
            ActiveOrder(id="x", amount="1", currency="RUB", direction="P2C")
        )
        idle_paused.state.pause()
        paused_with_order.state.pause()

        old_mono = idle_paused.last_used_monotonic - 100
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        idle_paused.last_used_monotonic = old_mono
        waiting_runtime.last_used_monotonic = old_mono
        paused_with_order.last_used_monotonic = old_mono
        idle_paused.last_used_at = old_time
        waiting_runtime.last_used_at = old_time
        paused_with_order.last_used_at = old_time

        removed = await manager._cleanup_once(idle_ttl_seconds=1)
        rows = await manager.runtime_statuses()
        user_ids = {int(row["user_id"]) for row in rows}

        assert removed == 1
        assert user_ids == {2, 3}
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_runtime_action_locks_are_isolated_per_user() -> None:
    manager = AgentRuntimeManager(
        settings=make_settings(),
        bot=cast(object, FakeBot()),
        preferences_repository=InMemoryAgentPreferencesRepository(),
        platform_session_repository=InMemoryPlatformSessionRepository(),
        active_order_repository=InMemoryActiveOrderRepository(),
    )
    try:
        runtime_a = await manager.get_or_create(11)
        runtime_b = await manager.get_or_create(22)

        assert runtime_a.action_lock is not runtime_b.action_lock
        async with runtime_a.action_lock:
            assert runtime_a.action_lock.locked() is True
            assert runtime_b.action_lock.locked() is False
    finally:
        await manager.stop()
