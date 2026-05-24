from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.access import is_allowed_user, reject_callback
from app.bot.callbacks import callback_data, edit_text
from app.bot.preferences import apply_user_preferences, persist_current_preferences
from app.bot.state import agent_state
from app.bot.ui import dashboard_keyboard, limit_keyboard, render_dashboard, render_limit_panel
from app.repositories.agent_preferences import AgentPreferencesRepository


def build_limits_router(
    allowed_user_ids: set[int],
    preferences_repository: AgentPreferencesRepository,
) -> Router:
    router = Router()

    @router.callback_query(F.data == "limit:menu")
    async def callback_limit_menu(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        await apply_user_preferences(callback.from_user.id, preferences_repository)
        snapshot = agent_state.snapshot()
        await edit_text(
            callback,
            render_limit_panel(snapshot.active_limit),
            limit_keyboard(snapshot.active_limit),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("limit:set:"))
    async def callback_set_limit(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        limit = int(callback_data(callback).rsplit(":", 1)[-1])
        snapshot = agent_state.set_limit(limit)
        await persist_current_preferences(callback.from_user.id, preferences_repository)
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot))
        await callback.answer(f"Лимит установлен: {snapshot.active_limit}")

    return router
