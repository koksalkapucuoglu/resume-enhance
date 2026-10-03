"""
Authentication views for user signup and profile.
"""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_http_methods

from core import email_verification
from core.consent import CONSENT_ERROR, record_privacy_consent


def _ui(user):
    """Interface strings in the user's chosen language."""
    from resume.i18n import TRANSLATIONS

    return TRANSLATIONS.get(user.profile.ui_language, TRANSLATIONS["en"])


class SignupForm(UserCreationForm):
    """Custom signup form with email field."""

    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(
            attrs={"class": "form-input", "placeholder": "you@example.com"}
        ),
    )

    # Explicit consent to the transfers abroad the service depends on (OpenAI,
    # Hetzner, Cloudflare, Google). Separate from being told about the policy:
    # KVKK treats information and consent as two different things.
    privacy_consent = forms.BooleanField(
        required=True, error_messages={"required": CONSENT_ERROR}
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            record_privacy_consent(user)
        return user


class SignupView(View):
    """User registration view."""

    def get(self, request):
        """Display signup form."""
        if request.user.is_authenticated:
            return redirect("resume:index")

        form = SignupForm()
        return render(request, "registration/signup.html", {"form": form})

    def post(self, request):
        """Process signup form."""
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            # The account works now; AI features wait for the address.
            if email_verification.require_verification(request, user):
                messages.info(request, _ui(user)["verify_sent"])
            else:
                messages.warning(request, _ui(user)["verify_send_failed"])
            # Two authentication backends are configured (allauth for Google),
            # so Django needs to be told which one vouched for this user.
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("resume:index")

        return render(request, "registration/signup.html", {"form": form})


@login_required
@require_http_methods(["POST"])
def issue_api_token(request):
    """
    Create or replace the caller's API token.

    Shown once and then only as a prefix: the stored value is the credential
    itself, so re-displaying it would keep a bearer secret on screen long after
    the moment it was needed. Replacing revokes the old one, which is how a
    leaked token is dealt with.
    """
    from rest_framework.authtoken.models import Token

    Token.objects.filter(user=request.user).delete()
    token = Token.objects.create(user=request.user)
    messages.success(request, "New token created. Copy it now — it is shown once.")
    request.session["fresh_api_token"] = token.key
    return redirect("profile")


@login_required
@require_http_methods(["POST"])
def revoke_api_token(request):
    """Revoke the token without issuing another."""
    from rest_framework.authtoken.models import Token

    deleted, _ = Token.objects.filter(user=request.user).delete()
    if deleted:
        messages.success(request, "Token revoked. Any client using it is now cut off.")
    request.session.pop("fresh_api_token", None)
    return redirect("profile")


@require_http_methods(["GET"])
def verify_email(request, token):
    """
    Follow the link from the verification email.

    Works signed in or out: people often open the email on another device.
    """
    from django.core import signing

    destination = "resume:dashboard" if request.user.is_authenticated else "login"
    try:
        payload = email_verification.read_token(token)
    except signing.SignatureExpired:
        messages.error(request, _ui_for(request)["verify_expired"])
        return redirect(destination)
    except signing.BadSignature:
        messages.error(request, _ui_for(request)["verify_invalid"])
        return redirect(destination)

    user = User.objects.filter(pk=payload.get("u")).first()
    # A link sent to an address the account no longer has is not proof of the
    # current one.
    if user is None or user.email != payload.get("e"):
        messages.error(request, _ui_for(request)["verify_invalid"])
        return redirect(destination)

    profile = user.profile
    if profile.email_verification_required:
        profile.email_verification_required = False
        profile.save(update_fields=["email_verification_required"])
    messages.success(request, _ui(user)["verify_done"])
    return redirect(destination)


@login_required
@require_http_methods(["POST"])
def resend_verification_email(request):
    """Send the verification link again, at most once a minute."""
    from django.core.cache import cache
    from django.utils.http import url_has_allowed_host_and_scheme

    user = request.user
    ui = _ui(user)
    back = request.META.get("HTTP_REFERER", "")
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = reverse("resume:dashboard")

    if not user.profile.email_verification_required:
        messages.info(request, ui["verify_already"])
    elif not cache.add(f"verify_resend_{user.pk}", 1, 60):
        messages.warning(request, ui["verify_wait"])
    elif email_verification.send_verification_email(request, user):
        messages.success(request, ui["verify_sent"])
    else:
        messages.error(request, ui["verify_send_failed"])
    return redirect(back)


def _ui_for(request):
    """Interface strings for a request that may not be signed in."""
    from resume.i18n import TRANSLATIONS

    if request.user.is_authenticated:
        return _ui(request.user)
    return TRANSLATIONS["en"]


@login_required
@require_http_methods(["POST"])
def delete_account(request):
    """
    Delete the signed-in user's account and everything that belongs to it.

    The password is asked for again because this is irreversible and a stolen
    session should not be enough. What disappears follows from the models'
    on_delete rules — resumes, restore points, language versions, applications,
    profile and API tokens cascade; feedback is kept with the user cleared —
    and the privacy policy promises exactly that list.
    """
    user = request.user
    ui = _ui(user)

    if user.has_usable_password():
        confirmed = user.check_password(request.POST.get("password", ""))
        failure = ui["delete_account_wrong_password"]
    else:
        # Accounts created with Google have no password to ask for; typing the
        # username is the deliberate step instead.
        confirmed = request.POST.get("confirm_username", "").strip() == user.username
        failure = ui["delete_account_wrong_username"]

    if not confirmed:
        messages.error(request, failure)
        return redirect(f"{reverse('profile')}#delete-account")

    # End the session first, then delete: the message is added after the
    # session is flushed so it survives into the next page.
    logout(request)
    user.delete()
    messages.success(request, ui["delete_account_done"])
    return redirect("resume:index")


@method_decorator(login_required, name="dispatch")
class ProfileView(View):
    """User profile view with password change."""

    def _ui(self, request):
        from resume.i18n import TRANSLATIONS

        return TRANSLATIONS.get(request.user.profile.ui_language or "en", TRANSLATIONS["en"])

    def _quota_rows(self, request, ui):
        """The free plan's four allowances, as progress rows."""
        allowances = request.user.profile.usage()["allowances"]
        rows = []
        for key in ("ai_credits", "downloads", "resumes", "job_copies"):
            a = allowances[key]
            if a["limit"] is None:
                continue
            rows.append({
                "label": ui[f"usage_{key}"],
                "hint": ui[f"usage_{key}_hint"],
                "used": a["used"],
                "limit": a["limit"],
                "percent": min(100, int(a["used"] * 100 / a["limit"])) if a["limit"] else 0,
            })
        return rows

    def _token_context(self, request):
        """
        The token, once.

        A freshly issued key is handed over through the session and removed on
        read, so a refresh does not put it back on screen.
        """
        from rest_framework.authtoken.models import Token

        token = Token.objects.filter(user=request.user).first()
        return {
            "api_token": token,
            "fresh_api_token": request.session.pop("fresh_api_token", None),
            # The address to paste into the client. Built from this request so
            # it is right in development and behind the proxy alike.
            "mcp_endpoint": request.build_absolute_uri(reverse("mcp_server:endpoint")),
        }

    def get(self, request):
        """Display profile page."""
        password_form = PasswordChangeForm(request.user)
        return render(
            request,
            "registration/profile.html",
            {
                "password_form": password_form,
                "settings": settings,
                "quota_rows": self._quota_rows(request, self._ui(request)),
                **self._token_context(request),
            },
        )

    def post(self, request):
        """Handle password change."""
        password_form = PasswordChangeForm(request.user, request.POST)

        if password_form.is_valid():
            user = password_form.save()
            # Keep user logged in after password change
            update_session_auth_hash(request, user)
            messages.success(request, "Your password has been changed successfully!")
            return redirect("profile")

        return render(
            request,
            "registration/profile.html",
            {
                "password_form": password_form,
                "settings": settings,
                "quota_rows": self._quota_rows(request, self._ui(request)),
                **self._token_context(request),
            },
        )
