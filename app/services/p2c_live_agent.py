from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlparse

from app.bot.session_state import PlatformSession
from app.bot.state import ActiveOrder, AgentMode, InMemoryAgentState
from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.platform_api import P2CPaymentDetails, P2CPaymentsClient, P2CPaymentsError
from app.integrations.platform_ws.p2c_socket import P2CSocketClient, P2CSocketConfig
from app.repositories.active_orders import ActiveOrderRepository, InMemoryActiveOrderRepository
from app.repositories.platform_session import PlatformSessionRepository
from app.services.p2c_events import P2COrderEvent, parse_order_events

logger = get_logger(__name__)

OrderNotifier = Callable[[ActiveOrder], Awaitable[None]]

OWNED_OK_STATUSES: Final[set[str]] = {"processing", "pending", "created", "in_progress"}
FINAL_STATUSES: Final[set[str]] = {
    "completed",
    "complete",
    "paid",
    "success",
    "succeeded",
    "cancelled",
    "canceled",
    "expired",
    "rejected",
    "failed",
    "error",
}


class P2CLiveAgent:
    def __init__(
        self,
        *,
        settings: Settings,
        state: InMemoryAgentState,
        session_repository: PlatformSessionRepository,
        notify_order_ready: OrderNotifier,
        active_order_repository: ActiveOrderRepository | None = None,
    ) -> None:
        self._settings = settings
        self._state = state
        self._session_repository = session_repository
        self._notify_order_ready = notify_order_ready
        self._payments_client = P2CPaymentsClient(base_url=settings.platform_base_url)
        self._active_order_repository = active_order_repository or InMemoryActiveOrderRepository()
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._inflight: set[str] = set()
        self._seen: set[str] = set()
        self._completing: set[str] = set()
        self._socket_client: P2CSocketClient | None = None
        self._pause_generation = 0
        self._cached_account_method_id: str = ""
        self._session_l1_cache: PlatformSession | None = None
        self._session_l1_cached_at_monotonic = 0.0
        self._session_l1_ttl_seconds = 2.0
        self._penalty_resume_task: asyncio.Task[None] | None = None
        self._penalty_events = 0

    def stop(self) -> None:
        self._stop_event.set()
        self._cancel_penalty_resume_task()
        if self._socket_client is not None:
            self._socket_client.stop()

    def on_pause(self) -> None:
        self._cancel_penalty_resume_task()
        self._increment_pause_generation_and_stop_socket()

    def on_run(self) -> None:
        return

    async def prewarm_take_channels(self, session: PlatformSession) -> None:
        try:
            await self._payments_client.prewarm_take_clients(session=session, channels=1)
        except Exception as exc:
            if self._is_auth_or_forbidden_error(exc):
                raise P2CPaymentsError(
                    "Platform session is unauthorized (401/403). Refresh session before run."
                ) from exc
            logger.warning("p2c_live_agent_prewarm_failed error=%s", type(exc).__name__)
            return
        logger.info("p2c_live_agent_prewarm_succeeded channels=1")

    def set_session_hint(self, session: PlatformSession) -> None:
        self._session_l1_cache = session
        self._session_l1_cached_at_monotonic = time.monotonic()

    async def aclose(self) -> None:
        await self._payments_client.aclose()

    async def run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._state.snapshot().mode == AgentMode.PAUSED:
                    await asyncio.sleep(0.5)
                    continue
                session = await self._get_session()
                if session is None or not session.cookie_header:
                    await asyncio.sleep(2)
                    continue
                if not self._settings.platform_ws_url:
                    logger.warning("p2c_live_agent_ws_url_missing")
                    await asyncio.sleep(2)
                    continue
                client = P2CSocketClient(
                    P2CSocketConfig(
                        url=self._settings.platform_ws_url,
                        cookie_header=session.cookie_header,
                        force_ipv4=self._settings.platform_force_ipv4,
                    ),
                    on_message=self._on_socket_message,
                )
                self._socket_client = client
                try:
                    await client.run_forever()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("p2c_live_agent_socket_failed error=%s", type(exc).__name__)
                    await asyncio.sleep(1)
                finally:
                    self._socket_client = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("p2c_live_agent_loop_iteration_failed error=%s", type(exc).__name__)
                await asyncio.sleep(1)

    async def complete_order(self, order_id: str) -> None:
        async with self._lock:
            if order_id in self._completing:
                raise P2CPaymentsError("Completion already in progress for this order")
            self._completing.add(order_id)
        details: P2CPaymentDetails | None = None
        resolved_method_id = ""
        resolved_source = "none"
        try:
            logger.info("p2c_live_agent_complete_started payment_id=%s", order_id)
            order = self._state.get_active_order(order_id)
            if order is None:
                raise P2CPaymentsError("Order is not active")
            session = await self._get_session()
            if session is None:
                raise P2CPaymentsError("Platform session is missing")
            details = await self._payments_client.get_payment(
                payment_id=int(order.id),
                session=session,
            )
            logger.info(
                "p2c_live_agent_complete_details payment_id=%s status=%s details_method_id=%s raw_account_id=%s",
                order.id,
                details.status,
                _bool_flag(details.method_id),
                _bool_flag(_extract_method_id_from_raw(details.raw)),
            )
            status = details.status.lower()
            if status in FINAL_STATUSES:
                self._state.mark_paid(order.id)
                await self._remove_order_from_storage(
                    order.id,
                    final_status="already_final",
                    reason=f"final_status:{details.status}",
                )
                logger.info(
                    "p2c_live_agent_complete_already_final payment_id=%s source_order_id=%s status=%s",
                    order.id,
                    order.source_order_id,
                    details.status,
                )
                return
            if status not in OWNED_OK_STATUSES:
                raise P2CPaymentsError(f"Order status is not completable: {details.status}")
            method_id, source = await self._resolve_method_id(
                order=order,
                details=details,
                session=session,
            )
            resolved_method_id = method_id
            resolved_source = source
            if not method_id:
                raise P2CPaymentsError(
                    "Order has no method id in payment details. Reopen method selection in platform and retry."
                )
            logger.info(
                "p2c_live_agent_complete_method_resolved payment_id=%s method_id=%s source=%s",
                order.id,
                method_id,
                source,
            )
            if not order.method_id and method_id:
                self._state.upsert_active_order(replace(order, method_id=method_id))
            await self._payments_client.complete(
                payment_id=int(order.id),
                method_id=method_id,
                session=session,
            )
            self._state.mark_paid(order.id)
            await self._remove_order_from_storage(order.id, final_status="paid", reason="operator_confirmed")
            logger.info(
                "p2c_live_agent_complete_succeeded payment_id=%s source_order_id=%s",
                order.id,
                order.source_order_id,
            )
        except Exception as exc:
            logger.warning(
                "p2c_live_agent_complete_failed payment_id=%s error=%s reason=%s details_status=%s details_method_id=%s raw_account_id=%s resolved_method_id=%s resolved_source=%s",
                order_id,
                type(exc).__name__,
                str(exc),
                details.status if details is not None else "",
                _bool_flag(details.method_id) if details is not None else "n/a",
                _bool_flag(_extract_method_id_from_raw(details.raw)) if details is not None else "n/a",
                _bool_flag(resolved_method_id),
                resolved_source,
            )
            raise
        finally:
            async with self._lock:
                self._completing.discard(order_id)

    async def cancel_order(self, order_id: str) -> None:
        details: P2CPaymentDetails | None = None
        resolved_method_id = ""
        resolved_source = "none"
        try:
            order = self._state.get_active_order(order_id)
            if order is None:
                raise P2CPaymentsError("Order is not active")
            session = await self._get_session()
            if session is None:
                raise P2CPaymentsError("Platform session is missing")
            details = await self._payments_client.get_payment(
                payment_id=int(order.id),
                session=session,
            )
            logger.info(
                "p2c_live_agent_cancel_details payment_id=%s status=%s details_method_id=%s raw_account_id=%s",
                order.id,
                details.status,
                _bool_flag(details.method_id),
                _bool_flag(_extract_method_id_from_raw(details.raw)),
            )
            method_id, source = await self._resolve_method_id(
                order=order,
                details=details,
                session=session,
            )
            resolved_method_id = method_id
            resolved_source = source
            logger.info(
                "p2c_live_agent_cancel_method_resolved payment_id=%s method_id=%s source=%s",
                order.id,
                method_id,
                source,
            )
            await self._payments_client.cancel(
                payment_id=int(order.id),
                method_id=method_id,
                session=session,
            )
            self._state.mark_paid(order.id)
            await self._remove_order_from_storage(order.id, final_status="cancelled", reason="operator_cancelled")
            logger.info(
                "p2c_live_agent_cancel_succeeded payment_id=%s source_order_id=%s",
                order.id,
                order.source_order_id,
            )
        except Exception as exc:
            logger.warning(
                "p2c_live_agent_cancel_failed payment_id=%s error=%s reason=%s details_status=%s details_method_id=%s raw_account_id=%s resolved_method_id=%s resolved_source=%s",
                order_id,
                type(exc).__name__,
                str(exc),
                details.status if details is not None else "",
                _bool_flag(details.method_id) if details is not None else "n/a",
                _bool_flag(_extract_method_id_from_raw(details.raw)) if details is not None else "n/a",
                _bool_flag(resolved_method_id),
                resolved_source,
            )
            raise

    async def _on_socket_message(self, message: str) -> None:
        if self._state.snapshot().mode == AgentMode.PAUSED:
            return
        if (
            not self._settings.platform_claim_from_snapshot
            and message.startswith('42["list:snapshot"')
        ):
            logger.info("p2c_live_agent_snapshot_skipped")
            return
        received_at = time.perf_counter()
        events = parse_order_events(message)
        if events:
            logger.info("p2c_live_agent_events_received count=%d", len(events))
        for event in events:
            generation = self._pause_generation
            task = asyncio.create_task(self._process_event(event, received_at, generation))
            task.add_done_callback(self._log_process_event_task_result)

    async def _process_event(
        self,
        event: P2COrderEvent,
        received_at: float,
        pause_generation: int,
    ) -> None:
        should_try = False
        skip_reason = ""
        skip_snapshot_mode = ""
        skip_free_slots = 0
        if pause_generation != self._pause_generation:
            skip_reason = "stale_pause_generation"
        else:
            snapshot = self._state.snapshot()
            if snapshot.mode != AgentMode.WAITING:
                skip_reason = f"mode_{snapshot.mode.value}"
                skip_snapshot_mode = snapshot.mode.value
            elif snapshot.free_slots <= 0:
                skip_reason = "no_free_slots"
                skip_free_slots = snapshot.free_slots
            elif not self._state.amount_matches(event.in_amount):
                skip_reason = "amount_filtered"
            else:
                async with self._lock:
                    if pause_generation != self._pause_generation:
                        skip_reason = "stale_pause_generation"
                    elif event.socket_order_id in self._seen or event.socket_order_id in self._inflight:
                        skip_reason = "already_seen_or_inflight"
                    else:
                        self._inflight.add(event.socket_order_id)
                        should_try = True
        if skip_reason == "amount_filtered":
            async with self._lock:
                self._seen.add(event.socket_order_id)
        if not should_try:
            logger.info(
                "p2c_live_agent_event_skipped socket_order_id=%s reason=%s amount=%s currency=%s mode=%s free_slots=%d",
                event.socket_order_id,
                skip_reason,
                event.in_amount,
                event.in_asset,
                skip_snapshot_mode,
                skip_free_slots,
            )
            return
        try:
            await self._claim_and_publish(event, received_at, pause_generation)
        except Exception as exc:
            logger.exception(
                "p2c_live_agent_process_event_unhandled socket_order_id=%s error=%s",
                event.socket_order_id,
                type(exc).__name__,
            )
        finally:
            async with self._lock:
                self._inflight.discard(event.socket_order_id)
                self._seen.add(event.socket_order_id)
                if len(self._seen) > 5000:
                    self._seen.clear()

    async def _claim_and_publish(
        self,
        event: P2COrderEvent,
        received_at: float,
        pause_generation: int,
    ) -> None:
        queue_wait_ms = int((time.perf_counter() - received_at) * 1000)
        logger.info(
            "p2c_live_agent_claim_started socket_order_id=%s amount=%s currency=%s provider=%s queue_wait_ms=%d",
            event.socket_order_id,
            event.in_amount,
            event.in_asset,
            event.provider,
            queue_wait_ms,
        )
        session = await self._get_session()
        if session is None:
            logger.info(
                "p2c_live_agent_claim_skipped_no_session socket_order_id=%s",
                event.socket_order_id,
            )
            return
        take_started: float | None = None
        payment_id: int | None = None
        try:
            if pause_generation != self._pause_generation:
                logger.info(
                    "p2c_live_agent_claim_skipped_stale_generation socket_order_id=%s",
                    event.socket_order_id,
                )
                return
            if self._state.snapshot().mode == AgentMode.PAUSED:
                logger.info(
                    "p2c_live_agent_claim_skipped_paused socket_order_id=%s",
                    event.socket_order_id,
                )
                return
            detect_to_take_start_ms = int((time.perf_counter() - received_at) * 1000)
            take_started = time.perf_counter()
            payment_id = await self._take_payment_id(
                socket_order_id=event.socket_order_id,
                session=session,
                received_at=received_at,
            )
            take_ms = int((time.perf_counter() - take_started) * 1000)
            total_from_detect_ms = int((time.perf_counter() - received_at) * 1000)
            logger.info(
                "p2c_live_agent_take_result socket_order_id=%s payment_id=%s detect_to_take_start_ms=%d take_http_ms=%d total_from_detect_ms=%d",
                event.socket_order_id,
                payment_id,
                detect_to_take_start_ms,
                take_ms,
                total_from_detect_ms,
            )
            logger.info(
                "p2c_live_agent_claim_context socket_order_id=%s brand=%s out_asset=%s url_host=%s payload=%s",
                event.socket_order_id,
                event.brand_name,
                event.out_asset,
                _url_host(event.url),
                _short(event.payload),
            )
            confirm_started = time.perf_counter()
            details = await self._confirm_owned(payment_id=payment_id)
            confirm_ms = int((time.perf_counter() - confirm_started) * 1000)
            logger.info(
                "p2c_live_agent_confirm_result socket_order_id=%s payment_id=%s confirm_ms=%d status=%s",
                event.socket_order_id,
                details.id,
                confirm_ms,
                details.status,
            )
        except P2CPaymentsError as exc:
            reason = "lost_race" if "InvalidStatus" in str(exc) else "api_error"
            logger.info(
                "p2c_live_agent_claim_failed socket_order_id=%s amount=%s currency=%s provider=%s brand=%s queue_wait_ms=%d detect_to_take_start_ms=%d take_http_ms=%s total_from_detect_ms=%d reason=%s error=%s",
                event.socket_order_id,
                event.in_amount,
                event.in_asset,
                event.provider,
                event.brand_name,
                queue_wait_ms,
                detect_to_take_start_ms,
                int((time.perf_counter() - take_started) * 1000) if take_started is not None else "n/a",
                int((time.perf_counter() - received_at) * 1000),
                reason,
                str(exc),
            )
            if self._is_penalty_error(exc):
                self._activate_penalty_backoff(
                    reference_id=event.socket_order_id,
                    retry_after_seconds=self._extract_retry_after_seconds(exc),
                    error=str(exc),
                )
                return
            if self._is_auth_or_forbidden_error(exc):
                self._pause_due_to_auth_error(
                    context="claim",
                    reference_id=event.socket_order_id,
                    error=str(exc),
                )
                return
            if payment_id is not None and reason == "api_error":
                await self._handle_taken_order_with_unknown_status(
                    event=event,
                    payment_id=payment_id,
                )
            return
        order = ActiveOrder(
            id=str(details.id),
            amount=details.in_amount,
            currency=details.in_asset,
            direction="P2C",
            url=details.url,
            provider=details.provider,
            payload=details.payload,
            method_id=details.method_id,
            source_order_id=event.socket_order_id,
            take_http_ms=take_ms,
            claim_total_ms=total_from_detect_ms,
            claimed_at=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(minutes=3),
        )
        self._state.upsert_active_order(order)
        await self._persist_active_order(order)
        logger.info(
            "p2c_live_agent_claim_succeeded socket_order_id=%s payment_id=%s amount=%s currency=%s out_amount=%s out_asset=%s provider=%s brand=%s url_host=%s payload=%s",
            event.socket_order_id,
            details.id,
            details.in_amount,
            details.in_asset,
            details.out_amount,
            details.out_asset,
            details.provider,
            details.brand_name,
            _url_host(details.url),
            _short(details.payload),
        )
        try:
            await self._notify_order_ready(order)
        except Exception as exc:
            self._state.pause()
            if self._socket_client is not None:
                self._socket_client.stop()
            logger.exception(
                "p2c_live_agent_notify_failed_paused payment_id=%s source_order_id=%s error=%s",
                order.id,
                order.source_order_id,
                type(exc).__name__,
            )
            return

    async def _handle_taken_order_with_unknown_status(
        self,
        *,
        event: P2COrderEvent,
        payment_id: int,
    ) -> None:
        order = ActiveOrder(
            id=str(payment_id),
            amount=str(event.in_amount),
            currency=event.in_asset,
            direction="P2C",
            url=event.url,
            provider=event.provider,
            payload=event.payload,
            method_id="",
            source_order_id=event.socket_order_id,
            take_http_ms=None,
            claim_total_ms=None,
            claimed_at=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(minutes=3),
        )
        self._state.upsert_active_order(order)
        await self._persist_active_order(order)
        self._state.pause()
        if self._socket_client is not None:
            self._socket_client.stop()
        logger.warning(
            "p2c_live_agent_taken_unconfirmed_paused payment_id=%s source_order_id=%s provider=%s brand=%s",
            order.id,
            order.source_order_id,
            event.provider,
            event.brand_name,
        )
        try:
            await self._notify_order_ready(order)
        except Exception as exc:
            logger.exception(
                "p2c_live_agent_notify_failed_after_taken_unconfirmed payment_id=%s error=%s",
                order.id,
                type(exc).__name__,
            )

    async def load_persisted_orders(self) -> int:
        try:
            orders = await self._active_order_repository.list_all()
        except Exception as exc:
            logger.warning("p2c_live_agent_load_orders_failed error=%s", type(exc).__name__)
            return 0
        for order in orders:
            self._state.upsert_active_order(order)
        logger.info("p2c_live_agent_orders_loaded count=%d", len(orders))
        return len(orders)

    async def _persist_active_order(self, order: ActiveOrder) -> None:
        try:
            await self._active_order_repository.upsert(order)
        except Exception as exc:
            logger.warning(
                "p2c_live_agent_order_persist_failed payment_id=%s error=%s",
                order.id,
                type(exc).__name__,
            )

    async def _remove_order_from_storage(
        self,
        order_id: str,
        *,
        final_status: str,
        reason: str = "",
    ) -> None:
        try:
            await self._active_order_repository.remove(
                order_id,
                final_status=final_status,
                reason=reason,
            )
        except Exception as exc:
            logger.warning(
                "p2c_live_agent_order_remove_failed payment_id=%s error=%s",
                order_id,
                type(exc).__name__,
            )

    async def _confirm_owned(
        self,
        *,
        payment_id: int,
    ) -> P2CPaymentDetails:
        attempts = 3
        last_error: Exception | None = None
        for _attempt in range(attempts):
            session = await self._get_session(force_refresh=_attempt > 0)
            if session is None:
                raise P2CPaymentsError("Platform session is missing")
            try:
                details = await self._payments_client.get_payment(payment_id=payment_id, session=session)
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.2)
                continue
            if details.id != payment_id:
                raise P2CPaymentsError("Payment id mismatch in details")
            if details.status.lower() not in OWNED_OK_STATUSES:
                raise P2CPaymentsError(f"Payment status is not owned-confirmed: {details.status}")
            return details
        if last_error is not None:
            raise P2CPaymentsError(f"Cannot confirm owned payment: {type(last_error).__name__}")
        raise P2CPaymentsError("Cannot confirm owned payment")

    async def _get_session(self, *, force_refresh: bool = False) -> PlatformSession | None:
        now = time.monotonic()
        if (
            not force_refresh
            and self._session_l1_cache is not None
            and (now - self._session_l1_cached_at_monotonic) <= self._session_l1_ttl_seconds
            and self._session_l1_cache.cookie_header
        ):
            return self._session_l1_cache
        session = await self._session_repository.current()
        if session is not None and session.cookie_header:
            self._session_l1_cache = session
            self._session_l1_cached_at_monotonic = now
            return session
        self._session_l1_cache = None
        self._session_l1_cached_at_monotonic = 0.0
        return None

    async def _take_payment_id(
        self,
        *,
        socket_order_id: str,
        session: PlatformSession,
        received_at: float,
    ) -> int:
        return await self._take_with_dispatch_log(
            socket_order_id=socket_order_id,
            session=session,
            received_at=received_at,
            client_slot=0,
        )

    async def _take_with_dispatch_log(
        self,
        *,
        socket_order_id: str,
        session: PlatformSession,
        received_at: float,
        client_slot: int,
    ) -> int:
        detect_to_post_dispatch_ms = int((time.perf_counter() - received_at) * 1000)
        logger.info(
            "p2c_live_agent_take_post_dispatch socket_order_id=%s attempt=%d detect_to_post_dispatch_ms=%d",
            socket_order_id,
            client_slot,
            detect_to_post_dispatch_ms,
        )
        started = time.perf_counter()
        try:
            payment_id = await self._payments_client.take(
                socket_order_id=socket_order_id,
                session=session,
                client_slot=client_slot,
            )
        except Exception as exc:
            logger.info(
                "p2c_live_agent_take_attempt_failed socket_order_id=%s attempt=%d attempt_http_ms=%d error=%s",
                socket_order_id,
                client_slot,
                int((time.perf_counter() - started) * 1000),
                type(exc).__name__,
            )
            raise
        logger.info(
            "p2c_live_agent_take_attempt_succeeded socket_order_id=%s attempt=%d payment_id=%s attempt_http_ms=%d",
            socket_order_id,
            client_slot,
            payment_id,
            int((time.perf_counter() - started) * 1000),
        )
        return payment_id

    @staticmethod
    def _is_auth_or_forbidden_error(exc: Exception) -> bool:
        text = str(exc)
        if "status 401" in text:
            return True
        return "status 403" in text and "MerchantPenalized" not in text

    @staticmethod
    def _is_penalty_error(exc: Exception) -> bool:
        return "MerchantPenalized" in str(exc)

    @staticmethod
    def _extract_retry_after_seconds(exc: Exception) -> int:
        match = re.search(r'"retry_after"\s*:\s*(\d+)', str(exc))
        if match is None:
            return 60
        try:
            return max(int(match.group(1)), 0)
        except ValueError:
            return 60

    def _pause_due_to_auth_error(self, *, context: str, reference_id: str, error: str) -> None:
        self._state.pause()
        self.on_pause()
        logger.warning(
            "p2c_live_agent_paused_due_to_auth context=%s reference_id=%s error=%s",
            context,
            reference_id,
            error,
        )

    def _activate_penalty_backoff(self, *, reference_id: str, retry_after_seconds: int, error: str) -> None:
        delay_seconds = max(retry_after_seconds, 0)
        self._penalty_events += 1
        self._state.pause()
        generation = self._increment_pause_generation_and_stop_socket()
        self._cancel_penalty_resume_task()
        self._penalty_resume_task = asyncio.create_task(
            self._resume_after_penalty(delay_seconds=delay_seconds, generation=generation)
        )
        self._penalty_resume_task.add_done_callback(self._log_background_task_result)
        logger.warning(
            "p2c_live_agent_penalty_backoff_started reference_id=%s retry_after=%d penalty_events=%d error=%s",
            reference_id,
            delay_seconds,
            self._penalty_events,
            error,
        )

    async def _resume_after_penalty(self, *, delay_seconds: int, generation: int) -> None:
        await asyncio.sleep(delay_seconds)
        if generation != self._pause_generation:
            logger.info(
                "p2c_live_agent_penalty_backoff_resume_skipped reason=stale_generation generation=%d current=%d",
                generation,
                self._pause_generation,
            )
            return
        snapshot = self._state.run()
        logger.info(
            "p2c_live_agent_penalty_backoff_resumed retry_after=%d mode=%s free_slots=%d",
            delay_seconds,
            snapshot.mode.value,
            snapshot.free_slots,
        )

    def _increment_pause_generation_and_stop_socket(self) -> int:
        self._pause_generation += 1
        if self._socket_client is not None:
            self._socket_client.stop()
        return self._pause_generation

    def _cancel_penalty_resume_task(self) -> None:
        task = self._penalty_resume_task
        if task is not None and not task.done():
            task.cancel()
        self._penalty_resume_task = None

    @staticmethod
    def _log_process_event_task_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception(
                "p2c_live_agent_process_event_task_failed error=%s",
                type(exc).__name__,
                exc_info=exc,
            )

    @staticmethod
    def _log_background_task_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        logger.exception(
            "p2c_live_agent_background_task_failed error=%s",
            type(exc).__name__,
            exc_info=exc,
        )

    async def _resolve_method_id(
        self,
        *,
        order: ActiveOrder,
        details: P2CPaymentDetails,
        session,
    ) -> tuple[str, str]:
        if order.method_id:
            logger.info(
                "p2c_live_agent_method_source payment_id=%s source=order",
                order.id,
            )
            return order.method_id, "order"
        if details.method_id:
            logger.info(
                "p2c_live_agent_method_source payment_id=%s source=details",
                order.id,
            )
            return details.method_id, "details"
        from_raw = _extract_method_id_from_raw(details.raw)
        if from_raw:
            logger.info(
                "p2c_live_agent_method_source payment_id=%s source=raw_account",
                order.id,
            )
            return from_raw, "raw_account"
        if self._cached_account_method_id:
            logger.info(
                "p2c_live_agent_method_source payment_id=%s source=accounts_cache",
                order.id,
            )
            return self._cached_account_method_id, "accounts_cache"
        try:
            accounts = await self._payments_client.list_accounts(session=session)
        except Exception as exc:
            logger.warning("p2c_live_agent_accounts_fetch_failed error=%s", type(exc).__name__)
            return "", "none"
        logger.info(
            "p2c_live_agent_accounts_fetched payment_id=%s total=%d",
            order.id,
            len(accounts),
        )
        active_ids = []
        all_ids = []
        for account in accounts:
            account_id = account.get("id")
            if isinstance(account_id, int):
                account_id = str(account_id)
            if not isinstance(account_id, str) or not account_id:
                continue
            all_ids.append(account_id)
            status = str(account.get("status", "")).lower()
            if status == "active":
                active_ids.append(account_id)
        if active_ids:
            self._cached_account_method_id = active_ids[0]
            logger.info(
                "p2c_live_agent_method_source payment_id=%s source=accounts_active",
                order.id,
            )
            return active_ids[0], "accounts_active"
        if all_ids:
            self._cached_account_method_id = all_ids[0]
            logger.info(
                "p2c_live_agent_method_source payment_id=%s source=accounts_any",
                order.id,
            )
            return all_ids[0], "accounts_any"
        logger.warning(
            "p2c_live_agent_method_source_missing payment_id=%s",
            order.id,
        )
        return "", "none"


def _short(value: str, limit: int = 24) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def _url_host(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""


def _extract_method_id_from_raw(raw: dict[str, object]) -> str:
    account = raw.get("account")
    if isinstance(account, dict):
        value = account.get("id")
        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
    return ""


def _bool_flag(value: str) -> str:
    return "yes" if bool(value) else "no"
