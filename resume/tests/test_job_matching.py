"""Tests for job matching, tailoring and application tracking."""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

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
    "tags": ["Python", "backend", "python"],
    "title": "Senior Python Developer",
    "company": "Acme",
    "verdict": "Strong backend fit, thin on cloud.",
    "suggestions": ["Add cloud work", "Mention CI/CD", "Quantify impact"],
})


class TagNormalisationTest(TestCase):
    def test_lowercased_trimmed_deduped_order_kept(self):
        self.assertEqual(
            JobPosting.normalize_tags([" Python ", "python", "BACKEND", "", None]),
            ["python", "backend"],
        )

    def test_empty(self):
        self.assertEqual(JobPosting.normalize_tags(None), [])


class AnalyzeMatchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())

    def test_parses_and_clamps(self):
        with patch("resume.services.job_service.send_openai_message", return_value=MATCH_JSON):
            result = job_service.analyze_match(self.resume, POSTING)
        self.assertEqual(result["score"], 72)
        self.assertEqual(result["missing_keywords"], ["AWS", "Kubernetes"])
        self.assertEqual(result["tags"], ["python", "backend"])

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


class JobToolTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.ctx = {"lang": "en", "active_resume": self.resume, "resumes": [], "quota": {}}

    def _match(self):
        with patch("resume.services.job_service.send_openai_message", return_value=MATCH_JSON):
            return agent_tools.get_tool("match_job").handler(
                self.user, self.ctx, description=POSTING
            )

    def test_match_saves_the_posting(self):
        result = self._match()
        posting = JobPosting.objects.get(pk=result.data["job_id"])
        self.assertEqual(posting.title, "Senior Python Developer")
        self.assertEqual(posting.company, "Acme")
        self.assertEqual(posting.match_score, 72)
        self.assertEqual(posting.resume, self.resume)
        self.assertEqual(posting.tags, ["python", "backend"])

    def test_match_emits_a_panel(self):
        result = self._match()
        self.assertEqual(result.ui[0]["type"], "job_match")
        self.assertEqual(result.ui[0]["score"], 72)

    def test_tailor_creates_a_copy_and_keeps_the_original(self):
        job_id = self._match().data["job_id"]
        payload = json.dumps({"resume": content("Ada Tailored"), "changes_summary": "Foregrounded APIs"})
        with patch("resume.services.job_service.send_openai_message", return_value=payload):
            result = agent_tools.get_tool("tailor_resume_for_job").handler(
                self.user, self.ctx, job_id=job_id
            )
        variant = Resume.objects.get(pk=result.data["resume_id"])
        self.assertNotEqual(variant.pk, self.resume.pk)
        self.assertEqual(variant.content["user_info"]["full_name"], "Ada Tailored")
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["user_info"]["full_name"], "Ada Lovelace")

    def test_tailoring_repoints_the_posting_at_the_variant(self):
        job_id = self._match().data["job_id"]
        payload = json.dumps({"resume": content("Ada Tailored"), "changes_summary": ""})
        with patch("resume.services.job_service.send_openai_message", return_value=payload):
            result = agent_tools.get_tool("tailor_resume_for_job").handler(
                self.user, self.ctx, job_id=job_id
            )
        posting = JobPosting.objects.get(pk=job_id)
        self.assertEqual(posting.resume_id, result.data["resume_id"])

    def test_tailor_needs_approval(self):
        self.assertTrue(agent_tools.get_tool("tailor_resume_for_job").destructive)

    def test_tailor_rejects_an_unknown_job(self):
        result = agent_tools.get_tool("tailor_resume_for_job").handler(
            self.user, self.ctx, job_id=99999
        )
        self.assertIn("error", result.data)

    def test_list_jobs_and_status_filter(self):
        self._match()
        listed = agent_tools.get_tool("list_jobs").handler(self.user, self.ctx)
        self.assertEqual(listed.data["count"], 1)
        filtered = agent_tools.get_tool("list_jobs").handler(
            self.user, self.ctx, status="applied"
        )
        self.assertEqual(filtered.data["count"], 0)

    def test_update_job_status(self):
        job_id = self._match().data["job_id"]
        result = agent_tools.get_tool("update_job").handler(
            self.user, self.ctx, job_id=job_id, status="interview"
        )
        self.assertEqual(result.data["status"], "interview")

    def test_update_job_rejects_a_bad_status(self):
        job_id = self._match().data["job_id"]
        result = agent_tools.get_tool("update_job").handler(
            self.user, self.ctx, job_id=job_id, status="ghosted"
        )
        self.assertIn("error", result.data)

    def test_update_job_with_nothing_to_change(self):
        job_id = self._match().data["job_id"]
        result = agent_tools.get_tool("update_job").handler(self.user, self.ctx, job_id=job_id)
        self.assertIn("error", result.data)

    def test_resume_groups_answers_which_cv_for_which_role(self):
        self._match()
        cpp = Resume.objects.create(user=self.user, title="C++ CV", content=content())
        JobPosting.objects.create(
            user=self.user, title="C++ Engineer", tags=["c++", "embedded"], resume=cpp
        )
        groups = {g["tag"]: g for g in
                  agent_tools.get_tool("resume_groups").handler(self.user, self.ctx).data["groups"]}
        self.assertEqual(groups["python"]["resumes"][0]["resume_id"], self.resume.pk)
        self.assertEqual(groups["c++"]["resumes"][0]["resume_id"], cpp.pk)

    def test_groups_rank_by_how_often_a_resume_is_used(self):
        """Uses are counted per application, and each posting counts once."""
        self._match()
        JobPosting.objects.create(
            user=self.user, title="Another Python role", description="different advert",
            content_hash="other-hash", tags=["python"], resume=self.resume,
        )
        other = Resume.objects.create(user=self.user, title="Other", content=content())
        JobPosting.objects.create(
            user=self.user, title="X", description="third advert",
            content_hash="third-hash", tags=["python"], resume=other,
        )
        groups = agent_tools.get_tool("resume_groups").handler(self.user, self.ctx).data["groups"]
        python = next(g for g in groups if g["tag"] == "python")
        self.assertEqual(python["resumes"][0]["resume_id"], self.resume.pk)
        self.assertEqual(python["resumes"][0]["uses"], 2)


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
        posting = JobPosting.objects.create(user=self.user, title="Job", resume=resume)
        resume.delete()
        posting.refresh_from_db()
        self.assertIsNone(posting.resume_id)


class ProGateTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.ctx = {"lang": "en", "active_resume": self.resume, "resumes": [], "quota": {}}

    def test_free_users_are_not_offered_the_pro_tools(self):
        names = {s["function"]["name"] for s in agent_tools.tool_schemas(self.user)}
        self.assertFalse(names & set(agent_tools.pro_tool_names()))

    def test_pro_users_are(self):
        self.user.profile.tier = "pro"
        self.user.profile.save()
        names = {s["function"]["name"] for s in agent_tools.tool_schemas(self.user)}
        self.assertTrue(set(agent_tools.pro_tool_names()) <= names)

    def test_handlers_refuse_free_users_even_if_called(self):
        """Defence in depth: withholding the schema is not the only guard."""
        with patch("resume.services.job_service.send_openai_message") as llm:
            result = agent_tools.get_tool("match_job").handler(
                self.user, self.ctx, description=POSTING
            )
        self.assertTrue(result.data["upgrade_required"])
        llm.assert_not_called()

    def test_no_posting_is_saved_when_refused(self):
        agent_tools.get_tool("match_job").handler(self.user, self.ctx, description=POSTING)
        self.assertEqual(JobPosting.objects.count(), 0)


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


class JobTrackerPageTest(TestCase):
    """The standard dashboard's table over the same data the tools reach."""

    def setUp(self):
        from django.urls import reverse

        self.reverse = reverse
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        self.other_resume = Resume.objects.create(
            user=self.user, title="Second", content=content()
        )
        self.job = JobPosting.objects.create(
            user=self.user, title="Backend Engineer", company="Acme",
            tags=["python"], resume=self.resume, match_score=72,
            missing_keywords=["AWS"],
        )
        self.client.force_login(self.user)

    def test_page_lists_the_users_applications(self):
        html = self.client.get(self.reverse("resume:jobs")).content.decode()
        self.assertIn("Backend Engineer", html)
        self.assertIn("Acme", html)
        self.assertIn("72", html)

    def test_page_shows_resume_groups(self):
        html = self.client.get(self.reverse("resume:jobs")).content.decode()
        self.assertIn("python", html)

    def test_free_users_get_the_upsell_not_the_table(self):
        self.user.profile.tier = "free"
        self.user.profile.save()
        html = self.client.get(self.reverse("resume:jobs")).content.decode()
        self.assertNotIn("Backend Engineer", html)
        self.assertIn("Pro", html)

    def test_another_users_applications_are_not_listed(self):
        eve = User.objects.create_user("eve", password="x")
        JobPosting.objects.create(user=eve, title="Eve's secret job")
        html = self.client.get(self.reverse("resume:jobs")).content.decode()
        self.assertNotIn("Eve's secret job", html)

    def test_status_can_be_changed(self):
        self.client.post(
            self.reverse("resume:update_job_posting", args=[self.job.pk]),
            {"status": "interview"},
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "interview")

    def test_resume_can_be_reassigned(self):
        self.client.post(
            self.reverse("resume:update_job_posting", args=[self.job.pk]),
            {"status": "applied", "resume": str(self.other_resume.pk)},
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.resume_id, self.other_resume.pk)

    def test_resume_can_be_cleared(self):
        self.client.post(
            self.reverse("resume:update_job_posting", args=[self.job.pk]),
            {"status": "applied", "resume": ""},
        )
        self.job.refresh_from_db()
        self.assertIsNone(self.job.resume_id)

    def test_a_bad_status_is_ignored(self):
        self.client.post(
            self.reverse("resume:update_job_posting", args=[self.job.pk]),
            {"status": "ghosted"},
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "saved")

    def test_another_users_resume_cannot_be_attached(self):
        eve = User.objects.create_user("eve", password="x")
        theirs = Resume.objects.create(user=eve, title="Eve CV", content=content())
        self.client.post(
            self.reverse("resume:update_job_posting", args=[self.job.pk]),
            {"resume": str(theirs.pk)},
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.resume_id, self.resume.pk)

    def test_deleting_stops_tracking(self):
        self.client.post(self.reverse("resume:delete_job_posting", args=[self.job.pk]))
        self.assertFalse(JobPosting.objects.filter(pk=self.job.pk).exists())

    def test_cannot_delete_another_users_application(self):
        eve = User.objects.create_user("eve", password="x")
        theirs = JobPosting.objects.create(user=eve, title="Eve's job")
        self.client.post(self.reverse("resume:delete_job_posting", args=[theirs.pk]))
        self.assertTrue(JobPosting.objects.filter(pk=theirs.pk).exists())

    def test_mutations_require_post(self):
        for name in ("resume:update_job_posting", "resume:delete_job_posting"):
            resp = self.client.get(self.reverse(name, args=[self.job.pk]))
            self.assertEqual(resp.status_code, 405, name)

    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(self.reverse("resume:jobs"))
        self.assertEqual(resp.status_code, 302)


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

    def test_a_different_posting_is_a_different_application(self):
        self._match()
        self._match(description="Frontend Engineer at Beta. React, TypeScript, CSS.")
        self.assertEqual(JobPosting.objects.count(), 2)

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


