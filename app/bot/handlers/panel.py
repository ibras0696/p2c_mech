from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.access import is_allowed_user, reject_callback, reject_message
from app.bot.callbacks import edit_text
from app.bot.preferences import apply_user_preferences
from app.bot.state import agent_state
from app.bot.ui import dashboard_keyboard, render_dashboard, render_help
from app.repositories.agent_preferences import AgentPreferencesRepository


def build_panel_router(
    allowed_user_ids: set[int],
    preferences_repository: AgentPreferencesRepository,
) -> Router:
    router = Router()

    @router.message(Command("start"))
    @router.message(Command("panel"))
    async def handle_panel(message: Message) -> None:
        if not is_allowed_user(message.from_user.id if message.from_user else None, allowed_user_ids):
            await reject_message(message)
            return
        if message.from_user is not None:
            await apply_user_preferences(message.from_user.id, preferences_repository)
        snapshot = agent_state.snapshot()
        await message.answer(render_dashboard(snapshot), reply_markup=dashboard_keyboard(snapshot))

    @router.callback_query(F.data == "panel:refresh")
    async def callback_refresh(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        await apply_user_preferences(callback.from_user.id, preferences_repository)
        snapshot = agent_state.snapshot()
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot))
        await callback.answer()

    @router.callback_query(F.data == "panel:help")
    async def callback_help(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        await apply_user_preferences(callback.from_user.id, preferences_repository)
        await edit_text(callback, render_help(), dashboard_keyboard(agent_state.snapshot()))
        await callback.answer()

    return router
