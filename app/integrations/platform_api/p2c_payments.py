from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import dataclass
from typing import Any

import httpx
from curl_cffi.const import CurlHttpVersion
from curl_cffi.requests import AsyncSession as CurlAsyncSession

from app.bot.session_state import PlatformSession
from app.core.logging import get_logger

logger = get_logger(__name__)

# Browser impersonation used for take POSTs — gives us a real Chrome TLS
# fingerprint instead of httpx's, which Cloudflare can detect as a bot.
_CURL_IMPERSONATE = "chrome131"



class P2CPaymentsError(RuntimeError):
    pass


@dataclass(frozen=True)
class P2CPaymentDetails:
    id: int
    status: str
    in_amount: str
    in_asset: str
    out_amount: str
    out_asset: str
    brand_name: str
    provider: str
    url: str
    payload: str
    method_id: str
    raw: dict[str, Any]


class P2CPaymentsClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 10.0,
        take_send_cf_cookie: bool = False,
    ) -> None:
        if not base_url:
            raise ValueError("Platform base URL is required")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._take_send_cf_cookie = take_send_cf_cookie
        self._client = self._build_httpx_client()
        # curl-cffi session for take POSTs: Chrome TLS fingerprint + HTTP/2 keepalive.
        self._curl_session = CurlAsyncSession(impersonate=_CURL_IMPERSONATE, max_clients=1)

    def _build_httpx_client(self) -> httpx.AsyncClient:
        http2_enabled = _is_http2_supported()
        try:
            transport = httpx.AsyncHTTPTransport(
                retries=0,
                http2=http2_enabled,
                local_address="0.0.0.0",
                socket_options=[
                    (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
                    (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
                ],
            )
        except TypeError:
            transport = httpx.AsyncHTTPTransport(
                retries=0,
                http2=False,
                local_address="0.0.0.0",
            )
            http2_enabled = False
        return httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            http2=http2_enabled,
            trust_env=False,
            transport=transport,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
                keepalive_expiry=30.0,
            ),
        )

    async def aclose(self) -> None:
        await self._curl_session.close()
        await self._client.aclose()

    async def take(
        self,
        *,
        socket_order_id: str,
        session: PlatformSession,
        client_slot: int | None = None,
        payment_method_id: str = "",  # accepted but not sent (A/B: body disabled)
    ) -> int:
        payload = await self._curl_take(
            path=f"/internal/v1/p2c/payments/take/{socket_order_id}",
            session=session,
        )
        payment_id = extract_payment_id(payload)
        if payment_id is None:
            raise P2CPaymentsError("Take response does not contain payment id")
        return payment_id

    async def prewarm_take_clients(self, *, session: PlatformSession, channels: int = 3) -> None:
        """Warm up the curl session by making a cheap GET (establishes TLS + HTTP/2)."""
        if channels <= 0:
            return
        try:
            await self._curl_get_prewarm(session=session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=prewarm_curl_failed error=%s", exc)

    async def get_payment(self, *, payment_id: int, session: PlatformSession) -> P2CPaymentDetails:
        payload = await self._request_json(
            method="GET",
            path=f"/internal/v1/p2c/payments/{payment_id}",
            session=session,
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise P2CPaymentsError("Payment details response does not contain data object")
        return P2CPaymentDetails(
            id=to_int(data.get("id"), "id"),
            status=to_str(data.get("status")),
            in_amount=to_str(data.get("in_amount")),
            in_asset=to_str(data.get("in_asset")),
            out_amount=to_str(data.get("out_amount")),
            out_asset=to_str(data.get("out_asset")),
            brand_name=to_str(data.get("brand_name")),
            provider=to_str(data.get("provider")),
            url=to_str(data.get("url")),
            payload=to_str(data.get("payload")),
            method_id=extract_method_id(data),
            raw=data,
        )

    async def complete(
        self,
        *,
        payment_id: int,
        method_id: str,
        session: PlatformSession,
    ) -> None:
        if not method_id:
            raise P2CPaymentsError("method_id is required to complete payment")
        await self._request(
            method="POST",
            path=f"/internal/v1/p2c/payments/{payment_id}/complete",
            session=session,
            json_body={"method": method_id},
            expect_json=False,
        )

    async def cancel(
        self,
        *,
        payment_id: int,
        session: PlatformSession,
        method_id: str = "",
    ) -> None:
        body_variants: list[dict[str, Any] | None] = []
        if method_id:
            body_variants.append({"method": method_id})
        body_variants.append(None)
        path_variants = [
            f"/internal/v1/p2c/payments/{payment_id}/cancel",
            f"/internal/v1/p2c/payments/{payment_id}/decline",
        ]
        last_error: P2CPaymentsError | None = None
        for path in path_variants:
            for body in body_variants:
                try:
                    await self._request(
                        method="POST",
                        path=path,
                        session=session,
                        json_body=body,
                        expect_json=False,
                    )
                except P2CPaymentsError as exc:
                    last_error = exc
                    text = str(exc)
                    if (
                        "status 404" in text
                        or "status 405" in text
                        or "status 422" in text
                        or "status 400" in text
                    ):
                        continue
                    raise
                else:
                    return
        if last_error is not None:
            raise P2CPaymentsError(f"Cancel failed for payment {payment_id}: {last_error}") from last_error
        raise P2CPaymentsError(f"Cancel failed for payment {payment_id}")

    async def list_accounts(self, *, session: PlatformSession) -> list[dict[str, Any]]:
        payload = await self._request_json(
            method="GET",
            path="/internal/v1/p2c/accounts",
            session=session,
        )
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    async def _request_json(
        self,
        *,
        method: str,
        path: str,
        session: PlatformSession,
        json_body: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        body = await self._request(
            method=method,
            path=path,
            session=session,
            json_body=json_body,
            expect_json=True,
            client=client,
        )
        if not isinstance(body, dict):
            raise P2CPaymentsError(f"{method} {path} returned unexpected payload")
        return body

    async def _request(
        self,
        *,
        method: str,
        path: str,
        session: PlatformSession,
        json_body: dict[str, Any] | None = None,
        expect_json: bool,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any] | None:
        if not session.cookie_header:
            raise P2CPaymentsError("Platform session cookie is missing")
        headers = {
            "accept": "application/json, text/plain, */*",
            "cookie": self._cookie_for_request(method=method, session=session),
            "origin": self._base_url,
            "referer": f"{self._base_url}/p2c/orders",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
        }
        if json_body is not None:
            headers["content-type"] = "application/json"
        url = f"{self._base_url}{path}"
        response = await (client or self._client).request(
            method=method, url=url, headers=headers, json=json_body
        )
        if response.status_code >= 400:
            raise P2CPaymentsError(
                f"{method} {path} failed with status {response.status_code}: {response.text[:300]}"
            )
        if not expect_json:
            return None
        try:
            body: Any = response.json()
        except ValueError as exc:
            raise P2CPaymentsError(f"{method} {path} returned non-JSON body") from exc
        return body if isinstance(body, dict) else None

    async def _curl_take(
        self,
        *,
        path: str,
        session: PlatformSession,
    ) -> dict[str, Any]:
        """POST via curl-cffi with Chrome TLS fingerprint + HTTP/2."""
        if not session.cookie_header:
            raise P2CPaymentsError("Platform session cookie is missing")
        cookie = (
            session.cookie_header
            if self._take_send_cf_cookie
            else f"access_token={session.access_token}"
        )
        headers = {
            "cookie": cookie,
        }
        url = f"{self._base_url}{path}"
        started = time.perf_counter()
        response = await self._curl_session.post(
            url,
            headers=headers,
            http_version=CurlHttpVersion.V2TLS,
            timeout=self._timeout,
            allow_redirects=False,
        )
        total_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "event=take_curl order=%s status=%d total_ms=%d",
            path.rsplit("/", 1)[-1],
            response.status_code,
            total_ms,
        )
        if response.status_code >= 400:
            raise P2CPaymentsError(
                f"POST {path} failed with status {response.status_code}: {response.text[:300]}"
            )
        try:
            body: Any = response.json()
        except ValueError as exc:
            raise P2CPaymentsError(f"POST {path} returned non-JSON body") from exc
        if not isinstance(body, dict):
            raise P2CPaymentsError(f"POST {path} returned unexpected payload")
        return body

    async def _curl_get_prewarm(self, *, session: PlatformSession) -> None:
        """Warm up curl session: GET /accounts establishes TLS + HTTP/2 connection."""
        if not session.cookie_header:
            return
        headers = {
            "accept": "application/json, text/plain, */*",
            "cookie": session.cookie_header,
            "origin": self._base_url,
            "referer": f"{self._base_url}/p2c/orders",
        }
        url = f"{self._base_url}/internal/v1/p2c/accounts"
        await self._curl_session.get(
            url,
            headers=headers,
            http_version=CurlHttpVersion.V2TLS,
            timeout=self._timeout,
            allow_redirects=False,
        )
        logger.info("event=prewarm_curl_succeeded")

    def _cookie_for_request(self, *, method: str, session: PlatformSession) -> str:
        # For take/complete/cancel we send only access_token by default — sending
        # __cf_bm there can route us through a slower Cloudflare bot check.
        # PLATFORM_TAKE_SEND_CF_COOKIE=true forces the full cookie header instead.
        if method.upper() == "POST" and session.access_token:
            if self._take_send_cf_cookie:
                return session.cookie_header
            return f"access_token={session.access_token}"
        return session.cookie_header


def extract_payment_id(payload: dict[str, Any]) -> int | None:
    candidates = [
        payload.get("id"),
        payload.get("payment_id"),
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("id"), data.get("payment_id")])
    for candidate in candidates:
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, str) and candidate.isdigit():
            return int(candidate)
    return None


def extract_method_id(data: dict[str, Any]) -> str:
    direct_keys = (
        "method",
        "method_id",
        "payment_method_id",
        "account_method_id",
        "account_id",
    )
    for key in direct_keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
    nested_method = data.get("method")
    if isinstance(nested_method, dict):
        for key in ("id", "method_id", "payment_method_id"):
            value = nested_method.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, int):
                return str(value)
    nested_account = data.get("account")
    if isinstance(nested_account, dict):
        for key in ("id", "account_id", "method_id"):
            value = nested_account.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, int):
                return str(value)
    return ""


def to_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def to_int(value: Any, field_name: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise P2CPaymentsError(f"Payment details has invalid {field_name}")


def _is_http2_supported() -> bool:
    try:
        import h2  # noqa: F401
    except ImportError:
        return False
    return True
