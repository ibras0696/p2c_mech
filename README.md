# p2c_mech

Multitenant P2C agent with Telegram control plane.

Access model is strict personal: `1 admin = 1 runtime agent = 1 platform session`.

## Stack

- Python 3.12
- FastAPI
- Aiogram 3
- PostgreSQL
- Redis
- Docker Compose

## Quick start

```bash
docker compose up -d --build
```

## Access model

- Owners are bootstrapped from `TELEGRAM_ADMIN_IDS`.
- Owner-only bot commands:
  - `/admin_add <id>`
  - `/admin_rm <id>`
  - `/admin_list`
  - `/runtime_list`
- Regular admins can operate only their own runtime/session/orders.

## Runtime health endpoints

- `GET /health` basic liveness.
- `GET /health/runtime` runtime snapshot for ops (internal perimeter only).

Example response:

```json
{
  "status": "ok",
  "runtime_count": 1,
  "runtime": [
    {
      "user_id": 1033560490,
      "mode": "waiting",
      "active_count": 0,
      "active_limit": 1,
      "free_slots": 1,
      "running": true,
      "last_used_at": "2026-05-27T10:10:00+00:00"
    }
  ]
}
```

## Important env vars

- `TELEGRAM_ADMIN_IDS`
- `RUNTIME_IDLE_TTL_SECONDS`
- `RUNTIME_CLEANUP_INTERVAL_SECONDS`
- `SESSION_CACHE_TTL_SECONDS`
- `PLATFORM_FORCE_IPV4`
- `PLATFORM_TAKE_HEALTH_ENABLED`
- `PLATFORM_TAKE_HEALTH_INTERVAL_SECONDS`

## Docker smoke checklist

1. `docker compose up -d --build`
2. `docker compose ps`
3. `curl http://localhost:8000/health`
4. `curl http://localhost:8000/health/runtime`
5. In Telegram as owner: `/admin_list`, `/runtime_list`
6. Add/remove admin: `/admin_add <id>`, `/admin_rm <id>`
7. Verify user isolation: run/pause and session updates for two users are independent.

## Documentation

- [Architecture and ToR](docs/TZ_AGENT_ARCHITECTURE_RU.md)
- [Bot UX](docs/TELEGRAM_BOT_UX_RU.md)
- [Project patterns](docs/PROJECT_PATTERNS_RU.md)
