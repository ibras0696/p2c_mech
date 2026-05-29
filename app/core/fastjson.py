"""Fast JSON parsing with a graceful fallback.

The order-claim hot path parses every ``list:update`` socket frame. ``orjson``
parses 2-3x faster than the stdlib and is used when available; otherwise the
stdlib ``json`` module is used so the app never hard-depends on the C extension.
"""

from __future__ import annotations

from typing import Any

try:
    import orjson

    def loads(data: str | bytes) -> Any:
        return orjson.loads(data)

    HAVE_ORJSON = True
except ImportError:  # pragma: no cover - depends on install
    import json

    def loads(data: str | bytes) -> Any:
        return json.loads(data)

    HAVE_ORJSON = False
