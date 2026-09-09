"""Tests for job matching, tailoring and application tracking."""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from resume.models import JobPosting, Resume
from resume.services import agent_tools, job_service

POSTING = (
    "Senior Python Developer at Acme. We need Django, PostgreSQL and AWS "
    "experience, plus familiarity with Kubernetes and CI/CD pipelines."
)


def content(name="Ada Lovelace"):
    return {
        "user_info": {"full_name": name, "email": "ada@example.com",
                      "skills": ["Python", "Django"]},
        "experience": [{"title": "Backend Engineer", "company": "Beta",
                        "start_date": "2022-01", "end_date": None,
                        "current_role": True, "description": ["Built APIs"]}],
        "education": [],
        "projects_and_publications": [],
    }


MATCH_JSON = json.dumps({
    "score": 72,
    "matched_keywords": ["Python", "Django"],
    "missing_keywords": ["AWS", "Kubernetes"],
    "title": "Senior Python Developer",
    "company": "Acme",
    "verdict": "Strong backend fit, thin on cloud.",
    "suggestions": ["Add cloud work", "Mention CI/CD", "Quantify impact"],
})


class AnalyzeMatchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())

    def test_parses_and_clamps(self):
        with patch("resume.services.job_service.send_openai_message", return_value=MATCH_JSON):
            result = job_service.analyze_match(self.resume, POSTING)
        self.assertEqual(result["score"], 72)
        self.assertEqual(result["missing_keywords"], ["AWS", "Kubernetes"])

    def test_score_out_of_range_is_clamped(self):
        payload = json.loads(MATCH_JSON) | {"score": 250}
        with patch("resume.services.job_service.send_openai_message",
                   return_value=json.dumps(payload)):
            self.assertEqual(job_service.analyze_match(self.resume, POSTING)["score"], 100)

    def test_non_numeric_score_becomes_zero(self):
        payload = json.loads(MATCH_JSON) | {"score": "very good"}
        with patch("resume.services.job_service.send_openai_message",
                   return_value=json.dumps(payload)):
            self.assertEqual(job_service.analyze_match(self.resume, POSTING)["score"], 0)

    def test_short_description_rejected_without_calling_the_model(self):
        with patch("resume.services.job_service.send_openai_message") as llm:
            result = job_service.analyze_match(self.resume, "Python dev")
        self.assertIn("error", result)
        llm.assert_not_called()

    def test_unparseable_response_is_an_error(self):
        with patch("resume.services.job_service.send_openai_message", return_value="not json"):
            self.assertIn("error", job_service.analyze_match(self.resume, POSTING))


class TailorContentTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())

    def test_returns_content_and_summary(self):
        payload = json.dumps({"resume": content("Ada L."), "changes_summary": "Reordered skills"})
        with patch("resume.services.job_service.send_openai_message", return_value=payload):
            result = job_service.tailor_content(self.resume, POSTING)
        self.assertEqual(result["content"]["user_info"]["full_name"], "Ada L.")
        self.assertEqual(result["changes_summary"], "Reordered skills")

    def test_malformed_resume_is_rejected(self):
        with patch("resume.services.job_service.send_openai_message",
                   return_value=json.dumps({"resume": {"nope": 1}})):
            self.assertIn("error", job_service.tailor_content(self.resume, POSTING))

    def test_missing_keywords_are_passed_as_a_hint(self):
        payload = json.dumps({"resume": content(), "changes_summary": ""})
        with patch("resume.services.job_service.send_openai_message",
                   return_value=payload) as llm:
            job_service.tailor_content(self.resume, POSTING, ["AWS", "Kubernetes"])
        sent = llm.call_args.kwargs["user_message"]
        self.assertIn("AWS", sent)
        self.assertIn("only surface them", sent.lower())


class JobSecurityTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.other = User.objects.create_user("eve", password="x")
        self.ctx = {"lang": "en", "active_resume": None, "resumes": [], "quota": {}}
        self.foreign_job = JobPosting.objects.create(
            user=self.other, title="Eve's job", description=POSTING
        )

    def test_another_users_job_is_not_reachable(self):
        for name, args in [
            ("update_job", {"job_id": self.foreign_job.id, "status": "applied"}),
            ("tailor_resume_for_job", {"job_id": self.foreign_job.id}),
        ]:
            result = agent_tools.get_tool(name).handler(self.user, self.ctx, **args)
            self.assertIn("error", result.data, name)
        self.foreign_job.refresh_from_db()
        self.assertEqual(self.foreign_job.status, "saved")

    def test_list_jobs_only_returns_your_own(self):
        JobPosting.objects.create(user=self.user, title="Mine")
        result = agent_tools.get_tool("list_jobs").handler(self.user, self.ctx)
        self.assertEqual([j["title"] for j in result.data["jobs"]], ["Mine"])

    def test_deleting_a_resume_keeps_the_application_history(self):
        resume = Resume.objects.create(user=self.user, title="CV", content=content())
        posting = JobPosting.objects.create(
            user=self.user, title="Job", source_resume=resume,
            snapshot_content=content(),
        )
        resume.delete()
        posting.refresh_from_db()
        self.assertIsNone(posting.source_resume_id)


class JobCopyTest(TestCase):
    """Panel wording travels with the data, in the conversation's language."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        Resume.objects.create(user=self.user, title="CV", content=content())
        self.client.force_login(self.user)

    def _turn(self, message):
        from resume.services import agent_loop  # noqa: F401

        with patch(
            "resume.services.agent_loop.send_openai_tool_turn",
            return_value=(
                __import__("types").SimpleNamespace(content="ok", tool_calls=None),
                __import__("types").SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            ),
        ):
            from django.urls import reverse

            return self.client.post(
                reverse("resume:agent_chat"),
                json.dumps({"message": message}),
                content_type="application/json",
            ).json()

    def test_turkish_turn_carries_turkish_job_copy(self):
        body = self._turn("başvurularımı göster")
        self.assertEqual(body["ui_copy"]["job"]["jobs_title"], "Başvurular")
        self.assertEqual(body["ui_copy"]["job"]["status"]["applied"], "Başvuruldu")

    def test_english_turn_carries_english_job_copy(self):
        body = self._turn("show my applications")
        self.assertEqual(body["ui_copy"]["job"]["jobs_title"], "Applications")

    def test_both_languages_define_the_same_keys(self):
        self.assertEqual(set(job_service.JOB_COPY["en"]), set(job_service.JOB_COPY["tr"]))
        self.assertEqual(
            set(job_service.JOB_COPY["en"]["status"]),
            set(job_service.JOB_COPY["tr"]["status"]),
        )

    def test_status_keys_cover_every_model_choice(self):
        model_statuses = {value for value, _ in JobPosting.STATUS_CHOICES}
        self.assertEqual(set(job_service.JOB_COPY["en"]["status"]), model_statuses)


class OneRecordPerPostingTest(TestCase):
    """Rule 1: pasting the same advert twice must not open a second row."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.ctx = {"lang": "en", "active_resume": self.resume, "resumes": [], "quota": {}}

    def _match(self, description=POSTING, score=72):
        payload = json.loads(MATCH_JSON) | {"score": score}
        with patch("resume.services.job_service.send_openai_message",
                   return_value=json.dumps(payload)):
            return agent_tools.get_tool("match_job").handler(
                self.user, self.ctx, description=description
            )

    def test_the_same_posting_updates_one_application(self):
        first = self._match()
        second = self._match(score=85)
        self.assertEqual(JobPosting.objects.count(), 1)
        self.assertEqual(first.data["job_id"], second.data["job_id"])

    def test_whitespace_and_case_do_not_create_a_second_one(self):
        self._match()
        self._match(description="  " + POSTING.upper() + "\n\n")
        self.assertEqual(JobPosting.objects.count(), 1)

    def test_a_posting_for_the_same_role_asks_instead_of_duplicating(self):
        """Same title and company, different text: could be a re-paste or a
        second opening, and guessing either way loses something."""
        self._match()
        second = self._match(description="Frontend Engineer at Beta. React, TypeScript and modern CSS tooling.")
        self.assertTrue(second.data["needs_choice"])
        self.assertEqual(JobPosting.objects.count(), 1)

    def test_answering_new_tracks_it_separately(self):
        self._match()
        payload = json.loads(MATCH_JSON)
        with patch("resume.services.job_service.send_openai_message",
                   return_value=json.dumps(payload)):
            agent_tools.get_tool("match_job").handler(
                self.user, self.ctx,
                description="A different advert entirely, for a data engineering role at Gamma.",
                apply_to="new",
            )
        self.assertEqual(JobPosting.objects.count(), 2)

    def test_answering_with_an_id_updates_that_application(self):
        first = self._match()
        job_id = first.data["job_id"]
        payload = json.loads(MATCH_JSON) | {"score": 91}
        with patch("resume.services.job_service.send_openai_message",
                   return_value=json.dumps(payload)):
            result = agent_tools.get_tool("match_job").handler(
                self.user, self.ctx,
                description="A longer version of the same advert, with the benefits section included.",
                apply_to=str(job_id),
            )
        self.assertEqual(JobPosting.objects.count(), 1)
        self.assertEqual(result.data["job_id"], job_id)
        posting = JobPosting.objects.get()
        self.assertEqual(posting.match_score, 91)
        self.assertIn("longer version", posting.description)

    def test_a_genuinely_unrelated_posting_needs_no_question(self):
        self._match()
        payload = json.loads(MATCH_JSON) | {"title": "Frontend Engineer",
                                            "company": "Beta"}
        with patch("resume.services.job_service.send_openai_message",
                   return_value=json.dumps(payload)):
            result = agent_tools.get_tool("match_job").handler(
                self.user, self.ctx, description="Frontend Engineer at Beta. React, TypeScript and CSS, building the design system.",
            )
        self.assertNotIn("needs_choice", result.data)
        self.assertEqual(JobPosting.objects.count(), 2)

    def test_answering_with_an_unknown_id_is_refused(self):
        result = agent_tools.get_tool("match_job").handler(
            self.user, self.ctx, description=POSTING, apply_to="99999"
        )
        self.assertIn("error", result.data)

    def test_the_first_match_is_flagged_as_new(self):
        self.assertTrue(self._match().data["is_new_application"])
        self.assertFalse(self._match().data["is_new_application"])

    def test_rescoring_reports_the_previous_score(self):
        self._match(score=60)
        second = self._match(score=78)
        self.assertEqual(second.data["previous_score"], 60)

    def test_every_measurement_is_kept(self):
        self._match(score=60)
        self._match(score=72)
        self._match(score=85)
        posting = JobPosting.objects.get()
        self.assertEqual([m["score"] for m in posting.score_history], [60, 72, 85])
        self.assertEqual(posting.match_score, 85)

    def test_history_does_not_grow_without_bound(self):
        for i in range(25):
            self._match(score=i)
        self.assertLessEqual(len(JobPosting.objects.get().score_history), 20)


