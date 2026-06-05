from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.repositories.agent_stats_repo import (
    KIND_CLAIM_LOST,
    KIND_CLAIM_WON,
    KIND_TAKE_RESULT,
    AgentStatsRepository,
    OrderEvent,
)

logger = get_logger(__name__)

# ----- Redis key layout (docs/C_AGENT_SPEC_RU.md §3.2 / §6.1) ---------------
def k_session(account_id: str) -> str:
    return f"p2c:session:{account_id}"


def k_account(account_id: str) -> str:
    return f"p2c:account:{account_id}"


KEY_ACCOUNTS_ENABLED = "p2c:accounts:enabled"


def k_stat_takes(account: str) -> str:
    return f"p2c:stat:{account}:takes"


def k_stat_wins(account: str) -> str:
    return f"p2c:stat:{account}:wins"


def k_stat_orders_seen(account: str) -> str:
    return f"p2c:stat:{account}:orders_seen"


def k_stat_http_ms(account: str) -> str:
    return f"p2c:stat:{account}:http_ms"


# ----- session payload (cache-aside, p2c:session:{id}) ----------------------
@dataclass(frozen=True)
class AgentSession:
    access_token: str
    cookie_header: str
    did: str = ""
    expires_at: int = 0


# A SessionLoader fetches a durable session from Postgres for cache-aside refill.
SessionLoader = Callable[[str], Awaitable[AgentSession | None]]
# A WinNotifier is the Telegram hook; never hard-depended on (may be None).
WinNotifier = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class AccountRuntime:
    account_id: str
    ws_connected: bool = False
    # The C agent defaults to running on add_account (it detects AND takes
    # until an explicit {"cmd":"mode","value":"paused"}). Mirror that here so
    # /agent/status and the bot "Статус" view match the agent's real state.
    mode: str = "running"
    orders_seen: int = 0
    takes: int = 0
    wins: int = 0
    last_heartbeat_ts: float | None = None


