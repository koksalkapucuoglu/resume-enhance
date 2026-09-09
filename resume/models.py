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
    # A derived resume is the same document in another form — another language,
    # or tailored to one job. It hangs off a base resume and does not consume a
    # resume slot: charging twice would penalise exactly the bilingual, many-
    # applications user this is built for.
    # A per-job version is no longer a resume of its own: it lives inside the
    # application as a frozen snapshot, so the resume list does not grow with
    # every posting applied to.
    DERIVED_TRANSLATION = "translation"
    DERIVED_KIND_CHOICES = [
        (DERIVED_TRANSLATION, "Language version"),
    ]

    derived_from = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="derivatives",
    )
    derived_kind = models.CharField(
        max_length=20, choices=DERIVED_KIND_CHOICES, blank=True, default=""
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
    def is_derived(self):
        return self.derived_from_id is not None

    @property
    def root(self):
        """The base resume this one derives from — itself if it is a base."""
        return self.derived_from or self

    def family(self, kind=None):
        """The base resume and its derivatives, oldest first."""
        root = self.root
        qs = Resume.objects.filter(
            models.Q(pk=root.pk) | models.Q(derived_from=root)
        )
        if kind:
            qs = qs.filter(models.Q(pk=root.pk) | models.Q(derived_kind=kind))
        return qs.order_by("created_at")

    def language_family(self):
        """This resume and its language versions, oldest first."""
        return self.family(kind=self.DERIVED_TRANSLATION)

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
    # Fingerprint of the posting body, so pasting the same advert twice updates
    # the application instead of opening a second one. Derived from the text
    # rather than the title, which the model may summarise differently.
    content_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # What was actually sent, frozen. A live reference cannot answer "what did
    # they receive": tailoring the same resume for a later posting would rewrite
    # this application's record of itself.
    snapshot_content = models.JSONField(default=dict, blank=True)
    snapshot_template = models.CharField(max_length=50, blank=True, default="")
    snapshot_taken_at = models.DateTimeField(null=True, blank=True)

    # The base resume the snapshot came from. Only used to group applications
    # ("which resume do I send for which kind of role"), so losing the resume
    # does not invalidate the application.
    source_resume = models.ForeignKey(
        Resume,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_postings",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_SAVED
    )
    # The latest measurement. Every measurement is kept in score_history as
    # {at, score, resume_id} so "did my edit help?" has an answer.
    match_score = models.IntegerField(null=True, blank=True)
    score_history = models.JSONField(default=list, blank=True)
    missing_keywords = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["user", "-updated_at"])]

    @staticmethod
    def fingerprint(description):
        """Stable id for a posting body, insensitive to whitespace and case."""
        import hashlib
        import re

        normalized = re.sub(r"\s+", " ", (description or "")).strip().lower()
        return hashlib.sha256(normalized.encode()).hexdigest() if normalized else ""

    def take_snapshot(self, content, template_selector, source_resume=None):
        """Freeze what would be sent for this application."""
        import copy as copy_module

        from django.utils import timezone

        self.snapshot_content = copy_module.deepcopy(content or {})
        self.snapshot_template = template_selector or "faangpath-simple"
        self.snapshot_taken_at = timezone.now()
        if source_resume is not None:
            self.source_resume = source_resume

    @property
    def has_snapshot(self):
        return bool(self.snapshot_content)

    @property
    def snapshot_name(self):
        """A name for the frozen document, for previews and clone titles."""
        full_name = (self.snapshot_content.get("user_info") or {}).get(
            "full_name", ""
        ).strip()
        return f"{full_name} → {self.title}" if full_name else self.title

    def record_score(self, score, resume_id=None, missing_keywords=None):
        """Add a measurement and return the one before it, if any."""
        from django.utils import timezone

        previous = self.match_score
        self.score_history = list(self.score_history or [])[-19:] + [
            {
                "at": timezone.now().isoformat(timespec="seconds"),
                "score": score,
                "resume_id": resume_id,
            }
        ]
        self.match_score = score
        if missing_keywords is not None:
            self.missing_keywords = missing_keywords
        return previous

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

    def can_track_application(self):
        """
        Whether another application can be tracked.

        Each one stores a resume snapshot, so the free allowance is bounded.
        """
        if self.is_pro():
            return True
        from django.conf import settings

        return (
            self.user.job_postings.count()
            < settings.FREE_TIER_LIMITS["application_count"]
        )

    def can_create_resume(self):
        """
        Check if user can create a new resume.

        Only base resumes count. Derived ones — language versions and per-job
        variants — are the same document in another form.
        """
        if self.is_pro():
            return True
        from django.conf import settings

        count = self.user.resumes.filter(derived_from__isnull=True).count()
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
