"""
Measuring a resume against a posting over MCP. Jev and OpenAI are mocked; the
calls go through the endpoint, so the path a real client takes is under test.
"""

import json
from unittest.mock import patch

from django.conf import settings

from mcp_server.tests.test_tools import ToolTestCase
from resume.models import Evaluation, JobPosting, Resume
from resume.tests.test_evaluation_service import ASK, DESCRIBE, NAMES
from resume.tests.test_job_match import CONTENT, POSTING, fake_ask


class PostingToolTestCase(ToolTestCase):
    def setUp(self):
        super().setUp()
        self.resume.content = json.loads(json.dumps(CONTENT))
        self.resume.save()

    def measure(self, arguments, **kwargs):
        with patch(ASK, side_effect=fake_ask), patch(DESCRIBE, return_value=NAMES):
            return self.data("evaluate_posting", arguments, **kwargs)

    def refused(self, arguments, **kwargs):
        with patch(ASK, side_effect=fake_ask), patch(DESCRIBE, return_value=NAMES):
            result = self.result("evaluate_posting", arguments, **kwargs)
        self.assertTrue(result["isError"], result["content"][0]["text"])
        return result["content"][0]["text"]


class EvaluateTests(PostingToolTestCase):
    def test_measuring_on_a_branch_leaves_the_resume_alone(self):
        data = self.measure(
            {"resume_id": self.resume.pk, "posting": POSTING, "target": "branch"}
        )
        branch = Resume.objects.get(derived_kind=Resume.DERIVED_JOB)
        self.assertTrue(data["is_job_branch"])
        self.assertEqual(data["evaluated_resume_id"], branch.pk)
        self.assertEqual(data["base_resume_id"], self.resume.pk)
        self.assertEqual(data["score"], 66)
        self.assertEqual((data["required_covered"], data["required_total"]), (2, 2))
        self.assertIn("preview_url", data)
        self.assertEqual(branch.content, self.resume.content)

    def test_the_rows_carry_status_and_the_evidence_line(self):
        data = self.measure(
            {"resume_id": self.resume.pk, "posting": POSTING, "target": "base"}
        )
        rows = {r["requirement"]: r for r in data["requirements"]}
        self.assertEqual(rows["PostgreSQL"]["status"], "covered")
        self.assertIn("Tuned PostgreSQL queries", rows["PostgreSQL"]["evidence"])
        self.assertTrue(rows["PostgreSQL"]["required"])
        self.assertEqual(rows["Go"]["status"], "missing")
        self.assertFalse(rows["Go"]["required"])
        self.assertFalse(Resume.objects.filter(derived_kind=Resume.DERIVED_JOB).exists())

    def test_the_sentence_names_the_gaps_and_advises_nothing(self):
        with patch(ASK, side_effect=fake_ask), patch(DESCRIBE, return_value=NAMES):
            result = self.result(
                "evaluate_posting",
                {"resume_id": self.resume.pk, "posting": POSTING, "target": "base"},
            )
        text = result["content"][0]["text"]
        self.assertIn("66/100", text)
        self.assertIn("Not fully shown: Go.", text)

    def test_the_same_posting_again_goes_to_its_branch_and_is_not_re_measured(self):
        first = self.measure(
            {"resume_id": self.resume.pk, "posting": POSTING, "target": "branch"}
        )
        # target is ignored once the posting has a branch: work stays in one place
        again = self.measure(
            {"resume_id": self.resume.pk, "posting": "  " + POSTING + "\n", "target": "base"}
        )
        self.assertEqual(again["posting_id"], first["posting_id"])
        self.assertEqual(again["evaluated_resume_id"], first["evaluated_resume_id"])
        self.assertEqual(JobPosting.objects.count(), 1)
        self.assertEqual(Evaluation.objects.count(), 1)

    def test_a_posting_too_short_to_read_is_a_tool_error(self):
        message = self.refused(
            {"resume_id": self.resume.pk, "posting": "Python dev", "target": "base"}
        )
        self.assertIn("posting", message.lower())
        self.assertFalse(JobPosting.objects.exists())

    def test_without_jev_nothing_is_stored(self):
        with patch(ASK, return_value=None), patch(DESCRIBE, return_value=NAMES):
            result = self.result(
                "evaluate_posting",
                {"resume_id": self.resume.pk, "posting": POSTING, "target": "base"},
            )
        self.assertTrue(result["isError"])
        self.assertFalse(JobPosting.objects.exists())
        self.assertFalse(Evaluation.objects.exists())

    def test_the_free_plans_branch_limit_is_reported_not_crashed(self):
        for i in range(settings.FREE_TIER_LIMITS["job_branch_count"]):
            self.measure({"resume_id": self.resume.pk,
                          "posting": POSTING + f"\nReference {i}", "target": "branch"})
        message = self.refused(
            {"resume_id": self.resume.pk, "posting": POSTING, "target": "branch"}
        )
        self.assertIn("branches", message)

    def test_branches_do_not_eat_resume_slots_in_check_quota(self):
        self.measure({"resume_id": self.resume.pk, "posting": POSTING, "target": "branch"})
        quota = self.data("check_quota")
        self.assertEqual(quota["resumes_left"], settings.FREE_TIER_LIMITS["resume_count"] - 1)
        self.assertEqual(quota["job_branches_left"],
                         settings.FREE_TIER_LIMITS["job_branch_count"] - 1)

    def test_another_users_resume_cannot_be_measured(self):
        from rest_framework.authtoken.models import Token

        token = Token.objects.create(user=self.other)
        self.refused(
            {"resume_id": self.resume.pk, "posting": POSTING, "target": "branch"},
            user_token=token.key,
        )
        self.assertFalse(JobPosting.objects.filter(user=self.user).exists())


class ListEvaluationsTests(PostingToolTestCase):
    def test_an_unmeasured_resume_says_so(self):
        data = self.data("list_evaluations", {"resume_id": self.resume.pk})
        self.assertEqual(data["evaluations"], [])

    def test_the_family_is_listed_and_an_edit_marks_it_stale(self):
        self.measure({"resume_id": self.resume.pk, "posting": POSTING, "target": "branch"})
        branch = Resume.objects.get(derived_kind=Resume.DERIVED_JOB)
        listed = self.data("list_evaluations", {"resume_id": self.resume.pk})["evaluations"]
        self.assertEqual(
            [(e["resume_id"], e["score"], e["stale"]) for e in listed],
            [(branch.pk, 66, False)],
        )

        branch.content["experience"][0]["description"].append("Wrote Go services")
        branch.save()
        # asking from the branch gives the same family list
        listed = self.data("list_evaluations", {"resume_id": branch.pk})["evaluations"]
        self.assertTrue(listed[0]["stale"])
