"""
Explicit consent to transferring personal data abroad (KVKK art. 9).

Shared by both ways of creating an account — the email form and the Google
sign-up step — so neither can create an account without it.
"""

from django.conf import settings
from django.utils import timezone

CONSENT_ERROR = (
    "Please give your consent to continue. / "
    "Devam etmek için açık rızanızı vermeniz gerekiyor."
)


def record_privacy_consent(user):
    """Store when consent was given and to which version of the policy."""
    profile = user.profile
    profile.privacy_consent_at = timezone.now()
    profile.privacy_consent_version = settings.PRIVACY_POLICY_VERSION
    profile.save(update_fields=["privacy_consent_at", "privacy_consent_version"])
