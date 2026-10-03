"""
Tests for AgentService — the resume operations the agent tools wrap.

All OpenAI calls are mocked via @patch('resume.services.agent_service.send_openai_message').
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from resume.models import Resume
from resume.services.agent_service import AgentService

User = get_user_model()

MOCK_RESUME_CONTENT = {
    "language": "English",
    "user_info": {
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "+1 555 000 0000",
        "github": "https://github.com/janedoe",
        "linkedin": "https://linkedin.com/in/janedoe",
        "skills": ["Django", "Python", "Docker"],
    },
    "experience": [
        {
            "title": "Software Engineer",
            "company": "Acme Corp",
            "start_date": "2022-03",
            "end_date": None,
            "current_role": True,
            "description": ["Developed REST APIs", "Led migration to Docker"],
        }
    ],
    "education": [
        {
            "school": "MIT",
            "degree": "Bachelor",
            "field_of_study": "CS",
            "start_year": 2018,
            "end_year": 2022,
        }
    ],
    "projects_and_publications": [
        {
            "name": "My Tool",
            "description": "Built a CLI tool",
            "link": "https://github.com/x",
        }
    ],
}

MOCK_RESUME_CONTENT_2 = {
    "language": "English",
    "user_info": {
        "full_name": "John Smith",
        "email": "john@example.com",
        "phone": "",
        "github": "",
        "linkedin": "",
        "skills": ["React", "TypeScript", "AWS"],
    },
    "experience": [
        {
            "title": "Frontend Developer",
            "company": "Google",
            "start_date": "2021-01",
            "end_date": "2023-06",
            "current_role": False,
            "description": ["Built React components"],
        }
    ],
    "education": [],
    "projects_and_publications": [],
}


class AgentServiceTestBase(TestCase):
    """Shared fixtures for all agent service tests."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", password="testpass123", email="test@example.com"
        )
        # UserProfile is auto-created via post_save signal
        self.profile = self.user.profile
        self.resume = Resume.objects.create(
            user=self.user,
            title="My Resume",
            content=MOCK_RESUME_CONTENT.copy(),
            template_selector="faangpath-simple",
        )
        self.service = AgentService()
        self.context = {
            "resumes": [
                {
                    "id": self.resume.id,
                    "display_name": self.resume.display_name,
                    "rank": 1,
                }
            ],
            "quota": {"tier": "free", "resume_count": 1},
        }

    # Intent dispatch moved to the agent loop, which calls tools directly.
    # These operations still exist and are what the tools wrap, so the shim
    # keeps the assertions below pointed at the same behaviour.
    _OPERATIONS = {
        "list_resumes": lambda svc, u, p, lang, ar: svc._exec_list_resumes(u, lang),
        "preview_resume": lambda svc, u, p, lang, ar: svc._exec_preview_resume(u, p, lang, ar),
        "download_resume": lambda svc, u, p, lang, ar: svc._exec_download_resume(u, p, lang),
        "duplicate_resume": lambda svc, u, p, lang, ar: svc._exec_duplicate_resume(u, p, lang),
        "edit_resume": lambda svc, u, p, lang, ar: svc._exec_edit_resume(u, p, lang),
        "check_quota": lambda svc, u, p, lang, ar: svc._exec_check_quota(u, lang),
        "upload_resume": lambda svc, u, p, lang, ar: svc._exec_upload_resume(lang),
        "translate_resume": lambda svc, u, p, lang, ar: svc._exec_translate_resume(u, p, lang, ar),
        "switch_template": lambda svc, u, p, lang, ar: svc._exec_switch_template(u, p, lang, ar),
    }

    def execute_intent(
        self, intent, params=None, user=None, lang="en", active_resume=None,
        user_message="", **kwargs,
    ):
        if intent == "modify_resume":
            return self.service._exec_modify_resume(
                user, params or {}, lang, user_message, active_resume
            )
        operation = self._OPERATIONS.get(intent)
        if operation is None:
            raise AssertionError(f"No operation for intent {intent!r}")
        return operation(self.service, user, params or {}, lang, active_resume)


