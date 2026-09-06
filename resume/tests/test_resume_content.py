"""
Tests for the canonical content shape.

These cover the bugs that came from three context builders disagreeing: skills
rendered one character at a time, and dates vanishing from the downloaded PDF
while showing correctly in the preview.
"""

from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from resume.models import Resume
from resume.services import resume_content


class AsListTest(TestCase):
    def test_a_string_becomes_one_entry_per_item_not_per_character(self):
        """`|join` over a string is what produced 'P, y, t, h, o, n'."""
        self.assertEqual(
            resume_content.as_list("Python, Django, Docker"),
            ["Python", "Django", "Docker"],
        )

    def test_newline_separated_string(self):
        self.assertEqual(
            resume_content.as_list("Built APIs\nRan migrations"),
            ["Built APIs", "Ran migrations"],
        )

    def test_a_list_is_left_alone(self):
        self.assertEqual(resume_content.as_list(["A", "B"]), ["A", "B"])

    def test_blanks_are_dropped(self):
        self.assertEqual(resume_content.as_list(["A", "", "  ", None]), ["A"])

    def test_empty_values(self):
        for value in (None, "", []):
            self.assertEqual(resume_content.as_list(value), [])


class ParseDateTest(TestCase):
    def test_year_month(self):
        self.assertEqual(resume_content.parse_date("2022-03"), date(2022, 3, 1))

    def test_full_iso(self):
        self.assertEqual(resume_content.parse_date("2022-03-15"), date(2022, 3, 15))

    def test_year_only(self):
        self.assertEqual(resume_content.parse_date("2022"), date(2022, 1, 1))

    def test_a_date_passes_through(self):
        self.assertEqual(resume_content.parse_date(date(2022, 3, 1)), date(2022, 3, 1))

    def test_unparseable_and_empty(self):
        for value in (None, "", "sometime", "Present"):
            self.assertIsNone(resume_content.parse_date(value))


class NormalizeTest(TestCase):
    def test_skills_string_becomes_a_list(self):
        result = resume_content.normalize(
            {"user_info": {"skills": "Python, Django"}}
        )
        self.assertEqual(result["user_info"]["skills"], ["Python", "Django"])

    def test_experience_description_string_becomes_a_list(self):
        result = resume_content.normalize(
            {"experience": [{"title": "Dev", "description": "Did a thing"}]}
        )
        self.assertEqual(result["experience"][0]["description"], ["Did a thing"])

    def test_education_gets_years_from_dates(self):
        """Imports and the agent store dates; the form editor reads years."""
        result = resume_content.normalize(
            {"education": [{"school": "ITU", "start_date": "2019-09",
                            "end_date": "2022-06"}]}
        )
        entry = result["education"][0]
        self.assertEqual(entry["start_year"], 2019)
        self.assertEqual(entry["end_year"], 2022)

    def test_existing_years_are_kept(self):
        result = resume_content.normalize(
            {"education": [{"school": "ITU", "start_year": 2019, "end_year": 2022}]}
        )
        self.assertEqual(result["education"][0]["start_year"], 2019)

    def test_is_idempotent(self):
        once = resume_content.normalize({"user_info": {"skills": "A, B"}})
        twice = resume_content.normalize(once)
        self.assertEqual(once, twice)

    def test_survives_junk(self):
        result = resume_content.normalize(
            {"experience": ["not a dict", None], "education": None, "user_info": None}
        )
        self.assertEqual(result["experience"], [])
        self.assertEqual(result["education"], [])
        self.assertEqual(result["user_info"]["skills"], [])

    def test_empty_content(self):
        result = resume_content.normalize({})
        self.assertEqual(result["user_info"]["skills"], [])


class BuildContextTest(TestCase):
    def test_dates_become_date_objects_so_the_template_can_format_them(self):
        context = resume_content.build_context(
            {"experience": [{"title": "Dev", "start_date": "2022-01",
                             "end_date": "2023-06"}]}
        )
        entry = context["experience_data"][0]
        self.assertEqual(entry["start_date"], date(2022, 1, 1))
        self.assertEqual(entry["end_date"], date(2023, 6, 1))

    def test_skills_arrive_as_a_list(self):
        context = resume_content.build_context(
            {"user_info": {"skills": "Python, Django"}}
        )
        self.assertEqual(context["user_data"]["skills"], ["Python", "Django"])

    def test_unparseable_dates_become_none_not_a_string(self):
        context = resume_content.build_context(
            {"experience": [{"title": "Dev", "start_date": "", "current_role": True}]}
        )
        self.assertIsNone(context["experience_data"][0]["start_date"])


