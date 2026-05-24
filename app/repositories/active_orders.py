from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC

import asyncpg  # type: ignore[import-untyped]

from app.bot.state import ActiveOrder, OrderStatus


class ActiveOrderRepository(ABC):
    @abstractmethod
    async def upsert(self, order: ActiveOrder) -> None:
        raise NotImplementedError

    @abstractmethod
    async def remove(
        self,
        order_id: str,
        *,
        final_status: str = OrderStatus.PAID.value,
        reason: str = "",
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_all(self) -> list[ActiveOrder]:
        raise NotImplementedError


class InMemoryActiveOrderRepository(ActiveOrderRepository):
    def __init__(self) -> None:
        self._orders: dict[str, ActiveOrder] = {}
        self._closed_ids: set[str] = set()

    async def upsert(self, order: ActiveOrder) -> None:
        self._orders[order.id] = order
        self._closed_ids.discard(order.id)

    async def remove(
        self,
        order_id: str,
        *,
        final_status: str = OrderStatus.PAID.value,
        reason: str = "",
    ) -> None:
        del reason
        order = self._orders.get(order_id)
        if order is None:
            return
        if final_status in {OrderStatus.PAID.value, "completed", "complete", "success"}:
            order.status = OrderStatus.PAID
        elif final_status in {OrderStatus.CANCELLED.value, "cancelled", "canceled"}:
            order.status = OrderStatus.CANCELLED
        self._closed_ids.add(order_id)

    async def list_all(self) -> list[ActiveOrder]:
        return [order for order_id, order in self._orders.items() if order_id not in self._closed_ids]


class PostgresActiveOrderRepository(ActiveOrderRepository):
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def upsert(self, order: ActiveOrder) -> None:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        await pool.execute(
            """
            insert into active_orders (
                order_id,
                amount,
                currency,
                direction,
                url,
                provider,
                payload,
                method_id,
                source_order_id,
                status,
                take_http_ms,
                claim_total_ms,
                claimed_at,
                deadline_at,
                closed_at,
                close_reason
            )
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,null,'')
            on conflict (order_id) do update set
                amount = excluded.amount,
                currency = excluded.currency,
                direction = excluded.direction,
                url = excluded.url,
                provider = excluded.provider,
                payload = excluded.payload,
                method_id = excluded.method_id,
                source_order_id = excluded.source_order_id,
                status = excluded.status,
                take_http_ms = excluded.take_http_ms,
                claim_total_ms = excluded.claim_total_ms,
                claimed_at = excluded.claimed_at,
                deadline_at = excluded.deadline_at,
                closed_at = null,
                close_reason = ''
            """,
            order.id,
            order.amount,
            order.currency,
            order.direction,
            order.url,
            order.provider,
            order.payload,
            order.method_id,
            order.source_order_id,
            order.status.value,
            order.take_http_ms,
            order.claim_total_ms,
            order.claimed_at,
            order.deadline_at,
        )

    async def remove(
        self,
        order_id: str,
        *,
        final_status: str = OrderStatus.PAID.value,
        reason: str = "",
    ) -> None:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        await pool.execute(
            """
            update active_orders
            set status = $2,
                closed_at = now(),
                close_reason = $3
            where order_id = $1
            """,
            order_id,
            final_status,
            reason,
        )

    async def list_all(self) -> list[ActiveOrder]:
        pool = await self._get_pool()
        await self._ensure_schema(pool)
        rows = await pool.fetch(
            """
            select
                order_id, amount, currency, direction, url, provider, payload,
                method_id, source_order_id, status, take_http_ms, claim_total_ms,
                claimed_at, deadline_at
            from active_orders
            where closed_at is null
            order by claimed_at asc
            """
        )
        result: list[ActiveOrder] = []
        for row in rows:
            claimed_at = row["claimed_at"]
            deadline_at = row["deadline_at"]
            if claimed_at is not None and claimed_at.tzinfo is None:
                claimed_at = claimed_at.replace(tzinfo=UTC)
            if deadline_at is not None and deadline_at.tzinfo is None:
                deadline_at = deadline_at.replace(tzinfo=UTC)
            status_raw = str(row["status"])
            try:
                status = OrderStatus(status_raw)
            except ValueError:
                status = OrderStatus.IN_PROGRESS
            result.append(
                ActiveOrder(
                    id=row["order_id"],
                    amount=row["amount"],
                    currency=row["currency"],
                    direction=row["direction"],
                    url=row["url"] or "",
                    provider=row["provider"] or "",
                    payload=row["payload"] or "",
                    method_id=row["method_id"] or "",
                    source_order_id=row["source_order_id"] or "",
                    status=status,
                    take_http_ms=row["take_http_ms"],
                    claim_total_ms=row["claim_total_ms"],
                    claimed_at=claimed_at,
                    deadline_at=deadline_at,
                )
            )
        return result

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=3)
        return self._pool

    async def _ensure_schema(self, pool: asyncpg.Pool) -> None:
        await pool.execute(
            """
            create table if not exists active_orders (
                order_id text primary key,
                amount text not null,
                currency text not null,
                direction text not null,
                url text not null default '',
                provider text not null default '',
                payload text not null default '',
                method_id text not null default '',
                source_order_id text not null default '',
                status text not null,
                take_http_ms integer null,
                claim_total_ms integer null,
                claimed_at timestamptz not null,
                deadline_at timestamptz null,
                closed_at timestamptz null,
                close_reason text not null default ''
            )
            """
        )
        await pool.execute(
            """
            alter table active_orders
            add column if not exists closed_at timestamptz null
            """
        )
        await pool.execute(
            """
            alter table active_orders
            add column if not exists close_reason text not null default ''
            """
        )


def build_active_order_repository(*, database_url: str) -> ActiveOrderRepository:
    if database_url:
        return PostgresActiveOrderRepository(database_url)
    return InMemoryActiveOrderRepository()