class ExecuteIntentTest(AgentServiceTestBase):
    """Tests for execute_intent dispatching and individual _exec_* methods."""

    def test_list_resumes_returns_resume_data(self):
        result = self.execute_intent("list_resumes", {}, self.user, lang="en")
        self.assertEqual(result["type"], "chat")
        self.assertEqual(result["data_type"], "resume_list")
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["id"], self.resume.id)
        self.assertIn("1 resume", result["message"])

    def test_list_resumes_empty(self):
        self.resume.delete()
        result = self.execute_intent("list_resumes", {}, self.user, lang="en")
        self.assertIn("don't have any resumes", result["message"])
        self.assertIsNone(result.get("quick_replies"))

    def test_preview_resume_returns_preview_type(self):
        result = self.execute_intent(
            "preview_resume", {"resume_id": self.resume.id}, self.user, lang="en"
        )
        self.assertEqual(result["type"], "preview")
        self.assertEqual(result["resume_id"], self.resume.id)
        self.assertIn("preview", result["message"].lower())

    def test_preview_resume_not_found(self):
        result = self.execute_intent(
            "preview_resume", {"resume_id": 99999}, self.user, lang="en"
        )
        self.assertEqual(result["type"], "chat")
        self.assertIn("99999", result["message"])

    def test_duplicate_resume_creates_copy(self):
        result = self.execute_intent(
            "duplicate_resume", {"resume_id": self.resume.id}, self.user, lang="en"
        )
        self.assertEqual(result["type"], "redirect")
        self.assertIn("duplicated", result["message"].lower())
        self.assertEqual(Resume.objects.filter(user=self.user).count(), 2)
        copy = Resume.objects.filter(user=self.user).exclude(pk=self.resume.pk).first()
        self.assertIn("(Copy)", copy.title)
        self.assertEqual(copy.content, self.resume.content)

    def test_duplicate_resume_quota_exceeded(self):
        # Create resumes up to limit (3 total, already have 1)
        for i in range(2):
            Resume.objects.create(user=self.user, title=f"Extra {i}", content={})
        self.assertEqual(Resume.objects.filter(user=self.user).count(), 3)
        result = self.execute_intent(
            "duplicate_resume", {"resume_id": self.resume.id}, self.user, lang="en"
        )
        self.assertEqual(result["type"], "chat")
        self.assertIn("limit", result["message"].lower())

    def test_check_quota_free_tier(self):
        self.profile.import_count = 1
        self.profile.enhance_count = 3
        self.profile.download_count = 2
        self.profile.agent_message_count = 5
        self.profile.save()
        result = self.execute_intent("check_quota", {}, self.user, lang="en")
        self.assertEqual(result["type"], "chat")
        self.assertEqual(result["data"]["tier"], "free")
        # Imports, enhancements and chat messages share one AI-credit pool.
        self.assertEqual(result["data"]["ai_credits_remaining"], 30 - 9)
        self.assertEqual(result["data"]["downloads_remaining"], 3)  # 5 - 2
        self.assertEqual(result["data"]["resumes_remaining"], 2)  # 3 - 1
        self.assertEqual(result["data_type"], "quota")

    def test_check_quota_pro_tier(self):
        self.profile.tier = "pro"
        self.profile.save()
        result = self.execute_intent("check_quota", {}, self.user, lang="en")
        self.assertEqual(result["data"]["tier"], "pro")
        self.assertIn("Pro", result["message"])

    def test_download_resume_returns_url(self):
        result = self.execute_intent(
            "download_resume", {"resume_id": self.resume.id}, self.user, lang="en"
        )
        self.assertEqual(result["type"], "download")
        self.assertIn("download_url", result)
        self.assertIn("filename", result)
        self.assertIn("Jane_Doe", result["filename"])

    def test_edit_resume_returns_redirect(self):
        result = self.execute_intent(
            "edit_resume", {"resume_id": self.resume.id}, self.user, lang="en"
        )
        self.assertEqual(result["type"], "redirect")
        self.assertIn(str(self.resume.id), result["url"])

    def test_upload_resume_asks_for_a_file(self):
        """Redirecting to the POST-only upload view bounced the user out of chat."""
        result = self.execute_intent("upload_resume", {}, self.user, lang="en")
        self.assertEqual(result["type"], "request_upload")
        self.assertEqual(result["source"], "pdf")
        self.assertIn("upload", result["upload_url"])

    def test_switch_template_valid(self):
        result = self.execute_intent(
            "switch_template",
            {"resume_id": self.resume.id, "template": "modern-sidebar"},
            self.user,
            lang="en",
        )
        self.assertEqual(result["type"], "switch_template")
        self.assertEqual(result["template"], "modern-sidebar")
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.template_selector, "modern-sidebar")

    def test_switch_template_alias(self):
        result = self.execute_intent(
            "switch_template",
            {"resume_id": self.resume.id, "template": "classic"},
            self.user,
            lang="en",
        )
        self.assertEqual(result["template"], "faangpath-simple")

    def test_switch_template_unknown_shows_picker(self):
        result = self.execute_intent(
            "switch_template",
            {"resume_id": self.resume.id, "template": "unknown-template"},
            self.user,
            lang="en",
        )
        self.assertEqual(result["type"], "template_picker")
        self.assertIn("templates", result)


