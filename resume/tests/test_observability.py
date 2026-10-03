"""Error pages, Sentry reporting and product analytics: helpful, and content-free."""

import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from core import analytics, observability
from core.views import server_error


class ErrorPagesTest(TestCase):
    def test_a_missing_page_explains_itself(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "find that page", status_code=404)

    def test_a_script_gets_json_not_a_page(self):
        response = self.client.get("/no-such-page/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], 404)

    def test_the_500_page_renders_without_a_user(self):
        request = RequestFactory().get("/")
        response = server_error(request)
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Something broke on our side", response.content)

    def test_another_users_resume_is_a_404_page(self):
        owner = User.objects.create_user("owner", password="x")
        from resume.models import Resume

        resume = Resume.objects.create(user=owner, title="CV", content={})
        User.objects.create_user("eve", password="x")
        self.client.login(username="eve", password="x")
        response = self.client.get(reverse("resume:preview_saved_resume", args=[resume.pk]))
        self.assertEqual(response.status_code, 404)


class BodyParsingTest(TestCase):
    """A JSON body that is not an object must be a 400, never a 500."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)

    def test_list_bodies_are_refused_cleanly(self):
        for name in ("resume:toggle_agent_mode", "resume:toggle_confirm_destructive",
                     "resume:submit_feedback", "resume:agent_chat_stream"):
            response = self.client.post(reverse(name), "[1, 2]", content_type="application/json")
            self.assertEqual(response.status_code, 400, name)


class SentryScrubTest(TestCase):
    def test_request_bodies_cookies_and_locals_are_dropped(self):
        event = {
            "request": {"data": {"message": "my resume"}, "cookies": {"sessionid": "x"},
                        "query_string": "q=secret", "headers": {"Cookie": "a", "Authorization": "Bearer t"}},
            "extra": {"content": "resume text", "resume_id": 4},
            "exception": {"values": [{"stacktrace": {"frames": [{"vars": {"text": "cv"}}]}}]},
        }
        out = observability._before_send(event, {})
        self.assertNotIn("data", out["request"])
        self.assertNotIn("cookies", out["request"])
        self.assertEqual(out["request"]["query_string"], "")
        self.assertEqual(out["request"]["headers"]["Authorization"], "[scrubbed]")
        self.assertEqual(out["extra"]["content"], "[scrubbed]")
        self.assertEqual(out["extra"]["resume_id"], 4)
        self.assertNotIn("vars", out["exception"]["values"][0]["stacktrace"]["frames"][0])

    def test_reporting_is_a_no_op_without_sentry(self):
        observability.report_degraded("test_kind", reason="x")
        observability.report_exception(ValueError("x"))

    @override_settings(SENTRY_DSN="")
    def test_init_without_a_dsn_does_nothing(self):
        self.assertFalse(observability.init())


class AnalyticsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x", email="ada@example.com")
        analytics._client = None

    def tearDown(self):
        analytics._client = None

    def test_off_without_a_key(self):
        with patch("posthog.Posthog") as client_class:
            analytics.track(self.user, "signed_up", method="email")
        client_class.assert_not_called()

    @override_settings(POSTHOG_API_KEY="phc_test")
    def test_events_carry_the_account_id_and_no_personal_data(self):
        fake = MagicMock()
        with patch("posthog.Posthog", return_value=fake):
            analytics.track(self.user, "signed_up", method="email")
        args, kwargs = fake.capture.call_args
        self.assertEqual(args[0], "signed_up")
        self.assertEqual(kwargs["distinct_id"], str(self.user.pk))
        sent = json.dumps(kwargs["properties"])
        self.assertNotIn("ada@example.com", sent)
        self.assertNotIn("ada", kwargs["distinct_id"])
        self.assertEqual(kwargs["properties"]["tier"], "free")

    @override_settings(POSTHOG_API_KEY="phc_test")
    def test_unknown_events_are_not_sent(self):
        fake = MagicMock()
        with patch("posthog.Posthog", return_value=fake):
            analytics.track(self.user, "something_new")
        fake.capture.assert_not_called()

    @override_settings(POSTHOG_API_KEY="phc_test")
    def test_a_failing_client_never_breaks_the_flow(self):
        fake = MagicMock()
        fake.capture.side_effect = RuntimeError("network")
        with patch("posthog.Posthog", return_value=fake):
            analytics.track(self.user, "signed_up", method="email")

    @override_settings(POSTHOG_API_KEY="phc_test")
    def test_signup_is_tracked(self):
        fake = MagicMock()
        with patch("posthog.Posthog", return_value=fake), \
                patch("core.email_verification.send_verification_email", return_value=True):
            self.client.post(reverse("signup"), {
                "username": "newbie", "email": "n@example.com",
                "password1": "a-Long-pass-123", "password2": "a-Long-pass-123",
                "privacy_consent": "on",
            })
        events = [c.args[0] for c in fake.capture.call_args_list]
        self.assertIn("signed_up", events)

    def test_score_band(self):
        self.assertEqual(analytics.score_band(10), "0-39")
        self.assertEqual(analytics.score_band(55), "40-69")
        self.assertEqual(analytics.score_band(90), "70-100")
        self.assertEqual(analytics.score_band(None), "none")


class TelemetrySnippetTest(TestCase):
    @override_settings(POSTHOG_API_KEY="phc_public", SENTRY_BROWSER_DSN="https://k@o1.ingest.sentry.io/1")
    def test_public_pages_are_cookieless_and_app_pages_have_no_web_analytics(self):
        landing = self.client.get(reverse("resume:index")).content.decode()
        self.assertIn('cookieless_mode: "always"', landing)
        self.assertIn('person_profiles: "never"', landing)
        self.assertIn("browser.sentry-cdn.com", landing)

        user = User.objects.create_user("ada", password="x")
        self.client.force_login(user)
        profile = self.client.get(reverse("profile")).content.decode()
        self.assertNotIn("posthog.init", profile)
        self.assertIn("browser.sentry-cdn.com", profile)

    def test_nothing_loads_without_keys(self):
        landing = self.client.get(reverse("resume:index")).content.decode()
        self.assertNotIn("posthog.init", landing)
        self.assertNotIn("sentry-cdn", landing)
