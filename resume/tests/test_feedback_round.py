"""Fixes from the agentic feedback round: improve with no gaps, per-step history, inline bullets."""

import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from resume.models import Evaluation, JobPosting, Resume
from resume.services import agent_tools, evaluation_service, job_match, revision_service


def content(bullets):
    return {
        "user_info": {"full_name": "Ada", "skills": ["SQL"]},
        "experience": [{"title": "Engineer", "company": "Acme", "description": bullets}],
        "education": [], "projects_and_publications": [],
    }


class ImproveWithNoGapsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content(["Built ETL"]))
        self.posting = JobPosting.objects.create(
            user=self.user, title="ETL Developer", text="t", fingerprint="f",
            requirements=[{"id": "r0", "text": "ETL", "label": "ETL", "kind": "requirement", "must_have": True}],
        )

    def _evaluate(self, status):
        Evaluation.objects.create(
            resume=self.resume, posting=self.posting, scorer=evaluation_service.scorer(),
            content_hash=evaluation_service.content_hash(self.resume.content),
            score=92 if status == "covered" else 40,
            rows=[{"id": "r0", "status": status, "level": 1}],
        )

    def _run(self):
        ctx = {"lang": "tr", "active_resume": self.resume, "active_posting": self.posting}
        return agent_tools.get_tool("improve_for_posting").handler(self.user, ctx)

    def test_all_covered_says_so_and_shows_no_cards(self):
        self._evaluate("covered")
        result = self._run()
        self.assertTrue(result.data["nothing_to_improve"])
        self.assertEqual(result.ui, [])

    def test_gaps_start_the_cards_and_are_named(self):
        self._evaluate("missing")
        result = self._run()
        self.assertEqual(result.ui[0]["type"], "improve_start")
        self.assertEqual(result.data["gaps"][0]["id"], "r0")


class PerStepHistoryTest(TestCase):
    """Each entry shows what that step changed, not everything since."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content(["one"]))

    def _edit(self, bullets):
        revision_service.snapshot(self.resume, source="manual")
        self.resume.content = content(bullets)
        self.resume.save()

    def test_an_older_step_shows_only_its_own_change(self):
        self._edit(["one 2"])            # step 1: adds "2"
        self._edit(["one 2", "AI line"])  # step 2: adds a bullet
        older, newer = sorted(revision_service.history(self.resume), key=lambda r: r.pk)
        body = self.client.get(
            reverse("resume:resume_revision_diff", args=[self.resume.pk, older.pk])
        ).json()
        text = json.dumps(body["changes"], ensure_ascii=False)
        self.assertIn("2", text)
        self.assertNotIn("AI line", text)
        latest = self.client.get(
            reverse("resume:resume_revision_diff", args=[self.resume.pk, newer.pk])
        ).json()
        self.assertIn("AI line", json.dumps(latest["changes"], ensure_ascii=False))


class InlineBulletPostingTest(TestCase):
    PASTED = (
        "Data Engineer at X. What We're Looking For:✅ 1–3 years of experience in banking"
        "✅ Solid knowledge of SQL and database systems✅ Interest in Data Engineering and BI"
        "✅ Hands-on experience with ETL tools and data visualization"
    )

    def test_an_inline_check_mark_list_becomes_separate_lines(self):
        lines = job_match.split_posting(self.PASTED)
        self.assertIn("Solid knowledge of SQL and database systems", lines)
        self.assertGreaterEqual(len(lines), 5)

    def test_middle_dots_inside_a_line_stay(self):
        self.assertIn("Remote · Full-time", job_match.split_posting("Remote · Full-time"))

    def test_a_posting_stored_as_one_long_line_is_parsed_again(self):
        self.assertTrue(job_match.needs_reparse([{"text": self.PASTED}]))
        self.assertFalse(job_match.needs_reparse([{"text": "SQL"}]))


class EditorHeaderTest(TestCase):
    def test_no_extra_dashboard_button_the_logo_goes_home(self):
        user = User.objects.create_user("ada", password="x")
        resume = Resume.objects.create(user=user, title="CV", content={})
        self.client.force_login(user)
        html = self.client.get(reverse("resume:resume_form_edit", args=[resume.pk])).content.decode()
        self.assertNotIn("arrow_back", html)
        self.assertIn("Ask AI", html)
