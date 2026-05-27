from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from app.bot.session_state import PlatformSession
from app.bot.state import ActiveOrder, AgentMode, InMemoryAgentState
from app.core.config import Settings
from app.integrations.platform_api import P2CPaymentDetails, P2CPaymentsError
from app.repositories.active_orders import InMemoryActiveOrderRepository
from app.repositories.platform_session import InMemoryPlatformSessionRepository
from app.services.p2c_events import P2COrderEvent
from app.services.p2c_live_agent import P2CLiveAgent


class FakePaymentsClient:
    def __init__(self) -> None:
        self.take_calls: list[str] = []
        self.take_side_effects: list[tuple[float, int | Exception]] = []
        self.complete_calls: list[tuple[int, str]] = []
        self.cancel_calls: list[tuple[int, str]] = []
        self.complete_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.list_accounts_calls = 0
        self.payment_method_id = "method-1"
        self.payment_status = "processing"
        self.complete_delay_seconds = 0.0
        self.get_payment_error: Exception | None = None
        self.prewarm_error: Exception | None = None

    async def take(
        self,
        *,
        socket_order_id: str,
        session: PlatformSession,
        client_slot: int | None = None,
    ) -> int:
        del session
        del client_slot
        self.take_calls.append(socket_order_id)
        index = len(self.take_calls) - 1
        if index < len(self.take_side_effects):
            delay, result = self.take_side_effects[index]
            if delay > 0:
                await asyncio.sleep(delay)
            if isinstance(result, Exception):
                raise result
            return result
        return 3566992

    async def get_payment(self, *, payment_id: int, session: PlatformSession) -> P2CPaymentDetails:
        del session
        if self.get_payment_error is not None:
            raise self.get_payment_error
        return P2CPaymentDetails(
            id=payment_id,
            status=self.payment_status,
            in_amount="97",
            in_asset="RUB",
            out_amount="1",
            out_asset="USDT",
            brand_name="Shop",
            provider="platiqr",
            url="https://multiqr.test",
            payload="trx-123",
            method_id=self.payment_method_id,
            raw={"id": payment_id, "account": {"id": self.payment_method_id}},
        )

    async def complete(self, *, payment_id: int, method_id: str, session: PlatformSession) -> None:
        del session
        if self.complete_error is not None:
            raise self.complete_error
        if self.complete_delay_seconds > 0:
            await asyncio.sleep(self.complete_delay_seconds)
        self.complete_calls.append((payment_id, method_id))

    async def cancel(self, *, payment_id: int, session: PlatformSession, method_id: str = "") -> None:
        del session
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancel_calls.append((payment_id, method_id))

    async def list_accounts(self, *, session: PlatformSession) -> list[dict[str, str]]:
        del session
        self.list_accounts_calls += 1
        return [{"id": "69eb8d7e6bdfddede1de9a79", "status": "active"}]

    async def prewarm_take_clients(self, *, session: PlatformSession, channels: int = 3) -> None:
        del session
        del channels
        if self.prewarm_error is not None:
            raise self.prewarm_error


class CountingSessionRepository(InMemoryPlatformSessionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.current_calls = 0

    async def current(self) -> PlatformSession | None:
        self.current_calls += 1
        return await super().current()


@pytest.mark.asyncio
async def test_live_agent_claims_and_notifies_owned_order() -> None:
    state = InMemoryAgentState()
    state.run()
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(
            access_token="token",
            cf_bm="cf",
            updated_at=datetime.now(UTC),
        )
    )
    notifications: list[ActiveOrder] = []
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, notifications),
    )
    fake = FakePaymentsClient()
    agent._payments_client = fake  # type: ignore[assignment]

    await agent._process_event(
        P2COrderEvent(
            socket_order_id="6a1206db7440f5cd5e5c69c7",
            in_amount=state.snapshot().min_amount + 1,
            in_asset="RUB",
            out_asset="USDT",
            url="https://multiqr.test",
            payload="trx-123",
            brand_name="Shop",
            provider="platiqr",
        ),
        0.0,
        agent._pause_generation,  # type: ignore[attr-defined]
    )

    snapshot = state.snapshot()
    assert fake.take_calls == ["6a1206db7440f5cd5e5c69c7"]
    assert snapshot.active_count == 1
    assert snapshot.active_orders[0].id == "3566992"
    assert snapshot.active_orders[0].method_id == "method-1"
    assert snapshot.active_orders[0].claim_total_ms is not None
    assert snapshot.active_orders[0].claim_total_ms >= 0
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_live_agent_skips_claim_when_paused() -> None:
    state = InMemoryAgentState()
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(
            access_token="token",
            cf_bm="cf",
            updated_at=datetime.now(UTC),
        )
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    agent._payments_client = fake  # type: ignore[assignment]

    await agent._process_event(
        P2COrderEvent(
            socket_order_id="offer-1",
            in_amount=state.snapshot().min_amount + 1,
            in_asset="RUB",
            out_asset="USDT",
            url="https://qr",
            payload="payload",
            brand_name="Shop",
            provider="nspk",
        ),
        0.0,
        agent._pause_generation,  # type: ignore[attr-defined]
    )

    assert fake.take_calls == []


