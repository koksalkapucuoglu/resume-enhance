"""Forms for signing up with Google."""

from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from django import forms

from core.consent import CONSENT_ERROR, record_privacy_consent


class GoogleSignupForm(SocialSignupForm):
    """
    The step between Google and a new ResuStack account.

    It exists for two reasons: to ask for the same explicit consent the email
    form asks for, and to let the user pick a username, since ResuStack signs
    people in by username.
    """

    privacy_consent = forms.BooleanField(
        required=True, error_messages={"required": CONSENT_ERROR}
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The address is the one Google verified. Left editable, it would let
        # someone create an account under an address Google never vouched for.
        if "email" in self.fields:
            self.fields["email"].disabled = True

    def save(self, request):
        user = super().save(request)
        record_privacy_consent(user)
        return user