class RescoreTest(TestCase):
    """Rule 3: a score the user cannot see move is not useful."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.ctx = {"lang": "en", "active_resume": self.resume, "resumes": [], "quota": {}}
        with patch("resume.services.job_service.send_openai_message",
                   return_value=MATCH_JSON):
            self.job_id = agent_tools.get_tool("match_job").handler(
                self.user, self.ctx, description=POSTING
            ).data["job_id"]

    def _rescore(self, score):
        payload = json.loads(MATCH_JSON) | {"score": score}
        with patch("resume.services.job_service.send_openai_message",
                   return_value=json.dumps(payload)):
            return agent_tools.get_tool("rescore_job").handler(
                self.user, self.ctx, job_id=self.job_id
            )

    def test_reports_the_change(self):
        result = self._rescore(85)
        self.assertEqual(result.data["previous_score"], 72)
        self.assertEqual(result.data["score"], 85)
        self.assertEqual(result.data["change"], 13)

    def test_a_drop_is_reported_too(self):
        self.assertEqual(self._rescore(60).data["change"], -12)

    def test_it_measures_the_stored_copy_not_a_live_resume(self):
        """The snapshot is what would be sent, so it is what gets measured."""
        posting = JobPosting.objects.get(pk=self.job_id)
        posting.snapshot_content = content("Snapshot Person")
        posting.save()
        with patch("resume.services.job_service.send_openai_message",
                   return_value=MATCH_JSON) as llm:
            agent_tools.get_tool("rescore_job").handler(
                self.user, self.ctx, job_id=self.job_id
            )
        self.assertIn("Snapshot Person", llm.call_args.kwargs["user_message"])

    def test_unknown_job(self):
        result = agent_tools.get_tool("rescore_job").handler(
            self.user, self.ctx, job_id=99999
        )
        self.assertIn("error", result.data)

    def test_it_does_not_need_approval(self):
        """Measuring changes nothing, so stopping to ask would be noise."""
        self.assertFalse(agent_tools.TOOL_REGISTRY["rescore_job"].destructive)


class DerivedResumeQuotaTest(TestCase):
    """Only base resumes consume a slot."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")

    def test_language_versions_are_free(self):
        from django.conf import settings

        limit = settings.FREE_TIER_LIMITS["resume_count"]
        bases = [
            Resume.objects.create(user=self.user, title=f"Base {i}", content=content())
            for i in range(limit)
        ]
        self.assertFalse(self.user.profile.can_create_resume())

        for base in bases:
            Resume.objects.create(
                user=self.user, title=f"{base.title} tr", content=content(),
                derived_from=base, derived_kind=Resume.DERIVED_TRANSLATION,
            )
        self.assertEqual(self.user.resumes.count(), limit * 2)
        self.assertEqual(
            self.user.resumes.filter(derived_from__isnull=True).count(), limit
        )

    def test_application_snapshots_are_not_resumes_at_all(self):
        """They live inside the application, so they cannot consume a slot."""
        base = Resume.objects.create(user=self.user, title="Base", content=content())
        for i in range(5):
            JobPosting.objects.create(
                user=self.user, title=f"Job {i}", description=POSTING,
                content_hash=f"h{i}", snapshot_content=content(), source_resume=base,
            )
        self.assertEqual(self.user.resumes.count(), 1)
        self.assertTrue(self.user.profile.can_create_resume())

    def test_family_covers_language_versions(self):
        base = Resume.objects.create(user=self.user, title="Base", content=content())
        translation = Resume.objects.create(
            user=self.user, title="tr", content=content(),
            derived_from=base, derived_kind=Resume.DERIVED_TRANSLATION,
        )
        self.assertEqual(
            set(base.family().values_list("pk", flat=True)),
            {base.pk, translation.pk},
        )
        self.assertEqual(translation.root, base)

    def test_deleting_the_base_removes_its_language_versions(self):
        base = Resume.objects.create(user=self.user, title="Base", content=content())
        derived = Resume.objects.create(
            user=self.user, title="d", content=content(),
            derived_from=base, derived_kind=Resume.DERIVED_TRANSLATION,
        )
        base.delete()
        self.assertFalse(Resume.objects.filter(pk=derived.pk).exists())

