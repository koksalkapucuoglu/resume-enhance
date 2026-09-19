"""
The dashboard's evaluation endpoints and the assistant's tools on top of the
same service. Jev and OpenAI are mocked.
"""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from resume.models import Evaluation, JobPosting, Resume
from resume.services import agent_loop, agent_tools
from resume.tests.test_evaluation_service import ASK, DESCRIBE, NAMES
from resume.tests.test_job_match import CONTENT, POSTING, fake_ask


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("viewer", password="x")
        self.resume = Resume.objects.create(user=self.user, title="Main", content=json.loads(json.dumps(CONTENT)))
        self.client.force_login(self.user)

    def post(self, name, body, args=None):
        with patch(ASK, side_effect=fake_ask), patch(DESCRIBE, return_value=NAMES):
            return self.client.post(reverse(name, args=args), json.dumps(body),
                                    content_type="application/json")

    def add(self):
        return self.post("resume:add_job_posting", {"text": POSTING, "resume_id": self.resume.pk}).json()


class AddAndEvaluateTests(Base):
    def test_adding_asks_where_when_there_is_no_branch_yet(self):
        body = self.add()
        self.assertEqual(body["posting_label"], "Backend Engineer · Acme")
        self.assertEqual(body["base"], {"id": self.resume.pk, "name": "Main"})
        self.assertIsNone(body["branch"])

    def test_evaluating_on_a_branch_creates_it_and_returns_the_panel(self):
        posting_id = self.add()["posting_id"]
        panel = self.post("resume:evaluate_job_posting",
                          {"posting_id": posting_id, "resume_id": self.resume.pk, "target": "branch"}).json()
        self.assertEqual(panel["type"], "evaluation")
        self.assertTrue(panel["resume"]["is_branch"])
        self.assertEqual(panel["resume"]["base_id"], self.resume.pk)
        self.assertEqual(panel["score"], 66)
        self.assertEqual(panel["required_covered"], 2)
        # a second paste of the same posting goes straight to that branch
        self.assertEqual(self.add()["branch"]["id"], panel["resume"]["id"])

    def test_evaluating_on_the_base_leaves_no_branch(self):
        posting_id = self.add()["posting_id"]
        panel = self.post("resume:evaluate_job_posting",
                          {"posting_id": posting_id, "resume_id": self.resume.pk, "target": "base"}).json()
        self.assertFalse(panel["resume"]["is_branch"])
        self.assertFalse(Resume.objects.filter(derived_kind=Resume.DERIVED_JOB).exists())

    def test_the_family_list_and_a_re_measure_after_an_edit(self):
        posting_id = self.add()["posting_id"]
        panel = self.post("resume:evaluate_job_posting",
                          {"posting_id": posting_id, "resume_id": self.resume.pk, "target": "branch"}).json()
        branch = Resume.objects.get(pk=panel["resume"]["id"])
        listed = self.client.get(reverse("resume:resume_evaluations", args=[self.resume.pk])).json()
        self.assertEqual([(e["resume_id"], e["score"], e["stale"]) for e in listed["evaluations"]],
                         [(branch.pk, 66, False)])

        branch.content["experience"][0]["description"].append("Wrote Go services")
        branch.save()
        with patch(ASK, side_effect=fake_ask), \
             patch("resume.tests.test_job_match.LEVEL", {"Python": 3, "PostgreSQL": 2, "Go": 3}), \
             patch("resume.tests.test_job_match.LINE", {"Python": "e1", "PostgreSQL": "e2", "Go": "e3"}):
            again = self.client.get(reverse("resume:resume_evaluation", args=[branch.pk, posting_id])).json()
        self.assertEqual(again["previous_score"], 66)
        self.assertEqual(again["changes"], [{"id": "r5", "label": "Go", "before": "missing", "after": "covered"}])

    def test_promoting_from_the_panel(self):
        posting_id = self.add()["posting_id"]
        panel = self.post("resume:evaluate_job_posting",
                          {"posting_id": posting_id, "resume_id": self.resume.pk, "target": "branch"}).json()
        branch = Resume.objects.get(pk=panel["resume"]["id"])
        branch.content["user_info"]["full_name"] = "Ada L."
        branch.save()
        body = self.client.post(reverse("resume:promote_job_branch", args=[branch.pk])).json()
        self.assertEqual(body["base"]["id"], self.resume.pk)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["user_info"]["full_name"], "Ada L.")

    def test_a_base_resume_cannot_be_promoted(self):
        response = self.client.post(reverse("resume:promote_job_branch", args=[self.resume.pk]))
        self.assertEqual(response.status_code, 400)


