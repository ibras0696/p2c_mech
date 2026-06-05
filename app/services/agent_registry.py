from __future__ import annotations

from typing import Any

from app.repositories.agent_stats_repo import AgentStatsRepository
from app.services.agent_supervisor import AgentSupervisor

# Process-wide singletons wired at startup (mirrors runtime_registry.py).
# The supervisor is optional: /stats reads only the stats repo + redis so it
# works even when the agent is stopped or never spawned (§6.2).
_supervisor: AgentSupervisor | None = None
_stats_repo: AgentStatsRepository | None = None
_redis: Any = None


def set_supervisor(supervisor: AgentSupervisor | None) -> None:
    global _supervisor
    _supervisor = supervisor


def get_supervisor() -> AgentSupervisor | None:
    return _supervisor


def set_stats_repo(repo: AgentStatsRepository | None) -> None:
    global _stats_repo
    _stats_repo = repo


def get_stats_repo() -> AgentStatsRepository | None:
    return _stats_repo


def set_stats_redis(client: Any) -> None:
    global _redis
    _redis = client


def get_stats_redis() -> Any:
    return _redis


def clear_agent_registry() -> None:
    global _supervisor, _stats_repo, _redis
    _supervisor = None
    _stats_repo = None
    _redis = None