class FingerprintTest(TestCase):
    def test_ignores_whitespace_and_case(self):
        self.assertEqual(
            JobPosting.fingerprint("Senior  Python\nDeveloper"),
            JobPosting.fingerprint("senior python developer"),
        )

    def test_different_text_differs(self):
        self.assertNotEqual(
            JobPosting.fingerprint("Python role"), JobPosting.fingerprint("Java role")
        )

    def test_empty(self):
        self.assertEqual(JobPosting.fingerprint(""), "")
        self.assertEqual(JobPosting.fingerprint(None), "")




class SnapshotTest(TestCase):
    """
    An application records what was sent, not a pointer to a document that can
    still change underneath it.
    """

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.base = Resume.objects.create(user=self.user, title="Main", content=content())
        self.ctx = {"lang": "en", "active_resume": self.base, "resumes": [], "quota": {}}

    def _match(self, description=POSTING):
        with patch("resume.services.job_service.send_openai_message",
                   return_value=MATCH_JSON):
            return agent_tools.get_tool("match_job").handler(
                self.user, self.ctx, description=description
            )

    def _tailor(self, job_id, name="Tailored"):
        payload = json.dumps({"resume": content(name), "changes_summary": "Reordered"})
        with patch("resume.services.job_service.send_openai_message", return_value=payload):
            return agent_tools.get_tool("tailor_resume_for_job").handler(
                self.user, self.ctx, job_id=job_id
            )

    def test_matching_freezes_the_resume(self):
        job_id = self._match().data["job_id"]
        posting = JobPosting.objects.get(pk=job_id)
        self.assertTrue(posting.has_snapshot)
        self.assertEqual(
            posting.snapshot_content["user_info"]["full_name"], "Ada Lovelace"
        )
        self.assertEqual(posting.source_resume, self.base)
        self.assertIsNotNone(posting.snapshot_taken_at)

    def test_editing_the_base_resume_does_not_rewrite_the_record(self):
        """The bug this design exists to prevent."""
        job_id = self._match().data["job_id"]
        self.base.content = content("Changed Later")
        self.base.save()
        posting = JobPosting.objects.get(pk=job_id)
        self.assertEqual(
            posting.snapshot_content["user_info"]["full_name"], "Ada Lovelace"
        )

    def test_tailoring_writes_the_snapshot_and_adds_no_resume(self):
        job_id = self._match().data["job_id"]
        before = Resume.objects.filter(user=self.user).count()
        result = self._tailor(job_id)
        self.assertEqual(result.data["stored_as"], "application_snapshot")
        self.assertEqual(Resume.objects.filter(user=self.user).count(), before)
        posting = JobPosting.objects.get(pk=job_id)
        self.assertEqual(
            posting.snapshot_content["user_info"]["full_name"], "Tailored"
        )

    def test_tailoring_twice_rewrites_the_same_snapshot(self):
        job_id = self._match().data["job_id"]
        self._tailor(job_id, "First")
        self._tailor(job_id, "Second")
        self.assertEqual(Resume.objects.filter(user=self.user).count(), 1)
        posting = JobPosting.objects.get(pk=job_id)
        self.assertEqual(posting.snapshot_content["user_info"]["full_name"], "Second")

    def test_tailoring_leaves_the_base_resume_alone(self):
        job_id = self._match().data["job_id"]
        self._tailor(job_id)
        self.base.refresh_from_db()
        self.assertEqual(self.base.content["user_info"]["full_name"], "Ada Lovelace")

    def test_tailoring_always_starts_from_the_base(self):
        """Tailoring the previous result is what made titles compound."""
        job_id = self._match().data["job_id"]
        self._tailor(job_id, "First")
        payload = json.dumps({"resume": content("Second"), "changes_summary": ""})
        with patch("resume.services.job_service.send_openai_message",
                   return_value=payload) as llm:
            self._tailor_called = agent_tools.get_tool(
                "tailor_resume_for_job"
            ).handler(self.user, self.ctx, job_id=job_id)
        sent = llm.call_args.kwargs["user_message"]
        self.assertIn("Ada Lovelace", sent)
        self.assertNotIn("First", sent)


