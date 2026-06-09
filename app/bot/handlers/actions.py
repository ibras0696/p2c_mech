from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.access import ensure_allowed_callback
from app.bot.agent_client import AgentClient, AgentClientError, FilterPayload
from app.bot.callbacks import edit_text
from app.bot.session_state import PlatformSession, default_account_id
from app.bot.state import AgentSnapshot
from app.bot.ui import dashboard_keyboard, render_dashboard
from app.core.logging import get_logger
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager

SESSION_MAX_AGE = timedelta(minutes=30)
logger = get_logger(__name__)


def build_actions_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
    agent_client: AgentClient,
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
        # Thin HTTP client (§7): start the supervisor, then push this account's
        # session + filters so the C-agent spins up a socket for it.
        remote_warning = await _start_remote_agent(
            agent_client=agent_client,
            user_id=user_id,
            session=session,
            snapshot=snapshot,
        )
        latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        logger.info(
            "event=agent_run_applied user_id=%s latency_ms=%d mode=%s active_count=%d",
            user_id,
            latency_ms,
            snapshot.mode.value,
            snapshot.active_count,
        )
        is_owner = await access_service.is_owner(user_id)
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot, is_owner=is_owner))
        await callback.answer(remote_warning or "Агент запущен", show_alert=bool(remote_warning))

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
        remote_warning: str | None = None
        try:
            # Pause via mode, NOT shutdown: keep the C-agent process, its WS and
            # the warm take connection alive so resuming is instant (no cold
            # TLS handshake). Only taking is suspended.
            await agent_client.set_mode(value="paused", account=default_account_id(user_id))
        except AgentClientError as exc:
            remote_warning = exc.message
            logger.warning("event=agent_pause_remote_failed user_id=%s error=%s", user_id, exc.message)
        latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        logger.info(
            "event=agent_pause_applied user_id=%s latency_ms=%d mode=%s active_count=%d",
            user_id,
            latency_ms,
            snapshot.mode.value,
            snapshot.active_count,
        )
        is_owner = await access_service.is_owner(user_id)
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot, is_owner=is_owner))
        await callback.answer(remote_warning or "Агент на паузе", show_alert=bool(remote_warning))

    return router


async def _start_remote_agent(
    *,
    agent_client: AgentClient,
    user_id: int,
    session: PlatformSession,
    snapshot: AgentSnapshot,
) -> str | None:
    """POST /agent/start then /agent/account for this user's account (§7).

    Returns a user-facing warning string on failure, or None on success.
    The local runtime has already been started, so a remote failure is
    surfaced as a non-fatal warning instead of crashing the bot.
    """
    # When the Python live agent owns the hot path (socket + take), do NOT also
    # spin up the C agent — two sockets on one account race each other and trip
    # Cloudflare. The Python agent is started by the runtime manager separately.
    from app.core.config import get_settings

    if get_settings().platform_python_socket_enabled:
        logger.info(
            "event=agent_start_remote_skipped user_id=%s reason=python_socket_authority",
            user_id,
        )
        return None

    account = default_account_id(user_id)
    filters = FilterPayload(
        min_amount=int(snapshot.min_amount),
        max_amount=int(snapshot.max_amount),
        currencies=[],
    )
    try:
        await agent_client.start()
        await agent_client.add_account(
            account=account,
            access_token=session.access_token,
            cookie_header=session.cookie_header,
            label=account,
            filters=filters,
        )
        # Re-adding an existing (paused) account does not reset its mode, so
        # explicitly resume taking. Harmless on a fresh add (already running).
        await agent_client.set_mode(value="running", account=account)
    except AgentClientError as exc:
        logger.warning(
            "event=agent_start_remote_failed user_id=%s account=%s error=%s",
            user_id,
            account,
            exc.message,
        )
        return exc.message
    logger.info("event=agent_start_remote_applied user_id=%s account=%s", user_id, account)
    return None


def validate_session_for_run(session: PlatformSession | None) -> str | None:
    if session is None:
        return "Сначала обновите сессию в разделе «Сессия»."
    if not session.access_token.strip():
        return "В сессии нет access_token. Пришлите socket cURL заново."
    if not session.cf_bm.strip():
        return "В сессии нет __cf_bm. Пришлите socket cURL заново."
    if datetime.now(UTC) - session.updated_at > SESSION_MAX_AGE:
        return "Сессия устарела. Пришлите socket cURL заново."
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
