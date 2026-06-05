from __future__ import annotations

from typing import Any

from app.main import app
from app.services.agent_registry import clear_agent_registry, set_supervisor
from fastapi.testclient import TestClient


class FakeSupervisor:
    def __init__(self) -> None:
        self.running = False
        self.accounts: dict[str, Any] = {}
        self.commands: list[dict[str, Any]] = []
        self.cached_sessions: list[tuple[str, Any]] = []
        self.cached_configs: list[dict[str, Any]] = []
        self.enabled_removed: list[str] = []

    async def spawn(self) -> bool:
        self.running = True
        return True

    async def stop(self) -> None:
        self.running = False

    async def send_cmd(self, cmd: dict[str, Any]) -> None:
        self.commands.append(cmd)

    async def cache_session(self, account: str, session: Any) -> None:
        self.cached_sessions.append((account, session))

    async def cache_account_config(self, account: str, **kw: Any) -> None:
        self.cached_configs.append({"account": account, **kw})

    async def remove_account_from_enabled(self, account: str) -> None:
        self.enabled_removed.append(account)

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "accounts": []}


def _client_with_fake() -> tuple[TestClient, FakeSupervisor]:
    fake = FakeSupervisor()
    client = TestClient(app)
    client.__enter__()  # run startup so default wiring happens first
    set_supervisor(fake)  # then override with our fake
    return client, fake


def test_account_upsert_caches_and_sends_add_account() -> None:
    clear_agent_registry()
    client, fake = _client_with_fake()
    try:
        resp = client.post(
            "/agent/account",
            json={
                "account": "acc1",
                "access_token": "t",
                "cookie_header": "c; __cf_bm=z",
                "filters": {"min_amount": 100, "max_amount": 5000, "currencies": ["RUB"]},
            },
        )
        assert resp.status_code == 200
        assert fake.cached_sessions[0][0] == "acc1"
        assert fake.cached_configs[0]["enabled"] is True
        add = [c for c in fake.commands if c["cmd"] == "add_account"][0]
        assert add["account"] == "acc1"
        assert add["filters"] == {"min_amount": 100, "max_amount": 5000, "currencies": ["RUB"]}
    finally:
        client.__exit__(None, None, None)
        clear_agent_registry()


def test_remove_account_sends_cmd_and_srem() -> None:
    clear_agent_registry()
    client, fake = _client_with_fake()
    try:
        resp = client.delete("/agent/account/acc9")
        assert resp.status_code == 200
        assert {"cmd": "remove_account", "account": "acc9"} in fake.commands
        assert "acc9" in fake.enabled_removed
    finally:
        client.__exit__(None, None, None)
        clear_agent_registry()


def test_mode_rejects_invalid_value() -> None:
    clear_agent_registry()
    client, fake = _client_with_fake()
    try:
        resp = client.post("/agent/mode", json={"value": "bogus"})
        assert resp.status_code == 422
        resp_ok = client.post("/agent/mode", json={"value": "running"})
        assert resp_ok.status_code == 200
        assert {"cmd": "mode", "value": "running"} in fake.commands
    finally:
        client.__exit__(None, None, None)
        clear_agent_registry()


def test_start_stop() -> None:
    clear_agent_registry()
    client, fake = _client_with_fake()
    try:
        assert client.post("/agent/start").json()["running"] is True
        assert client.post("/agent/stop").json()["running"] is False
    finally:
        client.__exit__(None, None, None)
        clear_agent_registry()
