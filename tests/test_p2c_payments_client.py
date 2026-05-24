from datetime import UTC, datetime

import httpx
import pytest
from app.bot.session_state import PlatformSession
from app.integrations.platform_api.p2c_payments import (
    P2CPaymentsClient,
    P2CPaymentsError,
    extract_method_id,
    extract_payment_id,
)


def test_extract_payment_id_from_data_object() -> None:
    payload = {"data": {"id": 3566992}}
    assert extract_payment_id(payload) == 3566992


def test_extract_method_id_prefers_direct_method() -> None:
    payload = {"method": "69eb8d7e6bdfddede1de9a79"}
    assert extract_method_id(payload) == "69eb8d7e6bdfddede1de9a79"


def test_extract_method_id_falls_back_to_account_id() -> None:
    payload = {
        "method": None,
        "method_id": None,
        "account": {"id": "69eb8d7e6bdfddede1de9a79"},
    }
    assert extract_method_id(payload) == "69eb8d7e6bdfddede1de9a79"


def test_extract_method_id_reads_direct_account_id() -> None:
    payload = {"account_id": "69eb8d7e6bdfddede1de9a79"}
    assert extract_method_id(payload) == "69eb8d7e6bdfddede1de9a79"


@pytest.mark.asyncio
async def test_complete_accepts_non_json_200_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/complete"):
            return httpx.Response(200, text="ok")
        return httpx.Response(404, json={"error": "not found"})

    client = P2CPaymentsClient(base_url="https://app.send.tg")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
    session = PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))

    await client.complete(payment_id=1, method_id="m1", session=session)
    await client.aclose()


@pytest.mark.asyncio
async def test_get_payment_requires_json_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/payments/1"):
            return httpx.Response(200, text="ok")
        return httpx.Response(404, json={"error": "not found"})

    client = P2CPaymentsClient(base_url="https://app.send.tg")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
    session = PlatformSession(access_token="token", cf_bm="cf", updated_at=datetime.now(UTC))

    with pytest.raises(P2CPaymentsError, match="returned non-JSON body"):
        await client.get_payment(payment_id=1, session=session)
    await client.aclose()