class SecurityTests(Base):
    def test_another_users_resume_and_posting_are_not_reachable(self):
        posting_id = self.add()["posting_id"]
        stranger = User.objects.create_user("stranger", password="x")
        self.client.force_login(stranger)
        theirs = Resume.objects.create(user=stranger, title="Theirs", content=CONTENT)
        response = self.post("resume:evaluate_job_posting",
                             {"posting_id": posting_id, "resume_id": theirs.pk, "target": "base"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.get(reverse("resume:resume_evaluations", args=[self.resume.pk])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse("resume:promote_job_branch", args=[self.resume.pk])).status_code, 400)

    def test_an_unverified_email_cannot_evaluate(self):
        self.user.profile.email_verification_required = True
        self.user.profile.save()
        with patch(ASK) as ask:
            response = self.client.post(reverse("resume:add_job_posting"),
                                        json.dumps({"text": POSTING, "resume_id": self.resume.pk}),
                                        content_type="application/json")
        self.assertEqual(response.status_code, 403)
        ask.assert_not_called()


class AssistantToolTests(Base):
    def ctx(self, resume=None):
        return {"lang": "en", "active_resume": resume or self.resume}

    def test_without_a_target_the_user_is_asked_with_a_card(self):
        with patch(ASK, side_effect=fake_ask), patch(DESCRIBE, return_value=NAMES):
            result = agent_tools.get_tool("evaluate_posting").handler(self.user, self.ctx(), posting=POSTING)
        self.assertTrue(result.data["asked_user"])
        self.assertEqual(result.ui[0]["type"], "evaluation_target")
        self.assertFalse(Evaluation.objects.exists())

    def test_with_a_target_it_evaluates(self):
        with patch(ASK, side_effect=fake_ask), patch(DESCRIBE, return_value=NAMES):
            result = agent_tools.get_tool("evaluate_posting").handler(
                self.user, self.ctx(), posting=POSTING, target="branch")
        self.assertTrue(result.data["is_branch"])
        self.assertEqual(result.ui[0]["type"], "evaluation")

    def test_the_active_evaluation_is_in_the_assistants_context(self):
        with patch(ASK, side_effect=fake_ask), patch(DESCRIBE, return_value=NAMES):
            agent_tools.get_tool("evaluate_posting").handler(
                self.user, self.ctx(), posting=POSTING, target="branch")
        branch = Resume.objects.get(derived_kind=Resume.DERIVED_JOB)
        ctx = {"active_resume": branch, "active_posting": branch.job_posting, "resumes": [], "quota": {}}
        block = agent_loop._context_block(ctx)
        self.assertIn("Active job posting: Backend Engineer · Acme", block)
        self.assertIn("job branch for this posting", block)
        self.assertIn("REQUIRED · covered · PostgreSQL", block)
        self.assertIn("closest evidence: experience #0, bullet #1", block)

    def test_the_view_passes_the_open_posting_for_a_base_resume(self):
        from django.test import RequestFactory

        from resume.views import _agent_context

        posting = JobPosting.objects.create(user=self.user, text="x" * 50, fingerprint="f")
        request = RequestFactory().post("/")
        request.user = self.user
        self.assertEqual(_agent_context(request, self.resume, posting_id=posting.pk)["active_posting"], posting)
        stranger = User.objects.create_user("s2", password="x")
        request.user = stranger
        self.assertIsNone(_agent_context(request, None, posting_id=posting.pk)["active_posting"])
