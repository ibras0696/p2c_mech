from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.bot.access import ensure_allowed_callback, ensure_allowed_message
from app.bot.agent_client import AgentClient, AgentClientError
from app.bot.callbacks import callback_data, edit_text
from app.bot.session_state import default_account_id
from app.bot.state import AgentSnapshot
from app.bot.ui import (
    amount_filter_keyboard,
    dashboard_keyboard,
    render_amount_filter_panel,
    render_dashboard,
)
from app.core.logging import get_logger
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager

logger = get_logger(__name__)


def build_filters_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
    agent_client: AgentClient,
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
        remote_note = await _push_filter_remote(agent_client, callback.from_user.id, snapshot)
        is_owner = await access_service.is_owner(callback.from_user.id)
        await edit_text(callback, render_dashboard(snapshot), dashboard_keyboard(snapshot, is_owner=is_owner))
        await callback.answer(remote_note or "Фильтр суммы обновлен", show_alert=bool(remote_note))

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
        remote_note = await _push_filter_remote(agent_client, message.from_user.id, snapshot)
        is_owner = await access_service.is_owner(message.from_user.id)
        text = render_dashboard(snapshot)
        if remote_note:
            text = f"{text}\n\n{remote_note}"
        await message.answer(text, reply_markup=dashboard_keyboard(snapshot, is_owner=is_owner))

    return router


async def _push_filter_remote(
    agent_client: AgentClient,
    user_id: int,
    snapshot: AgentSnapshot,
) -> str | None:
    """POST /agent/filter for the user's account (§7); returns a warning on failure."""
    account = default_account_id(user_id)
    try:
        await agent_client.set_filter(
            account=account,
            min_amount=int(snapshot.min_amount),
            max_amount=int(snapshot.max_amount),
        )
    except AgentClientError as exc:
        logger.warning("event=filter_push_remote_failed user_id=%s error=%s", user_id, exc.message)
        return f"⚠️ Фильтр сохранён локально, но снайпер не обновлён: {exc.message}"
    return None


def parse_amount_range(text: str) -> tuple[Decimal, Decimal]:
    parts = text.replace(",", ".").split()
    if len(parts) != 2:
        raise ValueError("Используйте формат диапазона: 100 500")
    try:
        min_amount = Decimal(parts[0])
        max_amount = Decimal(parts[1])
    except InvalidOperation as exc:
        raise ValueError("Не удалось прочитать значения суммы") from exc
    if min_amount < Decimal("0"):
        raise ValueError("Минимальная сумма не может быть отрицательной")
    if max_amount < min_amount:
        raise ValueError("Максимум не может быть меньше минимума")
    return min_amount, max_amount
