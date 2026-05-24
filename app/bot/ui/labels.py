from app.bot.state import AgentMode

MODE_LABELS: dict[AgentMode, str] = {
    AgentMode.WAITING: "🟢 Поиск активен",
    AgentMode.PAUSED: "⏸ Пауза",
    AgentMode.CAPACITY_REACHED: "🟠 Лимит заполнен",
    AgentMode.CAPTCHA_REQUIRED: "🧩 Нужна капча",
    AgentMode.ERROR: "🔴 Ошибка",
}
