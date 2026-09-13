"""
Sign in with Google, and soft email verification for email sign-ups.

The tests that matter most here are the negative ones: nobody reaches an
existing account by matching an email, nobody skips the consent step, and an
unconfirmed address cannot use the AI features.
"""

import json
from urllib.parse import urlparse
from unittest.mock import patch

from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from core import email_verification
from core.adapters import SocialAccountAdapter
from core.forms import GoogleSignupForm

PASSWORD = "a-long-unusual-passphrase-42"
GOOGLE_APP = {
    "google": {
        **settings.SOCIALACCOUNT_PROVIDERS["google"],
        "APP": {"client_id": "test-client", "secret": "test-secret", "key": ""},
    }
}


def request_with_session(method="get"):
    request = getattr(RequestFactory(), method)("/")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)
    return request


class SafetySettingsTests(TestCase):
    def test_no_account_is_reached_by_matching_an_email(self):
        self.assertFalse(settings.SOCIALACCOUNT_EMAIL_AUTHENTICATION)
        self.assertFalse(settings.SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT)

    def test_google_sign_ups_always_stop_at_the_consent_step(self):
        self.assertFalse(settings.SOCIALACCOUNT_AUTO_SIGNUP)

    def test_a_link_cannot_start_a_sign_in(self):
        self.assertFalse(settings.SOCIALACCOUNT_LOGIN_ON_GET)

    def test_google_tokens_are_not_stored(self):
        self.assertFalse(settings.SOCIALACCOUNT_STORE_TOKENS)

    def test_our_own_sign_in_and_sign_up_answer_first(self):
        self.assertEqual(resolve("/accounts/login/").func.view_class.__name__, "LoginView")
        self.assertEqual(resolve("/accounts/signup/").func.view_class.__name__, "SignupView")

    def test_the_callback_path_is_the_one_documented(self):
        self.assertEqual(reverse("google_callback"), "/accounts/google/login/callback/")


class EmailSignupVerificationTests(TestCase):
    def signup(self):
        return self.client.post(
            reverse("signup"),
            {
                "username": "newcomer",
                "email": "new@example.com",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "privacy_consent": "on",
            },
        )

    def user(self):
        return User.objects.get(username="newcomer")

    def verification_path(self):
        link = next(p for p in mail.outbox[-1].body.split() if "/accounts/verify-email/" in p)
        return urlparse(link).path

    def test_signed_in_at_once_with_ai_locked_and_a_link_sent(self):
        response = self.signup()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user().pk)
        self.assertTrue(self.user().profile.email_verification_required)
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])

    def test_following_the_link_unlocks_ai(self):
        self.signup()
        self.client.get(self.verification_path())
        self.assertFalse(self.user().profile.email_verification_required)

    def test_the_link_works_when_signed_out(self):
        self.signup()
        path = self.verification_path()
        self.client.logout()
        self.client.get(path)
        self.assertFalse(self.user().profile.email_verification_required)

    def test_a_tampered_link_changes_nothing(self):
        self.signup()
        self.client.get("/accounts/verify-email/not-a-real-token/")
        self.assertTrue(self.user().profile.email_verification_required)

    def test_an_expired_link_changes_nothing(self):
        self.signup()
        path = self.verification_path()
        with override_settings(EMAIL_VERIFICATION_MAX_AGE=-1):
            self.client.get(path)
        self.assertTrue(self.user().profile.email_verification_required)

    def test_a_link_for_a_previous_address_changes_nothing(self):
        self.signup()
        path = self.verification_path()
        user = self.user()
        user.email = "changed@example.com"
        user.save()
        self.client.get(path)
        self.assertTrue(self.user().profile.email_verification_required)

    def test_a_mail_outage_does_not_break_sign_up_or_log_the_address(self):
        with patch("core.email_verification.send_mail", side_effect=OSError("smtp down")), \
                self.assertLogs("core.email_verification", level="WARNING") as captured:
            self.signup()
        self.assertTrue(User.objects.filter(username="newcomer").exists())
        self.assertNotIn("new@example.com", "\n".join(captured.output))

    def test_resending_is_limited_to_once_a_minute(self):
        self.signup()
        mail.outbox.clear()
        self.client.post(reverse("resend_verification_email"))
        self.client.post(reverse("resend_verification_email"))
        self.assertEqual(len(mail.outbox), 1)

    def test_accounts_that_existed_before_are_not_locked(self):
        user = User.objects.create_user("old-timer", email="old@example.com", password=PASSWORD)
        self.assertFalse(user.profile.email_verification_required)


class AiGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("unconfirmed", email="u@example.com", password=PASSWORD)
        self.user.profile.email_verification_required = True
        self.user.profile.save()
        self.client.force_login(self.user)

    def test_enhance_is_refused(self):
        response = self.client.post(reverse("resume:enhance_experience"))
        self.assertEqual(response.status_code, 403)
        self.assertIn("Confirm your email", response.content.decode())

    def test_import_is_refused(self):
        response = self.client.post(reverse("resume:upload_cv"))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()["email_verification_required"])

    def test_agent_chat_is_refused_like_a_quota_answer(self):
        response = self.client.post(
            reverse("resume:agent_chat"),
            data=json.dumps({"message": "Improve my resume"}),
            content_type="application/json",
        )
        body = response.json()
        self.assertEqual(body["type"], "chat")
        self.assertTrue(body["email_verification_required"])

    def test_a_confirmed_account_passes_the_gate(self):
        self.user.profile.email_verification_required = False
        self.user.profile.save()
        response = self.client.post(reverse("resume:upload_cv"))
        # Past the gate: it now fails for the missing file instead.
        self.assertEqual(response.status_code, 400)


