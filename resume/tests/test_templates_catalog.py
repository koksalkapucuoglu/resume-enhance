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
from django.utils.html import escape

from resume import resume_templates
from resume.models import Resume
from resume.services import resume_content


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
        self.assertIn('id="tpl-editor-dev-mono"', body)
        # The hidden field is what posts; it must agree with the checked radio.
        self.assertIn('id="selected-template" value="dev-mono"', body)


class FocusAreasTests(TestCase):
    """The optional "what I'm working on" section."""

    def test_lines_survive_being_switched_off(self):
        normalized = resume_content.normalize_focus_areas(
            {"include": False, "items": ["Django", "Kubernetes"]}
        )
        self.assertEqual(normalized["items"], ["Django", "Kubernetes"])
        self.assertFalse(normalized["include"])

    def test_older_shapes_still_load(self):
        self.assertEqual(
            resume_content.normalize_focus_areas(["A", "B"]),
            {"include": True, "items": ["A", "B"]},
        )
        self.assertEqual(
            resume_content.normalize_focus_areas("A\nB")["items"], ["A", "B"]
        )
        self.assertEqual(
            resume_content.normalize_focus_areas(None),
            {"include": False, "items": []},
        )

    def test_context_hides_the_section_unless_included(self):
        content = {"focus_areas": {"include": False, "items": ["Django"]}}
        self.assertEqual(resume_content.build_context(content)["focus_areas"], [])

        content["focus_areas"]["include"] = True
        self.assertEqual(
            resume_content.build_context(content)["focus_areas"], ["Django"]
        )

    def test_every_design_renders_the_section_above_education(self):
        context = dict(SAMPLE_CONTEXT)
        context["focus_areas"] = ["Django ve DRF ile backend mimarisi"]
        for design in resume_templates.catalog():
            with self.subTest(template=design.key):
                html = render_to_string(
                    design.template_file,
                    resume_templates.design_context(design.key, context),
                )
                self.assertIn("Django ve DRF ile backend mimarisi", html)
                if design.layout != "sidebar":
                    # Education lives in the sidebar on two-column designs.
                    self.assertLess(
                        html.index(escape("WHAT I'M WORKING ON")), html.index("EDUCATION")
                    )

    def test_section_is_absent_when_empty(self):
        html = render_to_string(
            "resume_templates/layout_single.html",
            resume_templates.design_context("faangpath-simple", SAMPLE_CONTEXT),
        )
        self.assertNotIn(escape("WHAT I'M WORKING ON"), html)


class AppearanceEndpointTests(TestCase):
    """Changing how a saved resume looks, without opening the editor."""

    def setUp(self):
        self.user = User.objects.create_user("appearance", password="pw")
        self.other = User.objects.create_user("intruder", password="pw")
        self.resume = Resume.objects.create(
            user=self.user,
            title="CV",
            content={"focus_areas": {"include": False, "items": ["Django"]}},
            template_selector="faangpath-simple",
        )
        self.url = reverse("resume:set_resume_appearance", args=[self.resume.pk])
        self.client = Client()
        self.client.force_login(self.user)

    def test_switching_template_persists(self):
        response = self.client.post(self.url, {"template": "dev-mono"})
        self.assertEqual(response.status_code, 200)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.template_selector, "dev-mono")

    def test_unknown_template_is_rejected(self):
        response = self.client.post(self.url, {"template": "no-such"})
        self.assertEqual(response.status_code, 400)
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.template_selector, "faangpath-simple")

    def test_focus_toggle_keeps_the_lines(self):
        response = self.client.post(self.url, {"focus_areas_include": "true"})
        self.assertTrue(response.json()["focus_areas_include"])
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["focus_areas"]["items"], ["Django"])

    def test_another_users_resume_is_not_found(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.url, {"template": "dev-mono"}).status_code, 404)


class EditorFocusAreasTests(TestCase):
    """The editor's textarea and checkbox reach the saved resume."""

    def setUp(self):
        self.user = User.objects.create_user("editor", password="pw")
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.resume = Resume.objects.create(
            user=self.user, title="CV", content={}, template_selector="faangpath-simple"
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("resume:resume_form_edit", args=[self.resume.pk])

    def _payload(self, **overrides):
        data = {
            "form_action": "save_only",
            "export_format": "pdf",
            "template": "faangpath-simple",
            "resume_title": "CV",
            "full_name": "Ada",
            "email": "a@b.com",
            "phone": "",
            "github": "",
            "linkedin": "",
            "skills": "Python",
        }
        for prefix in ("education", "experience", "project"):
            data[f"{prefix}-TOTAL_FORMS"] = "0"
            data[f"{prefix}-INITIAL_FORMS"] = "0"
            data[f"{prefix}-MIN_NUM_FORMS"] = "0"
            data[f"{prefix}-MAX_NUM_FORMS"] = "1000"
        data.update(overrides)
        return data

    def test_saving_stores_lines_and_the_include_flag(self):
        self.client.post(
            self.url,
            self._payload(focus_areas="Django\nAWS", focus_areas_include="on"),
        )
        self.resume.refresh_from_db()
        self.assertEqual(
            self.resume.content["focus_areas"],
            {"include": True, "items": ["Django", "AWS"]},
        )

    def test_unticking_keeps_the_lines(self):
        self.client.post(
            self.url, self._payload(focus_areas="Django\nAWS", focus_areas_include="on")
        )
        self.client.post(self.url, self._payload(focus_areas="Django\nAWS"))
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.content["focus_areas"]["items"], ["Django", "AWS"])
        self.assertFalse(self.resume.content["focus_areas"]["include"])

    def test_the_editor_reloads_what_was_saved(self):
        self.resume.content = {"focus_areas": {"include": True, "items": ["Django"]}}
        self.resume.save(update_fields=["content"])
        body = self.client.get(self.url).content.decode()
        self.assertIn("Django</textarea>", body)
        self.assertIn('id="focus-areas-include"', body)

    def test_the_preview_endpoint_honours_the_checkbox(self):
        url = reverse("resume:preview_resume_form")
        off = self.client.post(url, self._payload(focus_areas="Django")).content.decode()
        self.assertNotIn(escape("WHAT I'M WORKING ON"), off)

        on = self.client.post(
            url, self._payload(focus_areas="Django", focus_areas_include="on")
        ).content.decode()
        self.assertIn(escape("WHAT I'M WORKING ON"), on)
