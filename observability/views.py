from datetime import UTC, datetime

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .activity import recent_events, record_event
from .adapters import MCPUnavailableError, configuration_status, discover_tools
from .contract import ENDPOINTS, TRACE_CONTRACT_VERSION
from .traces import mock_model_snapshot, mock_trace_snapshot, trace_store


def _no_store(payload: dict, *, status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def dashboard(request):
    record_event("dashboard.view", "completed")
    return render(request, "observability/dashboard.html")


@require_GET
def model_activity(request):
    record_event("models.view", "completed")
    return render(request, "observability/model_activity.html")


@require_GET
def run_traces(request):
    record_event("runs.view", "completed")
    return render(request, "observability/run_traces.html")


@require_GET
def status_api(request):
    return _no_store(
        {
            "application": "realms-frontend-local",
            "status": "ready",
            "timestamp": datetime.now(UTC).isoformat(),
            "mcp": configuration_status(),
            "observation_scope": "this_frontend_process",
            "observability": {
                "contract_version": TRACE_CONTRACT_VERSION,
                "source": "synthetic_fixture"
                if settings.OBSERVABILITY_MOCK_MODE
                else "live_process",
                "endpoints": ENDPOINTS,
            },
        }
    )


@require_GET
async def tools_api(request):
    try:
        catalog = await discover_tools()
    except MCPUnavailableError as error:
        return _no_store(
            {
                "source": "unavailable",
                "observed": False,
                "error": {"code": "mcp_unavailable", "message": str(error)},
                "tools": [],
            },
            status=503,
        )
    return _no_store(catalog)


@require_GET
def activity_api(request):
    return _no_store(
        {
            "source": "process_memory",
            "observed": True,
            "scope": "Calls handled by this frontend only",
            "events": recent_events(),
        }
    )


@require_GET
def model_activity_api(request):
    if settings.OBSERVABILITY_MOCK_MODE:
        payload = mock_model_snapshot(settings.OBSERVABILITY_MODEL_CATALOG)
    else:
        payload = trace_store.model_snapshot(settings.OBSERVABILITY_MODEL_CATALOG)
    return _no_store(payload)


@require_GET
def run_traces_api(request):
    payload = mock_trace_snapshot() if settings.OBSERVABILITY_MOCK_MODE else trace_store.snapshot()
    return _no_store(payload)
