"""
Imports are held to our shape and checked against the file they came from.

Jev is always mocked: `ask` is patched to answer from a table keyed by the
value each question is about, so a test reads as "Jev says this bullet is not
in the document".
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from resume.models import Resume
from resume.services import import_check, resume_content
from resume.typesafe_engine import Answers, ChoiceResult

SOURCE = """
Ada Lovelace — ada@example.com — +44 20 7946 0958
linkedin.com/in/adalovelace
Skills: Python, Django, PostgreSQL
Senior Engineer, Analytical Engines Ltd, 2019 – present
Built the scheduling service in Django.
Education: University of London, BSc Mathematics, 2012 – 2015
"""

EXTRACTED = {
    "language": "English",
    "user_info": {
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": "+44 20 7946 0958",
        "linkedin": "https://linkedin.com/in/adalovelace",
        "address": "12 St James's Square",
        "skills": ["Python", "Django", "Kubernetes"],
    },
    "experience": [
        {
            "title": "Senior Engineer",
            "company": "Analytical Engines Ltd",
            "start_date": "2019-01",
            "end_date": None,
            "current_role": True,
            "description": [
                "Built the scheduling service in Django.",
                "Cut infrastructure cost by 40%.",
            ],
            "certifications": ["AWS"],
        }
    ],
    "education": [
        {
            "school": "University of London",
            "degree": "Bachelor",
            "field_of_study": "Mathematics",
            "start_date": "2012-01",
            "end_date": "2015-01",
        }
    ],
}

# Values Jev "does not find" in SOURCE.
UNSUPPORTED = ("Kubernetes", "Cut infrastructure cost by 40%.")


def fake_ask(unsupported=UNSUPPORTED, picks=None):
    """An `ask` that says no to questions quoting an unsupported value."""

    def ask(state, questions, *, purpose):
        nouls, choices = {}, {}
        for key, question in questions.items():
            if question.type == "noul":
                text = question.instructions
                nouls[key] = 0.05 if any(v in text for v in unsupported) else 0.97
            else:
                choice = (picks or {}).get(key, import_check.NONE)
                choices[key] = ChoiceResult(choice=choice, confidence=0.95, probabilities={})
        return Answers(nouls=nouls, choices=choices, model="jev-test")

    return ask


class ConformTests(SimpleTestCase):
    def test_unknown_keys_are_dropped_and_named_without_values(self):
        content, dropped = resume_content.conform(resume_content.normalize(EXTRACTED))
        self.assertNotIn("address", content["user_info"])
        self.assertNotIn("certifications", content["experience"][0])
        self.assertEqual(dropped, ["experience[].certifications", "user_info.address"])
        self.assertNotIn("St James", json.dumps(dropped))

    def test_folded_education_dates_are_kept_as_years_and_not_reported(self):
        content, dropped = resume_content.conform(resume_content.normalize(EXTRACTED))
        self.assertEqual(content["education"][0]["start_year"], 2012)
        self.assertNotIn("start_date", content["education"][0])
        self.assertFalse([d for d in dropped if d.startswith("education")])

    def test_wrong_types_are_coerced(self):
        content, dropped = resume_content.conform(
            resume_content.normalize(
                {
                    "user_info": {"full_name": ["Ada", "Lovelace"], "phone": 442079460958,
                                  "email": {"work": "a@b.co"}},
                    "experience": [{"title": "Eng", "current_role": "true"}, "junk"],
                    "projects_and_publications": [{"name": "P", "description": ["a", "b"]}],
                }
            )
        )
        self.assertEqual(content["user_info"]["full_name"], "Ada\nLovelace")
        self.assertEqual(content["user_info"]["phone"], "442079460958")
        self.assertEqual(content["user_info"]["email"], "")
        self.assertIn("user_info.email", dropped)
        self.assertIs(content["experience"][0]["current_role"], True)
        self.assertEqual(len(content["experience"]), 1)
        self.assertEqual(content["projects_and_publications"][0]["description"], "a\nb")

    def test_conformed_content_fits_the_mcp_schema(self):
        from mcp_server.tools import CONTENT_SCHEMA

        self.assertIs(CONTENT_SCHEMA, resume_content.CONTENT_SCHEMA)


class RunTests(SimpleTestCase):
    def run_check(self, extracted=EXTRACTED, source=SOURCE, **kwargs):
        with patch("resume.services.import_check.typesafe_engine.ask", side_effect=fake_ask(**kwargs)):
            return import_check.run(extracted, source)

    def test_unsupported_values_are_flagged_not_removed(self):
        content, review = self.run_check()
        self.assertEqual(review["status"], "checked")
        flagged = {f["value"] for f in review["flags"]}
        self.assertEqual(flagged, set(UNSUPPORTED))
        self.assertIn("Kubernetes", content["user_info"]["skills"])
        self.assertIn("Cut infrastructure cost by 40%.", content["experience"][0]["description"])

    def test_paths_point_at_the_value(self):
        content, review = self.run_check()
        for flag in review["flags"]:
            self.assertEqual(import_check._get_path(content, flag["path"]), flag["value"])

    def test_contact_found_verbatim_needs_no_question(self):
        seen = []

        def ask(state, questions, *, purpose):
            seen.extend(q.instructions for q in questions.values())
            return fake_ask()(state, questions, purpose=purpose)

        with patch("resume.services.import_check.typesafe_engine.ask", side_effect=ask):
            import_check.run(EXTRACTED, SOURCE)
        self.assertFalse([q for q in seen if "ada@example.com" in q])
        self.assertFalse([q for q in seen if "email address" in q])

    def test_a_wrong_email_is_replaced_by_the_one_jev_picks_from_the_file(self):
        extracted = json.loads(json.dumps(EXTRACTED))
        extracted["user_info"]["email"] = "ada.lovelace@gmail.com"
        content, review = self.run_check(extracted, picks={"p0": "ada@example.com"})
        self.assertEqual(content["user_info"]["email"], "ada@example.com")
        corrected = [f for f in review["flags"] if f["status"] == "corrected"]
        self.assertEqual(corrected[0]["original"], "ada.lovelace@gmail.com")

    def test_a_contact_value_with_no_candidate_is_flagged(self):
        extracted = json.loads(json.dumps(EXTRACTED))
        extracted["user_info"]["github"] = "https://github.com/ada"
        content, review = self.run_check(extracted)
        github = [f for f in review["flags"] if f["kind"] == "github"]
        self.assertEqual(github[0]["status"], "unsupported")
        self.assertEqual(content["user_info"]["github"], "https://github.com/ada")

    def test_without_jev_the_import_is_shaped_and_marked_unchecked(self):
        extracted = json.loads(json.dumps(EXTRACTED))
        extracted["user_info"]["github"] = "https://github.com/ada"
        with patch("resume.services.import_check.typesafe_engine.ask", return_value=None):
            content, review = import_check.run(extracted, SOURCE)
        self.assertEqual(review["status"], "skipped")
        self.assertNotIn("address", content["user_info"])
        # What code alone can tell is still reported.
        self.assertEqual([f["kind"] for f in review["flags"]], ["github"])
        self.assertFalse(import_check.summary(review)["checked"])


class OpenFlagsTests(SimpleTestCase):
    def resume(self, content, flags):
        return SimpleNamespace(content=content, import_review={"flags": flags})

    def test_a_flag_disappears_once_the_value_is_edited(self):
        flag = {"path": "user_info.full_name", "kind": "full_name", "value": "Ada"}
        self.assertEqual(len(import_check.open_flags(self.resume({"user_info": {"full_name": "Ada"}}, [flag]))), 1)
        self.assertEqual(import_check.open_flags(self.resume({"user_info": {"full_name": "Ada L."}}, [flag])), [])

    def test_a_list_item_that_moved_is_still_shown(self):
        flag = {"path": "user_info.skills[2]", "kind": "skill", "value": "Kubernetes"}
        shown = import_check.open_flags(self.resume({"user_info": {"skills": ["Go", "Kubernetes"]}}, [flag]))
        self.assertEqual(len(shown), 1)

    def test_a_date_saved_by_the_editor_still_matches(self):
        flag = {"path": "experience[0].start_date", "kind": "dates", "value": "2019-01"}
        shown = import_check.open_flags(
            self.resume({"experience": [{"start_date": "2019-01-01"}]}, [flag])
        )
        self.assertEqual(shown[0]["field_id"], "id_experience-0-start_date")

    def test_labels_follow_the_interface_language(self):
        flag = {"path": "experience[0].description[1]", "kind": "bullet", "value": "x"}
        shown = import_check.open_flags(
            self.resume({"experience": [{"description": ["y", "x"]}]}, [flag]), "tr"
        )
        self.assertEqual(shown[0]["label"], "Deneyim maddesi")
        self.assertEqual(shown[0]["field_id"], "id_experience-0-description")


class UploadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("importer", password="x")
        self.client.force_login(self.user)

    def upload(self):
        pages = [SimpleNamespace(extract_text=lambda: SOURCE)]
        with patch("resume.views.PdfReader", return_value=SimpleNamespace(pages=pages)), \
             patch("resume.views.extract_resume_data", return_value=json.dumps(EXTRACTED)), \
             patch("resume.services.import_check.typesafe_engine.ask", side_effect=fake_ask()):
            return self.client.post(
                reverse("resume:upload_cv"),
                {"cv_file": SimpleUploadedFile("cv.pdf", b"%PDF-1.4", content_type="application/pdf")},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

    def test_the_import_is_stored_shaped_with_its_review(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["review"], {"checked": True, "flagged": 2,
                                          "dropped": ["experience[].certifications", "user_info.address"]})
        resume = Resume.objects.get(pk=body["resume_id"])
        self.assertNotIn("address", resume.content["user_info"])
        self.assertEqual(len(resume.import_review["flags"]), 2)

    def test_the_editor_shows_and_outlines_the_flags(self):
        resume_id = self.upload().json()["resume_id"]
        page = self.client.get(reverse("resume:resume_form_edit", args=[resume_id])).content.decode()
        self.assertIn('id="import-review"', page)
        self.assertIn("Cut infrastructure cost by 40%.", page)
        self.assertIn('data-field-id="id_skills"', page)

    def test_dismiss_clears_the_review(self):
        resume_id = self.upload().json()["resume_id"]
        response = self.client.post(reverse("resume:dismiss_import_review", args=[resume_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Resume.objects.get(pk=resume_id).import_review, {})
        page = self.client.get(reverse("resume:resume_form_edit", args=[resume_id])).content.decode()
        self.assertNotIn('id="import-review"', page)

    def test_dismiss_is_scoped_to_the_owner(self):
        resume_id = self.upload().json()["resume_id"]
        other = User.objects.create_user("other", password="x")
        self.client.force_login(other)
        response = self.client.post(reverse("resume:dismiss_import_review", args=[resume_id]))
        self.assertEqual(response.status_code, 404)
        self.assertNotEqual(Resume.objects.get(pk=resume_id).import_review, {})
