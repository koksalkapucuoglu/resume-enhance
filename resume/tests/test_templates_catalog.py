"""The design catalogue: every entry must be renderable and selectable.

The failure this guards against is a template that exists in one place and not
the others — listed over MCP but unrenderable, or renderable but missing from
the picker — which is what happened while the catalogue lived in three files.
"""

from django.conf import settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse

from resume import resume_templates
from resume.models import Resume


SAMPLE_CONTEXT = {
    "user_data": {
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": "+90 555 000 0000",
        "linkedin": "https://linkedin.com/in/ada",
        "github": "https://github.com/ada",
        "skills": ["Python", "Django", "PostgreSQL"],
    },
    "education_data": [
        {
            "school": "Marmara Üniversitesi",
            "degree": "Lisans",
            "field_of_study": "Elektrik Mühendisliği",
            "start_year": 2013,
            "end_year": 2017,
        }
    ],
    "experience_data": [
        {
            "title": "Kıdemli Backend Geliştirici",
            "company": "Riders AI",
            "start_date": "2025-03",
            "end_date": None,
            "current_role": True,
            "description": ["Django ve DRF ile API'yi optimize ettim."],
        }
    ],
    "project_data": [
        {"name": "Todo Takip", "description": "Bir todo uygulaması.", "link": ""}
    ],
}


class CatalogTests(TestCase):
    def test_settings_map_is_derived_from_the_catalog(self):
        self.assertEqual(
            settings.TEMPLATE_SELECTOR_HTML_MAP,
            {d.key: d.template_file for d in resume_templates.catalog()},
        )

    def test_keys_are_unique(self):
        keys = [d.key for d in resume_templates.catalog()]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_design_belongs_to_an_offered_family(self):
        families = {value for value, _ in resume_templates.FAMILIES}
        for design in resume_templates.catalog():
            self.assertIn(design.family, families, design.key)

    def test_unknown_key_falls_back_to_the_default(self):
        self.assertEqual(
            resume_templates.get("no-such-template").key,
            resume_templates.DEFAULT_TEMPLATE_KEY,
        )

    def test_keys_that_existing_resumes_already_store_still_resolve(self):
        """Stored rows point at these two; losing them would reformat resumes."""
        for key in ("faangpath-simple", "modern-sidebar"):
            self.assertEqual(resume_templates.get(key).key, key)

    def test_tokens_resolve_over_the_base_set(self):
        tokens = resume_templates.resolved_tokens("compact-ats")
        self.assertEqual(tokens["font_size"], "10pt")       # overridden
        self.assertIn("line_height", tokens)                # inherited


class RenderTests(TestCase):
    """Every design renders HTML with the resume's content in it.

    WeasyPrint is not invoked here (the project mocks it in tests); this covers
    the template layer, which is where a broken design actually shows up.
    """

    def test_every_design_renders(self):
        for design in resume_templates.catalog():
            with self.subTest(template=design.key):
                html = render_to_string(
                    design.template_file,
                    resume_templates.design_context(design.key, SAMPLE_CONTEXT),
                )
                self.assertIn("Ada Lovelace", html)
                self.assertIn("Riders AI", html)
                self.assertIn("Todo Takip", html)

    def test_every_design_names_its_font(self):
        """A design left to the renderer's default stops matching its preview."""
        for design in resume_templates.catalog():
            with self.subTest(template=design.key):
                html = render_to_string(
                    design.template_file,
                    resume_templates.design_context(design.key, SAMPLE_CONTEXT),
                )
                self.assertIn("--font-body:", html)
                self.assertIn(design.resolved["font_body"], html)

    def test_pagination_hooks_are_present(self):
        """The preview runtime finds work by data- attribute, not class name."""
        for design in resume_templates.catalog():
            with self.subTest(template=design.key):
                html = render_to_string(
                    design.template_file,
                    resume_templates.design_context(design.key, SAMPLE_CONTEXT),
                )
                self.assertIn("data-paginate=", html)
                self.assertIn("data-column", html)
                self.assertIn("data-item", html)


class PickerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("picker", password="pw")
        self.client = Client()
        self.client.force_login(self.user)

    def test_editor_offers_every_design(self):
        response = self.client.get(reverse("resume:resume_form"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for design in resume_templates.catalog():
            self.assertIn(f'value="{design.key}"', body)
            self.assertIn(design.name, body)

    def test_saved_template_is_preselected(self):
        resume = Resume.objects.create(
            user=self.user,
            title="CV",
            content={},
            template_selector="dev-mono",
        )
        body = self.client.get(
            reverse("resume:resume_form_edit", args=[resume.pk])
        ).content.decode()
        self.assertIn('id="tpl-desktop-dev-mono"', body)
        # The hidden field is what posts; it must agree with the checked radio.
        self.assertIn('id="selected-template" value="dev-mono"', body)
