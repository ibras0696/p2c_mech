from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.access import ensure_allowed_callback
from app.bot.callbacks import edit_text
from app.bot.session_state import PlatformSession
from app.bot.ui import dashboard_keyboard, render_dashboard
from app.core.logging import get_logger
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager

SESSION_MAX_AGE = timedelta(minutes=30)
logger = get_logger(__name__)


def build_actions_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
) -> Router:
    router = Router()

    @router.callback_query(F.data == "agent:run")
    async def callback_run(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        started_at = datetime.now(UTC)
        duplicate = await runtime_manager.is_callback_duplicate(
            user_id=user_id,
            message_id=callback.message.message_id if callback.message else 0,
            callback_data=callback.data or "",
        )
        if duplicate:
            logger.info("event=agent_run_duplicate user_id=%s", user_id)
            await callback.answer()
            return
        runtime = await runtime_manager.get_or_create(user_id)
        async with runtime.action_lock:
            session = await runtime.session_repository.current()
            validation_error = validate_session_for_run(session)
            if validation_error is not None:
                logger.info(
                    "event=agent_run_blocked user_id=%s reason=%s",
                    user_id,
                    validation_error,
                )
                await callback.answer(validation_error, show_alert=True)
                return
            session = await refresh_session_cache_for_run(session=session, runtime=runtime)
            runtime.live_agent.set_session_hint(session)
            try:
                await runtime.live_agent.prewarm_take_channels(session)
            except Exception as exc:
                logger.warning(
                    "event=agent_run_prewarm_failed user_id=%s error=%s",
                    user_id,
                    type(exc).__name__,
                )
                await callback.answer(str(exc), show_alert=True)
                return
            runtime.live_agent.on_run()
            snapshot = await runtime_manager.run(user_id)
        latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        logger.info(
            "event=agent_run_applied user_id=%s latency_ms=%d mode=%s active_count=%d",
            user_id,
            latency_ms,
            snapshot.mode.value,
            snapshot.active_count,
        )
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot))
        await callback.answer("Agent is running")

    @router.callback_query(F.data == "agent:pause")
    async def callback_pause(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        started_at = datetime.now(UTC)
        duplicate = await runtime_manager.is_callback_duplicate(
            user_id=user_id,
            message_id=callback.message.message_id if callback.message else 0,
            callback_data=callback.data or "",
        )
        if duplicate:
            logger.info("event=agent_pause_duplicate user_id=%s", user_id)
            await callback.answer()
            return
        runtime = await runtime_manager.get_or_create(user_id)
        async with runtime.action_lock:
            snapshot = await runtime_manager.pause(user_id)
        latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        logger.info(
            "event=agent_pause_applied user_id=%s latency_ms=%d mode=%s active_count=%d",
            user_id,
            latency_ms,
            snapshot.mode.value,
            snapshot.active_count,
        )
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot))
        await callback.answer("Agent is paused")

    return router


def validate_session_for_run(session: PlatformSession | None) -> str | None:
    if session is None:
        return "Update session first in the Session section."
    if not session.access_token.strip():
        return "Session has no access_token. Send socket cURL again."
    if not session.cf_bm.strip():
        return "Session has no __cf_bm. Send socket cURL again."
    if datetime.now(UTC) - session.updated_at > SESSION_MAX_AGE:
        return "Session is stale. Send socket cURL again."
    return None


async def refresh_session_cache_for_run(
    *,
    runtime=None,
    user_id: int | None = None,
    platform_session_repository=None,
    session: PlatformSession,
) -> PlatformSession:
    updated = PlatformSession(
        access_token=session.access_token,
        cf_bm=session.cf_bm,
        updated_at=datetime.now(UTC),
    )
    try:
        if runtime is not None:
            await runtime.session_repository.save(updated)
            return updated
        if platform_session_repository is None:
            return updated
        if user_id is not None and hasattr(platform_session_repository, "save_for_user"):
            await platform_session_repository.save_for_user(user_id, updated)
            return updated
        await platform_session_repository.save(updated)
    except Exception:
        return updated
    return updated
