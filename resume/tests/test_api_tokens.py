"""
Tests for token-authenticated API access.

This is the foundation the MCP server sits on: a client the user has authorised
reaches the same data, under the same limits, without a browser session.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token


def content(name="Ada Lovelace", skills=None):
    return {
        "user_info": {
            "full_name": name,
            "email": "ada@example.com",
            "skills": skills if skills is not None else ["Python", "Django"],
        },
        "experience": [],
        "education": [],
        "projects_and_publications": [],
    }


class TokenIssueTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)

    def test_issuing_creates_a_token_and_shows_it_once(self):
        response = self.client.post(reverse("issue_api_token"), follow=True)
        token = Token.objects.get(user=self.user)
        self.assertContains(response, token.key)

        # A reload must not put the secret back on screen
        again = self.client.get(reverse("profile"))
        self.assertNotContains(again, token.key)
        self.assertContains(again, token.key[:8])

    def test_replacing_revokes_the_previous_one(self):
        self.client.post(reverse("issue_api_token"))
        first = Token.objects.get(user=self.user).key
        self.client.post(reverse("issue_api_token"))
        second = Token.objects.get(user=self.user).key

        self.assertNotEqual(first, second)
        self.assertEqual(Token.objects.filter(user=self.user).count(), 1)

    def test_revoking_removes_it(self):
        self.client.post(reverse("issue_api_token"))
        self.client.post(reverse("revoke_api_token"))
        self.assertFalse(Token.objects.filter(user=self.user).exists())

    def test_a_revoked_token_stops_working(self):
        self.client.post(reverse("issue_api_token"))
        key = Token.objects.get(user=self.user).key
        self.client.post(reverse("revoke_api_token"))
        self.client.logout()

        response = self.client.get(
            "/api/v1/resumes/", HTTP_AUTHORIZATION=f"Token {key}"
        )
        self.assertEqual(response.status_code, 401)

    def test_the_rules_are_stated_on_the_page(self):
        """Someone connecting a client should know what it can and cannot do."""
        html = self.client.get(reverse("profile")).content.decode()
        self.assertIn("cannot delete anything", html)
        self.assertIn("can be undone", html)
        self.assertIn("like a password", html)

    def test_issuing_requires_post_and_login(self):
        self.assertEqual(
            self.client.get(reverse("issue_api_token")).status_code, 405
        )
        self.client.logout()
        self.assertEqual(
            self.client.post(reverse("issue_api_token")).status_code, 302
        )


class TokenApiAccessTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.token = Token.objects.create(user=self.user)
        self.auth = {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def test_no_token_is_refused(self):
        self.assertEqual(self.client.get("/api/v1/resumes/").status_code, 401)

    def test_a_token_reaches_the_api_without_a_session(self):
        from resume.models import Resume

        Resume.objects.create(user=self.user, title="CV", content=content())
        response = self.client.get("/api/v1/resumes/", **self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_another_users_resumes_are_not_visible(self):
        from resume.models import Resume

        eve = User.objects.create_user("eve", password="x")
        Resume.objects.create(user=eve, title="Eve CV", content=content("Eve"))
        response = self.client.get("/api/v1/resumes/", **self.auth)
        self.assertEqual(response.json(), [])

    def test_creating_through_the_api(self):
        response = self.client.post(
            "/api/v1/resumes/",
            {"title": "From a client", "content": content()},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["title"], "From a client")
        self.assertIn("preview_url", body)
        self.assertIn("/preview/", body["preview_url"])

    def test_the_resume_limit_applies_to_the_api_too(self):
        """A client must not be a way around the plan."""
        from django.conf import settings
        from resume.models import Resume

        for i in range(settings.FREE_TIER_LIMITS["resume_count"]):
            Resume.objects.create(user=self.user, title=f"CV {i}", content=content())

        response = self.client.post(
            "/api/v1/resumes/",
            {"title": "One too many", "content": content()},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 403)

    def test_content_is_normalised_on_the_way_in(self):
        """Clients send skills as a string often enough."""
        response = self.client.post(
            "/api/v1/resumes/",
            {"title": "CV", "content": content(skills="Python, Django, Docker")},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 201)
        from resume.models import Resume

        resume = Resume.objects.get(pk=response.json()["id"])
        self.assertEqual(
            resume.content["user_info"]["skills"], ["Python", "Django", "Docker"]
        )

    def test_an_unknown_template_is_refused_with_the_options(self):
        response = self.client.post(
            "/api/v1/resumes/",
            {"title": "CV", "content": content(), "template_selector": "nope"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("faangpath-simple", str(response.json()))

    def test_detail_carries_what_a_client_needs_to_render(self):
        from resume.models import Resume

        resume = Resume.objects.create(
            user=self.user, title="CV", content=content(),
            template_selector="modern-sidebar", language="tr",
        )
        body = self.client.get(f"/api/v1/resumes/{resume.pk}/", **self.auth).json()
        self.assertEqual(body["template_selector"], "modern-sidebar")
        self.assertEqual(body["language"], "tr")
        self.assertIn("display_name", body)
        self.assertIn("preview_url", body)

    def test_updating_through_the_api(self):
        from resume.models import Resume

        resume = Resume.objects.create(user=self.user, title="CV", content=content())
        response = self.client.patch(
            f"/api/v1/resumes/{resume.pk}/",
            {"content": content("Grace Hopper")},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 200)
        resume.refresh_from_db()
        self.assertEqual(resume.content["user_info"]["full_name"], "Grace Hopper")

    def test_another_users_resume_cannot_be_updated(self):
        from resume.models import Resume

        eve = User.objects.create_user("eve", password="x")
        theirs = Resume.objects.create(user=eve, title="Eve CV", content=content("Eve"))
        response = self.client.patch(
            f"/api/v1/resumes/{theirs.pk}/",
            {"title": "Hijacked"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.title, "Eve CV")
