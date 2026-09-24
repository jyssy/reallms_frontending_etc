from unittest.mock import AsyncMock, patch

import pytest
from django.urls import reverse

from observability.activity import reset_events
from observability.adapters import MCPUnavailableError, discovery_context


@pytest.fixture(autouse=True)
def clean_activity():
    reset_events()


def test_dashboard_renders_truthful_scope(client):
    response = client.get(reverse("observability:dashboard"))

    assert response.status_code == 200
    assert b"OBSERVED, NOT ASSUMED" in response.content
    assert b"this process" in response.content
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
