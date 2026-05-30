from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote

from aiogram import Bot
from redis import asyncio as redis_asyncio  # type: ignore[import-untyped]

from app.bot.state import ActiveOrder, AgentSnapshot, ClaimMetrics, InMemoryAgentState
from app.bot.ui import payment_confirm_keyboard, render_payment_confirmation
from app.core.config import Settings
from app.core.logging import get_logger
from app.repositories.active_orders import ActiveOrderRepository
from app.repositories.agent_preferences import AgentPreferences, AgentPreferencesRepository
from app.repositories.platform_session import PlatformSessionRepository
from app.services.p2c_live_agent import P2CLiveAgent

logger = get_logger(__name__)


class ScopedPlatformSessionRepository(PlatformSessionRepository):
    def __init__(self, *, parent: PlatformSessionRepository, user_id: int) -> None:
        self._parent = parent
        self._user_id = user_id

    async def save_for_user(self, user_id: int, session):
        return await self._parent.save_for_user(user_id, session)

    async def current_for_user(self, user_id: int):
        return await self._parent.current_for_user(user_id)

    async def save(self, session):
        return await self._parent.save_for_user(self._user_id, session)

    async def current(self):
        return await self._parent.current_for_user(self._user_id)


class ScopedActiveOrderRepository(ActiveOrderRepository):
    def __init__(self, *, parent: ActiveOrderRepository, user_id: int) -> None:
        self._parent = parent
        self._user_id = user_id

    async def upsert_for_user(self, user_id: int, order: ActiveOrder) -> None:
        await self._parent.upsert_for_user(user_id, order)

    async def remove_for_user(
        self,
        user_id: int,
        order_id: str,
        *,
        final_status: str = "paid",
        reason: str = "",
    ) -> None:
        await self._parent.remove_for_user(user_id, order_id, final_status=final_status, reason=reason)

    async def list_all_for_user(self, user_id: int) -> list[ActiveOrder]:
        return await self._parent.list_all_for_user(user_id)

    async def upsert(self, order: ActiveOrder) -> None:
        await self._parent.upsert_for_user(self._user_id, order)

    async def remove(
        self,
        order_id: str,
        *,
        final_status: str = "paid",
        reason: str = "",
    ) -> None:
        await self._parent.remove_for_user(self._user_id, order_id, final_status=final_status, reason=reason)

    async def list_all(self) -> list[ActiveOrder]:
        return await self._parent.list_all_for_user(self._user_id)


@dataclass
class UserRuntime:
    user_id: int
    state: InMemoryAgentState
    live_agent: P2CLiveAgent
    session_repository: PlatformSessionRepository
    action_lock: asyncio.Lock
    task: asyncio.Task[None]
    last_used_monotonic: float
    last_used_at: datetime


