from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class P2COrderEvent:
    socket_order_id: str
    in_amount: Decimal
    in_asset: str
    out_asset: str
    url: str
    payload: str
    brand_name: str
    provider: str


def parse_order_events(message: str) -> list[P2COrderEvent]:
    if not message.startswith("42"):
        return []
    try:
        packet: Any = json.loads(message[2:])
    except json.JSONDecodeError:
        return []
    if not isinstance(packet, list) or not packet:
        return []
    event_name = packet[0]
    if not isinstance(event_name, str):
        return []
    if event_name == "list:snapshot":
        if len(packet) < 2 or not isinstance(packet[1], list):
            return []
        return [event for event in (parse_order(item) for item in packet[1]) if event is not None]
    if event_name == "list:update":
        if len(packet) < 2 or not isinstance(packet[1], list):
            return []
        return parse_update_events(packet[1])
    return []


def parse_update_events(ops: list[Any]) -> list[P2COrderEvent]:
    results: list[P2COrderEvent] = []
    for op_item in ops:
        if not isinstance(op_item, dict):
            continue
        if op_item.get("op") != "add":
            continue
        data = op_item.get("data")
        if not isinstance(data, dict):
            continue
        event = parse_order(data)
        if event is not None:
            results.append(event)
    return results


def parse_order(item: Any) -> P2COrderEvent | None:
    if not isinstance(item, dict):
        return None
    order_id = str(item.get("id", "")).strip()
    if not order_id:
        return None
    try:
        amount = Decimal(str(item.get("in_amount", "")))
    except (InvalidOperation, ValueError):
        return None
    return P2COrderEvent(
        socket_order_id=order_id,
        in_amount=amount,
        in_asset=str(item.get("in_asset", "")),
        out_asset=str(item.get("out_asset", "")),
        url=str(item.get("url", "")),
        payload=str(item.get("payload", "")),
        brand_name=str(item.get("brand_name", "")),
        provider=str(item.get("provider", "")),
    )
