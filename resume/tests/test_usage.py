"""The free plan is four numbers, and AI actions share one pool."""

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from resume.models import Resume


class AiCreditPoolTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.profile = self.user.profile

    def test_imports_enhancements_and_messages_draw_from_one_pool(self):
        limit = settings.FREE_TIER_LIMITS["ai_credits"]
        self.profile.import_count = 1
        self.profile.enhance_count = limit - 2
        self.profile.agent_message_count = 0
        self.profile.save()
        self.assertTrue(self.profile.can_send_agent_message())

        self.profile.agent_message_count = 1
        self.profile.save()
        self.assertFalse(self.profile.can_import())
        self.assertFalse(self.profile.can_enhance())
        self.assertFalse(self.profile.can_send_agent_message())

    def test_pro_is_never_out_of_credits(self):
        self.profile.tier = "pro"
        self.profile.enhance_count = 10_000
        self.profile.save()
        self.assertTrue(self.profile.has_ai_credit())
        allowances = self.profile.usage()["allowances"]
        self.assertTrue(all(a["limit"] is None for a in allowances.values()))

    def test_usage_lists_the_four_allowances(self):
        Resume.objects.create(user=self.user, title="CV", content={})
        self.profile.download_count = 2
        self.profile.enhance_count = 4
        self.profile.save()
        usage = self.profile.usage()
        self.assertFalse(usage["is_pro"])
        a = usage["allowances"]
        self.assertEqual(set(a), {"ai_credits", "downloads", "resumes", "job_copies"})
        self.assertEqual(a["ai_credits"]["used"], 4)
        self.assertEqual(a["downloads"]["left"], settings.FREE_TIER_LIMITS["download_count"] - 2)
        self.assertEqual(a["resumes"]["used"], 1)
        self.assertTrue(a["ai_credits"]["monthly"])
        self.assertFalse(a["resumes"]["monthly"])

    def test_the_profile_page_shows_the_same_numbers(self):
        self.client.force_login(self.user)
        self.profile.enhance_count = 7
        self.profile.save()
        html = self.client.get("/accounts/profile/").content.decode()
        self.assertIn(f"7/{settings.FREE_TIER_LIMITS['ai_credits']}", html)
        self.assertIn("AI credits", html)