@override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_APP)
class GoogleSignupTests(TestCase):
    def sociallogin(self, verified=True, email="ada@gmail.com"):
        # A real Google login carries its provider; allauth uses it, for one,
        # to name the provider in the "email already registered" error.
        from allauth.socialaccount.adapter import get_adapter

        provider = get_adapter().get_provider(request_with_session(), "google")
        return SocialLogin(
            user=User(email=email),
            account=SocialAccount(provider="google", uid="10769150350006150715113082367",
                                  extra_data={"email": email, "email_verified": verified}),
            email_addresses=[EmailAddress(email=email, verified=verified, primary=True)],
            provider=provider,
        )

    def test_an_unverified_google_email_is_refused(self):
        with self.assertRaises(ImmediateHttpResponse):
            SocialAccountAdapter().pre_social_login(request_with_session(), self.sociallogin(verified=False))

    def test_a_verified_google_email_is_let_through(self):
        SocialAccountAdapter().pre_social_login(request_with_session(), self.sociallogin(verified=True))

    def test_the_sign_up_step_requires_consent(self):
        form = GoogleSignupForm(data={"username": "ada"}, sociallogin=self.sociallogin())
        self.assertFalse(form.is_valid())
        self.assertIn("privacy_consent", form.errors)

    def test_the_email_cannot_be_swapped_for_one_google_did_not_verify(self):
        form = GoogleSignupForm(
            data={"username": "ada", "email": "someone-else@example.com", "privacy_consent": "on"},
            sociallogin=self.sociallogin(),
        )
        self.assertTrue(form.fields["email"].disabled)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "ada@gmail.com")

    def test_an_existing_accounts_email_cannot_be_used_to_reach_it(self):
        """The takeover this setup exists to prevent."""
        User.objects.create_user("owner", email="ada@gmail.com", password=PASSWORD)
        form = GoogleSignupForm(
            data={"username": "intruder", "privacy_consent": "on"},
            sociallogin=self.sociallogin(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
        self.assertFalse(User.objects.filter(username="intruder").exists())

    def test_completing_the_step_records_consent_and_leaves_ai_unlocked(self):
        form = GoogleSignupForm(
            data={"username": "ada", "privacy_consent": "on"}, sociallogin=self.sociallogin()
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save(request_with_session("post"))
        profile = User.objects.get(pk=user.pk).profile
        self.assertIsNotNone(profile.privacy_consent_at)
        self.assertFalse(profile.email_verification_required)
        self.assertFalse(user.has_usable_password())


class GoogleButtonTests(TestCase):
    @override_settings(GOOGLE_LOGIN_ENABLED=False)
    def test_hidden_until_credentials_are_configured(self):
        self.assertNotIn("google-login-form", self.client.get(reverse("login")).content.decode())
        self.assertNotIn("google-signup-form", self.client.get(reverse("signup")).content.decode())

    @override_settings(GOOGLE_LOGIN_ENABLED=True, SOCIALACCOUNT_PROVIDERS=GOOGLE_APP)
    def test_sign_in_and_sign_up_offer_it_as_a_post_form(self):
        for name, form_id in (("login", "google-login-form"), ("signup", "google-signup-form")):
            body = self.client.get(reverse(name)).content.decode()
            start = body.index(f'id="{form_id}"')
            tag = body[body.rindex("<form", 0, start):start]
            self.assertIn('method="post"', tag)
            self.assertIn(reverse("google_login"), tag)

    @override_settings(GOOGLE_LOGIN_ENABLED=True, SOCIALACCOUNT_PROVIDERS=GOOGLE_APP)
    def test_the_profile_offers_to_connect_google(self):
        user = User.objects.create_user("linker", email="l@example.com", password=PASSWORD)
        self.client.force_login(user)
        body = self.client.get(reverse("profile")).content.decode()
        self.assertIn('id="google-account"', body)
        self.assertIn("process=connect", body)


class PasswordlessAccountDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("googler", email="g@example.com")
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user)

    def test_the_wrong_username_deletes_nothing(self):
        self.client.post(reverse("delete_account"), {"confirm_username": "someone"})
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_the_username_confirms_deletion(self):
        self.client.post(reverse("delete_account"), {"confirm_username": "googler"})
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

    def test_the_profile_asks_for_the_username_not_a_password(self):
        body = self.client.get(reverse("profile")).content.decode()
        self.assertIn('name="confirm_username"', body)
        self.assertNotIn('id="delete-account-password"', body)


class GoogleInPrivacyPolicyTests(TestCase):
    def test_both_versions_say_what_google_sign_in_shares(self):
        en = self.client.get(reverse("resume:privacy")).content.decode()
        tr = self.client.get(reverse("resume:privacy_tr")).content.decode()
        self.assertIn("If you sign in with Google", en)
        self.assertIn("Google ile giriş yaparsanız", tr)


class EmailSettingsTests(TestCase):
    """Account email has to work: verification locks AI until a link arrives."""

    def test_tls_and_ssl_are_never_both_on(self):
        self.assertFalse(settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL)

    def test_the_defaults_are_the_gmail_setup_production_uses(self):
        # Only meaningful when the environment does not override them.
        import os

        if not any(os.environ.get(k) for k in ("EMAIL_HOST", "EMAIL_PORT", "EMAIL_USE_TLS", "EMAIL_USE_SSL")):
            self.assertEqual(settings.EMAIL_HOST, "smtp.gmail.com")
            self.assertEqual(settings.EMAIL_PORT, 587)
            self.assertTrue(settings.EMAIL_USE_TLS)
