from django.contrib import admin
from .models import Resume

# Register your models here.

@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ("user", "title", "created_at", "updated_at")
    list_filter = ("created_at", "updated_at")
    search_fields = ("user__username", "user__email", "title")
    date_hierarchy = "created_at"
