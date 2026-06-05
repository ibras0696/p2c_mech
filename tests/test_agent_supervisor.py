from __future__ import annotations

import json
from typing import Any

import pytest
from app.repositories.agent_stats_repo import (
    KIND_CLAIM_LOST,
    KIND_CLAIM_WON,
    KIND_TAKE_RESULT,
    InMemoryAgentStatsRepository,
)
from app.services.agent_supervisor import (
    AgentSession,
    AgentSupervisor,
    k_stat_http_ms,
    k_stat_orders_seen,
    k_stat_takes,
    k_stat_wins,
)


class FakeRedis:
    """Minimal async Redis double covering the ops the supervisor uses."""

    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.lists: dict[str, list[Any]] = {}
        self.sets: dict[str, set[str]] = {}

    async def incr(self, key: str) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return self.kv[key]

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self.kv[key] = value

    async def sadd(self, key: str, member: str) -> None:
        self.sets.setdefault(key, set()).add(member)

    async def srem(self, key: str, member: str) -> None:
        self.sets.setdefault(key, set()).discard(member)

    async def expire(self, key: str, ttl: int) -> None:
        return None

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    def lpush(self, key: str, value: Any) -> FakePipeline:
        self._ops.append(("lpush", (key, value)))
        return self

    def ltrim(self, key: str, start: int, stop: int) -> FakePipeline:
        self._ops.append(("ltrim", (key, start, stop)))
        return self

    def hset(self, *args: Any, **kwargs: Any) -> FakePipeline:
        return self

    async def execute(self) -> None:
        for op, args in self._ops:
            if op == "lpush":
                key, value = args
                self._redis.lists.setdefault(key, []).insert(0, value)
            elif op == "ltrim":
                key, start, stop = args
                self._redis.lists[key] = self._redis.lists.get(key, [])[start : stop + 1]


def _make_supervisor(redis: FakeRedis, stats: InMemoryAgentStatsRepository, **kw: Any) -> AgentSupervisor:
    return AgentSupervisor(
        agent_bin="p2c_agent",
        ws_url="wss://app.send.tg/socket.io/?EIO=4&transport=websocket",
        base_url="https://app.send.tg",
        origin="https://app.send.tg",
        redis_url="redis://127.0.0.1:6379/0",
        redis=redis,
        stats_repo=stats,
        **kw,
    )


