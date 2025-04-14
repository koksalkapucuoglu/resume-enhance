from django.urls import path
from . import views

app_name = 'resume'

urlpatterns = [
    path('', views.index, name="index"),
    path('form/', views.ResumeFormView.as_view(), name="resume_form"),
    path('enhance_project', views.enhance_project, name="enhance_project"),
    path('enhance_experience', views.enhance_experience, name="enhance_experience"),
    path('preview_resume_form', views.preview_resume_form, name='preview-resume-form'),
    path('upload-cv/', views.upload_cv, name="upload_cv"),
    path('upload_linkedin/', views.upload_linkedin, name='upload_linkedin'),
]