from django.db import models
from django.contrib.auth import get_user_model


User = get_user_model()


class Resume(models.Model):
    """
    Resume model to store user's resume data.
    Content is stored as JSON to allow flexible schema (AI output).
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="resumes")
    title = models.CharField(max_length=255, default="My Resume")
    content = models.JSONField(default=dict) 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Optional: File field for the generated PDF if we want to store history later
    # pdf_file = models.FileField(upload_to="resumes/pdfs/", null=True, blank=True)

    @property
    def display_name(self):
        """
        Returns a user-friendly display name for the resume.
        Uses full_name from content if available, otherwise falls back to title.
        """
        full_name = self.content.get("user_info", {}).get("full_name", "").strip()
        
        if full_name:
            return f"{full_name}'s Resume"
        
        return self.title or "Untitled Resume"
    
    @property
    def owner_name(self):
        """Returns the full name from resume content."""
        return self.content.get("user_info", {}).get("full_name", "").strip()

    def __str__(self):
        return f"{self.user.username} - {self.title}"
