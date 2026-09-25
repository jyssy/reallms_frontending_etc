from django.urls import path

from . import views

app_name = "observability"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("models/", views.model_activity, name="model-activity"),
    path("runs/", views.run_traces, name="run-traces"),
    path("api/v1/status/", views.status_api, name="status-api"),
    path("api/v1/tools/", views.tools_api, name="tools-api"),
    path("api/v1/activity/", views.activity_api, name="activity-api"),
    path(
        "api/v1/observability/models/",
        views.model_activity_api,
        name="model-activity-api",
    ),
    path(
        "api/v1/observability/runs/",
        views.run_traces_api,
        name="run-traces-api",
    ),
]
