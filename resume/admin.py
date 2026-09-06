from django.contrib import admin
from django.utils import timezone

from .models import (
    Feedback,
    JobPosting,
    Purchase,
    Resume,
    ResumeRevision,
    UserProfile,
)


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "title",
        "language",
        "template_selector",
        "derived_from",
        "derived_kind",
        "created_at",
        "updated_at",
    )
    list_filter = ("language", "derived_kind", "template_selector", "created_at")
    search_fields = ("user__username", "user__email", "title")
    raw_id_fields = ("derived_from",)
    date_hierarchy = "created_at"


@admin.register(ResumeRevision)
class ResumeRevisionAdmin(admin.ModelAdmin):
    list_display = ("resume", "source", "tool_name", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("resume__title", "resume__user__username", "summary")
    raw_id_fields = ("resume",)
    date_hierarchy = "created_at"


@admin.register(JobPosting)
class JobPostingAdmin(admin.ModelAdmin):
    list_display = (
        "title", "company", "user", "status", "match_score", "resume", "updated_at"
    )
    list_filter = ("status", "created_at")
    search_fields = ("title", "company", "user__username")
    raw_id_fields = ("user", "resume")
    date_hierarchy = "created_at"


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "plan",
        "days_granted",
        "amount_cents",
        "currency",
        "provider",
        "granted_until",
        "created_at",
    )
    list_filter = ("provider", "plan", "created_at")
    search_fields = ("user__username", "external_id")
    raw_id_fields = ("user",)
    date_hierarchy = "created_at"
    # A purchase is a record of something that happened; editing it would only
    # desynchronise it from the profile it granted.
    readonly_fields = tuple(f.name for f in Purchase._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("user", "rating", "page", "short_message", "created_at")
    list_filter = ("rating", "page", "created_at")
    search_fields = ("user__username", "message")
    date_hierarchy = "created_at"

    @admin.display(description="Message")
    def short_message(self, obj):
        return obj.message[:80]


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "tier",
        "pro_status",
        "premium_until",
        "ui_mode",
        "ui_language",
        "import_count",
        "enhance_count",
        "download_count",
        "agent_message_count",
        "quota_reset_date",
    )
    list_editable = ("tier", "premium_until")
    search_fields = ("user__username", "user__email")
    list_filter = ("tier", "ui_mode", "ui_language", "quota_reset_date")
    actions = ("grant_90_days", "grant_365_days", "revoke_premium", "reset_quotas")

    @admin.display(boolean=True, description="Pro now")
    def pro_status(self, obj):
        return obj.is_pro()

    @admin.action(description="Grant 90 days of Pro")
    def grant_90_days(self, request, queryset):
        for profile in queryset:
            profile.grant_premium(90)
        self.message_user(request, f"Granted 90 days to {queryset.count()} account(s).")

    @admin.action(description="Grant 365 days of Pro")
    def grant_365_days(self, request, queryset):
        for profile in queryset:
            profile.grant_premium(365)
        self.message_user(request, f"Granted 365 days to {queryset.count()} account(s).")

    @admin.action(description="Revoke purchased Pro (keeps the tier field)")
    def revoke_premium(self, request, queryset):
        updated = queryset.update(premium_until=timezone.now())
        self.message_user(request, f"Revoked purchased access for {updated} account(s).")

    @admin.action(description="Reset this month's quotas")
    def reset_quotas(self, request, queryset):
        updated = queryset.update(
            import_count=0, enhance_count=0, download_count=0, agent_message_count=0
        )
        self.message_user(request, f"Reset quotas for {updated} account(s).")
