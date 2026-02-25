from django.contrib import admin
from .models import Resume, Feedback, UserProfile

# Register your models here.

@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ("user", "title", "created_at", "updated_at")
    list_filter = ("created_at", "updated_at")
    search_fields = ("user__username", "user__email", "title")
    date_hierarchy = "created_at"


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("user", "rating", "page", "created_at")
    list_filter = ("rating", "created_at")
    search_fields = ("user__username", "message")
    date_hierarchy = "created_at"


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "tier", "import_count", "enhance_count", "download_count", "quota_reset_date")
    list_editable = ("tier",)
    search_fields = ("user__username", "user__email")
    list_filter = ("tier", "quota_reset_date")
