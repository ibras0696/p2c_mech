from __future__ import annotations

from aiogram import Router

from app.bot.access import parse_admin_ids
from app.bot.handlers.actions import build_actions_router
from app.bot.handlers.filters import build_filters_router
from app.bot.handlers.limits import build_limits_router
from app.bot.handlers.orders import build_orders_router
from app.bot.handlers.panel import build_panel_router
from app.bot.handlers.session import build_session_router
from app.core.config import Settings
from app.repositories.agent_preferences import AgentPreferencesRepository
from app.repositories.platform_session import PlatformSessionRepository
from app.services.p2c_live_agent import P2CLiveAgent


def build_router(
    settings: Settings,
    *,
    agent_preferences_repository: AgentPreferencesRepository,
    platform_session_repository: PlatformSessionRepository,
    live_agent: P2CLiveAgent,
) -> Router:
    allowed_user_ids = parse_admin_ids(settings.telegram_admin_ids)
    router = Router()
    router.include_router(build_panel_router(allowed_user_ids, agent_preferences_repository))
    router.include_router(
        build_actions_router(
            allowed_user_ids,
            platform_session_repository,
            agent_preferences_repository,
            live_agent,
        )
    )
    router.include_router(build_orders_router(allowed_user_ids, live_agent))
    router.include_router(build_limits_router(allowed_user_ids, agent_preferences_repository))
    router.include_router(build_filters_router(allowed_user_ids, agent_preferences_repository))
    router.include_router(build_session_router(allowed_user_ids, platform_session_repository))
    return router
