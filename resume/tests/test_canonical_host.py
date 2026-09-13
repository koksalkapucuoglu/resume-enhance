"""www serves nothing itself: it redirects to the one host Google knows."""

from django.test import TestCase, override_settings


@override_settings(ALLOWED_HOSTS=["resustackapp.com", "www.resustackapp.com"], SECURE_SSL_REDIRECT=False)
class CanonicalHostTests(TestCase):
    def test_www_redirects_to_the_bare_domain_keeping_path_and_query(self):
        response = self.client.get("/accounts/login/?next=/dashboard/", HTTP_HOST="www.resustackapp.com")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "http://resustackapp.com/accounts/login/?next=/dashboard/")

    def test_a_post_to_www_stays_a_post(self):
        response = self.client.post("/accounts/google/login/", HTTP_HOST="www.resustackapp.com")
        self.assertEqual(response.status_code, 308)

    def test_the_bare_domain_is_served(self):
        response = self.client.get("/accounts/login/", HTTP_HOST="resustackapp.com")
        self.assertEqual(response.status_code, 200)
