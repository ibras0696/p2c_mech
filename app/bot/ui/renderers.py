from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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


def render_stats(payload: dict[str, Any]) -> str:
    """Render GET /stats response. Works regardless of agent state (§6.2)."""
    account = payload.get("account") or "все аккаунты"
    period = payload.get("period") or "all"
    live = payload.get("live") or {}
    aggregate = payload.get("aggregate") or {}

    orders_seen = live.get("orders_seen", 0)
    takes = aggregate.get("takes", live.get("takes", 0))
    wins = aggregate.get("wins", live.get("wins", 0))
    losses = aggregate.get("losses", 0)
    win_rate = aggregate.get("win_rate", 0.0) or 0.0
    p50 = aggregate.get("p50_http_ms")
    p99 = aggregate.get("p99_http_ms")

    lines = [
        "📊 Статистика",
        "━━━━━━━━━━━━━━",
        "",
        f"👤 Аккаунт: {account}",
        f"🗓 Период: {period}",
        "",
        f"👀 Ордеров замечено: {orders_seen}",
        f"🎯 Попыток (takes): {takes}",
        f"🏆 Побед: {wins}",
        f"❌ Поражений: {losses}",
        f"📈 Win-rate: {win_rate * 100:.1f}%",
        f"⚡ http_ms p50/p99: {_fmt_ms(p50)} / {_fmt_ms(p99)}",
    ]

    recent = aggregate.get("recent") or []
    if recent:
        lines.append("")
        lines.append("🧾 Последние события:")
        for event in recent[:10]:
            acc = event.get("account", "?")
            kind = event.get("kind", "?")
            status = event.get("status")
            http_ms = event.get("http_ms")
            parts = [f"• {acc} | {kind}"]
            if status is not None:
                parts.append(f"status={status}")
            if http_ms is not None:
                parts.append(f"{http_ms}ms")
            lines.append(" | ".join(parts))
    return "\n".join(lines)


def render_agent_status(payload: dict[str, Any]) -> str:
    """Render GET /agent/status response."""
    running = payload.get("running", False)
    accounts = payload.get("accounts") or []
    lines = [
        "🛰 Статус агента",
        "━━━━━━━━━━━━━━",
        "",
        f"⚙️ Запущен: {'да' if running else 'нет'}",
        f"👥 Аккаунтов: {len(accounts)}",
    ]
    if accounts:
        lines.append("")
        for acc in accounts:
            name = acc.get("account", "?")
            ws = "🔗" if acc.get("ws_connected") else "🔌"
            mode = acc.get("mode", "?")
            uptime = acc.get("uptime")
            suffix = f" | uptime {uptime}s" if uptime is not None else ""
            lines.append(f"{ws} {name} | {mode}{suffix}")
    return "\n".join(lines)


def _fmt_ms(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.0f}"
    return str(value)


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
