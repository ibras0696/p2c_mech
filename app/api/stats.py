from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from app.core.logging import get_logger
from app.services.agent_registry import get_stats_redis, get_stats_repo
from app.services.agent_supervisor import (
    k_stat_http_ms,
    k_stat_orders_seen,
    k_stat_takes,
    k_stat_wins,
)

logger = get_logger(__name__)

# Stats path is fully decoupled from the supervisor (§6.2): it reads only Redis
# counters and the Postgres history, so the button works even with the agent
# stopped. It MUST NOT call the supervisor.
router = APIRouter(tags=["stats"])


def _period_since(period: str | None) -> datetime | None:
    now = datetime.now(UTC)
    if period in (None, "", "all"):
        return None
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "hour":
        return now - timedelta(hours=1)
    if period == "week":
        return now - timedelta(days=7)
    return None


async def _redis_counter(redis: Any, key: str) -> int:
    if redis is None:
        return 0
    try:
        value = await redis.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stats_redis_get_failed key=%s error=%s", key, type(exc).__name__)
        return 0
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@router.get("/stats")
async def get_stats(
    account: str | None = Query(default=None),
    period: str | None = Query(default=None),
) -> dict[str, Any]:
    redis = get_stats_redis()
    repo = get_stats_repo()
    since = _period_since(period)

    # Fast "now" counters from Redis (only meaningful for a specific account key).
    live: dict[str, int] = {}
    if account:
        live = {
            "takes": await _redis_counter(redis, k_stat_takes(account)),
            "wins": await _redis_counter(redis, k_stat_wins(account)),
            "orders_seen": await _redis_counter(redis, k_stat_orders_seen(account)),
        }

    aggregate: dict[str, Any] = {
        "takes": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "p50_http_ms": None,
        "p99_http_ms": None,
        "recent": [],
    }
    if repo is not None:
        agg = await repo.aggregate(account_id=account, since=since)
        total_decided = agg.wins + agg.losses
        aggregate = {
            "takes": agg.takes,
            "wins": agg.wins,
            "losses": agg.losses,
            "win_rate": (agg.wins / total_decided) if total_decided else 0.0,
            "p50_http_ms": agg.p50_http_ms,
            "p99_http_ms": agg.p99_http_ms,
            "recent": [
                {
                    "account": e.account_id,
                    "order": e.order_id,
                    "kind": e.kind,
                    "status": e.status,
                    "http_ms": e.http_ms,
                    "payment_id": e.payment_id,
                    "ts": e.ts.isoformat(),
                }
                for e in agg.recent
            ],
        }

    return {
        "account": account,
        "period": period or "all",
        "live": live,
        "aggregate": aggregate,
    }


@router.post("/stats/reset")
async def reset_stats(account: str | None = Query(default=None)) -> dict[str, Any]:
    """Wipe stats: Redis live counters + Postgres order_events history.

    account=None resets everything; otherwise only that account's data.
    """
    redis = get_stats_redis()
    repo = get_stats_repo()
    if redis is not None and account:
        try:
            await redis.delete(
                k_stat_takes(account),
                k_stat_wins(account),
                k_stat_orders_seen(account),
                k_stat_http_ms(account),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("stats_reset_redis_failed error=%s", type(exc).__name__)
    if repo is not None:
        try:
            await repo.reset(account_id=account)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stats_reset_repo_failed error=%s", type(exc).__name__)
    logger.info("event=stats_reset account=%s", account)
    return {"ok": True, "account": account}
