from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.access import ensure_allowed_callback
from app.bot.callbacks import callback_data, delete_message_safely, edit_text
from app.bot.ui import (
    orders_keyboard,
    payment_confirm_keyboard,
    render_dashboard,
    render_payment_confirmation,
)
from app.core.logging import get_logger
from app.integrations.platform_api import P2CPaymentsError
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager

logger = get_logger(__name__)


def _is_order_closed_error(message: str) -> bool:
    return (
        "Order is not active" in message
        or "status 404" in message
        or "404 Not Found" in message
        or "InvalidStatus" in message
        or "status is not completable" in message
    )


def build_orders_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
) -> Router:
    router = Router()

    @router.callback_query(F.data == "orders:list")
    async def callback_orders(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        snapshot = await runtime_manager.snapshot(user_id)
        logger.info(
            "event=orders_list_opened user_id=%s active_count=%d",
            user_id,
            snapshot.active_count,
        )
        await edit_text(callback, render_dashboard(snapshot), orders_keyboard(snapshot))
        await callback.answer()

    @router.callback_query(F.data.startswith("order:paid:"))
    async def callback_paid(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        order_id = callback_data(callback).rsplit(":", 1)[-1]
        started_at = datetime.now(UTC)
        runtime = await runtime_manager.get_or_create(user_id)
        async with runtime.action_lock:
            try:
                await runtime.live_agent.complete_order(order_id)
            except P2CPaymentsError as exc:
                message = str(exc)
                latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
                if _is_order_closed_error(message):
                    logger.info(
                        "event=order_paid_already_closed user_id=%s payment_id=%s source_order_id=%s latency_ms=%d",
                        user_id,
                        order_id,
                        "",
                        latency_ms,
                    )
                    await callback.answer("Order is already closed or expired")
                    await delete_message_safely(callback)
                    return
                logger.warning(
                    "event=order_paid_failed user_id=%s payment_id=%s source_order_id=%s latency_ms=%d error=%s",
                    user_id,
                    order_id,
                    "",
                    latency_ms,
                    message,
                )
                await callback.answer(f"Failed to complete order: {message}", show_alert=True)
                return
        latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        logger.info(
            "event=order_paid_succeeded user_id=%s payment_id=%s source_order_id=%s latency_ms=%d",
            user_id,
            order_id,
            "",
            latency_ms,
        )
        await callback.answer("Payment confirmed")
        await delete_message_safely(callback)

    @router.callback_query(F.data.startswith("order:cancel:"))
    async def callback_cancel(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        order_id = callback_data(callback).rsplit(":", 1)[-1]
        started_at = datetime.now(UTC)
        runtime = await runtime_manager.get_or_create(user_id)
        async with runtime.action_lock:
            try:
                await runtime.live_agent.cancel_order(order_id)
            except P2CPaymentsError as exc:
                message = str(exc)
                latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
                if _is_order_closed_error(message):
                    logger.info(
                        "event=order_cancel_already_closed user_id=%s payment_id=%s source_order_id=%s latency_ms=%d",
                        user_id,
                        order_id,
                        "",
                        latency_ms,
                    )
                    await callback.answer("Order is already closed or expired")
                    await delete_message_safely(callback)
                    return
                logger.warning(
                    "event=order_cancel_failed user_id=%s payment_id=%s source_order_id=%s latency_ms=%d error=%s",
                    user_id,
                    order_id,
                    "",
                    latency_ms,
                    message,
                )
                await callback.answer(f"Cancel failed: {message}", show_alert=True)
                return
        latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        logger.info(
            "event=order_cancel_succeeded user_id=%s payment_id=%s source_order_id=%s latency_ms=%d",
            user_id,
            order_id,
            "",
            latency_ms,
        )
        await callback.answer("Order canceled")
        await delete_message_safely(callback)

    @router.callback_query(F.data.startswith("order:confirm:"))
    async def callback_confirm_paid(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        order_id = callback_data(callback).rsplit(":", 1)[-1]
        snapshot = await runtime_manager.snapshot(user_id)
        order = next((item for item in snapshot.active_orders if item.id == order_id), None)
        if order is None:
            await callback.answer("Order is no longer active", show_alert=True)
            return
        logger.info(
            "event=order_confirm_opened user_id=%s payment_id=%s source_order_id=%s latency_ms=%d",
            user_id,
            order.id,
            order.source_order_id,
            0,
        )
        await edit_text(
            callback,
            render_payment_confirmation(order),
            payment_confirm_keyboard(order.id),
        )
        await callback.answer()

    return router
