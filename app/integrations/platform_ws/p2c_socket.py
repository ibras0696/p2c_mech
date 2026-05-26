from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.logging import get_logger

logger = get_logger(__name__)

SocketMessageHandler = Callable[[str], Awaitable[None]]

ENGINE_OPEN = "0"
ENGINE_PING = "2"
ENGINE_PONG = "3"
SOCKET_CONNECT = "40"
SOCKET_DISCONNECT = "41"
LIST_INITIALIZE_PACKET = '42["list:initialize"]'


@dataclass(frozen=True)
class P2CSocketConfig:
    url: str
    cookie_header: str
    force_ipv4: bool = True
    origin: str = "https://app.send.tg"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    )
    reconnect_min_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    open_timeout_seconds: float = 10.0


class ReconnectBackoff:
    def __init__(self, min_delay: float, max_delay: float) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._current = min_delay

    def reset(self) -> None:
        self._current = self._min_delay

    def next_delay(self) -> float:
        delay = self._current
        self._current = min(self._current * 2, self._max_delay)
        return delay


class P2CSocketClient:
    def __init__(
        self,
        config: P2CSocketConfig,
        on_message: SocketMessageHandler | None = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._stop_event = asyncio.Event()
        self._backoff = ReconnectBackoff(
            min_delay=config.reconnect_min_seconds,
            max_delay=config.reconnect_max_seconds,
        )

    def stop(self) -> None:
        self._stop_event.set()

    async def run_forever(self) -> None:
        if not self._config.url:
            raise RuntimeError("Platform WebSocket URL is required")
        if not self._config.cookie_header:
            raise RuntimeError("Platform cookie header is required")

        while not self._stop_event.is_set():
            try:
                await self._run_once()
                self._backoff.reset()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay = self._backoff.next_delay()
                logger.warning(
                    "p2c_socket_reconnect_scheduled error=%s delay_seconds=%.2f",
                    type(exc).__name__,
                    delay,
                )
                await self._sleep_or_stop(delay)

    async def probe_once(self) -> str:
        if not self._config.url:
            raise RuntimeError("Platform WebSocket URL is required")
        if not self._config.cookie_header:
            raise RuntimeError("Platform cookie header is required")
        await self._run_probe()
        return "connected"

    async def _run_probe(self) -> None:
        headers = {
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cookie": self._config.cookie_header,
        }
        connect_kwargs: dict[str, Any] = {}
        if self._config.force_ipv4:
            connect_kwargs["family"] = socket.AF_INET
        async with websockets.connect(
            self._config.url,
            origin=cast(Any, self._config.origin),
            additional_headers=headers,
            user_agent_header=self._config.user_agent,
            open_timeout=self._config.open_timeout_seconds,
            ping_interval=None,
            proxy=None,
            **connect_kwargs,
        ) as websocket:
            open_packet = await asyncio.wait_for(websocket.recv(), timeout=10)
            if not isinstance(open_packet, str) or not open_packet.startswith(ENGINE_OPEN):
                raise RuntimeError("Unexpected Engine.IO open packet")
            await websocket.send(SOCKET_CONNECT)
            connect_packet = await asyncio.wait_for(websocket.recv(), timeout=10)
            if not isinstance(connect_packet, str) or not connect_packet.startswith(SOCKET_CONNECT):
                raise RuntimeError("Unexpected Socket.IO connect packet")

    async def _run_once(self) -> None:
        headers = {
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cookie": self._config.cookie_header,
        }
        connect_kwargs: dict[str, Any] = {}
        if self._config.force_ipv4:
            connect_kwargs["family"] = socket.AF_INET

        logger.info("p2c_socket_connecting")
        async with websockets.connect(
            self._config.url,
            origin=cast(Any, self._config.origin),
            additional_headers=headers,
            user_agent_header=self._config.user_agent,
            open_timeout=self._config.open_timeout_seconds,
            ping_interval=None,
            proxy=None,
            **connect_kwargs,
        ) as websocket:
            logger.info("p2c_socket_connected")
            namespace_connected = False
            list_initialized = False

            while not self._stop_event.is_set():
                try:
                    message = await websocket.recv()
                except ConnectionClosed as exc:
                    logger.info("p2c_socket_closed code=%s", exc.rcvd.code if exc.rcvd else None)
                    return

                if not isinstance(message, str):
                    logger.debug("p2c_socket_binary_message_ignored")
                    continue

                self._log_packet_received(message)

                if message.startswith(ENGINE_OPEN) and not namespace_connected:
                    self._log_engine_open(message)
                    await websocket.send(SOCKET_CONNECT)
                    namespace_connected = True
                    logger.info("p2c_socket_namespace_connect_sent")
                    continue

                if message == ENGINE_PING:
                    await websocket.send(ENGINE_PONG)
                    logger.debug("p2c_socket_pong_sent")
                    continue

                if message.startswith(SOCKET_CONNECT) and namespace_connected and not list_initialized:
                    await websocket.send(LIST_INITIALIZE_PACKET)
                    list_initialized = True
                    logger.info("p2c_socket_list_initialize_sent")
                    continue

                if message == SOCKET_DISCONNECT:
                    logger.info("p2c_socket_namespace_disconnected")
                    return

                if self._on_message is not None:
                    await self._on_message(message)
                else:
                    logger.info("p2c_socket_message packet_prefix=%s", message[:2])

    async def _sleep_or_stop(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except TimeoutError:
            return

    def _log_engine_open(self, message: str) -> None:
        payload = message[1:]
        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError:
            logger.info("p2c_socket_engine_open_received")
            return

        logger.info(
            "p2c_socket_engine_open_received ping_interval=%s ping_timeout=%s max_payload=%s upgrades=%s",
            data.get("pingInterval"),
            data.get("pingTimeout"),
            data.get("maxPayload"),
            data.get("upgrades"),
        )

    def _log_packet_received(self, message: str) -> None:
        prefix = message[:2]
        event = _extract_socket_event_name(message)
        if event == "list:update":
            logger.info(
                "p2c_socket_packet_received prefix=%s len=%d event=%s",
                prefix,
                len(message),
                event,
            )
            return
        logger.debug(
            "p2c_socket_packet_received prefix=%s len=%d event=%s",
            prefix,
            len(message),
            event,
        )


def build_cookie_header(
    *,
    raw_cookie_header: str,
    access_token: str,
    cf_bm_cookie: str,
) -> str:
    if raw_cookie_header:
        return raw_cookie_header

    cookies: list[str] = []
    if access_token:
        cookies.append(f"access_token={access_token}")
    if cf_bm_cookie:
        cookies.append(f"__cf_bm={cf_bm_cookie}")
    return "; ".join(cookies)


def _extract_socket_event_name(message: str) -> str:
    if not message.startswith("42"):
        return ""
    try:
        payload = json.loads(message[2:])
    except json.JSONDecodeError:
        return ""
    if isinstance(payload, list) and payload and isinstance(payload[0], str):
        return payload[0]
    return ""
