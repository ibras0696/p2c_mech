from __future__ import annotations

from datetime import UTC, datetime

from app.bot.state import agent_state
from app.core.logging import get_logger
from app.repositories.agent_preferences import AgentPreferences, AgentPreferencesRepository

logger = get_logger(__name__)


async def apply_user_preferences(user_id: int, repository: AgentPreferencesRepository) -> None:
    try:
        preferences = await repository.current_for_user(user_id)
    except Exception as exc:
        logger.warning("bot_preferences_load_failed user_id=%s error=%s", user_id, type(exc).__name__)
        return
    if preferences is None:
        return
    agent_state.set_limit(preferences.active_limit)
    agent_state.set_amount_filter(preferences.min_amount, preferences.max_amount)


async def persist_current_preferences(user_id: int, repository: AgentPreferencesRepository) -> None:
    snapshot = agent_state.snapshot()
    try:
        await repository.save_for_user(
            user_id,
            AgentPreferences(
                active_limit=snapshot.active_limit,
                min_amount=snapshot.min_amount,
                max_amount=snapshot.max_amount,
                updated_at=datetime.now(UTC),
            ),
        )
    except Exception as exc:
        logger.warning("bot_preferences_persist_failed user_id=%s error=%s", user_id, type(exc).__name__)
