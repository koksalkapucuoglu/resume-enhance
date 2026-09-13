"""
The privacy policy and the MCP Registry listing.

Both are read by people deciding whether to trust ResuStack with their data,
so the tests check the facts they state rather than just that pages load.
"""

import json
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from mcp_server import protocol

SERVER_JSON = Path(settings.BASE_DIR) / "server.json"


class PrivacyPolicyTests(TestCase):
    def body(self):
        response = self.client.get(reverse("resume:privacy"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_is_public(self):
        self.body()

    def test_names_every_processor_that_receives_user_data(self):
        """AI features send resume text to OpenAI; the policy must say so."""
        body = self.body()
        for processor in ("OpenAI", "Google", "Lemon Squeezy", "cdn.tailwindcss.com", "unpkg.com"):
            self.assertIn(processor, body)

    def test_states_passwords_are_hashed_not_absent(self):
        body = self.body()
        self.assertIn("one-way hash", body)

    def test_gives_a_way_to_delete_the_account(self):
        self.assertIn("mailto:", self.body())

    def test_is_linked_from_the_landing_page(self):
        landing = self.client.get(reverse("resume:index")).content.decode()
        self.assertIn(reverse("resume:privacy"), landing)


class RegistryProofTests(TestCase):
    url = "/.well-known/mcp-registry-auth"

    @override_settings(MCP_REGISTRY_AUTH="v=MCPv1; k=ed25519; p=AAAA")
    def test_serves_the_proof_record(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().strip(), "v=MCPv1; k=ed25519; p=AAAA")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))

    @override_settings(MCP_REGISTRY_AUTH="")
    def test_is_absent_until_configured(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    @override_settings(MCP_REGISTRY_AUTH="-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEI")
    def test_never_publishes_something_that_is_not_a_proof_record(self):
        """A private key pasted into the variable by mistake must not go public."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("PRIVATE", response.content.decode())

    def test_the_route_is_at_the_domain_root(self):
        self.assertEqual(reverse("resume:mcp_registry_auth"), self.url)


class ServerJsonTests(TestCase):
    def setUp(self):
        self.data = json.loads(SERVER_JSON.read_text())

    def test_uses_the_verified_domain_namespace(self):
        self.assertEqual(self.data["name"], "com.resustackapp/resustack")

    def test_fits_the_registry_schema_limits(self):
        self.assertLessEqual(len(self.data["description"]), 100)
        self.assertLessEqual(len(self.data["title"]), 100)
        for forbidden in ("latest", "^", "~", ">", "x", "*"):
            self.assertNotIn(forbidden, self.data["version"])

    def test_version_matches_what_the_server_reports(self):
        self.assertEqual(self.data["version"], protocol.SERVER_INFO["version"])

    def test_remote_points_at_the_real_endpoint(self):
        (remote,) = self.data["remotes"]
        self.assertEqual(remote["type"], "streamable-http")
        parsed = urlparse(remote["url"])
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.path, reverse("mcp_server:endpoint"))

    def test_declares_the_token_as_a_required_secret(self):
        (remote,) = self.data["remotes"]
        (header,) = remote["headers"]
        self.assertEqual(header["name"], "Authorization")
        self.assertTrue(header["isRequired"])
        self.assertTrue(header["isSecret"])