@dataclass
class AgentSupervisor:
    """Supervises the C-agent subprocess (docs/C_AGENT_SPEC_RU.md §5.2).

    Owns the stdin command channel and the stdout event dispatch. All durable
    state (stats history, session source of truth) lives in injected
    dependencies so /stats can read Redis/Postgres without this object.
    """

    agent_bin: str
    ws_url: str
    base_url: str
    origin: str
    redis_url: str
    impersonate: str = "chrome131"
    session_ttl_seconds: int = 1500
    http_ms_max_samples: int = 1000

    redis: Any = None  # redis.asyncio client (decode_responses=True) or None
    stats_repo: AgentStatsRepository | None = None
    session_loader: SessionLoader | None = None
    confirm_win: Callable[[str, int], Awaitable[None]] | None = None
    win_notifier: WinNotifier | None = None

    running: bool = field(default=False, init=False)
    accounts: dict[str, AccountRuntime] = field(default_factory=dict, init=False)
    _proc: asyncio.subprocess.Process | None = field(default=None, init=False)
    _stdout_task: asyncio.Task[None] | None = field(default=None, init=False)
    _stderr_task: asyncio.Task[None] | None = field(default=None, init=False)
    _stdin_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    # ----- lifecycle -------------------------------------------------------
    async def spawn(self) -> bool:
        if self.running:
            return True
        args = [
            self.agent_bin,
            "--ws-url",
            self.ws_url,
            "--base-url",
            self.base_url,
            "--origin",
            self.origin,
            "--redis",
            self.redis_url,
            "--impersonate",
            self.impersonate,
        ]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            # Binary may not exist in this environment (e.g. CI). Do not crash;
            # surface running=False so callers/UX can react.
            logger.warning("agent_spawn_failed reason=binary_not_found bin=%s", self.agent_bin)
            self._proc = None
            self.running = False
            return False
        except OSError as exc:
            logger.warning("agent_spawn_failed reason=%s", type(exc).__name__)
            self._proc = None
            self.running = False
            return False

        self.running = True
        if self._proc.stdout is not None:
            self._stdout_task = asyncio.create_task(self._read_stdout(self._proc.stdout))
        if self._proc.stderr is not None:
            self._stderr_task = asyncio.create_task(self._read_stderr(self._proc.stderr))
        logger.info("agent_spawned bin=%s pid=%s", self.agent_bin, self._proc.pid)
        return True

    async def stop(self, *, terminate_timeout: float = 5.0) -> None:
        if self._proc is not None:
            try:
                await self.send_cmd({"cmd": "shutdown"})
            except Exception as exc:  # noqa: BLE001
                logger.warning("agent_shutdown_cmd_failed error=%s", type(exc).__name__)
            with contextlib.suppress(ProcessLookupError):
                self._proc.terminate()
            try:
                async with asyncio.timeout(terminate_timeout):
                    await self._proc.wait()
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self._proc.kill()
        for task in (self._stdout_task, self._stderr_task):
            if task is not None:
                task.cancel()
        self._stdout_task = None
        self._stderr_task = None
        self._proc = None
        self.running = False
        logger.info("agent_stopped")

    # ----- stdin command channel (§4.6) ------------------------------------
    async def send_cmd(self, cmd: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            logger.warning("agent_send_cmd_dropped reason=not_running cmd=%s", cmd.get("cmd"))
            return
        line = json.dumps(cmd, separators=(",", ":")).encode() + b"\n"
        async with self._stdin_lock:
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()

    # ----- stdout / stderr readers -----------------------------------------
    async def _read_stdout(self, stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("agent_stdout_malformed line=%s", text[:200])
                continue
            if not isinstance(event, dict):
                logger.warning("agent_stdout_not_object line=%s", text[:200])
                continue
            try:
                await self._dispatch(event)
            except Exception as exc:  # noqa: BLE001 - never let one event kill the loop
                logger.error(
                    "agent_event_handler_failed event=%s error=%s",
                    event.get("event"),
                    type(exc).__name__,
                )

    async def _read_stderr(self, stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            logger.info("agent_stderr %s", line.decode("utf-8", "replace").rstrip())

    async def _dispatch(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        account = str(event.get("account", "")) if event.get("account") is not None else ""
        if name == "ws_connected":
            self._runtime(account).ws_connected = True
        elif name == "ws_disconnected":
            self._runtime(account).ws_connected = False
        elif name == "session_miss":
            await self.on_session_miss(account)
        elif name == "order_detected":
            await self.on_order_detected(account, event)
        elif name == "take_sent":
            await self.on_take_sent(account, event)
        elif name == "take_result":
            await self.on_take_result(account, event)
        elif name == "claim_won":
            await self.on_claim_won(account, event)
        elif name == "claim_lost":
            await self.on_claim_lost(account, event)
        elif name == "heartbeat":
            await self.on_heartbeat(event)
        elif name == "error":
            logger.warning(
                "agent_error account=%s where=%s msg=%s",
                account,
                event.get("where"),
                event.get("msg"),
            )
        else:
            logger.info("agent_event_ignored event=%s", name)

    # ----- handlers --------------------------------------------------------
    async def on_session_miss(self, account: str) -> None:
        # Cache-aside fallback (§3.3): Postgres -> Redis -> send_cmd session.
        session: AgentSession | None = None
        if self.session_loader is not None:
            session = await self.session_loader(account)
        if session is None:
            logger.warning("agent_session_miss_unresolved account=%s", account)
            return
        await self._cache_session(account, session)
        await self.send_cmd(
            {
                "cmd": "session",
                "account": account,
                "access_token": session.access_token,
                "cookie_header": session.cookie_header,
            }
        )

    async def on_order_detected(self, account: str, event: dict[str, Any]) -> None:
        rt = self._runtime(account)
        rt.orders_seen += 1
        await self._redis_incr(k_stat_orders_seen(account))

    async def on_take_sent(self, account: str, event: dict[str, Any]) -> None:
        self._runtime(account).takes += 1

    async def on_take_result(self, account: str, event: dict[str, Any]) -> None:
        status = _as_int(event.get("status"))
        http_ms = _as_float(event.get("http_ms"))
        payment_id = _as_int(event.get("payment_id"))
        order_id = str(event.get("order", ""))
        await self._redis_incr(k_stat_takes(account))
        if http_ms is not None and self.redis is not None:
            try:
                pipe = self.redis.pipeline(transaction=True)
                pipe.lpush(k_stat_http_ms(account), http_ms)
                pipe.ltrim(k_stat_http_ms(account), 0, self.http_ms_max_samples - 1)
                await pipe.execute()
            except Exception as exc:  # noqa: BLE001
                logger.warning("agent_redis_http_ms_failed error=%s", type(exc).__name__)
        await self._record_event(
            account, order_id, KIND_TAKE_RESULT, status=status, http_ms=http_ms, payment_id=payment_id
        )

    async def on_claim_won(self, account: str, event: dict[str, Any]) -> None:
        order_id = str(event.get("order", ""))
        payment_id = _as_int(event.get("payment_id"))
        rt = self._runtime(account)
        rt.wins += 1
        await self._redis_incr(k_stat_wins(account))
        await self._record_event(
            account, order_id, KIND_CLAIM_WON, status=None, http_ms=None, payment_id=payment_id
        )
        # Post-processing (§5.4): confirm/complete via the platform client.
        if self.confirm_win is not None and payment_id is not None:
            try:
                await self.confirm_win(account, payment_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("agent_confirm_win_failed account=%s error=%s", account, exc)
        # Telegram notification hook (never hard-depended on).
        if self.win_notifier is not None:
            try:
                await self.win_notifier(
                    {"account": account, "order": order_id, "payment_id": payment_id}
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("agent_win_notify_failed error=%s", type(exc).__name__)

    async def on_claim_lost(self, account: str, event: dict[str, Any]) -> None:
        order_id = str(event.get("order", ""))
        status = _as_int(event.get("status"))
        await self._record_event(
            account, order_id, KIND_CLAIM_LOST, status=status, http_ms=None, payment_id=None
        )

    async def on_heartbeat(self, event: dict[str, Any]) -> None:
        per_account = event.get("per_account")
        if not isinstance(per_account, list):
            return
        now = datetime.now(UTC).timestamp()
        for item in per_account:
            if not isinstance(item, dict):
                continue
            account = str(item.get("account", ""))
            if not account:
                continue
            rt = self._runtime(account)
            rt.orders_seen = _as_int(item.get("orders_seen")) or rt.orders_seen
            rt.takes = _as_int(item.get("takes")) or rt.takes
            rt.wins = _as_int(item.get("wins")) or rt.wins
            rt.last_heartbeat_ts = now

    # ----- supervisor-side cache writes (single writer, §3.4) --------------
    async def cache_session(self, account: str, session: AgentSession) -> None:
        await self._cache_session(account, session)

    async def cache_account_config(
        self, account: str, *, label: str, filters: dict[str, Any], enabled: bool, ttl: int = 1800
    ) -> None:
        if self.redis is None:
            return
        try:
            payload = json.dumps({"label": label, "filters": filters, "enabled": enabled})
            await self.redis.set(k_account(account), payload, ex=ttl)
            if enabled:
                await self.redis.sadd(KEY_ACCOUNTS_ENABLED, account)
            else:
                await self.redis.srem(KEY_ACCOUNTS_ENABLED, account)
            await self.redis.expire(KEY_ACCOUNTS_ENABLED, ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_cache_account_failed error=%s", type(exc).__name__)

    async def remove_account_from_enabled(self, account: str) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.srem(KEY_ACCOUNTS_ENABLED, account)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_srem_failed error=%s", type(exc).__name__)

    async def _cache_session(self, account: str, session: AgentSession) -> None:
        if self.redis is None:
            return
        try:
            payload = json.dumps(
                {
                    "access_token": session.access_token,
                    "cookie_header": session.cookie_header,
                    "did": session.did,
                    "expires_at": session.expires_at,
                }
            )
            await self.redis.set(k_session(account), payload, ex=self.session_ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_cache_session_failed error=%s", type(exc).__name__)

    # ----- helpers ---------------------------------------------------------
    def _runtime(self, account: str) -> AccountRuntime:
        rt = self.accounts.get(account)
        if rt is None:
            rt = AccountRuntime(account_id=account)
            self.accounts[account] = rt
        return rt

    async def _redis_incr(self, key: str) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.incr(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_redis_incr_failed key=%s error=%s", key, type(exc).__name__)

    async def _record_event(
        self,
        account: str,
        order_id: str,
        kind: str,
        *,
        status: int | None,
        http_ms: float | None,
        payment_id: int | None,
    ) -> None:
        if self.stats_repo is None:
            return
        try:
            await self.stats_repo.record_event(
                OrderEvent(
                    account_id=account,
                    order_id=order_id,
                    kind=kind,
                    status=status,
                    http_ms=http_ms,
                    payment_id=payment_id,
                    ts=datetime.now(UTC),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_record_event_failed kind=%s error=%s", kind, type(exc).__name__)

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "accounts": [
                {
                    "account": rt.account_id,
                    "ws_connected": rt.ws_connected,
                    "mode": rt.mode,
                    "orders_seen": rt.orders_seen,
                    "takes": rt.takes,
                    "wins": rt.wins,
                }
                for rt in self.accounts.values()
            ],
        }


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
