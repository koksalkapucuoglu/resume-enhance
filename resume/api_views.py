"""
Resume API Views using Django REST Framework.

These ViewSets provide CRUD operations for Resume model
via REST API endpoints for mobile and frontend apps.
"""
import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Resume, Feedback
from .serializers import (
    ResumeListSerializer,
    ResumeDetailSerializer,
    ResumeCreateSerializer,
)


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of a resume to edit it.
    """
    
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed only for authenticated users who own the object
        # For MVP, we don't allow public read access
        return obj.user == request.user


class ResumeViewSet(viewsets.ModelViewSet):
    """
    API endpoint for Resume CRUD operations.
    
    list: GET /api/v1/resumes/ - List all resumes for current user
    create: POST /api/v1/resumes/ - Create a new resume
    retrieve: GET /api/v1/resumes/{id}/ - Get resume detail
    update: PUT /api/v1/resumes/{id}/ - Full update
    partial_update: PATCH /api/v1/resumes/{id}/ - Partial update
    destroy: DELETE /api/v1/resumes/{id}/ - Delete resume
    """
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]
    
    def get_queryset(self):
        """Return only resumes belonging to the current user."""
        return Resume.objects.filter(user=self.request.user).order_by('-updated_at')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == 'list':
            return ResumeListSerializer
        elif self.action == 'create':
            return ResumeCreateSerializer
        return ResumeDetailSerializer
    
    def perform_create(self, serializer):
        """Set the user to current user when creating."""
        serializer.save(user=self.request.user)
    
    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        """
        Duplicate an existing resume.
        POST /api/v1/resumes/{id}/duplicate/
        """
        resume = self.get_object()
        new_resume = Resume.objects.create(
            user=request.user,
            title=f"{resume.title} (Copy)",
            content=resume.content.copy()
        )
        serializer = ResumeDetailSerializer(new_resume)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@require_http_methods(["POST"])
def submit_feedback(request):
    """Submit user feedback. Auth optional."""
    try:
        data = json.loads(request.body)
        message = data.get("message", "").strip()
        if not message:
            return JsonResponse({"error": "Message is required"}, status=400)
        rating = data.get("rating")
        page = data.get("page", "")
        Feedback.objects.create(
            user=request.user if request.user.is_authenticated else None,
            message=message[:2000],
            rating=rating if isinstance(rating, int) and rating in range(1, 6) else None,
            page=page[:100],
        )
        return JsonResponse({"success": True})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
