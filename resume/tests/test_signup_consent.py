"""
Explicit consent at sign-up.

ResuStack cannot run without providers abroad, so the consent is required, and
it is recorded with the policy version it was given to: under KVKK the data
controller has to be able to show that consent was given, and when.
"""

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class SignupConsentTests(TestCase):
    url = reverse("signup")

    def payload(self, **overrides):
        data = {
            "username": "newcomer",
            "email": "new@example.com",
            "password1": "a-long-unusual-passphrase-42",
            "password2": "a-long-unusual-passphrase-42",
            "privacy_consent": "on",
        }
        data.update(overrides)
        return data

    def test_the_form_asks_for_consent(self):
        body = self.client.get(self.url).content.decode()
        self.assertIn('name="privacy_consent"', body)
        self.assertIn("açık rıza", body)

    def test_no_account_without_consent(self):
        data = self.payload()
        del data["privacy_consent"]
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="newcomer").exists())
        self.assertIn("consent", response.content.decode())

    def test_consent_is_recorded_with_the_policy_version(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 302)
        profile = User.objects.get(username="newcomer").profile
        self.assertIsNotNone(profile.privacy_consent_at)
        self.assertEqual(profile.privacy_consent_version, settings.PRIVACY_POLICY_VERSION)

    def test_both_policies_state_the_consent_basis(self):
        en = self.client.get(reverse("resume:privacy")).content.decode()
        tr = self.client.get(reverse("resume:privacy_tr")).content.decode()
        self.assertIn("explicit consent you give", en)
        self.assertIn("açık rızaya dayanarak", tr)
        self.assertIn("tamamı yurt dışındadır", tr)

    def test_the_policy_version_matches_the_date_on_the_policy(self):
        """A consent must point at the words it agreed to."""
        from datetime import date

        version = date.fromisoformat(settings.PRIVACY_POLICY_VERSION)
        en = self.client.get(reverse("resume:privacy")).content.decode()
        self.assertIn(f"Last updated: {version.day} {version:%B %Y}", en)
