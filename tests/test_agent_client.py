from __future__ import annotations

import httpx
import pytest
from app.bot.agent_client import AgentClient, AgentClientError, FilterPayload


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch: pytest.MonkeyPatch):
    # Patch the module-level httpx.AsyncClient used by AgentClient._request.
    holder: dict[str, object] = {}

    real = httpx.AsyncClient

    def fake(*args, **kwargs):  # noqa: ANN002, ANN003
        transport = holder.get("transport")
        if transport is not None:
            kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr("app.bot.agent_client.httpx.AsyncClient", fake)
    return holder


@pytest.mark.asyncio
async def test_start_posts_to_start_endpoint(_patch_async_client) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"running": True, "started": True})

    _patch_async_client["transport"] = httpx.MockTransport(handler)
    client = AgentClient("http://app:8000")

    result = await client.start()

    assert seen == {"method": "POST", "path": "/agent/start"}
    assert result["running"] is True


@pytest.mark.asyncio
async def test_add_account_sends_filters_and_label(_patch_async_client) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    _patch_async_client["transport"] = httpx.MockTransport(handler)
    client = AgentClient("http://app:8000")

    await client.add_account(
        account="acc42",
        access_token="tok",
        cookie_header="access_token=tok; __cf_bm=cf",
        filters=FilterPayload(min_amount=100, max_amount=500, currencies=["RUB"]),
    )

    body = captured["body"]
    assert body["account"] == "acc42"
    assert body["label"] == "acc42"
    assert body["filters"] == {"min_amount": 100, "max_amount": 500, "currencies": ["RUB"]}


@pytest.mark.asyncio
async def test_stats_passes_query_params(_patch_async_client) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"account": "acc1", "aggregate": {}})

    _patch_async_client["transport"] = httpx.MockTransport(handler)
    client = AgentClient("http://app:8000")

    await client.stats(account="acc1", period="today")

    assert captured["params"] == {"account": "acc1", "period": "today"}


@pytest.mark.asyncio
async def test_http_error_becomes_agent_client_error(_patch_async_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "agent supervisor not configured"})

    _patch_async_client["transport"] = httpx.MockTransport(handler)
    client = AgentClient("http://app:8000")

    with pytest.raises(AgentClientError) as excinfo:
        await client.status()

    assert "503" in excinfo.value.message


@pytest.mark.asyncio
async def test_connection_error_becomes_friendly_error(_patch_async_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_async_client["transport"] = httpx.MockTransport(handler)
    client = AgentClient("http://app:8000")

    with pytest.raises(AgentClientError) as excinfo:
        await client.stop()

    assert "недоступен" in excinfo.value.message
