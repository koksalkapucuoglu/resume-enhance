"""
Tests for the tools themselves, called through the endpoint so the path a real
client takes is the path under test.
"""

import json
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token

from mcp_server import protocol, registry
from resume.models import Resume, ResumeRevision


class ToolTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="x")
        self.other = User.objects.create_user("stranger", password="x")
        self.token = Token.objects.create(user=self.user)
        self.url = reverse("mcp_server:endpoint")
        self.resume = Resume.objects.create(
            user=self.user,
            title="Ada Lovelace",
            content={
                "user_info": {"full_name": "Ada Lovelace", "skills": ["Python"]},
                "experience": [],
            },
        )

    def call(self, name, arguments=None, user_token=None):
        """Call a tool the way a legacy client would — the shorter envelope."""
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        response = self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            headers={"Authorization": f"Bearer {user_token or self.token.key}"},
        )
        return response

    def result(self, name, arguments=None, **kwargs):
        payload = self.call(name, arguments, **kwargs).json()
        self.assertNotIn("error", payload, payload.get("error"))
        return payload["result"]

    def data(self, name, arguments=None, **kwargs):
        result = self.result(name, arguments, **kwargs)
        self.assertFalse(result["isError"], result["content"][0]["text"])
        return result["structuredContent"]


class ReadingTests(ToolTestCase):
    def test_list_resumes_returns_the_users_own(self):
        Resume.objects.create(user=self.other, title="Someone Else")
        data = self.data("list_resumes")
        self.assertEqual([r["id"] for r in data["resumes"]], [self.resume.pk])

    def test_list_resumes_on_an_empty_account_says_so(self):
        token = Token.objects.create(user=self.other)
        result = self.result("list_resumes", user_token=token.key)
        self.assertIn("no resumes yet", result["content"][0]["text"])

    def test_get_resume_returns_content_and_a_preview_url(self):
        data = self.data("get_resume", {"resume_id": self.resume.pk})
        self.assertEqual(data["content"]["user_info"]["full_name"], "Ada Lovelace")
        self.assertTrue(data["preview_url"].startswith("http"))
        self.assertIn(f"/resume/{self.resume.pk}/preview/", data["preview_url"])

    def test_another_users_resume_is_not_readable(self):
        theirs = Resume.objects.create(user=self.other, title="Private")
        result = self.result("get_resume", {"resume_id": theirs.pk})
        self.assertTrue(result["isError"])
        # The message must not confirm the id exists somewhere else.
        self.assertIn("No resume", result["content"][0]["text"])

    def test_a_non_numeric_id_is_a_tool_error_not_a_crash(self):
        result = self.result("get_resume", {"resume_id": "banana"})
        self.assertTrue(result["isError"])

    def test_list_templates_matches_the_configured_map(self):
        data = self.data("list_templates")
        self.assertEqual(
            sorted(t["key"] for t in data["templates"]),
            sorted(settings.TEMPLATE_SELECTOR_HTML_MAP),
        )


class CreateTests(ToolTestCase):
    def test_create_stores_normalized_content(self):
        data = self.data(
            "create_resume",
            {
                "title": "Grace Hopper",
                # Skills as a string is the classic mistake; normalize() is what
                # keeps it from reaching a template that iterates characters.
                "content": {"user_info": {"full_name": "Grace Hopper", "skills": "COBOL"}},
            },
        )
        resume = Resume.objects.get(pk=data["id"])
        self.assertEqual(resume.content["user_info"]["skills"], ["COBOL"])
        self.assertIn("preview_url", data)

    def test_create_respects_the_resume_limit(self):
        limit = settings.FREE_TIER_LIMITS["resume_count"]
        for i in range(limit):
            Resume.objects.create(user=self.user, title=f"R{i}")
        result = self.result(
            "create_resume", {"title": "One more", "content": {"user_info": {}}}
        )
        self.assertTrue(result["isError"])
        self.assertIn("already holds", result["content"][0]["text"])

    def test_create_rejects_an_unknown_template(self):
        result = self.result(
            "create_resume",
            {"title": "X", "content": {"user_info": {}}, "template": "not-a-template"},
        )
        self.assertTrue(result["isError"])
        self.assertIn("Unknown template", result["content"][0]["text"])

    def test_create_normalizes_a_spelled_out_language(self):
        data = self.data(
            "create_resume",
            {"title": "TR", "content": {"user_info": {}}, "language": "Turkish"},
        )
        self.assertEqual(Resume.objects.get(pk=data["id"]).language, "tr")


