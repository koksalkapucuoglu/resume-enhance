from django.urls import path
from . import views

app_name = 'resume'

urlpatterns = [
    path('', views.landing_page, name="index"),
    path('start/', views.selection_page, name="selection_page"),
    path('dashboard/', views.DashboardView.as_view(), name="dashboard"),
    path('form/', views.ResumeFormView.as_view(), name="resume_form"),
    path('form/<int:pk>/', views.ResumeFormView.as_view(), name="resume_form_edit"),
    path('duplicate/<int:pk>/', views.duplicate_resume, name="duplicate_resume"),
    path('delete/<int:pk>/', views.delete_resume, name="delete_resume"),
    path('test-faangpath/', views.test_faangpath_template, name="test_faangpath"),
    path('enhance-project', views.enhance_project, name="enhance_project"),
    path('enhance-experience', views.enhance_experience, name="enhance_experience"),
    path('preview-resume-form', views.preview_resume_form, name='preview_resume_form'),
    path('upload-cv/', views.upload_cv, name="upload_cv"),
    path('upload-linkedin/', views.upload_linkedin_cv, name='upload_linkedin_cv'),
    path('resume/<int:pk>/download/', views.download_resume_pdf, name='download_resume_pdf'),
]