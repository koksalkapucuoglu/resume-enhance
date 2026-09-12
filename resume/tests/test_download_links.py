"""
Tests for short-lived, single-use PDF links.

A client that cannot hand over a file hands over a link instead, and the link
carries its own authorisation — so expiry and single use are what stand between
it and being a permanent public URL to someone's resume.
"""

import time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token

from resume.models import Resume
from resume.services import download_links

PDF = b"%PDF-1.7 rendered"


def content(name="Ada Lovelace"):
    return {
        "user_info": {"full_name": name, "email": "ada@example.com",
                      "skills": ["Python"]},
        "experience": [], "education": [], "projects_and_publications": [],
    }


class SignAndConsumeTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(
            user=self.user, title="CV", content=content()
        )

    def test_a_fresh_token_resolves_to_its_resume_and_owner(self):
        resume_id, user_id = download_links.consume(
            download_links.sign(self.resume)
        )
        self.assertEqual(resume_id, self.resume.pk)
        self.assertEqual(user_id, self.user.pk)

    def test_a_token_works_once(self):
        token = download_links.sign(self.resume)
        download_links.consume(token)
        with self.assertRaises(download_links.DownloadLinkError):
            download_links.consume(token)

    def test_two_tokens_for_the_same_resume_are_independent(self):
        first = download_links.sign(self.resume)
        second = download_links.sign(self.resume)
        download_links.consume(first)
        self.assertEqual(download_links.consume(second)[0], self.resume.pk)

    def test_tampering_is_rejected(self):
        token = download_links.sign(self.resume)
        with self.assertRaises(download_links.DownloadLinkError):
            download_links.consume(token[:-3] + "aaa")

    def test_a_token_from_another_salt_is_rejected(self):
        forged = signing.dumps({"r": self.resume.pk, "u": self.user.pk, "n": "x"},
                               salt="something-else")
        with self.assertRaises(download_links.DownloadLinkError):
            download_links.consume(forged)

    def test_nonsense_is_rejected(self):
        with self.assertRaises(download_links.DownloadLinkError):
            download_links.consume("not-a-token")

    @override_settings(DOWNLOAD_LINK_MAX_AGE=1)
    def test_it_expires(self):
        token = download_links.sign(self.resume)
        time.sleep(1.2)
        with self.assertRaises(download_links.DownloadLinkError) as ctx:
            download_links.consume(token)
        self.assertIn("expired", str(ctx.exception))


class DownloadLinkEndpointTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.token = Token.objects.create(user=self.user)
        self.auth = {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}
        self.resume = Resume.objects.create(
            user=self.user, title="CV", content=content()
        )

    def _mint(self):
        return self.client.post(
            f"/api/v1/resumes/{self.resume.pk}/download-link/", **self.auth
        )

    def test_minting_returns_an_absolute_single_use_url(self):
        body = self._mint().json()
        self.assertIn("/d/", body["download_url"])
        self.assertTrue(body["download_url"].startswith("http"))
        self.assertTrue(body["single_use"])
        self.assertEqual(body["expires_in_seconds"], download_links.max_age())

    def test_minting_needs_a_token(self):
        self.assertEqual(
            self.client.post(
                f"/api/v1/resumes/{self.resume.pk}/download-link/"
            ).status_code,
            401,
        )

    def test_another_users_resume_cannot_be_minted(self):
        eve = User.objects.create_user("eve", password="x")
        theirs = Resume.objects.create(user=eve, title="Eve CV", content=content("Eve"))
        response = self.client.post(
            f"/api/v1/resumes/{theirs.pk}/download-link/", **self.auth
        )
        self.assertEqual(response.status_code, 404)

    def test_minting_is_refused_over_quota(self):
        self.user.profile.download_count = 99
        self.user.profile.save()
        self.assertEqual(self._mint().status_code, 403)

    @patch("resume.views.resume_pdf_service.generate_resume_pdf", return_value=PDF)
    def test_following_the_link_serves_the_pdf(self, _render):
        url = self._mint().json()["download_url"]
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("attachment", response["Content-Disposition"])

    @patch("resume.views.resume_pdf_service.generate_resume_pdf", return_value=PDF)
    def test_the_link_needs_no_session_or_token(self, _render):
        """A browser following it has neither."""
        url = self._mint().json()["download_url"]
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 200)

    @patch("resume.views.resume_pdf_service.generate_resume_pdf", return_value=PDF)
    def test_a_second_visit_is_refused(self, _render):
        url = self._mint().json()["download_url"]
        self.client.get(url)
        second = self.client.get(url)
        self.assertEqual(second.status_code, 410)
        self.assertIn("already been used", second.content.decode())

    @patch("resume.views.resume_pdf_service.generate_resume_pdf", return_value=PDF)
    def test_the_quota_is_charged_on_delivery(self, _render):
        before = self.user.profile.download_count
        self._mint()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.download_count, before)

        self.client.get(self._mint().json()["download_url"])
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.download_count, before + 1)

    @patch("resume.views.resume_pdf_service.generate_resume_pdf", return_value=PDF)
    def test_a_link_that_is_never_followed_costs_nothing(self, _render):
        before = self.user.profile.download_count
        for _ in range(3):
            self._mint()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.download_count, before)

    def test_a_failed_render_does_not_charge_the_quota(self):
        from resume.services.pdf_service import PdfGenerationError

        url = self._mint().json()["download_url"]
        before = self.user.profile.download_count
        with patch(
            "resume.views.resume_pdf_service.generate_resume_pdf",
            side_effect=PdfGenerationError("boom"),
        ):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 500)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.download_count, before)

    @patch("resume.views.resume_pdf_service.generate_resume_pdf", return_value=PDF)
    def test_the_quota_is_rechecked_at_delivery(self, _render):
        """Minting under quota then going over must not still deliver."""
        url = self._mint().json()["download_url"]
        self.user.profile.download_count = 99
        self.user.profile.save()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_an_expired_link_says_so(self):
        url = self._mint().json()["download_url"]
        with override_settings(DOWNLOAD_LINK_MAX_AGE=-1):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 410)
        self.assertIn("expired", response.content.decode())

    @patch("resume.views.resume_pdf_service.generate_resume_pdf", return_value=PDF)
    def test_a_deleted_resume_gives_404_not_a_stale_pdf(self, _render):
        url = self._mint().json()["download_url"]
        self.resume.delete()
        self.assertEqual(self.client.get(url).status_code, 404)

    @patch("resume.views.resume_pdf_service.generate_resume_pdf", return_value=PDF)
    def test_the_filename_survives_turkish_characters(self, _render):
        self.resume.content["user_info"]["full_name"] = "Köksal Kapucuoğlu"
        self.resume.save()
        response = self.client.get(self._mint().json()["download_url"])
        disposition = response["Content-Disposition"]
        self.assertIn("Koksal_Kapucuoglu", disposition)
        self.assertTrue(disposition.isascii())
