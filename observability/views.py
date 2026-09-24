from datetime import UTC, datetime

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .activity import recent_events, record_event
from .adapters import MCPUnavailableError, configuration_status, discover_tools


@require_GET
def dashboard(request):
    record_event("dashboard.view", "completed")
    return render(request, "observability/dashboard.html")


@require_GET
def status_api(request):
    return JsonResponse(
        {
            "application": "realms-frontend-local",
            "status": "ready",
            "timestamp": datetime.now(UTC).isoformat(),
            "mcp": configuration_status(),
            "observation_scope": "this_frontend_process",
        }
    )


@require_GET
async def tools_api(request):
    try:
        catalog = await discover_tools()
    except MCPUnavailableError as error:
        return JsonResponse(
            {
                "source": "unavailable",
                "observed": False,
                "error": {"code": "mcp_unavailable", "message": str(error)},
                "tools": [],
            },
            status=503,
        )
    return JsonResponse(catalog)


@require_GET
def activity_api(request):
    return JsonResponse(
        {
            "source": "process_memory",
            "observed": True,
            "scope": "Calls handled by this frontend only",
            "events": recent_events(),
        }
    )
