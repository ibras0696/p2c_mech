# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run a single test file
pytest tests/test_agent_runtime_manager.py

# Run a single test
pytest tests/test_agent_runtime_manager.py::test_name

# Lint
ruff check app tests

# Format
ruff format app tests

# Type check
mypy app

# Start full stack
docker compose up -d --build

# Start with browser worker
docker compose --profile browser up -d --build

# Start with socket worker
docker compose --profile socket up -d --build
```

## Architecture

The system is a **multitenant P2C automation agent** with a Telegram control plane. Access model is strict personal: `1 admin = 1 runtime agent = 1 platform session`.

### Process model

Three independently-deployed processes share one Docker image:

| Process | Entry point | Role |
|---|---|---|
| `app` | `uvicorn app.main:app` | FastAPI — health + ops endpoints |
| `bot` | `python -m app.bot.runner` | Telegram control plane (aiogram 3) |
| `browser-worker` | `python -m app.workers.browser` | Playwright fallback (profile `browser`) |
| `p2c-socket-worker` | `python -m app.workers.p2c_socket` | Standalone WS worker (profile `socket`) |

### Layer dependency

```
api / bot / workers
    → services (use-case orchestration)
        → domain (AgentMode, AgentSnapshot, ActiveOrder — no external deps)
        → repositories (asyncpg + Redis adapters)
        → integrations (HTTP API client, WebSocket client)
```

Never import FastAPI or aiogram into domain or services; never put business logic into handlers.

### Key runtime classes

- **`InMemoryAgentState`** (`app/bot/state.py`) — per-user agent state machine. Modes: `WAITING`, `PAUSED`, `CAPACITY_REACHED`, `CAPTCHA_REQUIRED`, `ERROR`. Thread-safe mutations via synchronous methods that return `AgentSnapshot`.
- **`P2CLiveAgent`** (`app/services/p2c_live_agent.py`) — long-running coroutine per user. Holds a WebSocket connection to the platform, claims orders, calls `notify_order_ready` callback to push Telegram messages.
- **`AgentRuntimeManager`** (`app/services/agent_runtime_manager.py`) — singleton that owns the `dict[user_id → UserRuntime]`. Creates/hydrates runtimes on first use; runs an idle-cleanup loop (paused + no active orders + TTL exceeded). Scoped repository wrappers (`ScopedPlatformSessionRepository`, `ScopedActiveOrderRepository`) enforce per-user data isolation.
- **`AdminAccessService`** (`app/services/admin_access.py`) — bootstraps owners from `TELEGRAM_ADMIN_IDS`; handles `/admin_add`, `/admin_rm`, `/admin_list`.

### Session security

`PlatformSession` (access_token + __cf_bm cookie) is encrypted with `SESSION_ENCRYPTION_KEY` before writing to PostgreSQL. Passed into the bot via raw cURL snippet; `parse_platform_session_from_text` in `app/bot/session_state.py` extracts cookies with regex. If the key is lost, sessions cannot be decrypted — the admin must re-submit cookies via the bot.

### Repositories

All repositories have an `InMemory*` implementation used in tests and a concrete implementation returned by `build_*` factory functions that switch between asyncpg/Redis based on configured URLs.

### Bot structure

- `app/bot/router.py` — wires all handlers into an aiogram `Router`
- `app/bot/handlers/` — one file per scenario (actions, admin, filters, limits, orders, panel, session)
- `app/bot/ui/` — keyboards, label strings, renderers; no business logic

### Configuration

`app/core/config.py` — single `Settings` (pydantic-settings). Loaded once via `get_settings()` (lru_cache). The bot and app fail fast at startup if `TELEGRAM_BOT_TOKEN` or `TELEGRAM_ADMIN_IDS` are missing.

## Environment variables

Copy `.env.example` to `.env`. Key vars:

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS` — required for bot
- `SESSION_ENCRYPTION_KEY` — required; used to encrypt platform sessions at rest
- `DATABASE_URL` — overrides individual `POSTGRES_*` vars
- `REDIS_URL` — overrides individual `REDIS_*` vars
- `PLATFORM_BASE_URL`, `PLATFORM_WS_URL` — P2C platform endpoints
- `RUNTIME_IDLE_TTL_SECONDS`, `RUNTIME_CLEANUP_INTERVAL_SECONDS` — runtime GC tuning
