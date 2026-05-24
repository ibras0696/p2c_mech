from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetMe
from app.bot.callbacks import edit_text


def _bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=GetMe(), message=message)


class FakeMessage:
    def __init__(self) -> None:
        self.edited_text: list[str] = []
        self.edited_caption: list[str] = []
        self.sent_messages: list[str] = []
        self.raise_on_edit_text: Exception | None = None
        self.raise_on_edit_caption: Exception | None = None

    async def edit_text(self, text: str, reply_markup=None) -> None:  # noqa: ANN001
        del reply_markup
        if self.raise_on_edit_text is not None:
            raise self.raise_on_edit_text
        self.edited_text.append(text)

    async def edit_caption(self, caption: str, reply_markup=None) -> None:  # noqa: ANN001
        del reply_markup
        if self.raise_on_edit_caption is not None:
            raise self.raise_on_edit_caption
        self.edited_caption.append(caption)

    async def answer(self, text: str, reply_markup=None) -> None:  # noqa: ANN001
        del reply_markup
        self.sent_messages.append(text)


class FakeCallback:
    def __init__(self, message) -> None:  # noqa: ANN001
        self.message = message


@pytest.mark.asyncio
async def test_edit_text_falls_back_to_edit_caption() -> None:
    message = FakeMessage()
    message.raise_on_edit_text = _bad_request("there is no text in the message to edit")
    callback = FakeCallback(message)

    await edit_text(callback, "hello")

    assert message.edited_caption == ["hello"]


@pytest.mark.asyncio
async def test_edit_text_falls_back_to_send_message_when_caption_missing() -> None:
    message = FakeMessage()
    message.raise_on_edit_text = _bad_request("there is no text in the message to edit")
    message.raise_on_edit_caption = _bad_request("there is no caption in the message to edit")
    callback = FakeCallback(message)

    await edit_text(callback, "hello")

    assert message.sent_messages == ["hello"]


@pytest.mark.asyncio
async def test_edit_text_ignores_not_modified_error() -> None:
    message = FakeMessage()
    message.raise_on_edit_text = _bad_request("message is not modified")
    callback = FakeCallback(message)

    await edit_text(callback, "hello")

    assert message.edited_caption == []
    assert message.sent_messages == []
