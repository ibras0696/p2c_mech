import asyncio

from aiogram import Bot, Dispatcher

from app.bot.access import parse_admin_ids
from app.bot.router import build_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.repositories.active_orders import build_active_order_repository
from app.repositories.admin_registry import build_admin_registry_repository
from app.repositories.agent_preferences import build_agent_preferences_repository
from app.repositories.platform_session import build_platform_session_repository
from app.services.admin_access import AdminAccessService
from app.services.agent_runtime_manager import AgentRuntimeManager
from app.services.runtime_registry import clear_runtime_provider, set_runtime_provider

configure_logging()
logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    bot = Bot(token=settings.telegram_bot_token)
    owner_ids = parse_admin_ids(settings.telegram_admin_ids)
    if not owner_ids:
        raise RuntimeError("TELEGRAM_ADMIN_IDS must contain at least one owner id")

    session_repository = build_platform_session_repository(
        database_url=settings.database_url,
        encryption_key=settings.session_encryption_key,
        redis_host=settings.redis_host,
        redis_port=settings.redis_port,
        redis_db=settings.redis_db,
        redis_password=settings.redis_password,
        redis_url=settings.redis_url,
        session_cache_ttl_seconds=settings.session_cache_ttl_seconds,
    )
    preferences_repository = build_agent_preferences_repository(database_url=settings.database_url)
    active_order_repository = build_active_order_repository(database_url=settings.database_url)
    admin_registry_repository = build_admin_registry_repository(database_url=settings.database_url)
    access_service = AdminAccessService(repository=admin_registry_repository)
    await access_service.bootstrap_owners(owner_ids)

    runtime_manager = AgentRuntimeManager(
        settings=settings,
        bot=bot,
        preferences_repository=preferences_repository,
        platform_session_repository=session_repository,
        active_order_repository=active_order_repository,
    )
    await runtime_manager.start()
    set_runtime_provider(runtime_manager)

    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_router(
            access_service=access_service,
            runtime_manager=runtime_manager,
        )
    )
    logger.info("bot_started owners=%s", ",".join(str(item) for item in sorted(owner_ids)))
    try:
        await dispatcher.start_polling(bot)
    finally:
        clear_runtime_provider()
        await runtime_manager.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
