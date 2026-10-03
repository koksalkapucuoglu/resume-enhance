"""
Keep ResuStack's own email-verification flag in step with allauth.

The AI lock reads `UserProfile.email_verification_required`. An address
confirmed through allauth — or marked verified by staff in the admin's
"Email addresses" table — is the same proof, so it lifts the lock too.
"""

from allauth.account.models import EmailAddress
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=EmailAddress)
def verified_address_unlocks_ai(sender, instance, **kwargs):
    if not instance.verified:
        return
    user = instance.user
    if (instance.email or "").lower() != (user.email or "").lower():
        return
    profile = getattr(user, "profile", None)
    if profile is not None and profile.email_verification_required:
        profile.email_verification_required = False
        profile.save(update_fields=["email_verification_required"])
