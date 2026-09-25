"""Versioned, metadata-only contract for orchestrator observations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

TRACE_CONTRACT_VERSION = 1
MAX_PROGRESS_MESSAGE_BYTES = 16_384

EVENT_TYPES = frozenset(
    {
        "run.started",
        "router.completed",
        "retrieval.completed",
        "embedding.completed",
        "reranking.completed",
        "specialist.completed",
        "judge_critique.completed",
        "revision.completed",
        "revision.skipped",
        "provider.attempt",
        "provider.retry",
        "run.completed",
    }
)
COMPONENTS = frozenset(
    {
        "pipeline",
        "router",
        "retrieval",
        "embedding",
        "reranker",
        "specialist",
        "judge",
        "revision",
        "provider",
    }
)
STATUSES = frozenset({"started", "success", "degraded", "failed", "skipped", "retrying"})
RESULT_STATUSES = frozenset(
    {
        "success",
        "degraded_success",
        "unavailable_dependency",
        "invalid_configuration",
        "invalid_input",
        "security_block",
        "internal_failure",
    }
)
TASK_TYPES = frozenset({"coding", "general", "ops", "search"})

_METADATA_ENUMS = {
    "operation": frozenset({"completion", "embedding", "reranking", "routing"}),
    "provider": frozenset({"local", "remote"}),
    "result_status": RESULT_STATUSES,
    "task_type": TASK_TYPES,
}
_METADATA_BOOLEANS = frozenset(
    {
        "context_used",
        "fallback",
        "judge_enabled",
        "remote",
        "retrieval_used",
        "revision_required",
    }
)
_METADATA_INTEGERS = frozenset(
    {
        "attempt",
        "attempts",
        "batch_size",
        "candidate_count",
        "max_attempts",
        "selected_count",
    }
)
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}").fullmatch
_SAFE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}").fullmatch

MODEL_ROLES = ("router", "embedding", "reranker", "reviewer", "judge")
MODEL_STATES = frozenset({"configured", "observed", "skipped", "fallback", "not-reported"})

ENDPOINTS = {
    "status": "/api/v1/status/",
    "tools": "/api/v1/tools/",
    "activity": "/api/v1/activity/",
    "models": "/api/v1/observability/models/",
    "runs": "/api/v1/observability/runs/",
}


class ContractError(ValueError):
    """Raised when untrusted observation data does not match the public contract."""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _safe_timestamp(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ContractError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError("invalid_timestamp") from error
    if parsed.tzinfo is None:
        raise ContractError("invalid_timestamp")
    return value


def safe_model_name(value: object) -> str | None:
    """Return a conservative model identifier, never arbitrary provider text."""
    if isinstance(value, str) and _SAFE_MODEL(value):
        return value
    return None


def _safe_metadata(value: object) -> dict[str, str | int | bool]:
    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, str | int | bool] = {}
    for key, item in value.items():
        if key in _METADATA_BOOLEANS and isinstance(item, bool):
            safe[key] = item
        elif key in _METADATA_INTEGERS and _is_int(item) and 0 <= item <= 1_000_000:
            safe[key] = item
        elif key in _METADATA_ENUMS and isinstance(item, str) and item in _METADATA_ENUMS[key]:
            safe[key] = item
        elif key == "code" and isinstance(item, str) and _SAFE_CODE(item):
            safe[key] = item
    return safe


def parse_trace_event(value: object) -> dict[str, Any]:
    """Validate an upstream event and rebuild it from an explicit allowlist."""
    if not isinstance(value, Mapping):
        raise ContractError("invalid_event")
    if value.get("contract_version") != TRACE_CONTRACT_VERSION:
        raise ContractError("unsupported_contract_version")

    run_id = value.get("run_id")
    try:
        canonical_run_id = str(UUID(str(run_id)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ContractError("invalid_run_id") from error
    if run_id != canonical_run_id:
        raise ContractError("invalid_run_id")

    sequence = value.get("sequence")
    if not _is_int(sequence) or sequence < 1 or sequence > 1_000_000:
        raise ContractError("invalid_sequence")

    event_type = value.get("event_type")
    component = value.get("component")
    status = value.get("status")
    if event_type not in EVENT_TYPES or component not in COMPONENTS or status not in STATUSES:
        raise ContractError("invalid_event_vocabulary")

    duration = value.get("duration_ms")
    if duration is not None and (not _is_int(duration) or duration < 0 or duration > 86_400_000):
        raise ContractError("invalid_duration")

    return {
        "contract_version": TRACE_CONTRACT_VERSION,
        "run_id": canonical_run_id,
        "sequence": sequence,
        "timestamp": _safe_timestamp(value.get("timestamp")),
        "event_type": event_type,
        "component": component,
        "status": status,
        "duration_ms": duration,
        "metadata": _safe_metadata(value.get("metadata")),
    }


def parse_observed_result(value: object) -> dict[str, Any]:
    """Extract only status and attribution fields from a structured tool result."""
    if not isinstance(value, Mapping):
        return {"status": "internal_failure", "task_type": None, "model_roles": {}}
    status = value.get("status")
    task_type = value.get("task_type")
    raw_roles = value.get("model_roles")
    roles: dict[str, str] = {}
    if isinstance(raw_roles, Mapping):
        for role in ("reviewer", "judge"):
            model = safe_model_name(raw_roles.get(role))
            if model:
                roles[role] = model
    return {
        "status": status if status in RESULT_STATUSES else "internal_failure",
        "task_type": task_type if task_type in TASK_TYPES else None,
        "model_roles": roles,
    }


def configured_model_catalog(value: object) -> dict[str, str]:
    """Validate the frontend-owned, browser-safe model catalog."""
    if not isinstance(value, Mapping):
        return {}
    catalog: dict[str, str] = {}
    for role in MODEL_ROLES:
        model = safe_model_name(value.get(role))
        if model:
            catalog[role] = model
    return catalog
