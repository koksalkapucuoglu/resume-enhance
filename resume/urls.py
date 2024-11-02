from django.urls import path
from . import views

app_name = 'resume'

urlpatterns = [
    path('form', views.ResumeFormView.as_view(), name="resume_form"),
    path('enhance_test_form', views.enhance_test_form, name="enhance_test_form"),
    path('test_form', views.TestView.as_view(), name="test_form"),
    path('enhance_experience', views.enhance_experience, name="enhance_experience"),
    path('enhance_project', views.enhance_project, name="enhance_project"),
    path('preview_test_form/', views.preview_test_form, name='preview-test-form'),
    path('preview_resume_form/', views.preview_resume_form, name='preview-resume-form'),
]