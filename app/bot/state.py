from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class AgentMode(StrEnum):
    WAITING = "waiting"
    PAUSED = "paused"
    CAPACITY_REACHED = "capacity_reached"
    CAPTCHA_REQUIRED = "captcha_required"
    ERROR = "error"


class OrderStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    PAID = "paid"
    CANCELLED = "cancelled"


@dataclass
class ActiveOrder:
    id: str
    amount: str
    currency: str
    direction: str
    url: str = ""
    provider: str = ""
    payload: str = ""
    method_id: str = ""
    source_order_id: str = ""
    status: OrderStatus = OrderStatus.IN_PROGRESS
    take_http_ms: int | None = None
    claim_total_ms: int | None = None
    claimed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    deadline_at: datetime | None = None


@dataclass
class AgentSnapshot:
    mode: AgentMode
    active_limit: int
    active_orders: list[ActiveOrder]
    min_amount: Decimal
    max_amount: Decimal

    @property
    def active_count(self) -> int:
        return len(self.active_orders)

    @property
    def free_slots(self) -> int:
        return max(self.active_limit - self.active_count, 0)


_SAMPLE_WINDOW = 50


@dataclass
class ClaimMetrics:
    attempts: int = 0
    wins: int = 0
    win_ms_samples: deque[int] = field(default_factory=lambda: deque(maxlen=_SAMPLE_WINDOW))
    loss_ms_samples: deque[int] = field(default_factory=lambda: deque(maxlen=_SAMPLE_WINDOW))
    last_win_at: datetime | None = None

    @property
    def losses(self) -> int:
        return self.attempts - self.wins

    @property
    def win_rate_pct(self) -> float:
        return round(100 * self.wins / self.attempts, 1) if self.attempts else 0.0

    @property
    def avg_win_ms(self) -> int | None:
        return int(sum(self.win_ms_samples) / len(self.win_ms_samples)) if self.win_ms_samples else None

    @property
    def avg_loss_ms(self) -> int | None:
        return int(sum(self.loss_ms_samples) / len(self.loss_ms_samples)) if self.loss_ms_samples else None


class InMemoryAgentState:
    def __init__(self) -> None:
        self._mode = AgentMode.PAUSED
        self._active_limit = 1
        self._active_orders: list[ActiveOrder] = []
        self._min_amount = Decimal("0")
        self._max_amount = Decimal("1000000")
        self._metrics = ClaimMetrics()

    def mode(self) -> AgentMode:
        self._sync_capacity_mode()
        return self._mode

    def snapshot(self) -> AgentSnapshot:
        self._sync_capacity_mode()
        return AgentSnapshot(
            mode=self._mode,
            active_limit=self._active_limit,
            active_orders=list(self._active_orders),
            min_amount=self._min_amount,
            max_amount=self._max_amount,
        )

    def run(self) -> AgentSnapshot:
        self._mode = AgentMode.WAITING
        self._sync_capacity_mode()
        return self.snapshot()

    def pause(self) -> AgentSnapshot:
        self._mode = AgentMode.PAUSED
        return self.snapshot()

    def set_limit(self, limit: int) -> AgentSnapshot:
        self._active_limit = max(1, min(limit, 20))
        self._sync_capacity_mode()
        return self.snapshot()

    def set_amount_filter(self, min_amount: Decimal, max_amount: Decimal) -> AgentSnapshot:
        if min_amount < Decimal("0"):
            raise ValueError("Minimum amount cannot be negative")
        if max_amount < min_amount:
            raise ValueError("Maximum amount cannot be less than minimum amount")
        self._min_amount = min_amount
        self._max_amount = max_amount
        return self.snapshot()

    def amount_matches(self, amount: Decimal) -> bool:
        return self._min_amount <= amount <= self._max_amount

    def mark_paid(self, order_id: str) -> AgentSnapshot:
        self._active_orders = [order for order in self._active_orders if order.id != order_id]
        if self._mode == AgentMode.CAPACITY_REACHED:
            self._mode = AgentMode.WAITING
        return self.snapshot()

    def get_active_order(self, order_id: str) -> ActiveOrder | None:
        for order in self._active_orders:
            if order.id == order_id:
                return order
        return None

    def upsert_active_order(self, order: ActiveOrder) -> AgentSnapshot:
        self._active_orders = [item for item in self._active_orders if item.id != order.id]
        self._active_orders.append(order)
        self._sync_capacity_mode()
        return self.snapshot()

    def add_demo_order(self) -> AgentSnapshot:
        next_id = 100 + len(self._active_orders) + 1
        self._active_orders.append(
            ActiveOrder(
                id=f"demo-{next_id}",
                amount=f"{50 + next_id}.00",
                currency="USDT",
                direction="P2C",
            )
        )
        self._sync_capacity_mode()
        return self.snapshot()

    def record_claim(self, *, success: bool, take_ms: int) -> None:
        self._metrics.attempts += 1
        if success:
            self._metrics.wins += 1
            self._metrics.win_ms_samples.append(take_ms)
            self._metrics.last_win_at = datetime.now(UTC)
        else:
            self._metrics.loss_ms_samples.append(take_ms)

    def get_metrics(self) -> ClaimMetrics:
        return self._metrics

    def _sync_capacity_mode(self) -> None:
        if self._mode == AgentMode.PAUSED:
            return
        if len(self._active_orders) >= self._active_limit:
            self._mode = AgentMode.CAPACITY_REACHED
        elif self._mode == AgentMode.CAPACITY_REACHED:
            self._mode = AgentMode.WAITING
