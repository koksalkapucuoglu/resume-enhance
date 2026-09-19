"""
Postings parsed once, evaluations cached by content, job branches and
replacing the base with one. Jev and OpenAI are mocked.
"""

import json
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from resume.models import Evaluation, JobPosting, Resume, ResumeRevision
from resume.services import evaluation_service
from resume.services.evaluation_service import EvaluationError
from resume.tests.test_job_match import CONTENT, POSTING, fake_ask

ASK = "resume.services.job_match.typesafe_engine.ask"
DESCRIBE = "resume.services.evaluation_service.send_openai_message"
NAMES = json.dumps({"title": "Backend Engineer", "company": "Acme",
                    "labels": {"r2": "Python", "r3": "PostgreSQL", "r5": "Go"}})


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("evaluator", password="x")
        self.resume = Resume.objects.create(user=self.user, title="Main", content=json.loads(json.dumps(CONTENT)))

    def add(self, text=POSTING):
        with patch(ASK, side_effect=fake_ask), patch(DESCRIBE, return_value=NAMES):
            return evaluation_service.add_posting(self.user, text)

    def evaluate(self, resume=None, posting=None):
        with patch(ASK, side_effect=fake_ask) as ask:
            result = evaluation_service.evaluate(resume or self.resume, posting or self.posting)
        return result, ask


class PostingTests(Base):
    def test_requirements_are_parsed_once_and_named(self):
        posting = self.add()
        self.assertEqual([r["text"] for r in posting.requirements], ["Python", "PostgreSQL", "Go"])
        self.assertEqual(posting.requirements[0]["label"], "Python")
        self.assertEqual((posting.title, posting.company), ("Backend Engineer", "Acme"))

    def test_the_same_posting_pasted_again_is_the_same_record(self):
        first = self.add()
        with patch(ASK) as ask:
            again = evaluation_service.add_posting(self.user, "  " + POSTING.replace("\n", "\n\n") + "  ")
        self.assertEqual(first.pk, again.pk)
        ask.assert_not_called()

    def test_without_jev_there_is_no_posting(self):
        with patch(ASK, return_value=None), self.assertRaises(EvaluationError):
            evaluation_service.add_posting(self.user, POSTING)
        self.assertFalse(JobPosting.objects.exists())

    def test_a_short_text_is_refused(self):
        with self.assertRaises(EvaluationError):
            evaluation_service.add_posting(self.user, "Python dev")

    def test_unusable_names_fall_back_to_the_text(self):
        with patch(ASK, side_effect=fake_ask), patch(DESCRIBE, return_value="OpenAI API error"):
            posting = evaluation_service.add_posting(self.user, POSTING)
        self.assertEqual(posting.title, "Backend Engineer at Acme")
        self.assertEqual(posting.requirements[1]["label"], "PostgreSQL")


class EvaluationTests(Base):
    def setUp(self):
        super().setUp()
        self.posting = self.add()

    def test_rows_point_at_where_the_evidence_lives(self):
        (evaluation, previous), _ = self.evaluate()
        self.assertIsNone(previous)
        rows = {r["id"]: r for r in evaluation.rows}
        # "Tuned PostgreSQL queries" is bullet 1 of experience 0
        self.assertEqual(rows["r3"]["evidence_at"], {"section": "experience", "entry": 0, "bullet": 1})
        self.assertIsNone(rows["r5"]["evidence_at"])

    def test_unchanged_content_is_not_measured_again(self):
        (first, _), _ = self.evaluate()
        (second, _), ask = self.evaluate()
        self.assertEqual(first.pk, second.pk)
        ask.assert_not_called()

    def test_an_edit_is_measured_and_the_change_is_shown_row_by_row(self):
        (first, _), _ = self.evaluate()
        self.resume.content["experience"][0]["description"].append("Wrote Go services")
        self.resume.save()
        with patch("resume.tests.test_job_match.LEVEL", {"Python": 3, "PostgreSQL": 2, "Go": 2}), \
             patch("resume.tests.test_job_match.LINE", {"Python": "e1", "PostgreSQL": "e2", "Go": "e3"}):
            (second, previous), ask = self.evaluate()
        ask.assert_called_once()
        self.assertEqual(previous.pk, first.pk)
        self.assertGreater(second.score, first.score)
        moved = evaluation_service.changes(self.posting, previous, second)
        self.assertEqual(moved, [{"id": "r5", "label": "Go", "before": "missing", "after": "covered"}])

    def test_only_the_last_ten_are_kept(self):
        for i in range(evaluation_service.KEEP_EVALUATIONS + 2):
            self.resume.content["user_info"]["full_name"] = f"Ada {i}"
            self.resume.save()
            self.evaluate()
        self.assertEqual(Evaluation.objects.filter(resume=self.resume).count(),
                         evaluation_service.KEEP_EVALUATIONS)

    def test_the_table_puts_required_gaps_first(self):
        (evaluation, _), _ = self.evaluate()
        table = evaluation_service.table(self.posting, evaluation)
        self.assertEqual([(r["text"], r["required"], r["status"]) for r in table], [
            ("Python", True, "covered"), ("PostgreSQL", True, "covered"), ("Go", False, "missing"),
        ])


