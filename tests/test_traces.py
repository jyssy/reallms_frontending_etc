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
