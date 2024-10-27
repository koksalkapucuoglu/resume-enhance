from django.urls import path
from . import views

app_name = 'resume'

urlpatterns = [
    path('form', views.ResumeFormView.as_view(), name="resume_form"),
    path('enhance_education', views.enhance_education, name="enhance_education"),
    path('education_form', views.EducationView.as_view(), name="education"),
    path('enhance_experience', views.enhance_experience, name="enhance_experience"),
    path('enhance_project', views.enhance_project, name="enhance_project"),
]