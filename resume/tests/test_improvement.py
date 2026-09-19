"""
Improving for an evaluation's gaps: a partial requirement is rewritten where
its evidence is, a missing one needs the person's own words, nothing is saved
before approval, and a stale draft cannot overwrite newer content.
"""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from resume.models import ResumeRevision, Resume
from resume.services import evaluation_service, improvement_service
from resume.services.improvement_service import ImprovementError
from resume.tests.test_evaluation_service import DESCRIBE, NAMES
from resume.tests.test_job_match import POSTING, fake_ask
from resume.typesafe_engine import Answers, ChoiceResult

ASK = "resume.typesafe_engine.ask"
WRITE = "resume.services.improvement_service.send_openai_message"

CONTENT = {
    "user_info": {"full_name": "Ada", "skills": ["Python", "PostgreSQL"]},
    "experience": [
        {"title": "Engineer", "company": "Initech", "start_date": "2021-01", "current_role": True,
         "description": ["Built services in Python", "Tuned PostgreSQL queries"]},
        {"title": "Developer", "company": "Globex", "start_date": "2018-01", "end_date": "2020-12",
         "description": ["Maintained Go tooling scripts"]},
    ],
}


def ask(state, questions, *, purpose, supported=0.9, suggest="j1"):
    """job_match questions go to the shared fake; improvement ones are answered here."""
    if purpose == "improve.suggest_job":
        return Answers(choices={k: ChoiceResult(suggest, 0.9, {}) for k in questions})
    if purpose == "improve.check":
        return Answers(nouls={k: supported for k in questions})
    return fake_ask(state, questions, purpose=purpose)


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("improver", password="x")
        self.resume = Resume.objects.create(user=self.user, title="Main", content=json.loads(json.dumps(CONTENT)))
        with patch(ASK, side_effect=ask), patch(DESCRIBE, return_value=NAMES):
            self.posting = evaluation_service.add_posting(self.user, POSTING)
        # PostgreSQL partial (evidence: Initech bullet 1), Go missing, Python covered
        with patch(ASK, side_effect=ask), \
             patch("resume.tests.test_job_match.LEVEL", {"Python": 3, "PostgreSQL": 1, "Go": 0}):
            self.evaluation, _ = evaluation_service.evaluate(self.resume, self.posting)

    def plan(self, ids=None):
        with patch(ASK, side_effect=ask):
            return improvement_service.plan(self.resume, self.posting, self.evaluation, ids)

    def draft(self, rewrites, answers, bullets, supported=0.9):
        with patch(ASK, side_effect=lambda s, q, purpose: ask(s, q, purpose=purpose, supported=supported)), \
             patch(WRITE, return_value=json.dumps({"bullets": bullets})) as write:
            result = improvement_service.draft(self.user, self.resume, self.posting, rewrites, answers)
        return result, write


class PlanTests(Base):
    def test_a_partial_requirement_is_rewritten_where_its_evidence_is(self):
        plan = self.plan()
        self.assertEqual([(r["label"], r["entry"], r["bullet"]) for r in plan["rewrites"]],
                         [("PostgreSQL", 0, 1)])
        self.assertEqual(plan["rewrites"][0]["bullet_text"], "Tuned PostgreSQL queries")

    def test_a_missing_requirement_becomes_a_question_with_a_suggested_role(self):
        plan = self.plan()
        self.assertEqual([q["label"] for q in plan["questions"]], ["Go"])
        self.assertEqual(plan["questions"][0]["suggested_entry"], 1)
        self.assertEqual([j["label"] for j in plan["questions"][0]["jobs"]],
                         ["Engineer · Initech", "Developer · Globex"])

    def test_only_the_selected_gaps_are_planned(self):
        plan = self.plan(["r5"])
        self.assertEqual(plan["rewrites"], [])
        self.assertEqual(len(plan["questions"]), 1)


class DraftTests(Base):
    def test_the_draft_uses_only_the_bullet_and_the_persons_words(self):
        plan = self.plan()
        result, write = self.draft(
            plan["rewrites"], [{"req_id": "r5", "entry": 1, "fact": "Wrote CLI tools in Go"}],
            {"k0": "Cut report time by tuning PostgreSQL queries", "k1": "Wrote internal CLI tools in Go"},
        )
        sent = json.loads(write.call_args.kwargs["user_message"])
        self.assertEqual(sent["items"][0]["current_bullet"], "Tuned PostgreSQL queries")
        self.assertEqual(sent["items"][1]["what_they_did"], "Wrote CLI tools in Go")
        changes = {c["label"]: c for c in result["changes"]}
        self.assertEqual(changes["PostgreSQL"]["bullet"], 1)
        self.assertTrue(changes["PostgreSQL"]["words"])
        self.assertIsNone(changes["Go"]["bullet"])
        # nothing saved yet
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content, CONTENT)

    def test_a_line_saying_more_than_its_sources_is_flagged(self):
        plan = self.plan(["r3"])
        result, _ = self.draft(plan["rewrites"], [], {"k0": "Tuned PostgreSQL for 40M users"}, supported=0.1)
        self.assertTrue(result["changes"][0]["unsupported"])

    def test_i_did_not_do_this_adds_nothing(self):
        with self.assertRaises(ImprovementError):
            self.draft([], [{"req_id": "r5", "entry": "", "fact": ""}], {})


