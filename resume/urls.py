from django.urls import path
from . import views

app_name = "resume"

urlpatterns = [
    path("", views.landing_page, name="index"),
    path("start/", views.selection_page, name="selection_page"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    # Short, unguessable path: this is followed by a browser with no session.
    path("d/<str:token>/", views.signed_download, name="signed_download"),
    path("pricing/", views.pricing_page, name="pricing"),
    path("privacy/", views.privacy_policy, name="privacy"),
    path("gizlilik/", views.privacy_policy_tr, name="privacy_tr"),
    # Domain proof for publishing as com.resustackapp/* in the MCP Registry.
    path(
        ".well-known/mcp-registry-auth",
        views.mcp_registry_auth,
        name="mcp_registry_auth",
    ),
    path("checkout/<str:plan_code>/", views.start_checkout, name="start_checkout"),
    path(
        "pricing/notify/",
        views.register_premium_interest,
        name="register_premium_interest",
    ),
    path("webhooks/payments/", views.payment_webhook, name="payment_webhook"),
    path("form/", views.ResumeFormView.as_view(), name="resume_form"),
    path("form/<int:pk>/", views.ResumeFormView.as_view(), name="resume_form_edit"),
    path("duplicate/<int:pk>/", views.duplicate_resume, name="duplicate_resume"),
    path("delete/<int:pk>/", views.delete_resume, name="delete_resume"),
    path(
        "resume/<int:pk>/import-review/dismiss/",
        views.dismiss_import_review,
        name="dismiss_import_review",
    ),
    path("test-faangpath/", views.test_faangpath_template, name="test_faangpath"),
    path("enhance-project", views.enhance_project, name="enhance_project"),
    path("enhance-experience", views.enhance_experience, name="enhance_experience"),
    path("preview-resume-form", views.preview_resume_form, name="preview_resume_form"),
    path("upload-cv/", views.upload_cv, name="upload_cv"),
    path("upload-linkedin/", views.upload_linkedin_cv, name="upload_linkedin_cv"),
    path(
        "resume/<int:pk>/download/",
        views.download_resume_pdf,
        name="download_resume_pdf",
    ),
    path(
        "resume/<int:pk>/appearance/",
        views.set_resume_appearance,
        name="set_resume_appearance",
    ),
    path(
        "resume/<int:pk>/preview/",
        views.preview_saved_resume,
        name="preview_saved_resume",
    ),
    path(
        "resume/<int:pk>/revisions/",
        views.resume_revisions,
        name="resume_revisions",
    ),
    path(
        "resume/<int:pk>/revisions/latest/diff/",
        views.resume_latest_diff,
        name="resume_latest_diff",
    ),
    path(
        "resume/<int:pk>/revisions/<int:revision_id>/diff/",
        views.resume_revision_diff,
        name="resume_revision_diff",
    ),
    path(
        "resume/<int:pk>/revert/<int:revision_id>/",
        views.revert_resume,
        name="revert_resume",
    ),
    path("agent/chat/", views.agent_chat, name="agent_chat"),
    path("agent/chat/stream/", views.agent_chat_stream, name="agent_chat_stream"),
    path(
        "agent/approve/stream/",
        views.agent_approve_stream,
        name="agent_approve_stream",
    ),
    path("agent/approve/", views.agent_approve, name="agent_approve"),
    path(
        "agent/builder/start/",
        views.agent_builder_start,
        name="agent_builder_start",
    ),
    path("agent/toggle-mode/", views.toggle_agent_mode, name="toggle_agent_mode"),
    path("agent/toggle-language/", views.toggle_ui_language, name="toggle_ui_language"),
    path(
        "agent/toggle-confirm/",
        views.toggle_confirm_destructive,
        name="toggle_confirm_destructive",
    ),
]
