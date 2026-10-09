from unittest.mock import AsyncMock, patch

import pytest
from django.urls import reverse

from observability.activity import reset_events
from observability.adapters import MCPUnavailableError, discovery_context
from observability.traces import trace_store


@pytest.fixture(autouse=True)
def clean_activity():
    reset_events()
    trace_store.reset()


def test_dashboard_renders_truthful_scope(client):
    response = client.get(reverse("observability:dashboard"))

    assert response.status_code == 200
    assert b"OBSERVED, NOT ASSUMED" in response.content
    assert b"this process" in response.content
    assert b"Configured expectations and observed identities" in response.content
    assert b'id="model-activity-detail"' in response.content
    assert b"dashboard.js?v=5" in response.content
    assert b"/static/observability/favicon-32.png" in response.content
    assert "https://cdn.jsdelivr.net" in response.headers["Content-Security-Policy"]


def test_status_does_not_expose_configured_paths(client, settings, tmp_path):
    settings.ORCHESTRATOR_ROOT = tmp_path / "not-a-real-orchestrator"

    response = client.get(reverse("observability:status-api"))
    payload = response.json()

    assert response.status_code == 200
    assert payload["mcp"]["configured"] is False
    assert str(tmp_path) not in response.content.decode()


@patch("observability.views.discover_tools", new_callable=AsyncMock)
def test_tools_returns_discovered_catalog(discover_tools, client):
    discover_tools.return_value = {
        "source": "live_mcp",
        "observed": True,
        "server": {"name": "orchestrator", "version": "1", "protocol_version": "test"},
        "tool_count": 1,
        "latency_ms": 4,
        "tools": [{"name": "plan_task", "description": "Plan", "input_schema": {}}],
    }

    response = client.get(reverse("observability:tools-api"))

    assert response.status_code == 200
    assert response.json()["tools"][0]["name"] == "plan_task"


@patch("observability.views.discover_tools", new_callable=AsyncMock)
def test_tools_returns_sanitized_unavailable_response(discover_tools, client):
    discover_tools.side_effect = MCPUnavailableError("The local MCP server timed out.")

    response = client.get(reverse("observability:tools-api"))

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "mcp_unavailable",
        "message": "The local MCP server timed out.",
    }


def test_discovery_context_does_not_claim_model_use():
    context = discovery_context("orchestrator", "3.4.4")

    assert context["model_calls"] == 0
    assert context["steps"][-1]["action"] == "Not called during discovery."
    assert all(role["model"] is None for role in context["model_roles"])


def test_activity_endpoint_is_bounded_metadata(client):
    response = client.get(reverse("observability:activity-api"))

    assert response.status_code == 200
    assert response.json()["scope"] == "Calls handled by this frontend only"


def test_observability_pages_render_navigation_and_accessible_states(client):
    models = client.get(reverse("observability:model-activity"))
    runs = client.get(reverse("observability:run-traces"))

    assert models.status_code == 200
    assert b"Provider &amp; model activity" in models.content
    assert b"Frontend expectation" in models.content
    assert b"model_activity.js?v=6" in models.content
    assert b"not-reported" in models.content
    assert b'aria-live="polite"' in models.content
    assert runs.status_code == 200
    assert b"Orchestration run observatory" in runs.content
    assert b'id="workflow-trail"' in runs.content
    assert b'id="handoff-map"' in runs.content
    assert b'id="task-model-flow"' in runs.content
    assert b'id="executor-state"' in runs.content
    assert b'id="run-model-roster"' in runs.content
    assert b'id="run-resources"' in runs.content
    assert b'id="trace-id"' in runs.content
    assert b"Token throughput" in runs.content
    assert "Time (Eastern · UTC)".encode() in runs.content
    assert b"run_traces.js?v=7" in runs.content
    assert b"No observed run is available" in runs.content


def test_empty_observability_apis_are_bounded_and_not_cached(client, settings):
    settings.OBSERVABILITY_EXECUTOR_LABEL = "Codex"
    models = client.get(reverse("observability:model-activity-api"))
    runs = client.get(reverse("observability:run-traces-api"))

    assert models.status_code == 200
    assert models.headers["Cache-Control"] == "no-store"
    assert models.json()["source"] == "live_process"
    assert [model["role"] for model in models.json()["models"]] == [
        "router",
        "reviewer",
        "judge",
    ]
    assert models.json()["executor"] == {
        "name": "Codex",
        "state": "configured",
        "scope": "external_client",
    }
    assert runs.status_code == 200
    assert runs.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in runs.headers
    assert runs.json()["runs"] == []
    assert runs.json()["analytics"] == {
        "role_performance": [],
        "task_model_outcomes": [],
    }
    assert runs.json()["retention"]["max_total_events"] == 512


def test_mock_mode_is_visibly_synthetic(client, settings):
    settings.OBSERVABILITY_MOCK_MODE = True
    settings.OBSERVABILITY_MODEL_CATALOG = {"reviewer": "configured-reviewer"}
    settings.OBSERVABILITY_EXECUTOR_LABEL = "Codex"

    models = client.get(reverse("observability:model-activity-api")).json()
    runs = client.get(reverse("observability:run-traces-api")).json()

    assert models["source"] == "synthetic_fixture"
    assert models["observed"] is False
    assert models["executor"] == {
        "name": "Codex",
        "state": "configured",
        "scope": "external_client",
    }
    assert runs["source"] == "synthetic_fixture"
    assert runs["observed"] is False
    assert runs["runs"][0]["result"]["model_roles"] == {}
    assert all(not model["observed_models"] for model in models["models"])
    assert [stage["id"] for stage in runs["runs"][0]["workflow"]] == [
        "route",
        "retrieve",
        "review",
        "judge",
        "revise",
        "complete",
    ]
    assert runs["runs"][0]["summary"]["provider_attempts"] == 1
    assert runs["runs"][0]["summary"]["retrieval"]["selected_count"] == 4
    assert [model["role"] for model in runs["runs"][0]["models"]] == [
        "router",
        "reviewer",
        "judge",
    ]


def test_status_publishes_versioned_endpoint_contract_without_paths(client, settings, tmp_path):
    settings.ORCHESTRATOR_ROOT = tmp_path / "private-location"

    response = client.get(reverse("observability:status-api"))
    payload = response.json()

    assert payload["observability"]["contract_version"] == 1
    assert payload["observability"]["endpoints"]["runs"].endswith("/runs/")
    assert str(tmp_path) not in response.content.decode()


def test_executor_label_is_configurable_without_claiming_observation(client, settings):
    settings.OBSERVABILITY_EXECUTOR_LABEL = "Claude"

    payload = client.get(reverse("observability:model-activity-api")).json()

    assert payload["executor"] == {
        "name": "Claude",
        "state": "configured",
        "scope": "external_client",
    }
