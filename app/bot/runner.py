import asyncio
from datetime import UTC, datetime
from urllib.parse import quote

from aiogram import Bot, Dispatcher

from app.bot.access import parse_admin_ids
from app.bot.router import build_router
from app.bot.state import ActiveOrder, agent_state
from app.bot.ui import payment_confirm_keyboard, render_payment_confirmation
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.repositories.active_orders import build_active_order_repository
from app.repositories.agent_preferences import (
    AgentPreferences,
    AgentPreferencesRepository,
    build_agent_preferences_repository,
)
from app.repositories.platform_session import build_platform_session_repository
from app.services.p2c_live_agent import P2CLiveAgent

configure_logging()
logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    bot = Bot(token=settings.telegram_bot_token)
    admin_ids = parse_admin_ids(settings.telegram_admin_ids)
    session_repository = build_platform_session_repository(
        database_url=settings.database_url,
        encryption_key=settings.session_encryption_key,
    )
    preferences_repository = build_agent_preferences_repository(database_url=settings.database_url)
    active_order_repository = build_active_order_repository(database_url=settings.database_url)
    await hydrate_preferences(preferences_repository)
    live_agent = P2CLiveAgent(
        settings=settings,
        state=agent_state,
        session_repository=session_repository,
        active_order_repository=active_order_repository,
        notify_order_ready=lambda order: notify_order_ready(bot, admin_ids, order),
    )
    await live_agent.load_persisted_orders()
    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_router(
            settings,
            agent_preferences_repository=preferences_repository,
            platform_session_repository=session_repository,
            live_agent=live_agent,
        )
    )
    logger.info("bot_started")
    agent_task = asyncio.create_task(live_agent.run_forever())
    try:
        await dispatcher.start_polling(bot)
    finally:
        live_agent.stop()
        agent_task.cancel()
        await asyncio.gather(agent_task, return_exceptions=True)
        await live_agent.aclose()
        await bot.session.close()


async def hydrate_preferences(
    preferences_repository: AgentPreferencesRepository,
) -> None:
    try:
        preferences = await preferences_repository.current()
    except Exception as exc:
        logger.warning("agent_preferences_load_failed error=%s", type(exc).__name__)
        return
    if preferences is None:
        snapshot = agent_state.snapshot()
        try:
            await preferences_repository.save(
                AgentPreferences(
                    active_limit=snapshot.active_limit,
                    min_amount=snapshot.min_amount,
                    max_amount=snapshot.max_amount,
                    updated_at=datetime.now(UTC),
                )
            )
        except Exception as exc:
            logger.warning("agent_preferences_seed_failed error=%s", type(exc).__name__)
        return
    agent_state.set_limit(preferences.active_limit)
    agent_state.set_amount_filter(preferences.min_amount, preferences.max_amount)
    snapshot = agent_state.snapshot()
    logger.info(
        "agent_preferences_loaded active_limit=%d min_amount=%s max_amount=%s",
        snapshot.active_limit,
        snapshot.min_amount,
        snapshot.max_amount,
    )


async def notify_order_ready(bot: Bot, admin_ids: set[int], order: ActiveOrder) -> None:
    if not admin_ids:
        return
    caption = render_payment_confirmation(order)
    keyboard = payment_confirm_keyboard(order.id)
    qr_url = build_qr_image_url(order.url)
    for admin_id in admin_ids:
        try:
            if qr_url is not None:
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=qr_url,
                    caption=caption,
                    reply_markup=keyboard,
                )
            else:
                await bot.send_message(chat_id=admin_id, text=caption, reply_markup=keyboard)
        except Exception as exc:
            logger.warning(
                "bot_notify_order_failed admin_id=%s payment_id=%s error=%s",
                admin_id,
                order.id,
                type(exc).__name__,
            )


def build_qr_image_url(source_url: str) -> str | None:
    url = source_url.strip()
    if not url:
        return None
    return f"https://quickchart.io/qr?size=500&text={quote(url, safe='')}"


if __name__ == "__main__":
    asyncio.run(main())
