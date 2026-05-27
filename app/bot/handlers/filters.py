from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.bot.access import ensure_allowed_callback, ensure_allowed_message
from app.bot.callbacks import callback_data, edit_text
from app.bot.ui import (
    amount_filter_keyboard,
    dashboard_keyboard,
    render_amount_filter_panel,
    render_dashboard,
)
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager


def build_filters_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
) -> Router:
    router = Router()

    @router.callback_query(F.data == "filters:amount")
    async def callback_amount_filter(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        snapshot = await runtime_manager.snapshot(callback.from_user.id)
        await edit_text(callback, render_amount_filter_panel(snapshot), amount_filter_keyboard())
        await callback.answer()

    @router.callback_query(F.data.startswith("filters:amount:set:"))
    async def callback_set_amount_preset(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        _, _, _, min_raw, max_raw = callback_data(callback).split(":")
        runtime = await runtime_manager.get_or_create(callback.from_user.id)
        async with runtime.action_lock:
            snapshot = await runtime_manager.set_amount_filter(
                callback.from_user.id,
                Decimal(min_raw),
                Decimal(max_raw),
            )
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot))
        await callback.answer("Amount filter updated")

    @router.message(F.text.regexp(r"^\s*\d+(?:[.,]\d+)?\s+\d+(?:[.,]\d+)?\s*$"))
    async def handle_amount_filter_text(message: Message) -> None:
        if not await ensure_allowed_message(message, access_service):
            return
        if message.text is None or message.from_user is None:
            return
        try:
            min_amount, max_amount = parse_amount_range(message.text)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        runtime = await runtime_manager.get_or_create(message.from_user.id)
        async with runtime.action_lock:
            snapshot = await runtime_manager.set_amount_filter(message.from_user.id, min_amount, max_amount)
        await message.answer(render_dashboard(snapshot), reply_markup=dashboard_keyboard(snapshot))

    return router


def parse_amount_range(text: str) -> tuple[Decimal, Decimal]:
    parts = text.replace(",", ".").split()
    if len(parts) != 2:
        raise ValueError("Use amount range format: 100 500")
    try:
        min_amount = Decimal(parts[0])
        max_amount = Decimal(parts[1])
    except InvalidOperation as exc:
        raise ValueError("Cannot parse amount values") from exc
    if min_amount < Decimal("0"):
        raise ValueError("Minimum amount cannot be negative")
    if max_amount < min_amount:
        raise ValueError("Maximum amount cannot be less than minimum amount")
    return min_amount, max_amount
