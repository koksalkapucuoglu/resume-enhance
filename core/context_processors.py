"""Account state every page may need to show."""

from django.conf import settings


def account_status(request):
    user = getattr(request, "user", None)
    pending = bool(
        user
        and user.is_authenticated
        and getattr(getattr(user, "profile", None), "email_verification_required", False)
    )
    return {
        "email_verification_pending": pending,
        "google_login_enabled": settings.GOOGLE_LOGIN_ENABLED,
    }
