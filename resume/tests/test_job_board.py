"""
The Applications page: the summary and cards read back stored measurements
(no model calls), status changes are dated, and scoring from the page goes
through the same recorder as the assistant.
"""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from resume.models import JobPosting, Resume
from resume.services import job_board
from resume.tests.test_job_match import CONTENT, POSTING, PROSE, fake_ask


def requirement(text, status, must=0.9, label=None, evidence=""):
    return {"text": text, "label": label or text, "status": status, "must_have": must,
            "evidence": evidence, "uncertain": False}


class BoardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("board", password="x")

    def job(self, **kwargs):
        return JobPosting.objects.create(user=self.user, title=kwargs.pop("title", "Role"), **kwargs)

    def test_gaps_that_recur_across_postings_are_summarised(self):
        self.job(requirements=[requirement("PCI-DSS", "missing"), requirement("Go", "missing", must=0.1)])
        self.job(requirements=[requirement("pci-dss", "missing"), requirement("Python", "covered")])
        summary = job_board.board(self.user)["summary"]
        self.assertEqual(summary["top_gaps"], [{"label": "PCI-DSS", "count": 2}])

    def test_response_rate_waits_for_three_sent(self):
        self.job(status="applied")
        self.job(status="interview")
        self.assertIsNone(job_board.board(self.user)["summary"]["response_rate"])
        self.job(status="rejected")
        self.assertEqual(job_board.board(self.user)["summary"]["response_rate"], 33)

    def test_card_counts_required_lines_and_orders_gaps(self):
        posting = self.job(requirements=[
            requirement("Python", "covered"), requirement("Kafka", "partial"),
            requirement("PCI-DSS", "missing"), requirement("Go", "missing", must=0.1),
        ], match_score=60, scoring_version="jev-1")
        card = job_board.card(posting)
        self.assertEqual(card["required_ratio"], "1/3")
        self.assertEqual([g["label"] for g in card["gaps"]], ["PCI-DSS", "Kafka"])
        self.assertEqual([r["text"] for r in card["nice"]], ["Go"])

    def test_the_trend_only_joins_scores_from_the_same_scorer(self):
        posting = self.job()
        posting.record_score(40)
        posting.record_score(60, scoring_version="jev-1")
        posting.record_score(70, scoring_version="jev-1")
        card = job_board.card(posting)
        self.assertEqual(card["delta"], 10)
        self.assertEqual(len(card["trend"].split()), 2)

    def test_filter_and_sort(self):
        self.job(title="Low", match_score=30, status="applied")
        self.job(title="High", match_score=90)
        by_score = job_board.board(self.user, sort="score")["cards"]
        self.assertEqual([c["posting"].title for c in by_score], ["High", "Low"])
        applied = job_board.board(self.user, status="applied")["cards"]
        self.assertEqual([c["posting"].title for c in applied], ["Low"])


class StatusDatingTests(TestCase):
    def test_the_first_sent_status_records_the_application_date(self):
        user = User.objects.create_user("dates", password="x")
        posting = JobPosting.objects.create(user=user, title="Role")
        self.assertEqual(posting.set_status("saved"), [])
        fields = posting.set_status("interview")
        self.assertIn("applied_at", fields)
        self.assertEqual(posting.applied_at, timezone.localdate())
        self.assertIsNotNone(posting.status_changed_at)
        self.assertNotIn("applied_at", posting.set_status("offer"))


class PageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("page", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.resume = Resume.objects.create(user=self.user, title="Main", content=CONTENT)
        self.client.force_login(self.user)

    def test_empty_state_offers_scoring(self):
        page = self.client.get(reverse("resume:jobs")).content.decode()
        self.assertIn('id="score-modal"', page)
        self.assertIn("openScore()", page)

    def test_a_measured_application_shows_its_table(self):
        JobPosting.objects.create(
            user=self.user, title="Backend", company="Acme", match_score=66, scoring_version="jev-1",
            requirements=[requirement("Python", "covered", evidence="Built services in Python"),
                          requirement("PCI-DSS", "missing")],
        )
        page = self.client.get(reverse("resume:jobs")).content.decode()
        self.assertIn("1/2", page)
        self.assertIn("“Built services in Python”", page)
        self.assertIn("score-ring tone-fair", page)

    def test_scoring_from_the_page_tracks_the_posting(self):
        with patch("resume.services.job_match.typesafe_engine.ask", side_effect=fake_ask), \
             patch("resume.services.job_match.send_openai_message", return_value=PROSE):
            response = self.client.post(reverse("resume:score_job_posting"),
                                        {"posting": POSTING, "resume_id": self.resume.pk})
        posting = JobPosting.objects.get(user=self.user)
        self.assertRedirects(response, f"{reverse('resume:jobs')}?open={posting.pk}#job-{posting.pk}",
                             fetch_redirect_response=False)
        self.assertEqual(posting.scoring_version, "jev-1")

    def test_a_similar_posting_asks_on_the_page(self):
        JobPosting.objects.create(user=self.user, title="Backend Engineer", company="Acme")
        with patch("resume.services.job_match.typesafe_engine.ask", side_effect=fake_ask), \
             patch("resume.services.job_match.send_openai_message", return_value=PROSE):
            self.client.post(reverse("resume:score_job_posting"),
                             {"posting": POSTING, "resume_id": self.resume.pk})
        page = self.client.get(reverse("resume:jobs")).content.decode()
        self.assertIn('name="apply_to" value="new"', page)
        self.assertEqual(JobPosting.objects.filter(user=self.user).count(), 1)

    def test_another_users_resume_cannot_be_scored(self):
        other = User.objects.create_user("other", password="x")
        theirs = Resume.objects.create(user=other, title="Theirs", content=CONTENT)
        with patch("resume.services.job_match.typesafe_engine.ask") as ask:
            self.client.post(reverse("resume:score_job_posting"), {"posting": POSTING, "resume_id": theirs.pk})
        ask.assert_not_called()
        self.assertFalse(JobPosting.objects.exists())

    def test_status_change_over_ajax_is_dated(self):
        posting = JobPosting.objects.create(user=self.user, title="Role")
        response = self.client.post(reverse("resume:update_job_posting", args=[posting.pk]),
                                    {"status": "applied"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.json()["status"], "applied")
        posting.refresh_from_db()
        self.assertEqual(posting.applied_at, timezone.localdate())

    def test_notes_url_and_date_are_saved(self):
        posting = JobPosting.objects.create(user=self.user, title="Role")
        self.client.post(reverse("resume:update_job_posting", args=[posting.pk]),
                         {"notes": "Recruiter: Ayşe", "url": "jobs.example.com/1", "applied_at": "2026-09-01"})
        posting.refresh_from_db()
        self.assertEqual(posting.notes, "Recruiter: Ayşe")
        self.assertEqual(posting.url, "https://jobs.example.com/1")
        self.assertEqual(str(posting.applied_at), "2026-09-01")

    def test_rescoring_measures_the_stored_copy(self):
        posting = JobPosting.objects.create(user=self.user, title="Backend", description=POSTING)
        posting.take_snapshot(CONTENT, "faangpath-simple", self.resume)
        posting.record_score(50)
        posting.save()
        with patch("resume.services.job_match.typesafe_engine.ask", side_effect=fake_ask), \
             patch("resume.services.job_match.send_openai_message", return_value=PROSE):
            self.client.post(reverse("resume:rescore_job_posting", args=[posting.pk]))
        posting.refresh_from_db()
        self.assertEqual(posting.scoring_version, "jev-1")
        self.assertEqual(len(posting.requirements), 3)
