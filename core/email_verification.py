"""
Soft email verification for accounts created with an email address.

Soft: the account works straight away, but AI features stay locked until the
address is confirmed. That stops throwaway addresses from multiplying the free
AI allowance without making anyone wait to see the product.

The link is a signed, expiring token rather than a stored code — the same
approach as the signed download links — carrying the user id and the address
it was sent to, so a link stops working if the address changes.
"""

import logging

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.urls import reverse

logger = logging.getLogger(__name__)

SALT = "resustack.email-verification"


def make_token(user):
    return signing.dumps({"u": user.pk, "e": user.email}, salt=SALT, compress=True)


def read_token(token):
    """The payload, or signing.BadSignature / signing.SignatureExpired."""
    return signing.loads(token, salt=SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE)


def send_verification_email(request, user):
    """Send the confirmation link. Returns whether it was handed to the mail server."""
    link = request.build_absolute_uri(reverse("verify_email", args=[make_token(user)]))
    days = settings.EMAIL_VERIFICATION_MAX_AGE // 86400
    body = (
        f"Confirm your email address to start using the AI features on ResuStack:\n\n"
        f"{link}\n\n"
        f"The link works for {days} days. If you did not create a ResuStack account, ignore this email.\n\n"
        f"---\n\n"
        f"ResuStack'teki yapay zekâ özelliklerini kullanmaya başlamak için e-posta adresinizi doğrulayın:\n\n"
        f"{link}\n\n"
        f"Bağlantı {days} gün geçerlidir. ResuStack hesabı oluşturmadıysanız bu e-postayı yok sayın.\n"
    )
    try:
        send_mail(
            "Confirm your email for ResuStack / ResuStack e-postanızı doğrulayın",
            body,
            None,
            [user.email],
        )
        return True
    except Exception:  # noqa: BLE001 — a mail outage must not break sign-up
        # The user id only: logs never carry email addresses.
        logger.warning("Verification email could not be sent for user %s", user.pk)
        return False


def require_verification(request, user):
    """Lock AI features for a new email sign-up and send the link."""
    profile = user.profile
    profile.email_verification_required = True
    profile.save(update_fields=["email_verification_required"])
    return send_verification_email(request, user)
