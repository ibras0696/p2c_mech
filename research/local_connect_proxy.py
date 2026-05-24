from __future__ import annotations

import select
import socket
import socketserver
from contextlib import suppress
from urllib.parse import urlsplit

BUFFER_SIZE = 65536


class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = self.request.recv(BUFFER_SIZE)
        if not data:
            return

        first_line = data.split(b"\r\n", 1)[0].decode("latin1", errors="ignore")
        parts = first_line.split()
        if len(parts) < 2:
            return

        method = parts[0].upper()
        target = parts[1]
        if method == "CONNECT":
            self._handle_connect(target)
            return

        self._handle_http(data, first_line, target)

    def _handle_connect(self, target: str) -> None:
        host, port = self._parse_connect_target(target)
        with socket.create_connection((host, port), timeout=20) as upstream:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self._tunnel(self.request, upstream)

    def _handle_http(self, data: bytes, first_line: str, target: str) -> None:
        parsed = urlsplit(target)
        if parsed.scheme and parsed.hostname:
            host = parsed.hostname
            port = parsed.port or 80
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
        else:
            host = self._host_header(data)
            if host is None:
                self.request.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            if ":" in host:
                host, raw_port = host.rsplit(":", 1)
                port = int(raw_port)
            else:
                port = 80
            path = target

        method, _, version = first_line.partition(" ")
        _, _, version = version.partition(" ")
        request_line = f"{method} {path} {version or 'HTTP/1.1'}\r\n".encode("latin1")
        _, _, rest = data.partition(b"\r\n")
        forwarded = request_line + rest
        with socket.create_connection((host, port), timeout=20) as upstream:
            upstream.sendall(forwarded)
            self._tunnel(self.request, upstream)

    def _host_header(self, data: bytes) -> str | None:
        for line in data.split(b"\r\n"):
            if line.lower().startswith(b"host:"):
                return line.split(b":", 1)[1].strip().decode("latin1")
        return None

    def _parse_connect_target(self, target: str) -> tuple[str, int]:
        if ":" not in target:
            return target, 443
        host, port = target.rsplit(":", 1)
        return host, int(port)

    def _tunnel(self, client: socket.socket, upstream: socket.socket) -> None:
        sockets = [client, upstream]
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 60)
            if errored or not readable:
                return
            for sock in readable:
                other = upstream if sock is client else client
                chunk = sock.recv(BUFFER_SIZE)
                if not chunk:
                    return
                with suppress(OSError):
                    other.sendall(chunk)


class ThreadingProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with ThreadingProxy(("127.0.0.1", 10801), ProxyHandler) as server:
        server.serve_forever()
