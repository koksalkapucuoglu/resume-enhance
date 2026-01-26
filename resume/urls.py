from django.urls import path
from . import views

app_name = 'resume'

urlpatterns = [
    path('', views.index, name="index"),
    path('form/', views.ResumeFormView.as_view(), name="resume_form"),
    path('test-faangpath/', views.test_faangpath_template, name="test_faangpath"),
    path('enhance-project', views.enhance_project, name="enhance_project"),
    path('enhance-experience', views.enhance_experience, name="enhance_experience"),
    path('preview-resume-form', views.preview_resume_form, name='preview_resume_form'),
    path('upload-cv/', views.upload_cv, name="upload_cv"),
    path('upload-linkedin/', views.upload_linkedin_cv, name='upload_linkedin_cv'),
]