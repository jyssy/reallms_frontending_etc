from django.urls import path

from . import views

app_name = "observability"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("api/v1/status/", views.status_api, name="status-api"),
    path("api/v1/tools/", views.tools_api, name="tools-api"),
    path("api/v1/activity/", views.activity_api, name="activity-api"),
]
