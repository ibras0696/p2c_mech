from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.access import ensure_allowed_callback, ensure_allowed_message
from app.bot.agent_client import AgentClient, AgentClientError
from app.bot.callbacks import edit_text
from app.bot.session_state import default_account_id
from app.bot.ui import render_agent_status, render_stats
from app.bot.ui.keyboards import stats_keyboard
from app.core.logging import get_logger
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager

logger = get_logger(__name__)


def build_agent_remote_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
    agent_client: AgentClient,
) -> Router:
    router = Router()

    async def _show_stats(target: Message | CallbackQuery, user_id: int) -> None:
        # Stats are fully decoupled from the agent (§6.2): we ONLY call GET /stats,
        # never /agent/status. So the button works even if the agent is stopped.
        account = default_account_id(user_id)
        try:
            payload = await agent_client.stats(account=account, period="today")
        except AgentClientError as exc:
            await _reply_error(target, exc.message)
            return
        text = render_stats(payload)
        await _reply(target, text, stats_keyboard())
        logger.info("event=stats_viewed user_id=%s account=%s", user_id, account)

    @router.message(Command("stats"))
    async def handle_stats_command(message: Message) -> None:
        if not await ensure_allowed_message(message, access_service):
            return
        user_id = message.from_user.id if message.from_user else 0
        await _show_stats(message, user_id)

    @router.callback_query(F.data == "stats:view")
    async def callback_stats(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        await _show_stats(callback, callback.from_user.id)
        await callback.answer()

    @router.message(Command("agent_status"))
    async def handle_status_command(message: Message) -> None:
        if not await ensure_allowed_message(message, access_service):
            return
        user_id = message.from_user.id if message.from_user else 0
        await _show_status(message, agent_client, user_id)

    @router.callback_query(F.data == "agent:status")
    async def callback_status(callback: CallbackQuery) -> None:
        if not await ensure_allowed_callback(callback, access_service):
            return
        await _show_status(callback, agent_client, callback.from_user.id)
        await callback.answer()

    return router


async def _show_status(
    target: Message | CallbackQuery,
    agent_client: AgentClient,
    user_id: int,
) -> None:
    try:
        payload = await agent_client.status()
    except AgentClientError as exc:
        await _reply_error(target, exc.message)
        return
    text = render_agent_status(payload)
    await _reply(target, text, stats_keyboard())
    logger.info("event=agent_status_viewed user_id=%s", user_id)


async def _reply(target: Message | CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> None:
    if isinstance(target, CallbackQuery):
        await edit_text(target, text, keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


async def _reply_error(target: Message | CallbackQuery, text: str) -> None:
    if isinstance(target, CallbackQuery):
        await target.answer(text, show_alert=True)
    else:
        await target.answer(text)
