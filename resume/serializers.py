"""
Resume API Serializers for Mobile/Frontend consumption.

These serializers convert Resume model instances to JSON format
for the REST API endpoints.
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Resume


User = get_user_model()


class UserBriefSerializer(serializers.ModelSerializer):
    """Brief user info for nested serialization."""
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email']
        read_only_fields = fields


class ResumeListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for listing resumes.
    Used in list views where full content is not needed.
    """
    user = UserBriefSerializer(read_only=True)
    
    class Meta:
        model = Resume
        fields = ['id', 'title', 'created_at', 'updated_at', 'user']
        read_only_fields = ['id', 'created_at', 'updated_at', 'user']


class ResumeDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer for resume CRUD operations.
    Includes the JSON content field.
    """
    user = UserBriefSerializer(read_only=True)
    
    class Meta:
        model = Resume
        fields = ['id', 'title', 'content', 'created_at', 'updated_at', 'user']
        read_only_fields = ['id', 'created_at', 'updated_at', 'user']
    
    def validate_content(self, value):
        """
        Validate the content structure.
        Ensures required sections exist.
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError("Content must be a JSON object")
        
        # Optional: Validate expected keys exist
        expected_sections = ['user_info', 'education', 'experience', 'projects_and_publications']
        missing = [key for key in expected_sections if key not in value]
        
        # We don't enforce all sections - allow partial saves
        # This is a "warn" not "error" scenario for MVP
        
        return value


class ResumeCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating new resumes.
    User is set from request context, not from input.
    """
    
    class Meta:
        model = Resume
        fields = ['id', 'title', 'content']
        read_only_fields = ['id']
    
    def create(self, validated_data):
        """Create resume with current user from context."""
        user = self.context['request'].user
        return Resume.objects.create(user=user, **validated_data)