class AgentRuntimeManager:
    def __init__(
        self,
        *,
        settings: Settings,
        bot: Bot,
        preferences_repository: AgentPreferencesRepository,
        platform_session_repository: PlatformSessionRepository,
        active_order_repository: ActiveOrderRepository,
    ) -> None:
        self._settings = settings
        self._bot = bot
        self._preferences_repository = preferences_repository
        self._platform_session_repository = platform_session_repository
        self._active_order_repository = active_order_repository
        self._runtimes: dict[int, UserRuntime] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._local_dedupe: dict[str, float] = {}
        self._redis = self._build_redis_client()

    async def start(self) -> None:
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            await asyncio.gather(self._cleanup_task, return_exceptions=True)
        async with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            runtime.live_agent.stop()
            runtime.task.cancel()
            await asyncio.gather(runtime.task, return_exceptions=True)
            await runtime.live_agent.aclose()
        if self._redis is not None:
            await self._redis.aclose()

    async def get_or_create(self, user_id: int) -> UserRuntime:
        async with self._lock:
            runtime = self._runtimes.get(user_id)
            if runtime is not None:
                runtime.last_used_monotonic = time.monotonic()
                runtime.last_used_at = datetime.now(UTC)
                return runtime
            state = InMemoryAgentState()
            scoped_session_repo = ScopedPlatformSessionRepository(parent=self._platform_session_repository, user_id=user_id)
            scoped_order_repo = ScopedActiveOrderRepository(parent=self._active_order_repository, user_id=user_id)
            live_agent = P2CLiveAgent(
                settings=self._settings,
                state=state,
                session_repository=scoped_session_repo,
                active_order_repository=scoped_order_repo,
                notify_order_ready=lambda order: self._notify_order_ready(user_id, order),
                user_id=user_id,
            )
            runtime = UserRuntime(
                user_id=user_id,
                state=state,
                live_agent=live_agent,
                session_repository=scoped_session_repo,
                action_lock=asyncio.Lock(),
                task=asyncio.create_task(live_agent.run_forever()),
                last_used_monotonic=time.monotonic(),
                last_used_at=datetime.now(UTC),
            )
            self._runtimes[user_id] = runtime
            logger.info(
                "event=runtime_created user_id=%s runtime_count=%d",
                user_id,
                len(self._runtimes),
            )
        await self._hydrate_runtime(runtime)
        return runtime

    async def snapshot(self, user_id: int) -> AgentSnapshot:
        runtime = await self.get_or_create(user_id)
        runtime.last_used_monotonic = time.monotonic()
        runtime.last_used_at = datetime.now(UTC)
        return runtime.state.snapshot()

    async def run(self, user_id: int) -> AgentSnapshot:
        runtime = await self.get_or_create(user_id)
        runtime.last_used_monotonic = time.monotonic()
        runtime.last_used_at = datetime.now(UTC)
        return runtime.state.run()

    async def pause(self, user_id: int) -> AgentSnapshot:
        runtime = await self.get_or_create(user_id)
        runtime.last_used_monotonic = time.monotonic()
        runtime.last_used_at = datetime.now(UTC)
        runtime.live_agent.on_pause()
        return runtime.state.pause()

    async def set_limit(self, user_id: int, limit: int) -> AgentSnapshot:
        runtime = await self.get_or_create(user_id)
        snapshot = runtime.state.set_limit(limit)
        await self._persist_preferences(user_id, snapshot)
        runtime.last_used_monotonic = time.monotonic()
        runtime.last_used_at = datetime.now(UTC)
        return snapshot

    async def set_amount_filter(self, user_id: int, min_amount: Decimal, max_amount: Decimal) -> AgentSnapshot:
        runtime = await self.get_or_create(user_id)
        snapshot = runtime.state.set_amount_filter(min_amount, max_amount)
        await self._persist_preferences(user_id, snapshot)
        runtime.last_used_monotonic = time.monotonic()
        runtime.last_used_at = datetime.now(UTC)
        return snapshot

    async def get_metrics(self, user_id: int) -> ClaimMetrics:
        runtime = await self.get_or_create(user_id)
        return runtime.state.get_metrics()

    async def runtime_statuses(self) -> list[dict[str, object]]:
        async with self._lock:
            items = list(self._runtimes.items())
        result: list[dict[str, object]] = []
        for user_id, runtime in sorted(items, key=lambda item: item[0]):
            snapshot = runtime.state.snapshot()
            result.append(
                {
                    "user_id": user_id,
                    "mode": snapshot.mode.value,
                    "active_count": snapshot.active_count,
                    "active_limit": snapshot.active_limit,
                    "free_slots": snapshot.free_slots,
                    "running": not runtime.task.done(),
                    "last_used_at": runtime.last_used_at.isoformat(),
                }
            )
        return result

    async def acquire_action_lock(self, user_id: int) -> asyncio.Lock:
        runtime = await self.get_or_create(user_id)
        return runtime.action_lock

    async def is_callback_duplicate(
        self,
        *,
        user_id: int,
        message_id: int,
        callback_data: str,
        ttl_seconds: int = 2,
    ) -> bool:
        key = f"dedupe:{user_id}:{message_id}:{callback_data}"
        if self._redis is not None:
            try:
                created = await self._redis.set(key, "1", ex=max(1, ttl_seconds), nx=True)
                return created is None
            except Exception:
                pass
        now = time.monotonic()
        expires_at = self._local_dedupe.get(key)
        if expires_at is not None and expires_at > now:
            return True
        self._local_dedupe[key] = now + max(1, ttl_seconds)
        return False

    async def _hydrate_runtime(self, runtime: UserRuntime) -> None:
        preferences = await self._preferences_repository.current_for_user(runtime.user_id)
        if preferences is not None:
            runtime.state.set_limit(preferences.active_limit)
            runtime.state.set_amount_filter(preferences.min_amount, preferences.max_amount)
        await runtime.live_agent.load_persisted_orders()

    async def _persist_preferences(self, user_id: int, snapshot: AgentSnapshot) -> None:
        try:
            await self._preferences_repository.save_for_user(
                user_id,
                AgentPreferences(
                    active_limit=snapshot.active_limit,
                    min_amount=snapshot.min_amount,
                    max_amount=snapshot.max_amount,
                    updated_at=datetime.now(UTC),
                ),
            )
        except Exception as exc:
            logger.warning(
                "event=runtime_preferences_persist_failed user_id=%s error=%s",
                user_id,
                type(exc).__name__,
            )

    async def _notify_order_ready(self, user_id: int, order: ActiveOrder) -> None:
        caption = render_payment_confirmation(order)
        keyboard = payment_confirm_keyboard(order.id)
        qr_url = self._build_qr_image_url(order.url)
        try:
            if qr_url is not None:
                await self._bot.send_photo(
                    chat_id=user_id,
                    photo=qr_url,
                    caption=caption,
                    reply_markup=keyboard,
                )
            else:
                await self._bot.send_message(chat_id=user_id, text=caption, reply_markup=keyboard)
        except Exception as exc:
            logger.warning(
                "event=runtime_notify_order_failed user_id=%s payment_id=%s source_order_id=%s error=%s",
                user_id,
                order.id,
                order.source_order_id,
                type(exc).__name__,
            )

    async def _cleanup_loop(self) -> None:
        idle_ttl_seconds = max(1, int(self._settings.runtime_idle_ttl_seconds))
        interval_seconds = max(1, int(self._settings.runtime_cleanup_interval_seconds))
        while not self._stop_event.is_set():
            await asyncio.sleep(interval_seconds)
            await self._cleanup_once(idle_ttl_seconds=idle_ttl_seconds)

    async def _cleanup_once(self, *, idle_ttl_seconds: int) -> int:
        now = time.monotonic()
        async with self._lock:
            victims: list[int] = []
            for user_id, runtime in self._runtimes.items():
                snapshot = runtime.state.snapshot()
                idle_seconds = now - runtime.last_used_monotonic
                if snapshot.mode.value != "paused":
                    continue
                if snapshot.active_count > 0:
                    continue
                if idle_seconds < idle_ttl_seconds:
                    continue
                victims.append(user_id)
            runtimes = [self._runtimes.pop(user_id) for user_id in victims]
        for runtime in runtimes:
            runtime.live_agent.stop()
            runtime.task.cancel()
            await asyncio.gather(runtime.task, return_exceptions=True)
            await runtime.live_agent.aclose()
            logger.info("event=runtime_cleaned user_id=%s", runtime.user_id)
        if runtimes:
            logger.info(
                "event=runtime_cleanup_batch cleaned=%d runtime_count=%d",
                len(runtimes),
                len(self._runtimes),
            )
        return len(runtimes)

    def _build_redis_client(self):
        try:
            if self._settings.redis_url:
                return redis_asyncio.from_url(self._settings.redis_url, encoding="utf-8", decode_responses=True)
            return redis_asyncio.Redis(
                host=self._settings.redis_host,
                port=self._settings.redis_port,
                db=self._settings.redis_db,
                password=self._settings.redis_password or None,
                encoding="utf-8",
                decode_responses=True,
            )
        except Exception:
            return None

    @staticmethod
    def _build_qr_image_url(source_url: str) -> str | None:
        url = source_url.strip()
        if not url:
            return None
        return f"https://quickchart.io/qr?size=500&text={quote(url, safe='')}"
