from decimal import Decimal

from app.bot.state import AgentMode, InMemoryAgentState


def test_default_limit_is_one() -> None:
    state = InMemoryAgentState()
    snapshot = state.snapshot()
    assert snapshot.active_limit == 1


def test_limit_change_updates_capacity_state() -> None:
    state = InMemoryAgentState()
    state.set_limit(3)
    state.add_demo_order()
    state.add_demo_order()
    snapshot = state.run()
    assert snapshot.mode == AgentMode.WAITING

    snapshot = state.set_limit(2)

    assert snapshot.active_limit == 2
    assert snapshot.active_count == 2
    assert snapshot.mode == AgentMode.CAPACITY_REACHED


def test_mark_paid_removes_order_and_frees_slot() -> None:
    state = InMemoryAgentState()
    state.add_demo_order()
    state.add_demo_order()
    state.run()
    state.set_limit(2)

    snapshot = state.mark_paid("demo-101")

    assert snapshot.active_count == 1
    assert snapshot.free_slots == 1
    assert snapshot.mode == AgentMode.WAITING


def test_amount_filter_matches_inclusive_range() -> None:
    state = InMemoryAgentState()

    state.set_amount_filter(Decimal("100"), Decimal("500"))

    assert state.amount_matches(Decimal("100"))
    assert state.amount_matches(Decimal("250"))
    assert state.amount_matches(Decimal("500"))
    assert not state.amount_matches(Decimal("99.99"))
    assert not state.amount_matches(Decimal("500.01"))
