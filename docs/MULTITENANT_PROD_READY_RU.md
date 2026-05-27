# Multitenant Production Notes

## Access and roles

- Model is strict personal: `1 admin = 1 runtime = 1 session`.
- Owner is bootstrapped from `TELEGRAM_ADMIN_IDS`.
- Owner-only bot commands:
  - `/admin_add <id>`
  - `/admin_rm <id>`
  - `/admin_list`
  - `/runtime_list`
- Admin can manage only own runtime/session/orders.

## Runtime health API

- `GET /health` returns global service liveness.
- `GET /health/runtime` returns runtime snapshots:
  - `user_id`
  - `mode`
  - `active_count`
  - `active_limit`
  - `running`
  - `last_used_at`

Endpoint is internal in v1 and must be protected by network perimeter rules.

## Runtime lifecycle

- Runtime is created lazily on first user interaction.
- Idle cleanup removes only runtime contexts that are:
  - paused,
  - with zero active orders,
  - idle longer than `RUNTIME_IDLE_TTL_SECONDS`.
- Cleanup runs every `RUNTIME_CLEANUP_INTERVAL_SECONDS`.

## Logging contract (critical paths)

Mandatory fields in critical events:

- `event`
- `user_id`
- `payment_id`
- `source_order_id`
- `latency_ms`

Hot-path rule:

- only short structured fields in `INFO` logs,
- no long payload dumps.

## Smoke runbook (Docker Compose)

1. `docker compose up -d --build`
2. `docker compose ps`
3. `curl http://localhost:8000/health`
4. `curl http://localhost:8000/health/runtime`
5. Telegram owner checks:
   - `/admin_list`
   - `/runtime_list`
6. Add/remove admin:
   - `/admin_add <id>`
   - `/admin_rm <id>`
7. Isolation check with two admins:
   - different sessions,
   - different limits/filters,
   - independent run/pause behavior.
