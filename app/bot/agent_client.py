from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


class AgentClientError(Exception):
    """Raised when the FastAPI supervisor is unreachable or returns an error.

    Handlers catch this and surface a friendly message instead of crashing the bot.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class FilterPayload:
    min_amount: int = 0
    max_amount: int = 0
    currencies: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "min_amount": self.min_amount,
            "max_amount": self.max_amount,
            "currencies": self.currencies,
        }


class AgentClient:
    """Thin async HTTP client wrapping the FastAPI supervisor endpoints (§7).

    Every method maps to one endpoint in app/api/agent.py or app/api/stats.py.
    Connection / HTTP errors are converted to AgentClientError so callers can
    show a friendly message; the bot never crashes on a supervisor outage.
    """

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning(
                "event=agent_client_connect_failed method=%s path=%s error=%s",
                method,
                path,
                type(exc).__name__,
            )
            raise AgentClientError("Сервис снайпера недоступен. Проверьте, что FastAPI запущен.") from exc
        if response.status_code >= 400:
            detail = _extract_detail(response)
            logger.warning(
                "event=agent_client_http_error method=%s path=%s status=%s detail=%s",
                method,
                path,
                response.status_code,
                detail,
            )
            raise AgentClientError(f"Сервис снайпера вернул ошибку {response.status_code}: {detail}")
        try:
            payload = response.json()
        except ValueError:
            return {}
        if isinstance(payload, dict):
            return payload
        return {"data": payload}

    # --- /agent endpoints -------------------------------------------------

    async def start(self) -> dict[str, Any]:
        return await self._request("POST", "/agent/start")

    async def stop(self) -> dict[str, Any]:
        return await self._request("POST", "/agent/stop")

    async def add_account(
        self,
        *,
        account: str,
        access_token: str,
        cookie_header: str,
        did: str = "",
        label: str = "",
        filters: FilterPayload | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "account": account,
            "access_token": access_token,
            "cookie_header": cookie_header,
            "did": did,
            "label": label or account,
            "filters": (filters or FilterPayload()).as_dict(),
        }
        return await self._request("POST", "/agent/account", json=body)

    async def remove_account(self, account: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/agent/account/{account}")

    async def set_session(
        self,
        *,
        account: str,
        access_token: str,
        cookie_header: str,
        did: str = "",
    ) -> dict[str, Any]:
        body = {
            "account": account,
            "access_token": access_token,
            "cookie_header": cookie_header,
            "did": did,
        }
        return await self._request("POST", "/agent/session", json=body)

    async def set_filter(
        self,
        *,
        account: str,
        min_amount: int = 0,
        max_amount: int = 0,
        currencies: list[str] | None = None,
    ) -> dict[str, Any]:
        body = {
            "account": account,
            "min_amount": min_amount,
            "max_amount": max_amount,
            "currencies": currencies or [],
        }
        return await self._request("POST", "/agent/filter", json=body)

    async def set_mode(self, *, value: str, account: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"value": value}
        if account:
            body["account"] = account
        return await self._request("POST", "/agent/mode", json=body)

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/agent/status")

    # --- /stats endpoint (decoupled from the agent, §6.2) -----------------

    async def stats(self, *, account: str | None = None, period: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if account:
            params["account"] = account
        if period:
            params["period"] = period
        return await self._request("GET", "/stats", params=params)


def _extract_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return str(payload)[:200]
