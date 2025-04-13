from django.urls import path
from . import views

app_name = 'resume'

urlpatterns = [
    path('', views.index, name="index"),  # New URL pattern for the index page
    path('form/', views.ResumeFormView.as_view(), name="resume_form"),
    path('enhance_project', views.enhance_project, name="enhance_project"),
    path('enhance_experience', views.enhance_experience, name="enhance_experience"),
    path('preview_resume_form', views.preview_resume_form, name='preview-resume-form'),
    path('upload-cv/', views.upload_cv, name="upload_cv"),
    path('test_form/', views.TestView.as_view(), name="test_form"),
    path('enhance_test_form', views.enhance_test_form, name="enhance_test_form"),
    path('preview_test_form', views.preview_test_form, name='preview-test-form'),
]