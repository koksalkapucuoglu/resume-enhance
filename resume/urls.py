from django.urls import path
from . import views

app_name = 'resume'

urlpatterns = [
    path('form', views.user_info, name='user_info'),
    path('education_form', views.EducationView.as_view(), name="education"),
]