class ApplyTests(Base):
    def make_draft(self):
        plan = self.plan()
        result, _ = self.draft(
            plan["rewrites"], [{"req_id": "r5", "entry": 1, "fact": "Wrote CLI tools in Go"}],
            {"k0": "Cut report time by tuning PostgreSQL queries", "k1": "Wrote internal CLI tools in Go"},
        )
        return result

    def apply(self, token, accepted):
        with patch(ASK, side_effect=ask):
            return improvement_service.apply(self.user, self.resume, token, accepted)

    def test_accepted_changes_are_written_in_place_with_a_restore_point(self):
        draft = self.make_draft()
        accepted = [{"id": c["id"], "text": c["after"]} for c in draft["changes"]]
        accepted[1]["text"] = "Wrote internal CLI tools in Go (edited)"
        evaluation, previous, posting = self.apply(draft["token"], accepted)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["experience"][0]["description"],
                         ["Built services in Python", "Cut report time by tuning PostgreSQL queries"])
        self.assertEqual(self.resume.content["experience"][1]["description"],
                         ["Maintained Go tooling scripts", "Wrote internal CLI tools in Go (edited)"])
        revision = self.resume.revisions.first()
        self.assertEqual(revision.source, ResumeRevision.SOURCE_AGENT)
        self.assertIn("Improved for Acme", revision.summary)
        self.assertEqual(previous.pk, self.evaluation.pk)

    def test_unticked_changes_are_left_out(self):
        draft = self.make_draft()
        go = next(c for c in draft["changes"] if c["label"] == "Go")
        self.apply(draft["token"], [{"id": go["id"], "text": go["after"]}])
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["experience"][0]["description"][1], "Tuned PostgreSQL queries")

    def test_a_draft_cannot_overwrite_newer_content(self):
        draft = self.make_draft()
        self.resume.content["user_info"]["full_name"] = "Ada L."
        self.resume.save()
        with self.assertRaises(ImprovementError):
            self.apply(draft["token"], [{"id": draft["changes"][0]["id"], "text": "x"}])

    def test_a_draft_is_used_once(self):
        draft = self.make_draft()
        change = draft["changes"][1]
        self.apply(draft["token"], [{"id": change["id"], "text": change["after"]}])
        with self.assertRaises(ImprovementError):
            self.apply(draft["token"], [{"id": change["id"], "text": change["after"]}])


class ViewTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def post(self, name, body, bullets=None):
        with patch(ASK, side_effect=ask), \
             patch(WRITE, return_value=json.dumps({"bullets": bullets or {}})):
            return self.client.post(reverse(name), json.dumps(body), content_type="application/json")

    def test_a_draft_costs_one_enhancement(self):
        before = self.user.profile.enhance_count
        response = self.post("resume:improve_draft", {
            "resume_id": self.resume.pk, "posting_id": self.posting.pk,
            "rewrites": [{"req_id": "r3"}], "answers": [],
        }, {"k0": "Cut report time by tuning PostgreSQL queries"})
        self.assertEqual(response.status_code, 200, response.content)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.enhance_count, before + 1)

    def test_rewrite_targets_come_from_the_evaluation_not_the_client(self):
        response = self.post("resume:improve_draft", {
            "resume_id": self.resume.pk, "posting_id": self.posting.pk,
            "rewrites": [{"req_id": "r3", "entry": 1, "bullet": 0, "bullet_text": "forged"}], "answers": [],
        }, {"k0": "Cut report time by tuning PostgreSQL queries"})
        change = response.json()["changes"][0]
        self.assertEqual((change["entry"], change["bullet"], change["before"]), (0, 1, "Tuned PostgreSQL queries"))

    def test_no_enhancements_left_means_no_draft(self):
        profile = self.user.profile
        profile.enhance_count = improvement_service.enhance_limit()
        profile.save()
        with patch(WRITE) as write:
            response = self.client.post(reverse("resume:improve_draft"), json.dumps({
                "resume_id": self.resume.pk, "posting_id": self.posting.pk, "rewrites": [{"req_id": "r3"}],
            }), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        write.assert_not_called()

    def test_another_users_resume_is_not_reachable(self):
        stranger = User.objects.create_user("stranger", password="x")
        self.client.force_login(stranger)
        response = self.post("resume:improve_plan", {"resume_id": self.resume.pk, "posting_id": self.posting.pk})
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse("resume:improve_apply"), json.dumps(
            {"resume_id": self.resume.pk, "token": "x", "accepted": []}), content_type="application/json")
        self.assertEqual(response.status_code, 404)


class RewriteNoteTests(Base):
    def test_a_note_on_a_rewrite_feeds_that_rewrite_and_adds_no_bullet(self):
        plan = self.plan(["r3"])
        result, write = self.draft(
            plan["rewrites"], [{"req_id": "r3", "entry": None, "fact": "Cut report time from 9s to 1s"}],
            {"k0": "Cut report time from 9s to 1s by tuning PostgreSQL queries"},
        )
        sent = json.loads(write.call_args.kwargs["user_message"])
        self.assertEqual(len(sent["items"]), 1)
        self.assertEqual(sent["items"][0]["what_they_did"], "Cut report time from 9s to 1s")
        self.assertEqual(result["changes"][0]["bullet"], 1)

    def test_a_rewrite_that_only_adds_a_full_stop_is_no_change(self):
        plan = self.plan(["r3"])
        with self.assertRaises(ImprovementError):
            self.draft(plan["rewrites"], [], {"k0": "Tuned PostgreSQL queries."})
