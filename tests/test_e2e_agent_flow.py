from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from app.bot.session_state import PlatformSession
from app.bot.state import ActiveOrder, AgentMode, InMemoryAgentState
from app.core.config import Settings
from app.integrations.platform_api import P2CPaymentDetails, P2CPaymentsError
from app.repositories.platform_session import InMemoryPlatformSessionRepository
from app.services.p2c_live_agent import P2CLiveAgent


class E2EFakePaymentsClient:
    def __init__(self) -> None:
        self.take_calls: list[str] = []
        self.complete_calls: list[tuple[int, str]] = []
        self.get_payment_error: Exception | None = None

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
        return 7770001

    async def get_payment(self, *, payment_id: int, session: PlatformSession) -> P2CPaymentDetails:
        del session
        if self.get_payment_error is not None:
            raise self.get_payment_error
        return P2CPaymentDetails(
            id=payment_id,
            status="processing",
            in_amount="120",
            in_asset="RUB",
            out_amount="1000000000000000000",
            out_asset="USDT",
            brand_name="E2E Shop",
            provider="nspk",
            url="https://qr.nspk.ru/TEST",
            payload="TESTPAYLOAD",
            method_id="method-e2e",
            raw={"id": payment_id},
        )

    async def complete(self, *, payment_id: int, method_id: str, session: PlatformSession) -> None:
        del session
        self.complete_calls.append((payment_id, method_id))

    async def prewarm_take_clients(self, *, session: PlatformSession, channels: int = 3) -> None:
        del session
        del channels


@pytest.mark.asyncio
async def test_e2e_socket_update_to_complete_flow() -> None:
    state = InMemoryAgentState()
    state.run()
    repository = InMemoryPlatformSessionRepository()
    await repository.save(PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC)))
    notifications: list[ActiveOrder] = []
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, notifications),
    )
    fake = E2EFakePaymentsClient()
    agent._payments_client = fake  # type: ignore[assignment]

    await agent._on_socket_message(build_update_message(order_id="socket-e2e-1", amount="120"))
    await wait_for(lambda: state.snapshot().active_count == 1)

    snapshot = state.snapshot()
    assert snapshot.active_orders[0].id == "7770001"
    assert snapshot.active_orders[0].method_id == "method-e2e"
    assert len(notifications) == 1
    assert fake.take_calls == ["socket-e2e-1"]

    await agent.complete_order("7770001")
    assert state.snapshot().active_count == 0
    assert fake.complete_calls == [(7770001, "method-e2e")]


@pytest.mark.asyncio
async def test_e2e_taken_order_is_preserved_when_confirm_api_fails() -> None:
    state = InMemoryAgentState()
    state.run()
    repository = InMemoryPlatformSessionRepository()
    await repository.save(PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC)))
    notifications: list[ActiveOrder] = []
    agent = P2CLiveAgent(
        settings=make_settings(),
        state=state,
        session_repository=repository,
        notify_order_ready=lambda order: capture_order(order, notifications),
    )
    fake = E2EFakePaymentsClient()
    fake.get_payment_error = P2CPaymentsError("temporary upstream error")
    agent._payments_client = fake  # type: ignore[assignment]

    await agent._on_socket_message(build_update_message(order_id="socket-e2e-2", amount="250"))
    await wait_for(lambda: state.snapshot().active_count == 1 and state.snapshot().mode == AgentMode.PAUSED)

    snapshot = state.snapshot()
    assert snapshot.active_orders[0].id == "7770001"
    assert snapshot.active_orders[0].method_id == ""
    assert snapshot.mode == AgentMode.PAUSED
    assert len(notifications) == 1


def build_update_message(*, order_id: str, amount: str) -> str:
    payload = [
        "list:update",
        [
            {
                "op": "add",
                "data": {
                    "id": order_id,
                    "in_amount": amount,
                    "in_asset": "RUB",
                    "out_asset": "USDT",
                    "url": "https://qr.nspk.ru/TEST",
                    "payload": "payload",
                    "brand_name": "Shop",
                    "provider": "nspk",
                },
            }
        ],
    ]
    return f"42{json.dumps(payload, ensure_ascii=False)}"


async def capture_order(order: ActiveOrder, storage: list[ActiveOrder]) -> None:
    storage.append(order)


def make_settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            platform_base_url="https://app.send.tg",
            platform_ws_url="wss://app.send.tg/internal/v1/p2c-socket/?EIO=4&transport=websocket",
            platform_claim_from_snapshot=False,
        ),
    )


async def wait_for(predicate, timeout_seconds: float = 1.5) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Condition not reached within timeout")