@pytest.mark.asyncio
async def test_complete_order_calls_api_and_frees_slot() -> None:
    state = InMemoryAgentState()
    state.upsert_active_order(
        ActiveOrder(
            id="3566992",
            amount="97",
            currency="RUB",
            direction="P2C",
            method_id="method-1",
        )
    )
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(
            access_token="token",
            cf_bm="cf",
            updated_at=datetime.now(UTC),
        )
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    agent._payments_client = fake  # type: ignore[assignment]

    await agent.complete_order("3566992")

    assert fake.complete_calls == [(3566992, "method-1")]
    assert state.snapshot().active_count == 0


@pytest.mark.asyncio
async def test_complete_order_allows_after_deadline_and_completes() -> None:
    state = InMemoryAgentState()
    state.upsert_active_order(
        ActiveOrder(
            id="3566992",
            amount="97",
            currency="RUB",
            direction="P2C",
            method_id="method-1",
            deadline_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    agent._payments_client = fake  # type: ignore[assignment]

    await agent.complete_order("3566992")
    assert fake.complete_calls == [(3566992, "method-1")]
    assert state.snapshot().active_count == 0


@pytest.mark.asyncio
async def test_complete_order_clears_local_when_remote_already_final() -> None:
    state = InMemoryAgentState()
    state.upsert_active_order(
        ActiveOrder(
            id="3566992",
            amount="97",
            currency="RUB",
            direction="P2C",
            method_id="method-1",
        )
    )
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    fake.payment_status = "completed"
    agent._payments_client = fake  # type: ignore[assignment]

    await agent.complete_order("3566992")

    assert fake.complete_calls == []
    assert state.snapshot().active_count == 0


@pytest.mark.asyncio
async def test_complete_order_uses_method_from_details_when_missing_locally() -> None:
    state = InMemoryAgentState()
    state.upsert_active_order(
        ActiveOrder(
            id="3566992",
            amount="97",
            currency="RUB",
            direction="P2C",
            method_id="",
        )
    )
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    fake.payment_method_id = "method-from-details"
    agent._payments_client = fake  # type: ignore[assignment]

    await agent.complete_order("3566992")

    assert fake.complete_calls == [(3566992, "method-from-details")]
    assert state.snapshot().active_count == 0


@pytest.mark.asyncio
async def test_complete_order_falls_back_to_raw_account_id_when_method_missing() -> None:
    state = InMemoryAgentState()
    state.upsert_active_order(
        ActiveOrder(
            id="3566992",
            amount="97",
            currency="RUB",
            direction="P2C",
            method_id="",
        )
    )
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    fake.payment_method_id = ""

    async def get_payment_raw_account(*, payment_id: int, session: PlatformSession) -> P2CPaymentDetails:
        del session
        return P2CPaymentDetails(
            id=payment_id,
            status="processing",
            in_amount="97",
            in_asset="RUB",
            out_amount="1",
            out_asset="USDT",
            brand_name="Shop",
            provider="platiqr",
            url="https://multiqr.test",
            payload="trx-123",
            method_id="",
            raw={"id": payment_id, "account": {"id": "69eb8d7e6bdfddede1de9a79"}},
        )

    fake.get_payment = get_payment_raw_account  # type: ignore[method-assign]
    agent._payments_client = fake  # type: ignore[assignment]

    await agent.complete_order("3566992")

    assert fake.complete_calls == [(3566992, "69eb8d7e6bdfddede1de9a79")]
    assert state.snapshot().active_count == 0


@pytest.mark.asyncio
async def test_complete_order_falls_back_to_accounts_when_everything_missing() -> None:
    state = InMemoryAgentState()
    state.upsert_active_order(
        ActiveOrder(
            id="3566992",
            amount="97",
            currency="RUB",
            direction="P2C",
            method_id="",
        )
    )
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    fake.payment_method_id = ""

    async def get_payment_empty(*, payment_id: int, session: PlatformSession) -> P2CPaymentDetails:
        del session
        return P2CPaymentDetails(
            id=payment_id,
            status="processing",
            in_amount="97",
            in_asset="RUB",
            out_amount="1",
            out_asset="USDT",
            brand_name="Shop",
            provider="platiqr",
            url="https://multiqr.test",
            payload="trx-123",
            method_id="",
            raw={"id": payment_id, "account": {}},
        )

    fake.get_payment = get_payment_empty  # type: ignore[method-assign]
    agent._payments_client = fake  # type: ignore[assignment]

    await agent.complete_order("3566992")

    assert fake.complete_calls == [(3566992, "69eb8d7e6bdfddede1de9a79")]
    assert fake.list_accounts_calls == 1
    assert state.snapshot().active_count == 0


@pytest.mark.asyncio
async def test_complete_order_prevents_parallel_double_click() -> None:
    state = InMemoryAgentState()
    state.upsert_active_order(
        ActiveOrder(
            id="3566992",
            amount="97",
            currency="RUB",
            direction="P2C",
            method_id="method-1",
        )
    )
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    fake.complete_delay_seconds = 0.2
    agent._payments_client = fake  # type: ignore[assignment]

    first = asyncio.create_task(agent.complete_order("3566992"))
    await asyncio.sleep(0.05)
    with pytest.raises(P2CPaymentsError, match="Completion already in progress"):
        await agent.complete_order("3566992")
    await first
    assert fake.complete_calls == [(3566992, "method-1")]


@pytest.mark.asyncio
async def test_live_agent_pauses_when_notify_fails() -> None:
    state = InMemoryAgentState()
    state.run()
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(
            access_token="token",
            cf_bm="cf",
            updated_at=datetime.now(UTC),
        )
    )

    async def failing_notify(_order: ActiveOrder) -> None:
        raise RuntimeError("notify failed")

    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=failing_notify,
    )
    fake = FakePaymentsClient()
    agent._payments_client = fake  # type: ignore[assignment]

    await agent._process_event(
        P2COrderEvent(
            socket_order_id="offer-notify-fail",
            in_amount=state.snapshot().min_amount + 1,
            in_asset="RUB",
            out_asset="USDT",
            url="https://qr",
            payload="payload",
            brand_name="Shop",
            provider="nspk",
        ),
        0.0,
        agent._pause_generation,  # type: ignore[attr-defined]
    )

    assert state.snapshot().mode == AgentMode.PAUSED