class UpdateTests(ToolTestCase):
    def test_update_records_a_restore_point_of_the_previous_state(self):
        self.data(
            "update_resume",
            {
                "resume_id": self.resume.pk,
                "content": {"user_info": {"full_name": "Ada King"}},
                "summary": "Married name",
            },
        )
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["user_info"]["full_name"], "Ada King")

        revision = self.resume.revisions.first()
        # The revision holds what it looked like BEFORE, which is the whole
        # point of taking it before the write.
        self.assertEqual(revision.content["user_info"]["full_name"], "Ada Lovelace")
        self.assertEqual(revision.source, ResumeRevision.SOURCE_MCP)
        self.assertEqual(revision.summary, "Married name")

    def test_update_cannot_reach_another_users_resume(self):
        theirs = Resume.objects.create(
            user=self.other, title="Theirs", content={"user_info": {"full_name": "X"}}
        )
        result = self.result(
            "update_resume", {"resume_id": theirs.pk, "content": {"user_info": {}}}
        )
        self.assertTrue(result["isError"])
        theirs.refresh_from_db()
        self.assertEqual(theirs.content["user_info"]["full_name"], "X")

    def test_update_can_rename(self):
        self.data(
            "update_resume",
            {
                "resume_id": self.resume.pk,
                "content": {"user_info": {}},
                "title": "Renamed",
            },
        )
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.title, "Renamed")

    def test_content_of_the_wrong_type_is_a_tool_error(self):
        result = self.result(
            "update_resume", {"resume_id": self.resume.pk, "content": "a resume"}
        )
        self.assertTrue(result["isError"])


class TemplateTests(ToolTestCase):
    def test_set_template_changes_it_and_keeps_a_restore_point(self):
        self.data(
            "set_template",
            {"resume_id": self.resume.pk, "template": "modern-sidebar"},
        )
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.template_selector, "modern-sidebar")
        self.assertEqual(self.resume.revisions.count(), 1)

    def test_setting_the_template_it_already_has_writes_nothing(self):
        result = self.result(
            "set_template",
            {"resume_id": self.resume.pk, "template": self.resume.template_selector},
        )
        self.assertIn("already uses", result["content"][0]["text"])
        self.assertEqual(self.resume.revisions.count(), 0)


class RenderTests(ToolTestCase):
    def test_render_pdf_returns_a_single_use_link(self):
        data = self.data("render_pdf", {"resume_id": self.resume.pk})
        self.assertTrue(data["single_use"])
        self.assertGreater(data["expires_in_seconds"], 0)
        self.assertIn("/d/", data["download_url"])

    def test_the_link_actually_renders_a_pdf_and_then_is_spent(self):
        url = self.data("render_pdf", {"resume_id": self.resume.pk})["download_url"]
        path = url.split("testserver", 1)[1]

        with patch("resume.views.resume_pdf_service.generate_resume_pdf") as render:
            render.return_value = b"%PDF-1.7 fake"
            first = self.client.get(path)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first["Content-Type"], "application/pdf")

        second = self.client.get(path)
        self.assertEqual(second.status_code, 410)

    def test_render_refuses_when_the_download_quota_is_gone(self):
        profile = self.user.profile
        profile.download_count = settings.FREE_TIER_LIMITS["download_count"]
        profile.save()
        result = self.result("render_pdf", {"resume_id": self.resume.pk})
        self.assertTrue(result["isError"])
        self.assertIn("downloads this month", result["content"][0]["text"])

    def test_minting_a_link_does_not_itself_cost_a_download(self):
        # The charge lands when the link is followed, so an unclicked link is
        # free and a failed render costs nothing.
        before = self.user.profile.download_count
        self.data("render_pdf", {"resume_id": self.resume.pk})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.download_count, before)


class SurfaceTests(ToolTestCase):
    def test_the_advertised_surface_is_the_planned_tools(self):
        names = {d["name"] for d in registry.descriptors()}
        self.assertEqual(
            names,
            {
                "list_resumes",
                "get_resume",
                "create_resume",
                "update_resume",
                "set_focus_areas",
                "list_templates",
                "set_template",
                "render_pdf",
                "check_quota",
                "evaluate_posting",
                "list_evaluations",
            },
        )

    def test_every_tool_declares_a_usable_schema(self):
        for descriptor in registry.descriptors():
            schema = descriptor["inputSchema"]
            self.assertEqual(schema["type"], "object", descriptor["name"])
            self.assertTrue(descriptor["description"], descriptor["name"])
            self.assertTrue(descriptor["title"], descriptor["name"])

    def test_nothing_on_the_surface_can_delete(self):
        for descriptor in registry.descriptors():
            self.assertFalse(descriptor["annotations"]["destructiveHint"])

    def test_every_writing_tool_hands_back_a_preview_url(self):
        # The handoff into the app is what makes "edit in Claude, look in
        # ResuStack" work, so it is not optional on any write.
        created = self.data(
            "create_resume", {"title": "New", "content": {"user_info": {}}}
        )
        updated = self.data(
            "update_resume", {"resume_id": self.resume.pk, "content": {"user_info": {}}}
        )
        templated = self.data(
            "set_template", {"resume_id": self.resume.pk, "template": "modern-sidebar"}
        )
        focused = self.data(
            "set_focus_areas", {"resume_id": self.resume.pk, "items": ["Django"]}
        )
        for data in (created, updated, templated, focused):
            self.assertIn("preview_url", data)

    def test_tools_list_advertises_all_of_them(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {self.token.key}"},
        )
        tools = response.json()["result"]["tools"]
        self.assertEqual(len(tools), len(registry.descriptors()))
        self.assertEqual([t["name"] for t in tools], sorted(t["name"] for t in tools))
