from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.access import ensure_owner_callback, ensure_owner_message
from app.bot.callbacks import edit_text
from app.bot.ui import owner_menu_keyboard
from app.core.logging import get_logger
from app.repositories.admin_registry import AdminRole
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager

logger = get_logger(__name__)

_owner_pending_action: dict[int, str] = {}


def _render_admin_list(users) -> str:
    if not users:
        return "Активных админов нет."
    lines = ["Активные админы:"]
    for user in users:
        role = "owner" if user.role == AdminRole.OWNER else "admin"
        lines.append(f"- {user.user_id} ({role})")
    return "\n".join(lines)


def _render_runtime_list(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "Активных runtime пока нет."
    lines = ["Runtime состояния:"]
    for row in rows:
        lines.append(
            f"- user={row['user_id']} mode={row['mode']} "
            f"active={row['active_count']}/{row['active_limit']} "
            f"free={row['free_slots']} running={row['running']} "
            f"last_used_at={row['last_used_at']}"
        )
    return "\n".join(lines)


def build_admin_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
) -> Router:
    router = Router()

    @router.callback_query(F.data == "admin:menu")
    async def callback_owner_menu(callback: CallbackQuery) -> None:
        if not await ensure_owner_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        _owner_pending_action.pop(user_id, None)
        text = (
            "Панель владельца\n\n"
            "Используйте кнопки ниже для управления админами и runtime."
        )
        await edit_text(callback, text, owner_menu_keyboard())
        await callback.answer()

    @router.callback_query(F.data == "admin:list")
    async def callback_admin_list(callback: CallbackQuery) -> None:
        if not await ensure_owner_callback(callback, access_service):
            return
        users = await access_service.list_active()
        await edit_text(callback, _render_admin_list(users), owner_menu_keyboard())
        await callback.answer()

    @router.callback_query(F.data == "admin:runtime:list")
    async def callback_runtime_list(callback: CallbackQuery) -> None:
        if not await ensure_owner_callback(callback, access_service):
            return
        rows = await runtime_manager.runtime_statuses()
        await edit_text(callback, _render_runtime_list(rows), owner_menu_keyboard())
        await callback.answer()

    @router.callback_query(F.data == "admin:add:prompt")
    async def callback_admin_add_prompt(callback: CallbackQuery) -> None:
        if not await ensure_owner_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        _owner_pending_action[user_id] = "add"
        await edit_text(
            callback,
            "Отправьте Telegram user_id для добавления админа.\n\nПример: 123456789",
            owner_menu_keyboard(),
        )
        await callback.answer("Ожидаю user_id")

    @router.callback_query(F.data == "admin:remove:prompt")
    async def callback_admin_remove_prompt(callback: CallbackQuery) -> None:
        if not await ensure_owner_callback(callback, access_service):
            return
        user_id = callback.from_user.id
        _owner_pending_action[user_id] = "remove"
        await edit_text(
            callback,
            "Отправьте Telegram user_id для удаления админа.\n\nПример: 123456789",
            owner_menu_keyboard(),
        )
        await callback.answer("Ожидаю user_id")

    @router.message(F.text.regexp(r"^\s*\d+\s*$"))
    async def handle_owner_id_input(message: Message) -> None:
        if not await ensure_owner_message(message, access_service):
            return
        if message.from_user is None or message.text is None:
            return
        owner_id = message.from_user.id
        action = _owner_pending_action.get(owner_id)
        if action is None:
            return
        target_user_id = int(message.text.strip())
        _owner_pending_action.pop(owner_id, None)

        if action == "add":
            existing = await access_service.get_user(user_id=target_user_id)
            if existing is not None and existing.is_active:
                await message.answer(f"Пользователь уже активен: {target_user_id}", reply_markup=owner_menu_keyboard())
                logger.info(
                    "event=admin_add_skipped user_id=%s target_user_id=%s reason=already_active",
                    owner_id,
                    target_user_id,
                )
                return
            admin = await access_service.add_admin(
                actor_user_id=owner_id,
                target_user_id=target_user_id,
            )
            await message.answer(f"Админ добавлен: {admin.user_id}", reply_markup=owner_menu_keyboard())
            logger.info(
                "event=admin_add_applied user_id=%s target_user_id=%s role=%s",
                owner_id,
                admin.user_id,
                admin.role.value,
            )
            return

        if target_user_id == owner_id:
            await message.answer("Нельзя удалить самого себя из owner.", reply_markup=owner_menu_keyboard())
            logger.info(
                "event=admin_remove_skipped user_id=%s target_user_id=%s reason=self_remove_forbidden",
                owner_id,
                target_user_id,
            )
            return
        existing = await access_service.get_user(user_id=target_user_id)
        if existing is None:
            await message.answer(f"Пользователь не найден: {target_user_id}", reply_markup=owner_menu_keyboard())
            logger.info(
                "event=admin_remove_skipped user_id=%s target_user_id=%s reason=not_found",
                owner_id,
                target_user_id,
            )
            return
        if not existing.is_active:
            await message.answer(f"Пользователь уже отключен: {target_user_id}", reply_markup=owner_menu_keyboard())
            logger.info(
                "event=admin_remove_skipped user_id=%s target_user_id=%s reason=already_inactive",
                owner_id,
                target_user_id,
            )
            return
        await access_service.remove_admin(target_user_id=target_user_id)
        await message.answer(f"Админ отключен: {target_user_id}", reply_markup=owner_menu_keyboard())
        logger.info(
            "event=admin_remove_applied user_id=%s target_user_id=%s",
            owner_id,
            target_user_id,
        )

    @router.message(Command("admin_add"))
    async def handle_admin_add(message: Message) -> None:
        if not await ensure_owner_message(message, access_service):
            return
        if message.text is None or message.from_user is None:
            return
        actor_user_id = message.from_user.id
        parts = message.text.split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Формат: /admin_add <telegram_user_id>")
            return
        target_user_id = int(parts[1])
        existing = await access_service.get_user(user_id=target_user_id)
        if existing is not None and existing.is_active:
            await message.answer(f"Пользователь уже активен: {target_user_id}")
            logger.info(
                "event=admin_add_skipped user_id=%s target_user_id=%s reason=already_active",
                actor_user_id,
                target_user_id,
            )
            return
        admin = await access_service.add_admin(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
        )
        await message.answer(f"Админ добавлен: {admin.user_id}")
        logger.info(
            "event=admin_add_applied user_id=%s target_user_id=%s role=%s",
            actor_user_id,
            admin.user_id,
            admin.role.value,
        )

    @router.message(Command("admin_rm"))
    async def handle_admin_rm(message: Message) -> None:
        if not await ensure_owner_message(message, access_service):
            return
        if message.text is None or message.from_user is None:
            return
        actor_user_id = message.from_user.id
        parts = message.text.split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Формат: /admin_rm <telegram_user_id>")
            return
        target_user_id = int(parts[1])
        if target_user_id == actor_user_id:
            await message.answer("Нельзя удалить самого себя из owner.")
            logger.info(
                "event=admin_remove_skipped user_id=%s target_user_id=%s reason=self_remove_forbidden",
                actor_user_id,
                target_user_id,
            )
            return
        existing = await access_service.get_user(user_id=target_user_id)
        if existing is None:
            await message.answer(f"Пользователь не найден: {target_user_id}")
            logger.info(
                "event=admin_remove_skipped user_id=%s target_user_id=%s reason=not_found",
                actor_user_id,
                target_user_id,
            )
            return
        if not existing.is_active:
            await message.answer(f"Пользователь уже отключен: {target_user_id}")
            logger.info(
                "event=admin_remove_skipped user_id=%s target_user_id=%s reason=already_inactive",
                actor_user_id,
                target_user_id,
            )
            return
        await access_service.remove_admin(target_user_id=target_user_id)
        await message.answer(f"Админ отключен: {target_user_id}")
        logger.info(
            "event=admin_remove_applied user_id=%s target_user_id=%s",
            actor_user_id,
            target_user_id,
        )

    @router.message(Command("admin_list"))
    async def handle_admin_list(message: Message) -> None:
        if not await ensure_owner_message(message, access_service):
            return
        users = await access_service.list_active()
        await message.answer(_render_admin_list(users))

    @router.message(Command("runtime_list"))
    async def handle_runtime_list(message: Message) -> None:
        if not await ensure_owner_message(message, access_service):
            return
        rows = await runtime_manager.runtime_statuses()
        await message.answer(_render_runtime_list(rows))

    return router