class SnapshotCloneTest(TestCase):
    """The stored copy is read-only; editing it means cloning it."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.base = Resume.objects.create(user=self.user, title="Main", content=content())
        self.posting = JobPosting.objects.create(
            user=self.user, title="Backend Engineer", company="Acme",
            description=POSTING, content_hash="hash",
            snapshot_content=content("Snapshot Person"),
            snapshot_template="modern-sidebar", source_resume=self.base,
        )
        self.ctx = {"lang": "en", "active_resume": self.base, "resumes": [], "quota": {}}
        self.client.force_login(self.user)

    def test_the_tool_creates_an_editable_resume(self):
        result = agent_tools.get_tool("clone_application_resume").handler(
            self.user, self.ctx, job_id=self.posting.id
        )
        clone = Resume.objects.get(pk=result.data["resume_id"])
        self.assertEqual(clone.content["user_info"]["full_name"], "Snapshot Person")
        self.assertEqual(clone.template_selector, "modern-sidebar")
        self.assertIsNone(clone.derived_from_id)
        self.assertTrue(result.data["counted_against_resume_limit"])

    def test_the_clone_counts_against_the_resume_limit(self):
        from django.conf import settings

        for i in range(settings.FREE_TIER_LIMITS["resume_count"]):
            Resume.objects.create(user=self.user, title=f"Extra {i}", content=content())
        self.user.profile.tier = "free"
        self.user.profile.save()

        result = agent_tools.get_tool("clone_application_resume").handler(
            self.user, self.ctx, job_id=self.posting.id
        )
        self.assertTrue(result.data["upgrade_required"])

    def test_cloning_does_not_disturb_the_record(self):
        agent_tools.get_tool("clone_application_resume").handler(
            self.user, self.ctx, job_id=self.posting.id
        )
        self.posting.refresh_from_db()
        self.assertEqual(
            self.posting.snapshot_content["user_info"]["full_name"], "Snapshot Person"
        )

    def test_the_tool_needs_approval(self):
        self.assertTrue(
            agent_tools.TOOL_REGISTRY["clone_application_resume"].destructive
        )

    def test_the_page_renders_the_stored_copy_read_only(self):
        html = self.client.get(
            reverse("resume:application_snapshot", args=[self.posting.pk])
        ).content.decode()
        self.assertIn("Snapshot Person", html)

    def test_another_users_snapshot_is_not_readable(self):
        eve = User.objects.create_user("eve", password="x")
        theirs = JobPosting.objects.create(
            user=eve, title="Secret", description=POSTING,
            snapshot_content=content("Eve"),
        )
        resp = self.client.get(
            reverse("resume:application_snapshot", args=[theirs.pk])
        )
        self.assertEqual(resp.status_code, 404)

    def test_the_page_clones_on_post(self):
        self.client.post(
            reverse("resume:clone_application_snapshot", args=[self.posting.pk])
        )
        clone = Resume.objects.filter(title__startswith="Backend Engineer").first()
        self.assertIsNotNone(clone)
        self.assertEqual(clone.content["user_info"]["full_name"], "Snapshot Person")

    def test_cloning_another_users_application_is_refused(self):
        eve = User.objects.create_user("eve", password="x")
        theirs = JobPosting.objects.create(
            user=eve, title="Secret", description=POSTING,
            snapshot_content=content("Eve"),
        )
        self.client.post(
            reverse("resume:clone_application_snapshot", args=[theirs.pk])
        )
        self.assertFalse(Resume.objects.filter(user=self.user, title="Secret").exists())


class ApplicationLimitTest(TestCase):
    """Each application stores a resume, so the free allowance is bounded."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.ctx = {"lang": "en", "active_resume": self.resume, "resumes": [], "quota": {}}

    def test_free_users_stop_at_the_limit(self):
        from django.conf import settings

        limit = settings.FREE_TIER_LIMITS["application_count"]
        for i in range(limit):
            JobPosting.objects.create(
                user=self.user, title=f"Job {i}", description=POSTING,
                content_hash=f"hash-{i}",
            )
        self.assertFalse(self.user.profile.can_track_application())

    def test_pro_users_are_unlimited(self):
        from django.conf import settings

        for i in range(settings.FREE_TIER_LIMITS["application_count"] + 5):
            JobPosting.objects.create(
                user=self.user, title=f"Job {i}", description=POSTING,
                content_hash=f"hash-{i}",
            )
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.assertTrue(self.user.profile.can_track_application())

    def test_re_measuring_an_existing_application_is_not_blocked(self):
        """The limit is on how many are tracked, not on measuring them."""
        from django.conf import settings

        self.user.profile.tier = "pro"
        self.user.profile.save()
        with patch("resume.services.job_service.send_openai_message",
                   return_value=MATCH_JSON):
            first = agent_tools.get_tool("match_job").handler(
                self.user, self.ctx, description=POSTING
            )

        # At the cap, measuring the *same* posting again must still work: the
        # limit is on tracking new applications, not on re-measuring one.
        with patch.object(
            type(self.user.profile), "can_track_application", return_value=False
        ), patch("resume.services.job_service.send_openai_message",
                 return_value=MATCH_JSON):
            again = agent_tools.get_tool("match_job").handler(
                self.user, self.ctx, description=POSTING, apply_to=str(first.data["job_id"])
            )
        self.assertEqual(again.data.get("job_id"), first.data["job_id"])

    def test_a_new_posting_is_blocked_at_the_cap(self):
        self.user.profile.tier = "pro"
        self.user.profile.save()
        with patch.object(
            type(self.user.profile), "can_track_application", return_value=False
        ), patch("resume.services.job_service.send_openai_message",
                 return_value=MATCH_JSON):
            result = agent_tools.get_tool("match_job").handler(
                self.user, self.ctx, description=POSTING, apply_to="new"
            )
        self.assertTrue(result.data["upgrade_required"])
        self.assertFalse(JobPosting.objects.exists())