class FakeStream:
    """Feeds pre-canned lines to _read_stdout / _read_stderr."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


@pytest.mark.asyncio
async def test_stdout_dispatch_take_result_increments_and_records() -> None:
    redis = FakeRedis()
    stats = InMemoryAgentStatsRepository()
    sup = _make_supervisor(redis, stats)

    lines = [
        json.dumps({"event": "ws_connected", "account": "acc1"}).encode() + b"\n",
        json.dumps({"event": "order_detected", "account": "acc1", "order": "o1"}).encode() + b"\n",
        json.dumps({"event": "take_sent", "account": "acc1", "order": "o1"}).encode() + b"\n",
        json.dumps(
            {"event": "take_result", "account": "acc1", "order": "o1", "status": 200, "http_ms": 92, "payment_id": 7}
        ).encode()
        + b"\n",
    ]
    await sup._read_stdout(FakeStream(lines))  # type: ignore[arg-type]

    assert sup.accounts["acc1"].ws_connected is True
    assert redis.kv[k_stat_orders_seen("acc1")] == 1
    assert redis.kv[k_stat_takes("acc1")] == 1
    assert redis.lists[k_stat_http_ms("acc1")] == [92.0]
    take_events = [e for e in stats._events if e.kind == KIND_TAKE_RESULT]
    assert len(take_events) == 1
    assert take_events[0].status == 200
    assert take_events[0].http_ms == 92.0
    assert take_events[0].payment_id == 7


@pytest.mark.asyncio
async def test_stdout_dispatch_claim_won_calls_confirm_and_notifier() -> None:
    redis = FakeRedis()
    stats = InMemoryAgentStatsRepository()
    confirmed: list[tuple[str, int]] = []
    notified: list[dict[str, Any]] = []

    async def confirm(account: str, payment_id: int) -> None:
        confirmed.append((account, payment_id))

    async def notify(payload: dict[str, Any]) -> None:
        notified.append(payload)

    sup = _make_supervisor(redis, stats, confirm_win=confirm, win_notifier=notify)
    line = json.dumps(
        {"event": "claim_won", "account": "acc1", "order": "o1", "payment_id": 123}
    ).encode() + b"\n"
    await sup._read_stdout(FakeStream([line]))  # type: ignore[arg-type]

    assert redis.kv[k_stat_wins("acc1")] == 1
    assert confirmed == [("acc1", 123)]
    assert notified == [{"account": "acc1", "order": "o1", "payment_id": 123}]
    assert any(e.kind == KIND_CLAIM_WON for e in stats._events)


@pytest.mark.asyncio
async def test_stdout_dispatch_claim_lost_recorded() -> None:
    redis = FakeRedis()
    stats = InMemoryAgentStatsRepository()
    sup = _make_supervisor(redis, stats)
    line = json.dumps(
        {"event": "claim_lost", "account": "acc1", "order": "o1", "status": 400, "reason": "InvalidStatus"}
    ).encode() + b"\n"
    await sup._read_stdout(FakeStream([line]))  # type: ignore[arg-type]
    lost = [e for e in stats._events if e.kind == KIND_CLAIM_LOST]
    assert len(lost) == 1
    assert lost[0].status == 400


@pytest.mark.asyncio
async def test_session_miss_triggers_cache_aside_and_session_cmd() -> None:
    redis = FakeRedis()
    stats = InMemoryAgentStatsRepository()
    sent: list[dict[str, Any]] = []

    async def loader(account: str) -> AgentSession:
        return AgentSession(access_token="tok", cookie_header="access_token=tok; __cf_bm=x", did="d")

    sup = _make_supervisor(redis, stats, session_loader=loader)

    async def fake_send(cmd: dict[str, Any]) -> None:
        sent.append(cmd)

    sup.send_cmd = fake_send  # type: ignore[assignment]
    line = json.dumps({"event": "session_miss", "account": "acc1"}).encode() + b"\n"
    await sup._read_stdout(FakeStream([line]))  # type: ignore[arg-type]

    cached = json.loads(redis.kv["p2c:session:acc1"])
    assert cached["access_token"] == "tok"
    assert cached["did"] == "d"
    assert sent == [
        {
            "cmd": "session",
            "account": "acc1",
            "access_token": "tok",
            "cookie_header": "access_token=tok; __cf_bm=x",
        }
    ]


@pytest.mark.asyncio
async def test_malformed_lines_are_ignored() -> None:
    redis = FakeRedis()
    stats = InMemoryAgentStatsRepository()
    sup = _make_supervisor(redis, stats)
    lines = [
        b"not json\n",
        json.dumps([1, 2, 3]).encode() + b"\n",  # JSON but not an object
        json.dumps({"event": "order_detected", "account": "acc1"}).encode() + b"\n",
    ]
    await sup._read_stdout(FakeStream(lines))  # type: ignore[arg-type]
    assert redis.kv[k_stat_orders_seen("acc1")] == 1


@pytest.mark.asyncio
async def test_heartbeat_updates_runtime_counters() -> None:
    redis = FakeRedis()
    stats = InMemoryAgentStatsRepository()
    sup = _make_supervisor(redis, stats)
    line = json.dumps(
        {
            "event": "heartbeat",
            "per_account": [{"account": "acc1", "orders_seen": 10, "takes": 4, "wins": 1}],
        }
    ).encode() + b"\n"
    await sup._read_stdout(FakeStream([line]))  # type: ignore[arg-type]
    rt = sup.accounts["acc1"]
    assert rt.orders_seen == 10
    assert rt.takes == 4
    assert rt.wins == 1
    assert rt.last_heartbeat_ts is not None


# ----- command serialization (must match agent.h field names) ---------------
class WriterProc:
    """Captures bytes written to stdin."""

    def __init__(self) -> None:
        self.stdin = self
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
async def test_send_cmd_writes_ndjson_line() -> None:
    redis = FakeRedis()
    stats = InMemoryAgentStatsRepository()
    sup = _make_supervisor(redis, stats)
    proc = WriterProc()
    sup._proc = proc  # type: ignore[assignment]

    await sup.send_cmd(
        {
            "cmd": "add_account",
            "account": "acc1",
            "access_token": "t",
            "cookie_header": "c; __cf_bm=z",
            "filters": {"min_amount": 100, "max_amount": 50000, "currencies": ["RUB"]},
        }
    )
    assert len(proc.written) == 1
    raw = proc.written[0]
    assert raw.endswith(b"\n")
    parsed = json.loads(raw)
    # Field names per c_agent/include/agent.h §4.6.
    assert parsed["cmd"] == "add_account"
    assert parsed["account"] == "acc1"
    assert parsed["access_token"] == "t"
    assert parsed["cookie_header"] == "c; __cf_bm=z"
    assert parsed["filters"] == {"min_amount": 100, "max_amount": 50000, "currencies": ["RUB"]}


@pytest.mark.asyncio
async def test_send_cmd_dropped_when_not_running() -> None:
    redis = FakeRedis()
    stats = InMemoryAgentStatsRepository()
    sup = _make_supervisor(redis, stats)
    # _proc is None -> should not raise, just drop.
    await sup.send_cmd({"cmd": "shutdown"})


@pytest.mark.asyncio
async def test_spawn_handles_missing_binary() -> None:
    redis = FakeRedis()
    stats = InMemoryAgentStatsRepository()
    sup = _make_supervisor(redis, stats)
    sup.agent_bin = "definitely_not_a_real_binary_xyz_123"
    started = await sup.spawn()
    assert started is False
    assert sup.running is False
