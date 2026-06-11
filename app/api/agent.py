from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.services.agent_registry import get_supervisor
from app.services.agent_supervisor import AgentSession, AgentSupervisor

logger = get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


class FilterPayload(BaseModel):
    min_amount: int = 0
    max_amount: int = 0
    currencies: list[str] = Field(default_factory=list)


class AccountPayload(BaseModel):
    account: str
    access_token: str
    cookie_header: str
    did: str = ""
    label: str = ""
    filters: FilterPayload = Field(default_factory=FilterPayload)


class SessionPayload(BaseModel):
    account: str
    access_token: str
    cookie_header: str
    did: str = ""


class FilterUpdate(BaseModel):
    account: str
    min_amount: int = 0
    max_amount: int = 0
    currencies: list[str] = Field(default_factory=list)


class ModePayload(BaseModel):
    account: str | None = None
    value: str


def _require_supervisor() -> AgentSupervisor:
    supervisor = get_supervisor()
    if supervisor is None:
        raise HTTPException(status_code=503, detail="agent supervisor not configured")
    return supervisor


def _c_agent_disabled() -> bool:
    """The C agent is retired in favour of the Python live agent. When the
    Python socket owns the hot path, refuse to spawn/feed the C agent so it can
    never accidentally start a second competing socket on the same account."""
    from app.core.config import get_settings

    return bool(get_settings().platform_python_socket_enabled)


@router.post("/start")
async def start_agent() -> dict[str, Any]:
    if _c_agent_disabled():
        logger.info("c_agent_start_skipped reason=python_socket_authority")
        return {"running": False, "started": False, "disabled": "c_agent"}
    supervisor = _require_supervisor()
    started = await supervisor.spawn()
    return {"running": supervisor.running, "started": started}


@router.post("/stop")
async def stop_agent() -> dict[str, Any]:
    supervisor = _require_supervisor()
    await supervisor.stop()
    return {"running": supervisor.running}


@router.post("/account")
async def upsert_account(payload: AccountPayload) -> dict[str, Any]:
    if _c_agent_disabled():
        logger.info("c_agent_account_skipped reason=python_socket_authority account=%s", payload.account)
        return {"ok": True, "account": payload.account, "disabled": "c_agent"}
    supervisor = _require_supervisor()
    session = AgentSession(
        access_token=payload.access_token,
        cookie_header=payload.cookie_header,
        did=payload.did,
    )
    filters = payload.filters.model_dump()
    # Single writer (§3.4): cache session + account config + enabled set, then push to agent.
    await supervisor.cache_session(payload.account, session)
    await supervisor.cache_account_config(
        payload.account,
        label=payload.label or payload.account,
        filters=filters,
        enabled=True,
    )
    await supervisor.send_cmd(
        {
            "cmd": "add_account",
            "account": payload.account,
            "access_token": payload.access_token,
            "cookie_header": payload.cookie_header,
            "filters": filters,
        }
    )
    return {"ok": True, "account": payload.account}


@router.delete("/account/{account_id}")
async def remove_account(account_id: str) -> dict[str, Any]:
    supervisor = _require_supervisor()
    await supervisor.send_cmd({"cmd": "remove_account", "account": account_id})
    await supervisor.remove_account_from_enabled(account_id)
    return {"ok": True, "account": account_id}


@router.post("/session")
async def push_session(payload: SessionPayload) -> dict[str, Any]:
    supervisor = _require_supervisor()
    session = AgentSession(
        access_token=payload.access_token,
        cookie_header=payload.cookie_header,
        did=payload.did,
    )
    await supervisor.cache_session(payload.account, session)
    await supervisor.send_cmd(
        {
            "cmd": "session",
            "account": payload.account,
            "access_token": payload.access_token,
            "cookie_header": payload.cookie_header,
        }
    )
    return {"ok": True, "account": payload.account}


@router.post("/filter")
async def set_filter(payload: FilterUpdate) -> dict[str, Any]:
    supervisor = _require_supervisor()
    await supervisor.send_cmd(
        {
            "cmd": "filter",
            "account": payload.account,
            "min_amount": payload.min_amount,
            "max_amount": payload.max_amount,
            "currencies": payload.currencies,
        }
    )
    return {"ok": True, "account": payload.account}


@router.post("/mode")
async def set_mode(payload: ModePayload) -> dict[str, Any]:
    supervisor = _require_supervisor()
    if payload.value not in ("running", "paused"):
        raise HTTPException(status_code=422, detail="value must be running|paused")
    cmd: dict[str, Any] = {"cmd": "mode", "value": payload.value}
    if payload.account:
        cmd["account"] = payload.account
        rt = supervisor.accounts.get(payload.account)
        if rt is not None:
            rt.mode = payload.value
    else:
        for rt in supervisor.accounts.values():
            rt.mode = payload.value
    await supervisor.send_cmd(cmd)
    return {"ok": True}


@router.get("/status")
async def agent_status() -> dict[str, Any]:
    supervisor = get_supervisor()
    if supervisor is None:
        return {"running": False, "accounts": []}
    return supervisor.status()
