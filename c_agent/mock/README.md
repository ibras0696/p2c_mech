# Mock servers for the C P2C sniper agent

Two local servers that let you test the C agent end-to-end **without real
credentials or Cloudflare**. They speak the exact protocol the production
`app.send.tg` surface uses, so the same C client works unchanged against both
the mock and prod.

- `ws_server.py`   — Engine.IO v4 / Socket.IO order feed (mirrors
  `app/integrations/platform_ws/p2c_socket.py`, spec §4.3).
- `take_server.py` — HTTP claim endpoint with race simulation (mirrors
  `app/integrations/platform_api/p2c_payments.py`, spec §4.7 / §6.4).
- `run_all.py`     — runs both in one process for convenience.

All files are pure Python. The WS server uses the `websockets` library (already
a project dependency, requires the `.venv` with `websockets>=16`); the take
server is stdlib-only (`http.server`). No new dependencies.

## Run

Use the project virtualenv (it has the pinned `websockets>=16`):

```bash
# from the repo root /Users/ibragim/PycharmProjects/p2c_pro
.venv/bin/python -m c_agent.mock.ws_server      # WS feed   on :8081
.venv/bin/python -m c_agent.mock.take_server    # take HTTP on :8082
.venv/bin/python -m c_agent.mock.run_all        # both at once
```

Both servers log every significant event to **stderr** (client connected,
handshake steps, each order emitted with id, each take with id + result).

## WebSocket server (`ws_server`)

Path: `/socket.io/?EIO=4&transport=websocket` (path is not enforced; any path
is accepted).

Handshake / frame flow:

1. On connect → sends `0{"sid":...,"upgrades":[],"pingInterval":25000,"pingTimeout":20000,"maxPayload":1000000}`
2. Client sends `40` → server replies `40{"sid":...}`
3. Client sends `42["list:initialize"]` → server sends `42["list:snapshot",[]]`
   (optional) and starts the feed.
4. Server pushes `42["list:update",[{"op":"add","data":{...order...}}]]` on a
   timer.
5. Server sends Engine.IO ping `2` every `pingInterval`; client replies `3`
   (accepted and ignored). If the client pings, the server pongs back.

Order `data` shape (24 lowercase-hex id, amounts as **strings**):

```json
{"id":"6a1206db7440f5cd5e5c69c7","in_amount":"1110","in_asset":"RUB",
 "out_asset":"USDT","provider":"nspk","brand_name":"Bank","status":"new"}
```

### Env vars

| Var                | Default     | Meaning                                      |
|--------------------|-------------|----------------------------------------------|
| `WS_HOST`          | `0.0.0.0`   | listen host                                  |
| `WS_PORT`          | `8081`      | listen port                                  |
| `ORDER_INTERVAL_MS`| `1000`      | ms between order emits                       |
| `ORDER_BURST`      | `1`         | orders per emit                              |
| `AMOUNT_MIN`       | `500`       | min `in_amount` (integer)                    |
| `AMOUNT_MAX`       | `5000`      | max `in_amount` (integer)                    |
| `CURRENCIES`       | `RUB`       | comma list of `in_asset` values              |
| `SEND_SNAPSHOT`    | `true`      | emit an empty `list:snapshot` after init     |
| `PING_INTERVAL_MS` | `25000`     | Engine.IO ping interval                      |
| `PING_TIMEOUT_MS`  | `20000`     | `pingTimeout` advertised in the open packet  |

## Take server (`take_server`)

- `POST /internal/v1/p2c/payments/take/{order_id}` — any Cookie/headers
  accepted (no auth). Adds artificial latency (`TAKE_LATENCY_MS`, jittered
  ±20%).
  - **First** caller for an `order_id` → `200 {"payment_id": <int>, "status":"owned"}`
  - **Every subsequent** caller for the same id → `400 {"reason":"InvalidStatus"}`
  - `payment_id` increments from 1000. Claim set is thread-safe (the server is
    threaded), so concurrent racers resolve to exactly one winner.
- `GET /healthz` → `200 "ok"`

### Env vars

| Var               | Default   | Meaning                              |
|-------------------|-----------|--------------------------------------|
| `TAKE_HOST`       | `0.0.0.0` | listen host                          |
| `TAKE_PORT`       | `8082`    | listen port                          |
| `TAKE_LATENCY_MS` | `30`      | base latency ms, jittered ±20%       |

## Verification (observed)

WS handshake + first order, using a throwaway client:

```
RECV open: 0{"sid":"588c4f6d583e31bd","upgrades":[],"pingInterval":25000,"pingTimeout":20000,"maxPayload":1000000}
SENT 40
RECV connect: 40{"sid":"db4214410bfff107"}
SENT 42["list:initialize"]
RECV: 42["list:snapshot",[]]
RECV: 42["list:update",[{"op":"add","data":{"id":"58b90652599987bffee925e3","in_amount":"1699",...
ORDER ID: 58b90652599987bffee925e3 in_amount: 1699 asset: RUB
```

Take race, two calls for the same id + a concurrent 5-way race:

```
$ curl -X POST .../take/6a1206db7440f5cd5e5c69c7   → {"payment_id":1000,"status":"owned"}   [200]
$ curl -X POST .../take/6a1206db7440f5cd5e5c69c7   → {"reason":"InvalidStatus"}            [400]
$ 5x concurrent POST same id                       → 200 400 400 400 400
$ curl /healthz                                     → ok [200]
```
