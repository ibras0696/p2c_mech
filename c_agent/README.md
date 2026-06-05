# p2c_agent — C sniper agent (multi-account)

The hot-path component from `docs/C_AGENT_SPEC_RU.md`: per account it holds a
WebSocket (Engine.IO/Socket.IO) connection, detects new orders, and fires the
take POST over a warm HTTP/2 connection. Everything slow (confirm/complete,
Telegram, Postgres, session refresh) stays in the Python FastAPI supervisor.

## Architecture (one process)

```
main.c          arg parse, stdin command loop, account registry, heartbeat
 └─ account.c   per account: 1 WS thread (detect) + 1 take thread (send),
                own session, warm take conn, per-account seen-set + slots
     ├─ ws.c        libwebsockets client + Engine.IO handshake (0→40→42[init])
     ├─ engineio.c  frame classification (prefix scan, no JSON on cold frames)
     ├─ parser.c    extract add-orders from list:update (yyjson)
     ├─ taker.c     warm HTTP/2 POST via libcurl (cookie-only header)
     └─ redis_cache.c  cache-aside session read (hiredis), connect path only
events.c        stdout NDJSON events (machine), stderr human logs
config.c        CLI args + stdin NDJSON command parsing (yyjson)
```

Contracts (stdin commands / stdout events / Redis keys) are defined in
`include/agent.h` and `include/events.h` and shared with the FastAPI supervisor.

## Build

```bash
# Linux deps: build-essential cmake git pkg-config
#             libwebsockets-dev libcurl4-openssl-dev libssl-dev libhiredis-dev
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j

# or via Docker
docker build -f Dockerfile.agent -t p2c_agent:dev .
```

Production TLS fingerprint: build with `-DP2C_USE_IMPERSONATE=ON` and link
curl-impersonate-chrome (applies `curl_easy_impersonate(..., "chrome131")`).

## Run

```bash
p2c_agent \
  --ws-url 'wss://app.send.tg/socket.io/?EIO=4&transport=websocket' \
  --base-url https://app.send.tg --origin https://app.send.tg \
  --redis redis://127.0.0.1:6379/0 --impersonate chrome131
```

Sessions/cookies are never CLI args (visible in `ps`). They arrive via Redis
(`p2c:session:{account}`, cache-aside) or stdin `session`/`add_account` commands.

### stdin commands (one NDJSON object per line)
```json
{"cmd":"add_account","account":"acc1","access_token":"...","cookie_header":"...; __cf_bm=...","filters":{"min_amount":1000,"max_amount":50000,"currencies":["RUB"]}}
{"cmd":"session","account":"acc1","access_token":"...","cookie_header":"..."}
{"cmd":"filter","account":"acc1","min_amount":1000,"max_amount":50000,"currencies":["RUB"]}
{"cmd":"mode","account":"acc1","value":"running"}    // or "paused"; omit account = all
{"cmd":"remove_account","account":"acc1"}
{"cmd":"shutdown"}
```

### stdout events (NDJSON)
`ws_connected` · `ws_disconnected` · `session_miss` · `order_detected`
(`detect_ns`) · `take_sent` · `take_result` (`status`,`http_ms`,`payment_id`) ·
`claim_won` · `claim_lost` · `heartbeat` (per-account counters) · `error`.

## Local testing (no credentials, no Cloudflare)

Mock servers live in `mock/` (see `mock/README.md`). With `SHARED_FEED=1` the
mock broadcasts the SAME order to all clients, so multiple accounts race it.

```bash
# terminal 1: mocks (project venv has websockets>=16)
SHARED_FEED=1 ORDER_INTERVAL_MS=500 AMOUNT_MIN=1000 AMOUNT_MAX=5000 CURRENCIES=RUB \
  .venv/bin/python -m c_agent.mock.run_all

# terminal 2: agent against the mocks
printf '%s\n' '{"cmd":"add_account","account":"acc1","cookie_header":"access_token=t1","filters":{"min_amount":1000,"max_amount":5000,"currencies":["RUB"]}}' \
  | docker run --rm -i p2c_agent:dev \
      --ws-url 'ws://host.docker.internal:8081/socket.io/?EIO=4&transport=websocket' \
      --base-url 'http://host.docker.internal:8082' --origin http://host.docker.internal
```

Full-stack (FastAPI supervisor spawns the agent): build
`Dockerfile.integration` from the repo root and run
`integration/run_integration_test.sh`.

## Verified locally

- Engine.IO handshake + list:update detection against the mock.
- Multi-account: two accounts race the SAME order; exactly one wins (200),
  the other gets `400 InvalidStatus`. Per-account dedup: zero double-takes.
- detect→decision latency: p50 ≈ 37 µs, p99 ≈ 83 µs (budget §4.9 < 200 µs).
- Redis cache-aside: session HIT connects; MISS emits `session_miss`, then a
  stdin `session` push connects the account.

## Not yet (later milestones)

- `-DP2C_USE_IMPERSONATE` linked build with curl-impersonate (prod fingerprint).
- §4.9 micro-opts: byte-scan id extraction, lock-free ring, SCHED_FIFO/mlock/
  CPU pinning (`net_opt.c`). Current detect→decision is already well under budget.