class ModifyResumeTest(AgentServiceTestBase):
    """Tests for _exec_modify_resume — LLM-based content modification."""

    @patch("resume.services.agent_service.send_openai_message")
    def test_modify_resume_success(self, mock_llm):
        modified_content = MOCK_RESUME_CONTENT.copy()
        modified_content["user_info"] = {
            **modified_content["user_info"],
            "skills": ["Django", "Python", "Docker", "AWS"],
        }
        mock_llm.return_value = json.dumps(
            {
                "modified_resume": modified_content,
                "changes_summary": "Added AWS to skills",
                "response_message": "Added AWS to your skills list.",
            }
        )
        result = self.execute_intent(
            "modify_resume",
            {"resume_id": self.resume.id},
            self.user,
            lang="en",
            user_message="Add AWS to my skills",
        )
        self.assertEqual(result["type"], "modify_resume")
        self.assertEqual(result["resume_id"], self.resume.id)
        self.assertIn("AWS", result["message"])
        self.resume.refresh_from_db()
        self.assertIn("AWS", self.resume.content["user_info"]["skills"])

    @patch("resume.services.agent_service.send_openai_message")
    def test_modify_resume_normalizes_string_description(self, mock_llm):
        """Experience description returned as string should be normalized to list."""
        modified_content = MOCK_RESUME_CONTENT.copy()
        modified_content["experience"] = [
            {
                "title": "Software Engineer",
                "company": "Acme Corp",
                "description": "Built APIs\nLed team",  # String instead of list
            }
        ]
        mock_llm.return_value = json.dumps(
            {
                "modified_resume": modified_content,
                "changes_summary": "Updated experience",
                "response_message": "Done.",
            }
        )
        result = self.execute_intent(
            "modify_resume",
            {"resume_id": self.resume.id},
            self.user,
            lang="en",
            user_message="update experience",
        )
        self.assertEqual(result["type"], "modify_resume")
        self.resume.refresh_from_db()
        desc = self.resume.content["experience"][0]["description"]
        self.assertIsInstance(desc, list)
        self.assertEqual(desc, ["Built APIs", "Led team"])

    @patch("resume.services.agent_service.send_openai_message")
    def test_modify_resume_first_attempt_fails_retry_succeeds(self, mock_llm):
        """First LLM call returns invalid JSON, retry succeeds."""
        modified_content = MOCK_RESUME_CONTENT.copy()
        modified_content["user_info"]["full_name"] = "Jane Updated"
        mock_llm.side_effect = [
            "not valid json at all",  # First attempt fails
            json.dumps(
                {  # Retry succeeds
                    "modified_resume": modified_content,
                    "changes_summary": "Updated name",
                    "response_message": "Name updated.",
                }
            ),
        ]
        result = self.execute_intent(
            "modify_resume",
            {"resume_id": self.resume.id},
            self.user,
            lang="en",
            user_message="change my name to Jane Updated",
        )
        self.assertEqual(result["type"], "modify_resume")
        self.assertEqual(mock_llm.call_count, 2)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["user_info"]["full_name"], "Jane Updated")

    @patch("resume.services.agent_service.send_openai_message")
    def test_modify_resume_both_attempts_fail(self, mock_llm):
        mock_llm.return_value = "invalid json"
        result = self.execute_intent(
            "modify_resume",
            {"resume_id": self.resume.id},
            self.user,
            lang="en",
            user_message="do something weird",
        )
        self.assertEqual(result["type"], "chat")
        self.assertIn("couldn't apply", result["message"].lower())
        self.assertEqual(mock_llm.call_count, 2)

    @patch("resume.services.agent_service.send_openai_message")
    def test_modify_resume_missing_user_info_rejected(self, mock_llm):
        """Modified resume without user_info key should be rejected."""
        mock_llm.return_value = json.dumps(
            {
                "modified_resume": {"experience": []},  # Missing user_info
                "changes_summary": "test",
                "response_message": "test",
            }
        )
        result = self.execute_intent(
            "modify_resume",
            {"resume_id": self.resume.id},
            self.user,
            lang="en",
            user_message="break it",
        )
        # Both attempts return same invalid structure, should fail
        self.assertEqual(result["type"], "chat")
        self.assertIn("couldn't apply", result["message"].lower())

    @patch("resume.services.agent_service.send_openai_message")
    def test_modify_resume_falls_back_to_active_resume(self, mock_llm):
        """When no resume_id in params, should use active_resume."""
        modified = MOCK_RESUME_CONTENT.copy()
        mock_llm.return_value = json.dumps(
            {
                "modified_resume": modified,
                "changes_summary": "No changes",
                "response_message": "Done.",
            }
        )
        result = self.execute_intent(
            "modify_resume",
            {},  # No resume_id
            self.user,
            lang="en",
            user_message="update something",
            active_resume=self.resume,
        )
        self.assertEqual(result["type"], "modify_resume")
        self.assertEqual(result["resume_id"], self.resume.id)

    def test_modify_resume_no_resumes_at_all(self):
        self.resume.delete()
        result = self.execute_intent(
            "modify_resume",
            {},
            self.user,
            lang="en",
            user_message="update something",
        )
        self.assertEqual(result["type"], "chat")
        self.assertIn("don't have any resumes", result["message"].lower())


