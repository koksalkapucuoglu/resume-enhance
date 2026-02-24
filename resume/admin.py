from django.contrib import admin
from .models import Resume, Feedback

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
