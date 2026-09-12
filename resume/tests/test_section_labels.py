"""
Headings, "Present", the degree joiner and month names follow the language a
resume is written in.

The bug this closes: every design printed English words into Turkish resumes —
"EXPERIENCE", "Present", "Yüksek Lisans of Elektrik Mühendisliği".
"""

from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import translation
from django.utils.html import escape

from resume import resume_templates
from resume.models import Resume
from resume.services.pdf_service import HtmlToPdfConverter, ResumePdfService

CONTEXT = {
    "user_data": {"full_name": "Köksal", "email": "k@example.com", "skills": ["Django"]},
    "education_data": [
        {"school": "İTÜ", "degree": "Yüksek Lisans", "field_of_study": "Elektrik Mühendisliği",
         "start_year": 2019, "end_year": 2022}
    ],
    "experience_data": [
        {"title": "Yazılım Uzmanı", "company": "Gozen", "start_date": date(2022, 8, 1),
         "end_date": date(2025, 1, 1), "current_role": False, "description": ["API"]},
        {"title": "Kıdemli Geliştirici", "company": "Riders AI", "start_date": date(2025, 3, 1),
         "end_date": None, "current_role": True, "description": ["DRF"]},
    ],
    "project_data": [{"name": "Todo", "description": "Takip", "link": ""}],
    "focus_areas": ["Django backend mimarisi"],
}

# Checked as they appear in HTML: labels are autoescaped, so an apostrophe
# would otherwise let an English heading slip past the check.
ENGLISH_WORDS = ("EXPERIENCE", "EDUCATION", "SKILLS", "PROJECTS",
                 escape("WHAT I'M WORKING ON"), "Present", " of Elektrik")


def render(key, language):
    design = resume_templates.get(key)
    with resume_templates.rendering_language(language):
        return render_to_string(
            design.template_file,
            resume_templates.design_context(key, CONTEXT, language),
        )


class LabelsTests(TestCase):
    def test_every_design_prints_turkish_for_a_turkish_resume(self):
        for design in resume_templates.catalog():
            with self.subTest(template=design.key):
                html = render(design.key, "tr")
                for word in ENGLISH_WORDS:
                    self.assertNotIn(word, html, word)
                self.assertIn("DENEYİM", html)
                self.assertIn("Halen", html)
                self.assertIn("ŞU AN ÜZERİNDE ÇALIŞTIKLARIM", html)
                self.assertIn('lang="tr"', html)

    def test_english_is_unchanged(self):
        for design in resume_templates.catalog():
            with self.subTest(template=design.key):
                html = render(design.key, "en")
                self.assertIn("EXPERIENCE", html)
                self.assertIn("Present", html)

    def test_degree_reads_naturally_in_turkish(self):
        html = render("faangpath-simple", "tr")
        self.assertIn("Yüksek Lisans – Elektrik Mühendisliği", html)

    def test_month_names_follow_the_resume_language(self):
        self.assertIn("Ağu 2022", render("faangpath-simple", "tr"))
        self.assertIn("Aug 2022", render("faangpath-simple", "en"))

    def test_rendering_does_not_leak_the_language(self):
        before = translation.get_language()
        render("faangpath-simple", "tr")
        self.assertEqual(translation.get_language(), before)

    def test_an_unsupported_language_falls_back_to_english(self):
        self.assertEqual(resume_templates.language_code("de"), "en")
        self.assertIn("EXPERIENCE", render("faangpath-simple", "de"))

    def test_both_languages_label_the_same_things(self):
        self.assertEqual(
            set(resume_templates.SECTION_LABELS["en"]),
            set(resume_templates.SECTION_LABELS["tr"]),
        )


class PdfLanguageTests(TestCase):
    @patch.object(HtmlToPdfConverter, "convert_template_to_pdf")
    def test_the_pdf_renders_under_the_resume_language(self, convert):
        seen = {}

        def capture(template_name, context, request=None):
            seen["active"] = translation.get_language()
            seen["experience"] = context["labels"]["experience"]
            return b"%PDF-1.7"

        convert.side_effect = capture
        ResumePdfService().generate_resume_pdf(CONTEXT, "faangpath-simple", None, language="tr")
        self.assertEqual(seen, {"active": "tr", "experience": "DENEYİM"})


class ViewLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("dil", password="pw")
        self.client = Client()
        self.client.force_login(self.user)
        self.resume = Resume.objects.create(
            user=self.user, title="CV", language="tr",
            content={"user_info": {"full_name": "Köksal", "skills": ["Django"]},
                     "experience": [{"title": "Geliştirici", "company": "X",
                                     "start_date": "2025-03", "current_role": True,
                                     "description": ["DRF"]}]},
        )

    def test_saved_preview_uses_the_resume_language(self):
        html = self.client.get(
            reverse("resume:preview_saved_resume", args=[self.resume.pk])
        ).content.decode()
        self.assertIn("DENEYİM", html)
        self.assertNotIn("EXPERIENCE", html)

    def test_the_editor_posts_the_resume_language(self):
        html = self.client.get(
            reverse("resume:resume_form_edit", args=[self.resume.pk])
        ).content.decode()
        self.assertIn('name="resume_language" value="tr"', html)

    def _preview(self, language):
        data = {
            "template": "faangpath-simple", "resume_language": language,
            "full_name": "Köksal", "email": "k@example.com", "phone": "",
            "github": "", "linkedin": "", "skills": "Django",
        }
        for prefix in ("education", "experience", "project"):
            data.update({f"{prefix}-TOTAL_FORMS": "0", f"{prefix}-INITIAL_FORMS": "0",
                         f"{prefix}-MIN_NUM_FORMS": "0", f"{prefix}-MAX_NUM_FORMS": "1000"})
        return self.client.post(reverse("resume:preview_resume_form"), data).content.decode()

    def test_the_live_preview_follows_the_posted_language(self):
        self.assertIn("YETENEKLER", self._preview("tr"))
        self.assertIn("SKILLS", self._preview("en"))

    def test_an_unknown_posted_language_is_english(self):
        self.assertIn("SKILLS", self._preview("xx"))
