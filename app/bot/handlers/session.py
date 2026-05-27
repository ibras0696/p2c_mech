from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.access import ensure_allowed_callback, ensure_allowed_message
from app.bot.callbacks import edit_text
from app.bot.session_state import PlatformSession, parse_platform_session_from_text
from app.bot.ui import session_keyboard
from app.bot.ui.session import render_session_help, render_session_status
from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.platform_ws.p2c_socket import P2CSocketClient, P2CSocketConfig
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager

logger = get_logger(__name__)


def build_session_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
) -> Router:
    router = Router()

    @router.message(Command("session"))
    async def handle_session(message: Message) -> None:
        if not await ensure_allowed_message(message, access_service):
            return
        user_id = message.from_user.id if message.from_user else 0
        logger.info("event=session_help_opened user_id=%s", user_id)
        await message.answer(render_session_help(), reply_markup=session_keyboard())

    @router.message(F.text.contains("access_token=") | F.text.contains("__cf_bm="))
    async def handle_session_text(message: Message) -> None:
        if not await ensure_allowed_message(message, access_service):
            return
        if message.text is None or message.from_user is None:
            return
        user_id = message.from_user.id
        try:
            session = parse_platform_session_from_text(message.text)
        except ValueError:
            await message.answer("Не найден access_token или __cf_bm в сообщении.")
            return
        await _delete_secret_message(message)
        runtime = await runtime_manager.get_or_create(user_id)
        try:
            session = await runtime.session_repository.save(session)
        except Exception as exc:
            await message.answer(_format_storage_error(exc))
            logger.warning("event=session_save_failed user_id=%s error=%s", user_id, type(exc).__name__)
            return
        logger.info("event=session_saved user_id=%s", user_id)
        await message.answer(render_session_status(session), reply_markup=session_keyboard())

    @router.callback_query(F.data == "session:status")
    async def callback_session_status(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        runtime = await runtime_manager.get_or_create(user_id)
        try:
            session = await runtime.session_repository.current()
        except Exception as exc:
            await callback.answer(_format_storage_error(exc), show_alert=True)
            logger.warning("event=session_status_failed user_id=%s error=%s", user_id, type(exc).__name__)
            return
        if session is None:
            await edit_text(callback, render_session_help(), session_keyboard())
            await callback.answer("Сессия не сохранена")
            logger.info("event=session_status_empty user_id=%s", user_id)
            return
        await edit_text(callback, render_session_status(session), session_keyboard())
        access_state = "ok" if session.access_token.strip() else "нет access_token"
        cf_state = "ok" if session.cf_bm.strip() else "нет __cf_bm"
        await callback.answer(f"Сессия: {access_state}, {cf_state}")
        logger.info(
            "event=session_status user_id=%s has_access=%s has_cf=%s",
            user_id,
            bool(session.access_token.strip()),
            bool(session.cf_bm.strip()),
        )

    @router.callback_query(F.data == "session:help")
    async def callback_session_help(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        await edit_text(callback, render_session_help(), session_keyboard())
        await callback.answer("Инструкция обновлена")
        logger.info("event=session_help_refreshed user_id=%s", user_id)

    @router.callback_query(F.data == "session:probe_socket")
    async def callback_probe_socket(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        started_at = datetime.now(UTC)
        runtime = await runtime_manager.get_or_create(user_id)
        try:
            session = await runtime.session_repository.current()
        except Exception as exc:
            await callback.answer(_format_storage_error(exc), show_alert=True)
            logger.warning("event=session_probe_failed user_id=%s error=%s", user_id, type(exc).__name__)
            return
        if session is None or not session.access_token.strip():
            await callback.answer("Сначала пришлите socket cURL с access_token", show_alert=True)
            logger.info("event=session_probe_blocked user_id=%s reason=no_access_token", user_id)
            return
        settings = get_settings()
        client = P2CSocketClient(
            P2CSocketConfig(
                url=settings.platform_ws_url,
                cookie_header=session.cookie_header,
                force_ipv4=settings.platform_force_ipv4,
            )
        )
        try:
            await client.probe_once()
        except Exception as exc:
            logger.warning("event=session_probe_failed user_id=%s error=%s", user_id, type(exc).__name__)
            await callback.answer(f"Тест сокета не прошел: {type(exc).__name__}", show_alert=True)
            return

        refreshed = PlatformSession(
            access_token=session.access_token,
            cf_bm=session.cf_bm,
            updated_at=datetime.now(UTC),
        )
        try:
            await runtime.session_repository.save(refreshed)
        except Exception as exc:
            await callback.answer(_format_storage_error(exc), show_alert=True)
            logger.warning("event=session_probe_save_failed user_id=%s error=%s", user_id, type(exc).__name__)
            return
        latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        logger.info("event=session_probe_succeeded user_id=%s latency_ms=%d", user_id, latency_ms)
        await callback.answer("Сокет подключился, сессия обновлена.", show_alert=True)

    return router


async def _delete_secret_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        return


def _format_storage_error(exc: Exception) -> str:
    exc_name = type(exc).__name__
    if isinstance(exc, ConnectionRefusedError):
        return (
            "PostgreSQL недоступен. Запустите `docker compose up -d postgres redis` "
            "или проверьте DATABASE_URL."
        )
    return f"Хранилище сессии недоступно: {exc_name}"
