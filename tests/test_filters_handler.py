from decimal import Decimal

import pytest
from app.bot.handlers.filters import parse_amount_range


def test_parse_amount_range() -> None:
    assert parse_amount_range("100 500") == (Decimal("100"), Decimal("500"))
    assert parse_amount_range("100,5 500,7") == (Decimal("100.5"), Decimal("500.7"))


def test_parse_amount_range_rejects_invalid_order() -> None:
    with pytest.raises(ValueError):
        parse_amount_range("500 100")
