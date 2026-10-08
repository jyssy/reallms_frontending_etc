from observability.contract import parse_trace_event
from observability.traces import TraceStore


def event(sequence, *, run_id="00000000-0000-4000-8000-000000000001", **overrides):
    value = {
        "contract_version": 1,
        "run_id": run_id,
        "sequence": sequence,
        "timestamp": f"2026-09-24T12:00:{sequence:02d}+00:00",
        "event_type": "provider.attempt",
        "component": "provider",
        "status": "success",
        "duration_ms": sequence,
        "metadata": {"provider": "remote", "attempt": sequence},
    }
    value.update(overrides)
    return parse_trace_event(value)


def test_store_orders_out_of_order_events_and_suppresses_duplicates():
    store = TraceStore(max_runs=2, max_events_per_run=4, max_total_events=6)

    assert store.add_event(event(2)) is True
    assert store.add_event(event(1)) is True
    assert store.add_event(event(2)) is False

    run = store.snapshot()["runs"][0]
    assert [item["sequence"] for item in run["events"]] == [1, 2]
    assert run["event_count"] == 2


def test_store_enforces_per_run_and_global_retention():
    store = TraceStore(max_runs=2, max_events_per_run=2, max_total_events=3)
    other = "00000000-0000-4000-8000-000000000002"

    for sequence in (1, 2, 3):
        store.add_event(event(sequence))
    store.add_event(event(1, run_id=other))
    store.add_event(event(2, run_id=other))

    snapshot = store.snapshot()
    assert snapshot["retention"]["stored_events"] == 3
    assert snapshot["retention"]["dropped_events"] == 2
    assert all(run["event_count"] <= 2 for run in snapshot["runs"])


def test_model_projection_distinguishes_all_contract_states():
    store = TraceStore()
    run_id = "00000000-0000-4000-8000-000000000001"
    store.add_event(
        event(
            1,
            event_type="specialist.completed",
            component="specialist",
            status="degraded",
            metadata={"task_type": "coding", "fallback": True},
        )
    )
    store.add_event(
        event(
            2,
            event_type="judge_critique.completed",
            component="judge",
            status="skipped",
            metadata={"judge_enabled": False},
        )
    )
    store.add_event(
        event(
            3,
            event_type="embedding.completed",
            component="embedding",
            metadata={"batch_size": 2},
        )
    )
    store.finish_run(
        run_id,
        {
            "status": "degraded_success",
            "task_type": "coding",
            "model_roles": {"reviewer": "fallback-model", "judge": None},
        },
    )

    snapshot = store.model_snapshot(
        {"reviewer": "configured-model", "judge": "judge-model", "router": "router-model"}
    )
    roles = {model["role"]: model for model in snapshot["models"]}

    assert roles["reviewer"]["states"] == ["configured", "observed", "fallback"]
    assert roles["judge"]["states"] == ["configured", "skipped", "not-reported"]
    assert roles["embedding"]["states"] == ["not-reported"]
    assert roles["router"]["states"] == ["configured"]


def test_run_workflow_projects_truthful_stage_model_attribution():
    store = TraceStore()
    run_id = "00000000-0000-4000-8000-000000000001"
    store.add_event(
        event(
            1,
            event_type="router.completed",
            component="router",
            metadata={"task_type": "coding"},
        )
    )
    store.add_event(
        event(
            2,
            event_type="specialist.completed",
            component="specialist",
            status="degraded",
            metadata={"task_type": "coding", "fallback": True},
        )
    )
    store.add_event(
        event(
            3,
            event_type="judge_critique.completed",
            component="judge",
            status="skipped",
            metadata={"judge_enabled": False},
        )
    )
    store.add_event(
        event(
            4,
            event_type="run.completed",
            component="pipeline",
            metadata={"result_status": "degraded_success"},
        )
    )
    store.finish_run(
        run_id,
        {
            "status": "degraded_success",
            "task_type": "coding",
            "model_roles": {"reviewer": "observed-reviewer", "judge": None},
        },
    )

    run = store.snapshot(
        {"router": "configured-router", "reviewer": "configured-reviewer"}
    )["runs"][0]
    stages = {stage["id"]: stage for stage in run["workflow"]}

    assert [stage["id"] for stage in run["workflow"]] == [
        "route",
        "retrieve",
        "review",
        "judge",
        "revise",
        "complete",
    ]
    assert stages["route"]["models"] == [
        {
            "role": "router",
            "name": "configured-router",
            "state": "configured",
        }
    ]
    assert stages["review"]["models"] == [
        {"role": "reviewer", "name": "observed-reviewer", "state": "fallback"}
    ]
    assert stages["judge"]["models"] == [
        {"role": "judge", "name": None, "state": "skipped"}
    ]
    assert stages["retrieve"]["status"] == "not_run"
    assert stages["complete"]["status"] == "success"


def test_run_summary_projects_reported_timing_resources_and_model_roster():
    store = TraceStore()
    run_id = "00000000-0000-4000-8000-000000000001"
    store.add_event(
        event(
            1,
            event_type="run.started",
            component="pipeline",
            status="started",
            duration_ms=None,
            metadata={"judge_enabled": True},
        )
    )
    store.add_event(
        event(
            2,
            event_type="retrieval.completed",
            component="retrieval",
            duration_ms=80,
            metadata={
                "retrieval_used": True,
                "context_used": True,
                "candidate_count": 12,
                "selected_count": 4,
            },
        )
    )
    store.add_event(
        event(
            3,
            event_type="embedding.completed",
            component="embedding",
            duration_ms=20,
            metadata={"batch_size": 4},
        )
    )
    store.add_event(
        event(
            4,
            event_type="reranking.completed",
            component="reranker",
            duration_ms=10,
            metadata={"candidate_count": 12, "selected_count": 4},
        )
    )
    store.add_event(
        event(
            5,
            event_type="provider.attempt",
            component="provider",
            duration_ms=200,
            metadata={"provider": "remote", "operation": "completion", "attempt": 1},
        )
    )
    store.add_event(
        event(
            6,
            event_type="specialist.completed",
            component="specialist",
            duration_ms=250,
            metadata={"task_type": "coding", "fallback": True},
        )
    )
    store.add_event(
        event(
            7,
            event_type="run.completed",
            component="pipeline",
            duration_ms=600,
            metadata={"result_status": "degraded_success"},
        )
    )
    store.finish_run(
        run_id,
        {
            "status": "degraded_success",
            "task_type": "coding",
            "model_roles": {"reviewer": "observed-reviewer"},
        },
    )

    run = store.snapshot({"router": "configured-router"})["runs"][0]
    models = {model["role"]: model for model in run["models"]}

    assert run["summary"] == {
        "started_at": "2026-09-24T12:00:01+00:00",
        "completed_at": "2026-09-24T12:00:07+00:00",
        "total_duration_ms": 600,
        "reported_stage_duration_ms": 330,
        "reported_stage_count": 2,
        "provider_attempts": 1,
        "provider_retries": 0,
        "providers": ["remote"],
        "fallback": True,
        "retrieval": {
            "retrieval_used": True,
            "context_used": True,
            "candidate_count": 12,
            "selected_count": 4,
        },
        "resources": {
            "embedding_batch_size": 4,
            "rerank_candidate_count": 12,
            "rerank_selected_count": 4,
        },
    }
    assert models["reviewer"] == {
        "role": "reviewer",
        "name": "observed-reviewer",
        "state": "fallback",
        "event_count": 1,
    }
    assert models["router"] == {
        "role": "router",
        "name": "configured-router",
        "state": "configured",
        "event_count": 0,
    }
    assert models["judge"]["state"] == "not-reported"
