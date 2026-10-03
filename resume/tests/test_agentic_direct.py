"""Agentic buttons that run without the model, and the edges between modes."""

import json
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from resume.admin import UserProfileAdmin
from resume.models import Resume, UserProfile
from resume.services import agent_tools


def post_json(client, name, payload):
    return client.post(reverse(name), json.dumps(payload), content_type="application/json")


class DirectActionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content={})
        self.client.force_login(self.user)

    def test_list_and_limits_cost_no_credit_and_call_no_model(self):
        with patch("resume.services.agent_loop.stream_openai_tool_turn") as llm:
            listed = post_json(self.client, "resume:agent_action", {"action": "list_resumes"})
            limits = post_json(self.client, "resume:agent_action", {"action": "check_quota"})
        llm.assert_not_called()
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["effects"][0]["data_type"], "resume_list")
        self.assertEqual(limits.json()["effects"][0]["data_type"], "quota")
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.ai_credits_used(), 0)

    def test_new_resume_and_preview(self):
        created = post_json(self.client, "resume:agent_action", {"action": "create_blank_resume"})
        new_id = created.json()["data"]["resume_id"]
        self.assertTrue(Resume.objects.filter(pk=new_id, user=self.user).exists())
        preview = post_json(self.client, "resume:agent_action",
                            {"action": "preview_resume", "resume_id": new_id})
        self.assertEqual(preview.json()["effects"][0]["type"], "preview")

    def test_the_resume_limit_is_a_403(self):
        for i in range(2):
            Resume.objects.create(user=self.user, title=f"CV {i}", content={})
        response = post_json(self.client, "resume:agent_action", {"action": "create_blank_resume"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()["quota_exceeded"])

    def test_only_listed_actions_run(self):
        response = post_json(self.client, "resume:agent_action",
                             {"action": "delete_resume", "resume_id": self.resume.pk})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Resume.objects.filter(pk=self.resume.pk).exists())

    def test_another_users_resume_is_not_reachable(self):
        eve = User.objects.create_user("eve", password="x")
        theirs = Resume.objects.create(user=eve, title="Eve", content={})
        response = post_json(self.client, "resume:agent_action",
                             {"action": "preview_resume", "resume_id": theirs.pk})
        self.assertEqual(response.status_code, 404)

    def test_unverified_accounts_can_still_list(self):
        self.user.profile.email_verification_required = True
        self.user.profile.save()
        response = post_json(self.client, "resume:agent_action", {"action": "list_resumes"})
        self.assertEqual(response.status_code, 200)


class ModeEdgesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content={})
        self.client.force_login(self.user)

    def test_entering_agentic_asks_for_a_fresh_conversation(self):
        body = post_json(self.client, "resume:toggle_agent_mode",
                         {"mode": "agentic", "resume_id": self.resume.pk}).json()
        self.assertIn(f"resume={self.resume.pk}", body["redirect_url"])
        self.assertIn("fresh=1", body["redirect_url"])
        plain = post_json(self.client, "resume:toggle_agent_mode", {"mode": "agentic"}).json()
        self.assertTrue(plain["redirect_url"].endswith("?fresh=1"))

    def test_leaving_agentic_does_not(self):
        body = post_json(self.client, "resume:toggle_agent_mode", {"mode": "standard"}).json()
        self.assertNotIn("fresh", body["redirect_url"])

    def test_the_editor_has_no_mode_toggle(self):
        html = self.client.get(reverse("resume:resume_form_edit", args=[self.resume.pk])).content.decode()
        self.assertNotIn('id="btn-standard"', html)
        self.assertIn("Ask AI", html)

    def test_agentic_shows_server_notices_in_the_chat(self):
        """A notice set by a redirecting view must not wait for the standard dashboard."""
        self.user.profile.ui_mode = "agentic"
        self.user.profile.save()
        other = Resume.objects.create(user=self.user, title="Old CV", content={})
        page = self.client.post(reverse("resume:delete_resume", args=[other.pk]), follow=True)
        html = page.content.decode()
        start = html.index("const FLASH_MESSAGES")
        self.assertIn("deleted", html[start:start + 400])

    def test_template_change_needs_no_approval(self):
        self.assertFalse(agent_tools.get_tool("switch_template").destructive)


class VerificationSyncTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", email="ada@example.com", password="x")
        self.user.profile.email_verification_required = True
        self.user.profile.save()

    def test_a_verified_allauth_address_unlocks_ai(self):
        EmailAddress.objects.create(user=self.user, email="ADA@example.com", verified=True, primary=True)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.email_verification_required)

    def test_an_unverified_or_other_address_does_not(self):
        EmailAddress.objects.create(user=self.user, email="ada@example.com", verified=False)
        EmailAddress.objects.create(user=self.user, email="other@example.com", verified=True)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.email_verification_required)

    def test_admin_action_marks_verified(self):
        admin = UserProfileAdmin(UserProfile, AdminSite())
        request = RequestFactory().post("/")
        request._messages = type("M", (), {"add": lambda *a, **k: None})()
        admin.mark_email_verified(request, UserProfile.objects.filter(pk=self.user.profile.pk))
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.email_verification_required)
