from __future__ import annotations

from aiogram import Router

from app.bot.agent_client import AgentClient
from app.bot.handlers.actions import build_actions_router
from app.bot.handlers.admin import build_admin_router
from app.bot.handlers.agent_remote import build_agent_remote_router
from app.bot.handlers.filters import build_filters_router
from app.bot.handlers.limits import build_limits_router
from app.bot.handlers.orders import build_orders_router
from app.bot.handlers.panel import build_panel_router
from app.bot.handlers.session import build_session_router
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager


def build_router(
    *,
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
    agent_client: AgentClient,
) -> Router:
    router = Router()
    router.include_router(build_admin_router(access_service, runtime_manager))
    router.include_router(build_panel_router(access_service, runtime_manager))
    router.include_router(
        build_actions_router(access_service, runtime_manager, agent_client)
    )
    router.include_router(build_orders_router(access_service, runtime_manager))
    router.include_router(build_limits_router(access_service, runtime_manager))
    router.include_router(
        build_filters_router(access_service, runtime_manager, agent_client)
    )
    router.include_router(
        build_session_router(access_service, runtime_manager, agent_client)
    )
    router.include_router(
        build_agent_remote_router(access_service, runtime_manager, agent_client)
    )
    return router
