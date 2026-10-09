"""Bounded process-local retention and projections for observed trace events."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from .contract import (
    ORCHESTRATION_ACTOR_ROLES,
    configured_model_catalog,
    parse_observed_result,
)

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
_WORKFLOW_STAGES = (
    {
        "id": "route",
        "label": "Route",
        "purpose": "Classify the request and select the workflow.",
        "components": frozenset({"router"}),
        "model_roles": ("router",),
        "duration_event": "router.completed",
    },
    {
        "id": "retrieve",
        "label": "Retrieve",
        "purpose": "Find, embed, and rank approved context.",
        "components": frozenset({"retrieval", "embedding", "reranker"}),
        "model_roles": ("embedding", "reranker"),
        "duration_event": "retrieval.completed",
    },
    {
        "id": "review",
        "label": "Specialist",
        "purpose": "Produce the role-specific reviewed result.",
        "components": frozenset({"specialist"}),
        "model_roles": ("reviewer",),
        "duration_event": "specialist.completed",
    },
    {
        "id": "judge",
        "label": "Judge",
        "purpose": "Evaluate whether the result needs revision.",
        "components": frozenset({"judge"}),
        "model_roles": ("judge",),
        "duration_event": "judge_critique.completed",
    },
    {
        "id": "revise",
        "label": "Revision",
        "purpose": "Apply corrections when the judge requests them.",
        "components": frozenset({"revision"}),
        "model_roles": ("judge",),
        "duration_event": "revision.completed",
    },
    {
        "id": "complete",
        "label": "Complete",
        "purpose": "Publish the sanitized structured run status.",
        "components": frozenset({"pipeline"}),
        "model_roles": (),
        "duration_event": "run.completed",
    },
)


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

    def finish_run(self, run_id: str, result: object, configured: object = None) -> None:
        """Attach only the sanitized result projection to an existing run."""
        del configured
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.result = parse_observed_result(result)
                self._runs.move_to_end(run_id)

    def snapshot(self, configured: object = None) -> dict[str, Any]:
        catalog = configured_model_catalog(configured)
        with self._lock:
            runs = [
                self._public_run(run, catalog) for run in reversed(self._runs.values())
            ]
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
        for role in ORCHESTRATION_ACTOR_ROLES:
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
    def _public_run(run: _Run, catalog: dict[str, str]) -> dict[str, Any]:
        events = [deepcopy(run.events[key]) for key in sorted(run.events)]
        terminal = next(
            (event for event in reversed(events) if event["event_type"] == "run.completed"),
            None,
        )
        status = terminal["status"] if terminal else (events[-1]["status"] if events else "started")
        workflow = _workflow_stages(events, run.result, catalog, terminal is not None)
        return {
            "run_id": run.run_id,
            "lifecycle": "complete" if terminal else "in_progress",
            "status": status,
            "first_sequence": events[0]["sequence"] if events else None,
            "last_sequence": events[-1]["sequence"] if events else None,
            "event_count": len(events),
            "result": deepcopy(run.result),
            "summary": _run_summary(events, workflow, terminal),
            "models": _run_model_roster(events, run.result, catalog),
            "workflow": workflow,
            "events": events,
        }


trace_store = TraceStore()


def _stage_status(events: list[dict[str, Any]], *, complete: bool) -> str:
    if not events:
        return "not_run" if complete else "pending"
    statuses = {event["status"] for event in events}
    if "failed" in statuses:
        return "failed"
    if "degraded" in statuses:
        return "degraded"
    if statuses == {"skipped"}:
        return "skipped"
    if "retrying" in statuses:
        return "retrying"
    if "started" in statuses and "success" not in statuses:
        return "started"
    return "success"


def _model_attribution(
    role: str,
    events: list[dict[str, Any]],
    result: dict[str, Any] | None,
    catalog: dict[str, str],
) -> dict[str, Any] | None:
    configured = catalog.get(role)
    observed = (result or {}).get("model_roles", {}).get(role)
    if not events and not configured and not observed:
        return None
    if events and all(event["status"] == "skipped" for event in events):
        state = "skipped"
    elif any(event["metadata"].get("fallback") is True for event in events):
        state = "fallback"
    elif observed:
        state = "observed"
    elif configured:
        state = "configured"
    else:
        state = "not-reported"
    return {"role": role, "name": observed or configured, "state": state}


def _run_model_roster(
    events: list[dict[str, Any]],
    result: dict[str, Any] | None,
    catalog: dict[str, str],
) -> list[dict[str, Any]]:
    roster = []
    for role in ORCHESTRATION_ACTOR_ROLES:
        role_events = [
            event for event in events if event["component"] in _ROLE_COMPONENTS[role]
        ]
        attribution = _model_attribution(role, role_events, result, catalog) or {
            "role": role,
            "name": None,
            "state": "not-reported",
        }
        roster.append({**attribution, "event_count": len(role_events)})
    return roster


def _latest_component_event(
    events: list[dict[str, Any]], component: str
) -> dict[str, Any] | None:
    return next(
        (event for event in reversed(events) if event["component"] == component),
        None,
    )


def _run_summary(
    events: list[dict[str, Any]],
    workflow: list[dict[str, Any]],
    terminal: dict[str, Any] | None,
) -> dict[str, Any]:
    started = next(
        (event for event in events if event["event_type"] == "run.started"),
        None,
    )
    provider_events = [event for event in events if event["component"] == "provider"]
    retrieval = _latest_component_event(events, "retrieval")
    embedding = _latest_component_event(events, "embedding")
    reranking = _latest_component_event(events, "reranker")
    reported_durations = [
        stage["duration_ms"]
        for stage in workflow
        if stage["id"] != "complete" and stage["duration_ms"] is not None
    ]
    providers = sorted(
        {
            event["metadata"]["provider"]
            for event in provider_events
            if "provider" in event["metadata"]
        }
    )
    retrieval_metadata = retrieval["metadata"] if retrieval else {}
    terminal_metadata = terminal["metadata"] if terminal else {}
    embedding_metadata = embedding["metadata"] if embedding else {}
    reranking_metadata = reranking["metadata"] if reranking else {}
    return {
        "started_at": started["timestamp"] if started else None,
        "completed_at": terminal["timestamp"] if terminal else None,
        "total_duration_ms": terminal["duration_ms"] if terminal else None,
        "reported_stage_duration_ms": sum(reported_durations) if reported_durations else None,
        "reported_stage_count": len(reported_durations),
        "provider_attempts": sum(
            event["event_type"] == "provider.attempt" for event in provider_events
        ),
        "provider_retries": sum(
            event["event_type"] == "provider.retry" for event in provider_events
        ),
        "providers": providers,
        "fallback": any(event["metadata"].get("fallback") is True for event in events),
        "retrieval": {
            "retrieval_used": retrieval_metadata.get(
                "retrieval_used", terminal_metadata.get("retrieval_used")
            ),
            "context_used": retrieval_metadata.get(
                "context_used", terminal_metadata.get("context_used")
            ),
            "candidate_count": retrieval_metadata.get("candidate_count"),
            "selected_count": retrieval_metadata.get("selected_count"),
        },
        "resources": {
            "embedding_batch_size": embedding_metadata.get("batch_size"),
            "rerank_candidate_count": reranking_metadata.get("candidate_count"),
            "rerank_selected_count": reranking_metadata.get("selected_count"),
        },
    }


def _workflow_stages(
    events: list[dict[str, Any]],
    result: dict[str, Any] | None,
    catalog: dict[str, str],
    complete: bool,
) -> list[dict[str, Any]]:
    workflow = []
    for definition in _WORKFLOW_STAGES:
        stage_events = [
            event for event in events if event["component"] in definition["components"]
        ]
        if definition["id"] == "complete":
            stage_events = [
                event for event in stage_events if event["event_type"] == "run.completed"
            ]
        duration = next(
            (
                event["duration_ms"]
                for event in reversed(stage_events)
                if event["event_type"] == definition["duration_event"]
                and event["duration_ms"] is not None
            ),
            None,
        )
        models = []
        for role in definition["model_roles"]:
            role_events = [
                event for event in stage_events if event["component"] in _ROLE_COMPONENTS[role]
            ]
            attribution = _model_attribution(role, role_events, result, catalog)
            if attribution:
                models.append(attribution)
        workflow.append(
            {
                "id": definition["id"],
                "label": definition["label"],
                "purpose": definition["purpose"],
                "status": _stage_status(stage_events, complete=complete),
                "duration_ms": duration,
                "event_count": len(stage_events),
                "models": models,
            }
        )
    return workflow


def mock_trace_snapshot(configured: object = None) -> dict[str, Any]:
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
            "event_type": "router.completed",
            "component": "router",
            "status": "success",
            "duration_ms": 12,
            "metadata": {"task_type": "coding"},
        },
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 3,
            "timestamp": "2026-01-01T00:00:02+00:00",
            "event_type": "retrieval.completed",
            "component": "retrieval",
            "status": "success",
            "duration_ms": 80,
            "metadata": {
                "retrieval_used": True,
                "context_used": True,
                "candidate_count": 12,
                "selected_count": 4,
            },
        },
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 4,
            "timestamp": "2026-01-01T00:00:03+00:00",
            "event_type": "embedding.completed",
            "component": "embedding",
            "status": "success",
            "duration_ms": 24,
            "metadata": {"batch_size": 4},
        },
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 5,
            "timestamp": "2026-01-01T00:00:04+00:00",
            "event_type": "reranking.completed",
            "component": "reranker",
            "status": "success",
            "duration_ms": 18,
            "metadata": {"candidate_count": 12, "selected_count": 4},
        },
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 6,
            "timestamp": "2026-01-01T00:00:05+00:00",
            "event_type": "provider.attempt",
            "component": "provider",
            "status": "success",
            "duration_ms": 220,
            "metadata": {
                "provider": "remote",
                "operation": "completion",
                "attempt": 1,
                "max_attempts": 2,
            },
        },
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 7,
            "timestamp": "2026-01-01T00:00:06+00:00",
            "event_type": "specialist.completed",
            "component": "specialist",
            "status": "degraded",
            "duration_ms": 240,
            "metadata": {"task_type": "coding", "fallback": True},
        },
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 8,
            "timestamp": "2026-01-01T00:00:07+00:00",
            "event_type": "judge_critique.completed",
            "component": "judge",
            "status": "success",
            "duration_ms": 100,
            "metadata": {"judge_enabled": True, "revision_required": False},
        },
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 9,
            "timestamp": "2026-01-01T00:00:08+00:00",
            "event_type": "revision.skipped",
            "component": "revision",
            "status": "skipped",
            "duration_ms": None,
            "metadata": {"revision_required": False},
        },
        {
            "contract_version": 1,
            "run_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 10,
            "timestamp": "2026-01-01T00:00:09+00:00",
            "event_type": "run.completed",
            "component": "pipeline",
            "status": "degraded",
            "duration_ms": 480,
            "metadata": {"result_status": "degraded_success"},
        },
    ]
    store = TraceStore(max_runs=1, max_events_per_run=len(events), max_total_events=len(events))
    for event in events:
        store.add_event(event)
    store.finish_run(
        events[0]["run_id"],
        {
            "status": "degraded_success",
            "task_type": "coding",
            "model_roles": {},
        },
    )
    snapshot = store.snapshot(configured)
    snapshot["source"] = "synthetic_fixture"
    snapshot["observed"] = False
    snapshot["retention"]["scope"] = "fixture"
    return snapshot


def mock_model_snapshot(configured: object) -> dict[str, Any]:
    fixture = mock_trace_snapshot(configured)
    run = fixture["runs"][0]
    store = TraceStore(
        max_runs=1,
        max_events_per_run=len(run["events"]),
        max_total_events=len(run["events"]),
    )
    for event in run["events"]:
        store.add_event(event)
    store.finish_run(run["run_id"], run["result"])
    snapshot = store.model_snapshot(configured)
    snapshot["source"] = "synthetic_fixture"
    snapshot["observed"] = False
    return snapshot
