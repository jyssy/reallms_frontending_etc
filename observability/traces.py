"""Bounded process-local retention and projections for observed trace events."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from .contract import MODEL_ROLES, configured_model_catalog, parse_observed_result

MAX_RUNS = 24
MAX_EVENTS_PER_RUN = 128
MAX_TOTAL_EVENTS = 512

_ROLE_COMPONENTS = {
    "router": frozenset({"router"}),
    "embedding": frozenset({"embedding"}),
    "reranker": frozenset({"reranker"}),
    "reviewer": frozenset({"specialist"}),
    "judge": frozenset({"judge", "revision"}),
}


@dataclass
class _Run:
    run_id: str
    events: dict[int, dict[str, Any]] = field(default_factory=dict)
    result: dict[str, Any] | None = None


class TraceStore:
    """Thread-safe run store with duplicate suppression and deterministic ordering."""

    def __init__(
        self,
        *,
        max_runs: int = MAX_RUNS,
        max_events_per_run: int = MAX_EVENTS_PER_RUN,
        max_total_events: int = MAX_TOTAL_EVENTS,
    ) -> None:
        self.max_runs = max_runs
        self.max_events_per_run = max_events_per_run
        self.max_total_events = max_total_events
        self._runs: OrderedDict[str, _Run] = OrderedDict()
        self._lock = Lock()
        self._dropped_events = 0

    def add_event(self, event: dict[str, Any]) -> bool:
        """Store one already-validated event; return false for duplicates."""
        run_id = event["run_id"]
        sequence = event["sequence"]
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                run = _Run(run_id)
                self._runs[run_id] = run
            if sequence in run.events:
                return False
            if len(run.events) >= self.max_events_per_run:
                oldest = min(run.events)
                del run.events[oldest]
                self._dropped_events += 1
            run.events[sequence] = deepcopy(event)
            self._runs.move_to_end(run_id)
            self._enforce_bounds()
            return True

    def finish_run(self, run_id: str, result: object) -> None:
        """Attach only the sanitized result projection to an existing run."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.result = parse_observed_result(result)
                self._runs.move_to_end(run_id)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            runs = [self._public_run(run) for run in reversed(self._runs.values())]
            event_count = sum(len(run.events) for run in self._runs.values())
            return {
                "contract_version": 1,
                "source": "live_process",
                "observed": bool(runs),
                "retention": {
                    "scope": "process_memory",
                    "max_runs": self.max_runs,
                    "max_events_per_run": self.max_events_per_run,
                    "max_total_events": self.max_total_events,
                    "stored_events": event_count,
                    "dropped_events": self._dropped_events,
                },
                "runs": runs,
            }

    def model_snapshot(self, configured: object) -> dict[str, Any]:
        catalog = configured_model_catalog(configured)
        trace = self.snapshot()
        events = [event for run in trace["runs"] for event in run["events"]]
        results = [run["result"] for run in trace["runs"] if run["result"]]
        models = []
        for role in MODEL_ROLES:
            role_events = [
                event for event in events if event["component"] in _ROLE_COMPONENTS[role]
            ]
            observed_names = sorted(
                {
                    result["model_roles"][role]
                    for result in results
                    if role in result["model_roles"]
                }
            )
            states = []
            if role in catalog:
                states.append("configured")
            if observed_names:
                states.append("observed")
            if any(event["status"] == "skipped" for event in role_events):
                states.append("skipped")
            fallback = any(event["metadata"].get("fallback") is True for event in role_events)
            if fallback:
                states.append("fallback")
            if role_events and not observed_names:
                states.append("not-reported")
            models.append(
                {
                    "role": role,
                    "configured_model": catalog.get(role),
                    "observed_models": observed_names,
                    "states": states,
                    "event_count": len(role_events),
                }
            )

        providers = []
        for provider in ("local", "remote"):
            provider_events = [
                event
                for event in events
                if event["component"] == "provider"
                and event["metadata"].get("provider") == provider
            ]
            providers.append(
                {
                    "provider": provider,
                    "observed": bool(provider_events),
                    "attempts": sum(
                        event["event_type"] == "provider.attempt" for event in provider_events
                    ),
                    "retries": sum(
                        event["event_type"] == "provider.retry" for event in provider_events
                    ),
                    "degraded": any(
                        event["status"] in {"degraded", "failed"} for event in provider_events
                    ),
                }
            )
        return {
            "contract_version": 1,
            "source": trace["source"],
            "observed": trace["observed"],
            "models": models,
            "providers": providers,
            "retention": trace["retention"],
        }

    def reset(self) -> None:
        with self._lock:
            self._runs.clear()
            self._dropped_events = 0

    def _enforce_bounds(self) -> None:
        while len(self._runs) > self.max_runs:
            _, evicted = self._runs.popitem(last=False)
            self._dropped_events += len(evicted.events)
        while sum(len(run.events) for run in self._runs.values()) > self.max_total_events:
            oldest_run_id = next(iter(self._runs))
            oldest_run = self._runs[oldest_run_id]
            oldest_sequence = min(oldest_run.events)
            del oldest_run.events[oldest_sequence]
            self._dropped_events += 1
            if not oldest_run.events:
                del self._runs[oldest_run_id]

    @staticmethod
    def _public_run(run: _Run) -> dict[str, Any]:
        events = [deepcopy(run.events[key]) for key in sorted(run.events)]
        terminal = next(
            (event for event in reversed(events) if event["event_type"] == "run.completed"),
            None,
        )
        status = terminal["status"] if terminal else (events[-1]["status"] if events else "started")
        return {
            "run_id": run.run_id,
            "lifecycle": "complete" if terminal else "in_progress",
            "status": status,
            "first_sequence": events[0]["sequence"] if events else None,
            "last_sequence": events[-1]["sequence"] if events else None,
            "event_count": len(events),
            "result": deepcopy(run.result),
            "events": events,
        }


