"""
The application tools over MCP: match_job measures without writing prose,
the rest follow an application through. Jev is mocked with the job_match
test double; OpenAI must never be called from here.
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token

from mcp_server.tests.test_tools import ToolTestCase
from resume.models import JobPosting
from resume.tests.test_job_match import CONTENT, POSTING, fake_ask

ASK = "resume.services.job_match.typesafe_engine.ask"
OPENAI = "resume.services.job_match.send_openai_message"


class MatchJobTests(ToolTestCase):
    def setUp(self):
        super().setUp()
        self.resume.content = CONTENT
        self.resume.save()

    def match(self, **extra):
        arguments = {"resume_id": self.resume.pk, "posting": POSTING,
                     "title": "Backend Engineer", "company": "Acme", **extra}
        with patch(ASK, side_effect=fake_ask), patch(OPENAI) as openai:
            result = self.result("match_job", arguments)
        openai.assert_not_called()
        return result

    def test_the_requirement_table_comes_back_and_the_application_is_tracked(self):
        result = self.match(url="https://jobs.example.com/42")
        self.assertFalse(result["isError"], result["content"][0]["text"])
        data = result["structuredContent"]
        self.assertEqual(data["score"], 66)
        self.assertEqual(data["title"], "Backend Engineer")
        self.assertEqual(data["scoring"], "jev-1")
        self.assertTrue(data["ai_instructions_ignored"])
        by_text = {r["requirement"]: r for r in data["requirements"]}
        self.assertEqual(by_text["Python"]["status"], "covered")
        self.assertFalse(by_text["Go"]["required"])
        self.assertIn("scores 66/100", result["content"][0]["text"])
        posting = JobPosting.objects.get(pk=data["job_id"])
        self.assertEqual(posting.company, "Acme")
        self.assertEqual(posting.url, "https://jobs.example.com/42")

    def test_the_same_posting_again_remeasures_the_same_application(self):
        first = self.match()["structuredContent"]
        second = self.match()["structuredContent"]
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(second["previous_score"], 66)
        self.assertEqual(JobPosting.objects.filter(user=self.user).count(), 1)

    def test_a_similar_posting_asks_instead_of_guessing(self):
        self.match()
        other_text = POSTING + "\nApply by Friday."
        with patch(ASK, side_effect=fake_ask):
            data = self.data("match_job", {"resume_id": self.resume.pk, "posting": other_text,
                                           "title": "Backend Engineer", "company": "Acme"})
        self.assertTrue(data["needs_choice"])
        with patch(ASK, side_effect=fake_ask):
            data = self.data("match_job", {"resume_id": self.resume.pk, "posting": other_text,
                                           "title": "Backend Engineer", "company": "Acme",
                                           "apply_to": "new"})
        self.assertTrue(data["is_new_application"])
        self.assertEqual(JobPosting.objects.filter(user=self.user).count(), 2)

    def test_the_free_limit_is_enforced(self):
        for i in range(3):
            JobPosting.objects.create(user=self.user, title=f"Old {i}")
        with patch(ASK, side_effect=fake_ask):
            result = self.result("match_job", {"resume_id": self.resume.pk, "posting": POSTING,
                                               "title": "New", "company": "Initech"})
        self.assertTrue(result["isError"])
        self.assertIn("free limit", result["content"][0]["text"])

    def test_an_unconfirmed_email_cannot_use_it(self):
        self.user.profile.email_verification_required = True
        self.user.profile.save()
        with patch(ASK) as ask:
            result = self.result("match_job", {"resume_id": self.resume.pk, "posting": POSTING,
                                               "title": "x", "company": "y"})
        self.assertTrue(result["isError"])
        ask.assert_not_called()

    def test_another_users_resume_is_refused(self):
        theirs = self.other.resumes.create(title="Theirs", content=CONTENT)
        with patch(ASK) as ask:
            result = self.result("match_job", {"resume_id": theirs.pk, "posting": POSTING,
                                               "title": "x", "company": "y"})
        self.assertTrue(result["isError"])
        ask.assert_not_called()


class FollowThroughTests(ToolTestCase):
    def setUp(self):
        super().setUp()
        self.job = JobPosting.objects.create(
            user=self.user, title="Backend Engineer", company="Acme", description=POSTING,
            requirements=[{"text": "Python", "must_have": 0.9, "status": "covered",
                           "evidence": "Built services in Python", "uncertain": False}],
            match_score=70, scoring_version="jev-1",
        )
        self.theirs = JobPosting.objects.create(user=self.other, title="Secret role", company="Hidden")

    def test_list_only_shows_this_accounts_applications(self):
        jobs = self.data("list_jobs")["jobs"]
        self.assertEqual([j["job_id"] for j in jobs], [self.job.id])

    def test_list_filters_by_status(self):
        self.assertEqual(self.data("list_jobs", {"status": "interview"})["jobs"], [])

    def test_get_returns_the_posting_and_table(self):
        data = self.data("get_job", {"job_id": self.job.id})
        self.assertEqual(data["posting"], POSTING)
        self.assertEqual(data["requirements"][0]["evidence"], "Built services in Python")

    def test_another_accounts_application_is_not_reachable(self):
        result = self.result("get_job", {"job_id": self.theirs.id})
        self.assertTrue(result["isError"])
        self.assertNotIn("Secret", result["content"][0]["text"])
        self.assertTrue(self.result("update_job", {"job_id": self.theirs.id, "status": "offer"})["isError"])
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.status, "saved")

    def test_update_status_and_the_resume_sent(self):
        data = self.data("update_job", {"job_id": self.job.id, "status": "applied",
                                        "resume_id": self.resume.pk})
        self.assertEqual(data["status"], "applied")
        self.job.refresh_from_db()
        self.assertEqual(self.job.source_resume_id, self.resume.pk)
        self.assertTrue(self.job.has_snapshot)

    def test_a_bad_status_is_refused_by_the_schema_or_the_tool(self):
        result = self.call("update_job", {"job_id": self.job.id, "status": "hired"}).json()
        refused = "error" in result or result["result"]["isError"]
        self.assertTrue(refused)
