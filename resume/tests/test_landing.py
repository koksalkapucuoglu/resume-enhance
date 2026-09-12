"""
The public landing page.

It promotes what the product actually does, from the same sources the product
uses — the design catalogue and the MCP tool registry — so it cannot fall
behind them, and it must not make claims nothing backs up.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from mcp_server import registry
from mcp_server import tools as _mcp_tools  # noqa: F401  (registers the tools)
from resume import resume_templates


class LandingPageTests(TestCase):
    def get(self):
        response = self.client.get(reverse("resume:index"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_every_design_is_showcased(self):
        body = self.get()
        for design in resume_templates.catalog():
            self.assertIn(design.name, body)

    def test_the_mcp_section_lists_the_real_tools(self):
        body = self.get()
        for descriptor in registry.descriptors():
            self.assertIn(descriptor["name"], body)
        self.assertIn(reverse("mcp_server:endpoint"), body)

    def test_no_claims_nothing_backs_up(self):
        """The old page named companies, a model we do not use, and a score we never computed."""
        body = self.get()
        for phrase in (
            "Trusted by candidates",
            "GPT-4",
            "98/100",
            "thousands of job seekers",
        ):
            self.assertNotIn(phrase, body)

    def test_pricing_links_go_to_the_pricing_page(self):
        self.assertIn(reverse("resume:pricing"), self.get())

    def test_no_dead_links(self):
        self.assertNotIn('href="#"', self.get())

    def test_signed_in_users_go_to_their_dashboard(self):
        user = User.objects.create_user("landing", password="pw")
        self.client.force_login(user)
        response = self.client.get(reverse("resume:index"))
        self.assertRedirects(response, reverse("resume:dashboard"), fetch_redirect_response=False)
