from __future__ import annotations

from aiogram.types import CallbackQuery, Message


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
