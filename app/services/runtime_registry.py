from __future__ import annotations

from typing import Protocol


class RuntimeStatusProvider(Protocol):
    async def runtime_statuses(self) -> list[dict[str, object]]:
        ...


_runtime_provider: RuntimeStatusProvider | None = None


def set_runtime_provider(provider: RuntimeStatusProvider) -> None:
    global _runtime_provider
    _runtime_provider = provider


def clear_runtime_provider() -> None:
    global _runtime_provider
    _runtime_provider = None


def get_runtime_provider() -> RuntimeStatusProvider | None:
    return _runtime_provider

