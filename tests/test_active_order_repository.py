from datetime import UTC, datetime

import pytest

from app.bot.state import ActiveOrder
from app.repositories.active_orders import InMemoryActiveOrderRepository


@pytest.mark.asyncio
async def test_in_memory_active_order_repository_roundtrip() -> None:
    repo = InMemoryActiveOrderRepository()
    order = ActiveOrder(
        id="3567001",
        amount="150",
        currency="RUB",
        direction="P2C",
        provider="nspk",
        claimed_at=datetime.now(UTC),
    )

    await repo.upsert(order)
    data = await repo.list_all()

    assert len(data) == 1
    assert data[0].id == "3567001"


@pytest.mark.asyncio
async def test_in_memory_active_order_repository_remove() -> None:
    repo = InMemoryActiveOrderRepository()
    await repo.upsert(
        ActiveOrder(
            id="3567002",
            amount="200",
            currency="RUB",
            direction="P2C",
            claimed_at=datetime.now(UTC),
        )
    )
    await repo.remove("3567002")

    assert await repo.list_all() == []
