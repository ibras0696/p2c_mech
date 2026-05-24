from __future__ import annotations

from app.bot.session_state import PlatformSession


def render_session_help() -> str:
    return "\n".join(
        [
            "🔐 Обновление сессии",
            "━━━━━━━━━━━━━━",
            "",
            "Нужен WebSocket cURL из DevTools.",
            "Путь: Network → WS → p2c-socket → Copy → Copy as cURL (bash).",
            "",
            "URL должен быть таким:",
            "wss://app.send.tg/internal/v1/p2c-socket/?EIO=4&transport=websocket",
            "",
            "Бот извлечет access_token и __cf_bm, затем удалит сообщение с секретами.",
            "",
            "⚠️ Важно: обычный /p2c/orders cURL часто содержит только __cf_bm.",
            "Для сокета нужен access_token.",
        ]
    )


def render_session_status(session: PlatformSession) -> str:
    access_status = "есть" if session.access_token.strip() else "нет"
    cf_status = "есть" if session.cf_bm.strip() else "нет"
    return "\n".join(
        [
            "🔐 Статус сессии",
            "━━━━━━━━━━━━━━",
            "",
            f"access_token: {access_status}",
            f"__cf_bm: {cf_status}",
            f"updated_at: {session.updated_at.isoformat()}",
            "",
            "Значения токенов в интерфейсе не отображаются.",
        ]
    )
