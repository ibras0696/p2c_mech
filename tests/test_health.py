from app.main import app
from app.services.runtime_registry import clear_runtime_provider, set_runtime_provider
from fastapi.testclient import TestClient


def test_healthcheck() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_runtime_health_without_provider() -> None:
    clear_runtime_provider()
    client = TestClient(app)
    response = client.get("/health/runtime")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "runtime": [], "runtime_count": 0}


def test_runtime_health_with_provider() -> None:
    class FakeProvider:
        async def runtime_statuses(self) -> list[dict[str, object]]:
            return [
                {
                    "user_id": 100,
                    "mode": "paused",
                    "active_count": 0,
                    "active_limit": 1,
                    "free_slots": 1,
                    "running": True,
                    "last_used_at": "2026-05-27T00:00:00+00:00",
                }
            ]

    set_runtime_provider(FakeProvider())
    try:
        client = TestClient(app)
        response = client.get("/health/runtime")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "runtime_count": 1,
            "runtime": [
                {
                    "user_id": 100,
                    "mode": "paused",
                    "active_count": 0,
                    "active_limit": 1,
                    "free_slots": 1,
                    "running": True,
                    "last_used_at": "2026-05-27T00:00:00+00:00",
                }
            ],
        }
    finally:
        clear_runtime_provider()