@pytest.mark.asyncio
async def test_live_agent_keeps_taken_order_and_pauses_when_confirm_fails() -> None:
    state = InMemoryAgentState()
    state.run()
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(
            access_token="token",
            cf_bm="cf",
            updated_at=datetime.now(UTC),
        )
    )
    notifications: list[ActiveOrder] = []
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, notifications),
    )
    fake = FakePaymentsClient()
    fake.get_payment_error = P2CPaymentsError("network timeout")
    agent._payments_client = fake  # type: ignore[assignment]

    await agent._process_event(
        P2COrderEvent(
            socket_order_id="offer-confirm-fail",
            in_amount=state.snapshot().min_amount + 1,
            in_asset="RUB",
            out_asset="USDT",
            url="https://qr",
            payload="payload",
            brand_name="Shop",
            provider="nspk",
        ),
        0.0,
        agent._pause_generation,  # type: ignore[attr-defined]
    )

    snapshot = state.snapshot()
    assert snapshot.active_count == 1
    assert snapshot.active_orders[0].id == "3566992"
    assert snapshot.active_orders[0].claim_total_ms is None
    assert snapshot.mode == AgentMode.PAUSED
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_live_agent_skips_stale_event_after_pause_generation_changed() -> None:
    state = InMemoryAgentState()
    state.run()
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(
            access_token="token",
            cf_bm="cf",
            updated_at=datetime.now(UTC),
        )
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    agent._payments_client = fake  # type: ignore[assignment]
    stale_generation = agent._pause_generation  # type: ignore[attr-defined]
    agent.on_pause()

    await agent._process_event(  # type: ignore[arg-type]
        P2COrderEvent(
            socket_order_id="offer-stale-after-pause",
            in_amount=state.snapshot().min_amount + 1,
            in_asset="RUB",
            out_asset="USDT",
            url="https://qr",
            payload="payload",
            brand_name="Shop",
            provider="nspk",
        ),
        0.0,
        stale_generation,
    )

    assert fake.take_calls == []


