from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg  # type: ignore[import-untyped]

# Durable order history (docs/C_AGENT_SPEC_RU.md §6.1). Stats are read from here
# (plus Redis counters) and MUST work even when the C-agent is stopped (§6.2),
# so this repository never talks to the supervisor.

# Event kinds we persist (mirror the stdout NDJSON "event" names of events.h).
KIND_TAKE_RESULT = "take_result"
KIND_CLAIM_WON = "claim_won"
KIND_CLAIM_LOST = "claim_lost"


@dataclass(frozen=True)
class OrderEvent:
    account_id: str
    order_id: str
    kind: str
    status: int | None
    http_ms: float | None
    payment_id: int | None
    ts: datetime


@dataclass(frozen=True)
class StatsAggregate:
    account_id: str | None
    orders_seen: int
    takes: int
    wins: int
    losses: int
    p50_http_ms: float | None
    p99_http_ms: float | None
    recent: list[OrderEvent]


class AgentStatsRepository(ABC):
    @abstractmethod
    async def record_event(self, event: OrderEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    async def aggregate(
        self, *, account_id: str | None, since: datetime | None, recent_limit: int = 20
    ) -> StatsAggregate:
        raise NotImplementedError


class InMemoryAgentStatsRepository(AgentStatsRepository):
    """Fallback used when DATABASE_URL is unset (e.g. tests, local dev)."""

    def __init__(self) -> None:
        self._events: list[OrderEvent] = []

    async def record_event(self, event: OrderEvent) -> None:
        self._events.append(event)

    async def aggregate(
        self, *, account_id: str | None, since: datetime | None, recent_limit: int = 20
    ) -> StatsAggregate:
        rows = [
            e
            for e in self._events
            if (account_id is None or e.account_id == account_id)
            and (since is None or e.ts >= since)
        ]
        takes = sum(1 for e in rows if e.kind == KIND_TAKE_RESULT)
        wins = sum(1 for e in rows if e.kind == KIND_CLAIM_WON)
        losses = sum(1 for e in rows if e.kind == KIND_CLAIM_LOST)
        latencies = sorted(e.http_ms for e in rows if e.http_ms is not None)
        recent = sorted(rows, key=lambda e: e.ts, reverse=True)[:recent_limit]
        return StatsAggregate(
            account_id=account_id,
            orders_seen=0,
            takes=takes,
            wins=wins,
            losses=losses,
            p50_http_ms=_percentile(latencies, 0.50),
            p99_http_ms=_percentile(latencies, 0.99),
            recent=recent,
        )


class PostgresAgentStatsRepository(AgentStatsRepository):
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None
        self._schema_ready = False

    async def record_event(self, event: OrderEvent) -> None:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        await pool.execute(
            """
            insert into order_events (
                account_id, order_id, kind, status, http_ms, payment_id, ts
            )
            values ($1, $2, $3, $4, $5, $6, $7)
            """,
            event.account_id,
            event.order_id,
            event.kind,
            event.status,
            event.http_ms,
            event.payment_id,
            event.ts,
        )

    async def aggregate(
        self, *, account_id: str | None, since: datetime | None, recent_limit: int = 20
    ) -> StatsAggregate:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        where: list[str] = []
        args: list[Any] = []
        if account_id is not None:
            args.append(account_id)
            where.append(f"account_id = ${len(args)}")
        if since is not None:
            args.append(since)
            where.append(f"ts >= ${len(args)}")
        clause = (" where " + " and ".join(where)) if where else ""

        summary = await pool.fetchrow(
            f"""
            select
                count(*) filter (where kind = '{KIND_TAKE_RESULT}') as takes,
                count(*) filter (where kind = '{KIND_CLAIM_WON}') as wins,
                count(*) filter (where kind = '{KIND_CLAIM_LOST}') as losses,
                percentile_cont(0.50) within group (order by http_ms)
                    filter (where http_ms is not null) as p50,
                percentile_cont(0.99) within group (order by http_ms)
                    filter (where http_ms is not null) as p99
            from order_events{clause}
            """,
            *args,
        )
        recent_rows = await pool.fetch(
            f"""
            select account_id, order_id, kind, status, http_ms, payment_id, ts
            from order_events{clause}
            order by ts desc
            limit {int(recent_limit)}
            """,
            *args,
        )
        recent = [
            OrderEvent(
                account_id=r["account_id"],
                order_id=r["order_id"],
                kind=r["kind"],
                status=r["status"],
                http_ms=r["http_ms"],
                payment_id=r["payment_id"],
                ts=_as_utc(r["ts"]),
            )
            for r in recent_rows
        ]
        return StatsAggregate(
            account_id=account_id,
            orders_seen=0,
            takes=int(summary["takes"] or 0),
            wins=int(summary["wins"] or 0),
            losses=int(summary["losses"] or 0),
            p50_http_ms=_as_float(summary["p50"]),
            p99_http_ms=_as_float(summary["p99"]),
            recent=recent,
        )

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=3)
        return self._pool

    async def _ensure_schema(self, pool: asyncpg.Pool) -> None:
        if self._schema_ready:
            return
        await pool.execute(
            """
            create table if not exists order_events (
                id bigserial primary key,
                account_id text not null,
                order_id text not null,
                kind text not null,
                status integer,
                http_ms double precision,
                payment_id bigint,
                ts timestamptz not null default now()
            )
            """
        )
        await pool.execute(
            """
            create index if not exists idx_order_events_account_ts
            on order_events (account_id, ts desc)
            """
        )
        self._schema_ready = True


def build_agent_stats_repository(*, database_url: str) -> AgentStatsRepository:
    if database_url:
        return PostgresAgentStatsRepository(database_url)
    return InMemoryAgentStatsRepository()


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
