from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.access import is_allowed_user, reject_callback
from app.bot.callbacks import edit_text
from app.bot.preferences import apply_user_preferences
from app.bot.session_state import PlatformSession
from app.bot.state import agent_state
from app.bot.ui import dashboard_keyboard, render_dashboard
from app.core.logging import get_logger
from app.repositories.agent_preferences import AgentPreferencesRepository
from app.repositories.platform_session import PlatformSessionRepository
from app.services.p2c_live_agent import P2CLiveAgent

SESSION_MAX_AGE = timedelta(minutes=30)
logger = get_logger(__name__)


def build_actions_router(
    allowed_user_ids: set[int],
    platform_session_repository: PlatformSessionRepository,
    preferences_repository: AgentPreferencesRepository,
    live_agent: P2CLiveAgent,
) -> Router:
    router = Router()

    @router.callback_query(F.data == "agent:run")
    async def callback_run(callback: CallbackQuery) -> None:
        logger.info("bot_action_run_clicked user_id=%s", callback.from_user.id)
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        await apply_user_preferences(callback.from_user.id, preferences_repository)
        try:
            session = await platform_session_repository.current()
        except Exception as exc:
            await callback.answer(f"Сессия недоступна: {type(exc).__name__}", show_alert=True)
            return
        validation_error = validate_session_for_run(session)
        if validation_error is not None:
            logger.info("bot_action_run_blocked user_id=%s reason=%s", callback.from_user.id, validation_error)
            await callback.answer(validation_error, show_alert=True)
            return
        await refresh_session_cache_for_run(
            user_id=callback.from_user.id,
            platform_session_repository=platform_session_repository,
            session=session,
        )
        live_agent.set_session_hint(session)
        live_agent.on_run()
        snapshot = agent_state.run()
        logger.info(
            "bot_action_run_applied user_id=%s mode=%s active_count=%d free_slots=%d",
            callback.from_user.id,
            snapshot.mode.value,
            snapshot.active_count,
            snapshot.free_slots,
        )
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot))
        await callback.answer("Агент запущен")

    @router.callback_query(F.data == "agent:pause")
    async def callback_pause(callback: CallbackQuery) -> None:
        logger.info("bot_action_pause_clicked user_id=%s", callback.from_user.id)
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        live_agent.on_pause()
        snapshot = agent_state.pause()
        logger.info(
            "bot_action_pause_applied user_id=%s mode=%s active_count=%d free_slots=%d",
            callback.from_user.id,
            snapshot.mode.value,
            snapshot.active_count,
            snapshot.free_slots,
        )
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot))
        await callback.answer("Агент на паузе")

    return router


def validate_session_for_run(session: PlatformSession | None) -> str | None:
    if session is None:
        return "Сначала обнови сессию в разделе 🔐 Сессия"
    if not session.access_token.strip():
        return "В сессии нет access_token. Пришли socket cURL заново"
    if not session.cf_bm.strip():
        return "В сессии нет __cf_bm. Пришли socket cURL заново"
    if datetime.now(UTC) - session.updated_at > SESSION_MAX_AGE:
        return "Сессия устарела. Пришли socket cURL заново."
    return None


async def refresh_session_cache_for_run(
    *,
    user_id: int,
    platform_session_repository: PlatformSessionRepository,
    session: PlatformSession,
) -> None:
    try:
        await platform_session_repository.save(session)
    except Exception as exc:
        logger.warning(
            "bot_action_run_session_cache_refresh_failed user_id=%s error=%s",
            user_id,
            type(exc).__name__,
        )
        return
    logger.info("bot_action_run_session_cache_refreshed user_id=%s", user_id)