@pytest.mark.asyncio
async def test_cancel_order_calls_api_and_frees_slot() -> None:
    state = InMemoryAgentState()
    state.upsert_active_order(
        ActiveOrder(
            id="3566992",
            amount="97",
            currency="RUB",
            direction="P2C",
            method_id="method-1",
        )
    )
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    agent._payments_client = fake  # type: ignore[assignment]

    await agent.cancel_order("3566992")
    assert state.snapshot().active_count == 0
    assert fake.cancel_calls == [(3566992, "method-1")]


@pytest.mark.asyncio
async def test_complete_order_clears_local_when_complete_returns_404() -> None:
    state = InMemoryAgentState()
    state.upsert_active_order(
        ActiveOrder(
            id="3566992",
            amount="97",
            currency="RUB",
            direction="P2C",
            method_id="method-1",
        )
    )
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    fake.complete_error = P2CPaymentsError("POST /internal/v1/p2c/payments/3566992/complete failed with status 404")
    agent._payments_client = fake  # type: ignore[assignment]

    await agent.complete_order("3566992")

    assert state.snapshot().active_count == 0


@pytest.mark.asyncio
async def test_cancel_order_clears_local_when_cancel_returns_404() -> None:
    state = InMemoryAgentState()
    state.upsert_active_order(
        ActiveOrder(
            id="3566992",
            amount="97",
            currency="RUB",
            direction="P2C",
            method_id="method-1",
        )
    )
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    fake.cancel_error = P2CPaymentsError(
        "Cancel failed for payment 3566992: POST /internal/v1/p2c/payments/3566992/decline failed with status 404"
    )
    agent._payments_client = fake  # type: ignore[assignment]

    await agent.cancel_order("3566992")

    assert state.snapshot().active_count == 0


@pytest.mark.asyncio
async def test_live_agent_run_forever_survives_session_repository_errors() -> None:
    class BrokenSessionRepository:
        async def current(self):
            raise RuntimeError("db unavailable")

    state = InMemoryAgentState()
    state.run()
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=BrokenSessionRepository(),  # type: ignore[arg-type]
        notify_order_ready=lambda order: capture_order(order, []),
    )

    task = asyncio.create_task(agent.run_forever())
    await asyncio.sleep(0.05)
    assert not task.done()

    agent.stop()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_live_agent_loads_persisted_orders() -> None:
    state = InMemoryAgentState()
    repository = InMemoryPlatformSessionRepository()
    active_repo = InMemoryActiveOrderRepository()
    await active_repo.upsert(
        ActiveOrder(
            id="3567100",
            amount="120",
            currency="RUB",
            direction="P2C",
            provider="nspk",
            claimed_at=datetime.now(UTC),
        )
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        active_order_repository=active_repo,
        notify_order_ready=lambda order: capture_order(order, []),
    )

    count = await agent.load_persisted_orders()

    assert count == 1
    assert state.snapshot().active_count == 1
    assert state.snapshot().active_orders[0].id == "3567100"


async def capture_order(order: ActiveOrder, storage: list[ActiveOrder]) -> None:
    storage.append(order)


def make_settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            platform_base_url="https://app.send.tg",
            platform_ws_url="wss://app.send.tg/internal/v1/p2c-socket/?EIO=4&transport=websocket",
            platform_claim_from_snapshot=False,
            platform_take_burst_size=1,
            platform_take_health_enabled=False,
            platform_take_health_interval_seconds=5,
        ),
    )


@pytest.mark.asyncio
async def test_live_agent_l1_session_cache_reduces_repository_reads() -> None:
    state = InMemoryAgentState()
    repository = CountingSessionRepository()
    await repository.save(
        PlatformSession(
            access_token="token",
            cf_bm="cf",
            updated_at=datetime.now(UTC),
        )
    )
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )

    first = await agent._get_session()
    second = await agent._get_session()

    assert first is not None
    assert second is not None
    assert repository.current_calls == 1


@pytest.mark.asyncio
async def test_live_agent_uses_session_hint_without_repository_read() -> None:
    state = InMemoryAgentState()
    repository = CountingSessionRepository()
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    session = PlatformSession(
        access_token="token",
        cf_bm="cf",
        updated_at=datetime.now(UTC),
    )
    agent.set_session_hint(session)

    loaded = await agent._get_session()

    assert loaded == session
    assert repository.current_calls == 0


