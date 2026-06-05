#!/usr/bin/env bash
# Full-stack local integration test:
#   mock WS + mock take (host) + redis (host) + combined app+agent container.
# Drives the FastAPI /agent endpoints, then checks that the C agent was spawned,
# detected+took orders, stats populated, and /stats works AFTER the agent stops.
#
# Prereqs (started by this script's caller or here):
#   - mocks: SHARED_FEED=1 ... python -m c_agent.mock.run_all   (ports 8081/8082)
#   - redis: docker run -d --name p2c_redis_test -p 6399:6379 redis:7
#   - image: docker build -f c_agent/Dockerfile.integration -t p2c_app_agent:dev .
set -euo pipefail

APP=p2c_app_agent_test
PORT=8000
BASE=http://localhost:${PORT}

cleanup() { docker rm -f "$APP" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== pre-checks =="
curl -sf -m3 http://localhost:8082/healthz && echo "  mock take: ok"
docker exec p2c_redis_test redis-cli ping >/dev/null && echo "  redis: ok"

echo "== launch combined app+agent container =="
cleanup
docker run -d --name "$APP" -p ${PORT}:8000 \
  --add-host host.docker.internal:host-gateway \
  -e AGENT_BIN=/usr/local/bin/p2c_agent \
  -e PLATFORM_WS_URL='ws://host.docker.internal:8081/socket.io/?EIO=4&transport=websocket' \
  -e PLATFORM_BASE_URL='http://host.docker.internal:8082' \
  -e REDIS_URL='redis://host.docker.internal:6399/0' \
  -e DATABASE_URL='' \
  p2c_app_agent:dev >/dev/null
echo "  container started"

echo "== wait for FastAPI =="
for i in $(seq 1 30); do
  if curl -sf -m2 ${BASE}/health >/dev/null 2>&1 || curl -sf -m2 ${BASE}/healthz >/dev/null 2>&1; then
    echo "  app ready"; break
  fi
  sleep 1
done

j() { python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin),ensure_ascii=False))"; }

echo "== start agent =="
curl -sf -X POST ${BASE}/agent/start | j

echo "== add two accounts (own sessions + filters) =="
curl -sf -X POST ${BASE}/agent/account -H 'content-type: application/json' -d '{
  "account":"acc1","access_token":"tok1","cookie_header":"access_token=tok1; __cf_bm=aaa",
  "filters":{"min_amount":1000,"max_amount":5000,"currencies":["RUB"]}}' | j
curl -sf -X POST ${BASE}/agent/account -H 'content-type: application/json' -d '{
  "account":"acc2","access_token":"tok2","cookie_header":"access_token=tok2; __cf_bm=bbb",
  "filters":{"min_amount":1000,"max_amount":5000,"currencies":["RUB"]}}' | j

echo "== let it run (8s) =="
sleep 8

echo "== /agent/status =="
curl -sf ${BASE}/agent/status | j
echo "== /stats acc1 =="; curl -sf "${BASE}/stats?account=acc1" | j
echo "== /stats acc2 =="; curl -sf "${BASE}/stats?account=acc2" | j

echo "== STOP agent =="
curl -sf -X POST ${BASE}/agent/stop | j

echo "== /stats acc1 AFTER stop (must still work — independence) =="
curl -sf "${BASE}/stats?account=acc1" | j
curl -sf ${BASE}/agent/status | j

echo "== app logs (supervisor consuming agent events) — tail =="
docker logs "$APP" 2>&1 | grep -iE 'agent_spawned|claim|take_result|order_detected|win|session|error' | tail -25 || true

echo "== DONE =="
