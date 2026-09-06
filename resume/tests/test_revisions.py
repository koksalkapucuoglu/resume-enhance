"""Tests for restore points: snapshot, retention, diff and revert."""

import json

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from resume.models import Resume, ResumeRevision
from resume.services import diff_service, revision_service


def content(name="Ada Lovelace", skills=None, experience=None):
    return {
        "user_info": {
            "full_name": name,
            "email": "ada@example.com",
            "phone": "+90 555 000 0000",
            "skills": skills if skills is not None else ["Python", "Django"],
        },
        "experience": experience
        if experience is not None
        else [
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "start_date": "2023-01",
                "end_date": None,
                "current_role": True,
                "description": ["Built the API"],
            }
        ],
        "education": [],
        "projects_and_publications": [],
    }


class RevisionServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(
            user=self.user, title="CV", content=content()
        )

    def test_snapshot_stores_state_before_change(self):
        revision_service.snapshot(self.resume, ResumeRevision.SOURCE_MANUAL)
        self.resume.content = content(name="Grace Hopper")
        self.resume.save()

        revision = self.resume.revisions.first()
        self.assertEqual(revision.content["user_info"]["full_name"], "Ada Lovelace")
        self.assertEqual(self.resume.content["user_info"]["full_name"], "Grace Hopper")

    def test_snapshot_captures_template_selector(self):
        self.resume.template_selector = "modern-sidebar"
        self.resume.save()
        revision_service.snapshot(self.resume, ResumeRevision.SOURCE_AGENT)
        self.assertEqual(
            self.resume.revisions.first().template_selector, "modern-sidebar"
        )

    def test_snapshot_is_deep_copied(self):
        revision_service.snapshot(self.resume, ResumeRevision.SOURCE_MANUAL)
        self.resume.content["user_info"]["skills"].append("Rust")
        self.assertNotIn("Rust", self.resume.revisions.first().content["user_info"]["skills"])

    def test_snapshot_skipped_for_empty_content(self):
        blank = Resume.objects.create(user=self.user, title="Blank", content={})
        self.assertIsNone(revision_service.snapshot(blank, ResumeRevision.SOURCE_MANUAL))
        self.assertEqual(blank.revisions.count(), 0)

    def test_free_retention_prunes_oldest(self):
        limit = settings.FREE_TIER_LIMITS["revision_history"]
        for i in range(limit + 3):
            self.resume.content = content(name=f"Name {i}")
            revision_service.snapshot(self.resume, ResumeRevision.SOURCE_MANUAL)

        self.assertEqual(self.resume.revisions.count(), limit)
        # The survivors are the newest ones
        names = [r.content["user_info"]["full_name"] for r in self.resume.revisions.all()]
        self.assertEqual(names[0], f"Name {limit + 2}")

    def test_pro_retention_is_unlimited(self):
        self.user.profile.tier = "pro"
        self.user.profile.save()
        limit = settings.FREE_TIER_LIMITS["revision_history"]
        for i in range(limit + 3):
            self.resume.content = content(name=f"Name {i}")
            revision_service.snapshot(self.resume, ResumeRevision.SOURCE_MANUAL)
        self.assertEqual(self.resume.revisions.count(), limit + 3)

    def test_restore_rolls_back_content_and_template(self):
        revision = revision_service.snapshot(self.resume, ResumeRevision.SOURCE_AGENT)
        self.resume.content = content(name="Grace Hopper")
        self.resume.template_selector = "modern-sidebar"
        self.resume.save()

        revision_service.restore(self.resume, revision)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["user_info"]["full_name"], "Ada Lovelace")
        self.assertEqual(self.resume.template_selector, "faangpath-simple")

    def test_restore_is_itself_undoable(self):
        revision = revision_service.snapshot(self.resume, ResumeRevision.SOURCE_AGENT)
        self.resume.content = content(name="Grace Hopper")
        self.resume.save()

        undo_point = revision_service.restore(self.resume, revision)
        self.assertEqual(undo_point.source, ResumeRevision.SOURCE_REVERT)
        self.assertEqual(undo_point.content["user_info"]["full_name"], "Grace Hopper")

        revision_service.restore(self.resume, undo_point)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["user_info"]["full_name"], "Grace Hopper")

    def test_restore_rejects_foreign_revision(self):
        other = Resume.objects.create(user=self.user, title="Other", content=content())
        foreign = revision_service.snapshot(other, ResumeRevision.SOURCE_MANUAL)
        with self.assertRaises(ValueError):
            revision_service.restore(self.resume, foreign)


