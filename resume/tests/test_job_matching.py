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
        variant = Resume.objects.get(pk=result.data["new_resume_id"])
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
        self.assertEqual(posting.resume_id, result.data["new_resume_id"])

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
        for _ in range(2):
            self._match()
        other = Resume.objects.create(user=self.user, title="Other", content=content())
        JobPosting.objects.create(user=self.user, title="X", tags=["python"], resume=other)
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