class BranchTests(Base):
    def setUp(self):
        super().setUp()
        self.posting = self.add()

    def test_a_branch_is_a_copy_of_the_base_for_one_posting(self):
        branch = evaluation_service.create_branch(self.resume, self.posting)
        self.assertTrue(branch.is_job_branch)
        self.assertEqual(branch.derived_from, self.resume)
        self.assertEqual(branch.job_posting, self.posting)
        self.assertEqual(branch.content, self.resume.content)
        self.assertEqual(branch.title, "Main › Acme")
        # asking again, even from the branch itself, gives the same branch
        self.assertEqual(evaluation_service.create_branch(branch, self.posting).pk, branch.pk)

    def test_branches_cost_no_resume_slot_but_the_free_plan_keeps_a_few(self):
        limit = settings.FREE_TIER_LIMITS["job_branch_count"]
        for i in range(limit):
            other = self.add(POSTING + f"\nReference {i}")
            evaluation_service.create_branch(self.resume, other)
        self.assertTrue(self.user.profile.can_create_resume())
        with self.assertRaises(EvaluationError):
            evaluation_service.create_branch(self.resume, self.posting)
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.assertTrue(evaluation_service.create_branch(self.resume, self.posting).pk)

    def test_improving_a_branch_leaves_the_base_and_its_score_alone(self):
        branch = evaluation_service.create_branch(self.resume, self.posting)
        (base_eval, _), _ = self.evaluate(self.resume)
        branch.content["experience"][0]["description"].append("Wrote Go services")
        branch.save()
        self.resume.refresh_from_db()
        self.assertNotIn("Wrote Go services", self.resume.content["experience"][0]["description"])
        (again, _), ask = self.evaluate(self.resume)
        self.assertEqual(again.pk, base_eval.pk)
        ask.assert_not_called()

    def test_promoting_replaces_the_base_and_leaves_a_restore_point(self):
        branch = evaluation_service.create_branch(self.resume, self.posting)
        branch.content["experience"][0]["description"].append("Wrote Go services")
        branch.save()
        base = evaluation_service.promote(branch)
        self.assertEqual(base.content, branch.content)
        revision = base.revisions.first()
        self.assertEqual(revision.source, ResumeRevision.SOURCE_BRANCH)
        self.assertNotIn("Wrote Go services", revision.content["experience"][0]["description"])
        self.assertIn("Main › Acme", revision.summary)
        self.assertTrue(Resume.objects.filter(pk=branch.pk).exists())

    def test_only_a_branch_can_be_promoted(self):
        with self.assertRaises(ValueError):
            evaluation_service.promote(self.resume)

    def test_deleting_the_base_removes_its_branches_and_evaluations(self):
        branch = evaluation_service.create_branch(self.resume, self.posting)
        self.evaluate(branch)
        self.resume.delete()
        self.assertFalse(Resume.objects.filter(pk=branch.pk).exists())
        self.assertFalse(Evaluation.objects.exists())
        self.assertTrue(JobPosting.objects.filter(pk=self.posting.pk).exists())
