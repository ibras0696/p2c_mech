from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.state import AgentMode, AgentSnapshot


def dashboard_keyboard(snapshot: AgentSnapshot, *, is_owner: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if snapshot.mode == AgentMode.PAUSED:
        builder.button(text="▶️ Запустить", callback_data="agent:run")
    else:
        builder.button(text="⏸ Пауза", callback_data="agent:pause")

    builder.button(text="🔄 Обновить", callback_data="panel:refresh")
    builder.button(text="📋 Активные", callback_data="orders:list")
    builder.button(text="⚙️ Лимит", callback_data="limit:menu")
    builder.button(text="💵 Суммы", callback_data="filters:amount")
    builder.button(text="🔐 Сессия", callback_data="session:status")
    builder.button(text="ℹ️ Помощь", callback_data="panel:help")
    if is_owner:
        builder.button(text="👑 Владелец", callback_data="admin:menu")
        builder.adjust(2, 2, 2, 2, 1)
    else:
        builder.adjust(2, 2, 2, 2)
    return builder.as_markup()


def orders_keyboard(snapshot: AgentSnapshot) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in snapshot.active_orders:
        builder.button(text=f"✅ Проверить оплату {order.id}", callback_data=f"order:confirm:{order.id}")
    builder.button(text="⬅️ Назад", callback_data="panel:refresh")
    builder.adjust(1)
    return builder.as_markup()


def payment_confirm_keyboard(order_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить оплачено", callback_data=f"order:paid:{order_id}")
    builder.button(text="🛑 Отменить заявку", callback_data=f"order:cancel:{order_id}")
    builder.button(text="⬅️ Назад к заявкам", callback_data="orders:list")
    builder.adjust(1)
    return builder.as_markup()


def limit_keyboard(current_limit: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for limit in (1, 2, 3, 5, 10):
        label = f"{limit}"
        if limit == current_limit:
            label = f"{limit} текущий"
        builder.button(text=label, callback_data=f"limit:set:{limit}")
    builder.button(text="⬅️ Назад", callback_data="panel:refresh")
    builder.adjust(3, 2, 1)
    return builder.as_markup()


def session_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔐 Статус сессии", callback_data="session:status")
    builder.button(text="🧪 Тест сокета", callback_data="session:probe_socket")
    builder.button(text="❔ Как обновить", callback_data="session:help")
    builder.button(text="⬅️ Назад", callback_data="panel:refresh")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def amount_filter_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="0 - 100", callback_data="filters:amount:set:0:100")
    builder.button(text="100 - 500", callback_data="filters:amount:set:100:500")
    builder.button(text="500 - 1000", callback_data="filters:amount:set:500:1000")
    builder.button(text="1000 - 5000", callback_data="filters:amount:set:1000:5000")
    builder.button(text="⬅️ Назад", callback_data="panel:refresh")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def owner_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Список админов", callback_data="admin:list")
    builder.button(text="🧠 Runtime статусы", callback_data="admin:runtime:list")
    builder.button(text="➕ Добавить админа", callback_data="admin:add:prompt")
    builder.button(text="➖ Удалить админа", callback_data="admin:remove:prompt")
    builder.button(text="⬅️ Назад", callback_data="panel:refresh")
    builder.adjust(2, 2, 1)
    return builder.as_markup()
