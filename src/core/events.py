from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import UUID


EventType = Literal[
    "step_started",
    "step_completed",
    "step_failed",
    "retry_started",
    "hil_requested",
    "hil_responded",
    "run_completed",
    "run_failed",
    "run_cancelled",
]


@dataclass
class PipelineEvent:
    run_id: UUID
    event_type: EventType
    step_number: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """Async event bus for pipeline events. Subscribers filter by run_id."""

    def __init__(self) -> None:
        self._subscribers: dict[UUID | None, list[Callable]] = {}
        # None key = global subscribers (all events)

    def subscribe(self, callback: Callable, run_id: UUID | None = None) -> None:
        """Subscribe to events. If run_id is None, receives all events."""
        if run_id not in self._subscribers:
            self._subscribers[run_id] = []
        self._subscribers[run_id].append(callback)

    def unsubscribe(self, callback: Callable, run_id: UUID | None = None) -> None:
        """Remove a subscription."""
        if run_id in self._subscribers:
            self._subscribers[run_id] = [
                cb for cb in self._subscribers[run_id] if cb is not callback
            ]
            if not self._subscribers[run_id]:
                del self._subscribers[run_id]

    async def publish(self, event: PipelineEvent) -> None:
        """Publish an event to all matching subscribers."""
        callbacks = []
        # Global subscribers
        callbacks.extend(self._subscribers.get(None, []))
        # Run-specific subscribers
        callbacks.extend(self._subscribers.get(event.run_id, []))

        for callback in callbacks:
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass  # Don't let subscriber errors break the pipeline

    def clear(self, run_id: UUID | None = None) -> None:
        """Clear subscribers for a run, or all if run_id is None."""
        if run_id is None:
            self._subscribers.clear()
        elif run_id in self._subscribers:
            del self._subscribers[run_id]


# Singleton event bus instance
event_bus = EventBus()
