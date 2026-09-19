"""
Deleting an account from the Profile page.

The privacy policy promises exactly what is removed and what is kept, so these
tests hold the code to that list.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token

from resume.models import Feedback, Resume, ResumeRevision


class AccountDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("leaving", email="l@example.com", password="correct-horse")
        self.resume = Resume.objects.create(user=self.user, title="CV", content={"user_info": {}})
        ResumeRevision.objects.create(resume=self.resume, content={})
        Resume.objects.create(user=self.user, title="CV (TR)", content={}, derived_from=self.resume)
        Token.objects.create(user=self.user)
        self.feedback = Feedback.objects.create(user=self.user, message="Nice tool")
        self.url = reverse("delete_account")
        self.client.force_login(self.user)

    def test_wrong_password_deletes_nothing(self):
        response = self.client.post(self.url, {"password": "wrong"})
        # Back to the deletion card, so the error is next to the form that caused it.
        self.assertRedirects(
            response, f"{reverse('profile')}#delete-account", fetch_redirect_response=False
        )
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(Resume.objects.filter(user=self.user).count(), 2)

    def test_correct_password_removes_everything_the_policy_lists(self):
        user_id = self.user.pk
        response = self.client.post(self.url, {"password": "correct-horse"})
        self.assertRedirects(response, reverse("resume:index"), fetch_redirect_response=False)

        self.assertFalse(User.objects.filter(pk=user_id).exists())
        self.assertFalse(Resume.objects.filter(user_id=user_id).exists())
        self.assertFalse(ResumeRevision.objects.exists())
        self.assertFalse(Token.objects.filter(user_id=user_id).exists())

    def test_feedback_is_kept_without_the_name(self):
        self.client.post(self.url, {"password": "correct-horse"})
        self.feedback.refresh_from_db()
        self.assertIsNone(self.feedback.user)

    def test_the_session_ends(self):
        self.client.post(self.url, {"password": "correct-horse"})
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_get_does_not_delete(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_signed_out_visitors_cannot_use_it(self):
        self.client.logout()
        response = self.client.post(self.url, {"password": "correct-horse"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_the_profile_page_offers_it(self):
        body = self.client.get(reverse("profile")).content.decode()
        self.assertIn(self.url, body)


class JobDataDeletionTests(TestCase):
    def test_postings_evaluations_and_branches_go_with_the_account(self):
        from resume.models import Evaluation, JobPosting

        user = User.objects.create_user("leaver", password="pw-12345")
        base = Resume.objects.create(user=user, title="Main", content={"user_info": {"full_name": "A"}})
        posting = JobPosting.objects.create(user=user, text="x" * 50, fingerprint="f")
        branch = Resume.objects.create(user=user, title="B", content=base.content, derived_from=base,
                                       derived_kind=Resume.DERIVED_JOB, job_posting=posting)
        Evaluation.objects.create(resume=branch, posting=posting, content_hash="h", scorer="s", score=1)
        user.delete()
        self.assertFalse(JobPosting.objects.exists())
        self.assertFalse(Evaluation.objects.exists())
        self.assertFalse(Resume.objects.filter(pk=branch.pk).exists())
