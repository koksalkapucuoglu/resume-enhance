"""
Tests for the API token a client uses to reach the MCP endpoint.

This is the foundation the MCP server sits on: a client the user has authorised
reaches the same data, under the same limits, without a browser session.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token


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

        response = self.client.post(
            "/mcp",
            data='{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}',
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {key}",
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
