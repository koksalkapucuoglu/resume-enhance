"""Tests for a resume's written language and its linked translations."""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from resume.models import Resume
from resume.services import agent_tools


def content(name="Ada Lovelace", language="English"):
    return {
        "language": language,
        "user_info": {"full_name": name, "email": "ada@example.com", "skills": ["Python"]},
        "experience": [],
        "education": [],
        "projects_and_publications": [],
    }


class LanguageFieldTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")

    def test_normalize_language_accepts_names_and_codes(self):
        for value in ("English", "english", "en", "Ingilizce"):
            self.assertEqual(Resume.normalize_language(value), "en", value)
        for value in ("Turkish", "Türkçe", "turkce", "tr"):
            self.assertEqual(Resume.normalize_language(value), "tr", value)

    def test_unknown_language_falls_back(self):
        self.assertEqual(Resume.normalize_language("Klingon"), "en")
        self.assertEqual(Resume.normalize_language(None, default="tr"), "tr")

    def test_sync_from_content(self):
        resume = Resume.objects.create(
            user=self.user, title="CV", content=content(language="Turkish")
        )
        self.assertEqual(resume.sync_language_from_content(), "tr")

    def test_default_is_english(self):
        resume = Resume.objects.create(user=self.user, title="CV", content={})
        self.assertEqual(resume.language, "en")


class TranslationFamilyTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.original = Resume.objects.create(
            user=self.user, title="CV", content=content(), language="en"
        )
        self.turkish = Resume.objects.create(
            user=self.user,
            title="CV (TR)",
            content=content(),
            language="tr",
            derived_from=self.original,
            derived_kind=Resume.DERIVED_TRANSLATION,
        )

    def test_root_resolves_for_both(self):
        self.assertEqual(self.original.root, self.original)
        self.assertEqual(self.turkish.root, self.original)

    def test_family_contains_every_version(self):
        for resume in (self.original, self.turkish):
            ids = set(resume.language_family().values_list("pk", flat=True))
            self.assertEqual(ids, {self.original.pk, self.turkish.pk})

    def test_translations_do_not_consume_a_resume_slot(self):
        limit = settings.FREE_TIER_LIMITS["resume_count"]
        # One original + one translation so far
        self.assertTrue(self.user.profile.can_create_resume())
        for i in range(limit - 1):
            Resume.objects.create(user=self.user, title=f"Extra {i}", content=content())
        self.assertFalse(self.user.profile.can_create_resume())
        # Adding another translation is still allowed
        Resume.objects.create(
            user=self.user,
            title="Another TR",
            content=content(),
            language="tr",
            derived_from=self.original,
            derived_kind=Resume.DERIVED_TRANSLATION,
        )
        self.assertFalse(self.user.profile.can_create_resume())
        self.assertEqual(
            self.user.resumes.filter(derived_from__isnull=True).count(), limit
        )

    def test_deleting_the_original_removes_its_translations(self):
        self.original.delete()
        self.assertFalse(Resume.objects.filter(pk=self.turkish.pk).exists())


class TranslatedCopyToolTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(
            user=self.user, title="CV", content=content(), language="en"
        )
        self.ctx = {
            "lang": "en",
            "active_resume": self.resume,
            "resumes": [],
            "quota": {},
        }
        self.tool = agent_tools.get_tool("create_translated_copy")

    def _translated(self):
        """Stand in for the LLM translation, which the service performs in place."""
        def fake(user, params, lang, resume=None):
            target = Resume.objects.get(pk=params["resume_id"])
            target.content = content(name="Ada Lovelace", language="Turkish")
            target.save(update_fields=["content"])
            return {"type": "modify_resume", "resume_id": target.id}

        return patch.object(
            type(agent_tools._service()), "_exec_translate_resume", side_effect=fake
        )

    def test_creates_a_linked_copy_and_keeps_the_original(self):
        with self._translated():
            result = self.tool.handler(self.user, self.ctx, target_language="tr")

        self.assertTrue(result.data["ok"])
        copy = Resume.objects.get(pk=result.data["new_resume_id"])
        self.assertEqual(copy.language, "tr")
        self.assertEqual(copy.derived_from_id, self.resume.pk)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.language, "en")
        self.assertEqual(self.resume.content["user_info"]["full_name"], "Ada Lovelace")

    def test_refuses_a_duplicate_language(self):
        with self._translated():
            self.tool.handler(self.user, self.ctx, target_language="tr")
            second = self.tool.handler(self.user, self.ctx, target_language="tr")
        self.assertIn("error", second.data)
        self.assertIn("existing_resume_id", second.data)

    def test_refuses_translating_into_its_own_language(self):
        result = self.tool.handler(self.user, self.ctx, target_language="en")
        self.assertIn("error", result.data)

    def test_copy_is_attached_to_the_root_not_a_sibling(self):
        with self._translated():
            result = self.tool.handler(self.user, self.ctx, target_language="tr")
        turkish = Resume.objects.get(pk=result.data["new_resume_id"])
        # Translating from the Turkish variant back would attach to the original
        self.assertEqual(turkish.root, self.resume)

    def test_is_destructive_so_the_loop_asks_first(self):
        self.assertTrue(self.tool.destructive)

    def test_list_language_versions(self):
        with self._translated():
            self.tool.handler(self.user, self.ctx, target_language="tr")
        result = agent_tools.get_tool("list_language_versions").handler(
            self.user, self.ctx
        )
        languages = {v["language"] for v in result.data["versions"]}
        self.assertEqual(languages, {"en", "tr"})
        originals = [v for v in result.data["versions"] if v["is_original"]]
        self.assertEqual(len(originals), 1)


class ConversationLanguageTest(TestCase):
    """Confirmations follow the chat language, not the interface setting."""

    def test_approval_copy_matches_the_conversation(self):
        from resume.services import agent_loop

        turkish = agent_loop.approval_copy("tr", "delete_resume")
        english = agent_loop.approval_copy("en", "delete_resume")
        self.assertIn("Devam", turkish["title"])
        self.assertIn("Continue", english["title"])
        self.assertTrue(turkish["action"])
        self.assertNotEqual(turkish["action"], english["action"])

    def test_unknown_tool_still_yields_usable_copy(self):
        from resume.services import agent_loop

        copy = agent_loop.approval_copy("tr", "some_new_tool")
        self.assertTrue(copy["title"])
        self.assertEqual(copy["action"], "")

    def test_every_destructive_tool_has_copy_in_both_languages(self):
        from resume.services import agent_loop

        destructive = [
            name for name, t in agent_tools.TOOL_REGISTRY.items() if t.destructive
        ]
        for lang in ("en", "tr"):
            for name in destructive:
                self.assertTrue(
                    agent_loop.approval_copy(lang, name)["action"],
                    f"{name} has no {lang} description",
                )
