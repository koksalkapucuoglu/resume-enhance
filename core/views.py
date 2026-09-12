"""
Authentication views for user signup and profile.
"""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_http_methods


class SignupForm(UserCreationForm):
    """Custom signup form with email field."""

    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(
            attrs={"class": "form-input", "placeholder": "you@example.com"}
        ),
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
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
            login(request, user)  # Auto-login after signup
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


@method_decorator(login_required, name="dispatch")
class ProfileView(View):
    """User profile view with password change."""

    def _get_quota_percentages(self, request):
        """Calculate quota percentages for progress bars."""
        profile = request.user.profile
        limits = settings.FREE_TIER_LIMITS
        return {
            "import_percent": int((profile.import_count / limits["import_count"]) * 100)
            if limits["import_count"] > 0
            else 0,
            "enhance_percent": int(
                (profile.enhance_count / limits["enhance_count"]) * 100
            )
            if limits["enhance_count"] > 0
            else 0,
            "download_percent": int(
                (profile.download_count / limits["download_count"]) * 100
            )
            if limits["download_count"] > 0
            else 0,
            "resume_percent": int(
                (request.user.resumes.count() / limits["resume_count"]) * 100
            )
            if limits["resume_count"] > 0
            else 0,
        }

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
                "quota_percentages": self._get_quota_percentages(request),
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
                "quota_percentages": self._get_quota_percentages(request),
                **self._token_context(request),
            },
        )
