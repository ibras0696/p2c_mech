from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message


def callback_data(callback: CallbackQuery) -> str:
    if callback.data is None:
        raise ValueError("Callback data is required")
    return callback.data


async def edit_text(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    message = callback.message
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if "message is not modified" in error_text:
            return
        if "there is no text in the message to edit" not in error_text:
            raise
        await _edit_caption_or_send_new(message, text=text, reply_markup=reply_markup)


async def _edit_caption_or_send_new(
    message: Message,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    try:
        await message.edit_caption(caption=text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if "message is not modified" in error_text:
            return
        if "there is no caption in the message to edit" in error_text:
            await message.answer(text, reply_markup=reply_markup)
            return
        raise


async def delete_message_safely(callback: CallbackQuery) -> None:
    message = callback.message
    if message is None:
        return
    try:
        await message.delete()
    except TelegramBadRequest:
        return
