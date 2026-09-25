import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from observability.activity import recent_events, reset_events
from observability.adapters import ObservedRunUnavailableError, observe_orchestrator
from observability.traces import trace_store


@pytest.fixture(autouse=True)
def clean_traces():
    trace_store.reset()
    reset_events()


@patch("observability.adapters.configuration_status", return_value={"configured": True})
def test_observed_adapter_accepts_progress_and_discards_result_content(configuration_status):
    del configuration_status
    run_id = "00000000-0000-4000-8000-000000000001"
    progress_event = {
        "contract_version": 1,
        "run_id": run_id,
        "sequence": 1,
        "timestamp": "2026-09-24T12:00:00+00:00",
        "event_type": "run.started",
        "component": "pipeline",
        "status": "started",
        "duration_ms": None,
        "metadata": {"judge_enabled": True, "prompt": "forbidden-marker"},
    }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, arguments, progress_callback):
            assert name == "ask_orchestrator_observed"
            assert arguments["prompt"] == "server-only prompt"
            await progress_callback(1.0, None, json.dumps(progress_event))
            return SimpleNamespace(
                is_error=False,
                structured_content={
                    "status": "success",
                    "task_type": "coding",
                    "model_roles": {"reviewer": "safe-model", "judge": None},
                    "final": "forbidden-marker",
                    "draft": "forbidden-marker",
                    "repo_root": "/forbidden-marker",
                },
            )

    with patch("observability.adapters.Client", FakeClient):
        result = asyncio.run(observe_orchestrator("server-only prompt"))

    serialized = json.dumps({"result": result, "snapshot": trace_store.snapshot()})
    assert result["run_id"] == run_id
    assert result["accepted_events"] == 1
    assert "forbidden-marker" not in serialized


@patch("observability.adapters.configuration_status", return_value={"configured": True})
def test_observed_adapter_timeout_uses_stable_sanitized_error(configuration_status, settings):
    del configuration_status
    settings.ORCHESTRATOR_OBSERVED_TIMEOUT_SECONDS = 0.001

    class SlowClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, *args, **kwargs):
            await asyncio.sleep(1)

    with patch("observability.adapters.Client", SlowClient):
        with pytest.raises(ObservedRunUnavailableError) as caught:
            asyncio.run(observe_orchestrator("server-only prompt"))

    assert caught.value.code == "timeout"
    assert recent_events()[0]["metadata"] == {"reason": "timeout"}


@patch("observability.adapters.configuration_status", return_value={"configured": True})
def test_observed_adapter_cancellation_leaves_a_bounded_partial_run(configuration_status):
    del configuration_status
    progress_event = {
        "contract_version": 1,
        "run_id": "00000000-0000-4000-8000-000000000001",
        "sequence": 1,
        "timestamp": "2026-09-24T12:00:00+00:00",
        "event_type": "run.started",
        "component": "pipeline",
        "status": "started",
        "duration_ms": None,
        "metadata": {"judge_enabled": True},
    }
    progress_received = asyncio.Event()

    class BlockingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, arguments, progress_callback):
            await progress_callback(1.0, None, json.dumps(progress_event))
            progress_received.set()
            await asyncio.Event().wait()

    async def cancel_observation():
        task = asyncio.create_task(observe_orchestrator("server-only prompt"))
        await progress_received.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with patch("observability.adapters.Client", BlockingClient):
        asyncio.run(cancel_observation())

    run = trace_store.snapshot()["runs"][0]
    assert run["lifecycle"] == "in_progress"
    assert run["event_count"] == 1
