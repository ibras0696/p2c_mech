from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.access import ensure_allowed_callback
from app.bot.callbacks import callback_data, edit_text
from app.bot.ui import dashboard_keyboard, limit_keyboard, render_dashboard, render_limit_panel
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager


def build_limits_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
) -> Router:
    router = Router()

    @router.callback_query(F.data == "limit:menu")
    async def callback_limit_menu(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        snapshot = await runtime_manager.snapshot(callback.from_user.id)
        await edit_text(
            callback,
            render_limit_panel(snapshot.active_limit),
            limit_keyboard(snapshot.active_limit),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("limit:set:"))
    async def callback_set_limit(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        limit = int(callback_data(callback).rsplit(":", 1)[-1])
        runtime = await runtime_manager.get_or_create(callback.from_user.id)
        async with runtime.action_lock:
            snapshot = await runtime_manager.set_limit(callback.from_user.id, limit)
        is_owner = await access_service.is_owner(callback.from_user.id)
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot, is_owner=is_owner))
        await callback.answer(f"Limit set: {snapshot.active_limit}")

    return router
