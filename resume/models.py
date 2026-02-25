from django.db import models
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from datetime import date


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


class Feedback(models.Model):
    RATING_CHOICES = [(i, i) for i in range(1, 6)]
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="feedback")
    message = models.TextField()
    rating = models.IntegerField(choices=RATING_CHOICES, null=True, blank=True)
    page = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Feedback from {self.user or 'anonymous'} at {self.created_at:%Y-%m-%d}"


class UserProfile(models.Model):
    """
    Stores user subscription tier and monthly quota counts.
    Automatically created via post_save signal when a new user is registered.
    """
    TIER_FREE = 'free'
    TIER_PRO = 'pro'
    TIER_CHOICES = [(TIER_FREE, 'Free'), (TIER_PRO, 'Pro')]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    tier = models.CharField(max_length=10, choices=TIER_CHOICES, default=TIER_FREE)

    # Monthly quota counters
    import_count = models.IntegerField(default=0)
    enhance_count = models.IntegerField(default=0)
    download_count = models.IntegerField(default=0)
    quota_reset_date = models.DateField(auto_now_add=True)

    def reset_if_new_month(self):
        """Reset monthly quotas if a new month has started."""
        from django.conf import settings
        today = date.today()
        if today.month != self.quota_reset_date.month or today.year != self.quota_reset_date.year:
            self.import_count = 0
            self.enhance_count = 0
            self.download_count = 0
            self.quota_reset_date = today
            self.save()

    def is_pro(self):
        """Check if user is on Pro tier."""
        return self.tier == self.TIER_PRO

    def can_import(self):
        """Check if user can import a PDF."""
        if self.is_pro():
            return True
        self.reset_if_new_month()
        from django.conf import settings
        return self.import_count < settings.FREE_TIER_LIMITS['import_count']

    def can_enhance(self):
        """Check if user can use AI enhancement."""
        if self.is_pro():
            return True
        self.reset_if_new_month()
        from django.conf import settings
        return self.enhance_count < settings.FREE_TIER_LIMITS['enhance_count']

    def can_download(self):
        """Check if user can download a PDF."""
        if self.is_pro():
            return True
        self.reset_if_new_month()
        from django.conf import settings
        return self.download_count < settings.FREE_TIER_LIMITS['download_count']

    def can_create_resume(self):
        """Check if user can create a new resume."""
        if self.is_pro():
            return True
        from django.conf import settings
        count = self.user.resumes.count()
        return count < settings.FREE_TIER_LIMITS['resume_count']

    def __str__(self):
        return f"{self.user.username} - {self.tier.upper()}"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Automatically create UserProfile when a new User is created."""
    if created:
        UserProfile.objects.create(user=instance)
