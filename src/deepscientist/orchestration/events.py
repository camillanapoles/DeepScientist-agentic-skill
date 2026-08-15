"""Event helpers and an in-process pub/sub bus for orchestration.

The DB is the durable source of truth; the bus is a lightweight,
synchronous notification layer that lets other daemon components react
to state transitions (e.g. to trigger a runner install or push a UI
update). Subscribers receive events *after* they have been durably
appended.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from ..shared import generate_id, utc_now

# Re-exported for convenience.
__all__ = ["EventBus", "build_event"]

EventHandler = Callable[[dict[str, Any]], None]


def build_event(
    event_type: str,
    *,
    object_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a canonical event envelope (does not persist it)."""
    return {
        "event_id": generate_id("evt"),
        "event_type": event_type,
        "object_id": object_id,
        "created_at": utc_now(),
        "payload": payload or {},
    }


class EventBus:
    """Synchronous, in-process publish/subscribe bus.

    Handlers are called on the publishing thread. A failing handler is
    isolated so it cannot break the orchestration transaction; the
    exception is swallowed after being passed to the optional
    ``error_handler``.
    """

    def __init__(self, error_handler: Callable[[Exception], None] | None = None) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._wildcard: list[EventHandler] = []
        self._error_handler = error_handler

    def subscribe(
        self, event_type: str | None, handler: EventHandler
    ) -> Callable[[], None]:
        """Subscribe to one event type, or all events when ``event_type`` is None."""
        if event_type is None:
            self._wildcard.append(handler)
        else:
            self._handlers[event_type].append(handler)

        def _unsubscribe() -> None:
            if event_type is None:
                if handler in self._wildcard:
                    self._wildcard.remove(handler)
            else:
                if handler in self._handlers[event_type]:
                    self._handlers[event_type].remove(handler)

        return _unsubscribe

    def publish(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or "")
        for handler in list(self._handlers.get(event_type, ())) + list(self._wildcard):
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001 - isolate subscribers
                if self._error_handler is not None:
                    try:
                        self._error_handler(exc)
                    except Exception:
                        pass
