"""How Google sign-in is allowed to create and reach accounts."""

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib import messages
from django.shortcuts import redirect


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
