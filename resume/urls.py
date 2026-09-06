from django.urls import path
from . import views

app_name = "resume"

urlpatterns = [
    path("", views.landing_page, name="index"),
    path("start/", views.selection_page, name="selection_page"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("form/", views.ResumeFormView.as_view(), name="resume_form"),
    path("form/<int:pk>/", views.ResumeFormView.as_view(), name="resume_form_edit"),
    path("duplicate/<int:pk>/", views.duplicate_resume, name="duplicate_resume"),
    path("delete/<int:pk>/", views.delete_resume, name="delete_resume"),
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
    path("agent/approve/", views.agent_approve, name="agent_approve"),
    path("agent/toggle-mode/", views.toggle_agent_mode, name="toggle_agent_mode"),
    path("agent/toggle-language/", views.toggle_ui_language, name="toggle_ui_language"),
]