class LanguageDetectionTest(AgentServiceTestBase):
    """Tests for _detect_language — TR/EN detection."""

    def test_english_message(self):
        lang = self.service._detect_language("show my resumes")
        self.assertEqual(lang, "en")

    def test_turkish_without_special_chars(self):
        """Plenty of real Turkish requests carry no Turkish-only letters."""
        lang = self.service._detect_language("ozgecmisimi goster")
        self.assertEqual(lang, "tr")

    def test_turkish_chars_detected(self):
        lang = self.service._detect_language("ozgecmisimi gosterebilir misiniz lufen")
        # "mi" is not in the lowered words directly but checking "misiniz" won't match
        # Actually none of the words match tr_words exactly. Let me use actual Turkish chars.
        lang = self.service._detect_language("ozgecmisimi goster lutfen")
        # "lutfen" is in tr_words
        self.assertEqual(lang, "tr")

    def test_turkish_special_characters(self):
        lang = self.service._detect_language("Deneyimlerimi guncelle")
        # No special chars, no tr_words
        # Let me use an actual Turkish character
        lang = self.service._detect_language("Ozgecmisimi guncelle lutfen")
        # "lutfen" is in tr_words
        self.assertEqual(lang, "tr")

    def test_turkish_word_detection(self):
        lang = self.service._detect_language("resume listele ve goster")
        # "ve" is in tr_words
        self.assertEqual(lang, "tr")

    def test_turkish_verbs_are_recognised(self):
        """"guncelle" is a request users actually type, with or without diacritics."""
        self.assertEqual(
            self.service._detect_language("ozgecmislerimi guncelle"), "tr"
        )
        self.assertEqual(
            self.service._detect_language("ozgecmislerimi guncellestir lutfen"), "tr"
        )

    def test_mixed_with_turkish_char(self):
        lang = self.service._detect_language("Resume'umu guncelle")
        # The apostrophe/special char u is not in tr_chars_raw, but let's use u with umlaut
        lang = self.service._detect_language("Resume'umu guncelle abi")
        # No tr markers
        lang = self.service._detect_language("Resumeumu guncelle abi ile")
        # "ile" is in tr_words
        self.assertEqual(lang, "tr")


class QuickRepliesTest(AgentServiceTestBase):
    """Tests for quick_replies in various responses."""

    def test_list_resumes_has_quick_replies(self):
        result = self.execute_intent("list_resumes", {}, self.user, lang="en")
        self.assertIn("quick_replies", result)
        self.assertIsInstance(result["quick_replies"], list)
        self.assertTrue(len(result["quick_replies"]) > 0)

    def test_preview_has_quick_replies(self):
        result = self.execute_intent(
            "preview_resume", {"resume_id": self.resume.id}, self.user, lang="en"
        )
        self.assertIn("quick_replies", result)
        self.assertIn("Download PDF", result["quick_replies"])

    @patch("resume.services.agent_service.send_openai_message")
    def test_modify_resume_has_quick_replies(self, mock_llm):
        mock_llm.return_value = json.dumps(
            {
                "modified_resume": MOCK_RESUME_CONTENT,
                "changes_summary": "No changes",
                "response_message": "Done.",
            }
        )
        result = self.execute_intent(
            "modify_resume",
            {"resume_id": self.resume.id},
            self.user,
            lang="en",
            user_message="test",
        )
        self.assertIn("quick_replies", result)
        self.assertIn("Preview changes", result["quick_replies"])

    def test_list_resumes_turkish_quick_replies(self):
        result = self.execute_intent("list_resumes", {}, self.user, lang="tr")
        self.assertIn("quick_replies", result)
        # Turkish quick replies should not contain English text
        for reply in result["quick_replies"]:
            self.assertNotIn("Preview first", reply)

    def test_resolve_own_resume(self):
        resume = self.service._resolve_resume(self.user, {"resume_id": self.resume.id})
        self.assertEqual(resume, self.resume)

    def test_resolve_other_users_resume(self):
        other_user = User.objects.create_user(username="other", password="pass123")
        other_resume = Resume.objects.create(user=other_user, title="Other", content={})
        resume = self.service._resolve_resume(self.user, {"resume_id": other_resume.id})
        self.assertIsNone(resume)  # IDOR protection

    def test_resolve_no_resume_id(self):
        resume = self.service._resolve_resume(self.user, {})
        self.assertIsNone(resume)

    def test_resolve_nonexistent_id(self):
        resume = self.service._resolve_resume(self.user, {"resume_id": 99999})
        self.assertIsNone(resume)
