"""
The "What I'm working on" path over MCP: a tool that merges one section, and a
prompt that tells the client's model how to draft it.
"""

from mcp_server import protocol
from mcp_server.tests.test_endpoint import McpTestCase
from mcp_server.tests.test_tools import ToolTestCase
from resume.models import ResumeRevision


class SetFocusAreasTests(ToolTestCase):
    def test_only_the_section_is_written(self):
        """The point of the tool: a model that never read the resume cannot wipe it."""
        self.data(
            "set_focus_areas",
            {"resume_id": self.resume.pk, "items": ["Django backends", "AWS"]},
        )
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["user_info"]["full_name"], "Ada Lovelace")
        self.assertEqual(self.resume.content["user_info"]["skills"], ["Python"])
        self.assertEqual(
            self.resume.content["focus_areas"],
            {"include": True, "items": ["Django backends", "AWS"]},
        )

    def test_include_false_stores_without_printing(self):
        data = self.data(
            "set_focus_areas",
            {"resume_id": self.resume.pk, "items": ["Django"], "include": False},
        )
        self.assertEqual(data["focus_areas"], {"include": False, "items": ["Django"]})

    def test_a_restore_point_is_taken_first(self):
        self.data("set_focus_areas", {"resume_id": self.resume.pk, "items": ["Django"]})
        revision = self.resume.revisions.first()
        self.assertEqual(revision.source, ResumeRevision.SOURCE_MCP)
        self.assertNotIn("focus_areas", revision.content)

    def test_it_hands_back_a_preview_url(self):
        data = self.data("set_focus_areas", {"resume_id": self.resume.pk, "items": ["X"]})
        self.assertIn(f"/resume/{self.resume.pk}/preview/", data["preview_url"])

    def test_another_accounts_resume_is_refused(self):
        foreign = self.resume.__class__.objects.create(
            user=self.other, title="Not yours", content={}
        )
        result = self.result("set_focus_areas", {"resume_id": foreign.pk, "items": ["X"]})
        self.assertTrue(result["isError"])
        foreign.refresh_from_db()
        self.assertNotIn("focus_areas", foreign.content)

    def test_limits_are_enforced(self):
        too_many = self.result(
            "set_focus_areas", {"resume_id": self.resume.pk, "items": ["x"] * 9}
        )
        self.assertTrue(too_many["isError"])
        too_long = self.result(
            "set_focus_areas", {"resume_id": self.resume.pk, "items": ["x" * 201]}
        )
        self.assertTrue(too_long["isError"])
        not_strings = self.result(
            "set_focus_areas", {"resume_id": self.resume.pk, "items": [1, 2]}
        )
        self.assertTrue(not_strings["isError"])


class PromptTests(McpTestCase):
    def test_capabilities_announce_prompts(self):
        result = self.legacy("initialize", {"protocolVersion": protocol.LATEST_LEGACY}).json()["result"]
        self.assertIn("prompts", result["capabilities"])

    def test_prompts_list_offers_the_focus_areas_prompt(self):
        prompts = self.legacy("prompts/list").json()["result"]["prompts"]
        entry = next(p for p in prompts if p["name"] == "focus_areas_from_my_work")
        self.assertEqual(
            {a["name"] for a in entry["arguments"]}, {"resume_id", "period"}
        )

    def test_prompts_get_renders_the_arguments(self):
        result = self.legacy(
            "prompts/get",
            {"name": "focus_areas_from_my_work", "arguments": {"resume_id": "42", "period": "the last quarter"}},
        ).json()["result"]
        text = result["messages"][0]["content"]["text"]
        self.assertEqual(result["messages"][0]["role"], "user")
        self.assertIn("resume 42", text)
        self.assertIn("the last quarter", text)
        # The safety steps must survive any rewording of the prompt.
        self.assertIn("approval", text)
        self.assertIn("set_focus_areas", text)

    def test_without_a_resume_id_the_model_is_told_to_ask(self):
        text = self.legacy(
            "prompts/get", {"name": "focus_areas_from_my_work"}
        ).json()["result"]["messages"][0]["content"]["text"]
        self.assertIn("list_resumes", text)

    def test_an_unknown_prompt_is_invalid_params(self):
        response = self.legacy("prompts/get", {"name": "nope"})
        self.assertEqual(response.json()["error"]["code"], protocol.INVALID_PARAMS)

    def test_modern_clients_must_name_the_prompt_in_the_header(self):
        ok = self.modern(
            "prompts/get",
            {"name": "focus_areas_from_my_work"},
            headers={"Mcp-Name": "focus_areas_from_my_work"},
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["result"]["resultType"], "complete")

        missing = self.modern("prompts/get", {"name": "focus_areas_from_my_work"})
        self.assertEqual(missing.json()["error"]["code"], protocol.HEADER_MISMATCH)
