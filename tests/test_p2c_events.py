from decimal import Decimal

from app.services.p2c_events import parse_order_events


def test_parse_snapshot_event() -> None:
    message = (
        '42["list:snapshot",[{"id":"abc123","in_amount":"97","in_asset":"RUB","out_asset":"USDT",'
        '"url":"https://qr.test","payload":"p1","brand_name":"Shop","provider":"nspk"}]]'
    )
    events = parse_order_events(message)
    assert len(events) == 1
    event = events[0]
    assert event.socket_order_id == "abc123"
    assert event.in_amount == Decimal("97")
    assert event.in_asset == "RUB"
    assert event.url == "https://qr.test"


def test_parse_update_add_only() -> None:
    message = (
        '42["list:update",['
        '{"op":"remove","pos":0},'
        '{"op":"add","data":{"id":"new1","in_amount":"145.5","in_asset":"RUB","out_asset":"USDT",'
        '"url":"https://qr.next","payload":"p2","brand_name":"Cafe","provider":"platiqr"}}'
        "]]"
    )
    events = parse_order_events(message)
    assert len(events) == 1
    assert events[0].socket_order_id == "new1"
    assert events[0].in_amount == Decimal("145.5")
