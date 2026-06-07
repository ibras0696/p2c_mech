from datetime import UTC, datetime

from fastapi import FastAPI

from app.api.agent import router as agent_router
from app.api.health import router as health_router
from app.api.stats import router as stats_router
from app.bot.session_state import PlatformSession
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.services.agent_registry import (
    set_stats_redis,
    set_stats_repo,
    set_supervisor,
)
from app.services.agent_supervisor import AgentSession, AgentSupervisor

configure_logging()
logger = get_logger(__name__)

app = FastAPI(title="Automation Agent API", version="0.1.0")
app.include_router(health_router)
app.include_router(agent_router)
app.include_router(stats_router)


def _build_redis_client(settings: object):  # type: ignore[no-untyped-def]
    from redis import asyncio as redis_asyncio  # type: ignore[import-untyped]

    s = settings  # local alias
    if s.redis_url:  # type: ignore[attr-defined]
        return redis_asyncio.from_url(s.redis_url, encoding="utf-8", decode_responses=True)  # type: ignore[attr-defined]
    return redis_asyncio.Redis(
        host=s.redis_host,  # type: ignore[attr-defined]
        port=s.redis_port,  # type: ignore[attr-defined]
        db=s.redis_db,  # type: ignore[attr-defined]
        password=s.redis_password or None,  # type: ignore[attr-defined]
        encoding="utf-8",
        decode_responses=True,
    )


@app.on_event("startup")
async def on_startup() -> None:
    settings = get_settings()

    # Stats repo (Postgres durable history; in-memory fallback if no DATABASE_URL).
    from app.repositories.agent_stats_repo import build_agent_stats_repository

    stats_repo = build_agent_stats_repository(database_url=settings.database_url)
    set_stats_repo(stats_repo)

    # Redis for cache-aside + stat counters. Built lazily; failures are non-fatal.
    redis_client = None
    try:
        redis_client = _build_redis_client(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis_init_failed error=%s", type(exc).__name__)
    set_stats_redis(redis_client)

    # Cache-aside session loader: pull durable session from Postgres on miss.
    session_loader = _build_session_loader(settings)

    # Win post-processing: the operator confirms payment manually via the bot
    # (paid/cancel buttons), so we do NOT auto-complete on win. The win is
    # bridged to the bot process via Redis (KEY_WINS_PENDING). Use
    # _build_confirm_win(settings) only for a fully-automated deployment.
    confirm_win = None
    _ = _build_confirm_win  # keep helper referenced for optional re-enable

    supervisor = AgentSupervisor(
        agent_bin=settings.agent_bin,
        ws_url=settings.platform_ws_url,
        base_url=settings.platform_base_url,
        origin=settings.platform_base_url,
        redis_url=settings.redis_url or f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}",
        impersonate=settings.agent_impersonate,
        session_ttl_seconds=settings.session_ttl_seconds,
        http_ms_max_samples=settings.stat_http_ms_max_samples,
        redis=redis_client,
        stats_repo=stats_repo,
        session_loader=session_loader,
        confirm_win=confirm_win,
        win_notifier=None,  # TODO: wire Telegram notifier when bot exposes a hook.
    )
    set_supervisor(supervisor)
    logger.info("api_started")


def _build_session_loader(settings: object):  # type: ignore[no-untyped-def]
    database_url = settings.database_url  # type: ignore[attr-defined]
    encryption_key = settings.session_encryption_key  # type: ignore[attr-defined]
    if not database_url or not encryption_key:
        return None
    from app.repositories.platform_session import build_platform_session_repository

    repo = build_platform_session_repository(
        database_url=database_url,
        encryption_key=encryption_key,
        redis_host=settings.redis_host,  # type: ignore[attr-defined]
        redis_port=settings.redis_port,  # type: ignore[attr-defined]
        redis_db=settings.redis_db,  # type: ignore[attr-defined]
        redis_password=settings.redis_password,  # type: ignore[attr-defined]
        redis_url=settings.redis_url,  # type: ignore[attr-defined]
        session_cache_ttl_seconds=settings.session_cache_ttl_seconds,  # type: ignore[attr-defined]
    )

    async def loader(account: str) -> AgentSession | None:
        # account maps onto the per-user platform session store.
        try:
            user_id = int(account)
        except ValueError:
            user_id = 0
        stored = await repo.current_for_user(user_id)
        if stored is None:
            return None
        return AgentSession(
            access_token=stored.access_token,
            # access-only (no __cf_bm): the cache-aside / reconnect path must
            # match the add_account path, else a WS reconnect re-seeds the C
            # agent with the Cloudflare bot cookie we are trying to shed.
            cookie_header=stored.cookie_header_access_only,
        )

    return loader


def _build_confirm_win(settings: object):  # type: ignore[no-untyped-def]
    base_url = settings.platform_base_url  # type: ignore[attr-defined]
    if not base_url:
        return None

    loader = _build_session_loader(settings)

    async def confirm_win(account: str, payment_id: int) -> None:
        # §5.4: confirm(payment_id) -> complete(method_id). The platform client
        # exposes complete(); there is no confirm() yet, so we fetch the payment
        # to resolve method_id then complete. TODO: add a dedicated confirm()
        # call once the client supports it.
        if loader is None:
            logger.warning("confirm_win_skipped reason=no_session_loader account=%s", account)
            return
        session = await loader(account)
        if session is None:
            logger.warning("confirm_win_skipped reason=no_session account=%s", account)
            return
        platform_session = PlatformSession(
            access_token=session.access_token,
            cf_bm="",
            updated_at=datetime.now(UTC),
        )
        # Imported lazily: curl_cffi may be unavailable in some environments.
        from app.integrations.platform_api.p2c_payments import P2CPaymentsClient

        client = P2CPaymentsClient(base_url=base_url)
        try:
            details = await client.get_payment(payment_id=payment_id, session=platform_session)
            await client.complete(
                payment_id=payment_id,
                method_id=details.method_id,
                session=platform_session,
            )
        finally:
            await client.aclose()

    return confirm_win
