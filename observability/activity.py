from collections import deque
from datetime import UTC, datetime
from threading import Lock
from typing import Any

_MAX_EVENTS = 100
_events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_lock = Lock()
_sequence = 0


def record_event(event_type: str, status: str, **metadata: Any) -> dict[str, Any]:
    """Record sanitized operational metadata in a bounded process-local buffer."""
    global _sequence
    with _lock:
        _sequence += 1
        event = {
            "sequence": _sequence,
            "timestamp": datetime.now(UTC).isoformat(),
            "type": event_type,
            "status": status,
            "metadata": metadata,
        }
        _events.append(event)
        return event.copy()


def recent_events() -> list[dict[str, Any]]:
    with _lock:
        return [event.copy() for event in reversed(_events)]


def reset_events() -> None:
    """Clear process-local activity for deterministic tests."""
    global _sequence
    with _lock:
        _events.clear()
        _sequence = 0
