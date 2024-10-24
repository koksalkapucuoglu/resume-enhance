from django.urls import path
from . import views

app_name = 'resume'

urlpatterns = [
    path('form', views.ResumeFormView.as_view(), name="resume_form"),
    path('education_form', views.EducationView.as_view(), name="education"),
]