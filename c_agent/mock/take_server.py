"""Mock HTTP "take" endpoint simulating the order-claim race.

Mirrors the real path used by the agent:
    POST /internal/v1/p2c/payments/take/{order_id}
(see ``app/integrations/platform_api/p2c_payments.py`` and
``docs/SPRINT_1_REVERSE_ENGINEERING_RU.md`` §6.4).

Race semantics: the FIRST caller for a given order_id wins
(200 {"payment_id": <int>, "status":"owned"}); every subsequent caller for the
same id loses (400 {"reason":"InvalidStatus"}). This lets us verify multi-account
behaviour — several accounts race the same order, one wins, the rest get 400.

No auth: any Cookie/headers are accepted and ignored.

Run:  python -m c_agent.mock.take_server

Env vars:
    TAKE_PORT        listen port (default 8082)
    TAKE_HOST        listen host (default 0.0.0.0)
    TAKE_LATENCY_MS  base artificial latency ms, jittered +/-20% (default 30)
"""

from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TAKE_PREFIX = "/internal/v1/p2c/payments/take/"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        log(f"WARN bad int for {name}={raw!r}, using default {default}")
        return default


def log(message: str) -> None:
    print(f"[take] {message}", file=sys.stderr, flush=True)


class ClaimRegistry:
    """Thread-safe set of claimed order ids + monotonically increasing payment id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claimed: dict[str, int] = {}
        self._next_payment_id = 1000

    def claim(self, order_id: str) -> tuple[bool, int]:
        """Return (won, payment_id). won=True only for the first caller of order_id."""
        with self._lock:
            if order_id in self._claimed:
                return False, self._claimed[order_id]
            payment_id = self._next_payment_id
            self._next_payment_id += 1
            self._claimed[order_id] = payment_id
            return True, payment_id


class TakeHandler(BaseHTTPRequestHandler):
    server_version = "MockTake/1.0"
    protocol_version = "HTTP/1.1"

    # Injected by the server factory below.
    registry: ClaimRegistry
    latency_ms: int

    def log_message(self, fmt: str, *args: object) -> None:  # silence default logging
        pass

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drain_body(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 0:
            self.rfile.read(length)

    def _sleep_with_jitter(self) -> float:
        base = self.latency_ms
        jitter = base * 0.2
        delay_ms = max(0.0, base + random.uniform(-jitter, jitter))
        time.sleep(delay_ms / 1000.0)
        return delay_ms

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._write_text(200, "ok")
            return
        self._write_json(404, {"reason": "NotFound"})

    def do_POST(self) -> None:
        self._drain_body()

        if not self.path.startswith(TAKE_PREFIX):
            log(f"POST unknown path={self.path!r} → 404")
            self._write_json(404, {"reason": "NotFound"})
            return

        order_id = self.path[len(TAKE_PREFIX):].strip("/")
        if not order_id:
            log("POST take with empty order_id → 400")
            self._write_json(400, {"reason": "MissingOrderId"})
            return

        delay_ms = self._sleep_with_jitter()
        won, payment_id = self.registry.claim(order_id)

        if won:
            log(
                f"take received id={order_id} → 200 WON payment_id={payment_id} "
                f"latency_ms={delay_ms:.1f}"
            )
            self._write_json(200, {"payment_id": payment_id, "status": "owned"})
        else:
            log(
                f"take received id={order_id} → 400 LOST (already claimed by "
                f"payment_id={payment_id}) latency_ms={delay_ms:.1f}"
            )
            self._write_json(400, {"reason": "InvalidStatus"})


def build_server() -> ThreadingHTTPServer:
    host = os.environ.get("TAKE_HOST", "0.0.0.0")
    port = _env_int("TAKE_PORT", 8082)
    latency_ms = _env_int("TAKE_LATENCY_MS", 30)

    registry = ClaimRegistry()

    handler_cls = type(
        "BoundTakeHandler",
        (TakeHandler,),
        {"registry": registry, "latency_ms": latency_ms},
    )

    server = ThreadingHTTPServer((host, port), handler_cls)
    log(
        f"starting host={host} port={port} take_latency_ms={latency_ms} (jitter +/-20%)"
    )
    log(f"listening on http://{host}:{port}  (POST {TAKE_PREFIX}{{order_id}} , GET /healthz)")
    return server


def main() -> None:
    server = build_server()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutdown (KeyboardInterrupt)")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