class DiffServiceTest(TestCase):
    def test_no_changes(self):
        self.assertEqual(diff_service.diff_resume_content(content(), content()), [])
        self.assertEqual(diff_service.summarize([]), "No changes")

    def test_scalar_field_change(self):
        changes = diff_service.diff_resume_content(content(), content(name="Grace Hopper"))
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["field"], "Full Name")
        self.assertEqual(changes[0]["kind"], "changed")
        self.assertEqual(changes[0]["before"], "Ada Lovelace")
        self.assertEqual(changes[0]["after"], "Grace Hopper")

    def test_skill_added_and_removed(self):
        changes = diff_service.diff_resume_content(
            content(skills=["Python", "Django"]), content(skills=["Python", "AWS"])
        )
        kinds = {(c["field"], c["kind"]) for c in changes}
        self.assertIn(("AWS", "added"), kinds)
        self.assertIn(("Django", "removed"), kinds)

    def test_experience_description_change(self):
        after = content(
            experience=[
                {
                    "title": "Backend Engineer",
                    "company": "Acme",
                    "start_date": "2023-01",
                    "end_date": None,
                    "current_role": True,
                    "description": ["Built the API", "Scaled it to 1M users"],
                }
            ]
        )
        changes = diff_service.diff_resume_content(content(), after)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["section"], "Experience")
        self.assertEqual(changes[0]["field"], "Description")
        self.assertIn("Scaled it", changes[0]["after"])

    def test_experience_added_and_removed(self):
        added = diff_service.diff_resume_content(
            content(experience=[]), content()
        )
        self.assertEqual(added[0]["kind"], "added")
        self.assertEqual(added[0]["item"], "Backend Engineer · Acme")

        removed = diff_service.diff_resume_content(content(), content(experience=[]))
        self.assertEqual(removed[0]["kind"], "removed")

    def test_reordering_experience_is_not_a_change(self):
        first = {
            "title": "Backend Engineer", "company": "Acme", "start_date": "2023-01",
            "end_date": None, "current_role": True, "description": ["A"],
        }
        second = {
            "title": "Intern", "company": "Beta", "start_date": "2020-01",
            "end_date": "2021-01", "current_role": False, "description": ["B"],
        }
        before = content(experience=[first, second])
        after = content(experience=[second, first])
        self.assertEqual(diff_service.diff_resume_content(before, after), [])

    def test_summarize_counts(self):
        changes = diff_service.diff_resume_content(
            content(skills=["Python"]), content(skills=["Python", "AWS"], name="Grace Hopper")
        )
        self.assertEqual(diff_service.summarize(changes), "1 added, 1 changed")


class RevisionEndpointTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.other = User.objects.create_user("eve", password="x")
        self.resume = Resume.objects.create(
            user=self.user, title="CV", content=content()
        )
        self.client.force_login(self.user)

    def _snapshot_and_change(self):
        revision = revision_service.snapshot(self.resume, ResumeRevision.SOURCE_AGENT)
        self.resume.content = content(name="Grace Hopper")
        self.resume.save()
        return revision

    def test_list_revisions(self):
        self._snapshot_and_change()
        resp = self.client.get(
            reverse("resume:resume_revisions", args=[self.resume.pk])
        )
        body = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(body["revisions"]), 1)
        self.assertEqual(body["revisions"][0]["source"], "agent")
        self.assertEqual(body["retention"], settings.FREE_TIER_LIMITS["revision_history"])

    def test_diff_endpoint(self):
        revision = self._snapshot_and_change()
        resp = self.client.get(
            reverse("resume:resume_revision_diff", args=[self.resume.pk, revision.pk])
        )
        body = resp.json()
        self.assertEqual(body["summary"], "1 changed")
        self.assertEqual(body["changes"][0]["after"], "Grace Hopper")

    def test_latest_diff_endpoint(self):
        self._snapshot_and_change()
        resp = self.client.get(
            reverse("resume:resume_latest_diff", args=[self.resume.pk])
        )
        self.assertEqual(resp.json()["changes"][0]["field"], "Full Name")

    def test_latest_diff_without_revisions(self):
        resp = self.client.get(
            reverse("resume:resume_latest_diff", args=[self.resume.pk])
        )
        self.assertIsNone(resp.json()["revision_id"])

    def test_revert_endpoint(self):
        revision = self._snapshot_and_change()
        resp = self.client.post(
            reverse("resume:revert_resume", args=[self.resume.pk, revision.pk])
        )
        self.assertEqual(resp.status_code, 200)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["user_info"]["full_name"], "Ada Lovelace")
        self.assertIsNotNone(resp.json()["undo_revision_id"])

    def test_revert_requires_post(self):
        revision = self._snapshot_and_change()
        resp = self.client.get(
            reverse("resume:revert_resume", args=[self.resume.pk, revision.pk])
        )
        self.assertEqual(resp.status_code, 405)

    def test_other_users_resume_is_not_reachable(self):
        foreign = Resume.objects.create(
            user=self.other, title="Eve CV", content=content()
        )
        revision = revision_service.snapshot(foreign, ResumeRevision.SOURCE_MANUAL)
        for url in (
            reverse("resume:resume_revisions", args=[foreign.pk]),
            reverse("resume:resume_revision_diff", args=[foreign.pk, revision.pk]),
            reverse("resume:resume_latest_diff", args=[foreign.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(
            self.client.post(
                reverse("resume:revert_resume", args=[foreign.pk, revision.pk])
            ).status_code,
            404,
        )

    def test_revision_of_another_resume_is_rejected(self):
        mine = Resume.objects.create(user=self.user, title="Second", content=content())
        foreign_revision = revision_service.snapshot(mine, ResumeRevision.SOURCE_MANUAL)
        resp = self.client.post(
            reverse("resume:revert_resume", args=[self.resume.pk, foreign_revision.pk])
        )
        self.assertEqual(resp.status_code, 404)

    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(
            reverse("resume:resume_revisions", args=[self.resume.pk])
        )
        self.assertEqual(resp.status_code, 302)


class ManualSaveSnapshotTest(TestCase):
    """The editor's save path must leave a restore point behind."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(
            user=self.user, title="CV", content=content()
        )
        self.client.force_login(self.user)

    def test_saving_the_form_records_a_revision(self):
        payload = {
            "form_action": "save_only",
            "template": "faangpath-simple",
            "resume_title": "CV",
            "full_name": "Grace Hopper",
            "email": "grace@example.com",
            "phone": "+90 555 111 2233",
            "github": "",
            "linkedin": "",
            "skills": "Python, COBOL",
            "education-TOTAL_FORMS": "0", "education-INITIAL_FORMS": "0",
            "education-MIN_NUM_FORMS": "0", "education-MAX_NUM_FORMS": "1000",
            "experience-TOTAL_FORMS": "0", "experience-INITIAL_FORMS": "0",
            "experience-MIN_NUM_FORMS": "0", "experience-MAX_NUM_FORMS": "1000",
            "project-TOTAL_FORMS": "0", "project-INITIAL_FORMS": "0",
            "project-MIN_NUM_FORMS": "0", "project-MAX_NUM_FORMS": "1000",
        }
        self.client.post(
            reverse("resume:resume_form_edit", args=[self.resume.pk]),
            payload,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        revision = self.resume.revisions.first()
        self.assertIsNotNone(revision)
        self.assertEqual(revision.source, ResumeRevision.SOURCE_MANUAL)
        self.assertEqual(revision.content["user_info"]["full_name"], "Ada Lovelace")


class DiffCopyTest(TestCase):
    """Diff wording follows whoever is asking, in their language."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(user=self.user, title="CV", content=content())
        revision_service.snapshot(self.resume, ResumeRevision.SOURCE_AGENT)
        self.resume.content = content(name="Grace Hopper")
        self.resume.save()
        self.client.force_login(self.user)

    def test_default_follows_the_interface_language(self):
        self.user.profile.ui_language = "tr"
        self.user.profile.save()
        resp = self.client.get(
            reverse("resume:resume_latest_diff", args=[self.resume.pk])
        )
        self.assertEqual(resp.json()["copy"]["before"], "Önce")

    def test_lang_parameter_wins(self):
        """The agentic panel passes ?lang= because it follows the conversation."""
        resp = self.client.get(
            reverse("resume:resume_latest_diff", args=[self.resume.pk]) + "?lang=tr"
        )
        copy = resp.json()["copy"]
        self.assertEqual(copy["before"], "Önce")
        self.assertEqual(copy["restore"], "Geri yükle")

    def test_unknown_language_falls_back_to_english(self):
        resp = self.client.get(
            reverse("resume:resume_latest_diff", args=[self.resume.pk]) + "?lang=klingon"
        )
        self.assertEqual(resp.json()["copy"]["before"], "Before")

    def test_revision_diff_endpoint_carries_copy_too(self):
        revision = self.resume.revisions.first()
        resp = self.client.get(
            reverse("resume:resume_revision_diff", args=[self.resume.pk, revision.pk])
            + "?lang=tr"
        )
        self.assertEqual(resp.json()["copy"]["changed"], "değişti")

    def test_empty_history_still_returns_copy(self):
        blank = Resume.objects.create(user=self.user, title="Blank", content=content())
        resp = self.client.get(
            reverse("resume:resume_latest_diff", args=[blank.pk]) + "?lang=tr"
        )
        body = resp.json()
        self.assertIsNone(body["revision_id"])
        self.assertEqual(body["summary"], "Değişiklik yok")

    def test_every_key_exists_in_both_languages(self):
        from resume.services import diff_service

        self.assertEqual(
            set(diff_service.DIFF_COPY["en"]), set(diff_service.DIFF_COPY["tr"])
        )
