from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.access import ensure_allowed_callback, ensure_allowed_message
from app.bot.callbacks import edit_text
from app.bot.ui import dashboard_keyboard, render_dashboard, render_help
from app.core.logging import get_logger
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager

logger = get_logger(__name__)


def build_panel_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
) -> Router:
    router = Router()

    @router.message(Command("start"))
    @router.message(Command("panel"))
    async def handle_panel(message: Message) -> None:
        if not await ensure_allowed_message(message, access_service):
            return
        user_id = message.from_user.id if message.from_user else 0
        snapshot = await runtime_manager.snapshot(user_id)
        logger.info("event=panel_opened user_id=%s mode=%s active_count=%d", user_id, snapshot.mode.value, snapshot.active_count)
        await message.answer(render_dashboard(snapshot), reply_markup=dashboard_keyboard(snapshot))

    @router.callback_query(F.data == "panel:refresh")
    async def callback_refresh(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        duplicate = await runtime_manager.is_callback_duplicate(
            user_id=user_id,
            message_id=callback.message.message_id if callback.message else 0,
            callback_data=callback.data or "",
        )
        if duplicate:
            logger.info("event=panel_refresh_duplicate user_id=%s", user_id)
            await callback.answer()
            return
        snapshot = await runtime_manager.snapshot(user_id)
        logger.info("event=panel_refreshed user_id=%s mode=%s active_count=%d", user_id, snapshot.mode.value, snapshot.active_count)
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot))
        await callback.answer()

    @router.callback_query(F.data == "panel:help")
    async def callback_help(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        snapshot = await runtime_manager.snapshot(user_id)
        logger.info("event=panel_help_opened user_id=%s", user_id)
        await edit_text(callback, render_help(), dashboard_keyboard(snapshot))
        await callback.answer()

    return router