class GroupByBaseResumeTest(TestCase):
    """Grouping keys on what was sent, not on labels a model guessed."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.python_cv = Resume.objects.create(user=self.user, title="Python CV", content=content())
        self.cpp_cv = Resume.objects.create(user=self.user, title="C++ CV", content=content())
        self.ctx = {"lang": "en", "active_resume": self.python_cv, "resumes": [], "quota": {}}
        self.client.force_login(self.user)

    def _apply(self, resume, title, digest):
        JobPosting.objects.create(
            user=self.user, title=title, description=POSTING, content_hash=digest,
            snapshot_content=content(), source_resume=resume,
        )

    def test_applications_group_under_their_base_resume(self):
        self._apply(self.python_cv, "Django Dev", "a")
        self._apply(self.python_cv, "Backend Dev", "b")
        self._apply(self.cpp_cv, "Embedded Dev", "c")
        groups = agent_tools.get_tool("resume_groups").handler(
            self.user, self.ctx
        ).data["groups"]
        by_name = {g["resume_name"]: g for g in groups}
        self.assertEqual(len(by_name["Python CV"]["applications"]), 2)
        self.assertEqual(len(by_name["C++ CV"]["applications"]), 1)

    def test_one_resume_produces_no_groups(self):
        self._apply(self.python_cv, "Django Dev", "a")
        result = agent_tools.get_tool("resume_groups").handler(self.user, self.ctx)
        self.assertEqual(result.data["groups"], [])
        self.assertIn("only one resume", result.data["note"].lower())

    def test_the_standard_page_hides_a_single_group(self):
        self._apply(self.python_cv, "Django Dev", "a")
        response = self.client.get(reverse("resume:jobs"))
        self.assertEqual(response.context["groups"], [])

    def test_the_standard_page_shows_two(self):
        self._apply(self.python_cv, "Django Dev", "a")
        self._apply(self.cpp_cv, "Embedded Dev", "b")
        response = self.client.get(reverse("resume:jobs"))
        self.assertEqual(len(response.context["groups"]), 2)
        html = response.content.decode()
        self.assertIn("Python CV", html)
        self.assertIn("1 application", html)


class JobTrackerPageTest(TestCase):
    """The standard-mode table over the same data."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.job = JobPosting.objects.create(
            user=self.user, title="Backend Engineer", company="Acme",
            description=POSTING, content_hash="hash", match_score=72,
            missing_keywords=["AWS"], snapshot_content=content(),
            source_resume=self.resume,
        )
        self.client.force_login(self.user)

    def test_page_lists_the_users_applications(self):
        html = self.client.get(reverse("resume:jobs")).content.decode()
        self.assertIn("Backend Engineer", html)
        self.assertIn("Acme", html)
        self.assertIn("72", html)

    def test_the_stored_copy_is_offered_read_only(self):
        html = self.client.get(reverse("resume:jobs")).content.decode()
        self.assertIn(
            reverse("resume:application_snapshot", args=[self.job.pk]), html
        )
        self.assertNotIn('<select name="resume"', html)

    def test_free_users_see_their_applications(self):
        """The paywall is the allowance, not the feature."""
        self.user.profile.tier = "free"
        self.user.profile.save()
        response = self.client.get(reverse("resume:jobs"))
        self.assertIn("Backend Engineer", response.content.decode())
        self.assertFalse(response.context["at_cap"])

    def test_the_cap_is_announced_when_reached(self):
        from django.conf import settings

        self.user.profile.tier = "free"
        self.user.profile.save()
        for i in range(settings.FREE_TIER_LIMITS["application_count"]):
            JobPosting.objects.create(
                user=self.user, title=f"Filler {i}", description="advert",
                content_hash=f"filler-{i}",
            )
        response = self.client.get(reverse("resume:jobs"))
        self.assertTrue(response.context["at_cap"])
        html = response.content.decode()
        self.assertIn("free applications", html)
        # The applications themselves stay visible
        self.assertIn("Backend Engineer", html)

    def test_another_users_applications_are_not_listed(self):
        eve = User.objects.create_user("eve", password="x")
        JobPosting.objects.create(user=eve, title="Eve's secret job")
        html = self.client.get(reverse("resume:jobs")).content.decode()
        self.assertNotIn("Eve's secret job", html)

    def test_status_can_be_changed(self):
        self.client.post(
            reverse("resume:update_job_posting", args=[self.job.pk]),
            {"status": "interview"},
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "interview")

    def test_a_bad_status_is_ignored(self):
        self.client.post(
            reverse("resume:update_job_posting", args=[self.job.pk]),
            {"status": "ghosted"},
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "saved")

    def test_deleting_stops_tracking(self):
        self.client.post(reverse("resume:delete_job_posting", args=[self.job.pk]))
        self.assertFalse(JobPosting.objects.filter(pk=self.job.pk).exists())

    def test_mutations_require_post(self):
        for name in ("resume:update_job_posting", "resume:delete_job_posting",
                     "resume:clone_application_snapshot"):
            resp = self.client.get(reverse(name, args=[self.job.pk]))
            self.assertEqual(resp.status_code, 405, name)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("resume:jobs")).status_code, 302
        )


