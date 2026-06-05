from __future__ import annotations

from datetime import UTC, datetime

from app.main import app
from app.repositories.agent_stats_repo import (
    KIND_CLAIM_LOST,
    KIND_CLAIM_WON,
    KIND_TAKE_RESULT,
    InMemoryAgentStatsRepository,
    OrderEvent,
)
from app.services.agent_registry import (
    clear_agent_registry,
    set_stats_redis,
    set_stats_repo,
    set_supervisor,
)
from fastapi.testclient import TestClient


class FakeRedis:
    def __init__(self, kv: dict[str, str]) -> None:
        self.kv = kv

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)


async def test_stats_works_with_supervisor_absent() -> None:
    # Stats must work even when the agent/supervisor is stopped (§6.2).
    clear_agent_registry()
    set_supervisor(None)
    repo = InMemoryAgentStatsRepository()
    set_stats_repo(repo)
    set_stats_redis(
        FakeRedis(
            {
                "p2c:stat:acc1:takes": "5",
                "p2c:stat:acc1:wins": "2",
                "p2c:stat:acc1:orders_seen": "40",
            }
        )
    )

    ts = datetime.now(UTC)
    await repo.record_event(OrderEvent("acc1", "o1", KIND_TAKE_RESULT, 200, 90.0, 1, ts))
    await repo.record_event(OrderEvent("acc1", "o2", KIND_TAKE_RESULT, 200, 110.0, 2, ts))
    await repo.record_event(OrderEvent("acc1", "o1", KIND_CLAIM_WON, None, None, 1, ts))
    await repo.record_event(OrderEvent("acc1", "o2", KIND_CLAIM_LOST, 400, None, None, ts))

    try:
        client = TestClient(app)
        response = client.get("/stats", params={"account": "acc1", "period": "today"})
        assert response.status_code == 200
        body = response.json()
        assert body["account"] == "acc1"
        assert body["live"] == {"takes": 5, "wins": 2, "orders_seen": 40}
        agg = body["aggregate"]
        assert agg["takes"] == 2
        assert agg["wins"] == 1
        assert agg["losses"] == 1
        assert agg["win_rate"] == 0.5
        assert agg["p50_http_ms"] == 100.0  # interpolated between 90 and 110
        assert len(agg["recent"]) == 4
    finally:
        clear_agent_registry()


def test_stats_empty_when_nothing_configured() -> None:
    clear_agent_registry()
    client = TestClient(app)
    response = client.get("/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["account"] is None
    assert body["live"] == {}
    assert body["aggregate"]["takes"] == 0
    clear_agent_registry()


def test_agent_status_without_supervisor() -> None:
    clear_agent_registry()
    client = TestClient(app)
    response = client.get("/agent/status")
    assert response.status_code == 200
    assert response.json() == {"running": False, "accounts": []}
    clear_agent_registry()