@pytest.mark.asyncio
async def test_live_agent_forces_single_take_attempt_even_when_burst_configured() -> None:
    state = InMemoryAgentState()
    state.run()
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(
            access_token="token",
            cf_bm="cf",
            updated_at=datetime.now(UTC),
        )
    )
    notifications: list[ActiveOrder] = []
    agent = P2CLiveAgent(
        settings=cast(
            Settings,
            SimpleNamespace(
                platform_base_url="https://app.send.tg",
                platform_ws_url="wss://app.send.tg/internal/v1/p2c-socket/?EIO=4&transport=websocket",
                platform_claim_from_snapshot=False,
                platform_take_burst_size=3,
            ),
        ),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, notifications),
    )
    fake = FakePaymentsClient()
    fake.take_side_effects = [
        (0.0, 3567999),
        (0.0, P2CPaymentsError("must not be called")),
    ]
    agent._payments_client = fake  # type: ignore[assignment]

    await agent._process_event(
        P2COrderEvent(
            socket_order_id="burst-1",
            in_amount=state.snapshot().min_amount + 1,
            in_asset="RUB",
            out_asset="USDT",
            url="https://multiqr.test",
            payload="trx-123",
            brand_name="Shop",
            provider="platiqr",
        ),
        0.0,
        agent._pause_generation,  # type: ignore[attr-defined]
    )

    snapshot = state.snapshot()
    assert len(fake.take_calls) == 1
    assert snapshot.active_count == 1
    assert snapshot.active_orders[0].id == "3567999"


@pytest.mark.asyncio
async def test_live_agent_penalty_backoff_auto_resumes_waiting_mode() -> None:
    state = InMemoryAgentState()
    state.run()
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(
            access_token="token",
            cf_bm="cf",
            updated_at=datetime.now(UTC),
        )
    )
    notifications: list[ActiveOrder] = []
    agent = P2CLiveAgent(
        settings=cast(
            Settings,
            SimpleNamespace(
                platform_base_url="https://app.send.tg",
                platform_ws_url="wss://app.send.tg/internal/v1/p2c-socket/?EIO=4&transport=websocket",
                platform_claim_from_snapshot=False,
                platform_take_burst_size=1,
            ),
        ),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, notifications),
    )
    fake = FakePaymentsClient()
    fake.take_side_effects = [
        (
            0.0,
            P2CPaymentsError(
                'POST /internal/v1/p2c/payments/take/xyz failed with status 403: {"error":"MerchantPenalized","retry_after":0}'
            ),
        )
    ]
    agent._payments_client = fake  # type: ignore[assignment]

    await agent._process_event(
        P2COrderEvent(
            socket_order_id="penalty-resume",
            in_amount=state.snapshot().min_amount + 1,
            in_asset="RUB",
            out_asset="USDT",
            url="https://multiqr.test",
            payload="trx-penalty",
            brand_name="Shop",
            provider="platiqr",
        ),
        0.0,
        agent._pause_generation,  # type: ignore[attr-defined]
    )
    await asyncio.sleep(0.05)

    snapshot = state.snapshot()
    assert len(fake.take_calls) == 1
    assert snapshot.mode == AgentMode.WAITING
    assert snapshot.active_count == 0


@pytest.mark.asyncio
async def test_live_agent_pauses_when_take_returns_401() -> None:
    state = InMemoryAgentState()
    state.run()
    repository = InMemoryPlatformSessionRepository()
    await repository.save(
        PlatformSession(
            access_token="token",
            cf_bm="cf",
            updated_at=datetime.now(UTC),
        )
    )
    notifications: list[ActiveOrder] = []
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, notifications),
    )
    fake = FakePaymentsClient()
    fake.take_side_effects = [(0.0, P2CPaymentsError("POST /take failed with status 401: Unauthorized"))]
    agent._payments_client = fake  # type: ignore[assignment]

    await agent._process_event(
        P2COrderEvent(
            socket_order_id="auth-401",
            in_amount=state.snapshot().min_amount + 1,
            in_asset="RUB",
            out_asset="USDT",
            url="https://multiqr.test",
            payload="trx-auth",
            brand_name="Shop",
            provider="platiqr",
        ),
        0.0,
        agent._pause_generation,  # type: ignore[attr-defined]
    )

    snapshot = state.snapshot()
    assert snapshot.mode == AgentMode.PAUSED
    assert snapshot.active_count == 0
    assert notifications == []


@pytest.mark.asyncio
async def test_live_agent_prewarm_raises_on_unauthorized() -> None:
    state = InMemoryAgentState()
    repository = InMemoryPlatformSessionRepository()
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, []),
    )
    fake = FakePaymentsClient()
    fake.prewarm_error = P2CPaymentsError("GET /internal/v1/p2c/accounts failed with status 401: Unauthorized")
    agent._payments_client = fake  # type: ignore[assignment]
    session = PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))

    with pytest.raises(P2CPaymentsError, match="unauthorized"):
        await agent.prewarm_take_channels(session)
