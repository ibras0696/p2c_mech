from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.access import is_allowed_user, reject_callback
from app.bot.callbacks import callback_data, delete_message_safely, edit_text
from app.bot.state import agent_state
from app.bot.ui import (
    orders_keyboard,
    payment_confirm_keyboard,
    render_dashboard,
    render_payment_confirmation,
)
from app.integrations.platform_api import P2CPaymentsError
from app.services.p2c_live_agent import P2CLiveAgent


def build_orders_router(allowed_user_ids: set[int], live_agent: P2CLiveAgent) -> Router:
    router = Router()

    @router.callback_query(F.data == "orders:list")
    async def callback_orders(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        snapshot = agent_state.snapshot()
        await edit_text(callback, render_dashboard(snapshot), orders_keyboard(snapshot))
        await callback.answer()

    @router.callback_query(F.data.startswith("order:paid:"))
    async def callback_paid(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        order_id = callback_data(callback).rsplit(":", 1)[-1]
        try:
            await live_agent.complete_order(order_id)
        except P2CPaymentsError as exc:
            message = str(exc)
            if "Order is not active" in message:
                await callback.answer("Заявка уже закрыта или не активна")
                await delete_message_safely(callback)
                return
            await callback.answer(f"Не удалось завершить заявку: {message}", show_alert=True)
            return

        await callback.answer("Оплата подтверждена")
        await delete_message_safely(callback)

    @router.callback_query(F.data.startswith("order:cancel:"))
    async def callback_cancel(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        order_id = callback_data(callback).rsplit(":", 1)[-1]
        try:
            await live_agent.cancel_order(order_id)
        except P2CPaymentsError as exc:
            await callback.answer(f"Отмена недоступна: {exc}", show_alert=True)
            return

        await callback.answer("Заявка отменена")
        await delete_message_safely(callback)

    @router.callback_query(F.data.startswith("order:confirm:"))
    async def callback_confirm_paid(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        order_id = callback_data(callback).rsplit(":", 1)[-1]
        order = agent_state.get_active_order(order_id)
        if order is None:
            await callback.answer("Заявка уже не активна", show_alert=True)
            return
        await edit_text(
            callback,
            render_payment_confirmation(order),
            payment_confirm_keyboard(order.id),
        )
        await callback.answer()

    return router

