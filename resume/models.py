from django.db import models
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from datetime import date, timedelta

from django.utils import timezone


User = get_user_model()


class Resume(models.Model):
    """
    Resume model to store user's resume data.
    Content is stored as JSON to allow flexible schema (AI output).
    """

    LANGUAGE_CHOICES = [("en", "English"), ("tr", "Türkçe")]

    # Full language names as the AI extractor emits them, mapped to our codes.
    _LANGUAGE_ALIASES = {
        "english": "en", "ingilizce": "en", "en": "en",
        "turkish": "tr", "türkçe": "tr", "turkce": "tr", "tr": "tr",
    }

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="resumes")
    title = models.CharField(max_length=255, default="My Resume")
    content = models.JSONField(default=dict)
    template_selector = models.CharField(max_length=50, default="faangpath-simple")
    # The language the resume is WRITTEN in — unrelated to the interface language.
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default="en")
    # Set when this resume is a translated variant of another. Variants are the
    # same document in another language, so they do not consume a resume slot.
    translation_of = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="translations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Optional: File field for the generated PDF if we want to store history later
    # pdf_file = models.FileField(upload_to="resumes/pdfs/", null=True, blank=True)

    _GENERIC_TITLES = {"My Resume", "New Resume", "Untitled Resume", ""}

    @property
    def display_name(self):
        """
        Returns a user-friendly display name for the resume.

        Priority:
        1. Custom title (anything the user set that's not a generic default)
        2. Full name + creation month/year (disambiguates when title is generic)
        3. Title as-is (fallback)
        """
        title_base = (self.title or "").split(" 20")[0].strip()  # strip datetime suffix
        if self.title and title_base not in self._GENERIC_TITLES:
            return self.title

        full_name = self.content.get("user_info", {}).get("full_name", "").strip()
        if full_name:
            return f"{full_name} · {self.created_at.strftime('%b %Y')}"

        return self.title or "Untitled Resume"

    @classmethod
    def normalize_language(cls, value, default="en"):
        """Map whatever the AI or the user called a language onto a code."""
        return cls._LANGUAGE_ALIASES.get(str(value or "").strip().lower(), default)

    def sync_language_from_content(self):
        """Adopt the language the extractor detected, if we recognise it."""
        detected = (self.content or {}).get("language")
        if detected:
            self.language = self.normalize_language(detected, self.language)
        return self.language

    @property
    def language_label(self):
        return dict(self.LANGUAGE_CHOICES).get(self.language, self.language)

    @property
    def root(self):
        """The original resume in a translation family — itself if it is one."""
        return self.translation_of or self

    def language_family(self):
        """This resume and every translation sharing its root, oldest first."""
        root = self.root
        return Resume.objects.filter(
            models.Q(pk=root.pk) | models.Q(translation_of=root)
        ).order_by("created_at")

    @property
    def owner_name(self):
        """Returns the full name from resume content."""
        return self.content.get("user_info", {}).get("full_name", "").strip()

    def __str__(self):
        return f"{self.user.username} - {self.title}"


class JobPosting(models.Model):
    """
    A job the user is tracking, and the resume they are using for it.

    Tags are what group resumes by the kind of work they suit — "python" roles
    go out with one CV, "c++" roles with another — so the assistant can answer
    "which resume do I use for C++ jobs?" from real applications rather than a
    label the user had to maintain by hand.
    """

    STATUS_SAVED = "saved"
    STATUS_APPLIED = "applied"
    STATUS_INTERVIEW = "interview"
    STATUS_OFFER = "offer"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_SAVED, "Saved"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_INTERVIEW, "Interview"),
        (STATUS_OFFER, "Offer"),
        (STATUS_REJECTED, "Rejected"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="job_postings"
    )
    title = models.CharField(max_length=255)
    company = models.CharField(max_length=255, blank=True, default="")
    url = models.URLField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    # The resume used for this application. Kept if the resume is deleted so the
    # application history does not disappear with it.
    resume = models.ForeignKey(
        Resume,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_postings",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_SAVED
    )
    match_score = models.IntegerField(null=True, blank=True)
    missing_keywords = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["user", "-updated_at"])]

    @staticmethod
    def normalize_tags(tags):
        """Lowercase, trimmed, de-duplicated, order preserved."""
        seen, cleaned = set(), []
        for tag in tags or []:
            if tag is None:
                continue
            value = str(tag).strip().lower()
            if value and value not in seen:
                seen.add(value)
                cleaned.append(value)
        return cleaned

    @property
    def label(self):
        return f"{self.title} · {self.company}" if self.company else self.title

    def __str__(self):
        return f"{self.label} ({self.status})"


class ResumeRevision(models.Model):
    """
    A restore point for a Resume: the state it was in *before* a change.

    Written immediately before any mutation of Resume.content or
    Resume.template_selector, so restoring a revision undoes exactly one step.
    Free accounts keep a bounded number of these (see revision_service.prune).
    """

    SOURCE_MANUAL = "manual"
    SOURCE_AGENT = "agent"
    SOURCE_IMPORT = "import"
    SOURCE_REVERT = "revert"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual edit"),
        (SOURCE_AGENT, "Agent"),
        (SOURCE_IMPORT, "Import"),
        (SOURCE_REVERT, "Revert"),
    ]

    resume = models.ForeignKey(
        Resume, on_delete=models.CASCADE, related_name="revisions"
    )
    content = models.JSONField()
    template_selector = models.CharField(max_length=50, default="faangpath-simple")
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MANUAL
    )
    tool_name = models.CharField(max_length=50, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["resume", "-created_at"])]

    def __str__(self):
        return f"{self.resume_id} @ {self.created_at:%Y-%m-%d %H:%M} ({self.source})"