trace_store = TraceStore()


def mock_trace_snapshot() -> dict[str, Any]:
    """Return a visibly synthetic trace without mutating process-local state."""
    events = [
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 1,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "event_type": "run.started",
            "component": "pipeline",
            "status": "started",
            "duration_ms": None,
            "metadata": {"judge_enabled": True},
        },
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 2,
            "timestamp": "2026-01-01T00:00:01+00:00",
            "event_type": "specialist.completed",
            "component": "specialist",
            "status": "degraded",
            "duration_ms": 240,
            "metadata": {"task_type": "coding", "fallback": True},
        },
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 3,
            "timestamp": "2026-01-01T00:00:02+00:00",
            "event_type": "run.completed",
            "component": "pipeline",
            "status": "degraded",
            "duration_ms": 480,
            "metadata": {"result_status": "degraded_success"},
        },
    ]
    return {
        "contract_version": 1,
        "source": "synthetic_fixture",
        "observed": False,
        "retention": {
            "scope": "fixture",
            "max_runs": 1,
            "max_events_per_run": len(events),
            "max_total_events": len(events),
            "stored_events": len(events),
            "dropped_events": 0,
        },
        "runs": [
            {
                "run_id": events[0]["run_id"],
                "lifecycle": "complete",
                "status": "degraded",
                "first_sequence": 1,
                "last_sequence": 3,
                "event_count": 3,
                "result": {
                    "status": "degraded_success",
                    "task_type": "coding",
                    "model_roles": {"reviewer": "synthetic-reviewer"},
                },
                "events": events,
            }
        ],
    }


def mock_model_snapshot(configured: object) -> dict[str, Any]:
    fixture = mock_trace_snapshot()
    store = TraceStore(max_runs=1, max_events_per_run=8, max_total_events=8)
    run = fixture["runs"][0]
    for event in run["events"]:
        store.add_event(event)
    store.finish_run(run["run_id"], run["result"])
    snapshot = store.model_snapshot(configured)
    snapshot["source"] = "synthetic_fixture"
    snapshot["observed"] = False
    return snapshot
