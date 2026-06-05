"""Mock Engine.IO v4 / Socket.IO WebSocket server mimicking app.send.tg's order feed.

Speaks the exact protocol the real server uses (see
``app/integrations/platform_ws/p2c_socket.py`` and ``docs/C_AGENT_SPEC_RU.md``
§4.3) so the same C client works unchanged against this mock and production.

Run:  python -m c_agent.mock.ws_server

Env vars:
    WS_PORT          listen port (default 8081)
    WS_HOST          listen host (default 0.0.0.0)
    ORDER_INTERVAL_MS  ms between emits (default 1000)
    ORDER_BURST      orders pushed per emit (default 1)
    AMOUNT_MIN       min in_amount, integer RUB (default 500)
    AMOUNT_MAX       max in_amount, integer RUB (default 5000)
    CURRENCIES       comma list of in_asset values (default "RUB")
    SEND_SNAPSHOT    "1"/"true" to emit an (empty) list:snapshot (default true)
    PING_INTERVAL_MS Engine.IO ping interval ms (default 25000)
    PING_TIMEOUT_MS  Engine.IO ping timeout ms advertised to client (default 20000)
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import secrets
import sys

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

# --- Engine.IO / Socket.IO frame prefixes (mirror p2c_socket.py) -------------
ENGINE_OPEN = "0"
ENGINE_PING = "2"
ENGINE_PONG = "3"
SOCKET_CONNECT = "40"
SOCKET_DISCONNECT = "41"
SOCKET_EVENT = "42"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        log(f"WARN bad int for {name}={raw!r}, using default {default}")
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def log(message: str) -> None:
    print(f"[ws] {message}", file=sys.stderr, flush=True)


# Clients ready to receive the shared feed (SHARED_FEED mode).
_subscribers: set[ServerConnection] = set()


class Config:
    def __init__(self) -> None:
        self.host = os.environ.get("WS_HOST", "0.0.0.0")
        self.port = _env_int("WS_PORT", 8081)
        self.order_interval_ms = _env_int("ORDER_INTERVAL_MS", 1000)
        self.order_burst = max(1, _env_int("ORDER_BURST", 1))
        self.amount_min = _env_int("AMOUNT_MIN", 500)
        self.amount_max = _env_int("AMOUNT_MAX", 5000)
        self.currencies = _env_list("CURRENCIES", ["RUB"])
        self.send_snapshot = _env_bool("SEND_SNAPSHOT", True)
        self.ping_interval_ms = _env_int("PING_INTERVAL_MS", 25000)
        self.ping_timeout_ms = _env_int("PING_TIMEOUT_MS", 20000)
        # SHARED_FEED: one global emitter broadcasts the SAME orders to every
        # connected client (real-server behavior), so multiple accounts race the
        # same order id. Default off = per-client independent streams.
        self.shared_feed = _env_bool("SHARED_FEED", False)

    def describe(self) -> str:
        return (
            f"host={self.host} port={self.port} "
            f"order_interval_ms={self.order_interval_ms} order_burst={self.order_burst} "
            f"amount=[{self.amount_min},{self.amount_max}] currencies={self.currencies} "
            f"send_snapshot={self.send_snapshot} "
            f"ping_interval_ms={self.ping_interval_ms} ping_timeout_ms={self.ping_timeout_ms}"
        )


def _new_order_id() -> str:
    # 24 lowercase-hex chars, same shape as real socket order ids
    # (e.g. 6a1206db7440f5cd5e5c69c7).
    return secrets.token_hex(12)


def _make_order(cfg: Config) -> dict[str, object]:
    lo, hi = (cfg.amount_min, cfg.amount_max) if cfg.amount_min <= cfg.amount_max else (
        cfg.amount_max,
        cfg.amount_min,
    )
    amount = random.randint(lo, hi)
    return {
        "id": _new_order_id(),
        # amounts are STRINGS in the real feed
        "in_amount": str(amount),
        "in_asset": random.choice(cfg.currencies),
        "out_asset": "USDT",
        "provider": "nspk",
        "brand_name": "Bank",
        "status": "new",
    }


def _list_update_frame(orders: list[dict[str, object]]) -> str:
    payload = [{"op": "add", "data": order} for order in orders]
    return SOCKET_EVENT + json.dumps(["list:update", payload], separators=(",", ":"))


def _short_id(peer: tuple[object, ...] | None) -> str:
    if not peer:
        return "?"
    return f"{peer[0]}:{peer[1]}"


async def _emit_orders(ws: ServerConnection, cfg: Config, peer: str) -> None:
    """Background task: periodically push list:update order batches to one client."""
    interval = cfg.order_interval_ms / 1000.0
    while True:
        await asyncio.sleep(interval)
        orders = [_make_order(cfg) for _ in range(cfg.order_burst)]
        frame = _list_update_frame(orders)
        try:
            await ws.send(frame)
        except ConnectionClosed:
            return
        ids = ",".join(str(o["id"]) for o in orders)
        amounts = ",".join(str(o["in_amount"]) for o in orders)
        log(f"order emitted peer={peer} ids=[{ids}] in_amount=[{amounts}]")


async def _shared_emitter(cfg: Config) -> None:
    """Global task: build ONE order batch per tick and broadcast the identical
    frame to every ready client, so multiple accounts race the same order id."""
    interval = cfg.order_interval_ms / 1000.0
    while True:
        await asyncio.sleep(interval)
        if not _subscribers:
            continue
        orders = [_make_order(cfg) for _ in range(cfg.order_burst)]
        frame = _list_update_frame(orders)
        ids = ",".join(str(o["id"]) for o in orders)
        targets = list(_subscribers)
        for ws in targets:
            try:
                await ws.send(frame)
            except ConnectionClosed:
                _subscribers.discard(ws)
        log(f"shared order broadcast ids=[{ids}] clients={len(targets)}")


async def _engine_pinger(ws: ServerConnection, cfg: Config, peer: str) -> None:
    """Background task: send Engine.IO ping '2' on the configured interval."""
    interval = cfg.ping_interval_ms / 1000.0
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send(ENGINE_PING)
        except ConnectionClosed:
            return
        log(f"engine ping sent peer={peer}")


async def _handle(ws: ServerConnection, cfg: Config) -> None:
    peer = _short_id(ws.remote_address)
    log(f"client connected peer={peer} path={ws.request.path if ws.request else '?'}")

    # Engine.IO OPEN handshake. upgrades=[] because we are already on websocket.
    open_payload = {
        "sid": secrets.token_hex(8),
        "upgrades": [],
        "pingInterval": cfg.ping_interval_ms,
        "pingTimeout": cfg.ping_timeout_ms,
        "maxPayload": 1000000,
    }
    await ws.send(ENGINE_OPEN + json.dumps(open_payload, separators=(",", ":")))
    log(f"handshake: sent ENGINE_OPEN sid={open_payload['sid']} peer={peer}")

    namespace_connected = False
    list_initialized = False
    emit_task: asyncio.Task[None] | None = None
    ping_task: asyncio.Task[None] | None = None

    try:
        async for raw in ws:
            if not isinstance(raw, str):
                log(f"binary frame ignored peer={peer}")
                continue

            if raw == ENGINE_PONG:
                # client replied to our ping; accept and ignore
                log(f"engine pong received peer={peer}")
                continue

            if raw == ENGINE_PING:
                # some clients ping us; be polite and pong back
                await ws.send(ENGINE_PONG)
                continue

            if raw.startswith(SOCKET_CONNECT) and not namespace_connected:
                # client sent "40" namespace connect → reply "40{...}"
                namespace_connected = True
                connect_payload = {"sid": secrets.token_hex(8)}
                await ws.send(
                    SOCKET_CONNECT + json.dumps(connect_payload, separators=(",", ":"))
                )
                log(
                    f"handshake: SOCKET_CONNECT received → replied 40 sid="
                    f"{connect_payload['sid']} peer={peer}"
                )
                continue

            if raw == SOCKET_DISCONNECT:
                log(f"client sent SOCKET_DISCONNECT peer={peer}")
                break

            if raw.startswith(SOCKET_EVENT):
                event = _event_name(raw)
                log(f"socket event received peer={peer} event={event!r} raw={raw[:120]}")
                if event == "list:initialize" and not list_initialized:
                    list_initialized = True
                    if cfg.send_snapshot:
                        snapshot = SOCKET_EVENT + json.dumps(
                            ["list:snapshot", []], separators=(",", ":")
                        )
                        await ws.send(snapshot)
                        log(f"handshake: sent empty list:snapshot peer={peer}")
                    # Start the feed now that the client is ready.
                    if cfg.shared_feed:
                        _subscribers.add(ws)
                        log(f"subscribed to shared feed peer={peer} clients={len(_subscribers)}")
                    else:
                        emit_task = asyncio.create_task(_emit_orders(ws, cfg, peer))
                    ping_task = asyncio.create_task(_engine_pinger(ws, cfg, peer))
                    log(f"feed started peer={peer}")
                continue

            log(f"unhandled frame peer={peer} raw={raw[:120]!r}")
    except ConnectionClosed as exc:
        code = exc.rcvd.code if exc.rcvd else None
        log(f"client disconnected peer={peer} code={code}")
    finally:
        _subscribers.discard(ws)
        for task in (emit_task, ping_task):
            if task is not None:
                task.cancel()
        log(f"connection closed peer={peer}")


def _event_name(raw: str) -> str:
    try:
        payload = json.loads(raw[len(SOCKET_EVENT):])
    except json.JSONDecodeError:
        return ""
    if isinstance(payload, list) and payload and isinstance(payload[0], str):
        return payload[0]
    return ""


async def main() -> None:
    cfg = Config()
    log(f"starting {cfg.describe()}")

    async def handler(ws: ServerConnection) -> None:
        await _handle(ws, cfg)

    shared_task: asyncio.Task[None] | None = None
    if cfg.shared_feed:
        shared_task = asyncio.create_task(_shared_emitter(cfg))
        log("SHARED_FEED enabled: one broadcast feed for all clients")

    async with serve(
        handler,
        cfg.host,
        cfg.port,
        # We drive Engine.IO pings ourselves; disable websockets-level ping so it
        # does not inject control frames the C client does not expect.
        ping_interval=None,
        max_size=2_000_000,
    ):
        log(f"listening on ws://{cfg.host}:{cfg.port}/socket.io/?EIO=4&transport=websocket")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("shutdown (KeyboardInterrupt)")
