from django.urls import path

from .views import mcp_endpoint

app_name = "mcp_server"

urlpatterns = [
    path("", mcp_endpoint, name="endpoint"),
]
