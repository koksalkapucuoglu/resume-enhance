"""
Server logs hold technical information, never user content.

The privacy policy says so. The AI responses parsed here describe a person's
resume, so a failure path that logs even an
excerpt of them would put personal data into the logs.
"""

from django.test import TestCase

SECRET = "Ada Lovelace, ada@example.com, salary expectation 95k"


class ModifyResumeLogsTests(TestCase):
    def test_an_unparseable_rewrite_is_logged_by_length(self):
        from resume.services.agent_service import AgentService

        with self.assertLogs("resume.services.agent_service", level="WARNING") as captured:
            parsed = AgentService()._validate_modify_result(f"not json: {SECRET}")
        self.assertIsNone(parsed)
        logged = "\n".join(captured.output)
        self.assertIn("chars", logged)
        self.assertNotIn("Ada", logged)
