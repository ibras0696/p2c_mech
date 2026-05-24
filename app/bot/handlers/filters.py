from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.bot.access import is_allowed_user, reject_callback, reject_message
from app.bot.callbacks import callback_data, edit_text
from app.bot.preferences import apply_user_preferences, persist_current_preferences
from app.bot.state import agent_state
from app.bot.ui import (
    amount_filter_keyboard,
    dashboard_keyboard,
    render_amount_filter_panel,
    render_dashboard,
)
from app.repositories.agent_preferences import AgentPreferencesRepository


def build_filters_router(
    allowed_user_ids: set[int],
    preferences_repository: AgentPreferencesRepository,
) -> Router:
    router = Router()

    @router.callback_query(F.data == "filters:amount")
    async def callback_amount_filter(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        await apply_user_preferences(callback.from_user.id, preferences_repository)
        snapshot = agent_state.snapshot()
        await edit_text(callback, render_amount_filter_panel(snapshot), amount_filter_keyboard())
        await callback.answer()

    @router.callback_query(F.data.startswith("filters:amount:set:"))
    async def callback_set_amount_preset(callback: CallbackQuery) -> None:
        if not is_allowed_user(callback.from_user.id, allowed_user_ids):
            await reject_callback(callback)
            return
        _, _, _, min_raw, max_raw = callback_data(callback).split(":")
        snapshot = agent_state.set_amount_filter(Decimal(min_raw), Decimal(max_raw))
        await persist_current_preferences(callback.from_user.id, preferences_repository)
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot))
        await callback.answer("Фильтр суммы обновлен")

    @router.message(F.text.regexp(r"^\s*\d+(?:[.,]\d+)?\s+\d+(?:[.,]\d+)?\s*$"))
    async def handle_amount_filter_text(message: Message) -> None:
        if not is_allowed_user(message.from_user.id if message.from_user else None, allowed_user_ids):
            await reject_message(message)
            return
        if message.text is None or message.from_user is None:
            return
        try:
            min_amount, max_amount = parse_amount_range(message.text)
            snapshot = agent_state.set_amount_filter(min_amount, max_amount)
            await persist_current_preferences(message.from_user.id, preferences_repository)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(render_dashboard(snapshot), reply_markup=dashboard_keyboard(snapshot))

    return router


def parse_amount_range(text: str) -> tuple[Decimal, Decimal]:
    parts = text.replace(",", ".").split()
    if len(parts) != 2:
        raise ValueError("Отправьте диапазон в формате: 100 500")
    try:
        min_amount = Decimal(parts[0])
        max_amount = Decimal(parts[1])
    except InvalidOperation as exc:
        raise ValueError("Не удалось прочитать суммы") from exc
    if min_amount < Decimal("0"):
        raise ValueError("Минимальная сумма не может быть отрицательной")
    if max_amount < min_amount:
        raise ValueError("Максимум не может быть меньше минимума")
    return min_amount, max_amount
