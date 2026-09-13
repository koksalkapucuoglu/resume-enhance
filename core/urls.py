"""core URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from mcp_server.views import mcp_endpoint
from core.views import (
    SignupView,
    ProfileView,
    delete_account,
    issue_api_token,
    revoke_api_token,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("resume.urls")),
    path("api/v1/", include("resume.api_urls")),  # Mobile API endpoints
    # The MCP endpoint. One URL, POST only — clients are given exactly this.
    path("mcp", include("mcp_server.urls")),
    # The trailing-slash spelling answers too. APPEND_SLASH only adds slashes,
    # it never strips them, so without this /mcp/ would be a bare 404.
    path("mcp/", mcp_endpoint),
    path("accounts/", include("django.contrib.auth.urls")),  # Login, Logout, etc.
    path("accounts/signup/", SignupView.as_view(), name="signup"),  # Signup page
    path("accounts/profile/", ProfileView.as_view(), name="profile"),  # Profile page
    path("accounts/api-token/", issue_api_token, name="issue_api_token"),
    path("accounts/api-token/revoke/", revoke_api_token, name="revoke_api_token"),
    path("accounts/delete/", delete_account, name="delete_account"),
]
