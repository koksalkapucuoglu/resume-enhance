"""
Resume API Serializers for Mobile/Frontend consumption.

These serializers convert Resume model instances to JSON format
for the REST API endpoints.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import serializers

from .models import Resume
from .services import resume_content


User = get_user_model()


class UserBriefSerializer(serializers.ModelSerializer):
    """Brief user info for nested serialization."""

    class Meta:
        model = User
        fields = ["id", "username", "email"]
        read_only_fields = fields


class ResumeListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for listing resumes.

    Carries enough to choose between resumes without fetching each one: which
    template it renders with, what language it is written in, and whether it is
    a base resume or a language version of another.
    """

    user = UserBriefSerializer(read_only=True)
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = Resume
        fields = [
            "id",
            "title",
            "display_name",
            "template_selector",
            "language",
            "derived_from",
            "derived_kind",
            "created_at",
            "updated_at",
            "user",
        ]
        read_only_fields = [
            "id",
            "display_name",
            "derived_from",
            "derived_kind",
            "created_at",
            "updated_at",
            "user",
        ]


def _validated_template(value):
    if value and value not in settings.TEMPLATE_SELECTOR_HTML_MAP:
        raise serializers.ValidationError(
            f"Unknown template. Available: "
            f"{', '.join(settings.TEMPLATE_SELECTOR_HTML_MAP)}"
        )
    return value


def _normalized_content(value):
    """
    Accept resume content in whatever shape the caller sent it.

    Content arrives from four places with slightly different conventions —
    skills as a string or a list, education dated or by year. Normalising on the
    way in means every reader, including the PDF renderer, sees one shape.
    """
    if not isinstance(value, dict):
        raise serializers.ValidationError("Content must be a JSON object.")
    return resume_content.normalize(value)


class ResumeDetailSerializer(serializers.ModelSerializer):
    """Full serializer for resume CRUD operations."""

    user = UserBriefSerializer(read_only=True)
    display_name = serializers.CharField(read_only=True)
    preview_url = serializers.SerializerMethodField()

    class Meta:
        model = Resume
        fields = [
            "id",
            "title",
            "display_name",
            "content",
            "template_selector",
            "language",
            "derived_from",
            "derived_kind",
            "preview_url",
            "created_at",
            "updated_at",
            "user",
        ]
        read_only_fields = [
            "id",
            "display_name",
            "derived_from",
            "derived_kind",
            "preview_url",
            "created_at",
            "updated_at",
            "user",
        ]

    def get_preview_url(self, obj):
        """Where to look at the result — the handoff back into the app."""
        path = reverse("resume:preview_saved_resume", args=[obj.pk])
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path

    def validate_content(self, value):
        return _normalized_content(value)

    def validate_template_selector(self, value):
        return _validated_template(value)


class ResumeCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating new resumes.
    User is set from request context, not from input.
    """

    preview_url = serializers.SerializerMethodField()

    class Meta:
        model = Resume
        fields = ["id", "title", "content", "template_selector", "language",
                  "preview_url"]
        read_only_fields = ["id", "preview_url"]

    def get_preview_url(self, obj):
        """Where to look at the result — the handoff back into the app."""
        path = reverse("resume:preview_saved_resume", args=[obj.pk])
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path

    def validate_content(self, value):
        return _normalized_content(value)

    def validate_template_selector(self, value):
        return _validated_template(value)

    # The owner is set by the view's perform_create, not here: doing it in both
    # places passes `user` twice and the create blows up.
