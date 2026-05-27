from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.access import ensure_owner_message
from app.core.logging import get_logger
from app.repositories.admin_registry import AdminRole
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager

logger = get_logger(__name__)


def build_admin_router(
    access_service: AdminAccessService,
    runtime_manager: AgentRuntimeManager,
) -> Router:
    router = Router()

    @router.message(Command("admin_add"))
    async def handle_admin_add(message: Message) -> None:
        if not await ensure_owner_message(message, access_service):
            return
        if message.text is None or message.from_user is None:
            return
        actor_user_id = message.from_user.id
        parts = message.text.split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Usage: /admin_add <telegram_user_id>")
            return
        target_user_id = int(parts[1])
        existing = await access_service.get_user(user_id=target_user_id)
        if existing is not None and existing.is_active:
            await message.answer(f"User is already active: {target_user_id}")
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
        await message.answer(f"Admin added: {admin.user_id}")
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
            await message.answer("Usage: /admin_rm <telegram_user_id>")
            return
        target_user_id = int(parts[1])
        if target_user_id == actor_user_id:
            await message.answer("Cannot remove yourself from owner role.")
            logger.info(
                "event=admin_remove_skipped user_id=%s target_user_id=%s reason=self_remove_forbidden",
                actor_user_id,
                target_user_id,
            )
            return
        existing = await access_service.get_user(user_id=target_user_id)
        if existing is None:
            await message.answer(f"User not found: {target_user_id}")
            logger.info(
                "event=admin_remove_skipped user_id=%s target_user_id=%s reason=not_found",
                actor_user_id,
                target_user_id,
            )
            return
        if not existing.is_active:
            await message.answer(f"User already inactive: {target_user_id}")
            logger.info(
                "event=admin_remove_skipped user_id=%s target_user_id=%s reason=already_inactive",
                actor_user_id,
                target_user_id,
            )
            return
        await access_service.remove_admin(target_user_id=target_user_id)
        await message.answer(f"Admin removed: {target_user_id}")
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
        if not users:
            await message.answer("No active admins.")
            return
        lines = ["Active admins:"]
        for user in users:
            role = "owner" if user.role == AdminRole.OWNER else "admin"
            lines.append(f"- {user.user_id} ({role})")
        await message.answer("\n".join(lines))

    @router.message(Command("runtime_list"))
    async def handle_runtime_list(message: Message) -> None:
        if not await ensure_owner_message(message, access_service):
            return
        rows = await runtime_manager.runtime_statuses()
        if not rows:
            await message.answer("No active runtime contexts yet.")
            return
        lines = ["Runtime statuses:"]
        for row in rows:
            lines.append(
                f"- user={row['user_id']} mode={row['mode']} "
                f"active={row['active_count']}/{row['active_limit']} "
                f"free={row['free_slots']} running={row['running']} "
                f"last_used_at={row['last_used_at']}"
            )
        await message.answer("\n".join(lines))

    return router