class OneVariantPerApplicationTest(TestCase):
    """Rule 2: tailoring the same job again updates the same variant."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.base = Resume.objects.create(user=self.user, title="Main", content=content())
        self.ctx = {"lang": "en", "active_resume": self.base, "resumes": [], "quota": {}}
        with patch("resume.services.job_service.send_openai_message",
                   return_value=MATCH_JSON):
            self.job_id = agent_tools.get_tool("match_job").handler(
                self.user, self.ctx, description=POSTING
            ).data["job_id"]

    def _tailor(self, name="Ada Tailored"):
        payload = json.dumps({"resume": content(name), "changes_summary": "Reordered"})
        with patch("resume.services.job_service.send_openai_message", return_value=payload):
            return agent_tools.get_tool("tailor_resume_for_job").handler(
                self.user, self.ctx, job_id=self.job_id
            )

    def test_second_run_reuses_the_variant(self):
        first = self._tailor("First Pass")
        second = self._tailor("Second Pass")
        self.assertEqual(first.data["resume_id"], second.data["resume_id"])
        self.assertTrue(first.data["created_new_variant"])
        self.assertFalse(second.data["created_new_variant"])
        self.assertEqual(Resume.objects.filter(user=self.user).count(), 2)

    def test_the_update_is_undoable(self):
        variant_id = self._tailor("First Pass").data["resume_id"]
        self._tailor("Second Pass")
        variant = Resume.objects.get(pk=variant_id)
        self.assertEqual(variant.content["user_info"]["full_name"], "Second Pass")
        self.assertEqual(variant.revisions.count(), 1)
        self.assertEqual(
            variant.revisions.first().content["user_info"]["full_name"], "First Pass"
        )

    def test_the_title_does_not_compound(self):
        self._tailor()
        self._tailor()
        self._tailor()
        variant = Resume.objects.get(derived_kind=Resume.DERIVED_TAILORED)
        self.assertEqual(variant.title, "Main → Senior Python Developer")

    def test_tailoring_always_starts_from_the_base_resume(self):
        """Tailoring a variant of a variant is what made titles pile up."""
        self._tailor()
        variant = Resume.objects.get(derived_kind=Resume.DERIVED_TAILORED)
        self.ctx["active_resume"] = variant
        self._tailor()
        self.assertEqual(
            Resume.objects.filter(derived_kind=Resume.DERIVED_TAILORED).count(), 1
        )
        variant.refresh_from_db()
        self.assertEqual(variant.derived_from_id, self.base.pk)

    def test_the_original_is_untouched(self):
        self._tailor()
        self.base.refresh_from_db()
        self.assertEqual(self.base.content["user_info"]["full_name"], "Ada Lovelace")

    def test_variants_do_not_consume_a_resume_slot(self):
        self._tailor()
        self.assertEqual(
            self.user.resumes.filter(derived_from__isnull=True).count(), 1
        )


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

    def test_it_measures_the_resume_attached_to_the_application(self):
        other = Resume.objects.create(user=self.user, title="Other", content=content())
        posting = JobPosting.objects.get(pk=self.job_id)
        posting.resume = other
        posting.save()
        self.assertEqual(self._rescore(80).data["resume_id"], other.pk)

    def test_unknown_job(self):
        result = agent_tools.get_tool("rescore_job").handler(
            self.user, self.ctx, job_id=99999
        )
        self.assertIn("error", result.data)

    def test_it_is_pro_only(self):
        self.assertTrue(agent_tools.TOOL_REGISTRY["rescore_job"].pro_only)

    def test_it_does_not_need_approval(self):
        """Measuring changes nothing, so stopping to ask would be noise."""
        self.assertFalse(agent_tools.TOOL_REGISTRY["rescore_job"].destructive)


class DerivedResumeQuotaTest(TestCase):
    """Only base resumes consume a slot."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")

    def test_derived_resumes_are_free(self):
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
            Resume.objects.create(
                user=self.user, title=f"{base.title} job", content=content(),
                derived_from=base, derived_kind=Resume.DERIVED_TAILORED,
            )
        self.assertEqual(self.user.resumes.count(), limit * 3)
        self.assertEqual(
            self.user.resumes.filter(derived_from__isnull=True).count(), limit
        )

    def test_family_spans_both_kinds(self):
        base = Resume.objects.create(user=self.user, title="Base", content=content())
        translation = Resume.objects.create(
            user=self.user, title="tr", content=content(),
            derived_from=base, derived_kind=Resume.DERIVED_TRANSLATION,
        )
        tailored = Resume.objects.create(
            user=self.user, title="job", content=content(),
            derived_from=base, derived_kind=Resume.DERIVED_TAILORED,
        )
        self.assertEqual(
            set(base.family().values_list("pk", flat=True)),
            {base.pk, translation.pk, tailored.pk},
        )
        self.assertEqual(
            set(base.language_family().values_list("pk", flat=True)),
            {base.pk, translation.pk},
        )
        self.assertEqual(tailored.root, base)

    def test_deleting_the_base_removes_its_derivatives(self):
        base = Resume.objects.create(user=self.user, title="Base", content=content())
        derived = Resume.objects.create(
            user=self.user, title="d", content=content(),
            derived_from=base, derived_kind=Resume.DERIVED_TAILORED,
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
