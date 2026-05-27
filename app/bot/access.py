from __future__ import annotations

from aiogram.types import CallbackQuery, Message

from app.services.admin_access import AdminAccessService


def parse_admin_ids(raw_value: str) -> set[int]:
    result: set[int] = set()
    for item in raw_value.split(","):
        item = item.strip()
        if item:
            result.add(int(item))
    return result


def is_allowed_user(user_id: int | None, allowed_user_ids: set[int]) -> bool:
    return bool(user_id and (not allowed_user_ids or user_id in allowed_user_ids))


async def reject_message(message: Message) -> None:
    await message.answer("Доступ закрыт")


async def reject_callback(callback: CallbackQuery) -> None:
    await callback.answer("Доступ закрыт", show_alert=True)


async def ensure_allowed_message(
    message: Message,
    access_service: AdminAccessService,
) -> bool:
    user_id = message.from_user.id if message.from_user else None
    if await access_service.is_allowed(user_id):
        return True
    await reject_message(message)
    return False


async def ensure_allowed_callback(
    callback: CallbackQuery,
    access_service: AdminAccessService,
) -> bool:
    if await access_service.is_allowed(callback.from_user.id):
        return True
    await reject_callback(callback)
    return False


async def ensure_owner_message(
    message: Message,
    access_service: AdminAccessService,
) -> bool:
    user_id = message.from_user.id if message.from_user else None
    if await access_service.is_owner(user_id):
        return True
    await reject_message(message)
    return False


async def ensure_owner_callback(
    callback: CallbackQuery,
    access_service: AdminAccessService,
) -> bool:
    if await access_service.is_owner(callback.from_user.id):
        return True
    await reject_callback(callback)
    return False