class RenderedOutputTest(TestCase):
    """The preview and the download must show the same thing."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.resume = Resume.objects.create(
            user=self.user,
            title="CV",
            content={
                # Stored the way the agent and the importers write it
                "user_info": {"full_name": "Ada Lovelace", "email": "ada@example.com",
                              "skills": "Python, Django, Docker"},
                "experience": [{"title": "Backend Engineer", "company": "Acme",
                                "start_date": "2022-01", "end_date": None,
                                "current_role": True, "description": "Built APIs"}],
                "education": [{"school": "ITU", "degree": "Bachelor",
                               "field_of_study": "CS", "start_date": "2016-09",
                               "end_date": "2020-06"}],
                "projects_and_publications": [],
            },
        )
        self.client.force_login(self.user)

    def test_preview_renders_skills_as_words_not_letters(self):
        html = self.client.get(
            reverse("resume:preview_saved_resume", args=[self.resume.pk])
        ).content.decode()
        self.assertIn("Python, Django, Docker", html)
        self.assertNotIn("P, y, t, h, o, n", html)

    def test_preview_shows_dates(self):
        html = self.client.get(
            reverse("resume:preview_saved_resume", args=[self.resume.pk])
        ).content.decode()
        self.assertIn("Jan 2022", html)   # experience
        self.assertIn("2016", html)       # education year derived from a date

    def test_download_builds_the_same_context_as_the_preview(self):
        captured = {}

        def capture(resume_data, template_selector, request):
            captured.update(resume_data)
            return b"%PDF-1.7 ok"

        with patch(
            "resume.views.resume_pdf_service.generate_resume_pdf", side_effect=capture
        ):
            resp = self.client.get(
                reverse("resume:download_resume_pdf", args=[self.resume.pk])
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            captured["user_data"]["skills"], ["Python", "Django", "Docker"]
        )
        self.assertEqual(captured["experience_data"][0]["start_date"], date(2022, 1, 1))
        self.assertEqual(captured["education_data"][0]["start_year"], 2016)

    def test_agent_written_content_stays_openable_by_the_form_editor(self):
        """A resume the agent wrote must not break the standard editor."""
        from resume.services import resume_content as rc

        self.resume.content = rc.normalize(
            {"user_info": {"full_name": "Ada", "email": "a@b.com", "skills": "A, B"},
             "experience": [{"title": "Dev", "company": "X", "start_date": "2022-01",
                             "description": "One thing"}],
             "education": [{"school": "S", "degree": "Bachelor",
                            "field_of_study": "CS", "start_date": "2016-09",
                            "end_date": "2020-06"}],
             "projects_and_publications": []}
        )
        self.resume.save()
        resp = self.client.get(reverse("resume:resume_form_edit", args=[self.resume.pk]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("2016", html)   # education year populated in the form


class FormEditorOpensAgentContentTest(TestCase):
    """
    The editor is a fourth reader of resume content, and it mangled skills the
    same way the download did.
    """

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.resume = Resume.objects.create(
            user=self.user,
            title="CV",
            content={
                "user_info": {"full_name": "Ada", "email": "a@b.com",
                              "skills": "Python, Django, Docker"},
                "experience": [{"title": "Dev", "company": "Acme",
                                "start_date": "2022-01", "current_role": True,
                                "description": "Built APIs"}],
                "education": [{"school": "ITU", "degree": "Bachelor",
                               "field_of_study": "CS", "start_date": "2016-09",
                               "end_date": "2020-06"}],
                "projects_and_publications": [],
            },
        )
        self.client.force_login(self.user)

    def _html(self):
        return self.client.get(
            reverse("resume:resume_form_edit", args=[self.resume.pk])
        ).content.decode()

    def test_skills_are_words_not_letters(self):
        import re

        match = re.search(r'name="skills"[^>]*>(.*?)</textarea>', self._html(), re.S)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), "Python, Django, Docker")

    def test_education_years_are_filled_from_dates(self):
        self.assertIn("2016", self._html())

    def test_the_page_opens(self):
        resp = self.client.get(
            reverse("resume:resume_form_edit", args=[self.resume.pk])
        )
        self.assertEqual(resp.status_code, 200)
