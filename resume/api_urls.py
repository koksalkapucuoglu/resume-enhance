"""
API URL Configuration for Resume app.

This module defines the REST API routes using DRF routers.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .api_views import ResumeViewSet, submit_feedback


# Create a router and register our viewsets
router = DefaultRouter()
router.register(r'resumes', ResumeViewSet, basename='resume')

# API URL patterns
urlpatterns = [
    path('feedback/', submit_feedback, name='feedback'),
    path('', include(router.urls)),
]
