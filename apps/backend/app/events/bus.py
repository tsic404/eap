"""Async event bus over blinker signals (replaces NestJS's @nestjs/event-emitter)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from blinker import Signal

EventHandler = Callable[..., Awaitable[None]]


class EventBus:
    """Named async pub/sub.

    Subscribers are async callables registered per event name. ``emit`` awaits
    every subscriber in registration order before returning, so callers can rely
    on handlers having run. Decoupling lives in what a handler *does* — e.g. the
    audit handler enqueues to RQ instead of blocking on a database write.
    """

    def __init__(self) -> None:
        self._signals: dict[str, Signal] = {}

    def subscribe(self, event: str, handler: EventHandler) -> EventHandler:
        """Register ``handler`` for ``event`` and return it (for decorator use)."""
        self._signal(event).connect(handler, weak=False)
        return handler

    def unsubscribe(self, event: str, handler: EventHandler) -> None:
        signal = self._signals.get(event)
        if signal is not None:
            signal.disconnect(handler)

    async def emit(self, name: str, **payload: Any) -> None:
        """Dispatch ``name`` to every subscriber, awaiting each in order."""
        signal = self._signals.get(name)
        if signal is not None:
            await signal.send_async(name, **payload)

    def _signal(self, event: str) -> Signal:
        signal = self._signals.get(event)
        if signal is None:
            signal = Signal(event)
            self._signals[event] = signal
        return signal


# Process-wide bus. Event wiring is global and idempotent; registration happens
# once in ``create_app`` (see app/events/audit.register_audit_log_handler).
bus = EventBus()
