"""How Google sign-in is allowed to create and reach accounts."""

import logging

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib import messages
from django.shortcuts import redirect

logger = logging.getLogger(__name__)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        """
        Refuse a Google account whose address Google has not verified.

        A verified address is the reason to offer Google sign-in at all; an
        unverified one would bring back the throwaway-address problem.
        """
        if not any(address.verified for address in sociallogin.email_addresses):
            messages.error(
                request,
                "Your Google account's email address is not verified, so it cannot be used to sign in. / "
                "Google hesabınızın e-posta adresi doğrulanmamış; bu hesapla giriş yapılamaz.",
            )
            raise ImmediateHttpResponse(redirect("login"))

    def on_authentication_error(
        self, request, provider, error=None, exception=None, extra_context=None
    ):
        """
        Send a failed or cancelled Google sign-in back where it started.

        allauth would otherwise render its own unstyled "Third-Party Login
        Failure" page — for someone who pressed Cancel on Google's consent
        screen, or opened the callback address without coming from Google.
        """
        # Provider and error code only: no request data, no email addresses.
        logger.info(
            "Social sign-in did not complete: provider=%s error=%s",
            getattr(provider, "id", provider),
            error,
        )
        if str(error) == "cancelled":
            messages.info(
                request,
                "Google sign-in was cancelled. / Google ile giriş iptal edildi.",
            )
        else:
            messages.error(
                request,
                "Google sign-in could not be completed. Please try again. / "
                "Google ile giriş tamamlanamadı. Lütfen tekrar deneyin.",
            )
        destination = "profile" if request.user.is_authenticated else "login"
        raise ImmediateHttpResponse(redirect(destination))
