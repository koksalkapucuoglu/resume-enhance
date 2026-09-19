"""
Server logs hold technical information, never user content.

The privacy policy says so. The AI responses parsed here describe a person's
resume and a job they are applying for, so a failure path that logs even an
excerpt of them would put personal data into the logs.
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from resume.models import Resume
from resume.services import job_service

SECRET = "Ada Lovelace, ada@example.com, salary expectation 95k"

# Both functions return early, before calling the model, for descriptions under
# 40 characters — too short a posting would never reach the logging path.
POSTING = "Senior backend engineer at Acme: Django, PostgreSQL, Celery and AWS."


class JobServiceLogsTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("logs", password="x")
        self.resume = Resume.objects.create(
            user=user, title="CV", content={"user_info": {"full_name": "Ada Lovelace"}}
        )

    def assert_logged_without_content(self, call):
        with patch(
            "resume.services.job_service.send_openai_message",
            return_value=f"not json: {SECRET}",
        ), self.assertLogs("resume.services.job_service", level="WARNING") as captured:
            result = call()
        self.assertIn("error", result)
        logged = "\n".join(captured.output)
        self.assertIn("unparseable JSON", logged)
        self.assertNotIn(SECRET, logged)
        self.assertNotIn("Ada", logged)

    def test_a_failed_match_does_not_log_the_response(self):
        self.assert_logged_without_content(
            lambda: job_service.analyze_snapshot(self.resume.content, POSTING)
        )

    def test_a_failed_tailoring_does_not_log_the_response(self):
        self.assert_logged_without_content(
            lambda: job_service.tailor_content(self.resume, POSTING)
        )


class ModifyResumeLogsTests(TestCase):
    def test_an_unparseable_rewrite_is_logged_by_length(self):
        from resume.services.agent_service import AgentService

        with self.assertLogs("resume.services.agent_service", level="WARNING") as captured:
            parsed = AgentService()._validate_modify_result(f"not json: {SECRET}")
        self.assertIsNone(parsed)
        logged = "\n".join(captured.output)
        self.assertIn("chars", logged)
        self.assertNotIn("Ada", logged)
