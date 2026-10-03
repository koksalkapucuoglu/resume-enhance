"""One import entry point: CV or LinkedIn export, and a clear answer when it fails."""

import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from resume import views
from resume.models import Resume

LINKEDIN_TEXT = (
    "Contact www.linkedin.com/in/ada-lovelace (LinkedIn) Top Skills Python Django "
    "Experience Analytical Engine Lead Engineer 1842 - Present Page 1 of 2"
)
CV_TEXT = "Ada Lovelace — Engineer. Experience: Analytical Engine, 1842-present. Python, Django, maths."
EXTRACTED = json.dumps({
    "language": "English",
    "user_info": {"full_name": "Ada Lovelace", "skills": ["Python"]},
    "experience": [], "education": [], "projects_and_publications": [],
})


def pdf_upload(name="cv.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 fake", content_type="application/pdf")


def reader_with(text):
    page = MagicMock()
    page.extract_text.return_value = text
    reader = MagicMock()
    reader.pages = [page]
    return reader


class LinkedInDetectionTest(TestCase):
    def test_a_linkedin_export_is_recognised(self):
        self.assertTrue(views._looks_like_linkedin_export(LINKEDIN_TEXT))

    def test_a_cv_with_a_linkedin_link_is_not(self):
        self.assertFalse(
            views._looks_like_linkedin_export(CV_TEXT + " linkedin.com/in/ada")
        )


@patch("resume.services.import_check.run", side_effect=lambda content, text: (content, {}))
class UploadTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)

    def post(self):
        return self.client.post(reverse("resume:upload_cv"), {"cv_file": pdf_upload()})

    def test_a_cv_goes_through_the_cv_extractor(self, _check):
        with patch("resume.views.PdfReader", return_value=reader_with(CV_TEXT)), \
                patch("resume.views.extract_resume_data", return_value=EXTRACTED) as cv, \
                patch("resume.views.extract_linkedin_resume_data") as linkedin:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        cv.assert_called_once()
        linkedin.assert_not_called()

    def test_a_linkedin_export_goes_through_the_linkedin_extractor(self, _check):
        with patch("resume.views.PdfReader", return_value=reader_with(LINKEDIN_TEXT)), \
                patch("resume.views.extract_resume_data") as cv, \
                patch("resume.views.extract_linkedin_resume_data", return_value=EXTRACTED):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        cv.assert_not_called()
        resume = Resume.objects.get(pk=response.json()["resume_id"])
        self.assertTrue(resume.title.startswith("LinkedIn"))

    def test_the_old_linkedin_field_name_still_works(self, _check):
        with patch("resume.views.PdfReader", return_value=reader_with(LINKEDIN_TEXT)), \
                patch("resume.views.extract_linkedin_resume_data", return_value=EXTRACTED):
            response = self.client.post(
                reverse("resume:upload_cv"), {"linkedin_file": pdf_upload()}
            )
        self.assertEqual(response.status_code, 200)

    def test_an_unreadable_pdf_is_a_422_not_a_500(self, _check):
        with patch("resume.views.PdfReader", side_effect=ValueError("broken xref")):
            response = self.post()
        self.assertEqual(response.status_code, 422)
        self.assertIn("could not be read", response.json()["error"])

    def test_an_unreadable_ai_answer_is_a_502_and_is_reported(self, _check):
        with patch("resume.views.PdfReader", return_value=reader_with(CV_TEXT)), \
                patch("resume.views.extract_resume_data", return_value="not json"), \
                patch("resume.views.report_degraded") as report:
            response = self.post()
        self.assertEqual(response.status_code, 502)
        report.assert_called_once()
        self.assertEqual(report.call_args.args[0], "import_unparseable_output")
        self.assertFalse(Resume.objects.filter(user=self.user).exists())

    def test_ai_down_is_a_503_without_the_provider_message(self, _check):
        with patch("resume.views.PdfReader", return_value=reader_with(CV_TEXT)), \
                patch("resume.views.extract_resume_data",
                      return_value="OpenAI API returned an API Error: key sk-123"), \
                patch("resume.views.report_degraded"):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sk-123", response.json()["error"])

    def test_get_is_not_allowed(self, _check):
        self.assertEqual(self.client.get(reverse("resume:upload_cv")).status_code, 405)



def stream_of(*chunks):
    """Stand-in for openai_engine.stream_extraction."""
    def fake(text, linkedin=False):
        for chunk in chunks:
            yield ("delta", chunk)
        yield ("done", "".join(chunks))
    return fake


def frames(response):
    body = b"".join(response.streaming_content).decode()
    out = []
    for block in body.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


@patch("resume.services.import_check.run", side_effect=lambda content, text: (content, {}))
class StreamingUploadTest(TestCase):
    """Accept: text/event-stream → stage frames while the parser writes, then the result."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)

    def post(self):
        return self.client.post(
            reverse("resume:upload_cv"), {"cv_file": pdf_upload()},
            HTTP_ACCEPT="text/event-stream",
        )

    def test_stages_follow_the_sections_being_written(self, _check):
        parsed = json.loads(EXTRACTED)
        chunks = ['{"language": "English", "user_info": ', json.dumps(parsed["user_info"]),
                  ', "experience": [], ', '"education": [], ', '"projects_and_publications": []}']
        with patch("resume.views.PdfReader", return_value=reader_with(CV_TEXT)), \
                patch("resume.openai_engine.stream_extraction", side_effect=stream_of(*chunks)):
            response = self.post()
        self.assertEqual(response["Content-Type"], "text/event-stream")
        events = frames(response)
        stages = [data["stage"] for kind, data in events if kind == "progress"]
        self.assertEqual(stages[0], "reading")
        for stage in ("contact", "experience", "education", "projects", "checking", "saved"):
            self.assertIn(stage, stages)
        self.assertLess(stages.index("experience"), stages.index("education"))
        progress = [data["progress"] for kind, data in events if kind == "progress"]
        self.assertEqual(progress, sorted(progress))
        kind, done = events[-1]
        self.assertEqual(kind, "done")
        self.assertEqual(done["http_status"], 200)
        self.assertTrue(Resume.objects.filter(pk=done["resume_id"], user=self.user).exists())
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.import_count, 1)

    def test_an_ai_outage_ends_the_stream_with_an_error_and_charges_nothing(self, _check):
        def failing(text, linkedin=False):
            yield ("error", "OpenAI API returned an API Error: boom")
        with patch("resume.views.PdfReader", return_value=reader_with(CV_TEXT)), \
                patch("resume.openai_engine.stream_extraction", side_effect=failing), \
                patch("resume.views.report_degraded") as report:
            events = frames(self.post())
        kind, done = events[-1]
        self.assertEqual((kind, done["http_status"]), ("done", 503))
        self.assertNotIn("boom", done["error"])
        report.assert_called_once()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.import_count, 0)

    def test_refusals_are_still_plain_json_before_any_stream(self, _check):
        Resume.objects.create(user=self.user, title="1", content={})
        Resume.objects.create(user=self.user, title="2", content={})
        Resume.objects.create(user=self.user, title="3", content={})
        response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()["quota_exceeded"])
