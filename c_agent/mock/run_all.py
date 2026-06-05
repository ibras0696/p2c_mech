"""Run both mock servers together for convenience.

Runs the take HTTP server in a background thread and the WS server on the main
asyncio loop in the same process. All env vars from ``ws_server`` and
``take_server`` apply.

Run:  python -m c_agent.mock.run_all
"""

from __future__ import annotations

import asyncio
import sys
import threading

from c_agent.mock import take_server, ws_server


def _run_take() -> None:
    server = take_server.build_server()
    try:
        server.serve_forever()
    except Exception as exc:  # noqa: BLE001
        print(f"[run_all] take server stopped: {exc}", file=sys.stderr, flush=True)
    finally:
        server.server_close()


def main() -> None:
    take_thread = threading.Thread(target=_run_take, name="take-server", daemon=True)
    take_thread.start()
    try:
        asyncio.run(ws_server.main())
    except KeyboardInterrupt:
        print("[run_all] shutdown (KeyboardInterrupt)", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