class Feedback(models.Model):
    RATING_CHOICES = [(i, i) for i in range(1, 6)]
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="feedback"
    )
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

    TIER_FREE = "free"
    TIER_PRO = "pro"
    TIER_CHOICES = [(TIER_FREE, "Free"), (TIER_PRO, "Pro")]

    UI_STANDARD = "standard"
    UI_AGENTIC = "agentic"
    UI_MODE_CHOICES = [(UI_STANDARD, "Standard"), (UI_AGENTIC, "Agentic")]
    # Mode used when the user has never made an explicit choice (ui_mode is NULL).
    UI_MODE_DEFAULT = UI_AGENTIC

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    tier = models.CharField(max_length=10, choices=TIER_CHOICES, default=TIER_FREE)
    # Access bought outright, not a subscription: when this passes, the account
    # falls back to free and the data stays put. Set by the payment webhook;
    # `tier` remains the manual override for staff-granted accounts.
    premium_until = models.DateTimeField(null=True, blank=True)
    # NULL means "the user has not chosen a mode yet" — resolves to UI_MODE_DEFAULT.
    # An explicit choice is always preserved.
    ui_mode = models.CharField(
        max_length=20, choices=UI_MODE_CHOICES, null=True, blank=True, default=None
    )
    ui_language = models.CharField(max_length=5, default='en')
    # Whether the agent stops for confirmation before changing a resume. Some
    # people want the speed and have the change history to fall back on.
    confirm_destructive = models.BooleanField(default=True)

    # Monthly quota counters
    import_count = models.IntegerField(default=0)
    enhance_count = models.IntegerField(default=0)
    download_count = models.IntegerField(default=0)
    agent_message_count = models.IntegerField(default=0)
    quota_reset_date = models.DateField(auto_now_add=True)

    def reset_if_new_month(self):
        """Reset monthly quotas if a new month has started."""
        today = date.today()
        if (
            today.month != self.quota_reset_date.month
            or today.year != self.quota_reset_date.year
        ):
            self.import_count = 0
            self.enhance_count = 0
            self.download_count = 0
            self.agent_message_count = 0
            self.quota_reset_date = today
            self.save()

    @property
    def resolved_ui_mode(self):
        """The mode to render: the user's explicit choice, or the product default."""
        return self.ui_mode or self.UI_MODE_DEFAULT

    def is_pro(self):
        """Pro either because staff set the tier, or because access was bought."""
        if self.tier == self.TIER_PRO:
            return True
        return bool(self.premium_until and self.premium_until > timezone.now())

    @property
    def premium_days_left(self):
        if self.tier == self.TIER_PRO:
            return None  # granted indefinitely
        if not self.premium_until:
            return 0
        remaining = self.premium_until - timezone.now()
        return max(0, remaining.days)

    def grant_premium(self, days):
        """
        Extend paid access by `days`.

        Extends from whichever is later — now, or an unexpired balance — so
        buying again before expiry adds time instead of discarding it.
        """
        start = max(timezone.now(), self.premium_until or timezone.now())
        self.premium_until = start + timedelta(days=days)
        self.save(update_fields=["premium_until"])
        return self.premium_until

    def can_import(self):
        """Check if user can import a PDF."""
        if self.is_pro():
            return True
        self.reset_if_new_month()
        from django.conf import settings

        return self.import_count < settings.FREE_TIER_LIMITS["import_count"]

    def can_enhance(self):
        """Check if user can use AI enhancement."""
        if self.is_pro():
            return True
        self.reset_if_new_month()
        from django.conf import settings

        return self.enhance_count < settings.FREE_TIER_LIMITS["enhance_count"]

    def can_download(self):
        """Check if user can download a PDF."""
        if self.is_pro():
            return True
        self.reset_if_new_month()
        from django.conf import settings

        return self.download_count < settings.FREE_TIER_LIMITS["download_count"]

    def can_send_agent_message(self):
        """Check if user can send an agent chat message."""
        if self.is_pro():
            return True
        self.reset_if_new_month()
        from django.conf import settings

        return (
            self.agent_message_count < settings.FREE_TIER_LIMITS["agent_message_count"]
        )

    def can_create_resume(self):
        """
        Check if user can create a new resume.

        Translated variants are the same document in another language, so they
        are not counted — a bilingual user is not penalised for keeping both.
        """
        if self.is_pro():
            return True
        from django.conf import settings

        count = self.user.resumes.filter(translation_of__isnull=True).count()
        return count < settings.FREE_TIER_LIMITS["resume_count"]

    def __str__(self):
        return f"{self.user.username} - {self.tier.upper()}"


class Purchase(models.Model):
    """
    A completed payment.

    Kept as an audit trail independent of the profile: it answers what someone
    paid for and when, and makes webhook delivery idempotent — providers retry,
    and a retry must not grant a second period.
    """

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="purchases"
    )
    provider = models.CharField(max_length=30)
    # The provider's own id for this payment. Unique, so a redelivered webhook
    # is recognised rather than granting access twice.
    external_id = models.CharField(max_length=255, unique=True)
    plan = models.CharField(max_length=30)
    days_granted = models.IntegerField()
    amount_cents = models.IntegerField(default=0)
    currency = models.CharField(max_length=10, default="USD")
    granted_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} · {self.plan} · {self.created_at:%Y-%m-%d}"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Automatically create UserProfile when a new User is created."""
    if created:
        UserProfile.objects.create(user=instance)
