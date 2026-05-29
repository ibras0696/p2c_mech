from __future__ import annotations

from datetime import UTC, datetime

from app.bot.state import ActiveOrder, AgentSnapshot
from app.bot.ui.labels import MODE_LABELS


def render_dashboard(snapshot: AgentSnapshot) -> str:
    lines = [
        "🤖 Панель агента",
        "━━━━━━━━━━━━━━",
        "",
        f"📡 Статус: {MODE_LABELS[snapshot.mode]}",
        f"📦 Активные заявки: {snapshot.active_count}/{snapshot.active_limit}",
        f"🧷 Свободные слоты: {snapshot.free_slots}",
        f"💵 Фильтр суммы: {snapshot.min_amount} - {snapshot.max_amount}",
        "",
        "📋 Активные заявки:",
    ]
    if snapshot.active_orders:
        lines.extend(render_order_line(order) for order in snapshot.active_orders)
    else:
        lines.append("Пока нет активных заявок")
    return "\n".join(lines)


def render_order_line(order: ActiveOrder) -> str:
    deadline = render_deadline(order)
    latency = render_claim_latency(order, with_prefix=True)
    return (
        f"• {order.id} | {order.amount} {order.currency} | {order.direction}"
        f" | {order.provider or 'provider?'}{latency}{deadline}"
    )


def render_payment_confirmation(order: ActiveOrder) -> str:
    return "\n".join(
        [
            "✅ Подтверждение оплаты",
            "━━━━━━━━━━━━━━",
            "",
            f"🧾 Заявка: {order.id}",
            f"💰 Сумма: {order.amount} {order.currency}",
            f"🔁 Направление: {order.direction}",
            f"🏷 Провайдер: {order.provider or 'unknown'}",
            f"⚡ Захват: {render_claim_latency(order, with_prefix=False)}",
            f"🔑 Method ID: {order.method_id or 'не найден'}",
            f"🔗 Ссылка: {order.url or 'не найдена'}",
            f"⏱ Дедлайн: {render_deadline(order, with_prefix=False)}",
            "",
            "⚠️ Подтверждайте только после фактической оплаты.",
            "Если заявка чужая или реквизиты не совпали, не оплачивайте ее.",
        ]
    )


PROCESSED_HEADERS = {
    "paid": "✅ Оплачено",
    "cancelled": "❌ Отменено",
    "closed": "⚠️ Заявка закрыта или просрочена",
}


def render_order_processed(order: ActiveOrder, *, outcome: str) -> str:
    header = PROCESSED_HEADERS.get(outcome, "✅ Обработано")
    processed_at = datetime.now(UTC).strftime("%H:%M:%S")
    return "\n".join(
        [
            header,
            "━━━━━━━━━━━━━━",
            "",
            f"🧾 Заявка: {order.id}",
            f"💰 Сумма: {order.amount} {order.currency}",
            f"🏷 Провайдер: {order.provider or 'unknown'}",
            f"⚡ Время захвата: {render_claim_latency(order, with_prefix=False)}",
            f"🕒 Обработано: {processed_at}",
        ]
    )


def render_limit_panel(current_limit: int) -> str:
    return "\n".join(
        [
            "⚙️ Лимит активных заявок",
            "━━━━━━━━━━━━━━",
            "",
            f"Текущий лимит: {current_limit}",
            "Выберите новое значение. Применяется без перезапуска.",
        ]
    )


def render_amount_filter_panel(snapshot: AgentSnapshot) -> str:
    return "\n".join(
        [
            "💵 Фильтр суммы",
            "━━━━━━━━━━━━━━",
            "",
            f"Минимум: {snapshot.min_amount}",
            f"Максимум: {snapshot.max_amount}",
            "",
            "Выберите пресет или отправьте сообщение:",
            "min max",
            "",
            "Пример: 100 500",
        ]
    )


def render_help() -> str:
    return "\n".join(
        [
            "ℹ️ Панель управления",
            "━━━━━━━━━━━━━━",
            "",
            "Основная работа идет через кнопки под панелью.",
            "Команды /start и /panel нужны, чтобы открыть интерфейс заново.",
            "",
            "✅ Оплачено закрывает выбранную заявку и освобождает слот после ручной проверки.",
        ]
    )


def render_deadline(order: ActiveOrder, *, with_prefix: bool = True) -> str:
    if order.deadline_at is None:
        return "дедлайн не задан" if not with_prefix else ""
    remaining = int((order.deadline_at - datetime.now(UTC)).total_seconds())
    if remaining <= 0:
        text = "просрочено"
    else:
        minutes, seconds = divmod(remaining, 60)
        text = f"{minutes:02d}:{seconds:02d}"
    if with_prefix:
        return f" | ⏱ {text}"
    return text


def render_claim_latency(order: ActiveOrder, *, with_prefix: bool) -> str:
    if order.claim_total_ms is not None:
        value = f"{order.claim_total_ms} ms"
    elif order.take_http_ms is not None:
        value = f"{order.take_http_ms} ms (take)"
    else:
        value = "n/a"
    if with_prefix:
        return f" | ⚡ {value}"
    return value