class SnapshotIsolationTest(TestCase):
    """The reason snapshots exist, stated as a test."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.base = Resume.objects.create(user=self.user, title="Main", content=content())
        self.posting = JobPosting.objects.create(
            user=self.user, title="Backend Engineer", company="Acme",
            description=POSTING, content_hash="hash", source_resume=self.base,
        )
        self.posting.take_snapshot(
            self.base.content, self.base.template_selector, self.base
        )
        self.posting.save()

    def test_editing_the_base_resume_leaves_the_record_alone(self):
        self.base.content = content("Someone Else")
        self.base.save()
        self.posting.refresh_from_db()
        self.assertEqual(
            self.posting.snapshot_content["user_info"]["full_name"], "Ada Lovelace"
        )

    def test_deleting_the_base_resume_leaves_the_record_alone(self):
        """Application history should outlive the resume it came from."""
        self.base.delete()
        self.posting.refresh_from_db()
        self.assertIsNone(self.posting.source_resume_id)
        self.assertTrue(self.posting.has_snapshot)
        self.assertEqual(
            self.posting.snapshot_content["user_info"]["full_name"], "Ada Lovelace"
        )

    def test_a_second_application_does_not_disturb_the_first(self):
        """Tailoring for a later posting used to rewrite the earlier record."""
        second = JobPosting.objects.create(
            user=self.user, title="Other role", description="another advert",
            content_hash="hash2", source_resume=self.base,
        )
        second.take_snapshot(content("Tailored For Second"), "faangpath-simple", self.base)
        second.save()

        self.posting.refresh_from_db()
        self.assertEqual(
            self.posting.snapshot_content["user_info"]["full_name"], "Ada Lovelace"
        )
        self.assertEqual(
            second.snapshot_content["user_info"]["full_name"], "Tailored For Second"
        )

    def test_the_snapshot_is_copied_not_referenced(self):
        self.base.content["user_info"]["skills"].append("Mutated In Place")
        self.assertNotIn(
            "Mutated In Place", self.posting.snapshot_content["user_info"]["skills"]
        )
