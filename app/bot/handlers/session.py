from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.access import is_allowed_user, reject_callback, reject_message
from app.bot.callbacks import edit_text
from app.bot.session_state import PlatformSession, parse_platform_session_from_text
from app.bot.ui import session_keyboard
from app.bot.ui.session import render_session_help, render_session_status
from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.platform_ws.p2c_socket import P2CSocketClient, P2CSocketConfig
from app.repositories.platform_session import PlatformSessionRepository

logger = get_logger(__name__)


def build_session_router(
    allowed_user_ids: set[int],
    platform_session_repository: PlatformSessionRepository,
) -> Router:
    router = Router()

    @router.message(Command("session"))
    async def handle_session(message: Message) -> None:
        if not is_allowed_user(message.from_user.id if message.from_user else None, allowed_user_ids):
            await reject_message(message)
            return
        await message.answer(render_session_help(), reply_markup=session_keyboard())

    @router.message(F.text.contains("access_token=") | F.text.contains("__cf_bm="))
    async def handle_session_text(message: Message) -> None:
        if not is_allowed_user(message.from_user.id if message.from_user else None, allowed_user_ids):
            await reject_message(message)
            return
        if message.text is None:
            return
        try:
            session = parse_platform_session_from_text(message.text)
        except ValueError:
            await message.answer("Не нашел access_token или __cf_bm в сообщении.")
            return
        await _delete_secret_message(message)
        try:
            session = await platform_session_repository.save(session)
        except Exception as exc:
            await message.answer(_format_storage_error(exc))
            return
        logger.info(
            "bot_session_updated user_id=%s has_access_token=%s has_cf_bm=%s updated_at=%s",
            message.from_user.id if message.from_user else None,
            bool(session.access_token.strip()),
            bool(session.cf_bm.strip()),
            session.updated_at.isoformat(),
        )
        await message.answer(render_session_status(session), reply_markup=session_keyboard())

    @router.callback_query(F.data == "session:status")
    async def callback_session_status(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        try:
            session = await platform_session_repository.current()
        except Exception as exc:
            await callback.answer(_format_storage_error(exc), show_alert=True)
            return
        if session is None:
            await edit_text(callback, render_session_help(), session_keyboard())
            await callback.answer("Сессия не сохранена")
            return
        await edit_text(callback, render_session_status(session), session_keyboard())
        access_state = "ok" if session.access_token.strip() else "нет access_token"
        cf_state = "ok" if session.cf_bm.strip() else "нет __cf_bm"
        await callback.answer(f"Сессия: {access_state}, {cf_state}")

    @router.callback_query(F.data == "session:help")
    async def callback_session_help(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        await edit_text(callback, render_session_help(), session_keyboard())
        await callback.answer("Инструкция обновлена")

    @router.callback_query(F.data == "session:probe_socket")
    async def callback_probe_socket(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        try:
            session = await platform_session_repository.current()
        except Exception as exc:
            await callback.answer(_format_storage_error(exc), show_alert=True)
            return
        if session is None or not session.access_token.strip():
            await callback.answer("Сначала пришлите socket curl с access_token", show_alert=True)
            return
        settings = get_settings()
        client = P2CSocketClient(
            P2CSocketConfig(
                url=settings.platform_ws_url,
                cookie_header=session.cookie_header,
            )
        )
        try:
            await client.probe_once()
        except Exception as exc:
            logger.warning("bot_session_probe_failed user_id=%s error=%s", callback.from_user.id, type(exc).__name__)
            await callback.answer(f"Сокет не подключился: {type(exc).__name__}", show_alert=True)
            return

        refreshed = PlatformSession(
            access_token=session.access_token,
            cf_bm=session.cf_bm,
            updated_at=datetime.now(UTC),
        )
        try:
            await platform_session_repository.save(refreshed)
        except Exception as exc:
            await callback.answer(_format_storage_error(exc), show_alert=True)
            return

        logger.info("bot_session_probe_succeeded user_id=%s", callback.from_user.id)
        await callback.answer("Сокет подключился, сессия обновлена", show_alert=True)

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
            "PostgreSQL недоступен. Запусти docker compose up -d postgres redis "
            "или проверь DATABASE_URL."
        )
    return f"Хранилище сессии недоступно: {exc_name}"
